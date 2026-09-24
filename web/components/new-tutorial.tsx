"use client";

import { useEffect, useMemo, useState } from "react";

import { api, ApiError } from "@/lib/api";
import type { GitHubRepo } from "@/lib/types";

import { Badge, Button, Card, ErrorNote, inputClass } from "./ui";

/** Pick a repository (from GitHub, a URL, or a local folder) and start the analysis. */
export function NewTutorial({ githubConnected, busy, onAnalyze }: {
  githubConnected: boolean;
  busy: boolean;
  onAnalyze: (source: string, name?: string) => void;
}) {
  const [chosenMode, setMode] = useState<"github" | "url" | null>(null);
  const mode = chosenMode ?? (githubConnected ? "github" : "url");
  const [repos, setRepos] = useState<GitHubRepo[] | null>(null);
  const [filter, setFilter] = useState("");
  const [source, setSource] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (mode !== "github" || !githubConnected || repos !== null) return;
    api.githubRepos().then(setRepos).catch((e) => setError(e instanceof ApiError ? e.message : String(e)));
  }, [mode, githubConnected, repos]);

  const visible = useMemo(() => {
    const q = filter.toLowerCase();
    return (repos ?? []).filter((r) => r.full_name.toLowerCase().includes(q)).slice(0, 50);
  }, [repos, filter]);

  return (
    <Card
      title="New tutorial"
      actions={
        <div className="flex rounded-lg bg-zinc-950 p-0.5 text-xs">
          {(["github", "url"] as const).map((m) => (
            <button
              key={m}
              onClick={() => setMode(m)}
              className={`rounded-md px-2 py-1 ${mode === m ? "bg-zinc-800 text-zinc-100" : "text-zinc-400"}`}
            >
              {m === "github" ? "My repos" : "URL / folder"}
            </button>
          ))}
        </div>
      }
    >
      <div className="flex flex-col gap-3">
        {mode === "github" ? (
          githubConnected ? (
            <>
              <input className={inputClass} placeholder="Search your repositories…" value={filter}
                onChange={(e) => setFilter(e.target.value)} />
              <ul className="max-h-72 divide-y divide-zinc-800 overflow-y-auto rounded-lg border border-zinc-800">
                {repos === null && <li className="p-3 text-sm text-zinc-400">Loading repositories…</li>}
                {repos !== null && visible.length === 0 && <li className="p-3 text-sm text-zinc-400">No matches.</li>}
                {visible.map((repo) => (
                  <li key={repo.full_name}>
                    <button
                      onClick={() => setSource(repo.html_url)}
                      className={`w-full px-3 py-2 text-left hover:bg-zinc-800 ${source === repo.html_url ? "bg-zinc-800" : ""}`}
                    >
                      <div className="flex items-center gap-2 text-sm text-zinc-100">
                        {repo.full_name}
                        {repo.private && <Badge color="yellow">private</Badge>}
                        {repo.language && <span className="text-xs text-zinc-500">{repo.language}</span>}
                      </div>
                      {repo.description && <p className="truncate text-xs text-zinc-400">{repo.description}</p>}
                    </button>
                  </li>
                ))}
              </ul>
            </>
          ) : (
            <p className="text-sm text-zinc-400">Connect GitHub (top right) to pick from your repositories, including private ones.</p>
          )
        ) : (
          <input className={inputClass} value={source} onChange={(e) => setSource(e.target.value)}
            placeholder="https://github.com/owner/repo  or  /path/to/project" />
        )}

        <input className={inputClass} value={name} onChange={(e) => setName(e.target.value)}
          placeholder="Project name (optional, defaults to the repo name)" />
        <ErrorNote message={error} />
        <Button variant="primary" disabled={!source.trim() || busy} onClick={() => onAnalyze(source.trim(), name.trim() || undefined)}>
          {busy ? "A job is running…" : source ? `Analyze ${shortName(source)}` : "Choose a repository"}
        </Button>
      </div>
    </Card>
  );
}

function shortName(source: string): string {
  const parts = source.replace(/\/+$/, "").split("/");
  return parts.slice(-2).join("/");
}
