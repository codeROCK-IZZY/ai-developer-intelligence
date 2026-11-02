"""Browse code and read the analysis of a snapshot.

Every endpoint takes a snapshot (or symbol) id and checks, through the snapshot's repository,
that the caller is a member of its workspace; otherwise 404.
"""

from __future__ import annotations

import posixpath
import uuid
from collections import Counter, defaultdict
from typing import Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import aliased

from app.ai.providers import get_llm
from app.api.deps import DB, Viewer
from app.code.analyze import grade
from app.code.rules import RULES, SEVERITY_RANK
from app.core.errors import NotFound, UnprocessableData
from app.db.models import CodeFile, CodeSymbol, Duplicate, FindingRow, ImportRow, Ref, Snapshot
from app.modules.code.impact import compare
from app.modules.workspaces.service import require_repository, require_snapshot

router = APIRouter(tags=["code"])


class FileOut(BaseModel):
    id: int
    path: str
    language: str
    loc: int
    is_test: bool
    health: int
    grade: str
    centrality: float
    fan_in: int
    fan_out: int
    findings: dict[str, int]


class SymbolOut(BaseModel):
    id: int
    file_id: int
    path: str
    kind: str
    name: str
    qualified_name: str
    start_line: int
    end_line: int
    signature: str
    docstring: str
    exported: bool
    decorators: list[str]
    params: int
    complexity: int
    max_nesting: int
    loc: int
    fan_in: int
    fan_out: int
    tested: bool


class FindingOut(BaseModel):
    id: int
    rule: str
    title: str
    category: str
    severity: str
    path: str
    file_id: int
    line: int
    symbol_id: int | None
    symbol: str | None
    message: str
    evidence: str
    fix: str


def _ready(snap: Snapshot) -> Snapshot:
    if snap.status != "ready":
        raise UnprocessableData(f"The snapshot is {snap.status}")
    return snap


def _symbol_out(s: CodeSymbol, path: str) -> SymbolOut:
    return SymbolOut(
        id=s.id,
        file_id=s.file_id,
        path=path,
        kind=s.kind,
        name=s.name,
        qualified_name=s.qualified_name,
        start_line=s.start_line,
        end_line=s.end_line,
        signature=s.signature,
        docstring=s.docstring,
        exported=s.exported,
        decorators=s.decorators,
        params=s.params,
        complexity=s.complexity,
        max_nesting=s.max_nesting,
        loc=s.loc,
        fan_in=s.fan_in,
        fan_out=s.fan_out,
        tested=s.tested,
    )


def _finding_out(f: FindingRow, path: str, symbol: str | None) -> FindingOut:
    meta = RULES[f.rule]
    return FindingOut(
        id=f.id,
        rule=f.rule,
        title=meta["title"],
        category=f.category,
        severity=f.severity,
        path=path,
        file_id=f.file_id,
        line=f.line,
        symbol_id=f.symbol_id,
        symbol=symbol,
        message=f.message,
        evidence=f.evidence,
        fix=meta["fix"],
    )


