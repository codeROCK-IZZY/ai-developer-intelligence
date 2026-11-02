import { useMutation, useQuery } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { Repository, SearchResponse } from "../api/types";
import { fileHref, SymbolLink } from "../components/code";
import { Badge, Button, Card, Empty, ErrorBox, Input, PageHeader, Select } from "../components/ui";
import { pct } from "../lib/format";

export function SearchPage() {
  const repos = useQuery({ queryKey: ["repositories"], queryFn: () => api<Repository[]>("/repositories") });
  const [query, setQuery] = useState("");
  const [repo, setRepo] = useState("");
  const [language, setLanguage] = useState("");
  const [mode, setMode] = useState("hybrid");
  const [rerank, setRerank] = useState(false);
  const [tests, setTests] = useState(true);
  const search = useMutation({
    mutationFn: () => api<SearchResponse>("/search", {
      body: {
        query, mode, rerank, include_tests: tests, language: language || null,
        repository_ids: repo ? [repo] : null, limit: 20,
      },
    }),
  });
  return (
    <>
      <PageHeader title="Code search" subtitle="Search by meaning or by name across every repository you can read: “where do we retry payments”, “hash_password”." />
      <form className="mb-4 grid gap-2 lg:grid-cols-[1fr_auto]" onSubmit={(e: FormEvent) => { e.preventDefault(); if (query.trim()) search.mutate(); }}>
        <Input aria-label="Search query" autoFocus placeholder="Describe what the code does, or type a name…" value={query} onChange={(e) => setQuery(e.target.value)} />
        <Button type="submit" busy={search.isPending}>Search</Button>
        <div className="flex flex-wrap items-center gap-2 text-sm lg:col-span-2">
          <Select aria-label="Repository" value={repo} onChange={(e) => setRepo(e.target.value)}>
            <option value="">All repositories</option>
            {repos.data?.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
          </Select>
          <Select aria-label="Language" value={language} onChange={(e) => setLanguage(e.target.value)}>
            <option value="">Any language</option>
            {["python", "typescript", "tsx", "javascript", "go", "java", "text"].map((l) => <option key={l} value={l}>{l}</option>)}
          </Select>
          <Select aria-label="Mode" value={mode} onChange={(e) => setMode(e.target.value)}>
            <option value="hybrid">Hybrid</option><option value="vector">Semantic only</option><option value="keyword">Keyword only</option>
          </Select>
          <label className="flex items-center gap-1"><input type="checkbox" checked={rerank} onChange={(e) => setRerank(e.target.checked)} /> Re-rank (slower)</label>
          <label className="flex items-center gap-1"><input type="checkbox" checked={tests} onChange={(e) => setTests(e.target.checked)} /> Include tests</label>
        </div>
      </form>
      <ErrorBox error={search.error} />
      {search.data && (
        <>
          <p className="mb-3 text-xs text-slate-500">{search.data.results.length} results · {search.data.latency_ms} ms{search.data.reranked && " · re-ranked"}</p>
          {!search.data.results.length ? <Empty>No matching code.</Empty> : (
            <div className="space-y-3">
              {search.data.results.map((h) => (
                <Card key={h.chunk_id}>
                  <div className="mb-2 flex flex-wrap items-center gap-2 text-sm">
                    <Badge tone="violet">{h.repository_name}</Badge>
                    {h.symbol ? <SymbolLink id={h.symbol_id} name={h.symbol} /> : <span className="text-slate-500">module code</span>}
                    <Link className="font-mono text-xs text-sky-700 hover:underline" to={fileHref(h.repository_id, h.path, h.start_line, h.snapshot_id)}>
                      {h.path}:{h.start_line}-{h.end_line}</Link>
                    <Badge>{h.language}</Badge>{h.is_test && <Badge tone="sky">test</Badge>}
                    <span className="ml-auto text-xs text-slate-500">
                      {h.rerank_score !== null ? `relevance ${pct(h.rerank_score)}` : h.vector_similarity !== null ? `similarity ${h.vector_similarity.toFixed(2)}` : "keyword match"}
                    </span>
                  </div>
                  <pre className="max-h-48 overflow-auto rounded bg-slate-950 p-3 text-xs leading-5 text-slate-100">{h.text}</pre>
                </Card>
              ))}
            </div>
          )}
        </>
      )}
    </>
  );
}
