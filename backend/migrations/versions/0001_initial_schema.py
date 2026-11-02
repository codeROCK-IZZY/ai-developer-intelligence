"""Initial schema.

Revision ID: 0001
Revises:
Create Date: 2026-11-02
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

UPGRADE_SQL = """
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE users (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email              varchar(320) NOT NULL,
    full_name          varchar(200) NOT NULL DEFAULT '',
    password_hash      varchar(255) NOT NULL,
    role               varchar(20)  NOT NULL CHECK (role IN ('admin', 'member', 'viewer')),
    is_active          boolean      NOT NULL DEFAULT true,
    failed_login_count integer      NOT NULL DEFAULT 0,
    locked_until       timestamptz,
    created_at         timestamptz  NOT NULL DEFAULT now(),
    updated_at         timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT uq_users_email UNIQUE (email),
    CONSTRAINT ck_users_email_lower CHECK (email = lower(email))
);
ALTER TABLE users RENAME CONSTRAINT users_pkey TO pk_users;

CREATE TABLE refresh_tokens (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     uuid NOT NULL,
    token_hash  varchar(64) NOT NULL,
    expires_at  timestamptz NOT NULL,
    revoked_at  timestamptz,
    created_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_refresh_tokens_token_hash UNIQUE (token_hash),
    CONSTRAINT fk_refresh_tokens_user_id_users FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
);
ALTER TABLE refresh_tokens RENAME CONSTRAINT refresh_tokens_pkey TO pk_refresh_tokens;
CREATE INDEX ix_refresh_tokens_user_id ON refresh_tokens (user_id);

CREATE TABLE workspaces (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name        varchar(200) NOT NULL,
    description text NOT NULL DEFAULT '',
    created_by  uuid,
    created_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_workspaces_name UNIQUE (name),
    CONSTRAINT fk_workspaces_created_by_users FOREIGN KEY (created_by) REFERENCES users (id) ON DELETE SET NULL
);
ALTER TABLE workspaces RENAME CONSTRAINT workspaces_pkey TO pk_workspaces;

CREATE TABLE workspace_members (
    workspace_id uuid NOT NULL,
    user_id      uuid NOT NULL,
    role         varchar(10) NOT NULL CHECK (role IN ('owner', 'editor', 'reader')),
    created_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT pk_workspace_members PRIMARY KEY (workspace_id, user_id),
    CONSTRAINT fk_workspace_members_workspace_id_workspaces
        FOREIGN KEY (workspace_id) REFERENCES workspaces (id) ON DELETE CASCADE,
    CONSTRAINT fk_workspace_members_user_id_users FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
);
CREATE INDEX ix_workspace_members_user_id ON workspace_members (user_id);

CREATE TABLE repositories (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id       uuid NOT NULL,
    name               varchar(200) NOT NULL,
    description        text NOT NULL DEFAULT '',
    source_url         varchar(500),  -- GitHub repository URL, when imported from GitHub
    default_ref        varchar(200),
    latest_snapshot_id uuid,  -- latest *ready* snapshot; no FK (circular), maintained by the pipeline
    created_by         uuid,
    created_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_repositories_workspace_name UNIQUE (workspace_id, name),
    CONSTRAINT fk_repositories_workspace_id_workspaces
        FOREIGN KEY (workspace_id) REFERENCES workspaces (id) ON DELETE CASCADE,
    CONSTRAINT fk_repositories_created_by_users FOREIGN KEY (created_by) REFERENCES users (id) ON DELETE SET NULL
);
ALTER TABLE repositories RENAME CONSTRAINT repositories_pkey TO pk_repositories;
CREATE INDEX ix_repositories_workspace_id ON repositories (workspace_id);

-- The ONE place that defines who may read a repository: members of its workspace.
-- Every search, Q&A retrieval, browse and analysis query is scoped through this view.
CREATE VIEW readable_repositories AS
    SELECT r.id AS repository_id, m.user_id,
           (CASE m.role WHEN 'owner' THEN 'owner' WHEN 'editor' THEN 'edit' ELSE 'read' END)::varchar AS access
    FROM repositories r
    JOIN workspace_members m ON m.workspace_id = r.workspace_id;

