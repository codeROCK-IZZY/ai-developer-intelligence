"""Code search and questions about code."""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import DB, Viewer
from app.core.errors import TooManyRequests
from app.core.ratelimit import SlidingWindowLimiter
from app.db.models import Question, SearchHistory
from app.modules.workspaces.service import require_repository, require_snapshot
from app.retrieval.qa import answer_question
from app.retrieval.search import search

router = APIRouter(tags=["search"])
_ask_limiter = SlidingWindowLimiter(30)


class SearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    repository_ids: list[uuid.UUID] | None = None
    snapshot_id: uuid.UUID | None = None
    language: str | None = Field(default=None, max_length=20)
    path_prefix: str | None = Field(default=None, max_length=500)
    include_tests: bool = True
    mode: Literal["hybrid", "keyword", "vector"] = "hybrid"
    rerank: bool | None = None
    limit: int = Field(default=10, ge=1, le=50)


class HitOut(BaseModel):
    chunk_id: int
    repository_id: uuid.UUID
    repository_name: str
    snapshot_id: uuid.UUID
    file_id: int
    path: str
    language: str
    is_test: bool
    symbol_id: int | None
    symbol: str | None
    symbol_kind: str | None
    signature: str | None
    start_line: int
    end_line: int
    text: str
    score: float
    vector_similarity: float | None
    keyword_rank: float | None
    rerank_score: float | None


class SearchOut(BaseModel):
    query: str
    mode: str
    reranked: bool
    latency_ms: int
    results: list[HitOut]


class AskIn(BaseModel):
    question: str = Field(min_length=3, max_length=1000)
    repository_ids: list[uuid.UUID] | None = None


class QuestionOut(BaseModel):
    id: uuid.UUID
    question: str
    answer: str
    status: str
    provider: str
    citations: list[dict[str, Any]]
    latency_ms: int
    created_at: datetime


@router.post("/search", response_model=SearchOut)
def search_code(body: SearchIn, db: DB, user: Viewer) -> SearchOut:
    if body.repository_ids:
        for rid in body.repository_ids:
            require_repository(db, user, rid)
    if body.snapshot_id:
        require_snapshot(db, user, body.snapshot_id)
    t0 = time.perf_counter()
    hits = search(
        db,
        user.id,
        body.query,
        limit=body.limit,
        repository_ids=body.repository_ids,
        snapshot_id=body.snapshot_id,
        language=body.language,
        path_prefix=body.path_prefix,
        include_tests=body.include_tests,
        mode=body.mode,
        rerank=body.rerank,
    )
    latency = round((time.perf_counter() - t0) * 1000)
    reranked = any(h.rerank_score is not None for h in hits)
    db.add(
        SearchHistory(
            user_id=user.id, query=body.query, mode=body.mode, reranked=reranked, results=len(hits), latency_ms=latency
        )
    )
    db.commit()
    return SearchOut(
        query=body.query,
        mode=body.mode,
        reranked=reranked,
        latency_ms=latency,
        results=[HitOut(**{k: v for k, v in asdict(h).items() if k != "docstring"}) for h in hits],
    )


@router.post("/ask")
def ask(body: AskIn, db: DB, user: Viewer) -> dict[str, Any]:
    """Answer a question about the code, citing file and line ranges — or say it is not found."""
    if not _ask_limiter.allow(str(user.id)):
        raise TooManyRequests("Too many questions; try again in a minute")
    if body.repository_ids:
        for rid in body.repository_ids:
            require_repository(db, user, rid)
    result = answer_question(db, user.id, body.question, repository_ids=body.repository_ids)
    return asdict(result)


@router.get("/questions", response_model=list[QuestionOut])
def question_history(db: DB, user: Viewer, limit: int = Query(default=30, ge=1, le=100)) -> list[QuestionOut]:
    rows = db.scalars(
        select(Question).where(Question.user_id == user.id).order_by(Question.created_at.desc()).limit(limit)
    )
    return [
        QuestionOut(
            id=q.id,
            question=q.question,
            answer=q.answer,
            status=q.status,
            provider=q.provider,
            citations=q.citations,
            latency_ms=q.latency_ms,
            created_at=q.created_at,
        )
        for q in rows
    ]
