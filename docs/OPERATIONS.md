# Operations runbook

## Health
`GET /api/health/live` (process) · `GET /api/health/ready` (database + active AI mode) ·
`GET /api/v1/system/info` (provider and model shown in the UI).

## Imports
- **A snapshot stays `queued`.** The worker isn't running: `docker compose ps worker`, then its logs.
- **A snapshot is `failed`.** The repository card shows the error. Common causes: not a ZIP; archive over
  `ADI_MAX_ARCHIVE_MB`; more than `ADI_ARCHIVE_MAX_ENTRIES` entries; no supported source files; GitHub
  ref not found or the repository is private. Bad-input failures are not retried; transient errors are
  retried 3 times with backoff (**Administration → Recent jobs**).
- **Files are missing.** Each snapshot records what was skipped and why (dependency/build folders,
  binaries, minified bundles, files over `ADI_MAX_FILE_KB`) — shown on the Overview tab.
- **Re-process** a snapshot after upgrading the analyser: `POST /api/v1/snapshots/{id}/reprocess`
  (editor). Processing is idempotent — previous rows for the snapshot are replaced.
- **Slow processing.** Embedding dominates. Check thread limits (`ADI_EMBEDDING_THREADS`,
  `OMP_THREAD_LIMIT`); the stats show how many embeddings were reused from the previous snapshot.

## Answers look wrong
1. Open the citation. If the cited code is wrong, it is a retrieval problem: run the question in
   **Code search** with and without re-ranking and compare.
2. "Not found" on an answerable question: the best re-ranker relevance was below
   `ADI_MIN_RERANK_RELEVANCE` (0.01; shown as *best relevance* under the answer). Lowering it answers more,
   from weaker evidence.
3. Re-run `make evaluate` after any change to retrieval or the analyser, and check that leakage is 0.

## Findings
- A false positive in a specific place: there is no suppression mechanism yet; record it in the review.
  Rule changes go through `app/code/rules.py` with a test in `tests/test_analysis.py` and a re-run of
  the evaluation (finding precision/recall on the labelled demo).

## Access questions
"Why can user X see repository Y?" Only workspace membership grants access:
```sql
SELECT * FROM readable_repositories WHERE repository_id = '<id>';
```
Membership changes are in `audit_log` (`workspace.member`, `workspace.member_remove`); imports and
deletions too (`snapshot.create`, `repository.delete`).

## Backups
- **Local:** `docker compose exec db pg_dump -U adi -Fc adi > adi.dump`, plus the `files` volume
  (imported archives — everything else can be rebuilt from them by reprocessing).
- **AWS:** RDS automated backups (7 days) and a final snapshot; S3 versioning (7-day window).

## Changing the AI provider
- **Turn Claude on:** set `ANTHROPIC_API_KEY`, restart the API. Answers and explanations become generated
  text with citations; code chunks are then sent to Anthropic.
- **Turn Claude off:** remove the key or set `ADI_LLM_PROVIDER=offline`.
- **Cost control:** `ADI_CLAUDE_MODEL`, `ADI_CLAUDE_EFFORT` (default `medium`); token usage is stored per
  question in `questions.usage`.
