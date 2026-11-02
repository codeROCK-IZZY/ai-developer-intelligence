from typing import Any

from fastapi import APIRouter
from sqlalchemy import func, select

from app.api.deps import DB, Admin, Viewer
from app.core.config import get_settings
from app.db.models import Chunk, CodeFile, CodeSymbol, Job, Question, Repository, Snapshot

router = APIRouter(tags=["admin"])


@router.get("/system/info")
def system_info(_: Viewer) -> dict[str, Any]:
    """What the UI needs to label AI output honestly (which provider/model is active)."""
    s = get_settings()
    return {
        "llm_mode": s.llm_mode,
        "llm_model": s.claude_model if s.llm_mode == "claude" else None,
        "embedding_model": s.embedding_model,
        "code_leaves_server": s.llm_mode == "claude",
    }


@router.get("/admin/stats")
def stats(db: DB, _: Admin) -> dict[str, Any]:
    by_status = dict(db.execute(select(Snapshot.status, func.count()).group_by(Snapshot.status)).all())
    answers = dict(db.execute(select(Question.status, func.count()).group_by(Question.status)).all())
    return {
        "repositories": db.scalar(select(func.count()).select_from(Repository)) or 0,
        "snapshots_by_status": by_status,
        "files": db.scalar(select(func.count()).select_from(CodeFile)) or 0,
        "symbols": db.scalar(select(func.count()).select_from(CodeSymbol)) or 0,
        "chunks": db.scalar(select(func.count()).select_from(Chunk)) or 0,
        "answers_by_status": {str(k): v for k, v in answers.items()},
    }


@router.get("/admin/jobs")
def jobs(db: DB, _: Admin, limit: int = 50) -> list[dict[str, Any]]:
    rows = db.scalars(select(Job).order_by(Job.created_at.desc()).limit(min(limit, 500)))
    return [
        {
            "id": j.id,
            "type": j.job_type,
            "status": j.status,
            "attempts": j.attempts,
            "payload": j.payload,
            "last_error": (j.last_error or "")[:500] or None,
            "created_at": j.created_at.isoformat(),
        }
        for j in rows
    ]
