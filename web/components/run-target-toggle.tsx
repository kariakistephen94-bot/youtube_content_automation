"use client";

import { useEffect, useState } from "react";

import { api } from "@/lib/api";
import { useRunTarget } from "@/lib/run-target";
import type { SandboxStatus } from "@/lib/types";

/** The header switch: run recordings and the VS Code test on this Mac, or in the sandbox. */
export function RunTargetToggle({ disabled }: { disabled?: boolean }) {
  const [target, setTarget] = useRunTarget();
  const [status, setStatus] = useState<SandboxStatus | null>(null);

  useEffect(() => {
    let cancelled = false;
    const check = () => api.sandbox().then((s) => !cancelled && setStatus(s)).catch(() => {});
    check();
    const timer = setInterval(check, 10000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  const ready = !!status?.daemon_running && status.image_built;
  const dot = ready ? "bg-emerald-400" : status?.daemon_running ? "bg-amber-400" : "bg-zinc-600";
  const hint = ready ? "Sandbox ready" : status?.message ?? "Checking the sandbox…";

  return (
    <div className="flex items-center gap-2 text-xs text-zinc-400" title={hint}>
      Run automation on
      <div role="radiogroup" aria-label="Run automation on" className="flex rounded-lg border border-zinc-700 bg-zinc-950 p-0.5">
        <button role="radio" aria-checked={target === "mac"} disabled={disabled} onClick={() => setTarget("mac")}
          className={`rounded-md px-3 py-1 font-medium ${target === "mac" ? "bg-zinc-100 text-zinc-900" : "text-zinc-400 hover:text-zinc-200"}`}>
          This Mac
        </button>
        <button role="radio" aria-checked={target === "sandbox"} disabled={disabled} onClick={() => setTarget("sandbox")}
          className={`flex items-center gap-1.5 rounded-md px-3 py-1 font-medium ${target === "sandbox" ? "bg-sky-500 text-white" : "text-zinc-400 hover:text-zinc-200"}`}>
          <span className={`h-2 w-2 rounded-full ${dot}`} />
          Sandbox
        </button>
      </div>
    </div>
  );
}
