"""Snapshot processing: archive → analysis → database → embeddings.

Runs in the worker (job type ``process_snapshot``). Idempotent: rows from a previous attempt
for the same snapshot are deleted first, so a retried job never duplicates data.

Stages (recorded on the snapshot so the UI can show progress):
``fetch`` (GitHub download, if needed) → ``analyze`` (parse, resolve, rules) → ``store`` →
``embed`` (local embedding model) → ``ready``.

Incremental indexing: each chunk stores a hash of the text it was embedded from; when a new
snapshot of the same repository is processed, chunks whose text is unchanged reuse the previous
snapshot's embeddings, so re-importing after a small change costs little.

Bulk inserts use SQLAlchemy "insertmanyvalues" with ``RETURNING`` in parameter order, so the
database ids of files and symbols can be mapped back to the in-memory analysis.
"""

from __future__ import annotations

import hashlib
import logging
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, insert, select, text, update
from sqlalchemy.orm import Session

from app.ai.providers import get_embedder
from app.code.analyze import Analysis, analyze
from app.code.archive import read_archive
from app.code.chunking import embedding_text, search_text
from app.code.rules import fingerprint
from app.connectors.github import download_archive, parse_repo_url
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.logging import log_extra
from app.db.models import (
    CodeFile,
    CodeSymbol,
    Duplicate,
    FindingRow,
    ImportRow,
    Ref,
    Repository,
    Snapshot,
)
from app.storage.files import get_store

logger = logging.getLogger(__name__)
EMBED_BATCH = 64

INSERT_CHUNK_SQL = text(
    """
    INSERT INTO chunks (snapshot_id, file_id, symbol_id, kind, start_line, end_line, text, content_hash, tsv, embedding)
    VALUES (:snapshot_id, :file_id, :symbol_id, :kind, :start_line, :end_line, :text, :content_hash,
            to_tsvector('english', :search), CAST(:embedding AS vector))
    """
)


def _stage(db: Session, snap: Snapshot, stage: str) -> None:
    snap.stage = stage
    db.commit()


def _reset(db: Session, snapshot_id: uuid.UUID) -> None:
    # files cascade to symbols, refs, imports, chunks, findings and duplicates
    db.execute(delete(CodeFile).where(CodeFile.snapshot_id == snapshot_id))


def process_snapshot(db: Session, snapshot_id: uuid.UUID) -> dict[str, Any]:
    snap = db.get(Snapshot, snapshot_id)
    if snap is None:
        return {"status": "missing"}
    started = time.perf_counter()
    snap.status, snap.error = "processing", None
    try:
        data = _fetch(db, snap)
        _stage(db, snap, "analyze")
        s = get_settings()
        contents = read_archive(
            data,
            max_entries=s.archive_max_entries,
            max_total_mb=s.archive_max_uncompressed_mb,
            max_file_kb=s.max_file_kb,
            max_files=s.max_files,
        )
        if not contents.files:
            raise AppError("The archive contains no supported source files")
        result = analyze(contents.files)
        _stage(db, snap, "store")
        _reset(db, snap.id)
        ids = _store(db, snap.id, result)
        _stage(db, snap, "embed")
        n_chunks, reused = _embed(db, snap.id, result, ids)
    except AppError as exc:  # bad input: retrying will not help
        db.rollback()
        snap = db.get(Snapshot, snapshot_id, populate_existing=True)
        assert snap is not None
        snap.status, snap.error, snap.stage = "failed", exc.detail, None
        db.commit()
        return {"status": "failed", "error": exc.detail}
    except Exception as exc:
        db.rollback()
        failed = db.get(Snapshot, snapshot_id, populate_existing=True)
        if failed is not None:
            failed.status, failed.error = "failed", f"Processing error: {exc.__class__.__name__}"
            db.commit()
        raise
    snap.stats = {
        **result.stats,
        "chunks": n_chunks,
        "embeddings_reused": reused,
        "seconds": round(time.perf_counter() - started, 2),
    }
    snap.skipped = contents.skipped
    snap.status, snap.stage = "ready", None
    snap.processed_at = datetime.now(UTC)
    repo = db.get(Repository, snap.repository_id)
    if repo is not None:
        latest = db.get(Snapshot, repo.latest_snapshot_id) if repo.latest_snapshot_id else None
        if latest is None or latest.seq <= snap.seq:
            repo.latest_snapshot_id = snap.id
    db.commit()
    logger.info("snapshot ready", extra=log_extra(snapshot_id=str(snap.id), files=len(result.files), chunks=n_chunks))
    return {"status": "ready", "files": len(result.files), "chunks": n_chunks}


