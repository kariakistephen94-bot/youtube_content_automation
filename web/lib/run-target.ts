"use client";

import { useSyncExternalStore } from "react";

/** Where desktop automation (recording, the VS Code test) runs: this Mac's screen, or the sandbox. */
export type RunTarget = "mac" | "sandbox";

const KEY = "tutorial-ai.record-where";
const EVENT = "tutorial-ai:run-target";

function read(): RunTarget {
  try {
    return localStorage.getItem(KEY) === "sandbox" ? "sandbox" : "mac";
  } catch {
    return "mac";
  }
}

function subscribe(onChange: () => void): () => void {
  window.addEventListener(EVENT, onChange);
  window.addEventListener("storage", onChange);
  return () => {
    window.removeEventListener(EVENT, onChange);
    window.removeEventListener("storage", onChange);
  };
}

export function setRunTarget(target: RunTarget): void {
  try {
    localStorage.setItem(KEY, target);
  } catch {
    /* private window: the choice lasts for this page only */
  }
  window.dispatchEvent(new Event(EVENT));
}

/** Shared by the header switch, the Record tab and the VS Code test; default: this Mac. */
export function useRunTarget(): [RunTarget, (target: RunTarget) => void] {
  const target = useSyncExternalStore(subscribe, read, () => "mac" as RunTarget);
  return [target, setRunTarget];
}
