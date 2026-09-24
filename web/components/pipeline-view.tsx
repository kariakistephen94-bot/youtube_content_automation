"use client";

import type { Job, ProjectDetail } from "@/lib/types";

type StageState = "done" | "partial" | "running" | "todo" | "not_built";

interface Stage {
  id: string;
  label: string;
  state: StageState;
  detail: string;
  tab?: "overview" | "plan" | "script" | "narration" | "timeline" | "rehearsal" | "record";
}

const JOB_STAGE: Record<string, string> = { analyze: "analysis", plan: "plan", script: "script", narrate: "narration", timeline: "timeline", rehearse: "rehearsal", record: "recording", build: "video" };

const STYLE: Record<StageState, { dot: string; ring: string; text: string; label: string }> = {
  done: { dot: "bg-emerald-400", ring: "border-emerald-800", text: "text-emerald-300", label: "done" },
  partial: { dot: "bg-amber-400", ring: "border-amber-800", text: "text-amber-300", label: "partial" },
  running: { dot: "bg-sky-400 animate-pulse", ring: "border-sky-600", text: "text-sky-300", label: "running" },
  todo: { dot: "bg-zinc-500", ring: "border-zinc-700", text: "text-zinc-400", label: "not started" },
  not_built: { dot: "bg-transparent border border-dashed border-zinc-600", ring: "border-dashed border-zinc-800", text: "text-zinc-600", label: "not built yet" },
};

/** Every stage from repository to final MP4, with what is done, running, or still to be built. */
export function PipelineView({ project, job, logs, onOpen }: {
  project: ProjectDetail;
  job: Job | null;
  logs: string[];
  onOpen: (tab: "overview" | "plan" | "script" | "narration" | "timeline" | "rehearsal" | "record") => void;
}) {
  const stages = buildStages(project, job, logs);
  return (
    <div className="overflow-x-auto pb-1">
      <ol className="flex min-w-max items-stretch gap-1.5">
        {stages.map((stage, i) => {
          const s = STYLE[stage.state];
          const clickable = stage.tab && stage.state !== "not_built" && stage.state !== "todo";
          return (
            <li key={stage.id} className="flex items-center gap-1.5">
              <button
                disabled={!clickable}
                onClick={() => stage.tab && onOpen(stage.tab)}
                title={s.label}
                className={`flex w-36 flex-col gap-1 rounded-lg border bg-zinc-950/60 px-2.5 py-2 text-left ${s.ring} ${clickable ? "hover:bg-zinc-800" : "cursor-default"}`}
              >
                <span className="flex items-center gap-1.5">
                  <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${s.dot}`} />
                  <span className={`text-xs font-medium ${stage.state === "not_built" ? "text-zinc-500" : "text-zinc-100"}`}>
                    {stage.label}
                  </span>
                </span>
                <span className={`text-[11px] leading-tight ${s.text}`}>{stage.detail}</span>
              </button>
              {i < stages.length - 1 && <span className="text-zinc-700">→</span>}
            </li>
          );
        })}
      </ol>
    </div>
  );
}

function buildStages(project: ProjectDetail, job: Job | null, logs: string[]): Stage[] {
  const { analysis, plan, script, scenes, summary } = project;
  const runningStage =
    job?.status === "running" && (job.project === summary.name || job.project === null)
      ? JOB_STAGE[job.kind]
      : undefined;
  const planned = plan?.sections.length ?? 0;
  const written = script?.sections.length ?? 0;
  const sectionProgress = [...logs].reverse().map((l) => l.match(/Writing section (\d+)\/(\d+)/)).find(Boolean);

  const percent = job?.progress != null ? ` ${Math.round(job.progress * 100)}%` : "";
  const stage = (id: string, label: string, state: StageState, detail: string, tab?: Stage["tab"]): Stage =>
    runningStage === id
      ? { id, label, state: "running", tab, detail: (job?.progress_label ?? (id === "script" && sectionProgress ? `writing section ${sectionProgress[1]}/${sectionProgress[2]}` : "working…")) + percent }
      : { id, label, state, detail, tab };

  const minutes = (seconds: number) => `~${(seconds / 60).toFixed(1)} min`;
  const totalSegments = scenes?.scenes.reduce((t, s) => t + (s.narration_segments?.length ?? 0), 0) ?? 0;
  const recordedCount = project.recordings?.results.filter((r) => r.status === "SUCCESS").length ?? 0;
  const failedScene = project.recordings?.results.find((r) => r.status !== "SUCCESS")?.scene_id;

  return [
    stage("source", "Repository", analysis ? "done" : "todo",
      analysis ? (analysis.source.kind === "github" ? "GitHub clone" : "local folder") : "choose a repo"),
    stage("analysis", "Analysis", analysis ? "done" : "todo",
      analysis ? `${analysis.primary_language} · ${analysis.frameworks.slice(0, 2).join(", ") || "-"}` : "not started", "overview"),
    stage("plan", "Teaching plan", plan ? "done" : "todo",
      plan ? `${planned} sections · ${minutes(plan.estimated_duration_seconds)}` : "not started", "plan"),
    stage("script", "Script", written === 0 ? "todo" : written >= planned ? "done" : "partial",
      written === 0 ? "not started" : `${written}/${planned} sections${script?.warnings.length ? ` · ${script.warnings.length} warnings` : ""}`, "script"),
    stage("scenes", "Scenes", scenes?.scenes.length ? (written >= planned ? "done" : "partial") : "todo",
      scenes?.scenes.length
        ? `${scenes.scenes.length} scenes · ${minutes(scenes.scenes.reduce((t, s) => t + s.estimated_duration_seconds, 0))}`
        : "built with the script", "script"),
    stage("rehearsal", "Rehearsal", !project.rehearsal ? "todo" : project.rehearsal.ok ? "done" : "partial",
      !project.rehearsal ? "run each section's code"
        : project.rehearsal.ok ? "all checks passed"
        : `${project.rehearsal.sections.flatMap((s) => s.checks).filter((c) => !c.ok).length} failing checks`, "rehearsal"),
    stage("narration", "Narration audio",
      !project.narration ? "todo" : project.narration.entries.length >= totalSegments ? "done" : "partial",
      project.narration
        ? `${project.narration.entries.length} clips · ${minutes(project.narration.entries.reduce((t, e) => t + e.duration_seconds, 0))}`
        : "text-to-speech per segment", "narration"),
    stage("timeline", "Timeline", project.timeline ? "done" : "todo",
      project.timeline
        ? `${project.timeline.scenes.length} scenes · ${minutes(project.timeline.scenes.reduce((t, s) => t + s.duration_seconds, 0))}`
        : "sync voice and actions", "timeline"),
    stage("recording", "Recording",
      recordedCount === 0 ? "todo" : recordedCount >= (scenes?.scenes.length ?? 0) ? "done" : "partial",
      recordedCount === 0 ? "screen + narration per scene" : `${recordedCount}/${scenes?.scenes.length ?? 0} scenes${failedScene ? ` · ${failedScene} paused` : ""}`, "record"),
    stage("video", "Final MP4", project.recordings?.final_video_path ? "done" : "todo",
      project.recordings?.final_video_path
        ? `tutorial.mp4 · ${minutes(project.recordings.final_duration_seconds ?? 0)}` : "tutorial.mp4", "record"),
  ];
}
