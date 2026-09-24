"use client";

import { useCallback, useEffect, useState } from "react";

import { GitHubConnect } from "@/components/github-connect";
import { JobLog } from "@/components/job-log";
import { NewTutorial } from "@/components/new-tutorial";
import { ProjectList } from "@/components/project-list";
import { ProjectView } from "@/components/project-view";
import { RunTargetToggle } from "@/components/run-target-toggle";
import { Button, Card, ErrorNote } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import type { GitHubStatus, Job, ProjectDetail, ProjectSummary } from "@/lib/types";
import { useRunTarget } from "@/lib/run-target";
import { useJob } from "@/lib/use-job";

export default function Home() {
  const [health, setHealth] = useState<{ ai_provider?: string; gemini_model: string; gemini_configured: boolean } | null>(null);
  const [apiError, setApiError] = useState<string | null>(null);
  const [github, setGithub] = useState<GitHubStatus | null>(null);
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<ProjectDetail | null>(null);
  const [speed, setSpeed] = useState("normal");
  const [models, setModels] = useState<string[]>([]);
  const [model, setModel] = useState<string>("");
  const [runTarget] = useRunTarget();

  const loadProjects = useCallback(async () => {
    try {
      setProjects(await api.projects());
      setApiError(null);
    } catch (e) {
      setApiError(e instanceof ApiError ? e.message : String(e));
    }
  }, []);

  const loadDetail = useCallback(async (name: string) => {
    try {
      setDetail(await api.project(name));
    } catch (e) {
      setApiError(e instanceof ApiError ? e.message : String(e));
    }
  }, []);

  const onJobFinished = useCallback(
    async (job: Job) => {
      await loadProjects();
      const project = (job.result?.project as string | undefined) ?? job.project;
      if (project) {
        setSelected(project);
        await loadDetail(project);
      }
    },
    [loadProjects, loadDetail],
  );
  const { job, logs, error: jobError, start, running } = useJob(onJobFinished);

  useEffect(() => {
    const fail = (e: unknown) => setApiError(e instanceof ApiError ? e.message : String(e));
    api.health().then((h) => {
      setHealth(h);
      setModel(h.gemini_model);
    }).catch(fail);
    api.models().then((m) => setModels(m.available)).catch(() => setModels([]));
    api.projects().then(setProjects).catch(fail);
  }, []);

  useEffect(() => {
    if (!selected) return;
    api.project(selected).then(setDetail).catch((e) => setApiError(e instanceof ApiError ? e.message : String(e)));
  }, [selected]);

  // While a job runs, refresh the selected project every few seconds so completed script
  // sections appear as soon as they are saved.
  useEffect(() => {
    if (!running || !selected) return;
    const timer = setInterval(() => {
      api.project(selected).then(setDetail).catch(() => {});
    }, 4000);
    return () => clearInterval(timer);
  }, [running, selected]);

  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-100">
      <header className="flex flex-wrap items-center justify-between gap-4 border-b border-zinc-800 px-6 py-4">
        <div>
          <h1 className="text-lg font-semibold">tutorial-ai</h1>
          <p className="text-xs text-zinc-500">
            Repository → teaching plan → script → VS Code
            {health?.ai_provider && <> · {health.ai_provider}</>}
            {health && !health.gemini_configured && <span className="text-amber-300"> · API key not set</span>}
          </p>
        </div>
        <label className="flex items-center gap-2 text-xs text-zinc-400">
          AI model
          <select
            value={model}
            onChange={(e) => setModel(e.target.value)}
            title="Free-tier quota is counted per model: switch if one is exhausted."
            className="rounded-lg border border-zinc-700 bg-zinc-950 px-2 py-1 text-xs text-zinc-100"
          >
            {[model, ...models.filter((m) => m !== model)].filter(Boolean).map((m) => (
              <option key={m} value={m}>{m}</option>
            ))}
          </select>
        </label>
        <RunTargetToggle disabled={running} />
        <GitHubConnect onChange={setGithub} />
      </header>

      <main className="grid grid-cols-1 gap-4 p-6 lg:grid-cols-[360px_1fr]">
        <div className="flex flex-col gap-4">
          <ErrorNote message={apiError} />
          <NewTutorial
            githubConnected={!!github?.connected}
            busy={running}
            onAnalyze={(source, name) => start(() => api.analyze(source, name, false, model))}
          />
          <ProjectList projects={projects} selected={selected} onSelect={setSelected} />
          <Card title="VS Code automation">
            <p className="mb-3 text-sm text-zinc-400">
              Opens VS Code, creates main.py, types code, saves and runs it.{" "}
              {runTarget === "sandbox"
                ? "Runs in the sandbox: your keyboard and screen stay free."
                : "Runs on this Mac: hands off the keyboard while it runs."}
            </p>
            <div className="flex gap-2">
              <select value={speed} onChange={(e) => setSpeed(e.target.value)}
                className="rounded-lg border border-zinc-700 bg-zinc-950 px-2 text-sm">
                <option value="slow">slow</option>
                <option value="normal">normal</option>
                <option value="fast">fast</option>
              </select>
              <Button
                disabled={running}
                onClick={() => {
                  if (runTarget === "sandbox") void start(() => api.vscodeTest(speed, true));
                  else if (confirm("This opens VS Code and types on your screen for about 20 seconds. Keep your hands off the keyboard and mouse. Start?"))
                    void start(() => api.vscodeTest(speed));
                }}
              >
                {runTarget === "sandbox" ? "Run VS Code test in sandbox" : "Run VS Code test"}
              </Button>
            </div>
          </Card>
        </div>

        <div className="flex min-w-0 flex-col gap-4">
          <JobLog job={job} logs={logs} error={jobError} />
          {detail ? (
            <ProjectView
              key={detail.summary.name}
              project={detail}
              job={job}
              logs={logs}
              busy={running}
              onPlan={(body) => start(() => api.plan({ project: detail.summary.name, model, ...body }))}
              onScript={(opts) => start(() => api.script(detail.summary.name, { model, ...opts }))}
              onNarrate={(opts) => start(() => api.narrate(detail.summary.name, opts))}
              onTimeline={(speed) => start(() => api.timeline(detail.summary.name, speed))}
              onRehearse={(sections) => start(() => api.rehearse(detail.summary.name, sections))}
              onRecord={(opts) => start(() => api.record(detail.summary.name, opts))}
              onBuild={() => start(() => api.build(detail.summary.name))}
              onBuildSandbox={() => start(() => api.sandboxBuild())}
            />
          ) : (
            <Card><p className="text-sm text-zinc-500">Select a project, or analyze a repository to create one.</p></Card>
          )}
        </div>
      </main>
    </div>
  );
}