# ---------------------------------------------------------------------- overview
@router.get("/snapshots/{snapshot_id}/overview")
def overview(snapshot_id: uuid.UUID, db: DB, user: Viewer) -> dict[str, Any]:
    """Everything a newcomer needs first: size, languages, health, the core files to read,
    entry points, the most complex functions and the most important findings."""
    snap = _ready(require_snapshot(db, user, snapshot_id))
    central = db.execute(
        select(CodeFile.id, CodeFile.path, CodeFile.centrality, CodeFile.fan_in, CodeFile.health)
        .where(CodeFile.snapshot_id == snap.id, CodeFile.language != "text", ~CodeFile.is_test)
        .order_by(CodeFile.centrality.desc())
        .limit(10)
    ).all()
    complex_rows = db.execute(
        select(CodeSymbol, CodeFile.path)
        .join(CodeFile, CodeFile.id == CodeSymbol.file_id)
        .where(CodeSymbol.snapshot_id == snap.id, CodeSymbol.kind != "class", ~CodeFile.is_test)
        .order_by(CodeSymbol.complexity.desc(), CodeSymbol.loc.desc())
        .limit(10)
    ).all()
    most_used = db.execute(
        select(CodeSymbol, CodeFile.path)
        .join(CodeFile, CodeFile.id == CodeSymbol.file_id)
        .where(CodeSymbol.snapshot_id == snap.id, ~CodeFile.is_test, CodeSymbol.fan_in > 0)
        .order_by(CodeSymbol.fan_in.desc())
        .limit(10)
    ).all()
    top_findings = _findings_query(db, snap.id, min_severity="medium", limit=10)
    directories: Counter[str] = Counter()
    for path, loc in db.execute(select(CodeFile.path, CodeFile.loc).where(CodeFile.snapshot_id == snap.id)):
        directories[path.split("/")[0] if "/" in path else "."] += loc
    return {
        "snapshot": {"id": snap.id, "seq": snap.seq, "label": snap.label, "processed_at": snap.processed_at},
        "stats": snap.stats,
        "skipped": snap.skipped,
        "core_files": [
            {
                "id": r.id,
                "path": r.path,
                "centrality": round(r.centrality, 4),
                "imported_by": r.fan_in,
                "health": r.health,
                "grade": grade(r.health),
            }
            for r in central
        ],
        "complex_functions": [_symbol_out(s, p) for s, p in complex_rows],
        "most_used": [_symbol_out(s, p) for s, p in most_used],
        "top_findings": top_findings,
        "top_directories": [{"directory": d, "loc": n} for d, n in directories.most_common(12)],
    }


# ---------------------------------------------------------------------- files
@router.get("/snapshots/{snapshot_id}/files", response_model=list[FileOut])
def list_files(snapshot_id: uuid.UUID, db: DB, user: Viewer) -> list[FileOut]:
    snap = _ready(require_snapshot(db, user, snapshot_id))
    counts: dict[int, dict[str, int]] = defaultdict(dict)
    for file_id, severity, n in db.execute(
        select(FindingRow.file_id, FindingRow.severity, func.count())
        .where(FindingRow.snapshot_id == snap.id, FindingRow.severity != "info")
        .group_by(FindingRow.file_id, FindingRow.severity)
    ):
        counts[file_id][severity] = n
    rows = db.execute(
        select(
            CodeFile.id,
            CodeFile.path,
            CodeFile.language,
            CodeFile.loc,
            CodeFile.is_test,
            CodeFile.health,
            CodeFile.centrality,
            CodeFile.fan_in,
            CodeFile.fan_out,
        )
        .where(CodeFile.snapshot_id == snap.id)
        .order_by(CodeFile.path)
    ).all()
    return [
        FileOut(
            id=r.id,
            path=r.path,
            language=r.language,
            loc=r.loc,
            is_test=r.is_test,
            health=r.health,
            grade=grade(r.health),
            centrality=round(r.centrality, 5),
            fan_in=r.fan_in,
            fan_out=r.fan_out,
            findings=counts.get(r.id, {}),
        )
        for r in rows
    ]


