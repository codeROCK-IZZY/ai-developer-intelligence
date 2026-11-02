"""Grounded question answering over code.

Flow: hybrid retrieval with re-ranking (access-controlled) → relevance gate → provider answer
with citations → grounding enforcement → saved to the question history.

Grounding is enforced in code, not only in the prompt:
* **Relevance gate** — if the best chunk's re-ranker relevance is below
  ``ADI_MIN_RERANK_RELEVANCE``, reply "not found" without calling the language model.
* **No citation, no answer** — an "answered" result with zero citations becomes "not found".
* **Graceful degradation** — if the Claude call fails, the offline provider answers instead,
  and the response says so.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.ai.base import NOT_FOUND_TEXT, AnswerResult, Passage
from app.ai.offline import OfflineProvider
from app.ai.providers import get_llm
from app.core.config import get_settings
from app.db.models import Question
from app.retrieval.search import Hit, search


@dataclass
class CitationOut:
    ordinal: int
    chunk_id: int
    repository_id: uuid.UUID
    repository_name: str
    snapshot_id: uuid.UUID
    path: str
    start_line: int
    end_line: int
    symbol: str | None
    symbol_id: int | None
    cited_text: str


@dataclass
class AskResult:
    question_id: uuid.UUID
    status: str
    answer: str
    provider: str
    sentences: list[dict[str, Any]]
    citations: list[CitationOut]
    retrieved: int
    best_relevance: float | None
    latency_ms: int
    notes: list[str] = field(default_factory=list)


def is_relevant(hits: list[Hit]) -> bool:
    """The re-ranker reads the question and the code together, so its score is the gate. Without
    re-ranking (disabled), fall back to vector similarity plus a keyword match."""
    if not hits:
        return False
    reranked = [h.rerank_score for h in hits if h.rerank_score is not None]
    s = get_settings()
    if reranked:
        return max(reranked) >= s.min_rerank_relevance
    best_sim = max((h.vector_similarity or 0.0) for h in hits)
    return best_sim >= s.min_relevance and any(h.keyword_rank for h in hits[:5])


def answer_question(
    db: Session,
    user_id: uuid.UUID,
    question: str,
    *,
    repository_ids: list[uuid.UUID] | None = None,
    persist: bool = True,
) -> AskResult:
    t0 = time.perf_counter()
    s = get_settings()
    llm = get_llm()
    notes: list[str] = []
    hits = search(db, user_id, question, limit=s.retrieval_top_k, repository_ids=repository_ids, rerank=True)
    # Developers ask about the implementation: relevant source code first, tests after it.
    hits.sort(key=lambda h: (h.is_test, -(h.rerank_score or 0.0)))
    passages = [
        Passage(
            index=i,
            chunk_id=h.chunk_id,
            path=h.path,
            start_line=h.start_line,
            end_line=h.end_line,
            symbol=h.symbol,
            symbol_kind=h.symbol_kind,
            signature=h.signature,
            docstring=h.docstring,
            text=h.text,
        )
        for i, h in enumerate(hits)
    ]
    best = max((h.rerank_score or 0.0 for h in hits), default=None)
    if not is_relevant(hits):
        result = AnswerResult(status="not_found", sentences=[], provider="relevance-gate")
    else:
        # The offline provider lists locations, so it only gets passages that pass the gate.
        if llm.name == "offline":
            passages = [
                p for p, h in zip(passages, hits, strict=True) if (h.rerank_score or 0) >= s.min_rerank_relevance
            ]
        result = llm.answer(question, passages)
        if result.status == "error":
            notes.append("The language model was unavailable; showing the most relevant code instead.")
            result = OfflineProvider().answer(question, passages)
    if result.status == "answered" and not any(sent.citations for sent in result.sentences):
        result = AnswerResult(status="not_found", sentences=[], provider=result.provider, usage=result.usage)
        notes.append("The model's answer had no supporting citation, so it was withheld.")

    citations: list[CitationOut] = []
    sentences_out: list[dict[str, Any]] = []
    for sent in result.sentences if result.status == "answered" else []:
        ords = []
        for passage_index, cited in sent.citations:
            hit = hits[passage_index]
            existing = next((c for c in citations if c.chunk_id == hit.chunk_id and c.cited_text == cited), None)
            if existing is None:
                existing = CitationOut(
                    len(citations) + 1,
                    hit.chunk_id,
                    hit.repository_id,
                    hit.repository_name,
                    hit.snapshot_id,
                    hit.path,
                    hit.start_line,
                    hit.end_line,
                    hit.symbol,
                    hit.symbol_id,
                    cited,
                )
                citations.append(existing)
            ords.append(existing.ordinal)
        sentences_out.append({"text": sent.text, "citations": ords, "supported": bool(ords)})
    answer_text = (
        result.text
        if result.status == "answered"
        else (NOT_FOUND_TEXT if result.status == "not_found" else "The request could not be answered.")
    )
    latency = round((time.perf_counter() - t0) * 1000)
    qid = uuid.uuid4()
    if persist:
        db.add(
            Question(
                id=qid,
                user_id=user_id,
                repository_id=repository_ids[0] if repository_ids and len(repository_ids) == 1 else None,
                question=question,
                answer=answer_text,
                status=result.status,
                provider=result.provider,
                citations=[
                    {**asdict(c), "repository_id": str(c.repository_id), "snapshot_id": str(c.snapshot_id)}
                    for c in citations
                ],
                usage=result.usage,
                latency_ms=latency,
            )
        )
        db.commit()
    return AskResult(
        question_id=qid,
        status=result.status,
        answer=answer_text,
        provider=result.provider,
        sentences=sentences_out,
        citations=citations,
        retrieved=len(hits),
        best_relevance=round(best, 4) if best is not None else None,
        latency_ms=latency,
        notes=notes,
    )
