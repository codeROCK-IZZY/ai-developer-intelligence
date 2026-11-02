# Known limitations

| Area | Limitation | Why / path forward |
|---|---|---|
| Languages | Python, JavaScript, TypeScript/TSX, Go, Java are parsed. Other files are indexed as text only | Adding a language is a grammar wheel plus one `LanguageSpec`; C#, Rust and Ruby are the obvious next ones |
| Call resolution | Static and name-based: no type inference. Calls on parameters or attributes resolve only via the unique-method heuristic; inheritance and interfaces are not followed | A type-aware resolver (e.g. Pyright, `tsc`, `gopls` via LSP) would raise recall but needs each project's dependencies installed |
| Security rules | Pattern rules on single call sites, no taint/data-flow tracking | Integrate a dedicated SAST engine (Semgrep, CodeQL) and import its SARIF output as findings |
| Dependency vulnerabilities | External packages are listed, not checked against advisories | Query OSV.dev for the parsed manifests |
| GitHub | Public repositories only; no credentials are stored | GitHub App installation tokens, stored in Secrets Manager |
| Git history | Snapshots are archives, not commits: no blame, churn or authorship | Clone with `git` in the worker to add churn-weighted hotspots and per-commit impact |
| Search relevance | Small benchmark (40 answerable questions, one repository); the Q&A gate was tuned on it | Larger labelled set across real open-source repositories; a code-trained re-ranker/embedding model |
| Re-ranking cost | ~3 s per question on a laptop CPU | GPU inference, a smaller cross-encoder, or re-ranking fewer candidates |
| Indexing speed | Embedding is ~4 chunks/s on a laptop CPU, so a first import of a 2,000-file repository takes minutes | Batch on a larger worker; incremental reuse already avoids re-embedding unchanged code |
| Vector search scale | Exact scan over the allowed chunks (correct under access control; fine to ~100k chunks per user) | Partition by repository, or HNSW with iterative scans (pgvector ≥ 0.8) once the access filter is selective enough |
| Rate limiting | Per-process (login, Q&A) | Shared store (Redis) or WAF rules with several API replicas |
| Large files | Files over 512 KB and minified bundles are skipped (reported per snapshot) | Configurable via `ADI_MAX_FILE_KB` |