@router.get("/snapshots/{snapshot_id}/file")
def get_file(snapshot_id: uuid.UUID, db: DB, user: Viewer, path: str = Query(max_length=1000)) -> dict[str, Any]:
    snap = _ready(require_snapshot(db, user, snapshot_id))
    f = db.scalar(select(CodeFile).where(CodeFile.snapshot_id == snap.id, CodeFile.path == path))
    if f is None:
        raise NotFound("File not found in this snapshot")
    symbols = db.scalars(select(CodeSymbol).where(CodeSymbol.file_id == f.id).order_by(CodeSymbol.start_line)).all()
    names = {s.id: s.qualified_name for s in symbols}
    findings = db.scalars(select(FindingRow).where(FindingRow.file_id == f.id).order_by(FindingRow.line)).all()
    imports = db.scalars(select(ImportRow).where(ImportRow.file_id == f.id).order_by(ImportRow.line)).all()
    target_ids = {t for i in imports for t in i.target_file_ids}
    target_paths = (
        dict(db.execute(select(CodeFile.id, CodeFile.path).where(CodeFile.id.in_(target_ids))).all())
        if target_ids
        else {}
    )
    imported_by = db.execute(
        select(CodeFile.path)
        .join(ImportRow, ImportRow.file_id == CodeFile.id)
        .where(ImportRow.snapshot_id == snap.id, ImportRow.target_file_ids.contains([f.id]))
        .distinct()
        .order_by(CodeFile.path)
    ).scalars()
    return {
        "id": f.id,
        "path": f.path,
        "language": f.language,
        "loc": f.loc,
        "is_test": f.is_test,
        "health": f.health,
        "grade": grade(f.health),
        "content": f.content,
        "symbols": [_symbol_out(s, f.path) for s in symbols],
        "findings": [_finding_out(x, f.path, names.get(x.symbol_id or -1)) for x in findings],
        "imports": [
            {
                "module": i.module,
                "line": i.line,
                "kind": i.kind,
                "package": i.package,
                "targets": [target_paths[t] for t in i.target_file_ids if t in target_paths],
            }
            for i in imports
        ],
        "imported_by": list(imported_by),
    }


# ---------------------------------------------------------------------- symbols
@router.get("/snapshots/{snapshot_id}/symbols", response_model=list[SymbolOut])
def find_symbols(
    snapshot_id: uuid.UUID,
    db: DB,
    user: Viewer,
    q: str = Query(default="", max_length=200),
    kind: Literal["function", "method", "class"] | None = None,
    limit: int = Query(default=30, ge=1, le=200),
) -> list[SymbolOut]:
    """Go-to-symbol: exact name first, then prefix, then substring matches."""
    snap = _ready(require_snapshot(db, user, snapshot_id))
    stmt = (
        select(CodeSymbol, CodeFile.path)
        .join(CodeFile, CodeFile.id == CodeSymbol.file_id)
        .where(CodeSymbol.snapshot_id == snap.id)
    )
    if kind:
        stmt = stmt.where(CodeSymbol.kind == kind)
    term = q.strip().lower()
    if term:
        like = "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        rank = (
            func.lower(CodeSymbol.name) == term,
            func.lower(CodeSymbol.name).startswith(term, autoescape=True),
        )
        stmt = stmt.where(
            or_(func.lower(CodeSymbol.name).like(like), func.lower(CodeSymbol.qualified_name).like(like))
        ).order_by(rank[0].desc(), rank[1].desc(), func.length(CodeSymbol.name), CodeFile.path)
    else:
        stmt = stmt.order_by(CodeSymbol.fan_in.desc(), CodeFile.path)
    return [_symbol_out(s, p) for s, p in db.execute(stmt.limit(limit)).all()]


def _require_symbol(db: DB, user: Any, symbol_id: int) -> tuple[CodeSymbol, CodeFile]:
    sym = db.get(CodeSymbol, symbol_id)
    if sym is None:
        raise NotFound("Symbol not found")
    try:
        require_snapshot(db, user, sym.snapshot_id)
    except NotFound as exc:
        raise NotFound("Symbol not found") from exc
    f = db.get(CodeFile, sym.file_id)
    assert f is not None
    return sym, f


def _neighbours(db: DB, sym: CodeSymbol) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    caller = aliased(CodeSymbol)
    callers = db.execute(
        select(caller, CodeFile.path, func.min(Ref.line), func.count())
        .join(Ref, Ref.caller_id == caller.id)
        .join(CodeFile, CodeFile.id == caller.file_id)
        .where(Ref.callee_id == sym.id)
        .group_by(caller.id, CodeFile.path)
        .order_by(CodeFile.path, caller.start_line)
    ).all()
    callee = aliased(CodeSymbol)
    callees = db.execute(
        select(callee, CodeFile.path, func.min(Ref.line), func.count())
        .join(Ref, Ref.callee_id == callee.id)
        .join(CodeFile, CodeFile.id == callee.file_id)
        .where(Ref.caller_id == sym.id)
        .group_by(callee.id, CodeFile.path)
        .order_by(func.min(Ref.line))
    ).all()
    unresolved = db.execute(
        select(Ref.name, Ref.receiver, func.min(Ref.line), func.count())
        .where(Ref.caller_id == sym.id, Ref.callee_id.is_(None))
        .group_by(Ref.name, Ref.receiver)
        .order_by(func.min(Ref.line))
    ).all()

    def brief(s: CodeSymbol, path: str, line: int, n: int) -> dict[str, Any]:
        return {
            "id": s.id,
            "qualified_name": s.qualified_name,
            "kind": s.kind,
            "path": path,
            "start_line": s.start_line,
            "line": line,
            "calls": n,
        }

    return (
        [brief(s, p, ln, n) for s, p, ln, n in callers],
        [brief(s, p, ln, n) for s, p, ln, n in callees],
        [{"name": (f"{r}." if r else "") + nm, "line": ln, "calls": n} for nm, r, ln, n in unresolved],
    )


