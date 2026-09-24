"use client";

import type { ProjectDetail } from "@/lib/types";

import { Badge, Button } from "./ui";

/** Results of running the script's code off camera: what worked, what broke, and where. */
export function RehearsalView({ project, busy, onRehearse, onFix }: {
  project: ProjectDetail;
  busy: boolean;
  onRehearse: (sections?: number[]) => void;
  onFix: () => void;
}) {
  const report = project.rehearsal;
  const failures = report ? report.sections.flatMap((s) => s.checks.filter((c) => !c.ok).map((c) => ({ section: s.section_number, ...c })))
    .concat(report.final_tests.filter((c) => !c.ok).map((c) => ({ section: 0, ...c }))) : [];
  const environmentOnly = failures.length > 0 && failures.every((f) => f.detail.includes("already in use"));

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <Button variant="primary" disabled={busy || !project.script} onClick={() => onRehearse()}>
          {report ? "Rehearse again" : "Rehearse the script"}
        </Button>
        <span className="text-xs text-zinc-500">
          Writes the files, runs the commands and starts the servers in a throwaway folder. No recording, no AI quota.
        </span>
        {report && (
          <Badge color={report.ok ? "green" : "red"}>{report.ok ? "passed" : `${failures.length} failing`}</Badge>
        )}
      </div>

      {report && failures.length > 0 && (
        <div className="rounded-lg border border-red-900 bg-red-950/40 p-3 text-sm">
          <p className="mb-2 font-medium text-red-200">Fix these before recording:</p>
          <ul className="flex flex-col gap-2">
            {failures.map((f, i) => (
              <li key={i} className="text-red-100">
                <span className="font-mono text-xs text-red-300">{f.section ? `section ${f.section} · ` : "final tests · "}{f.beat_id} · {f.kind}</span>
                <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap rounded bg-zinc-950 p-2 font-mono text-xs text-zinc-300">{f.detail}</pre>
              </li>
            ))}
          </ul>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            {!environmentOnly && (
              <Button variant="primary" disabled={busy} onClick={onFix}>Fix with AI (rewrite failing sections)</Button>
            )}
            <span className="text-xs text-zinc-400">
              {environmentOnly
                ? "These are environment problems (a busy port), not script problems: free the port and rehearse again."
                : "Sends these failures back to the AI, rewrites from the first failing section onward, then rehearse again."}
            </span>
          </div>
        </div>
      )}

      {report?.sections.map((section) => (
        <div key={section.section_number} className="rounded-lg border border-zinc-800">
          <div className="flex items-center justify-between px-3 py-2">
            <span className="text-sm font-medium text-zinc-100">{section.section_number}. {section.title}</span>
            <span className="flex items-center gap-2 text-xs text-zinc-500">
              {section.seconds.toFixed(0)}s
              <Badge color={section.ok ? "green" : "red"}>{section.ok ? "ok" : "failed"}</Badge>
            </span>
          </div>
          <ul className="divide-y divide-zinc-800 border-t border-zinc-800">
            {section.checks.map((check, i) => (
              <li key={i} className="flex items-start gap-2 px-3 py-1.5 text-xs">
                <span className={check.ok ? "text-emerald-400" : "text-red-400"}>{check.ok ? "✓" : "✗"}</span>
                <span className="w-24 shrink-0 font-mono text-zinc-500">{check.beat_id}</span>
                <span className="w-24 shrink-0 text-zinc-400">{check.kind}</span>
                <span className={`truncate ${check.ok ? "text-zinc-300" : "text-red-200"}`}>{check.detail.split("\n")[0]}</span>
              </li>
            ))}
          </ul>
        </div>
      ))}
      {report && report.final_tests.length > 0 && (
        <div className="rounded-lg border border-zinc-800 px-3 py-2 text-xs">
          <span className="font-medium text-zinc-100">Final tests</span>
          {report.final_tests.map((c, i) => (
            <div key={i} className={c.ok ? "text-emerald-300" : "text-red-200"}>{c.ok ? "✓" : "✗"} {c.detail.split("\n")[0]}</div>
          ))}
        </div>
      )}
    </div>
  );
}
