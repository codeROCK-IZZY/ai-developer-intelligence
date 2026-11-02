"""Test fixtures. Integration tests run against a real PostgreSQL + pgvector database
(``adi_test``); the schema is rebuilt from the Alembic migration at session start."""

import os
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path

_tmp = Path(tempfile.mkdtemp(prefix="adi-test-"))
os.environ.setdefault("ADI_DATABASE_URL", "postgresql+psycopg://adi:adi@127.0.0.1:5437/adi_test")
os.environ["ADI_JWT_SECRET"] = "test-secret-not-for-production-0123456789abcdef"
os.environ["ADI_ENV"] = "test"
os.environ["ADI_COOKIE_SECURE"] = "false"
os.environ["ADI_LOG_JSON"] = "false"
os.environ["ADI_LOG_LEVEL"] = "WARNING"
os.environ["ADI_FILE_DIR"] = str(_tmp / "files")
os.environ["ADI_LLM_PROVIDER"] = "offline"  # tests never call a paid API
os.environ.pop("ANTHROPIC_API_KEY", None)
# Persistent model cache so embedding / re-ranker models download once, not on every test run.
os.environ.setdefault("ADI_EMBEDDING_CACHE_DIR", str(Path(__file__).resolve().parents[1] / "var" / "models"))

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.code.archive import build_archive  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.db.models import User  # noqa: E402
from app.db.session import get_engine, new_session  # noqa: E402
from app.main import app  # noqa: E402
from app.modules.auth.router import login_limiter  # noqa: E402

BACKEND = Path(__file__).resolve().parent.parent
PASSWORD = "Correct-Horse-Battery-9"
TABLES = (
    "audit_log, jobs, search_history, questions, duplicates, findings, chunks, imports, refs, symbols, files, "
    "snapshots, repositories, workspace_members, workspaces, refresh_tokens, users"
)


def _alembic(direction: str) -> None:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.attributes["database_url"] = get_settings().database_url
    cfg.attributes["skip_logging_config"] = True
    (command.downgrade if direction == "down" else command.upgrade)(cfg, "base" if direction == "down" else "head")


@pytest.fixture(scope="session", autouse=True)
def migrated_db() -> None:
    # Downgrade/upgrade (not DROP SCHEMA) so the superuser-created `vector` extension survives.
    _alembic("down")
    _alembic("up")


def reset_database() -> None:
    with get_engine().begin() as conn:
        conn.execute(text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))
    login_limiter.reset()


@pytest.fixture
def clean_db() -> None:
    reset_database()


@pytest.fixture
def client(clean_db: None) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


def create_user(email: str, role: str = "member", password: str = PASSWORD, active: bool = True) -> User:
    with new_session() as s:
        u = User(
            email=email,
            full_name=email.split("@")[0],
            password_hash=hash_password(password),
            role=role,
            is_active=active,
        )
        s.add(u)
        s.commit()
        s.refresh(u)
        return u


def login(client: TestClient, email: str, password: str = PASSWORD) -> dict[str, str]:
    r = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
def auth(client: TestClient) -> Callable[..., dict[str, str]]:
    """``auth("alice")`` → headers for member alice@example.com (created on first use)."""
    made: dict[str, dict[str, str]] = {}

    def _auth(name: str, role: str = "member") -> dict[str, str]:
        if name not in made:
            create_user(f"{name}@example.com", role)
            made[name] = login(client, f"{name}@example.com")
        return made[name]

    return _auth


def drain_jobs() -> int:
    from app.jobs.worker import drain

    return drain()


def make_repo(client: TestClient, headers: dict[str, str], workspace: str = "Team", name: str = "service") -> dict:
    """Create (or reuse) a workspace owned by the caller and a repository in it."""
    existing = {w["name"]: w for w in client.get("/api/v1/workspaces", headers=headers).json()}
    ws = existing.get(workspace) or client.post("/api/v1/workspaces", headers=headers, json={"name": workspace}).json()
    r = client.post(f"/api/v1/workspaces/{ws['id']}/repositories", headers=headers, json={"name": name})
    assert r.status_code == 201, r.text
    return {**r.json(), "workspace": ws}


def add_member(client: TestClient, owner: dict[str, str], workspace_id: str, email: str, role: str) -> None:
    r = client.put(f"/api/v1/workspaces/{workspace_id}/members", headers=owner, json={"email": email, "role": role})
    assert r.status_code == 200, r.text


def upload_snapshot(
    client: TestClient,
    headers: dict[str, str],
    repo_id: str,
    files: dict[str, str] | bytes,
    label: str = "v1",
    process: bool = True,
) -> dict:
    data = files if isinstance(files, bytes) else build_archive(files)
    r = client.post(
        f"/api/v1/repositories/{repo_id}/snapshots:upload",
        headers=headers,
        files={"file": ("repo.zip", data, "application/zip")},
        data={"label": label},
    )
    assert r.status_code == 202, r.text
    if process:
        drain_jobs()
        r = client.get(f"/api/v1/snapshots/{r.json()['id']}", headers=headers)
        assert r.json()["status"] == "ready", r.json()
    return r.json()


SMALL_REPO = {
    "app/auth.py": (
        '"""Authentication helpers."""\n\nimport hashlib\n\n\n'
        "def hash_password(password: str, salt: str) -> str:\n"
        '    """Hash a password with PBKDF2 for storage."""\n'
        '    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000).hex()\n\n\n'
        "def check_password(password: str, salt: str, stored: str) -> bool:\n"
        '    """Compare a password with its stored hash."""\n'
        "    return hash_password(password, salt) == stored\n"
    ),
    "app/billing.py": (
        '"""Invoices and tax."""\n\nfrom app.auth import check_password\n\n\n'
        "def invoice_total(lines: list[int], tax_rate: float) -> int:\n"
        '    """Sum invoice lines in cents and add sales tax."""\n'
        "    subtotal = sum(lines)\n"
        "    return subtotal + round(subtotal * tax_rate)\n\n\n"
        "def run_report(db, month):\n"
        "    db.execute(f\"SELECT * FROM invoices WHERE month = '{month}'\")\n"
        "    return check_password('x', 'y', 'z')\n"
    ),
    "README.md": "# Billing service\n\nIssues invoices and computes sales tax.\n",
}
