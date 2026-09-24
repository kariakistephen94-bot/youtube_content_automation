"use client";

import { useState } from "react";

import type { RecordOptions } from "@/lib/api";
import type { Audience, Job, ProjectDetail } from "@/lib/types";

import { NarrationView } from "./narration-view";
import { PipelineView } from "./pipeline-view";
import { RecordView } from "./record-view";
import { RehearsalView } from "./rehearsal-view";
import { ScriptView } from "./script-view";
import { TimelineView } from "./timeline-view";
import { Badge, Button, Card, inputClass } from "./ui";

type Tab = "overview" | "plan" | "script" | "narration" | "timeline" | "rehearsal" | "record";

export function ProjectView({ project, job, logs, busy, onPlan, onScript, onNarrate, onTimeline, onRehearse, onRecord, onBuild, onBuildSandbox }: {
  project: ProjectDetail;
  job: Job | null;
  logs: string[];
  busy: boolean;
  onPlan: (body: { title?: string; audience: Audience; duration_minutes: number; notes?: string }) => void;
  onScript: (opts: { restart?: boolean; from_section?: number | null; fix_from_rehearsal?: boolean }) => void;
  onNarrate: (opts: { provider: string; voice?: string }) => void;
  onTimeline: (speed: string) => void;
  onRehearse: (sections?: number[]) => void;
  onRecord: (opts: RecordOptions) => void;
  onBuild: () => void;
  onBuildSandbox: () => void;
}) {
  const { analysis, plan, script, summary } = project;
  const [tab, setTab] = useState<Tab>(script ? "script" : plan ? "plan" : "overview");

  const tabs: { id: Tab; label: string; disabled?: boolean }[] = [
    { id: "overview", label: "Analysis" },
    { id: "plan", label: "Plan", disabled: !analysis },
    { id: "script", label: `Script${script ? ` (${script.sections.length}/${plan?.sections.length ?? "?"})` : ""}`, disabled: !plan },
    { id: "rehearsal", label: `Rehearsal${project.rehearsal ? (project.rehearsal.ok ? " ✓" : " ✗") : ""}`, disabled: !script },
    { id: "narration", label: `Narration${project.narration ? ` (${project.narration.entries.length})` : ""}`, disabled: !script },
    { id: "timeline", label: "Timeline", disabled: !project.narration },
    { id: "record", label: `Record${project.recordings ? ` (${project.recordings.results.filter((r) => r.status === "SUCCESS").length})` : ""}`, disabled: !project.timeline },
  ];

  return (
    <Card
      title={plan?.title ?? analysis?.repository_name ?? summary.name}
      actions={
        <div className="flex rounded-lg bg-zinc-950 p-0.5 text-xs">
          {tabs.map((t) => (
            <button key={t.id} disabled={t.disabled} onClick={() => setTab(t.id)}
              className={`rounded-md px-2.5 py-1 disabled:text-zinc-700 ${tab === t.id ? "bg-zinc-800 text-zinc-100" : "text-zinc-400"}`}>
              {t.label}
            </button>
          ))}
        </div>
      }
    >
      <div className="mb-4">
        <PipelineView project={project} job={job} logs={logs} onOpen={setTab} />
      </div>
      {tab === "overview" && analysis && <Overview project={project} />}
      {tab === "plan" && <PlanTab project={project} busy={busy} onPlan={onPlan} />}
      {tab === "rehearsal" && <RehearsalView project={project} busy={busy} onRehearse={onRehearse} onFix={() => onScript({ fix_from_rehearsal: true })} />}
      {tab === "narration" && <NarrationView project={project} busy={busy} onNarrate={onNarrate} />}
      {tab === "timeline" && <TimelineView project={project} busy={busy} onBuild={onTimeline} />}
      {tab === "record" && <RecordView project={project} job={job} busy={busy} onRecord={onRecord} onBuild={onBuild} onBuildSandbox={onBuildSandbox} />}
      {tab === "script" && plan && (
        <div className="flex flex-col gap-3">
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="primary" disabled={busy} onClick={() => onScript({})}>
              {script && script.sections.length > 0 && script.sections.length < plan.sections.length
                ? `Continue from section ${script.sections.length + 1}`
                : script ? "Check for missing sections" : "Write script"}
            </Button>
            {script && (
              <Button disabled={busy} onClick={() => {
                if (confirm("Rewrite every section? This uses AI quota and replaces the current script.")) onScript({ restart: true });
              }}>
                Rewrite all
              </Button>
            )}
            <span className="text-xs text-zinc-500">One AI call per section. Progress is saved after each section.</span>
          </div>
          {script ? <ScriptView script={script} typedCode={project.typed_code} /> :
            <p className="text-sm text-zinc-500">No script yet.</p>}
        </div>
      )}
    </Card>
  );
}

