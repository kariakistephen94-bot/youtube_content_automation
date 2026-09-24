"use client";

import { useEffect, useRef } from "react";

import type { Job } from "@/lib/types";

import { Badge, Card, ErrorNote } from "./ui";

const STATUS_COLOR = { running: "blue", succeeded: "green", failed: "red" } as const;

/** Live log of the current job - the same lines you'd see running the CLI in a terminal. */
export function JobLog({ job, logs, error }: { job: Job | null; logs: string[]; error: string | null }) {
  const bottom = useRef<HTMLDivElement>(null);
  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "nearest" });
  }, [logs.length]);

  return (
    <Card
      title={job ? <>Job: {job.kind}{job.project ? ` · ${job.project}` : ""}</> : "Job log"}
      actions={job && <Badge color={STATUS_COLOR[job.status]}>{job.status === "running" ? "running…" : job.status}</Badge>}
    >
      <ErrorNote message={error} />
      {!job && !error && <p className="text-sm text-zinc-500">Start a step and its progress appears here.</p>}
      {job && (
        <div className="mb-3">
          <div className="mb-1 flex justify-between text-xs text-zinc-400">
            <span>{job.progress_label ?? (job.status === "running" ? "working…" : job.status)}</span>
            <span>{job.progress != null ? `${Math.round(job.progress * 100)}%` : job.status === "running" ? "…" : ""}</span>
          </div>
          <div className="h-1.5 w-full overflow-hidden rounded bg-zinc-800">
            <div
              className={`h-full rounded transition-all ${job.status === "failed" ? "bg-red-500" : job.status === "succeeded" ? "bg-emerald-500" : "bg-sky-500"} ${job.progress == null && job.status === "running" ? "w-1/3 animate-pulse" : ""}`}
              style={job.progress != null ? { width: `${Math.max(2, job.progress * 100)}%` } : undefined}
            />
          </div>
        </div>
      )}
      {job && (
        <div className="max-h-72 overflow-y-auto rounded-lg bg-zinc-950 p-3 font-mono text-xs leading-relaxed text-zinc-300">
          {logs.length === 0 && <div className="text-zinc-500">Starting…</div>}
          {logs.map((line, i) => (
            <div key={i} className={line.includes("ERROR") ? "text-red-300" : line.includes("WARNING") ? "text-amber-300" : ""}>
              {line}
            </div>
          ))}
          <div ref={bottom} />
        </div>
      )}
      {job?.status === "failed" && job.error && <div className="mt-3"><ErrorNote message={job.error} /></div>}
    </Card>
  );
}