def _fetch(db: Session, snap: Snapshot) -> bytes:
    store = get_store()
    if snap.file_key:
        return store.get(snap.file_key)
    if snap.source != "github":
        raise AppError("The snapshot has no archive")
    _stage(db, snap, "fetch")
    repo = db.get(Repository, snap.repository_id)
    if repo is None or not repo.source_url:
        raise AppError("The repository has no GitHub URL")
    data = download_archive(parse_repo_url(repo.source_url), snap.ref or repo.default_ref or "HEAD")
    key = f"snapshots/{snap.repository_id}/{snap.id}.zip"
    store.put(key, data)
    snap.file_key = key
    snap.archive_sha256 = hashlib.sha256(data).hexdigest()
    db.commit()
    return data


def _store(db: Session, snapshot_id: uuid.UUID, a: Analysis) -> dict[str, Any]:
    file_rows = [
        {
            "snapshot_id": snapshot_id,
            "path": f.path,
            "language": f.language,
            "sha256": f.sha256,
            "size_bytes": f.size_bytes,
            "loc": f.loc,
            "is_test": f.is_test,
            "health": f.health,
            "centrality": round(a.centrality.get(i, 0.0), 6),
            "fan_in": a.file_fan_in.get(i, 0),
            "fan_out": a.file_fan_out.get(i, 0),
            "content": f.content,
        }
        for i, f in enumerate(a.files)
    ]
    file_ids: list[int] = list(
        db.scalars(insert(CodeFile).returning(CodeFile.id, sort_by_parameter_order=True), file_rows)
    )
    sym_keys: list[tuple[int, int]] = []
    sym_rows: list[dict[str, Any]] = []
    for fi, f in enumerate(a.files):
        for si, s in enumerate(f.parsed.symbols if f.parsed else []):
            sym_keys.append((fi, si))
            sym_rows.append(
                {
                    "snapshot_id": snapshot_id,
                    "file_id": file_ids[fi],
                    "kind": s.kind,
                    "name": s.name[:300],
                    "qualified_name": s.qualified_name[:1000],
                    "start_line": s.start_line,
                    "end_line": s.end_line,
                    "signature": s.signature,
                    "docstring": s.docstring,
                    "exported": s.exported,
                    "decorators": s.decorators,
                    "params": s.params,
                    "complexity": s.complexity,
                    "max_nesting": s.max_nesting,
                    "loc": s.loc,
                    "body_hash": s.body_hash,
                    "fan_in": a.symbol_fan_in.get((fi, si), 0),
                    "fan_out": a.symbol_fan_out.get((fi, si), 0),
                    "tested": (fi, si) in a.tested,
                }
            )
    sym_id: dict[tuple[int, int], int] = {}
    if sym_rows:
        ids = list(db.scalars(insert(CodeSymbol).returning(CodeSymbol.id, sort_by_parameter_order=True), sym_rows))
        sym_id = dict(zip(sym_keys, ids, strict=True))
        parents = [
            {"id": sym_id[(fi, si)], "parent_id": sym_id[(fi, s.parent)]}
            for (fi, si) in sym_keys
            if (s := a.files[fi].parsed.symbols[si]).parent is not None  # type: ignore[union-attr]
        ]
        if parents:
            db.execute(update(CodeSymbol), parents)

    def sid(fi: int, si: int | None) -> int | None:
        return sym_id.get((fi, si)) if si is not None else None

    ref_rows = []
    for cr in a.resolved.calls:
        call = a.files[cr.file_idx].parsed.calls[cr.call_idx]  # type: ignore[union-attr]
        ref_rows.append(
            {
                "snapshot_id": snapshot_id,
                "file_id": file_ids[cr.file_idx],
                "caller_id": sid(cr.file_idx, call.caller),
                "callee_id": sym_id.get(cr.target) if cr.target else None,
                "name": call.name[:300],
                "receiver": call.receiver[:100] if call.receiver else None,
                "line": call.line,
                "resolution": cr.resolution,
            }
        )
    if ref_rows:
        db.execute(insert(Ref), ref_rows)
    import_rows = [
        {
            "snapshot_id": snapshot_id,
            "file_id": file_ids[ir.file_idx],
            "module": (a.files[ir.file_idx].parsed.imports[ir.import_idx].module or ".")[:500],  # type: ignore[union-attr]
            "line": a.files[ir.file_idx].parsed.imports[ir.import_idx].line,  # type: ignore[union-attr]
            "kind": ir.kind,
            "package": ir.package,
            "target_file_ids": [file_ids[t] for t in ir.targets],
        }
        for ir in a.resolved.imports
    ]
    if import_rows:
        db.execute(insert(ImportRow), import_rows)
    finding_rows = []
    for fnd in a.findings:
        f = a.files[fnd.file_idx]
        qual = f.parsed.symbols[fnd.symbol].qualified_name if f.parsed and fnd.symbol is not None else None
        finding_rows.append(
            {
                "snapshot_id": snapshot_id,
                "file_id": file_ids[fnd.file_idx],
                "symbol_id": sid(fnd.file_idx, fnd.symbol),
                "rule": fnd.rule,
                "category": fnd.category,
                "severity": fnd.severity,
                "line": fnd.line,
                "message": fnd.message,
                "evidence": fnd.evidence[:500],
                "fingerprint": fingerprint(fnd.rule, f.path, qual, fnd.key),
            }
        )
    if finding_rows:
        db.execute(insert(FindingRow), finding_rows)
    dup_rows = [
        {"snapshot_id": snapshot_id, "symbol_a": sym_id[x], "symbol_b": sym_id[y], "similarity": sim}
        for x, y, sim in a.duplicate_pairs
    ]
    if dup_rows:
        db.execute(insert(Duplicate), dup_rows)
    db.commit()
    return {"file_ids": file_ids, "symbol_ids": sym_id}