CREATE TABLE snapshots (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    repository_id  uuid NOT NULL,
    seq            integer NOT NULL,
    label          varchar(200) NOT NULL,
    source         varchar(10) NOT NULL CHECK (source IN ('upload', 'github')),
    ref            varchar(200),
    file_key       varchar(500),
    archive_sha256 varchar(64),
    status         varchar(20) NOT NULL DEFAULT 'queued'
                   CHECK (status IN ('queued', 'processing', 'ready', 'failed')),
    stage          varchar(30),
    error          text,
    stats          jsonb NOT NULL DEFAULT '{}'::jsonb,
    skipped        jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_by     uuid,
    created_at     timestamptz NOT NULL DEFAULT now(),
    processed_at   timestamptz,
    CONSTRAINT uq_snapshots_repository_seq UNIQUE (repository_id, seq),
    CONSTRAINT fk_snapshots_repository_id_repositories
        FOREIGN KEY (repository_id) REFERENCES repositories (id) ON DELETE CASCADE,
    CONSTRAINT fk_snapshots_created_by_users FOREIGN KEY (created_by) REFERENCES users (id) ON DELETE SET NULL
);
ALTER TABLE snapshots RENAME CONSTRAINT snapshots_pkey TO pk_snapshots;

CREATE TABLE files (
    id          bigserial PRIMARY KEY,
    snapshot_id uuid NOT NULL,
    path        varchar(1000) NOT NULL,
    language    varchar(20) NOT NULL,
    sha256      varchar(64) NOT NULL,
    size_bytes  integer NOT NULL,
    loc         integer NOT NULL,
    is_test     boolean NOT NULL DEFAULT false,
    health      integer NOT NULL,
    centrality  double precision NOT NULL DEFAULT 0,
    fan_in      integer NOT NULL DEFAULT 0,
    fan_out     integer NOT NULL DEFAULT 0,
    content     text NOT NULL,
    CONSTRAINT uq_files_snapshot_path UNIQUE (snapshot_id, path),
    CONSTRAINT fk_files_snapshot_id_snapshots FOREIGN KEY (snapshot_id) REFERENCES snapshots (id) ON DELETE CASCADE
);
ALTER TABLE files RENAME CONSTRAINT files_pkey TO pk_files;

CREATE TABLE symbols (
    id             bigserial PRIMARY KEY,
    snapshot_id    uuid NOT NULL,
    file_id        bigint NOT NULL,
    parent_id      bigint,
    kind           varchar(10) NOT NULL CHECK (kind IN ('function', 'method', 'class')),
    name           varchar(300) NOT NULL,
    qualified_name varchar(1000) NOT NULL,
    start_line     integer NOT NULL,
    end_line       integer NOT NULL,
    signature      text NOT NULL,
    docstring      text NOT NULL DEFAULT '',
    exported       boolean NOT NULL,
    decorators     jsonb NOT NULL DEFAULT '[]'::jsonb,
    params         integer NOT NULL DEFAULT 0,
    complexity     integer NOT NULL DEFAULT 1,
    max_nesting    integer NOT NULL DEFAULT 0,
    loc            integer NOT NULL,
    body_hash      varchar(40) NOT NULL,
    fan_in         integer NOT NULL DEFAULT 0,
    fan_out        integer NOT NULL DEFAULT 0,
    tested         boolean NOT NULL DEFAULT false,  -- called directly from a test file (static)
    CONSTRAINT fk_symbols_snapshot_id_snapshots
        FOREIGN KEY (snapshot_id) REFERENCES snapshots (id) ON DELETE CASCADE,
    CONSTRAINT fk_symbols_file_id_files FOREIGN KEY (file_id) REFERENCES files (id) ON DELETE CASCADE,
    CONSTRAINT fk_symbols_parent_id_symbols FOREIGN KEY (parent_id) REFERENCES symbols (id) ON DELETE CASCADE
);
ALTER TABLE symbols RENAME CONSTRAINT symbols_pkey TO pk_symbols;
CREATE INDEX ix_symbols_snapshot_id ON symbols (snapshot_id);
CREATE INDEX ix_symbols_file_id ON symbols (file_id);
CREATE INDEX ix_symbols_snapshot_name ON symbols (snapshot_id, lower(name));