@router.get("/symbols/{symbol_id}")
def get_symbol(symbol_id: int, db: DB, user: Viewer) -> dict[str, Any]:
    sym, f = _require_symbol(db, user, symbol_id)
    callers, callees, external = _neighbours(db, sym)
    findings = db.scalars(select(FindingRow).where(FindingRow.symbol_id == sym.id).order_by(FindingRow.line)).all()
    other = aliased(CodeSymbol)
    dups = db.execute(
        select(Duplicate.similarity, other, CodeFile.path)
        .join(
            other,
            or_(
                and_(Duplicate.symbol_a == sym.id, other.id == Duplicate.symbol_b),
                and_(Duplicate.symbol_b == sym.id, other.id == Duplicate.symbol_a),
            ),
        )
        .join(CodeFile, CodeFile.id == other.file_id)
    ).all()
    children = db.scalars(select(CodeSymbol).where(CodeSymbol.parent_id == sym.id).order_by(CodeSymbol.start_line))
    lines = f.content.splitlines()
    return {
        "symbol": _symbol_out(sym, f.path),
        "snapshot_id": sym.snapshot_id,
        "language": f.language,
        "code": "\n".join(lines[sym.start_line - 1 : sym.end_line]),
        "callers": callers,
        "callees": callees,
        "external_calls": external,
        "members": [_symbol_out(c, f.path) for c in children],
        "findings": [_finding_out(x, f.path, sym.qualified_name) for x in findings],
        "duplicates": [
            {"id": o.id, "qualified_name": o.qualified_name, "path": p, "similarity": sim} for sim, o, p in dups
        ],
    }


@router.get("/symbols/{symbol_id}/explain")
def explain_symbol(symbol_id: int, db: DB, user: Viewer) -> dict[str, Any]:
    """Plain-language explanation. With Claude configured it is generated (and labelled as such);
    offline it is assembled from facts the analysis already knows — never invented."""
    sym, f = _require_symbol(db, user, symbol_id)
    callers, callees, external = _neighbours(db, sym)
    lines = f.content.splitlines()
    code = "\n".join(lines[sym.start_line - 1 : sym.end_line])
    facts = _facts(sym, f, callers, callees, external)
    llm = get_llm()
    generated = llm.explain(code[:30_000], "\n".join(f"- {x}" for x in facts)) if llm.name != "offline" else None
    if generated is not None:
        return {"provider": generated[1], "generated": True, "text": generated[0], "facts": facts}
    return {"provider": "offline", "generated": False, "text": " ".join(facts[:3]), "facts": facts}


