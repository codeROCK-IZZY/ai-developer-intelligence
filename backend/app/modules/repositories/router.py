"""Repositories and their snapshots (imported versions)."""

from __future__ import annotations

import hashlib
import io
import uuid
import zipfile
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import DB, Member, Viewer
from app.connectors.github import parse_repo_url, validate_ref
from app.core.config import get_settings
from app.core.errors import Conflict, UnprocessableData
from app.db.models import Repository, Snapshot, Workspace, WorkspaceMember
from app.jobs.queue import enqueue
from app.modules import audit
from app.modules.workspaces.service import (
    require_repository,
    require_snapshot,
    require_workspace,
    workspace_role,
)
from app.pipeline.runner import next_seq
from app.storage.files import get_store

router = APIRouter(tags=["repositories"])


class RepositoryIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    github_url: str | None = Field(default=None, max_length=300)
    default_ref: str | None = Field(default=None, max_length=200)


class RepositoryPatch(BaseModel):
    description: str | None = Field(default=None, max_length=2000)
    github_url: str | None = Field(default=None, max_length=300)
    default_ref: str | None = Field(default=None, max_length=200)


class SnapshotOut(BaseModel):
    id: uuid.UUID
    repository_id: uuid.UUID
    seq: int
    label: str
    source: str
    ref: str | None
    status: str
    stage: str | None
    error: str | None
    stats: dict[str, Any]
    skipped: dict[str, Any]
    created_at: datetime
    processed_at: datetime | None


