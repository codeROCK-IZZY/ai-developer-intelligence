import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { Explanation, Repository, SymbolDetail, SymbolRef } from "../api/types";
import { CodeView, fileHref, SeverityBadge, SymbolLink } from "../components/code";
import { Badge, Button, Card, Empty, ErrorBox, Loading, PageHeader, SourceTag, Stat } from "../components/ui";

export function SymbolPage() {
  const { id = "" } = useParams();
  const q = useQuery({ queryKey: ["symbol", id], queryFn: () => api<SymbolDetail>(`/symbols/${id}`) });
  const snapshot = q.data?.snapshot_id;
  const repos = useQuery({ queryKey: ["repositories"], queryFn: () => api<Repository[]>("/repositories"), enabled: !!snapshot });
  const [explainOn, setExplainOn] = useState(false);
  const explain = useQuery({
    queryKey: ["explain", id],
    queryFn: () => api<Explanation>(`/symbols/${id}/explain`),
    enabled: explainOn,
  });
  if (q.isLoading) return <Loading />;
  if (!q.data) return <ErrorBox error={q.error} />;
  const { symbol: s } = q.data;
  const repo = repos.data?.find((r) => r.latest_snapshot?.id === snapshot) ?? null;
  return (
    <>
      <PageHeader
        title={s.qualified_name}
        subtitle={<>
          <Badge>{s.kind}</Badge>{" "}
          {repo ? <Link className="font-mono text-sky-700 hover:underline" to={fileHref(repo.id, s.path, s.start_line, snapshot)}>{s.path}:{s.start_line}</Link>
            : <span className="font-mono">{s.path}:{s.start_line}</span>}
          {s.decorators.map((d) => <code key={d} className="ml-2 text-xs text-slate-500">@{d}</code>)}
        </>}
      />
      {s.docstring && <p className="mb-4 max-w-3xl text-sm text-slate-700">{s.docstring}</p>}
      <div className="mb-6 grid grid-cols-2 gap-4 lg:grid-cols-5">
        <Stat label="Complexity" value={s.complexity} sub={s.complexity > 10 ? "above 10: consider splitting" : "cyclomatic"} />
        <Stat label="Lines" value={s.loc} sub={`nesting depth ${s.max_nesting}`} />
        <Stat label="Called by" value={s.fan_in} sub="resolved call sites in this repository" />
        <Stat label="Calls" value={s.fan_out} sub="functions in this repository" />
        <Stat label="Tests" value={s.tested ? "called by a test" : "no direct test"} />
      </div>
      <div className="grid gap-6 xl:grid-cols-[1fr_380px]">
        <div className="min-w-0 space-y-4">
          <CodeView code={q.data.code} startLine={s.start_line} findings={q.data.findings} />
          <Card title="Explain this code" actions={!explainOn && <Button variant="secondary" onClick={() => setExplainOn(true)}>Explain</Button>}>
            {!explainOn ? <p className="text-sm text-slate-500">A plain-language summary of what this code does and how it fits in.</p>
              : explain.isLoading ? <Loading /> : explain.data && (
                <div className="space-y-2 text-sm">
                  <SourceTag source={explain.data.generated ? explain.data.provider : "rule:facts"} />
                  {explain.data.generated
                    ? <p className="whitespace-pre-wrap text-slate-800">{explain.data.text}</p>
                    : <ul className="list-disc space-y-1 pl-5 text-slate-700">{explain.data.facts.map((f) => <li key={f}>{f}</li>)}</ul>}
                  {!explain.data.generated && <p className="text-xs text-slate-500">Offline mode: assembled from what the analysis knows. Configure Claude for a generated explanation.</p>}
                </div>
              )}
            <ErrorBox error={explain.error} />
          </Card>
        </div>
        <div className="space-y-4">
          <RefList title="Called by" items={q.data.callers} empty="No resolved callers (entry point, dynamic call, or unused)." />
          <RefList title="Calls" items={q.data.callees} empty="No calls into this repository." />
          {q.data.external_calls.length > 0 && (
            <Card title="Library and unresolved calls">
              <div className="flex flex-wrap gap-1">{q.data.external_calls.map((e) => <code key={e.name} className="rounded bg-slate-100 px-1.5 text-xs">{e.name}</code>)}</div>
            </Card>
          )}
          {q.data.members.length > 0 && <Card title="Members"><ul className="space-y-1 text-sm">{q.data.members.map((m) => <li key={m.id}><SymbolLink id={m.id} name={m.qualified_name} /></li>)}</ul></Card>}
          {q.data.findings.length > 0 && (
            <Card title="Findings"><ul className="space-y-2 text-sm">{q.data.findings.map((f) => (
              <li key={f.id}><SeverityBadge severity={f.severity} /> <span className="font-medium">{f.title}</span><p className="text-xs text-slate-600">{f.message}</p><p className="text-xs text-slate-500">{f.fix}</p></li>))}</ul></Card>
          )}
          {q.data.duplicates.length > 0 && (
            <Card title="Near-duplicates"><ul className="space-y-1 text-sm">{q.data.duplicates.map((d) => (
              <li key={d.id}><SymbolLink id={d.id} name={d.qualified_name} /> <span className="text-xs text-slate-500">{d.path} · {Math.round(d.similarity * 100)}%</span></li>))}</ul></Card>
          )}
        </div>
      </div>
    </>
  );
}

function RefList({ title, items, empty }: { title: string; items: SymbolRef[]; empty: string }) {
  return (
    <Card title={`${title} (${items.length})`}>
      {!items.length ? <Empty>{empty}</Empty> : (
        <ul className="space-y-1 text-sm">{items.map((r) => (
          <li key={r.id}><SymbolLink id={r.id} name={r.qualified_name} />
            <span className="block text-xs text-slate-500">{r.path} · line {r.line}{r.calls > 1 ? ` · ${r.calls} calls` : ""}</span></li>))}
        </ul>
      )}
    </Card>
  );
}
