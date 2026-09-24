"use client";

import { useState } from "react";

import type { Beat, BeatKind, Script } from "@/lib/types";

import { Badge } from "./ui";

const KIND: Record<BeatKind, { label: string; color: "blue" | "green" | "yellow" | "purple" | "red" | "gray" }> = {
  talk: { label: "talk", color: "gray" },
  write_code: { label: "type code", color: "green" },
  run_command: { label: "run", color: "yellow" },
  highlight: { label: "highlight", color: "purple" },
  open_browser: { label: "browser", color: "blue" },
  try_endpoint: { label: "try endpoint", color: "blue" },
  stop_process: { label: "stop", color: "red" },
  delete_file: { label: "delete file", color: "red" },
};

/** The script as a screenplay: what is said, and what happens on screen, step by step. */
export function ScriptView({ script, typedCode }: { script: Script; typedCode: Record<string, string> }) {
  const [open, setOpen] = useState<number | null>(script.sections[0]?.section_number ?? null);

  return (
    <div className="flex flex-col gap-2">
      {script.warnings.length > 0 && (
        <ul className="rounded-lg border border-amber-900 bg-amber-950/40 p-3 text-sm text-amber-200">
          {script.warnings.map((w) => <li key={w}>{w}</li>)}
        </ul>
      )}
      {script.sections.map((section) => (
        <div key={section.section_number} className="rounded-lg border border-zinc-800">
          <button
            onClick={() => setOpen(open === section.section_number ? null : section.section_number)}
            className="flex w-full items-center justify-between px-3 py-2 text-left hover:bg-zinc-800/60"
          >
            <span className="text-sm font-medium text-zinc-100">
              {section.section_number}. {section.title}
            </span>
            <span className="text-xs text-zinc-500">{section.beats.length} steps</span>
          </button>
          {open === section.section_number && (
            <ol className="flex flex-col gap-3 border-t border-zinc-800 p-3">
              {section.beats.map((beat) => <BeatRow key={beat.id} beat={beat} typed={typedCode[beat.id]} />)}
              <li className="text-xs text-zinc-500">After this section: {section.state_summary}</li>
            </ol>
          )}
        </div>
      ))}
    </div>
  );
}

function BeatRow({ beat, typed }: { beat: Beat; typed?: string }) {
  const [showFull, setShowFull] = useState(false);
  const kind = KIND[beat.kind] ?? { label: beat.kind, color: "gray" as const };
  const request =
    beat.kind === "try_endpoint"
      ? `${(beat.method ?? "").toUpperCase()} ${beat.path ?? ""}` +
        ((beat.params?.length ?? 0) > 0 ? ` (${beat.params!.map((p) => `${p.name}=${p.value}`).join(", ")})` : "")
      : null;
  const target = request ?? beat.file ?? beat.command ?? beat.url;

  return (
    <li className="flex flex-col gap-1.5">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="font-mono text-zinc-600">{beat.id}</span>
        <Badge color={kind.color}>{kind.label}</Badge>
        {target && <code className="font-mono text-zinc-300">{target}</code>}
        {request && beat.url && <span className="text-zinc-500">in {beat.url}</span>}
        {beat.background && <Badge>background</Badge>}
        {beat.highlight && <code className="rounded bg-fuchsia-950 px-1 font-mono text-fuchsia-200">{beat.highlight}</code>}
      </div>
      {beat.narration && <p className="text-sm italic text-zinc-300">“{beat.narration}”</p>}
      {beat.code_after !== null && (
        <div>
          <pre className="max-h-80 overflow-auto rounded-lg bg-zinc-950 p-3 font-mono text-xs leading-relaxed text-emerald-200">
            {showFull ? beat.code_after : (typed ?? beat.code_after).replace(/^\n+|\n+$/g, "") || "(no new text)"}
          </pre>
          <button onClick={() => setShowFull(!showFull)} className="mt-1 text-xs text-zinc-500 hover:text-zinc-300">
            {showFull ? "Show only what is typed" : "Show the whole file after this step"}
          </button>
        </div>
      )}
    </li>
  );
}