def _facts(
    sym: CodeSymbol,
    f: CodeFile,
    callers: list[dict[str, Any]],
    callees: list[dict[str, Any]],
    external: list[dict[str, Any]],
) -> list[str]:
    facts = [f"{sym.kind.capitalize()} `{sym.qualified_name}` in {f.path}, lines {sym.start_line}–{sym.end_line}."]
    if sym.docstring:
        facts.append(f"Its documentation says: {sym.docstring}")
    else:
        facts.append("It has no documentation comment.")
    if sym.kind != "class":
        facts.append(
            f"Signature `{sym.signature}`; {sym.params} parameter(s); cyclomatic complexity {sym.complexity}; "
            f"{sym.loc} lines; nesting depth {sym.max_nesting}."
        )
    if callers:
        names = ", ".join(f"`{c['qualified_name']}` ({c['path']})" for c in callers[:6])
        facts.append(f"Called by {len(callers)} function(s) in this repository: {names}.")
    else:
        facts.append(
            "No caller was found in this repository (it may be an entry point, called dynamically, or unused)."
        )
    if callees:
        facts.append("It calls " + ", ".join(f"`{c['qualified_name']}`" for c in callees[:8]) + ".")
    if external:
        facts.append("Library or unresolved calls: " + ", ".join(f"`{e['name']}`" for e in external[:8]) + ".")
    facts.append("A test calls it directly." if sym.tested else "No test calls it directly.")
    if sym.decorators:
        facts.append("Decorators: " + ", ".join(f"`@{d}`" for d in sym.decorators) + ".")
    return facts


# ---------------------------------------------------------------------- findings
def _findings_query(
    db: DB,
    snapshot_id: uuid.UUID,
    *,
    min_severity: str | None = None,
    severity: str | None = None,
    category: str | None = None,
    rule: str | None = None,
    path_prefix: str | None = None,
    limit: int = 500,
) -> list[FindingOut]:
    stmt = (
        select(FindingRow, CodeFile.path, CodeSymbol.qualified_name)
        .join(CodeFile, CodeFile.id == FindingRow.file_id)
        .outerjoin(CodeSymbol, CodeSymbol.id == FindingRow.symbol_id)
        .where(FindingRow.snapshot_id == snapshot_id)
    )
    if severity:
        stmt = stmt.where(FindingRow.severity == severity)
    if min_severity:
        allowed = [s for s, r in SEVERITY_RANK.items() if r >= SEVERITY_RANK[min_severity]]
        stmt = stmt.where(FindingRow.severity.in_(allowed))
    if category:
        stmt = stmt.where(FindingRow.category == category)
    if rule:
        stmt = stmt.where(FindingRow.rule == rule)
    if path_prefix:
        stmt = stmt.where(CodeFile.path.startswith(path_prefix, autoescape=True))
    rows = db.execute(stmt.order_by(CodeFile.path, FindingRow.line)).all()
    out = [_finding_out(f, p, q) for f, p, q in rows]
    out.sort(key=lambda x: (-SEVERITY_RANK[x.severity], x.category, x.path, x.line))
    return out[:limit]


@router.get("/snapshots/{snapshot_id}/findings")
def list_findings(
    snapshot_id: uuid.UUID,
    db: DB,
    user: Viewer,
    severity: Literal["info", "low", "medium", "high"] | None = None,
    min_severity: Literal["info", "low", "medium", "high"] | None = None,
    category: str | None = Query(default=None, max_length=20),
    rule: str | None = Query(default=None, max_length=40),
    path_prefix: str | None = Query(default=None, max_length=500),
    limit: int = Query(default=500, ge=1, le=2000),
) -> dict[str, Any]:
    snap = _ready(require_snapshot(db, user, snapshot_id))
    items = _findings_query(
        db,
        snap.id,
        severity=severity,
        min_severity=min_severity,
        category=category,
        rule=rule,
        path_prefix=path_prefix,
        limit=limit,
    )
    by_rule = db.execute(
        select(FindingRow.rule, FindingRow.severity, func.count())
        .where(FindingRow.snapshot_id == snap.id)
        .group_by(FindingRow.rule, FindingRow.severity)
    ).all()
    return {
        "items": items,
        "summary": [
            {"rule": r, "title": RULES[r]["title"], "category": RULES[r]["category"], "severity": s, "count": n}
            for r, s, n in sorted(by_rule, key=lambda x: (-SEVERITY_RANK[x[1]], -x[2]))
        ],
        "rules": {k: v for k, v in RULES.items()},
    }


