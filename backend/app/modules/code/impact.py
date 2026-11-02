"""Change impact between two snapshots of a repository.

1. **What changed** — files by content hash; functions and methods by ``(path, qualified name)``
   and a hash of their normalised body (whitespace changes are not changes).
2. **Blast radius** — walk the *head* snapshot's call graph backwards from every changed or
   added function, up to ``MAX_DEPTH`` calls away. Every caller reached could behave
   differently; test functions reached are the tests to run.
3. **Untested changes** — changed functions that no test reaches (directly or indirectly).
4. **Findings** — new and fixed findings, matched by fingerprint (rule + path + symbol + key, no
   line numbers, so moved code does not show up as "fixed and re-introduced").
5. **Risk score** — a transparent points system (below), not a model. Each contribution is
   returned with its reason so a reviewer can disagree with a specific part.
"""

from __future__ import annotations

import uuid
from collections import defaultdict, deque
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import CodeFile, CodeSymbol, FindingRow, Ref, Snapshot

MAX_DEPTH = 3


def _symbols(db: Session, snapshot_id: uuid.UUID) -> dict[tuple[str, str], dict[str, Any]]:
    rows = db.execute(
        select(CodeSymbol, CodeFile.path, CodeFile.is_test)
        .join(CodeFile, CodeFile.id == CodeSymbol.file_id)
        .where(CodeSymbol.snapshot_id == snapshot_id, CodeSymbol.kind != "class")
    ).all()
    return {
        (path, s.qualified_name): {
            "id": s.id,
            "path": path,
            "qualified_name": s.qualified_name,
            "kind": s.kind,
            "start_line": s.start_line,
            "end_line": s.end_line,
            "complexity": s.complexity,
            "loc": s.loc,
            "body_hash": s.body_hash,
            "exported": s.exported,
            "tested": s.tested,
            "is_test": is_test,
        }
        for s, path, is_test in rows
    }


def _public(sym: dict[str, Any], **extra: Any) -> dict[str, Any]:
    keys = ("id", "path", "qualified_name", "kind", "start_line", "end_line", "complexity", "loc")
    return {k: sym[k] for k in keys} | extra


