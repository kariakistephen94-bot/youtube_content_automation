"use client";

import { useState } from "react";

import type { NarrationManifest, ProjectDetail } from "@/lib/types";

import { Badge, Button, inputClass } from "./ui";

/** Narration audio per segment: listen to every line and see its measured length. */
export function NarrationView({ project, busy, onNarrate }: {
  project: ProjectDetail;
  busy: boolean;
  onNarrate: (opts: { provider: string; voice?: string }) => void;
}) {
  const manifest: NarrationManifest | null = project.narration;
  const [provider, setProvider] = useState(manifest?.provider ?? "openai-tts");
  const [voice, setVoice] = useState(manifest?.voice ?? "");
  const scenes = project.scenes?.scenes ?? [];
  const byScene = new Map<string, NarrationManifest["entries"]>();
  for (const entry of manifest?.entries ?? []) {
    byScene.set(entry.scene_id, [...(byScene.get(entry.scene_id) ?? []), entry]);
  }
  const total = (manifest?.entries ?? []).reduce((t, e) => t + e.duration_seconds, 0);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end gap-2">
        <label className="text-xs text-zinc-400">
          Voice provider
          <select className={inputClass} value={provider} onChange={(e) => setProvider(e.target.value)}>
            <option value="openai-tts">OpenAI TTS (natural)</option>
            <option value="macos-say">macOS voice (local, free)</option>
            <option value="gemini-tts">Gemini TTS</option>
          </select>
        </label>
        <label className="text-xs text-zinc-400">
          Voice name
          <input className={inputClass} value={voice} onChange={(e) => setVoice(e.target.value)}
            placeholder={provider === "macos-say" ? "Samantha" : provider === "openai-tts" ? "ash (or alloy, nova, echo, onyx, coral, sage)" : "Kore"} />
        </label>
        <Button variant="primary" disabled={busy || scenes.length === 0}
          onClick={() => onNarrate({ provider, voice: voice || undefined })}>
          {manifest ? "Regenerate narration" : "Generate narration"}
        </Button>
        {manifest && (
          <span className="text-xs text-zinc-500">
            {manifest.entries.length} segments · {(total / 60).toFixed(1)} min · {manifest.provider} / {manifest.voice}
          </span>
        )}
      </div>
      {scenes.length === 0 && <p className="text-sm text-zinc-500">Write the script first; narration is generated per scene.</p>}
      {!manifest && scenes.length > 0 && (
        <p className="text-sm text-zinc-500">No audio yet. OpenAI voices sound natural; the macOS voice is free and offline.</p>
      )}
      {manifest && scenes.map((scene) => {
        const entries = byScene.get(scene.id) ?? [];
        const sceneSeconds = entries.reduce((t, e) => t + e.duration_seconds, 0);
        return (
          <div key={scene.id} className="rounded-lg border border-zinc-800">
            <div className="flex items-center justify-between px-3 py-2">
              <span className="text-sm font-medium text-zinc-100">{scene.id} · {scene.title}</span>
              <Badge color="blue">{sceneSeconds.toFixed(1)}s of speech</Badge>
            </div>
            <ul className="divide-y divide-zinc-800 border-t border-zinc-800">
              {entries.map((entry) => (
                <li key={entry.segment_id} className="flex flex-col gap-1 px-3 py-2 md:flex-row md:items-center md:gap-3">
                  <audio controls preload="none" className="h-8 w-64 shrink-0"
                    src={`/api/projects/${encodeURIComponent(project.summary.name)}/files/${entry.audio_path}`} />
                  <span className="w-12 shrink-0 font-mono text-xs text-zinc-500">{entry.duration_seconds.toFixed(1)}s</span>
                  <span className="text-sm text-zinc-300">{entry.text}</span>
                </li>
              ))}
            </ul>
          </div>
        );
      })}
    </div>
  );
}
