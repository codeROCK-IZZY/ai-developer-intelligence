import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { AskResult, QuestionRow, Repository } from "../api/types";
import { fileHref, SymbolLink } from "../components/code";
import { Badge, Button, Card, Empty, ErrorBox, Input, PageHeader, Select, SourceTag } from "../components/ui";
import { ANSWER_TONE, dateTime } from "../lib/format";

export function AskPage() {
  const qc = useQueryClient();
  const repos = useQuery({ queryKey: ["repositories"], queryFn: () => api<Repository[]>("/repositories") });
  const history = useQuery({ queryKey: ["questions"], queryFn: () => api<QuestionRow[]>("/questions?limit=20") });
  const [question, setQuestion] = useState("");
  const [repo, setRepo] = useState("");
  const ask = useMutation({
    mutationFn: () => api<AskResult>("/ask", { body: { question, repository_ids: repo ? [repo] : null } }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["questions"] }),
  });
  const r = ask.data;
  return (
    <>
      <PageHeader title="Ask the codebase" subtitle="Answers cite the exact file and lines. If the code doesn't answer it, you get “not found”, never a guess." />
      <form className="mb-6 grid gap-2 md:grid-cols-[1fr_220px_auto]" onSubmit={(e: FormEvent) => { e.preventDefault(); if (question.trim().length >= 3) ask.mutate(); }}>
        <Input aria-label="Question" autoFocus placeholder="e.g. What happens to reserved stock when a payment is declined?" value={question} onChange={(e) => setQuestion(e.target.value)} />
        <Select aria-label="Repository" value={repo} onChange={(e) => setRepo(e.target.value)}>
          <option value="">All my repositories</option>
          {repos.data?.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
        </Select>
        <Button type="submit" busy={ask.isPending}>Ask</Button>
      </form>
      <ErrorBox error={ask.error} />
      {r && (
        <Card className="mb-6" title={<span className="flex items-center gap-2">Answer <Badge tone={ANSWER_TONE[r.status]}>{r.status.replace("_", " ")}</Badge></span>}
          actions={<SourceTag source={r.provider === "offline" ? "rule:offline" : r.provider} />}>
          {r.status === "answered" ? (
            <div className="space-y-2 text-sm leading-6">
              {r.sentences.map((s, i) => (
                <p key={i} className={s.supported ? "" : "text-slate-500"}>{s.text}
                  {s.citations.map((c) => <sup key={c} className="ml-0.5 text-sky-700">[{c}]</sup>)}</p>
              ))}
              {r.provider === "offline" && <p className="text-xs text-slate-500">Offline mode lists the most relevant code with its own documentation; it does not generate prose.</p>}
            </div>
          ) : <p className="text-sm text-slate-700">{r.answer}</p>}
          {r.notes.map((n) => <p key={n} className="mt-2 text-xs text-amber-700">{n}</p>)}
          {r.citations.length > 0 && (
            <ol className="mt-4 space-y-2 border-t border-slate-100 pt-3 text-sm">
              {r.citations.map((c) => (
                <li key={c.ordinal}>
                  <span className="mr-1 text-sky-700">[{c.ordinal}]</span>
                  {c.symbol && <SymbolLink id={c.symbol_id} name={c.symbol} className="mr-2" />}
                  <Link className="font-mono text-xs text-sky-700 hover:underline" to={fileHref(c.repository_id, c.path, c.start_line, c.snapshot_id)}>
                    {c.repository_name}/{c.path}:{c.start_line}-{c.end_line}</Link>
                  <blockquote className="mt-1 border-l-2 border-slate-200 pl-2 text-xs text-slate-600">{c.cited_text}</blockquote>
                </li>
              ))}
            </ol>
          )}
          <p className="mt-3 text-xs text-slate-400">{r.retrieved} code chunks considered · best relevance {r.best_relevance ?? "—"} · {r.latency_ms} ms</p>
        </Card>
      )}
      <Card title="Your recent questions">
        {!history.data?.length ? <Empty>Questions you ask appear here.</Empty> : (
          <ul className="divide-y divide-slate-100 text-sm">{history.data.map((q) => (
            <li key={q.id} className="py-2">
              <button className="text-left font-medium text-slate-800 hover:text-sky-700" onClick={() => setQuestion(q.question)}>{q.question}</button>
              <p className="text-xs text-slate-500"><Badge tone={ANSWER_TONE[q.status]}>{q.status.replace("_", " ")}</Badge> {q.citations.length} citations · {dateTime(q.created_at)}</p>
            </li>))}
          </ul>
        )}
      </Card>
    </>
  );
}
