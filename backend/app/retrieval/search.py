"""Hybrid code search with access control inside the query.

1. ``allowed`` CTE — the snapshot to search for each repository the user may read (the latest
   ready snapshot, or one explicitly requested), via the ``readable_repositories`` view.
2. Keyword branch — PostgreSQL full-text search over the chunk's *expanded* text (path words,
   symbol-name parts, docstring, code with identifiers split), OR-ing the query's content words.
3. Vector branch — cosine similarity between the query embedding and each chunk's embedding
   (location + docstring + code).
4. Reciprocal Rank Fusion: score = Σ 1 / (60 + rank) over both branches.
5. Optional re-ranking of the top candidates with a cross-encoder, exposed as ``rerank_score``.

The vector branch is an exact scan over the allowed chunks (no approximate index), so a user
whose repositories are a small share of the data still gets correct results; see
docs/known-limitations.md for the scaling path.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, replace
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.ai.providers import get_embedder, get_reranker
from app.ai.textutil import query_terms
from app.core.config import get_settings

RRF_K = 60
BRANCH_LIMIT = 50
RERANK_CHARS = 1500


@dataclass(frozen=True)
class Hit:
    chunk_id: int
    repository_id: uuid.UUID
    repository_name: str
    snapshot_id: uuid.UUID
    file_id: int
    path: str
    language: str
    is_test: bool
    symbol_id: int | None
    symbol: str | None  # qualified name
    symbol_kind: str | None
    signature: str | None
    docstring: str | None
    start_line: int
    end_line: int
    text: str
    score: float
    vector_similarity: float | None
    keyword_rank: float | None
    rerank_score: float | None = None  # cross-encoder relevance 0–1 (logistic of the raw score)

    @property
    def location(self) -> str:
        return f"{self.path}:{self.start_line}-{self.end_line}"

    def passage(self) -> str:
        """Text shown to the re-ranker and the language model: where, what, then the code."""
        head = self.path
        if self.symbol:
            head += f" — {self.symbol_kind} {self.symbol}"
        if self.docstring:
            head += f"\n{self.docstring}"
        return f"{head}\n{self.text}"[:RERANK_CHARS]


def or_tsquery_text(query: str) -> str | None:
    words = query_terms(query)
    return " | ".join(words) if words else None


ALLOWED_LATEST = """
    SELECT r.id AS repository_id, r.name AS repository_name, r.latest_snapshot_id AS snapshot_id
    FROM readable_repositories rr
    JOIN repositories r ON r.id = rr.repository_id
    WHERE rr.user_id = :user_id AND r.latest_snapshot_id IS NOT NULL {repo_filter}
"""
ALLOWED_SNAPSHOT = """
    SELECT r.id AS repository_id, r.name AS repository_name, s.id AS snapshot_id
    FROM readable_repositories rr
    JOIN repositories r ON r.id = rr.repository_id
    JOIN snapshots s ON s.repository_id = r.id
    WHERE rr.user_id = :user_id AND s.id = :snapshot_id AND s.status = 'ready'
"""

SEARCH_SQL = """
WITH allowed AS ({allowed}),
candidates AS (
    SELECT c.id, c.tsv, c.embedding
    FROM chunks c
    JOIN allowed a ON a.snapshot_id = c.snapshot_id
    JOIN files f ON f.id = c.file_id
    WHERE true {filters}
),
kw AS (
    SELECT c.id, ts_rank_cd(c.tsv, q.query) AS rank_score,
           row_number() OVER (ORDER BY ts_rank_cd(c.tsv, q.query) DESC, c.id) AS rnk
    FROM candidates c
    CROSS JOIN (SELECT to_tsquery('english', :tsq) AS query) q
    WHERE CAST(:tsq AS text) IS NOT NULL AND c.tsv @@ q.query
    ORDER BY rank_score DESC, c.id
    LIMIT :kw_limit
),
vec AS (
    SELECT c.id, 1 - (c.embedding <=> CAST(:qvec AS vector)) AS sim,
           row_number() OVER (ORDER BY c.embedding <=> CAST(:qvec AS vector), c.id) AS rnk
    FROM candidates c
    ORDER BY c.embedding <=> CAST(:qvec AS vector), c.id
    LIMIT :vec_limit
),
fused AS (
    SELECT id, sum(rrf) AS score, max(sim) AS sim, max(rank_score) AS kw
    FROM (
        SELECT id, 1.0 / (:rrf_k + rnk) AS rrf, NULL::float AS sim, rank_score FROM kw
        UNION ALL
        SELECT id, 1.0 / (:rrf_k + rnk), sim, NULL::float FROM vec
    ) u
    GROUP BY id
)
SELECT c.id AS chunk_id, a.repository_id, a.repository_name, c.snapshot_id, f.id AS file_id, f.path, f.language,
       f.is_test, s.id AS symbol_id, s.qualified_name AS symbol, s.kind AS symbol_kind, s.signature, s.docstring,
       c.start_line, c.end_line, c.text, fu.score, fu.sim, fu.kw
