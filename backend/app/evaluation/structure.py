"""Evaluate the static analysis against the hand-labelled demo repository.

* **Findings** — precision and recall over the rules that have labels (security, reliability,
  design and the maintainability rules), matched by ``(rule, path::symbol)``.
* **Call graph** — for each labelled caller, the labelled set of in-repository callees is
  compared with the resolved edges: precision, recall, and precision per resolution kind.
* **Import cycles** — exact match of the cycles' file sets.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from app.code.analyze import Analysis

EVALUATED_RULES = frozenset(
    {
        "hardcoded-secret",
        "sql-injection",
        "dangerous-eval",
        "insecure-deserialization",
        "shell-injection",
        "tls-verification-disabled",
        "dangerous-html",
        "weak-hash",
        "swallowed-exception",
        "high-complexity",
        "long-function",
        "deep-nesting",
        "too-many-parameters",
        "import-cycle",
        "dead-code",
        "duplicate-code",
    }
)


def symbol_key(a: Analysis, fi: int, si: int | None) -> str:
    f = a.files[fi]
    name = f.parsed.symbols[si].qualified_name if f.parsed is not None and si is not None else ""
    return f"{f.path}::{name}"


def _prf(tp: int, fp: int, fn: int) -> dict[str, Any]:
    p = tp / (tp + fp) if tp + fp else None
    r = tp / (tp + fn) if tp + fn else None
    f1 = 2 * p * r / (p + r) if p and r else 0.0 if p is not None and r is not None else None
    return {"precision": p, "recall": r, "f1": f1, "tp": tp, "fp": fp, "fn": fn}


def evaluate_findings(a: Analysis, truth: dict[str, Any]) -> dict[str, Any]:
    expected = {(t["rule"], t["symbol"]) for t in truth["findings"]}
    found = {(f.rule, symbol_key(a, f.file_idx, f.symbol)) for f in a.findings if f.rule in EVALUATED_RULES}
    per_rule: dict[str, dict[str, Any]] = {}
    for rule in sorted({r for r, _ in expected | found}):
        e = {k for k in expected if k[0] == rule}
        g = {k for k in found if k[0] == rule}
        per_rule[rule] = _prf(len(e & g), len(g - e), len(e - g))
    return {
        "overall": _prf(len(expected & found), len(found - expected), len(expected - found)),
        "per_rule": per_rule,
        "false_positives": sorted(f"{r} @ {s}" for r, s in found - expected),
        "missed": sorted(f"{r} @ {s}" for r, s in expected - found),
    }


def evaluate_call_graph(a: Analysis, truth: dict[str, Any]) -> dict[str, Any]:
    labelled: dict[str, set[str]] = {k: set(v) for k, v in truth["call_graph"].items()}
    resolved: dict[str, dict[str, str]] = defaultdict(dict)  # caller -> callee -> resolution kind
    for cr in a.resolved.calls:
        if cr.target is None:
            continue
        call = a.files[cr.file_idx].parsed.calls[cr.call_idx]  # type: ignore[union-attr]
        if call.caller is None:
            continue
        caller = symbol_key(a, cr.file_idx, call.caller)
        if caller in labelled:
            resolved[caller].setdefault(symbol_key(a, *cr.target), cr.resolution or "")
    tp = fp = fn = 0
    by_kind: dict[str, list[int]] = defaultdict(lambda: [0, 0])  # kind -> [correct, total]
    wrong: list[str] = []
    missed: list[str] = []
    for caller, expected in labelled.items():
        got = resolved.get(caller, {})
        for callee, kind in got.items():
            ok = callee in expected
            by_kind[kind][1] += 1
            by_kind[kind][0] += int(ok)
            if not ok:
                wrong.append(f"{caller} -> {callee} ({kind})")
        tp += len(set(got) & expected)
        fp += len(set(got) - expected)
        fn += len(expected - set(got))
        missed.extend(f"{caller} -> {c}" for c in sorted(expected - set(got)))
    return {
        "callers": len(labelled),
        "edges": sum(len(v) for v in labelled.values()),
        "overall": _prf(tp, fp, fn),
        "precision_by_resolution": {
            k: {"correct": c, "total": t, "precision": c / t if t else None} for k, (c, t) in sorted(by_kind.items())
        },
        "wrong": sorted(wrong),
        "missed": missed,
    }


def evaluate_cycles(a: Analysis, truth: dict[str, Any]) -> dict[str, Any]:
    expected = {frozenset(c) for c in truth["cycles"]}
    found = {frozenset(a.files[i].path for i in c) for c in a.cycles}
    return _prf(len(expected & found), len(found - expected), len(expected - found))
