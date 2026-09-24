"use client";

import { useCallback, useEffect, useState } from "react";

import { api, ApiError } from "@/lib/api";
import type { DeviceStart, GitHubStatus } from "@/lib/types";

import { Button } from "./ui";

/** "Connect with GitHub" via OAuth device flow: show a code, the user enters it on github.com. */
export function GitHubConnect({ onChange }: { onChange: (status: GitHubStatus) => void }) {
  const [status, setStatus] = useState<GitHubStatus | null>(null);
  const [device, setDevice] = useState<DeviceStart | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const s = await api.githubStatus();
      setStatus(s);
      onChange(s);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    }
  }, [onChange]);

  useEffect(() => {
    api.githubStatus()
      .then((s) => {
        setStatus(s);
        onChange(s);
      })
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)));
  }, [onChange]);

  // Poll GitHub while the user enters the code.
  useEffect(() => {
    if (!device) return;
    let interval = device.interval;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const result = await api.githubPoll(device.user_code);
        if (cancelled) return;
        if (result.status === "authorized") {
          setDevice(null);
          await refresh();
          return;
        }
        if (result.status === "expired" || result.status === "denied") {
          setDevice(null);
          setError(result.status === "denied" ? "Sign-in was cancelled on GitHub." : "The code expired. Try again.");
          return;
        }
        if (result.status === "slow_down") interval = (result.interval ?? interval) + 1;
      } catch (e) {
        if (!cancelled) {
          setDevice(null);
          setError(e instanceof ApiError ? e.message : String(e));
        }
        return;
      }
      timer = setTimeout(poll, interval * 1000);
    };
    timer = setTimeout(poll, interval * 1000);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [device, refresh]);

  const connect = async () => {
    setError(null);
    try {
      const code = await api.githubStart();
      setDevice(code);
      window.open(code.verification_uri, "_blank", "noopener");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    }
  };

  const disconnect = async () => {
    await api.githubDisconnect();
    await refresh();
  };

  if (status?.connected && status.user) {
    return (
      <div className="flex items-center gap-3">
        {status.user.avatar_url && (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={status.user.avatar_url} alt="" className="h-7 w-7 rounded-full" />
        )}
        <a href={status.user.html_url} target="_blank" rel="noreferrer" className="text-sm text-zinc-200 hover:underline">
          {status.user.login}
        </a>
        <Button variant="ghost" onClick={disconnect}>Disconnect</Button>
      </div>
    );
  }

  return (
    <div className="flex flex-col items-end gap-1">
      {device ? (
        <div className="flex items-center gap-3 rounded-lg border border-emerald-800 bg-emerald-950/40 px-3 py-1.5 text-sm">
          <span className="text-zinc-300">Enter this code on GitHub:</span>
          <code className="rounded bg-zinc-950 px-2 py-0.5 font-mono text-base tracking-widest text-emerald-300">
            {device.user_code}
          </code>
          <a href={device.verification_uri} target="_blank" rel="noreferrer" className="text-emerald-300 underline">
            open github.com/login/device
          </a>
          <span className="animate-pulse text-zinc-400">waiting…</span>
        </div>
      ) : (
        <Button variant="primary" onClick={connect} disabled={status !== null && !status.configured}>
          <GitHubMark /> Connect with GitHub
        </Button>
      )}
      {status && !status.configured && (
        <span className="max-w-md text-right text-xs text-amber-300">
          Set GITHUB_CLIENT_ID in .env.local (see README), then restart `tutorial-ai serve`.
        </span>
      )}
      {status?.error && <span className="max-w-md text-right text-xs text-red-300">{status.error}</span>}
      {error && <span className="max-w-md text-right text-xs text-red-300">{error}</span>}
    </div>
  );
}

function GitHubMark() {
  return (
    <svg viewBox="0 0 16 16" className="h-4 w-4" fill="currentColor" aria-hidden>
      <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z" />
    </svg>
  );
}