FROM fused fu
JOIN chunks c ON c.id = fu.id
JOIN allowed a ON a.snapshot_id = c.snapshot_id
JOIN files f ON f.id = c.file_id
LEFT JOIN symbols s ON s.id = c.symbol_id
ORDER BY fu.score DESC, c.id
LIMIT :limit
"""


def search(
    db: Session,
    user_id: uuid.UUID,
    query: str,
    *,
    limit: int = 10,
    repository_ids: list[uuid.UUID] | None = None,
    snapshot_id: uuid.UUID | None = None,
    language: str | None = None,
    path_prefix: str | None = None,
    include_tests: bool = True,
    mode: str = "hybrid",
    rerank: bool | None = None,
) -> list[Hit]:
    """``mode`` = hybrid | keyword | vector (single-branch modes exist for evaluation).
    ``rerank`` = re-order the top candidates with the cross-encoder (default from settings)."""
    s = get_settings()
    rerank = s.rerank_by_default if rerank is None else rerank
    if mode not in ("hybrid", "keyword", "vector"):
        raise ValueError(f"unknown search mode {mode}")
    params: dict[str, Any] = {}
    if snapshot_id is not None:
        allowed = ALLOWED_SNAPSHOT
        params["snapshot_id"] = snapshot_id
    else:
        repo_filter = ""
        if repository_ids is not None:
            repo_filter = "AND r.id = ANY(:repo_ids)"
            params["repo_ids"] = repository_ids
        allowed = ALLOWED_LATEST.format(repo_filter=repo_filter)
    filters: list[str] = []
    if language:
        filters.append("AND f.language = :language")
        params["language"] = language
    if path_prefix:
        filters.append("AND f.path LIKE :path_prefix")
        params["path_prefix"] = path_prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    if not include_tests:
        filters.append("AND NOT f.is_test")
    tsq = or_tsquery_text(query) if mode != "vector" else None
    rows = (
        db.execute(
            text(SEARCH_SQL.format(allowed=allowed, filters=" ".join(filters))),
            {
                "user_id": user_id,
                "tsq": tsq,
                "qvec": str(get_embedder().query(query)),
                "rrf_k": RRF_K,
                "limit": max(limit, s.rerank_candidates) if rerank else limit,
                "kw_limit": BRANCH_LIMIT if mode != "vector" else 0,
                "vec_limit": BRANCH_LIMIT if mode != "keyword" else 0,
                **params,
            },
        )
        .mappings()
        .all()
    )
    hits = [_hit(r) for r in rows]
    if rerank and hits:
        raw = get_reranker().scores(query, [h.passage() for h in hits])
        scored = [replace(h, rerank_score=1 / (1 + math.exp(-x))) for h, x in zip(hits, raw, strict=True)]
        hits = sorted(scored, key=lambda h: h.rerank_score or 0.0, reverse=True)
    return hits[:limit]


def _hit(r: Any) -> Hit:
    return Hit(
        chunk_id=r["chunk_id"],
        repository_id=r["repository_id"],
        repository_name=r["repository_name"],
        snapshot_id=r["snapshot_id"],
        file_id=r["file_id"],
        path=r["path"],
        language=r["language"],
        is_test=r["is_test"],
        symbol_id=r["symbol_id"],
        symbol=r["symbol"],
        symbol_kind=r["symbol_kind"],
        signature=r["signature"],
        docstring=r["docstring"],
        start_line=r["start_line"],
        end_line=r["end_line"],
        text=r["text"],
        score=float(r["score"]),
        vector_similarity=r["sim"],
        keyword_rank=r["kw"],
    )
