// Mirrors the Pydantic models returned by the Python API (src/tutorial_ai/api/app.py).

export type Audience = "beginner" | "intermediate" | "advanced";

export interface GitHubUser {
  login: string;
  name: string | null;
  avatar_url: string | null;
  html_url: string;
}

export interface GitHubStatus {
  configured: boolean;
  connected: boolean;
  user: GitHubUser | null;
  error: string | null;
}

export interface DeviceStart {
  user_code: string;
  verification_uri: string;
  expires_in: number;
  interval: number;
}

export interface GitHubRepo {
  full_name: string;
  name: string;
  owner: string;
  private: boolean;
  description: string | null;
  html_url: string;
  language: string | null;
  updated_at: string | null;
  stargazers_count: number;
}

export interface ProjectSummary {
  name: string;
  repository_name: string | null;
  source: string | null;
  has_analysis: boolean;
  has_plan: boolean;
  plan_title: string | null;
  plan_sections: number;
  script_sections: number;
  scenes: number;
  updated_at: string | null;
}

export interface TeachingStep {
  title: string;
  goal: string;
  concepts: string[];
  files: string[];
}

export interface Analysis {
  repository_name: string;
  description: string;
  primary_language: string;
  frameworks: string[];
  difficulty: Audience;
  entry_points: { path: string; description: string }[];
  run_commands: { command: string; description: string }[];
  important_concepts: { name: string; explanation: string }[];
  potential_teaching_order: TeachingStep[];
  teaching_notes: string[];
  validation_warnings: string[];
  project_structure: string;
  source: { origin: string; kind: string };
}

export interface PlanSection {
  section_number: number;
  title: string;
  goal: string;
  expected_result: string;
  estimated_duration_seconds: number;
  files_involved: string[];
}

export interface Plan {
  title: string;
  audience: Audience;
  summary: string;
  learning_objectives: string[];
  simplifications: string[];
  final_architecture: string;
  estimated_duration_seconds: number;
  sections: PlanSection[];
}

export type BeatKind =
  | "talk"
  | "write_code"
  | "delete_file"
  | "run_command"
  | "stop_process"
  | "highlight"
  | "open_browser"
  | "try_endpoint";

export interface SampleFile {
  name: string;
  content: string;
}

export interface Beat {
  id: string;
  kind: BeatKind;
  narration: string;
  file: string | null;
  code_after: string | null;
  command: string | null;
  background: boolean;
  highlight: string | null;
  url: string | null;
  /** try_endpoint only */
  method?: string | null;
  path?: string | null;
  params?: { name: string; value: string }[] | null;
  body?: string | null;
  sample_files?: SampleFile[] | null;
}

export interface ScriptSection {
  section_number: number;
  title: string;
  beats: Beat[];
  state_summary: string;
}

export interface Script {
  title: string;
  sections: ScriptSection[];
  convergence: { path: string; status: string; similarity: number }[];
  warnings: string[];
}

export interface NarrationEntry {
  scene_id: string;
  segment_id: string;
  text: string;
  audio_path: string;
  duration_seconds: number;
}

export interface NarrationManifest {
  provider: string;
  voice: string;
  generated_at: string;
  entries: NarrationEntry[];
}

export interface TimelineEvent {
  time_seconds: number;
  duration_seconds: number;
  kind: "narration" | "action";
  ref_id: string;
  description: string;
}

export interface SceneTimeline {
  scene_id: string;
  events: TimelineEvent[];
  duration_seconds: number;
}

export interface Timeline {
  project_name: string;
  scenes: SceneTimeline[];
}

export interface RehearsalCheck {
  beat_id: string;
  kind: string;
  ok: boolean;
  detail: string;
  seconds: number;
}

export interface RehearsalReport {
  ran_at: string;
  workspace: string;
  ok: boolean;
  sections: { section_number: number; title: string; ok: boolean; checks: RehearsalCheck[]; seconds: number }[];
  final_tests: RehearsalCheck[];
}

export interface RecordingResult {
  scene_id: string;
  status: "PENDING" | "RECORDING" | "SUCCESS" | "RETRY" | "REPAIR" | "PAUSED" | "ABORTED";
  video_path: string | null;
  raw_video_path: string | null;
  duration_seconds: number | null;
  recorded_at: string | null;
  error: string | null;
}

export interface RecordingsManifest {
  results: RecordingResult[];
  final_video_path: string | null;
  final_duration_seconds: number | null;
}

export interface ProjectDetail {
  summary: ProjectSummary;
  analysis: Analysis | null;
  plan: Plan | null;
  script: Script | null;
  scenes: { scenes: { id: string; title: string; estimated_duration_seconds: number; narration_segments: { id: string; text: string }[] }[] } | null;
  typed_code: Record<string, string>;
  narration: NarrationManifest | null;
  timeline: Timeline | null;
  rehearsal: RehearsalReport | null;
  recordings: RecordingsManifest | null;
}

export interface Job {
  id: string;
  kind: string;
  project: string | null;
  status: "running" | "succeeded" | "failed";
  created_at: string;
  finished_at: string | null;
  logs: string[];
  log_count?: number;
  progress?: number | null;
  progress_label?: string | null;
  error: string | null;
  result: Record<string, unknown> | null;
}

/** The recording sandbox (a container with its own screen, VS Code and Chrome). */
export interface SandboxStatus {
  docker: string | null;
  daemon_running: boolean;
  image_built: boolean;
  running: boolean;
  live_view_url: string;
  message: string;
}
