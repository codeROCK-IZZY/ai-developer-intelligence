"""Workspaces and membership."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import DB, Member, Viewer
from app.core.errors import Conflict, NotFound, UnprocessableData
from app.db.models import Repository, User, Workspace, WorkspaceMember
from app.modules import audit
from app.modules.workspaces.service import require_workspace

router = APIRouter(tags=["workspaces"])

Role = Literal["owner", "editor", "reader"]


class WorkspaceIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)


class WorkspaceOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str
    role: str
    members: int
    repositories: int
    created_at: datetime


class MemberIn(BaseModel):
    email: EmailStr
    role: Role


class MemberOut(BaseModel):
    user_id: uuid.UUID
    email: str
    full_name: str
    role: str


def _ws_out(db: DB, ws: Workspace, role: str) -> WorkspaceOut:
    members = db.scalar(select(func.count()).where(WorkspaceMember.workspace_id == ws.id)) or 0
    repos = db.scalar(select(func.count()).where(Repository.workspace_id == ws.id)) or 0
    return WorkspaceOut(
        id=ws.id,
        name=ws.name,
        description=ws.description,
        role=role,
        members=members,
        repositories=repos,
        created_at=ws.created_at,
    )


@router.get("/workspaces", response_model=list[WorkspaceOut])
def list_workspaces(db: DB, user: Viewer) -> list[WorkspaceOut]:
    rows = db.execute(
        select(Workspace, WorkspaceMember.role)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(WorkspaceMember.user_id == user.id)
        .order_by(Workspace.name)
    ).all()
    return [_ws_out(db, ws, role) for ws, role in rows]


@router.post("/workspaces", response_model=WorkspaceOut, status_code=201)
def create_workspace(body: WorkspaceIn, db: DB, user: Member) -> WorkspaceOut:
    ws = Workspace(name=body.name.strip(), description=body.description, created_by=user.id)
    db.add(ws)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise Conflict("A workspace with this name already exists") from exc
    db.add(WorkspaceMember(workspace_id=ws.id, user_id=user.id, role="owner"))
    audit.record(db, user.id, "workspace.create", "workspace", str(ws.id), name=ws.name)
    db.commit()
    return _ws_out(db, ws, "owner")


@router.delete("/workspaces/{workspace_id}", status_code=204)
def delete_workspace(workspace_id: uuid.UUID, db: DB, user: Member) -> None:
    ws = require_workspace(db, user, workspace_id, "owner")
    audit.record(db, user.id, "workspace.delete", "workspace", str(ws.id), name=ws.name)
    db.delete(ws)
    db.commit()


@router.get("/workspaces/{workspace_id}/members", response_model=list[MemberOut])
def list_members(workspace_id: uuid.UUID, db: DB, user: Viewer) -> list[MemberOut]:
    require_workspace(db, user, workspace_id)
    rows = db.execute(
        select(WorkspaceMember, User)
        .join(User, User.id == WorkspaceMember.user_id)
        .where(WorkspaceMember.workspace_id == workspace_id)
        .order_by(User.email)
    ).all()
    return [MemberOut(user_id=u.id, email=u.email, full_name=u.full_name, role=m.role) for m, u in rows]


@router.put("/workspaces/{workspace_id}/members", response_model=MemberOut)
def upsert_member(workspace_id: uuid.UUID, body: MemberIn, db: DB, user: Member) -> MemberOut:
    """Add a member or change their role (owners only)."""
    require_workspace(db, user, workspace_id, "owner")
    target = db.scalar(select(User).where(User.email == body.email.lower(), User.is_active))
    if target is None:
        raise NotFound(f"No active user with e-mail {body.email}")
    m = db.get(WorkspaceMember, (workspace_id, target.id))
    if m is not None and m.role == "owner" and body.role != "owner" and _owner_count(db, workspace_id) == 1:
        raise UnprocessableData("A workspace must keep at least one owner")
    if m is None:
        m = WorkspaceMember(workspace_id=workspace_id, user_id=target.id, role=body.role)
        db.add(m)
    else:
        m.role = body.role
    audit.record(db, user.id, "workspace.member", "workspace", str(workspace_id), email=target.email, role=body.role)
    db.commit()
    return MemberOut(user_id=target.id, email=target.email, full_name=target.full_name, role=body.role)


@router.delete("/workspaces/{workspace_id}/members/{user_id}", status_code=204)
def remove_member(workspace_id: uuid.UUID, user_id: uuid.UUID, db: DB, user: Member) -> None:
    if user_id != user.id:  # anyone may leave; removing others needs owner
        require_workspace(db, user, workspace_id, "owner")
    m = db.get(WorkspaceMember, (workspace_id, user_id))
    if m is None:
        raise NotFound("Member not found")
    if m.role == "owner" and _owner_count(db, workspace_id) == 1:
        raise UnprocessableData("A workspace must keep at least one owner")
    db.delete(m)
    audit.record(db, user.id, "workspace.member_remove", "workspace", str(workspace_id), member_id=str(user_id))
    db.commit()


def _owner_count(db: DB, workspace_id: uuid.UUID) -> int:
    return (
        db.scalar(
            select(func.count()).where(WorkspaceMember.workspace_id == workspace_id, WorkspaceMember.role == "owner")
        )
        or 0
    )