CREATE TABLE refs (
    id          bigserial PRIMARY KEY,
    snapshot_id uuid NOT NULL,
    file_id     bigint NOT NULL,
    caller_id   bigint,  -- NULL = module-level code
    callee_id   bigint,  -- NULL = unresolved (library call or dynamic dispatch)
    name        varchar(300) NOT NULL,
    receiver    varchar(100),
    line        integer NOT NULL,
    resolution  varchar(20),
    CONSTRAINT fk_refs_snapshot_id_snapshots FOREIGN KEY (snapshot_id) REFERENCES snapshots (id) ON DELETE CASCADE,
    CONSTRAINT fk_refs_file_id_files FOREIGN KEY (file_id) REFERENCES files (id) ON DELETE CASCADE,
    CONSTRAINT fk_refs_caller_id_symbols FOREIGN KEY (caller_id) REFERENCES symbols (id) ON DELETE CASCADE,
    CONSTRAINT fk_refs_callee_id_symbols FOREIGN KEY (callee_id) REFERENCES symbols (id) ON DELETE CASCADE
);
ALTER TABLE refs RENAME CONSTRAINT refs_pkey TO pk_refs;
CREATE INDEX ix_refs_snapshot_id ON refs (snapshot_id);
CREATE INDEX ix_refs_caller_id ON refs (caller_id);
CREATE INDEX ix_refs_callee_id ON refs (callee_id);

CREATE TABLE imports (
    id              bigserial PRIMARY KEY,
    snapshot_id     uuid NOT NULL,
    file_id         bigint NOT NULL,
    module          varchar(500) NOT NULL,
    line            integer NOT NULL,
    kind            varchar(12) NOT NULL CHECK (kind IN ('internal', 'stdlib', 'external', 'unresolved')),
    package         varchar(200),
    target_file_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
    CONSTRAINT fk_imports_snapshot_id_snapshots FOREIGN KEY (snapshot_id) REFERENCES snapshots (id) ON DELETE CASCADE,
    CONSTRAINT fk_imports_file_id_files FOREIGN KEY (file_id) REFERENCES files (id) ON DELETE CASCADE
);
ALTER TABLE imports RENAME CONSTRAINT imports_pkey TO pk_imports;
CREATE INDEX ix_imports_snapshot_id ON imports (snapshot_id);

CREATE TABLE chunks (
    id          bigserial PRIMARY KEY,
    snapshot_id uuid NOT NULL,
    file_id     bigint NOT NULL,
    symbol_id   bigint,
    kind        varchar(10) NOT NULL,
    start_line  integer NOT NULL,
    end_line    integer NOT NULL,
    text        text NOT NULL,
    content_hash varchar(64) NOT NULL,  -- of the embedded text; unchanged chunks reuse embeddings
    tsv         tsvector NOT NULL,
    embedding   vector(384) NOT NULL,
    CONSTRAINT fk_chunks_snapshot_id_snapshots FOREIGN KEY (snapshot_id) REFERENCES snapshots (id) ON DELETE CASCADE,
    CONSTRAINT fk_chunks_file_id_files FOREIGN KEY (file_id) REFERENCES files (id) ON DELETE CASCADE,
    CONSTRAINT fk_chunks_symbol_id_symbols FOREIGN KEY (symbol_id) REFERENCES symbols (id) ON DELETE CASCADE
);
ALTER TABLE chunks RENAME CONSTRAINT chunks_pkey TO pk_chunks;
CREATE INDEX ix_chunks_snapshot_id ON chunks (snapshot_id);
CREATE INDEX ix_chunks_tsv ON chunks USING gin (tsv);

CREATE TABLE findings (
    id          bigserial PRIMARY KEY,
    snapshot_id uuid NOT NULL,
    file_id     bigint NOT NULL,
    symbol_id   bigint,
    rule        varchar(40) NOT NULL,
    category    varchar(20) NOT NULL,
    severity    varchar(10) NOT NULL CHECK (severity IN ('info', 'low', 'medium', 'high')),
    line        integer NOT NULL,
    message     text NOT NULL,
    evidence    text NOT NULL DEFAULT '',
    fingerprint varchar(32) NOT NULL,
    CONSTRAINT fk_findings_snapshot_id_snapshots FOREIGN KEY (snapshot_id) REFERENCES snapshots (id) ON DELETE CASCADE,
    CONSTRAINT fk_findings_file_id_files FOREIGN KEY (file_id) REFERENCES files (id) ON DELETE CASCADE,
    CONSTRAINT fk_findings_symbol_id_symbols FOREIGN KEY (symbol_id) REFERENCES symbols (id) ON DELETE CASCADE
);
ALTER TABLE findings RENAME CONSTRAINT findings_pkey TO pk_findings;
CREATE INDEX ix_findings_snapshot_id ON findings (snapshot_id);

