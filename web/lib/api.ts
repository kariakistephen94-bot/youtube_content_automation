import type {
  DeviceStart,
  GitHubRepo,
  GitHubStatus,
  GitHubUser,
  Job,
  ProjectDetail,
  ProjectSummary,
  SandboxStatus,
} from "./types";

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        // Required by the API for every state-changing request (CSRF protection).
        "X-Tutorial-AI": "1",
        ...init?.headers,
      },
      cache: "no-store",
    });
  } catch {
    throw new ApiError("Cannot reach the tutorial-ai API. Is `uv run tutorial-ai serve` running?", 0);
  }
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* keep statusText */
    }
    if (response.status === 500 && !detail) {
      detail = "Cannot reach the tutorial-ai API. Is `uv run tutorial-ai serve` running?";
    }
    throw new ApiError(detail, response.status);
  }
  return response.json() as Promise<T>;
}

const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

export interface RecordOptions {
  scenes?: string[];
  from_scene?: string;
  speed: string;
  audible: boolean;
  sandbox?: boolean;
}

export const api = {
  health: () => request<{ ok: boolean; ai_provider?: string; gemini_model: string; gemini_configured: boolean }>("/api/health"),

  models: () => request<{ current: string; available: string[] }>("/api/models"),

  githubStatus: () => request<GitHubStatus>("/api/github/status"),
  githubStart: () => post<DeviceStart>("/api/github/device/start"),
  githubPoll: (userCode: string) =>
    post<{ status: string; interval: number | null; user: GitHubUser | null }>(
      "/api/github/device/poll",
      { user_code: userCode },
    ),
  githubDisconnect: () => post<{ ok: boolean }>("/api/github/disconnect"),
  githubRepos: () => request<GitHubRepo[]>("/api/github/repos"),

  projects: () => request<ProjectSummary[]>("/api/projects"),
  project: (name: string) => request<ProjectDetail>(`/api/projects/${encodeURIComponent(name)}`),

  job: (id: string, since = 0) => request<Job>(`/api/jobs/${id}?since=${since}`),
  analyze: (source: string, name?: string, refresh = false, model?: string) =>
    post<Job>("/api/jobs/analyze", { source, name: name || null, refresh, model }),
  plan: (body: {
    project: string;
    title?: string;
    audience: string;
    duration_minutes: number;
    notes?: string;
    model?: string;
  }) => post<Job>("/api/jobs/plan", body),
  script: (project: string, opts: { restart?: boolean; from_section?: number | null; fix_from_rehearsal?: boolean; model?: string } = {}) =>
    post<Job>("/api/jobs/script", { project, ...opts }),
  narrate: (project: string, opts: { provider?: string; voice?: string } = {}) =>
    post<Job>("/api/jobs/narrate", { project, ...opts }),
  timeline: (project: string, speed: string) => post<Job>("/api/jobs/timeline", { project, speed }),
  rehearse: (project: string, sections?: number[]) => post<Job>("/api/jobs/rehearse", { project, sections }),
  record: (project: string, opts: RecordOptions) => post<Job>("/api/jobs/record", { project, ...opts }),
  sandbox: () => request<SandboxStatus>("/api/sandbox"),
  sandboxBuild: () => post<Job>("/api/jobs/sandbox-build"),
  sandboxStop: () => post<{ stopped: boolean }>("/api/sandbox/stop"),
  build: (project: string) => post<Job>("/api/jobs/build", { project }),
  vscodeTest: (speed: string, sandbox = false) => post<Job>("/api/jobs/vscode-test", { speed, sandbox }),
};
