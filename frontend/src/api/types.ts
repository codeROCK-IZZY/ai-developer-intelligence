export type Role = "admin" | "member" | "viewer";
export type WorkspaceRole = "owner" | "editor" | "reader";
export type SnapshotStatus = "queued" | "processing" | "ready" | "failed";
export type AnswerStatus = "answered" | "not_found" | "refused" | "error";
export type Severity = "info" | "low" | "medium" | "high";

export interface User { id: string; email: string; full_name: string; role: Role; is_active: boolean; created_at: string }

export interface SystemInfo {
  llm_mode: "claude" | "offline"; llm_model: string | null; embedding_model: string; code_leaves_server: boolean;
}

export interface Workspace {
  id: string; name: string; description: string; role: WorkspaceRole; members: number; repositories: number;
  created_at: string;
}

export interface Member { user_id: string; email: string; full_name: string; role: WorkspaceRole }

export interface EntryPoint { path: string; line: number; kind: "script" | "main" | "route"; name: string; detail?: string }

export interface SnapshotStats {
  files: number; code_files: number; test_files: number; loc: number; code_loc: number;
  languages: Record<string, { files: number; loc: number }>; functions: number; classes: number;
  avg_complexity: number; max_complexity: number; calls: number; resolved_calls: number;
  external_packages: Record<string, number>; findings: Partial<Record<Severity, number>>;
  findings_by_category: Record<string, number>; import_cycles: number; public_functions: number;
  public_functions_called_from_tests: number; health: number; grade: string; syntax_errors: number;
  entry_points: EntryPoint[]; chunks: number; embeddings_reused: number; seconds: number;
}

export interface Snapshot {
  id: string; repository_id: string; seq: number; label: string; source: "upload" | "github"; ref: string | null;
  status: SnapshotStatus; stage: string | null; error: string | null; stats: Partial<SnapshotStats>;
  skipped: Record<string, number>; created_at: string; processed_at: string | null;
}

export interface Repository {
  id: string; workspace_id: string; workspace_name: string; name: string; description: string;
  source_url: string | null; default_ref: string | null; role: WorkspaceRole; latest_snapshot: Snapshot | null;
  snapshots: number; created_at: string;
}

export interface CodeFile {
  id: number; path: string; language: string; loc: number; is_test: boolean; health: number; grade: string;
  centrality: number; fan_in: number; fan_out: number; findings: Partial<Record<Severity, number>>;
}

export interface CodeSymbol {
  id: number; file_id: number; path: string; kind: "function" | "method" | "class"; name: string;
  qualified_name: string; start_line: number; end_line: number; signature: string; docstring: string;
  exported: boolean; decorators: string[]; params: number; complexity: number; max_nesting: number; loc: number;
  fan_in: number; fan_out: number; tested: boolean;
}

export interface Finding {
  id: number; rule: string; title: string; category: string; severity: Severity; path: string; file_id: number;
  line: number; symbol_id: number | null; symbol: string | null; message: string; evidence: string; fix: string;
}

export interface FileDetail {
  id: number; path: string; language: string; loc: number; is_test: boolean; health: number; grade: string;
  content: string; symbols: CodeSymbol[]; findings: Finding[]; imported_by: string[];
  imports: { module: string; line: number; kind: string; package: string | null; targets: string[] }[];
}

export interface SymbolRef { id: number; qualified_name: string; kind: string; path: string; start_line: number; line: number; calls: number }

export interface SymbolDetail {
  symbol: CodeSymbol; snapshot_id: string; language: string; code: string; callers: SymbolRef[]; callees: SymbolRef[];
  external_calls: { name: string; line: number; calls: number }[]; members: CodeSymbol[]; findings: Finding[];
  duplicates: { id: number; qualified_name: string; path: string; similarity: number }[];
}

export interface Explanation { provider: string; generated: boolean; text: string; facts: string[] }

export interface Overview {
  snapshot: { id: string; seq: number; label: string; processed_at: string };
  stats: SnapshotStats; skipped: Record<string, number>;
  core_files: { id: number; path: string; centrality: number; imported_by: number; health: number; grade: string }[];
  complex_functions: CodeSymbol[]; most_used: CodeSymbol[]; top_findings: Finding[];
  top_directories: { directory: string; loc: number }[];
}

export interface FindingsResponse {
  items: Finding[];
  summary: { rule: string; title: string; category: string; severity: Severity; count: number }[];
}

export interface DependencyGraph {
  level: "file" | "directory";
  nodes: { id: string; files: number; loc: number; tests: number; health: number }[];
  edges: { source: string; target: string; imports: number; mutual: boolean }[];
  cycles: string[];
}

export interface Duplicate { similarity: number; a: CodeSymbol; b: CodeSymbol }

export interface ImpactSymbol {
  id: number; path: string; qualified_name: string; kind: string; start_line: number; end_line: number;
  complexity: number; loc: number; depth?: number; via?: string; complexity_before?: number;
}

export interface Comparison {
  base: { id: string; seq: number; label: string }; head: { id: string; seq: number; label: string };
  files: { added: string[]; removed: string[]; modified: string[] };
  symbols: { changed: ImpactSymbol[]; added: ImpactSymbol[]; removed: ImpactSymbol[] };
  impacted: ImpactSymbol[]; tests_to_run: ImpactSymbol[]; untested_changes: ImpactSymbol[];
  findings: { new: Omit<Finding, "id" | "title" | "fix" | "file_id" | "symbol_id" | "symbol" | "evidence">[];
    fixed: Omit<Finding, "id" | "title" | "fix" | "file_id" | "symbol_id" | "symbol" | "evidence">[] };
  metrics: Record<string, { base: unknown; head: unknown }>;
  risk: { score: number; level: "low" | "medium" | "high"; reasons: { points: number; reason: string }[] };
}

export interface SearchHit {
  chunk_id: number; repository_id: string; repository_name: string; snapshot_id: string; file_id: number; path: string;
  language: string; is_test: boolean; symbol_id: number | null; symbol: string | null; symbol_kind: string | null;
  signature: string | null; start_line: number; end_line: number; text: string; score: number;
  vector_similarity: number | null; keyword_rank: number | null; rerank_score: number | null;
}

export interface SearchResponse { query: string; mode: string; reranked: boolean; latency_ms: number; results: SearchHit[] }

export interface Citation {
  ordinal: number; chunk_id: number; repository_id: string; repository_name: string; snapshot_id: string; path: string;
  start_line: number; end_line: number; symbol: string | null; symbol_id: number | null; cited_text: string;
}

export interface AskResult {
  question_id: string; status: AnswerStatus; answer: string; provider: string;
  sentences: { text: string; citations: number[]; supported: boolean }[]; citations: Citation[];
  retrieved: number; best_relevance: number | null; latency_ms: number; notes: string[];
}

export interface QuestionRow {
  id: string; question: string; answer: string; status: AnswerStatus; provider: string; citations: Citation[];
  latency_ms: number; created_at: string;
}
