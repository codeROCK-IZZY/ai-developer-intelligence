# Development guide

## Prerequisites
Python 3.12+, Node 22, PostgreSQL 16 with the **pgvector** extension. No other system packages:
tree-sitter grammars and ONNX Runtime install as Python wheels.

## Database
pgvector must be enabled by a superuser once per database (the migration also runs
`CREATE EXTENSION IF NOT EXISTS`, which works for the RDS master user).

```bash
sudo -u postgres psql -c "CREATE ROLE adi LOGIN PASSWORD 'adi' CREATEDB;" -c "CREATE DATABASE adi OWNER adi;" -c "CREATE DATABASE adi_test OWNER adi;"
```

```bash
for d in adi adi_test; do sudo -u postgres psql -d $d -c "CREATE EXTENSION IF NOT EXISTS vector;"; done
```

Or with Docker:

```bash
docker run -d --name adi-db -e POSTGRES_USER=adi -e POSTGRES_PASSWORD=adi -e POSTGRES_DB=adi_test -p 127.0.0.1:5437:5432 pgvector/pgvector:pg16
```

## Backend

```bash
cd backend && python -m venv .venv && . .venv/bin/activate && pip install -r requirements-dev.txt
```

```bash
export ADI_DATABASE_URL=postgresql+psycopg://adi:adi@localhost:5432/adi ADI_JWT_SECRET=$(python -c "import secrets; print(secrets.token_urlsafe(48))") ADI_COOKIE_SECURE=false ADI_DEMO_PASSWORD='choose-a-long-password'
```

```bash
python -m app.cli demo        # migrate, create demo users and repositories, process the snapshots
uvicorn app.main:app --reload --port 8000
python -m app.cli worker      # in another terminal, for later imports
```

The first run downloads the embedding and re-ranker models (~150 MB) to the fastembed cache.

## Frontend

```bash
cd frontend && npm install && npm run dev
```

The app is at <http://localhost:5173>; `/api` is proxied to port 8000.

## Tests

| Command | What it runs |
|---|---|
| `cd backend && pytest` | All backend tests on `adi_test` (schema rebuilt from the migration). AI runs offline: no API key, no cost |
| `pytest tests/test_analysis.py` | Parser, resolver, rules, chunking, archives — no database; includes the regression guard against the labelled demo |
| `cd frontend && npm test` | Vitest |
| `make evaluate` | Full evaluation on the loaded demo → `eval/reports/` |

Tests read `ADI_DATABASE_URL` (default `postgresql+psycopg://adi:adi@127.0.0.1:5437/adi_test`).

## CLI (`python -m app.cli`)
`migrate` · `create-user --email E --role admin|member|viewer` · `load-demo` · `drain` · `worker` · `evaluate --out DIR` · `demo`

## Code map
| Path | Responsibility |
|---|---|
| `app/code/` | The analysis engine, no database: archive reading, tree-sitter parsing, resolution, graph, rules, duplicates, chunking, `analyze()` |
| `app/pipeline/runner.py` | Snapshot job: fetch → analyse → store → embed (with reuse) |
| `app/connectors/github.py` | SSRF-safe download of public GitHub archives |
| `app/modules/repositories/` | Repositories, snapshot import, reprocess, delete |
| `app/modules/code/` | Browse and analysis endpoints; `impact.py` = snapshot comparison |
| `app/retrieval/` | Hybrid search with access control in SQL; grounded Q&A |
| `app/ai/` | Provider interface, Claude, offline answerer, embeddings and re-ranker |
| `app/synthetic/demo.py`, `demo/` | The demo repository (v1, v2) and its hand-labelled ground truth |
| `app/evaluation/` | Every reported metric |
| `migrations/versions/0001_initial_schema.py` | Schema, including the `readable_repositories` access view |