function Overview({ project }: { project: ProjectDetail }) {
  const a = project.analysis!;
  return (
    <div className="flex flex-col gap-4 text-sm">
      <p className="text-zinc-300">{a.description}</p>
      <div className="flex flex-wrap gap-2">
        <Badge color="blue">{a.primary_language}</Badge>
        {a.frameworks.map((f) => <Badge key={f}>{f}</Badge>)}
        <Badge color="purple">{a.difficulty}</Badge>
        <span className="text-xs text-zinc-500">{a.source.origin}</span>
      </div>
      <div>
        <h3 className="mb-1 font-medium text-zinc-200">Suggested teaching order</h3>
        <ol className="list-decimal pl-5 text-zinc-300">
          {a.potential_teaching_order.map((s) => <li key={s.title}>{s.title} <span className="text-zinc-500">— {s.goal}</span></li>)}
        </ol>
      </div>
      <div>
        <h3 className="mb-1 font-medium text-zinc-200">Run</h3>
        {a.run_commands.map((c) => <code key={c.command} className="block font-mono text-xs text-zinc-300">{c.command}</code>)}
      </div>
      {a.validation_warnings.length > 0 && (
        <div className="text-amber-300">
          <h3 className="mb-1 font-medium">Checks against the real repository</h3>
          <ul className="list-disc pl-5">{a.validation_warnings.map((w) => <li key={w}>{w}</li>)}</ul>
        </div>
      )}
      <details>
        <summary className="cursor-pointer text-zinc-400">File tree</summary>
        <pre className="mt-2 max-h-80 overflow-auto rounded-lg bg-zinc-950 p-3 font-mono text-xs text-zinc-400">{a.project_structure}</pre>
      </details>
    </div>
  );
}

function PlanTab({ project, busy, onPlan }: {
  project: ProjectDetail;
  busy: boolean;
  onPlan: (body: { title?: string; audience: Audience; duration_minutes: number; notes?: string }) => void;
}) {
  const plan = project.plan;
  const [title, setTitle] = useState(plan?.title ?? "");
  const [audience, setAudience] = useState<Audience>(plan?.audience ?? "beginner");
  const [duration, setDuration] = useState(plan ? Math.round(plan.estimated_duration_seconds / 60) : 10);
  const [notes, setNotes] = useState("");

  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-1 gap-2 md:grid-cols-[2fr_1fr_1fr]">
        <input className={inputClass} placeholder="Video title (optional)" value={title} onChange={(e) => setTitle(e.target.value)} />
        <select className={inputClass} value={audience} onChange={(e) => setAudience(e.target.value as Audience)}>
          <option value="beginner">Beginner</option>
          <option value="intermediate">Intermediate</option>
          <option value="advanced">Advanced</option>
        </select>
        <label className="flex items-center gap-2 text-sm text-zinc-400">
          <input className={inputClass} type="number" min={1} max={180} value={duration}
            onChange={(e) => setDuration(Number(e.target.value))} /> min
        </label>
      </div>
      <input className={inputClass} placeholder="Notes for the planner (optional), e.g. 'skip the tests section'"
        value={notes} onChange={(e) => setNotes(e.target.value)} />
      <div className="flex items-center gap-2">
        <Button variant="primary" disabled={busy} onClick={() =>
          onPlan({ title: title || undefined, audience, duration_minutes: duration, notes: notes || undefined })}>
          {plan ? "Re-plan" : "Create plan"}
        </Button>
        {plan && <span className="text-xs text-zinc-500">Re-planning means the script must be rewritten.</span>}
      </div>

      {plan && (
        <div className="flex flex-col gap-3 text-sm">
          <p className="text-zinc-300">{plan.summary}</p>
          <ol className="flex flex-col gap-2">
            {plan.sections.map((s) => (
              <li key={s.section_number} className="rounded-lg border border-zinc-800 px-3 py-2">
                <div className="flex justify-between gap-2">
                  <span className="font-medium text-zinc-100">{s.section_number}. {s.title}</span>
                  <span className="text-xs text-zinc-500">{(s.estimated_duration_seconds / 60).toFixed(1)} min</span>
                </div>
                <p className="text-zinc-400">{s.expected_result}</p>
              </li>
            ))}
          </ol>
          {plan.simplifications.length > 0 && (
            <div>
              <h3 className="mb-1 font-medium text-zinc-200">Simplifications</h3>
              <ul className="list-disc pl-5 text-zinc-400">{plan.simplifications.map((s) => <li key={s}>{s}</li>)}</ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