class RepositoryOut(BaseModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    workspace_name: str
    name: str
    description: str
    source_url: str | None
    default_ref: str | None
    role: str
    latest_snapshot: SnapshotOut | None
    snapshots: int
    created_at: datetime


class GitHubImportIn(BaseModel):
    ref: str | None = Field(default=None, max_length=200)
    label: str | None = Field(default=None, max_length=200)


def snapshot_out(s: Snapshot) -> SnapshotOut:
    return SnapshotOut(
        id=s.id,
        repository_id=s.repository_id,
        seq=s.seq,
        label=s.label,
        source=s.source,
        ref=s.ref,
        status=s.status,
        stage=s.stage,
        error=s.error,
        stats=s.stats,
        skipped=s.skipped,
        created_at=s.created_at,
        processed_at=s.processed_at,
    )


def _repo_out(db: DB, repo: Repository, ws: Workspace, role: str) -> RepositoryOut:
    latest = db.get(Snapshot, repo.latest_snapshot_id) if repo.latest_snapshot_id else None
    count = db.scalar(select(func.count()).where(Snapshot.repository_id == repo.id)) or 0
    return RepositoryOut(
        id=repo.id,
        workspace_id=ws.id,
        workspace_name=ws.name,
        name=repo.name,
        description=repo.description,
        source_url=repo.source_url,
        default_ref=repo.default_ref,
        role=role,
        latest_snapshot=snapshot_out(latest) if latest else None,
        snapshots=count,
        created_at=repo.created_at,
    )


def _normalise_github(url: str | None) -> str | None:
    if not url:
        return None
    return parse_repo_url(url).url


@router.get("/repositories", response_model=list[RepositoryOut])
def list_repositories(db: DB, user: Viewer) -> list[RepositoryOut]:
    rows = db.execute(
        select(Repository, Workspace, WorkspaceMember.role)
        .join(Workspace, Workspace.id == Repository.workspace_id)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(WorkspaceMember.user_id == user.id)
        .order_by(Workspace.name, Repository.name)
    ).all()
    return [_repo_out(db, r, ws, role) for r, ws, role in rows]


@router.post("/workspaces/{workspace_id}/repositories", response_model=RepositoryOut, status_code=201)
def create_repository(workspace_id: uuid.UUID, body: RepositoryIn, db: DB, user: Member) -> RepositoryOut:
    ws = require_workspace(db, user, workspace_id, "editor")
    repo = Repository(
        workspace_id=ws.id,
        name=body.name.strip(),
        description=body.description,
        source_url=_normalise_github(body.github_url),
        default_ref=validate_ref(body.default_ref) if body.default_ref else None,
        created_by=user.id,
    )
    db.add(repo)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise Conflict("A repository with this name already exists in the workspace") from exc
    audit.record(db, user.id, "repository.create", "repository", str(repo.id), name=repo.name)
    db.commit()
    return _repo_out(db, repo, ws, workspace_role(db, user, ws.id) or "reader")


@router.get("/repositories/{repository_id}", response_model=RepositoryOut)
def get_repository(repository_id: uuid.UUID, db: DB, user: Viewer) -> RepositoryOut:
    repo = require_repository(db, user, repository_id)
    ws = db.get(Workspace, repo.workspace_id)
    assert ws is not None
    return _repo_out(db, repo, ws, workspace_role(db, user, ws.id) or "reader")


@router.patch("/repositories/{repository_id}", response_model=RepositoryOut)
def update_repository(repository_id: uuid.UUID, body: RepositoryPatch, db: DB, user: Member) -> RepositoryOut:
    repo = require_repository(db, user, repository_id, "editor")
    if body.description is not None:
        repo.description = body.description
    if body.github_url is not None:
        repo.source_url = _normalise_github(body.github_url)
    if body.default_ref is not None:
        repo.default_ref = validate_ref(body.default_ref) if body.default_ref else None
    db.commit()
    return get_repository(repository_id, db, user)


@router.delete("/repositories/{repository_id}", status_code=204)
def delete_repository(repository_id: uuid.UUID, db: DB, user: Member) -> None:
    repo = require_repository(db, user, repository_id, "owner")
    keys = list(db.scalars(select(Snapshot.file_key).where(Snapshot.repository_id == repo.id)))
    audit.record(db, user.id, "repository.delete", "repository", str(repo.id), name=repo.name)
    db.delete(repo)
    db.commit()
    store = get_store()
    for key in keys:
        if key:
            store.delete(key)


@router.get("/repositories/{repository_id}/snapshots", response_model=list[SnapshotOut])
def list_snapshots(repository_id: uuid.UUID, db: DB, user: Viewer) -> list[SnapshotOut]:
    require_repository(db, user, repository_id)
    snaps = db.scalars(select(Snapshot).where(Snapshot.repository_id == repository_id).order_by(Snapshot.seq.desc()))
    return [snapshot_out(s) for s in snaps]


def _create_snapshot(
    db: DB,
    repo: Repository,
    user_id: uuid.UUID,
    *,
    source: str,
    label: str | None,
    ref: str | None,
    data: bytes | None = None,
) -> Snapshot:
    seq = next_seq(db, repo.id)
    snap = Snapshot(
        repository_id=repo.id,
        seq=seq,
        label=(label or ref or f"Snapshot {seq}")[:200],
        source=source,
        ref=ref,
        created_by=user_id,
    )
    db.add(snap)
    db.flush()
    if data is not None:
        key = f"snapshots/{repo.id}/{snap.id}.zip"
        get_store().put(key, data)
        snap.file_key = key
        snap.archive_sha256 = hashlib.sha256(data).hexdigest()
    enqueue(db, "process_snapshot", {"snapshot_id": str(snap.id)})
    audit.record(db, user_id, "snapshot.create", "repository", str(repo.id), seq=seq, source=source)
    db.commit()
    return snap


@router.post("/repositories/{repository_id}/snapshots:upload", response_model=SnapshotOut, status_code=202)
def upload_snapshot(
    repository_id: uuid.UUID,
    db: DB,
    user: Member,
    file: Annotated[UploadFile, File(description="ZIP archive of the repository")],
    label: Annotated[str | None, Form(max_length=200)] = None,
) -> SnapshotOut:
    repo = require_repository(db, user, repository_id, "editor")
    limit = get_settings().max_archive_mb * 1024 * 1024
    data = file.file.read(limit + 1)
    if len(data) > limit:
        raise UnprocessableData(f"The archive is larger than {get_settings().max_archive_mb} MB")
    if not zipfile.is_zipfile(io.BytesIO(data)):
        raise UnprocessableData("Upload a .zip archive of the repository")
    sha = hashlib.sha256(data).hexdigest()
    latest = db.get(Snapshot, repo.latest_snapshot_id) if repo.latest_snapshot_id else None
    if latest is not None and latest.archive_sha256 == sha:
        raise Conflict(f"This archive is identical to the latest snapshot (#{latest.seq})")
    snap = _create_snapshot(db, repo, user.id, source="upload", label=label or file.filename, ref=None, data=data)
    return snapshot_out(snap)


@router.post("/repositories/{repository_id}/snapshots:github", response_model=SnapshotOut, status_code=202)
def import_github_snapshot(repository_id: uuid.UUID, body: GitHubImportIn, db: DB, user: Member) -> SnapshotOut:
    """Queue a download of the repository's GitHub URL at ``ref`` (default branch if omitted)."""
    repo = require_repository(db, user, repository_id, "editor")
    if not repo.source_url:
        raise UnprocessableData("Set the repository's GitHub URL first")
    ref = validate_ref(body.ref or repo.default_ref or "HEAD")
    snap = _create_snapshot(db, repo, user.id, source="github", label=body.label, ref=ref)
    return snapshot_out(snap)


@router.get("/snapshots/{snapshot_id}", response_model=SnapshotOut)
def get_snapshot(snapshot_id: uuid.UUID, db: DB, user: Viewer) -> SnapshotOut:
    return snapshot_out(require_snapshot(db, user, snapshot_id))


@router.post("/snapshots/{snapshot_id}/reprocess", response_model=SnapshotOut, status_code=202)
def reprocess_snapshot(snapshot_id: uuid.UUID, db: DB, user: Member) -> SnapshotOut:
    snap = require_snapshot(db, user, snapshot_id, "editor")
    if snap.status in ("queued", "processing"):
        raise Conflict("The snapshot is already being processed")
    snap.status, snap.error, snap.stage = "queued", None, None
    enqueue(db, "process_snapshot", {"snapshot_id": str(snap.id)}, dedupe=True)
    db.commit()
    return snapshot_out(snap)


@router.delete("/snapshots/{snapshot_id}", status_code=204)
def delete_snapshot(snapshot_id: uuid.UUID, db: DB, user: Member) -> None:
    snap = require_snapshot(db, user, snapshot_id, "editor")
    repo = db.get(Repository, snap.repository_id)
    assert repo is not None
    key = snap.file_key
    audit.record(db, user.id, "snapshot.delete", "repository", str(repo.id), seq=snap.seq)
    db.delete(snap)
    db.flush()
    if repo.latest_snapshot_id == snapshot_id:
        repo.latest_snapshot_id = db.scalar(
            select(Snapshot.id)
            .where(Snapshot.repository_id == repo.id, Snapshot.status == "ready")
            .order_by(Snapshot.seq.desc())
            .limit(1)
        )
    db.commit()
    if key:
        get_store().delete(key)
