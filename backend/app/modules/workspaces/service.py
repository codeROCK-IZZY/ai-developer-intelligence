"""Workspace membership and repository access.

Roles (strongest first): ``owner`` > ``editor`` > ``reader``.
* reader — browse code, search, ask, view analysis
* editor — also create repositories and import snapshots
* owner  — also manage members and delete repositories

A workspace, repository or snapshot the caller cannot read is reported as **404**, not 403, so
its existence is not revealed. Cross-repository queries (search, Q&A) are scoped through the
``readable_repositories`` view, which derives from the same membership table.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import Forbidden, NotFound
from app.db.models import Repository, Snapshot, User, Workspace, WorkspaceMember

RANK = {"reader": 1, "editor": 2, "owner": 3}


def workspace_role(db: Session, user: User, workspace_id: uuid.UUID) -> str | None:
    return db.scalar(
        select(WorkspaceMember.role).where(
            WorkspaceMember.workspace_id == workspace_id, WorkspaceMember.user_id == user.id
        )
    )


def require_workspace(db: Session, user: User, workspace_id: uuid.UUID, need: str = "reader") -> Workspace:
    role = workspace_role(db, user, workspace_id)
    ws = db.get(Workspace, workspace_id) if role else None
    if ws is None:
        raise NotFound("Workspace not found")
    assert role is not None
    if RANK[role] < RANK[need]:
        raise Forbidden(f"Requires the '{need}' role in this workspace")
    return ws


def require_repository(db: Session, user: User, repository_id: uuid.UUID, need: str = "reader") -> Repository:
    repo = db.get(Repository, repository_id)
    if repo is None or workspace_role(db, user, repo.workspace_id) is None:
        raise NotFound("Repository not found")
    require_workspace(db, user, repo.workspace_id, need)
    return repo


def require_snapshot(db: Session, user: User, snapshot_id: uuid.UUID, need: str = "reader") -> Snapshot:
    snap = db.get(Snapshot, snapshot_id)
    if snap is None:
        raise NotFound("Snapshot not found")
    try:
        require_repository(db, user, snap.repository_id, need)
    except NotFound as exc:
        raise NotFound("Snapshot not found") from exc
    return snap


def readable_repository_ids(db: Session, user: User) -> list[uuid.UUID]:
    return list(
        db.scalars(
            select(Repository.id)
            .join(WorkspaceMember, WorkspaceMember.workspace_id == Repository.workspace_id)
            .where(WorkspaceMember.user_id == user.id)
        )
    )