def _previous_embeddings(db: Session, snapshot_id: uuid.UUID) -> dict[str, str]:
    """Embeddings of the previous ready snapshot of the same repository, by content hash."""
    snap = db.get(Snapshot, snapshot_id)
    assert snap is not None
    prev = db.scalar(
        select(Snapshot.id)
        .where(Snapshot.repository_id == snap.repository_id, Snapshot.status == "ready", Snapshot.seq < snap.seq)
        .order_by(Snapshot.seq.desc())
        .limit(1)
    )
    if prev is None:
        return {}
    rows = db.execute(
        text("SELECT DISTINCT ON (content_hash) content_hash, embedding::text FROM chunks WHERE snapshot_id = :s"),
        {"s": prev},
    )
    return {h: e for h, e in rows}


def _embed(db: Session, snapshot_id: uuid.UUID, a: Analysis, ids: dict[str, Any]) -> tuple[int, int]:
    """Insert all chunks; only text not seen in the previous snapshot is sent to the embedding model.
    Returns (chunks, embeddings reused)."""
    file_ids: list[int] = ids["file_ids"]
    sym_id: dict[tuple[int, int], int] = ids["symbol_ids"]
    known = _previous_embeddings(db, snapshot_id)
    pending: list[dict[str, Any]] = []
    to_embed: list[tuple[dict[str, Any], str]] = []
    embedder = get_embedder()
    total = reused = 0

    def flush() -> None:
        nonlocal total
        if to_embed:
            vectors = embedder.passages([t for _, t in to_embed])
            for (row, _), vec in zip(to_embed, vectors, strict=True):
                row["embedding"] = str(vec)
        if pending:
            db.execute(INSERT_CHUNK_SQL, pending)
            db.commit()
        total += len(pending)
        pending.clear()
        to_embed.clear()

    for fi, f in enumerate(a.files):
        for ch in f.chunks:
            sym = f.parsed.symbols[ch.symbol] if f.parsed and ch.symbol is not None else None
            embed_text = embedding_text(f.path, ch, sym)
            content_hash = hashlib.sha256(embed_text.encode()).hexdigest()
            row: dict[str, Any] = {
                "snapshot_id": snapshot_id,
                "file_id": file_ids[fi],
                "symbol_id": sym_id.get((fi, ch.symbol)) if ch.symbol is not None else None,
                "kind": ch.kind,
                "start_line": ch.start_line,
                "end_line": ch.end_line,
                "text": ch.text,
                "content_hash": content_hash,
                "search": search_text(f.path, ch, sym),
                "embedding": known.get(content_hash),
            }
            pending.append(row)
            if row["embedding"] is None:
                to_embed.append((row, embed_text))
            else:
                reused += 1
            if len(to_embed) >= EMBED_BATCH or len(pending) >= 4 * EMBED_BATCH:
                flush()
    flush()
    return total, reused


def next_seq(db: Session, repository_id: uuid.UUID) -> int:
    current = db.scalar(
        select(Snapshot.seq).where(Snapshot.repository_id == repository_id).order_by(Snapshot.seq.desc())
    )
    return (current or 0) + 1
