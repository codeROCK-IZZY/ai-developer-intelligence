"""Background job handlers."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.pipeline.runner import process_snapshot


def handle_process_snapshot(db: Session, payload: dict[str, Any]) -> dict[str, Any]:
    return process_snapshot(db, uuid.UUID(payload["snapshot_id"]))


HANDLERS = {"process_snapshot": handle_process_snapshot}
