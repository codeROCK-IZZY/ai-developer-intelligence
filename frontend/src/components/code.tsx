import clsx from "clsx";
import { useEffect, useRef } from "react";
import { Link } from "react-router-dom";
import type { Finding, Severity } from "../api/types";
import { Badge } from "./ui";

export const SEVERITY_TONE = { high: "red", medium: "amber", low: "sky", info: "slate" } as const;
const GRADE_TONE = { A: "green", B: "green", C: "amber", D: "red", E: "red" } as const;

export function SeverityBadge({ severity }: { severity: Severity }) {
  return <Badge tone={SEVERITY_TONE[severity]}>{severity}</Badge>;
}

export function GradeBadge({ grade, health }: { grade: string; health?: number }) {
  return (
    <span title={health !== undefined ? `Health ${health}/100 (rule-based)` : undefined}>
      <Badge tone={GRADE_TONE[grade as keyof typeof GRADE_TONE] ?? "slate"}>{grade}</Badge>
    </span>
  );
}

export function FindingCounts({ counts }: { counts: Partial<Record<Severity, number>> }) {
  const order: Severity[] = ["high", "medium", "low"];
  const items = order.filter((s) => counts[s]);
  if (!items.length) return <span className="text-xs text-slate-400">—</span>;
  return (
    <span className="flex gap-1">
      {items.map((s) => <Badge key={s} tone={SEVERITY_TONE[s]}>{counts[s]} {s}</Badge>)}
    </span>
  );
}

/** Link to a file in a repository's code browser, optionally at a line. */
export function fileHref(repositoryId: string, path: string, line?: number, snapshotId?: string) {
  const q = new URLSearchParams({ tab: "files", path });
  if (line) q.set("line", String(line));
  if (snapshotId) q.set("snapshot", snapshotId);
  return `/repositories/${repositoryId}?${q.toString()}`;
}

export function SymbolLink({ id, name, className }: { id: number | null; name: string; className?: string }) {
  if (id === null) return <code className={className}>{name}</code>;
  return <Link to={`/symbols/${id}`} className={clsx("font-mono text-sky-700 hover:underline", className)}>{name}</Link>;
}

/**
 * Read-only code view with line numbers. Lines in ``highlight`` are shaded; lines with
 * findings get a coloured marker whose tooltip is the finding message.
 */
export function CodeView({ code, startLine = 1, highlight, findings = [], focusLine }: {
  code: string; startLine?: number; highlight?: [number, number]; findings?: Finding[]; focusLine?: number;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const byLine = new Map<number, Finding[]>();
  for (const f of findings) byLine.set(f.line, [...(byLine.get(f.line) ?? []), f]);
  useEffect(() => {
    if (focusLine && ref.current) {
      ref.current.querySelector(`[data-line="${focusLine}"]`)?.scrollIntoView({ block: "center" });
    }
  }, [focusLine, code]);
  const lines = code.split("\n");
  return (
    <div ref={ref} className="max-h-[70vh] overflow-auto rounded-lg border border-slate-200 bg-slate-950 text-[13px] leading-5">
      <pre className="min-w-max py-2 font-mono text-slate-100">
        {lines.map((text, i) => {
          const n = startLine + i;
          const marks = byLine.get(n);
          const hot = highlight && n >= highlight[0] && n <= highlight[1];
          const worst = marks?.sort((a, b) => sevRank(b.severity) - sevRank(a.severity))[0];
          return (
            <div key={n} data-line={n} className={clsx("flex", hot && "bg-sky-900/50", n === focusLine && "bg-amber-800/40")}>
              <span className="w-12 shrink-0 select-none pr-3 text-right text-slate-500">{n}</span>
              <span className={clsx("w-1.5 shrink-0", worst && {
                high: "bg-red-500", medium: "bg-amber-400", low: "bg-sky-400", info: "bg-slate-500",
              }[worst.severity])} title={marks?.map((m) => `${m.title}: ${m.message}`).join("\n")} />
              <code className="whitespace-pre pl-3 pr-6">{text || " "}</code>
            </div>
          );
        })}
      </pre>
    </div>
  );
}

const sevRank = (s: Severity) => ({ info: 0, low: 1, medium: 2, high: 3 })[s];

export function LanguageBar({ languages }: { languages: Record<string, { files: number; loc: number }> }) {
  const total = Object.values(languages).reduce((a, b) => a + b.loc, 0) || 1;
  const colors: Record<string, string> = {
    python: "bg-sky-600", typescript: "bg-indigo-500", tsx: "bg-indigo-300", javascript: "bg-amber-400",
    go: "bg-cyan-500", java: "bg-orange-500", text: "bg-slate-300",
  };
  const entries = Object.entries(languages).sort((a, b) => b[1].loc - a[1].loc);
  return (
    <div>
      <div className="flex h-2.5 overflow-hidden rounded-full bg-slate-100">
        {entries.map(([lang, v]) => (
          <div key={lang} className={colors[lang] ?? "bg-slate-400"} style={{ width: `${(v.loc / total) * 100}%` }} title={lang} />
        ))}
      </div>
      <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-600">
        {entries.map(([lang, v]) => (
          <li key={lang} className="flex items-center gap-1.5">
            <span className={clsx("inline-block h-2 w-2 rounded-full", colors[lang] ?? "bg-slate-400")} />
            {lang} · {v.files} files · {v.loc.toLocaleString()} lines
          </li>
        ))}
      </ul>
    </div>
  );
}