def compare(db: Session, base: Snapshot, head: Snapshot) -> dict[str, Any]:
    base_files = dict(db.execute(select(CodeFile.path, CodeFile.sha256).where(CodeFile.snapshot_id == base.id)).all())
    head_files = dict(db.execute(select(CodeFile.path, CodeFile.sha256).where(CodeFile.snapshot_id == head.id)).all())
    files = {
        "added": sorted(set(head_files) - set(base_files)),
        "removed": sorted(set(base_files) - set(head_files)),
        "modified": sorted(p for p in set(base_files) & set(head_files) if base_files[p] != head_files[p]),
    }

    bs, hs = _symbols(db, base.id), _symbols(db, head.id)
    changed = [k for k in hs.keys() & bs.keys() if hs[k]["body_hash"] != bs[k]["body_hash"]]
    added = [k for k in hs.keys() - bs.keys()]
    removed = [k for k in bs.keys() - hs.keys()]

    # ---- blast radius on the head call graph
    callers: dict[int, set[int]] = defaultdict(set)
    for caller_id, callee_id in db.execute(
        select(Ref.caller_id, Ref.callee_id).where(
            Ref.snapshot_id == head.id, Ref.callee_id.is_not(None), Ref.caller_id.is_not(None)
        )
    ):
        if caller_id is not None and callee_id is not None:
            callers[callee_id].add(caller_id)
    by_id = {v["id"]: v for v in hs.values()}
    seeds = {hs[k]["id"] for k in [*changed, *added]}
    impacted: dict[int, dict[str, Any]] = {}
    reached_from: dict[int, set[int]] = defaultdict(set)  # seed -> symbols it reaches
    for seed in seeds:
        queue: deque[tuple[int, int]] = deque([(seed, 0)])
        seen = {seed}
        while queue:
            node, depth = queue.popleft()
            if depth >= MAX_DEPTH:
                continue
            for c in callers.get(node, ()):
                if c in seen:
                    continue
                seen.add(c)
                reached_from[seed].add(c)
                queue.append((c, depth + 1))
                if c not in seeds and c in by_id:
                    prev = impacted.get(c)
                    if prev is None or prev["depth"] > depth + 1:
                        via = by_id.get(seed, {}).get("qualified_name")
                        impacted[c] = _public(by_id[c], depth=depth + 1, via=via, is_test=by_id[c]["is_test"])
    tests = sorted((v for v in impacted.values() if v["is_test"]), key=lambda v: (v["path"], v["qualified_name"]))
    impacted_code = sorted(
        (v for v in impacted.values() if not v["is_test"]), key=lambda v: (v["depth"], v["path"], v["qualified_name"])
    )

    untested = []
    for k in [*changed, *added]:
        sym = hs[k]
        if sym["is_test"]:
            continue
        reaches_test = any(by_id.get(c, {}).get("is_test") for c in reached_from.get(sym["id"], ()))
        if not sym["tested"] and not reaches_test:
            untested.append(_public(sym))

    # ---- findings
    def findings(snap_id: uuid.UUID) -> dict[str, dict[str, Any]]:
        rows = db.execute(
            select(FindingRow, CodeFile.path)
            .join(CodeFile, CodeFile.id == FindingRow.file_id)
            .where(FindingRow.snapshot_id == snap_id)
        ).all()
        return {
            f.fingerprint: {
                "rule": f.rule,
                "severity": f.severity,
                "category": f.category,
                "path": path,
                "line": f.line,
                "message": f.message,
            }
            for f, path in rows
        }

    bf, hf = findings(base.id), findings(head.id)
    new_findings = sorted((hf[k] for k in hf.keys() - bf.keys()), key=_severity_key)
    fixed_findings = sorted((bf[k] for k in bf.keys() - hf.keys()), key=_severity_key)

    # ---- risk
    changed_code = [hs[k] for k in changed if not hs[k]["is_test"]]
    added_code = [hs[k] for k in added if not hs[k]["is_test"]]
    reasons: list[dict[str, Any]] = []

    def add(points: float, cap: float, reason: str) -> None:
        p = round(min(points, cap))
        if p > 0:
            reasons.append({"points": p, "reason": reason})

    add(
        3 * len(changed_code) + 2 * len(added_code),
        25,
        f"{len(changed_code)} changed and {len(added_code)} added functions",
    )
    complex_touched = [s for s in changed_code + added_code if s["complexity"] > 10]
    add(
        sum(s["complexity"] - 10 for s in complex_touched),
        20,
        f"{len(complex_touched)} changed functions have complexity above 10",
    )
    add(
        2 * len(impacted_code),
        20,
        f"{len(impacted_code)} other functions call the changed code (up to {MAX_DEPTH} hops)",
    )
    high = sum(1 for f in new_findings if f["severity"] == "high")
    medium = sum(1 for f in new_findings if f["severity"] == "medium")
    add(15 * high + 5 * medium, 25, f"{high} new high and {medium} new medium severity findings")
    add(3 * len(untested), 10, f"{len(untested)} changed functions are not reached by any test")
    score = min(100, sum(r["points"] for r in reasons))
    level = "high" if score >= 50 else "medium" if score >= 25 else "low"

    def delta(key: str) -> dict[str, Any]:
        return {"base": base.stats.get(key), "head": head.stats.get(key)}

    return {
        "base": {"id": base.id, "seq": base.seq, "label": base.label},
        "head": {"id": head.id, "seq": head.seq, "label": head.label},
        "files": files,
        "symbols": {
            "changed": sorted((_public(hs[k], complexity_before=bs[k]["complexity"]) for k in changed), key=_pathkey),
            "added": sorted((_public(hs[k]) for k in added), key=_pathkey),
            "removed": sorted((_public(bs[k]) for k in removed), key=_pathkey),
        },
        "impacted": impacted_code,
        "tests_to_run": tests,
        "untested_changes": sorted(untested, key=_pathkey),
        "findings": {"new": new_findings, "fixed": fixed_findings},
        "metrics": {k: delta(k) for k in ("loc", "functions", "avg_complexity", "health", "findings", "import_cycles")},
        "risk": {"score": score, "level": level, "reasons": reasons},
    }


def _severity_key(f: dict[str, Any]) -> tuple[int, str, int]:
    order = {"high": 0, "medium": 1, "low": 2, "info": 3}
    return order[f["severity"]], f["path"], f["line"]


def _pathkey(s: dict[str, Any]) -> tuple[str, int]:
    return s["path"], s["start_line"]
