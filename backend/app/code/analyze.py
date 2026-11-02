"""Analyse a whole snapshot in memory: parse → resolve → graph → rules → chunks.

Pure function of the source files (no database), so the pipeline, the tests and the
evaluation all exercise exactly the same code.

**File health score** (0–100, shown as grades A–E) is a transparent rule, not a model:
100 minus finding penalties (high 15, medium 6, low 2, info 0) scaled per 100 lines of
code, so one issue in a large file weighs less than the same issue in a small one.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from app.code import duplicates, graph
from app.code.archive import SourceFile
from app.code.chunking import Chunk, chunk_file
from app.code.languages import is_test_path
from app.code.parser import ParsedFile, parse_source
from app.code.resolve import FileInput, Resolved, SymRef, resolve
from app.code.rules import Finding, dead_code_findings, file_findings

PENALTY = {"high": 15, "medium": 6, "low": 2, "info": 0}
ROUTE_DECORATOR = re.compile(r"^(?:\w+\.)?(get|post|put|patch|delete|route|api_route|websocket)\(", re.IGNORECASE)


@dataclass
class FileResult:
    path: str
    language: str
    content: str
    sha256: str
    size_bytes: int
    is_test: bool
    loc: int
    parsed: ParsedFile | None
    chunks: list[Chunk] = field(default_factory=list)
    health: int = 100

    @property
    def grade(self) -> str:
        return grade(self.health)


@dataclass
class Analysis:
    files: list[FileResult]
    resolved: Resolved
    findings: list[Finding]
    duplicate_pairs: list[tuple[SymRef, SymRef, float]]
    cycles: list[list[int]]
    edges: dict[int, set[int]]
    centrality: dict[int, float]
    file_fan_in: dict[int, int]
    file_fan_out: dict[int, int]
    symbol_fan_in: dict[SymRef, int]
    symbol_fan_out: dict[SymRef, int]
    tested: set[SymRef]
    stats: dict[str, Any]


def grade(score: float) -> str:
    return "A" if score >= 90 else "B" if score >= 75 else "C" if score >= 60 else "D" if score >= 40 else "E"


def analyze(sources: list[SourceFile]) -> Analysis:
    files: list[FileResult] = []
    for sf in sources:
        parsed = None
        if sf.language != "text":
            parsed = parse_source(sf.language, sf.content)
        loc = sum(1 for line in sf.content.splitlines() if line.strip())
        fr = FileResult(sf.path, sf.language, sf.content, sf.sha256, sf.size_bytes, is_test_path(sf.path), loc, parsed)
        fr.chunks = chunk_file(sf.language, sf.content, parsed)
        files.append(fr)

    inputs = [FileInput(f.path, f.language, f.parsed, f.content) for f in files]
    resolved = resolve(inputs)
    is_test = [f.is_test for f in files]

    # ---- symbol-level call graph
    sym_in: Counter[SymRef] = Counter()
    sym_out: dict[SymRef, set[SymRef]] = defaultdict(set)
    tested: set[SymRef] = set()
    for cr in resolved.calls:
        if cr.target is None:
            continue
        call = files[cr.file_idx].parsed.calls[cr.call_idx]  # type: ignore[union-attr]
        if call.caller is not None:
            src = (cr.file_idx, call.caller)
            if src != cr.target and cr.target not in sym_out[src]:
                sym_out[src].add(cr.target)
                sym_in[cr.target] += 1
        else:
            sym_in[cr.target] += 1
        if files[cr.file_idx].is_test and not files[cr.target[0]].is_test:
            tested.add(cr.target)

    # ---- file-level import graph
    code_nodes = [i for i, f in enumerate(files) if f.parsed is not None]
    edges: dict[int, set[int]] = defaultdict(set)
    for ir in resolved.imports:
        for t in ir.targets:
            if t != ir.file_idx:
                edges[ir.file_idx].add(t)
    cycles = graph.cycles(code_nodes, edges)
    centrality = graph.pagerank(code_nodes, edges)
    fan_in, fan_out = graph.degrees(code_nodes, edges)

    # ---- findings
    findings: list[Finding] = []
    for fi, f in enumerate(files):
        findings.extend(file_findings(fi, inputs[fi], f.is_test))
    findings.extend(dead_code_findings(inputs, resolved, is_test))
    for cyc in cycles:
        paths = [files[i].path for i in cyc]
        first = cyc[0]
        line = next(
            (
                files[first].parsed.imports[ir.import_idx].line  # type: ignore[union-attr]
                for ir in resolved.imports
                if ir.file_idx == first and any(t in cyc for t in ir.targets)
            ),
            1,
        )
        findings.append(
            Finding(
                "import-cycle",
                "medium",
                first,
                line,
                None,
                "Import cycle: " + " → ".join([*paths, paths[0]]),
                key="|".join(sorted(paths)),
            )
        )
    items: list[tuple[int, list[str]]] = []
    keys: list[SymRef] = []
    for fi, f in enumerate(files):
        if f.parsed is None or f.is_test:
            continue
        for si, s in enumerate(f.parsed.symbols):
            if s.kind != "class" and s.loc >= 5:
                keys.append((fi, si))
                items.append((len(keys) - 1, s.tokens))
    pairs = duplicates.find_duplicates(items)
    dup_pairs: list[tuple[SymRef, SymRef, float]] = []
    for a, b, sim in pairs:
        ra, rb = keys[a], keys[b]
        sa = files[ra[0]].parsed.symbols[ra[1]]  # type: ignore[union-attr]
        sb = files[rb[0]].parsed.symbols[rb[1]]  # type: ignore[union-attr]
        if sa.parent == rb[1] or sb.parent == ra[1]:
            continue
        dup_pairs.append((ra, rb, sim))
        for (fi, si), other, osym in ((ra, rb, sb), (rb, ra, sa)):
            s = files[fi].parsed.symbols[si]  # type: ignore[union-attr]
            findings.append(
                Finding(
                    "duplicate-code",
                    "low",
                    fi,
                    s.start_line,
                    si,
                    f"{s.qualified_name} is {round(sim * 100)}% similar to "
                    f"{osym.qualified_name} in {files[other[0]].path}",
                    key=f"{files[other[0]].path}::{osym.qualified_name}",
                )
            )

    # ---- health
    by_file: dict[int, list[Finding]] = defaultdict(list)
    for fnd in findings:
        by_file[fnd.file_idx].append(fnd)
    for fi, f in enumerate(files):
        penalty = sum(PENALTY[x.severity] for x in by_file.get(fi, []))
        f.health = max(0, round(100 - penalty * 100 / max(100, f.loc)))

    return Analysis(
        files=files,
        resolved=resolved,
        findings=findings,
        duplicate_pairs=dup_pairs,
        cycles=cycles,
        edges=edges,
        centrality=centrality,
        file_fan_in=fan_in,
        file_fan_out=fan_out,
        symbol_fan_in=dict(sym_in),
        symbol_fan_out={k: len(v) for k, v in sym_out.items()},
        tested=tested,
        stats={**_stats(files, resolved, findings, tested, cycles), "entry_points": entry_points(files)[:100]},
    )


def entry_points(files: list[FileResult]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for f in files:
        if f.parsed is None or f.is_test:
            continue
        for m in f.parsed.markers:
            if m.kind == "entry-point":
                out.append({"path": f.path, "line": m.line, "kind": "script", "name": "__main__"})
        for s in f.parsed.symbols:
            if s.name == "main" and s.parent is None:
                out.append({"path": f.path, "line": s.start_line, "kind": "main", "name": s.qualified_name})
            elif any(ROUTE_DECORATOR.match(d) for d in s.decorators):
                route = next(d for d in s.decorators if ROUTE_DECORATOR.match(d))
                out.append(
                    {"path": f.path, "line": s.start_line, "kind": "route", "name": s.qualified_name, "detail": route}
                )
    return out


def _exported(f: FileResult, symbol_idx: int) -> bool:
    return f.parsed is not None and f.parsed.symbols[symbol_idx].exported


def _stats(
    files: list[FileResult], resolved: Resolved, findings: list[Finding], tested: set[SymRef], cycles: list[list[int]]
) -> dict[str, Any]:
    languages: dict[str, dict[str, int]] = {}
    functions = classes = 0
    complexity: list[int] = []
    public_functions = 0
    for f in files:
        lang = languages.setdefault(f.language, {"files": 0, "loc": 0})
        lang["files"] += 1
        lang["loc"] += f.loc
        if f.parsed is None:
            continue
        for s in f.parsed.symbols:
            if s.kind == "class":
                classes += 1
                continue
            functions += 1
            complexity.append(s.complexity)
            if not f.is_test and s.exported:
                public_functions += 1
    external: Counter[str] = Counter()
    for ir in resolved.imports:
        if ir.kind == "external" and ir.package:
            external[ir.package] += 1
    resolved_calls = sum(1 for c in resolved.calls if c.target is not None)
    code_loc = sum(f.loc for f in files if f.parsed is not None)
    weighted = sum(f.health * f.loc for f in files if f.parsed is not None)
    health = round(weighted / code_loc) if code_loc else 100
    tested_public = len([(fi, si) for fi, si in tested if _exported(files[fi], si)])
    return {
        "files": len(files),
        "code_files": sum(1 for f in files if f.parsed is not None),
        "test_files": sum(1 for f in files if f.is_test),
        "loc": sum(f.loc for f in files),
        "code_loc": code_loc,
        "languages": languages,
        "functions": functions,
        "classes": classes,
        "avg_complexity": round(sum(complexity) / len(complexity), 2) if complexity else 0,
        "max_complexity": max(complexity, default=0),
        "calls": len(resolved.calls),
        "resolved_calls": resolved_calls,
        "external_packages": dict(external.most_common()),
        "findings": dict(Counter(f.severity for f in findings)),
        "findings_by_category": dict(Counter(f.category for f in findings)),
        "import_cycles": len(cycles),
        "public_functions": public_functions,
        "public_functions_called_from_tests": tested_public,
        "health": health,
        "grade": grade(health),
        "syntax_errors": sum(f.parsed.syntax_errors for f in files if f.parsed is not None),
    }
