"""Command-line tools: ``python -m app.cli <command>``.

migrate                          apply database migrations
create-user --email E --role R   create a user (password from ADI_USER_PASSWORD or prompt)
load-demo                        demo users, workspaces and repositories; queue the demo snapshots
evaluate --out DIR               evaluate analysis, search and Q&A on the demo and write the report
worker                           run the background worker
drain                            process all queued jobs, then exit
demo                             migrate + load-demo + drain (one-command setup)
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import sys
from pathlib import Path

from app.core.config import get_settings
from app.core.logging import configure_logging

# dana edits the Storefront workspace; sam can only read it; neither belongs to Platform.
DEMO_USERS = {"dana": "member", "sam": "member", "admin": "admin"}
APP_DIR = Path(__file__).resolve().parent


def _migrate() -> None:
    from alembic import command
    from alembic.config import Config

    root = APP_DIR.parent
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    cfg.attributes["database_url"] = get_settings().database_url
    cfg.attributes["skip_logging_config"] = True
    command.upgrade(cfg, "head")
    print("migrations applied")


def _create_user(email: str, role: str, password: str | None = None) -> None:
    from sqlalchemy import select

    from app.core.security import hash_password
    from app.db.models import User
    from app.db.session import session_scope

    password = password or os.environ.get("ADI_USER_PASSWORD") or getpass.getpass("Password (min 12 chars): ")
    if len(password) < 12:
        sys.exit("password must be at least 12 characters")
    with session_scope() as db:
        if db.scalar(select(User).where(User.email == email.lower())):
            print(f"user {email} exists — skipped")
            return
        db.add(
            User(
                email=email.lower(),
                full_name=email.split("@")[0].title(),
                password_hash=hash_password(password),
                role=role,
            )
        )
    print(f"user {email} ({role}) created")


def self_archive() -> bytes:
    """This platform's own backend source as a ZIP (the "Platform" demo repository)."""
    from app.code.archive import build_archive

    files = {
        f"developer-intelligence/app/{p.relative_to(APP_DIR).as_posix()}": p.read_text(encoding="utf-8")
        for p in sorted(APP_DIR.rglob("*.py"))
    }
    return build_archive(files)


def load_demo() -> dict[str, str]:
    """Create the demo users, workspaces and repositories and queue their snapshots.
    Idempotent: existing objects are reused, existing snapshots are not re-imported."""
    from sqlalchemy import select

    from app.core.security import hash_password
    from app.db.models import Repository, Snapshot, User, Workspace, WorkspaceMember
    from app.db.session import session_scope
    from app.jobs.queue import enqueue
    from app.pipeline.runner import next_seq
    from app.storage.files import get_store
    from app.synthetic import demo

    password = os.environ.get("ADI_DEMO_PASSWORD")
    if not password or len(password) < 12:
        sys.exit("set ADI_DEMO_PASSWORD (12+ characters) for the demo users")
    store = get_store()
    out: dict[str, str] = {}
    with session_scope() as db:
        users: dict[str, User] = {}
        for name, role in DEMO_USERS.items():
            email = f"{name}@example.com"
            u = db.scalar(select(User).where(User.email == email))
            if u is None:
                u = User(email=email, full_name=name.title(), password_hash=hash_password(password), role=role)
                db.add(u)
                db.flush()
            users[name] = u
        layout = {
            "Storefront": ("Fernwood Outfitters online shop", {"admin": "owner", "dana": "editor", "sam": "reader"}),
            "Platform": ("Internal developer tools", {"admin": "owner"}),
        }
        workspaces: dict[str, Workspace] = {}
        for ws_name, (desc, members) in layout.items():
            ws = db.scalar(select(Workspace).where(Workspace.name == ws_name))
            if ws is None:
                ws = Workspace(name=ws_name, description=desc, created_by=users["admin"].id)
                db.add(ws)
                db.flush()
            for uname, role in members.items():
                if db.get(WorkspaceMember, (ws.id, users[uname].id)) is None:
                    db.add(WorkspaceMember(workspace_id=ws.id, user_id=users[uname].id, role=role))
            workspaces[ws_name] = ws
        repos = [
            (
                "Storefront",
                "fernwood-shop",
                "Storefront API, web client and shipping service",
                [("v2.3.0", demo.archive(1)), ("v2.4.0 — coupons", demo.archive(2))],
            ),
            ("Platform", "developer-intelligence", "This platform's own backend source", [("current", self_archive())]),
        ]
        for ws_name, repo_name, desc, versions in repos:
            ws = workspaces[ws_name]
            repo = db.scalar(select(Repository).where(Repository.workspace_id == ws.id, Repository.name == repo_name))
            if repo is None:
                repo = Repository(workspace_id=ws.id, name=repo_name, description=desc, created_by=users["admin"].id)
                db.add(repo)
                db.flush()
            out[repo_name] = str(repo.id)
            existing = set(db.scalars(select(Snapshot.label).where(Snapshot.repository_id == repo.id)))
            for label, data in versions:
                if label in existing:
                    continue
                snap = Snapshot(
                    repository_id=repo.id,
                    seq=next_seq(db, repo.id),
                    label=label,
                    source="upload",
                    created_by=users["admin"].id,
                )
                db.add(snap)
                db.flush()
                key = f"snapshots/{repo.id}/{snap.id}.zip"
                store.put(key, data)
                snap.file_key, snap.archive_sha256 = key, hashlib.sha256(data).hexdigest()
                enqueue(db, "process_snapshot", {"snapshot_id": str(snap.id)})
                db.flush()
    print(f"demo loaded: {', '.join(out)}; processing is queued")
    return out


def _evaluate(out: Path) -> None:
    from app.db.session import new_session
    from app.evaluation.run import evaluate, write_report

    db = new_session()
    try:
        report = evaluate(db)
    finally:
        db.close()
    write_report(report, out)
    print(json.dumps({k: report[k] for k in ("structure", "retrieval")}, indent=1, default=str)[:4000])


def main(argv: list[str] | None = None) -> None:
    configure_logging(get_settings().log_level, as_json=False)
    p = argparse.ArgumentParser(
        prog="python -m app.cli", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate")
    cu = sub.add_parser("create-user")
    cu.add_argument("--email", required=True)
    cu.add_argument("--role", choices=["admin", "member", "viewer"], default="member")
    sub.add_parser("load-demo")
    ev = sub.add_parser("evaluate")
    ev.add_argument("--out", type=Path, default=Path("../eval/reports"))
    sub.add_parser("worker")
    sub.add_parser("drain")
    sub.add_parser("demo")
    args = p.parse_args(argv)

    if args.cmd == "migrate":
        _migrate()
    elif args.cmd == "create-user":
        _create_user(args.email, args.role)
    elif args.cmd == "load-demo":
        load_demo()
    elif args.cmd == "evaluate":
        _evaluate(args.out)
    elif args.cmd == "worker":
        from app.jobs.worker import run_forever

        run_forever()
    elif args.cmd in ("drain", "demo"):
        if args.cmd == "demo":
            _migrate()
            load_demo()
        from app.jobs.worker import drain

        print(f"processed {drain()} jobs")


if __name__ == "__main__":
    main()
