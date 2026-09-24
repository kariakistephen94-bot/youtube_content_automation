"use client";

import { useCallback, useEffect, useState } from "react";

import { api, type RecordOptions } from "@/lib/api";
import { useRunTarget } from "@/lib/run-target";
import type { Job, ProjectDetail, SandboxStatus } from "@/lib/types";

import { Badge, Button, inputClass } from "./ui";

const STATUS_COLOR: Record<string, "green" | "red" | "yellow" | "blue" | "gray"> = {
  SUCCESS: "green", PAUSED: "red", ABORTED: "red", RECORDING: "blue", PENDING: "gray", RETRY: "yellow", REPAIR: "yellow",
};

/** Record scenes in VS Code (in the sandbox or on this Mac), watch it live, build the final MP4. */
export function RecordView({ project, job, busy, onRecord, onBuild, onBuildSandbox }: {
  project: ProjectDetail;
  job: Job | null;
  busy: boolean;
  onRecord: (opts: RecordOptions) => void;
  onBuild: () => void;
  onBuildSandbox: () => void;
}) {
  const [speed, setSpeed] = useState("normal");
  const [audible, setAudible] = useState(false);
  const [where, setWhere] = useRunTarget();
  const [sandbox, setSandbox] = useState<SandboxStatus | null>(null);
  const scenes = project.scenes?.scenes ?? [];
  const manifest = project.recordings;
  const results = new Map((manifest?.results ?? []).map((r) => [r.scene_id, r]));
  const recorded = scenes.filter((s) => results.get(s.id)?.status === "SUCCESS").length;
  const ready = !!project.timeline;
  const rehearsalWarning = project.rehearsal && !project.rehearsal.ok;
  const fileUrl = (path: string) => `/api/projects/${encodeURIComponent(project.summary.name)}/files/${path}`;
  const sandboxReady = !!sandbox?.daemon_running && sandbox.image_built;
  const recordingNow = job?.kind === "record" && job.status === "running";
  const showLiveView = where === "sandbox" && (sandbox?.running || recordingNow);
  const canRecord = ready && (where === "mac" || sandboxReady);

  const refreshSandbox = useCallback(() => {
    api.sandbox().then(setSandbox).catch(() => setSandbox(null));
  }, []);

  useEffect(() => {
    if (where !== "sandbox") return;
    refreshSandbox();
    const timer = setInterval(refreshSandbox, recordingNow || busy ? 3000 : 6000);
    return () => clearInterval(timer);
  }, [where, recordingNow, busy, refreshSandbox]);

  const confirmAndRecord = (opts: { scenes?: string[]; from_scene?: string }) => {
    if (where === "sandbox") {
      onRecord({ ...opts, speed, audible: false, sandbox: true });
    } else if (confirm("This takes over VS Code and your screen for several minutes. Keep your hands off the keyboard and mouse. Start recording?")) {
      onRecord({ ...opts, speed, audible });
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2 text-xs text-zinc-400">
        Record in
        <div className="flex rounded-lg bg-zinc-950 p-0.5">
          {([["mac", "This Mac's screen"], ["sandbox", "Sandbox (your Mac stays free)"]] as const).map(([id, label]) => (
            <button key={id} onClick={() => setWhere(id)} disabled={busy}
              className={`rounded-md px-2.5 py-1 ${where === id ? "bg-zinc-800 text-zinc-100" : "text-zinc-400"}`}>
              {label}
            </button>
          ))}
        </div>
      </div>

      {where === "sandbox" && <SandboxPanel status={sandbox} busy={busy} onBuildSandbox={onBuildSandbox} onRefresh={refreshSandbox} />}

      {showLiveView && sandbox && (
        <div className="rounded-lg border border-sky-900 bg-black">
          <div className="flex items-center justify-between px-3 py-1.5 text-xs text-sky-200">
            <span>Live view of the sandbox (read-only)</span>
            <span className="flex items-center gap-3">
              <a className="underline" href={sandbox.live_view_url} target="_blank" rel="noreferrer">open in a new tab</a>
              <button className="text-red-300 underline" onClick={() => api.sandboxStop().then(refreshSandbox)}>stop recording</button>
            </span>
          </div>
          <iframe title="Sandbox live view" src={sandbox.live_view_url} className="aspect-video w-full rounded-b-lg" />
        </div>
      )}

      <div className="flex flex-wrap items-end gap-2">
        <label className="text-xs text-zinc-400">
          Typing speed
          <select className={inputClass} value={speed} onChange={(e) => setSpeed(e.target.value)}>
            <option value="slow">slow</option><option value="normal">normal</option><option value="fast">fast</option>
          </select>
        </label>
        {where === "mac" && (
          <label className="flex items-center gap-2 pb-2 text-xs text-zinc-400">
            <input type="checkbox" checked={audible} onChange={(e) => setAudible(e.target.checked)} /> play narration aloud while recording
          </label>
        )}
        <Button variant="primary" disabled={busy || !canRecord} onClick={() => confirmAndRecord({})}>
          Record all scenes
        </Button>
        <Button disabled={busy || recorded === 0 || recorded < scenes.length} onClick={onBuild}>
          Build tutorial.mp4
        </Button>
        <span className="text-xs text-zinc-500">{recorded}/{scenes.length} scenes recorded</span>
      </div>
      {!ready && <p className="text-sm text-amber-300">Build the timeline first (Narration → Timeline).</p>}
      {rehearsalWarning && (
        <p className="rounded-lg border border-amber-900 bg-amber-950/40 px-3 py-2 text-sm text-amber-200">
          The rehearsal found failing checks. Recording will likely hit the same problems: fix the script first.
        </p>
      )}
      {where === "mac" ? (
        <p className="text-xs text-zinc-500">
          Recording on this Mac takes over VS Code, the keyboard and the screen. It needs macOS permissions for the app running
          the API (Accessibility, and Screen &amp; System Audio Recording). Ports used by the tutorial (e.g. 8000) must be free.
          If another window comes to the front, the recording pauses safely.
        </p>
      ) : (
        <p className="text-xs text-zinc-500">
          The sandbox is a container with its own screen, VS Code, terminal and Chrome. Recording there never touches your
          keyboard, mouse or windows; the scenes land in this project as usual.
        </p>
      )}

      <ol className="flex flex-col gap-2">
        {scenes.map((scene) => {
          const result = results.get(scene.id);
          return (
            <li key={scene.id} className="rounded-lg border border-zinc-800 p-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="text-sm font-medium text-zinc-100">{scene.id} · {scene.title}</span>
                <span className="flex items-center gap-2">
                  {result && <Badge color={STATUS_COLOR[result.status] ?? "gray"}>{result.status.toLowerCase()}</Badge>}
                  {result?.duration_seconds != null && <span className="text-xs text-zinc-500">{result.duration_seconds.toFixed(0)}s</span>}
                  <Button variant="ghost" disabled={busy || !canRecord} onClick={() => confirmAndRecord({ scenes: [scene.id] })}>
                    {result ? "Re-record" : "Record"}
                  </Button>
                  <Button variant="ghost" disabled={busy || !canRecord} onClick={() => confirmAndRecord({ from_scene: scene.id })}>
                    From here
                  </Button>
                </span>
              </div>
              {result?.error && <pre className="mt-2 whitespace-pre-wrap rounded bg-red-950/50 p-2 text-xs text-red-200">{result.error}</pre>}
              {result?.video_path && (
                <video controls preload="metadata" className="mt-2 w-full max-w-2xl rounded-lg border border-zinc-800 bg-black"
                  src={fileUrl(result.video_path)} />
              )}
            </li>
          );
        })}
      </ol>

      {manifest?.final_video_path && (
        <div className="rounded-lg border border-emerald-900 bg-emerald-950/30 p-3">
          <div className="mb-2 flex items-center gap-2">
            <span className="text-sm font-medium text-emerald-200">tutorial.mp4</span>
            <Badge color="green">{((manifest.final_duration_seconds ?? 0) / 60).toFixed(1)} min</Badge>
            <a className="text-xs text-emerald-300 underline" href={fileUrl(manifest.final_video_path)} download>download</a>
          </div>
          <video controls preload="metadata" className="w-full max-w-3xl rounded-lg border border-zinc-800 bg-black"
            src={fileUrl(manifest.final_video_path)} />
        </div>
      )}
    </div>
  );
}

function SandboxPanel({ status, busy, onBuildSandbox, onRefresh }: {
  status: SandboxStatus | null;
  busy: boolean;
  onBuildSandbox: () => void;
  onRefresh: () => void;
}) {
  if (status === null) return <p className="text-xs text-zinc-500">Checking the sandbox…</p>;
  const steps: { label: string; done: boolean }[] = [
    { label: "Container runtime installed (OrbStack or Docker Desktop)", done: !!status.docker },
    { label: "Runtime running", done: status.daemon_running },
    { label: "Sandbox image built", done: status.image_built },
  ];
  if (steps.every((s) => s.done)) {
    return (
      <p className="flex items-center gap-2 text-xs text-emerald-300">
        <Badge color="green">sandbox ready</Badge>
        {status.running ? "A sandbox is running." : "Recording runs in the container; watch it live here."}
      </p>
    );
  }
  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-950/60 p-3 text-sm">
      <ol className="mb-2 flex flex-col gap-1">
        {steps.map((s) => (
          <li key={s.label} className={s.done ? "text-emerald-300" : "text-zinc-300"}>
            {s.done ? "✓" : "○"} {s.label}
          </li>
        ))}
      </ol>
      <p className="mb-2 text-xs text-zinc-400">{status.message}</p>
      <div className="flex gap-2">
        {status.daemon_running && !status.image_built && (
          <Button variant="primary" disabled={busy} onClick={onBuildSandbox}>Build sandbox image</Button>
        )}
        <Button variant="ghost" onClick={onRefresh}>Check again</Button>
      </div>
    </div>
  );
}
