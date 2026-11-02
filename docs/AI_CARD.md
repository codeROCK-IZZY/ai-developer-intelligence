# AI card — what is a model and what is a rule

| Output | Produced by | Deterministic? |
|---|---|---|
| Symbols, calls, imports, complexity, nesting | tree-sitter parsing + code (`app/code/parser.py`) | Yes |
| Call graph, dependency graph, cycles, central files | Rules over the parse (`resolve.py`, `graph.py`) | Yes |
| Findings (security, reliability, maintainability, design) | Documented rules (`rules.py`, `docs/DESIGN.md` §4) | Yes |
| Duplicates | MinHash over normalised tokens | Yes (fixed hashing) |
| Health grade, change risk score | Points systems with published weights | Yes |
| Search ranking | Local embedding model `BAAI/bge-small-en-v1.5` + PostgreSQL full text; optional re-ranker `ms-marco-MiniLM-L-6-v2` | Yes for given models |
| Q&A relevance gate | Re-ranker score threshold | Yes for given models |
| Answers (offline) | Lists the most relevant functions with their own documentation | Yes — no generated text |
| Answers and explanations (Claude) | Claude (`ADI_CLAUDE_MODEL`, default `claude-opus-5-5`) | No — generated, labelled "AI" in the UI |

## How answers are grounded
* Only chunks from repositories the asker can read are retrieved (access control is in the SQL).
* If the best chunk's re-ranker relevance is below the gate, the answer is "not found" and no model is called.
* With Claude, each chunk is a cited `document` block; an answer with no citations is withheld.
* Code and comments are treated as data. The model has no tools, so instructions planted in code cannot trigger actions.

## Data sent to third parties
None in offline mode. With `ANTHROPIC_API_KEY` set, the **retrieved code chunks** for a question (up to 6)
and, for "Explain", the selected function's code are sent to Anthropic. `/api/v1/system/info` reports
`code_leaves_server`, and the UI shows the active mode.

## When not to trust it
* **The call graph is static.** Dynamic dispatch, dependency injection, callbacks, reflection and calls
  through generic names (`repo.save()`) are often unresolved — on the benchmark 10 % of real calls were
  missed. "No callers" means "none found", not "none exist".
* **Findings are pattern rules.** They miss vulnerabilities that need data-flow analysis and can flag
  safe code (e.g. an f-string built from validated input). Treat them as review prompts.
* **Dead code** is conservative but can still be wrong for code used by other repositories, plugins or
  frameworks that call by name.
* **The models are general-purpose.** The re-ranker was trained on web text and scores code low; the
  Q&A gate was tuned on the project's own benchmark. Expect more "not found" on unfamiliar codebases.
* **"Tested"** means a test calls the function directly (static). It is not runtime coverage.