CREATE TABLE duplicates (
    id          bigserial PRIMARY KEY,
    snapshot_id uuid NOT NULL,
    symbol_a    bigint NOT NULL,
    symbol_b    bigint NOT NULL,
    similarity  double precision NOT NULL,
    CONSTRAINT fk_duplicates_snapshot_id_snapshots
        FOREIGN KEY (snapshot_id) REFERENCES snapshots (id) ON DELETE CASCADE,
    CONSTRAINT fk_duplicates_symbol_a_symbols FOREIGN KEY (symbol_a) REFERENCES symbols (id) ON DELETE CASCADE,
    CONSTRAINT fk_duplicates_symbol_b_symbols FOREIGN KEY (symbol_b) REFERENCES symbols (id) ON DELETE CASCADE
);
ALTER TABLE duplicates RENAME CONSTRAINT duplicates_pkey TO pk_duplicates;
CREATE INDEX ix_duplicates_snapshot_id ON duplicates (snapshot_id);

CREATE TABLE questions (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       uuid NOT NULL,
    repository_id uuid,
    question      text NOT NULL,
    answer        text NOT NULL,
    status        varchar(20) NOT NULL CHECK (status IN ('answered', 'not_found', 'refused', 'error')),
    provider      varchar(100) NOT NULL,
    citations     jsonb NOT NULL DEFAULT '[]'::jsonb,
    usage         jsonb NOT NULL DEFAULT '{}'::jsonb,
    latency_ms    integer NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fk_questions_user_id_users FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
    CONSTRAINT fk_questions_repository_id_repositories
        FOREIGN KEY (repository_id) REFERENCES repositories (id) ON DELETE SET NULL
);
ALTER TABLE questions RENAME CONSTRAINT questions_pkey TO pk_questions;
CREATE INDEX ix_questions_user_id ON questions (user_id);

CREATE TABLE search_history (
    id         bigserial PRIMARY KEY,
    user_id    uuid NOT NULL,
    query      varchar(500) NOT NULL,
    mode       varchar(10) NOT NULL,
    reranked   boolean NOT NULL,
    results    integer NOT NULL,
    latency_ms integer NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fk_search_history_user_id_users FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
);
ALTER TABLE search_history RENAME CONSTRAINT search_history_pkey TO pk_search_history;
CREATE INDEX ix_search_history_user_id ON search_history (user_id);

CREATE TABLE jobs (
    id           bigserial PRIMARY KEY,
    job_type     varchar(50) NOT NULL,
    payload      jsonb NOT NULL DEFAULT '{}'::jsonb,
    status       varchar(20) NOT NULL DEFAULT 'queued'
                 CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
    attempts     integer NOT NULL DEFAULT 0,
    max_attempts integer NOT NULL DEFAULT 3,
    run_after    timestamptz NOT NULL DEFAULT now(),
    locked_at    timestamptz,
    finished_at  timestamptz,
    last_error   text,
    result       jsonb,
    created_at   timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE jobs RENAME CONSTRAINT jobs_pkey TO pk_jobs;
CREATE INDEX ix_jobs_status_run_after ON jobs (status, run_after);

CREATE TABLE audit_log (
    id        bigserial PRIMARY KEY,
    user_id   uuid,
    action    varchar(100) NOT NULL,
    entity    varchar(50) NOT NULL,
    entity_id varchar(100),
    details   jsonb NOT NULL DEFAULT '{}'::jsonb,
    ts        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fk_audit_log_user_id_users FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE SET NULL
);
ALTER TABLE audit_log RENAME CONSTRAINT audit_log_pkey TO pk_audit_log;
"""

DOWNGRADE_SQL = """
DROP VIEW IF EXISTS readable_repositories;
DROP TABLE IF EXISTS audit_log, jobs, search_history, questions, duplicates, findings, chunks, imports, refs,
    symbols, files, snapshots, repositories, workspace_members, workspaces, refresh_tokens, users CASCADE;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
