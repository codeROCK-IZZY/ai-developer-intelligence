"""End-to-end evaluation on the demo repository (``python -m app.cli evaluate``).

Requires the demo to be loaded and processed (``python -m app.cli demo``). Reports:

* **Static analysis** (in memory, v1): findings, call-graph resolution and import cycles
  against the hand-labelled ground truth.
* **Change impact** (database, v1 → v2): changed/added/removed symbols, impacted callers,
  tests to run, new and fixed findings.
* **Retrieval**: for every answerable question, asked as *dana* (Storefront editor, *not* a
  member of Platform): symbol-level Recall@k and MRR for keyword, vector, hybrid and
  hybrid + re-ranking.
* **Q&A**: does the answer cite an expected symbol? false refusals, correct refusals on
  unanswerable questions, and leakage (any citation from a repository dana cannot read).

Numbers describe this small labelled repository, not every codebase.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from statistics import mean, median
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.providers import get_llm
from app.code.analyze import analyze
from app.code.archive import read_archive
from app.core.config import get_settings
from app.db.models import CodeSymbol, Repository, Snapshot, User
from app.evaluation.structure import evaluate_call_graph, evaluate_cycles, evaluate_findings
from app.modules.code.impact import compare
from app.modules.workspaces.service import readable_repository_ids
from app.retrieval.qa import answer_question
from app.retrieval.search import Hit, search
from app.synthetic import demo

MODES = [
    ("keyword", "keyword", False),
    ("vector", "vector", False),
    ("hybrid", "hybrid", False),
    ("hybrid + re-rank", "hybrid", True),
]
KS = (1, 3, 5, 10)


def _symbol_keys(db: Session, hits: list[Hit]) -> list[str]:
    """Distinct ``path::qualified name`` in rank order (module-level chunks count as ``path::``)."""
    out: list[str] = []
    for h in hits:
        key = f"{h.path}::{h.symbol or ''}"
        if key not in out:
            out.append(key)
    return out


def _retrieval(db: Session, user: User, questions: list[dict[str, Any]]) -> dict[str, Any]:
    answerable = [q for q in questions if q["expect"]]
    rows: dict[str, Any] = {}
    for label, mode, rerank in MODES:
        ranks: list[int | None] = []
        latencies: list[float] = []
        by_kind: dict[str, list[float]] = {}
        for q in answerable:
            t0 = time.perf_counter()
            hits = search(db, user.id, q["q"], limit=10, mode=mode, rerank=rerank)
            latencies.append((time.perf_counter() - t0) * 1000)
            keys = _symbol_keys(db, hits)
            rank = next((i + 1 for i, k in enumerate(keys) if k in q["expect"]), None)
            ranks.append(rank)
            by_kind.setdefault(q["kind"], []).append(1 / rank if rank else 0.0)
        rows[label] = {
            **{f"recall@{k}": mean(1.0 if r and r <= k else 0.0 for r in ranks) for k in KS},
            "mrr": mean(1 / r if r else 0.0 for r in ranks),
            "mrr_by_kind": {k: mean(v) for k, v in sorted(by_kind.items())},
            "median_latency_ms": round(median(latencies), 1),
        }
    return {"questions": len(answerable), "modes": rows}


def _qa(db: Session, user: User, questions: list[dict[str, Any]], readable: set[str]) -> dict[str, Any]:
    per_kind: dict[str, dict[str, int]] = {}
    leakage = 0
    latencies: list[int] = []
    failures: list[dict[str, Any]] = []
    for q in questions:
        result = answer_question(db, user.id, q["q"], persist=False)
        latencies.append(result.latency_ms)
        stats = per_kind.setdefault(q["kind"], {"n": 0, "located": 0, "refused": 0})
        stats["n"] += 1
        cited = {f"{c.path}::{c.symbol or ''}" for c in result.citations}
        leakage += sum(1 for c in result.citations if str(c.repository_id) not in readable)
        if result.status != "answered":
            stats["refused"] += 1
        elif cited & set(q["expect"]):
            stats["located"] += 1
        if (q["expect"] and not cited & set(q["expect"])) or (not q["expect"] and result.status == "answered"):
            failures.append(
                {
                    "question": q["q"],
                    "status": result.status,
                    "cited": sorted(cited)[:3],
                    "best_relevance": result.best_relevance,
                }
            )
    answerable = [k for k in per_kind if k != "unanswerable"]
    n = sum(per_kind[k]["n"] for k in answerable)
    unans = per_kind.get("unanswerable", {"n": 0, "refused": 0})
    return {
        "provider": get_llm().name,
        "location_accuracy": sum(per_kind[k]["located"] for k in answerable) / n if n else None,
        "false_refusal_rate": sum(per_kind[k]["refused"] for k in answerable) / n if n else None,
        "correct_refusal_rate": unans["refused"] / unans["n"] if unans["n"] else None,
        "leakage": leakage,
        "by_kind": per_kind,
        "median_latency_ms": median(latencies) if latencies else None,
        "failures": failures,
    }


def _impact(db: Session, truth: dict[str, Any]) -> dict[str, Any]:
    repo = db.scalar(select(Repository).where(Repository.name == "fernwood-shop"))
    if repo is None:
        raise RuntimeError("demo not loaded — run `python -m app.cli demo` first")
    snaps = list(db.scalars(select(Snapshot).where(Snapshot.repository_id == repo.id).order_by(Snapshot.seq)))
    base, head = snaps[0], snaps[-1]
    result = compare(db, base, head)

    def keys(items: list[dict[str, Any]]) -> set[str]:
        return {f"{s['path']}::{s['qualified_name']}" for s in items}

    def score(expected: list[str], got: set[str]) -> dict[str, Any]:
        e = set(expected)
        return {
            "expected": len(e),
            "found": len(got),
            "correct": len(e & got),
            "missed": sorted(e - got),
            "unexpected": sorted(got - e),
        }

    def fkeys(items: list[dict[str, Any]]) -> set[str]:
        return {f"{f['rule']}@{f['path']}" for f in items}

    t = truth["impact"]
    return {
        "changed": score(t["changed"], keys(result["symbols"]["changed"])),
        "added": score(t["added"], keys(result["symbols"]["added"])),
        "removed": score(t["removed"], keys(result["symbols"]["removed"])),
        "impacted": score(t["impacted"], keys(result["impacted"])),
        "tests_to_run": score(t["tests_to_run"], keys(result["tests_to_run"])),
        "new_findings": score(
            [f"{x['rule']}@{x['path']}" for x in t["new_findings"]], fkeys(result["findings"]["new"])
        ),
        "fixed_findings": score(
            [f"{x['rule']}@{x['path']}" for x in t["fixed_findings"]], fkeys(result["findings"]["fixed"])
        ),
        "risk": result["risk"],
    }


def evaluate(db: Session) -> dict[str, Any]:
    started = time.perf_counter()
    truth = demo.ground_truth()
    s = get_settings()
    contents = read_archive(demo.archive(1), max_entries=10_000, max_total_mb=100, max_file_kb=512, max_files=5000)
    t0 = time.perf_counter()
    analysis = analyze(contents.files)
    analysis_ms = (time.perf_counter() - t0) * 1000
    user = db.scalar(select(User).where(User.email == "dana@example.com"))
    if user is None:
        raise RuntimeError("demo not loaded — run `python -m app.cli demo` first")
    readable = {str(r) for r in readable_repository_ids(db, user)}
    platform = db.scalar(select(Repository).where(Repository.name == "developer-intelligence"))
    corpus = {"symbols_indexed": db.scalar(select(func.count()).select_from(CodeSymbol)) or 0}
    return {
        "generated_by": "python -m app.cli evaluate",
        "config": {
            "embedding_model": s.embedding_model,
            "reranker_model": s.reranker_model,
            "answer_provider": get_llm().name,
            "min_rerank_relevance": s.min_rerank_relevance,
        },
        "corpus": {
            **corpus,
            "demo_files": len(contents.files),
            "questions": len(truth["questions"]),
            "unreadable_repository_present": platform is not None and str(platform.id) not in readable,
        },
        "structure": {
            "findings": evaluate_findings(analysis, truth),
            "call_graph": evaluate_call_graph(analysis, truth),
            "cycles": evaluate_cycles(analysis, truth),
            "analysis_ms": round(analysis_ms),
            "stats": {k: v for k, v in analysis.stats.items() if k != "entry_points"},
        },
        "impact": _impact(db, truth),
        "retrieval": _retrieval(db, user, truth["questions"]),
        "qa": _qa(db, user, truth["questions"], readable),
        "seconds": round(time.perf_counter() - started, 1),
    }


def _pct(x: float | None) -> str:
    return "—" if x is None else f"{x * 100:.1f}%"


def write_report(r: dict[str, Any], out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "evaluation.json").write_text(json.dumps(r, indent=1, default=str), encoding="utf-8")
    st = r["structure"]
    f, cg = st["findings"], st["call_graph"]
    lines = [
        "# Evaluation report — AI Developer Intelligence",
        "",
        f"Generated by `{r['generated_by']}` on the demo repository *fernwood-shop* (fictional; Python, TypeScript "
        f"and Go; {r['corpus']['demo_files']} files) and its hand-labelled answers in "
        "`backend/demo/ground_truth.json`. "
        "These numbers describe this small repository, not every codebase.",
        "",
        f"Answer provider: **{r['config']['answer_provider']}** · embeddings: `{r['config']['embedding_model']}` · "
        f"re-ranker: `{r['config']['reranker_model']}`",
        "",
        "## Static analysis (v1)",
        "",
        "| Check | Precision | Recall | Detail |",
        "|---|---|---|---|",
        f"| Findings (labelled rules) | {_pct(f['overall']['precision'])} | {_pct(f['overall']['recall'])} | "
        f"{f['overall']['tp']} correct, {f['overall']['fp']} false positives, {f['overall']['fn']} missed |",
        f"| Call graph ({cg['callers']} callers, {cg['edges']} labelled calls) | {_pct(cg['overall']['precision'])} | "
        f"{_pct(cg['overall']['recall'])} | {cg['overall']['fn']} calls not resolved |",
        f"| Import cycles | {_pct(st['cycles']['precision'])} | {_pct(st['cycles']['recall'])} | |",
        "",
        "Call resolution precision by method: "
        + ", ".join(f"{k} {v['correct']}/{v['total']}" for k, v in cg["precision_by_resolution"].items())
        + ".",
        "",
        "| Rule | Precision | Recall |",
        "|---|---|---|",
        *[f"| {k} | {_pct(v['precision'])} | {_pct(v['recall'])} |" for k, v in f["per_rule"].items()],
        "",
        f"Unresolved labelled calls: {', '.join(cg['missed']) or 'none'}.",
        "",
        "## Change impact (v2.3.0 → v2.4.0)",
        "",
        "| Item | Expected | Found | Correct |",
        "|---|---|---|---|",
        *[
            f"| {k.replace('_', ' ')} | {v['expected']} | {v['found']} | {v['correct']} |"
            for k, v in r["impact"].items()
            if k != "risk"
        ],
        "",
        f"Risk score: **{r['impact']['risk']['score']} ({r['impact']['risk']['level']})** — "
        + "; ".join(f"{x['reason']} (+{x['points']})" for x in r["impact"]["risk"]["reasons"])
        + ".",
        "",
        f"## Code search ({r['retrieval']['questions']} questions with a known answer, asked as a Storefront member)",
        "",
        "Relevance is symbol-level: a result counts when it is one of the labelled functions.",
        "",
        "| Mode | Recall@1 | Recall@5 | Recall@10 | MRR | Median latency |",
        "|---|---|---|---|---|---|",
        *[
            f"| {m} | {_pct(v['recall@1'])} | {_pct(v['recall@5'])} | {_pct(v['recall@10'])} | {v['mrr']:.3f} | "
            f"{v['median_latency_ms']} ms |"
            for m, v in r["retrieval"]["modes"].items()
        ],
        "",
        "MRR by question kind: "
        + "; ".join(
            f"**{m}** " + ", ".join(f"{k} {x:.2f}" for k, x in v["mrr_by_kind"].items())
            for m, v in r["retrieval"]["modes"].items()
        ),
        "",
        "## Questions about the code",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Answer cites a correct function | {_pct(r['qa']['location_accuracy'])} |",
        f'| False "not found" (answer exists) | {_pct(r["qa"]["false_refusal_rate"])} |',
        f'| Correct "not found" (unanswerable) | {_pct(r["qa"]["correct_refusal_rate"])} |',
        f"| **Leakage** (citations from a repository the asker cannot read) | **{r['qa']['leakage']}** |",
        f"| Median answer latency | {r['qa']['median_latency_ms']} ms |",
        "",
        "Failures: " + ("; ".join(f'"{x["question"]}" ({x["status"]})' for x in r["qa"]["failures"]) or "none") + ".",
        "",
        f"Evaluation took {r['seconds']} s.",
        "",
    ]
    (out / "evaluation.md").write_text("\n".join(lines), encoding="utf-8")
