import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { Repository, Snapshot, Workspace } from "../api/types";
import { FindingCounts, GradeBadge } from "../components/code";
import { Badge, Button, Card, Empty, ErrorBox, Field, Input, Loading, PageHeader, Select } from "../components/ui";
import { dateTime, STATUS_TONE } from "../lib/format";

const busy = (s: Snapshot | null) => s?.status === "queued" || s?.status === "processing";

export function RepositoriesPage() {
  const [showNew, setShowNew] = useState(false);
  const repos = useQuery({
    queryKey: ["repositories"],
    queryFn: () => api<Repository[]>("/repositories"),
    refetchInterval: (q) => (q.state.data?.some((r) => busy(r.latest_snapshot) || r.snapshots === 0) ? 3000 : false),
  });
  const workspaces = useQuery({ queryKey: ["workspaces"], queryFn: () => api<Workspace[]>("/workspaces") });
  const canCreate = workspaces.data?.some((w) => w.role !== "reader");
  return (
    <>
      <PageHeader
        title="Repositories"
        subtitle="Import a codebase (ZIP or public GitHub) to search it, ask questions, and review its structure, quality and security."
        actions={canCreate && <Button onClick={() => setShowNew((v) => !v)}>{showNew ? "Close" : "New repository"}</Button>}
      />
      {showNew && workspaces.data && <NewRepository workspaces={workspaces.data} onDone={() => setShowNew(false)} />}
      {repos.isLoading ? <Loading /> : repos.error ? <ErrorBox error={repos.error} /> : !repos.data?.length ? (
        <Empty>No repositories yet. {canCreate ? "Create one to get started." : "Ask a workspace owner to add you."}</Empty>
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          {repos.data.map((r) => <RepositoryCard key={r.id} repo={r} />)}
        </div>
      )}
    </>
  );
}

function NewRepository({ workspaces, onDone }: { workspaces: Workspace[]; onDone: () => void }) {
  const qc = useQueryClient();
  const editable = workspaces.filter((w) => w.role !== "reader");
  const [workspace, setWorkspace] = useState(editable[0]?.id ?? "");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [githubUrl, setGithubUrl] = useState("");
  const [ref, setRef] = useState("");
  const create = useMutation({
    mutationFn: async () => {
      const repo = await api<Repository>(`/workspaces/${workspace}/repositories`, {
        body: { name, description, github_url: githubUrl || null, default_ref: ref || null },
      });
      if (githubUrl) await api(`/repositories/${repo.id}/snapshots:github`, { body: { ref: ref || null } });
      return repo;
    },
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ["repositories"] }); onDone(); },
  });
  return (
    <Card title="New repository" className="mb-6">
      <form className="grid gap-3 md:grid-cols-2" onSubmit={(e: FormEvent) => { e.preventDefault(); create.mutate(); }}>
        <Field label="Workspace">
          <Select value={workspace} onChange={(e) => setWorkspace(e.target.value)}>
            {editable.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
          </Select>
        </Field>
        <Field label="Name"><Input value={name} onChange={(e) => setName(e.target.value)} placeholder="payments-service" /></Field>
        <Field label="Description"><Input value={description} onChange={(e) => setDescription(e.target.value)} /></Field>
        <Field label="Public GitHub URL (optional)" hint="Leave empty to upload a ZIP instead.">
          <Input value={githubUrl} onChange={(e) => setGithubUrl(e.target.value)} placeholder="https://github.com/owner/repo" />
        </Field>
        {githubUrl && (
          <Field label="Branch, tag or commit" hint="Default branch when empty.">
            <Input value={ref} onChange={(e) => setRef(e.target.value)} placeholder="main" />
          </Field>
        )}
        <div className="flex items-end"><Button type="submit" disabled={!name.trim() || !workspace} busy={create.isPending}>Create{githubUrl ? " and import" : ""}</Button></div>
      </form>
      <ErrorBox error={create.error} />
    </Card>
  );
}

function RepositoryCard({ repo }: { repo: Repository }) {
  const qc = useQueryClient();
  const fileInput = useRef<HTMLInputElement>(null);
  const snap = repo.latest_snapshot;
  const s = snap?.stats;
  const canEdit = repo.role !== "reader";
  const upload = useMutation({
    mutationFn: (file: File) => {
      const form = new FormData();
      form.append("file", file);
      form.append("label", file.name.replace(/\.zip$/i, ""));
      return api<Snapshot>(`/repositories/${repo.id}/snapshots:upload`, { form });
    },
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["repositories"] }),
  });
  const pull = useMutation({
    mutationFn: () => api<Snapshot>(`/repositories/${repo.id}/snapshots:github`, { body: {} }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["repositories"] }),
  });
  const pending = useQuery({
    queryKey: ["snapshots", repo.id],
    queryFn: () => api<Snapshot[]>(`/repositories/${repo.id}/snapshots`),
    refetchInterval: (q) => (q.state.data?.some(busy) ? 2500 : false),
  });
  const inFlight = pending.data?.find(busy);
  const lastFailed = pending.data?.[0]?.status === "failed" ? pending.data[0] : null;
  return (
    <Card
      title={<Link to={`/repositories/${repo.id}`} className="text-sky-800 hover:underline">{repo.name}</Link>}
      actions={<Badge tone="violet">{repo.workspace_name}</Badge>}
    >
      {repo.description && <p className="mb-3 text-sm text-slate-600">{repo.description}</p>}
      {snap && s ? (
        <div className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
          <div><p className="text-xs text-slate-500">Health</p><GradeBadge grade={s.grade ?? "?"} health={s.health} /></div>
          <div><p className="text-xs text-slate-500">Files</p><p className="tabular-nums">{s.files} · {s.loc?.toLocaleString()} lines</p></div>
          <div><p className="text-xs text-slate-500">Functions</p><p className="tabular-nums">{s.functions}</p></div>
          <div><p className="text-xs text-slate-500">Findings</p><FindingCounts counts={s.findings ?? {}} /></div>
        </div>
      ) : !inFlight && <Empty>No snapshot yet.</Empty>}
      <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-slate-500">
        {snap && <span>Latest: <strong className="text-slate-700">{snap.label}</strong> · {dateTime(snap.processed_at)}</span>}
        {inFlight && <Badge tone={STATUS_TONE[inFlight.status]}>{inFlight.label}: {inFlight.stage ?? inFlight.status}…</Badge>}
        {lastFailed && <Badge tone="red" >{lastFailed.label} failed: {lastFailed.error}</Badge>}
      </div>
      {canEdit && (
        <div className="mt-3 flex gap-2">
          <input ref={fileInput} type="file" accept=".zip" className="hidden" aria-label="Upload ZIP"
            onChange={(e) => { const f = e.target.files?.[0]; if (f) upload.mutate(f); e.target.value = ""; }} />
          <Button variant="secondary" busy={upload.isPending} onClick={() => fileInput.current?.click()}>Upload ZIP</Button>
          {repo.source_url && <Button variant="secondary" busy={pull.isPending} onClick={() => pull.mutate()}>Import latest from GitHub</Button>}
        </div>
      )}
      <ErrorBox error={upload.error ?? pull.error} />
    </Card>
  );
}