# ---------------------------------------------------------------------- architecture
@router.get("/snapshots/{snapshot_id}/dependencies")
def dependencies(
    snapshot_id: uuid.UUID,
    db: DB,
    user: Viewer,
    level: Literal["file", "directory"] = "directory",
    depth: int = Query(default=2, ge=1, le=6),
) -> dict[str, Any]:
    """Internal import graph, at file level or grouped by directory (``depth`` path segments)."""
    snap = _ready(require_snapshot(db, user, snapshot_id))
    files = {
        r.id: r
        for r in db.execute(
            select(
                CodeFile.id, CodeFile.path, CodeFile.loc, CodeFile.is_test, CodeFile.health, CodeFile.centrality
            ).where(CodeFile.snapshot_id == snap.id, CodeFile.language != "text")
        ).all()
    }

    def group(path: str) -> str:
        if level == "file":
            return path
        d = posixpath.dirname(path)
        return "/".join(d.split("/")[:depth]) if d else "."

    nodes: dict[str, dict[str, Any]] = {}
    for r in files.values():
        g = group(r.path)
        n = nodes.setdefault(g, {"id": g, "files": 0, "loc": 0, "tests": 0, "health_loc": 0})
        n["files"] += 1
        n["loc"] += r.loc
        n["tests"] += int(r.is_test)
        n["health_loc"] += r.health * r.loc
    edges: Counter[tuple[str, str]] = Counter()
    for file_id, targets in db.execute(
        select(ImportRow.file_id, ImportRow.target_file_ids).where(
            ImportRow.snapshot_id == snap.id, ImportRow.kind == "internal"
        )
    ):
        if file_id not in files:
            continue
        src = group(files[file_id].path)
        for t in targets:
            if t in files:
                dst = group(files[t].path)
                if dst != src:
                    edges[(src, dst)] += 1
    cycles = [
        f.message.removeprefix("Import cycle: ")
        for f in db.scalars(
            select(FindingRow).where(FindingRow.snapshot_id == snap.id, FindingRow.rule == "import-cycle")
        )
    ]
    pairs = set(edges)
    return {
        "level": level,
        "nodes": [
            {
                "id": n["id"],
                "files": n["files"],
                "loc": n["loc"],
                "tests": n["tests"],
                "health": round(n["health_loc"] / n["loc"]) if n["loc"] else 100,
            }
            for n in sorted(nodes.values(), key=lambda n: n["id"])
        ],
        "edges": [
            {"source": s, "target": t, "imports": n, "mutual": (t, s) in pairs} for (s, t), n in sorted(edges.items())
        ],
        "cycles": cycles,
    }


@router.get("/snapshots/{snapshot_id}/duplicates")
def duplicates(snapshot_id: uuid.UUID, db: DB, user: Viewer) -> list[dict[str, Any]]:
    snap = _ready(require_snapshot(db, user, snapshot_id))
    a, b = aliased(CodeSymbol), aliased(CodeSymbol)
    fa, fb = aliased(CodeFile), aliased(CodeFile)
    rows = db.execute(
        select(Duplicate.similarity, a, fa.path, b, fb.path)
        .join(a, a.id == Duplicate.symbol_a)
        .join(b, b.id == Duplicate.symbol_b)
        .join(fa, fa.id == a.file_id)
        .join(fb, fb.id == b.file_id)
        .where(Duplicate.snapshot_id == snap.id)
        .order_by(Duplicate.similarity.desc())
    ).all()
    return [{"similarity": sim, "a": _symbol_out(sa, pa), "b": _symbol_out(sb, pb)} for sim, sa, pa, sb, pb in rows]


@router.get("/repositories/{repository_id}/compare")
def compare_snapshots(
    repository_id: uuid.UUID, base: uuid.UUID, head: uuid.UUID, db: DB, user: Viewer
) -> dict[str, Any]:
    """Change impact between two snapshots: what changed, what calls it, tests to run, risk."""
    repo = require_repository(db, user, repository_id)
    snaps = {s.id: s for s in db.scalars(select(Snapshot).where(Snapshot.id.in_([base, head])))}
    if base not in snaps or head not in snaps or any(s.repository_id != repo.id for s in snaps.values()):
        raise NotFound("Snapshot not found in this repository")
    return compare(db, _ready(snaps[base]), _ready(snaps[head]))
