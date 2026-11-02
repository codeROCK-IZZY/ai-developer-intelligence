import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { useMemo, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type {
  CodeFile, Comparison, DependencyGraph, Duplicate, FileDetail, FindingsResponse, ImpactSymbol, Overview, Repository,
  Severity, Snapshot,
} from "../api/types";
import { CodeView, fileHref, FindingCounts, GradeBadge, LanguageBar, SeverityBadge, SymbolLink } from "../components/code";
import { Badge, Card, Empty, ErrorBox, Input, Loading, PageHeader, Select, Stat, Table } from "../components/ui";
import { pct } from "../lib/format";

const TABS = [
  ["overview", "Overview"], ["files", "Code"], ["findings", "Findings"], ["architecture", "Architecture"],
  ["changes", "Changes"],
] as const;

export function RepositoryPage() {
  const { id = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") ?? "overview";
  const repo = useQuery({ queryKey: ["repository", id], queryFn: () => api<Repository>(`/repositories/${id}`) });
  const snaps = useQuery({ queryKey: ["snapshots", id], queryFn: () => api<Snapshot[]>(`/repositories/${id}/snapshots`) });
  const ready = snaps.data?.filter((s) => s.status === "ready") ?? [];
  const snapshotId = params.get("snapshot") ?? repo.data?.latest_snapshot?.id ?? null;
  const set = (k: string, v: string | null) => {
    const next = new URLSearchParams(params);
    if (v === null) next.delete(k); else next.set(k, v);
    if (k === "tab") { next.delete("path"); next.delete("line"); }
    setParams(next);
  };
  if (repo.isLoading) return <Loading />;
  if (repo.error || !repo.data) return <ErrorBox error={repo.error} />;
  const r = repo.data;
  return (
    <>
      <PageHeader
        title={r.name}
        subtitle={<>
          <Badge tone="violet">{r.workspace_name}</Badge>{" "}
          {r.description}{" "}
          {r.source_url && <a className="text-sky-700 hover:underline" href={r.source_url} target="_blank" rel="noreferrer">GitHub</a>}
        </>}
        actions={ready.length > 0 && (
          <Select aria-label="Snapshot" value={snapshotId ?? ""} onChange={(e) => set("snapshot", e.target.value)}>
            {ready.map((s) => <option key={s.id} value={s.id}>#{s.seq} {s.label}</option>)}
          </Select>
        )}
      />
      <nav className="mb-5 flex gap-1 border-b border-slate-200" aria-label="Repository sections">
        {TABS.map(([key, label]) => (
          <button key={key} onClick={() => set("tab", key)}
            className={clsx("-mb-px border-b-2 px-3 py-2 text-sm", tab === key ? "border-sky-600 font-medium text-sky-800" : "border-transparent text-slate-600 hover:text-slate-900")}>
            {label}
          </button>
        ))}
      </nav>
      {!snapshotId ? <Empty>This repository has no processed snapshot yet. Import one from the Repositories page.</Empty> : (
        <>
          {tab === "overview" && <OverviewTab repoId={id} snapshotId={snapshotId} />}
          {tab === "files" && <FilesTab repoId={id} snapshotId={snapshotId} path={params.get("path")} line={Number(params.get("line")) || undefined}
            onOpen={(p) => set("path", p)} />}
          {tab === "findings" && <FindingsTab repoId={id} snapshotId={snapshotId} />}
          {tab === "architecture" && <ArchitectureTab snapshotId={snapshotId} />}
          {tab === "changes" && <ChangesTab repoId={id} snapshots={ready} />}
        </>
      )}
    </>
  );
}

// ------------------------------------------------------------------ overview
function OverviewTab({ repoId, snapshotId }: { repoId: string; snapshotId: string }) {
  const q = useQuery({ queryKey: ["overview", snapshotId], queryFn: () => api<Overview>(`/snapshots/${snapshotId}/overview`) });
  if (q.isLoading) return <Loading />;
  if (!q.data) return <ErrorBox error={q.error} />;
  const { stats: s } = q.data;
  const deps = Object.entries(s.external_packages ?? {});
  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-5">
        <Stat label="Health" value={<GradeBadge grade={s.grade} health={s.health} />} sub={`${s.health}/100, rule-based`} />
        <Stat label="Code" value={`${s.loc.toLocaleString()} lines`} sub={`${s.files} files · ${s.test_files} test files`} />
        <Stat label="Functions" value={s.functions} sub={`${s.classes} classes · avg complexity ${s.avg_complexity}`} />
        <Stat label="Findings" value={<FindingCounts counts={s.findings} />} sub={`${s.import_cycles} import cycles`} />
        <Stat label="Reached by tests" value={pct(s.public_functions ? s.public_functions_called_from_tests / s.public_functions : 0)}
          sub="public functions called directly from a test (static, not coverage)" />
      </div>
      <Card title="Languages"><LanguageBar languages={s.languages} /></Card>
      <div className="grid gap-6 lg:grid-cols-2">
        <Card title="Start here: the most central files">
          <p className="mb-2 text-xs text-slate-500">Ranked by PageRank on the import graph — files that many important files depend on.</p>
          <Table>
            <thead><tr><th>File</th><th>Imported by</th><th>Health</th></tr></thead>
            <tbody>{q.data.core_files.map((f) => (
              <tr key={f.id}><td><Link className="font-mono text-sky-700 hover:underline" to={fileHref(repoId, f.path, undefined, snapshotId)}>{f.path}</Link></td>
                <td className="tabular-nums">{f.imported_by}</td><td><GradeBadge grade={f.grade} health={f.health} /></td></tr>))}
            </tbody>
          </Table>
        </Card>
        <Card title="Entry points">
          {!s.entry_points?.length ? <Empty>No entry points detected.</Empty> : (
            <ul className="space-y-1 text-sm">{s.entry_points.slice(0, 15).map((e) => (
              <li key={`${e.path}:${e.line}`} className="flex items-center gap-2">
                <Badge tone={e.kind === "route" ? "sky" : "slate"}>{e.kind}</Badge>
                <Link className="font-mono text-sky-700 hover:underline" to={fileHref(repoId, e.path, e.line, snapshotId)}>{e.name}</Link>
                {e.detail && <code className="truncate text-xs text-slate-500">@{e.detail}</code>}
              </li>))}
            </ul>
          )}
        </Card>
        <Card title="Most complex functions">
          <Table>
            <thead><tr><th>Function</th><th>Complexity</th><th>Lines</th></tr></thead>
            <tbody>{q.data.complex_functions.map((f) => (
              <tr key={f.id}><td><SymbolLink id={f.id} name={f.qualified_name} /><p className="text-xs text-slate-500">{f.path}</p></td>
                <td className={clsx("tabular-nums", f.complexity > 10 && "font-semibold text-red-700")}>{f.complexity}</td><td className="tabular-nums">{f.loc}</td></tr>))}
            </tbody>
          </Table>
        </Card>
        <Card title="Most used functions">
          <Table>
            <thead><tr><th>Function</th><th>Callers</th><th>Tested</th></tr></thead>
            <tbody>{q.data.most_used.map((f) => (
              <tr key={f.id}><td><SymbolLink id={f.id} name={f.qualified_name} /><p className="text-xs text-slate-500">{f.path}</p></td>
                <td className="tabular-nums">{f.fan_in}</td><td>{f.tested ? <Badge tone="green">yes</Badge> : <Badge tone="amber">no</Badge>}</td></tr>))}
            </tbody>
          </Table>
        </Card>
      </div>
      <Card title="Most important findings">
        {!q.data.top_findings.length ? <Empty>No high or medium severity findings.</Empty> : <FindingsTable repoId={repoId} snapshotId={snapshotId} items={q.data.top_findings} />}
      </Card>
      <div className="grid gap-6 lg:grid-cols-2">
        <Card title="External dependencies">
          {!deps.length ? <Empty>None detected.</Empty> : (
            <div className="flex flex-wrap gap-1.5">{deps.map(([p, n]) => <Badge key={p}>{p} · {n}</Badge>)}</div>
          )}
        </Card>
        <Card title="Indexing">
          <p className="text-sm text-slate-600">
            {s.chunks} search chunks · {s.embeddings_reused} embeddings reused from the previous snapshot · processed in {s.seconds}s
            {s.syntax_errors > 0 && <> · {s.syntax_errors} syntax errors tolerated</>}.
          </p>
          {Object.keys(q.data.skipped).length > 0 && (
            <p className="mt-2 text-xs text-slate-500">Skipped: {Object.entries(q.data.skipped).map(([k, v]) => `${v} ${k}`).join(" · ")}</p>
          )}
        </Card>
      </div>
    </div>
  );
}

function FindingsTable({ repoId, snapshotId, items }: { repoId: string; snapshotId: string; items: FindingsResponse["items"] }) {
  return (
    <Table>
      <thead><tr><th>Severity</th><th>Finding</th><th>Where</th><th>How to fix</th></tr></thead>
      <tbody>{items.map((f) => (
        <tr key={f.id}>
          <td><SeverityBadge severity={f.severity} /></td>
          <td><p className="font-medium">{f.title}</p><p className="text-xs text-slate-600">{f.message}</p>
            {f.evidence && <code className="mt-1 block max-w-md truncate text-xs text-slate-500" title={f.evidence}>{f.evidence}</code>}</td>
          <td><Link className="font-mono text-xs text-sky-700 hover:underline" to={fileHref(repoId, f.path, f.line, snapshotId)}>{f.path}:{f.line}</Link>
            {f.symbol && <p className="text-xs"><SymbolLink id={f.symbol_id} name={f.symbol} /></p>}</td>
          <td className="max-w-xs text-xs text-slate-600">{f.fix}</td>
        </tr>))}
      </tbody>
    </Table>
  );
}

// ------------------------------------------------------------------ files
function FilesTab({ repoId, snapshotId, path, line, onOpen }: {
  repoId: string; snapshotId: string; path: string | null; line?: number; onOpen: (p: string) => void;
}) {
  const files = useQuery({ queryKey: ["files", snapshotId], queryFn: () => api<CodeFile[]>(`/snapshots/${snapshotId}/files`) });
  const [filter, setFilter] = useState("");
  const shown = files.data?.filter((f) => f.path.toLowerCase().includes(filter.toLowerCase())) ?? [];
  const current = path ?? files.data?.find((f) => f.language !== "text")?.path ?? null;
  return (
    <div className="grid gap-6 lg:grid-cols-[320px_1fr]">
      <Card title={`Files (${files.data?.length ?? 0})`}>
        <Input aria-label="Filter files" placeholder="Filter…" value={filter} onChange={(e) => setFilter(e.target.value)} />
        <ul className="mt-2 max-h-[70vh] space-y-0.5 overflow-auto text-sm">
          {shown.map((f) => (
            <li key={f.id}>
              <button onClick={() => onOpen(f.path)} className={clsx("flex w-full items-center justify-between gap-2 rounded px-2 py-1 text-left",
                current === f.path ? "bg-sky-50 font-medium" : "hover:bg-slate-50")}>
                <span className={clsx("truncate font-mono text-xs", f.is_test && "text-slate-500")}>{f.path}</span>
                <span className="flex shrink-0 items-center gap-1">
                  {(f.findings.high ?? 0) > 0 && <span className="h-2 w-2 rounded-full bg-red-500" title={`${f.findings.high} high`} />}
                  {f.language !== "text" && <GradeBadge grade={f.grade} health={f.health} />}
                </span>
              </button>
            </li>
          ))}
        </ul>
      </Card>
      {current ? <FileView repoId={repoId} snapshotId={snapshotId} path={current} line={line} /> : <Empty>Select a file.</Empty>}
    </div>
  );
}

function FileView({ repoId, snapshotId, path, line }: { repoId: string; snapshotId: string; path: string; line?: number }) {
  const q = useQuery({
    queryKey: ["file", snapshotId, path],
    queryFn: () => api<FileDetail>(`/snapshots/${snapshotId}/file?path=${encodeURIComponent(path)}`),
  });
  if (q.isLoading) return <Loading />;
  if (!q.data) return <ErrorBox error={q.error} />;
  const f = q.data;
  return (
    <div className="min-w-0 space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="font-mono text-sm font-semibold">{f.path}</h2>
        <Badge>{f.language}</Badge>{f.is_test && <Badge tone="sky">test</Badge>}
        <GradeBadge grade={f.grade} health={f.health} /><span className="text-xs text-slate-500">{f.loc} lines</span>
      </div>
      <CodeView code={f.content} findings={f.findings} focusLine={line} />
      <div className="grid gap-4 xl:grid-cols-3">
        <Card title="Outline">
          {!f.symbols.length ? <Empty>No symbols.</Empty> : (
            <ul className="space-y-1 text-sm">{f.symbols.map((s) => (
              <li key={s.id} className={clsx(s.kind === "method" && "pl-4")}>
                <SymbolLink id={s.id} name={s.qualified_name} />{" "}
                <span className="text-xs text-slate-500">L{s.start_line} · cc {s.complexity}</span>
              </li>))}
            </ul>
          )}
        </Card>
        <Card title="Dependencies">
          <p className="text-xs font-medium text-slate-500">Imports</p>
          <ul className="mb-3 space-y-0.5 text-xs">{f.imports.map((i) => (
            <li key={`${i.line}-${i.module}`} className="font-mono">
              {i.targets.length ? i.targets.map((t) => <Link key={t} className="text-sky-700 hover:underline" to={fileHref(repoId, t, undefined, snapshotId)}>{t} </Link>)
                : <span className="text-slate-500">{i.module || "."} <Badge>{i.kind}</Badge></span>}
            </li>))}
          </ul>
          <p className="text-xs font-medium text-slate-500">Imported by</p>
          <ul className="space-y-0.5 text-xs">{f.imported_by.map((p) => (
            <li key={p}><Link className="font-mono text-sky-700 hover:underline" to={fileHref(repoId, p, undefined, snapshotId)}>{p}</Link></li>))}
            {!f.imported_by.length && <li className="text-slate-400">nothing in this repository</li>}
          </ul>
        </Card>
        <Card title={`Findings (${f.findings.length})`}>
          {!f.findings.length ? <Empty>None.</Empty> : (
            <ul className="space-y-2 text-sm">{f.findings.map((x) => (
              <li key={x.id}><SeverityBadge severity={x.severity} /> <span className="font-medium">{x.title}</span>
                <span className="text-xs text-slate-500"> · line {x.line}</span><p className="text-xs text-slate-600">{x.message}</p></li>))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ findings
function FindingsTab({ repoId, snapshotId }: { repoId: string; snapshotId: string }) {
  const [severity, setSeverity] = useState<Severity | "">("");
  const [category, setCategory] = useState("");
  const [rule, setRule] = useState("");
  const qs = new URLSearchParams();
  if (severity) qs.set("severity", severity);
  if (category) qs.set("category", category);
  if (rule) qs.set("rule", rule);
  const q = useQuery({
    queryKey: ["findings", snapshotId, severity, category, rule],
    queryFn: () => api<FindingsResponse>(`/snapshots/${snapshotId}/findings?${qs.toString()}`),
  });
  return (
    <div className="space-y-4">
      <Card title="Summary">
        <div className="flex flex-wrap gap-1.5">
          {q.data?.summary.map((s) => (
            <button key={`${s.rule}-${s.severity}`} onClick={() => setRule(rule === s.rule ? "" : s.rule)}
              className={clsx("rounded-full border px-2.5 py-1 text-xs", rule === s.rule ? "border-sky-600 bg-sky-50" : "border-slate-200 hover:bg-slate-50")}>
              <SeverityBadge severity={s.severity} /> {s.title} · {s.count}
            </button>
          ))}
        </div>
        <p className="mt-2 text-xs text-slate-500">All findings come from deterministic, documented rules (no AI): the same code always gives the same findings.</p>
      </Card>
      <div className="grid max-w-md gap-2 sm:grid-cols-2">
        <Select aria-label="Severity" value={severity} onChange={(e) => setSeverity(e.target.value as Severity | "")}>
          <option value="">All severities</option>{["high", "medium", "low", "info"].map((s) => <option key={s} value={s}>{s}</option>)}
        </Select>
        <Select aria-label="Category" value={category} onChange={(e) => setCategory(e.target.value)}>
          <option value="">All categories</option>{["security", "reliability", "maintainability", "design"].map((c) => <option key={c} value={c}>{c}</option>)}
        </Select>
      </div>
      {q.isLoading ? <Loading /> : !q.data?.items.length ? <Empty>No findings match.</Empty> : (
        <Card><FindingsTable repoId={repoId} snapshotId={snapshotId} items={q.data.items} /></Card>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ architecture
function ArchitectureTab({ snapshotId }: { snapshotId: string }) {
  const [depth, setDepth] = useState(2);
  const g = useQuery({
    queryKey: ["deps", snapshotId, depth],
    queryFn: () => api<DependencyGraph>(`/snapshots/${snapshotId}/dependencies?level=directory&depth=${depth}`),
  });
  const dups = useQuery({ queryKey: ["dups", snapshotId], queryFn: () => api<Duplicate[]>(`/snapshots/${snapshotId}/duplicates`) });
  return (
    <div className="space-y-6">
      <Card title="Module dependencies" actions={
        <Select aria-label="Grouping depth" value={depth} onChange={(e) => setDepth(Number(e.target.value))}>
          {[1, 2, 3, 4].map((d) => <option key={d} value={d}>Group by {d} folder level{d > 1 ? "s" : ""}</option>)}
        </Select>}>
        {g.isLoading ? <Loading /> : g.data && <DependencyDiagram graph={g.data} />}
        {g.data?.cycles.length ? (
          <div className="mt-3"><p className="text-sm font-medium text-red-700">Import cycles</p>
            <ul className="text-xs text-slate-700">{g.data.cycles.map((c) => <li key={c} className="font-mono">{c}</li>)}</ul></div>
        ) : null}
      </Card>
      <Card title="Duplicated functions">
        {!dups.data?.length ? <Empty>No near-duplicate functions found.</Empty> : (
          <Table>
            <thead><tr><th>Function</th><th>Copy</th><th>Similarity</th></tr></thead>
            <tbody>{dups.data.map((d) => (
              <tr key={`${d.a.id}-${d.b.id}`}>
                <td><SymbolLink id={d.a.id} name={d.a.qualified_name} /><p className="text-xs text-slate-500">{d.a.path}</p></td>
                <td><SymbolLink id={d.b.id} name={d.b.qualified_name} /><p className="text-xs text-slate-500">{d.b.path}</p></td>
                <td className="tabular-nums">{pct(d.similarity)}</td></tr>))}
            </tbody>
          </Table>
        )}
      </Card>
    </div>
  );
}

const MUTUAL = "#d97706";

/** Circular layout: node size by lines of code, colour by health; amber edges = folders importing each other. */
export function DependencyDiagram({ graph }: { graph: DependencyGraph }) {
  const width = 860;
  const height = 580;
  const radius = 220;
  const pos = useMemo(() => new Map(graph.nodes.map((n, i) => {
    const a = (2 * Math.PI * i) / Math.max(graph.nodes.length, 1) - Math.PI / 2;
    return [n.id, { x: width / 2 + radius * 1.25 * Math.cos(a), y: height / 2 + radius * Math.sin(a), a }];
  })), [graph]);
  const maxLoc = Math.max(...graph.nodes.map((n) => n.loc), 1);
  if (!graph.nodes.length) return <Empty>No internal imports.</Empty>;
  return (
    <>
    <svg viewBox={`0 0 ${width} ${height}`} className="mx-auto w-full max-w-4xl" role="img" aria-label="Module dependency diagram">
      <defs>
        <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="#64748b" />
        </marker>
      </defs>
      {graph.edges.map((e) => {
        const s = pos.get(e.source), t = pos.get(e.target);
        if (!s || !t) return null;
        return <line key={`${e.source}>${e.target}`} x1={s.x} y1={s.y} x2={t.x} y2={t.y} markerEnd="url(#arrow)"
          stroke={e.mutual ? MUTUAL : "#94a3b8"} strokeWidth={Math.min(1 + e.imports / 2, 4)} opacity={0.7}>
          <title>{`${e.source} → ${e.target} (${e.imports} imports)`}</title></line>;
      })}
      {graph.nodes.map((n) => {
        const p = pos.get(n.id)!;
        const r = 6 + 14 * Math.sqrt(n.loc / maxLoc);
        const fill = n.health >= 90 ? "#16a34a" : n.health >= 75 ? "#65a30d" : n.health >= 60 ? "#d97706" : "#dc2626";
        const right = Math.cos(p.a) >= 0;
        return (
          <g key={n.id}>
            <circle cx={p.x} cy={p.y} r={r} fill={fill} opacity={0.85}><title>{`${n.id}: ${n.files} files, ${n.loc} lines, health ${n.health}`}</title></circle>
            <text x={p.x + (right ? r + 4 : -r - 4)} y={p.y + 4} textAnchor={right ? "start" : "end"} fontSize="11" fill="#334155">{n.id}</text>
          </g>
        );
      })}
    </svg>
    <p className="mt-2 text-center text-xs text-slate-500">
      Circle size = lines of code · colour = health (green good → red poor) · arrow = imports ·{" "}
      <span style={{ color: MUTUAL }}>amber</span> = two folders import each other
    </p>
    </>
  );
}

// ------------------------------------------------------------------ changes
function ChangesTab({ repoId, snapshots }: { repoId: string; snapshots: Snapshot[] }) {
  const [base, setBase] = useState(snapshots[1]?.id ?? "");
  const [head, setHead] = useState(snapshots[0]?.id ?? "");
  const q = useQuery({
    queryKey: ["compare", base, head],
    queryFn: () => api<Comparison>(`/repositories/${repoId}/compare?base=${base}&head=${head}`),
    enabled: !!base && !!head && base !== head,
  });
  if (snapshots.length < 2) return <Empty>Import a second snapshot (a newer ZIP or GitHub ref) to see what changed and what it affects.</Empty>;
  const d = q.data;
  return (
    <div className="space-y-6">
      <div className="grid max-w-3xl items-center gap-2 text-sm sm:grid-cols-[auto_1fr_auto_1fr]">
        <span>Compare</span>
        <Select aria-label="Base snapshot" value={base} onChange={(e) => setBase(e.target.value)}>
          {snapshots.map((s) => <option key={s.id} value={s.id}>#{s.seq} {s.label}</option>)}</Select>
        <span>→</span>
        <Select aria-label="Head snapshot" value={head} onChange={(e) => setHead(e.target.value)}>
          {snapshots.map((s) => <option key={s.id} value={s.id}>#{s.seq} {s.label}</option>)}</Select>
      </div>
      {q.isLoading ? <Loading /> : q.error ? <ErrorBox error={q.error} /> : d && (
        <>
          <div className="grid gap-4 lg:grid-cols-[260px_1fr]">
            <Stat label="Change risk (rule-based)" value={<span className={clsx("text-2xl", { low: "text-green-700", medium: "text-amber-700", high: "text-red-700" }[d.risk.level])}>{d.risk.score} · {d.risk.level}</span>}
              sub={`${d.files.modified.length} modified, ${d.files.added.length} added, ${d.files.removed.length} removed files`} />
            <Card title="Why">
              <ul className="space-y-1 text-sm">{d.risk.reasons.map((r) => <li key={r.reason}><span className="tabular-nums font-medium">+{r.points}</span> {r.reason}</li>)}</ul>
            </Card>
          </div>
          <div className="grid gap-6 lg:grid-cols-2">
            <ImpactList title="Changed functions" items={d.symbols.changed} extra={(s) => s.complexity_before !== undefined && s.complexity_before !== s.complexity ? `complexity ${s.complexity_before} → ${s.complexity}` : null} />
            <ImpactList title="Added functions" items={d.symbols.added} />
            <ImpactList title="Affected callers (blast radius)" items={d.impacted} extra={(s) => `${s.depth} call${s.depth === 1 ? "" : "s"} away · via ${s.via}`}
              empty="Nothing else calls the changed code." />
            <ImpactList title="Tests to run" items={d.tests_to_run} empty="No test reaches the changed code." />
            <ImpactList title="Changed code no test reaches" items={d.untested_changes} empty="Every change is reached by a test." />
            <ImpactList title="Removed functions" items={d.symbols.removed} />
          </div>
          <div className="grid gap-6 lg:grid-cols-2">
            <Card title={`New findings (${d.findings.new.length})`}>
              {!d.findings.new.length ? <Empty>None.</Empty> : <ul className="space-y-1 text-sm">{d.findings.new.map((f, i) => (
                <li key={i}><SeverityBadge severity={f.severity} /> {f.message} <span className="font-mono text-xs text-slate-500">{f.path}:{f.line}</span></li>))}</ul>}
            </Card>
            <Card title={`Fixed findings (${d.findings.fixed.length})`}>
              {!d.findings.fixed.length ? <Empty>None.</Empty> : <ul className="space-y-1 text-sm">{d.findings.fixed.map((f, i) => (
                <li key={i}><Badge tone="green">fixed</Badge> {f.message} <span className="font-mono text-xs text-slate-500">{f.path}</span></li>))}</ul>}
            </Card>
          </div>
          <Card title="Metrics">
            <Table><thead><tr><th>Metric</th><th>Before</th><th>After</th></tr></thead>
              <tbody>{Object.entries(d.metrics).map(([k, v]) => (
                <tr key={k}><td>{k.replaceAll("_", " ")}</td><td className="tabular-nums">{fmt(v.base)}</td><td className="tabular-nums">{fmt(v.head)}</td></tr>))}</tbody>
            </Table>
          </Card>
        </>
      )}
    </div>
  );
}

const fmt = (v: unknown) => (v && typeof v === "object" ? Object.entries(v).map(([k, n]) => `${n} ${k}`).join(", ") : String(v ?? "—"));

function ImpactList({ title, items, extra, empty = "None." }: {
  title: string; items: ImpactSymbol[]; extra?: (s: ImpactSymbol) => string | null; empty?: string;
}) {
  return (
    <Card title={`${title} (${items.length})`}>
      {!items.length ? <Empty>{empty}</Empty> : (
        <ul className="space-y-1 text-sm">{items.map((s) => (
          <li key={`${s.path}:${s.qualified_name}`}>
            <SymbolLink id={s.id} name={s.qualified_name} /> <span className="font-mono text-xs text-slate-500">{s.path}:{s.start_line}</span>
            {extra?.(s) && <span className="block text-xs text-slate-500">{extra(s)}</span>}
          </li>))}
        </ul>
      )}
    </Card>
  );
}

