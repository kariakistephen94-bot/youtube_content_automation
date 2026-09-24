"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { api, ApiError } from "./api";
import type { Job } from "./types";

/** Starts jobs and streams their logs by polling /api/jobs/{id}. */
export function useJob(onFinished?: (job: Job) => void) {
  const [job, setJob] = useState<Job | null>(null);
  const [logs, setLogs] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const finishedRef = useRef(onFinished);
  useEffect(() => {
    finishedRef.current = onFinished;
  }, [onFinished]);

  const start = useCallback(async (launch: () => Promise<Job>) => {
    setError(null);
    try {
      const started = await launch();
      setLogs([]);
      setJob(started);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    if (!job || job.status !== "running") return;
    let cancelled = false;
    const tick = async () => {
      try {
        // Always fetch the full log and replace it: idempotent even if two pollers overlap.
        const update = await api.job(job.id, 0);
        if (cancelled) return;
        setLogs(update.logs);
        setJob((prev) => (prev && prev.id === update.id ? { ...prev, progress: update.progress, progress_label: update.progress_label } : prev));
        if (update.status !== "running") {
          setJob(update);
          finishedRef.current?.(update);
          return;
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof ApiError ? e.message : String(e));
      }
      if (!cancelled) timer = setTimeout(tick, 1000);
    };
    let timer = setTimeout(tick, 300);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [job]);

  return { job, logs, error, start, running: job?.status === "running" };
}
