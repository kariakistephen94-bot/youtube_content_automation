"use client";

import { useState } from "react";

import type { ProjectDetail, TimelineEvent } from "@/lib/types";

import { Badge, Button, inputClass } from "./ui";

const ROW_H = 22;
const LABEL_W = 70;

function colorFor(event: TimelineEvent): string {
  if (event.kind === "narration") return "#38bdf8"; // sky
  const d = event.description;
  if (d.startsWith("type")) return "#34d399"; // emerald
  if (d.startsWith("run")) return "#fbbf24"; // amber
  if (d.startsWith("highlight")) return "#e879f9"; // fuchsia
  if (d.startsWith("open http")) return "#60a5fa"; // blue
  if (d.startsWith("ctrl+c")) return "#f87171"; // red
  return "#a1a1aa"; // zinc
}

/** When each narration line is spoken and each on-screen action happens, per scene. */
export function TimelineView({ project, busy, onBuild }: {
  project: ProjectDetail;
  busy: boolean;
  onBuild: (speed: string) => void;
}) {
  const [speed, setSpeed] = useState("normal");
  const timeline = project.timeline;
  const total = timeline?.scenes.reduce((t, s) => t + s.duration_seconds, 0) ?? 0;
  const ready = !!project.narration;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end gap-2">
        <label className="text-xs text-zinc-400">
          Typing speed
          <select className={inputClass} value={speed} onChange={(e) => setSpeed(e.target.value)}>
            <option value="slow">slow</option>
            <option value="normal">normal</option>
            <option value="fast">fast</option>
          </select>
        </label>
        <Button variant="primary" disabled={busy || !ready} onClick={() => onBuild(speed)}>
          {timeline ? "Rebuild timeline" : "Build timeline"}
        </Button>
        {timeline && <span className="text-xs text-zinc-500">{timeline.scenes.length} scenes · {(total / 60).toFixed(1)} min</span>}
        {!ready && <span className="text-xs text-amber-300">Generate narration first: the timeline uses the real audio lengths.</span>}
      </div>
      <div className="flex flex-wrap gap-2 text-xs text-zinc-400">
        {[["narration", "#38bdf8"], ["type code", "#34d399"], ["run command", "#fbbf24"], ["highlight", "#e879f9"], ["browser", "#60a5fa"], ["stop", "#f87171"], ["other", "#a1a1aa"]].map(([label, color]) => (
          <span key={label} className="flex items-center gap-1"><span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: color }} />{label}</span>
        ))}
      </div>
      {timeline?.scenes.map((scene) => {
        const title = project.scenes?.scenes.find((s) => s.id === scene.scene_id)?.title ?? scene.scene_id;
        return <SceneChart key={scene.scene_id} id={scene.scene_id} title={title} duration={scene.duration_seconds} events={scene.events} />;
      })}
    </div>
  );
}

function SceneChart({ id, title, duration, events }: { id: string; title: string; duration: number; events: TimelineEvent[] }) {
  const [hover, setHover] = useState<TimelineEvent | null>(null);
  const width = 900;
  const plotW = width - LABEL_W - 10;
  const x = (t: number) => LABEL_W + (t / Math.max(duration, 1)) * plotW;
  const rows: { label: string; events: TimelineEvent[] }[] = [
    { label: "narration", events: events.filter((e) => e.kind === "narration") },
    { label: "actions", events: events.filter((e) => e.kind === "action") },
  ];
  const ticks = Array.from({ length: Math.floor(duration / 15) + 1 }, (_, i) => i * 15);
  const height = 16 + rows.length * (ROW_H + 6) + 18;

  return (
    <div className="rounded-lg border border-zinc-800 p-3">
      <div className="mb-2 flex items-center justify-between">
        <span className="text-sm font-medium text-zinc-100">{id} · {title}</span>
        <Badge color="blue">{duration.toFixed(0)}s</Badge>
      </div>
      <svg viewBox={`0 0 ${width} ${height}`} className="w-full" role="img" aria-label={`Timeline of ${title}`}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={x(t)} x2={x(t)} y1={12} y2={height - 18} stroke="#27272a" />
            <text x={x(t)} y={height - 4} fontSize="10" fill="#71717a" textAnchor="middle">{t}s</text>
          </g>
        ))}
        {rows.map((row, r) => {
          const y = 16 + r * (ROW_H + 6);
          return (
            <g key={row.label}>
              <text x={0} y={y + ROW_H / 2 + 4} fontSize="11" fill="#a1a1aa">{row.label}</text>
              {row.events.map((e) => (
                <rect key={e.ref_id} x={x(e.time_seconds)} y={y} width={Math.max(x(e.time_seconds + e.duration_seconds) - x(e.time_seconds), 2)}
                  height={ROW_H} rx={3} fill={colorFor(e)} opacity={hover && hover.ref_id !== e.ref_id ? 0.45 : 0.9}
                  onMouseEnter={() => setHover(e)} onMouseLeave={() => setHover(null)}>
                  <title>{`${e.time_seconds.toFixed(1)}s (+${e.duration_seconds.toFixed(1)}s) ${e.description}`}</title>
                </rect>
              ))}
            </g>
          );
        })}
      </svg>
      <p className="mt-1 h-4 truncate font-mono text-xs text-zinc-400">
        {hover ? `${hover.time_seconds.toFixed(1)}s · ${hover.duration_seconds.toFixed(1)}s · ${hover.description}` : "Hover a bar for details"}
      </p>
    </div>
  );
}
