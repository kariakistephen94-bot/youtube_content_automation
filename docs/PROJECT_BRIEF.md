# Project Brief: AI Programming Tutorial Video Generator

> Original product brief, stored verbatim-in-substance as the source of truth for scope and phases.
> Phases are implemented one at a time; see "Phases" below.

## Goal

Take a GitHub repository or local programming project and automatically turn it into a
YouTube-style programming tutorial. The finished system should eventually be able to:

1. Read a GitHub repository.
2. Understand the project.
3. Understand the programming language/framework.
4. Determine how the project should be taught.
5. Reconstruct the finished project into a logical teaching sequence.
6. Generate a natural YouTube-style teaching script.
7. Generate narration audio.
8. Open VS Code.
9. Create files and folders.
10. Type code at a realistic human speed.
11. Open and use the terminal.
12. Run commands.
13. Run the application.
14. Open a browser when necessary.
15. Demonstrate the finished application.
16. Synchronize actions with narration.
17. Record the screen and audio.
18. Produce a final MP4 video.

Long-term pipeline:

```
GitHub Repository → Repository Analysis → Tutorial Planning → Teaching Script → Narration Audio
→ Action Timeline → VS Code Automation → Terminal Automation → Browser Automation
→ Screen + Audio Recording → Video Processing → Final YouTube Tutorial MP4
```

## Start small

The first version runs locally on a Mac. V1 concentrates on:

```
GitHub/local project → AI analysis → tutorial plan → script → structured scenes → TTS narration
→ VS Code automation → human-style typing → terminal automation → screen recording → final MP4
```

Once the local pipeline works reliably, recording workers move to cloud Linux environments.
Do NOT prematurely introduce Kubernetes, Kafka, microservices, distributed orchestration,
complex cloud infrastructure, or enterprise abstractions. Keep V1 simple, modular, understandable.

## Primary use case

```
tutorial-ai create \
  --repo https://github.com/myusername/fastapi-file-upload \
  --title "Build a File Upload API with FastAPI" \
  --audience beginner \
  --duration 25
```

(or a web interface later). The program clones/reads the repo, analyzes it, creates a tutorial,
narration and an action timeline, launches a clean teaching workspace, records itself rebuilding
and teaching the project, and produces `output/tutorial.mp4`.

## Core product principle

The system must NOT simply read repository files and type them one after another. It must
understand the project and reconstruct it into a TEACHING JOURNEY. Example for a FastAPI upload
repo (`app/main.py`, `routes/uploads.py`, `services/files.py`, `schemas/files.py`):

1. Explain what we're building. 2. Initialize the project. 3. Create a virtual environment.
4. Install dependencies. 5. Create the first FastAPI application. 6. Run the server.
7. Explain UploadFile. 8. Build the upload endpoint. 9. Test the endpoint. 10. Add validation.
11. Extract file logic into a service. 12. Improve error handling. 13. Test PDF upload.
14. Test image upload. 15. Explain final architecture.

Order optimizes understanding; it need not match commit history or file ordering.

## Teaching style

Natural, clear, conversational, direct, educational, practical, beginner-friendly when requested,
technically correct, not unnecessarily formal.

Avoid: "Let's delve into...", "In the ever-evolving landscape...", "Unlock the power...",
"Revolutionize...", "Game-changing...".

Prefer: "Alright, let's start by creating the project." / "Before we write this endpoint, I want to
explain why we're using UploadFile here." / "Let's run this and make sure everything works before we
continue." / "Now this works, but there's one problem with the way we've written it." / "Instead of
putting all this logic inside the route, let's move it into a service."

## Pausing and teaching

Don't type continuously. Real instructors type, pause, explain, highlight, run code, talk, return to
coding. Example: narration "Notice that this function is async." → action highlight `async` →
narration "That means FastAPI can wait for I/O..." → pause → "Now let's add the file parameter." →
continue typing.

## Architecture (separate modules)

Repository analysis, tutorial planning, script generation, narration generation, scene generation,
action generation, human typing, IDE control, terminal control, browser control, recording, video
processing, AI providers, TTS providers, storage, configuration.

## Tech stack (V1)

Python 3.12+, uv (`uv init` / `uv add` / `uv run`; no pip unless technically required), Pydantic,
Google Gemini as first AI provider, Playwright for browser automation, pyautogui or native Mac
automation, FFmpeg for recording/video processing, Git CLI for cloning, asyncio where appropriate.

## AI provider architecture

Interface `AIProvider` with methods like `analyze_repository()`, `create_tutorial_plan()`,
`create_script()`, `create_scene()`, `create_actions()`, `explain_code()`, `repair_scene()`.
Implement `GeminiProvider(AIProvider)`. Config from env: `GEMINI_API_KEY`, `GEMINI_MODEL`.
No hardcoded keys; no model names scattered through the code.

## TTS architecture

Interface `TTSProvider` with `synthesize(text, output_path) -> AudioResult` and
`get_audio_duration()`. Eventually ElevenLabs, Gemini TTS, OpenAI TTS, local TTS; implement one first.

## Core data model (Pydantic)

At minimum: RepositoryAnalysis, TutorialPlan, TutorialSection, Scene, NarrationSegment, Action,
Timeline, AudioSegment, RecordingJob, RecordingResult, VideoProject. No random dicts passed around.

### RepositoryAnalysis

repository_name, description, primary_language, frameworks, dependencies, entry_points,
project_structure, important_files, important_concepts, application_flow, setup_steps,
run_commands, test_commands, environment_variables, external_services, difficulty, prerequisites,
potential_teaching_order, files_to_ignore.

The analyzer must not send huge irrelevant files to the AI. Ignore .git, node_modules, .venv, dist,
build, coverage, binary files, lockfile contents unless needed, generated files, media, large
datasets. Respect token limits.

### TutorialPlan

title, audience, difficulty, learning_objectives, prerequisites, estimated_duration, sections.
Each section: section_number, title, goal, concepts, files_involved, commands, expected_result,
estimated_duration.

### Scene system (very important)

Video = scenes (Introduction, Project setup, Create FastAPI app, Run first server, Create upload
endpoint, ...). Scene: id, title, goal, narration_segments, actions, estimated_duration,
expected_state, files_involved.

### Narration segments

Don't use one voice file per scene. NarrationSegment: id, text, estimated_duration, audio_path,
start_offset, linked_actions.

## Action language

Strict schema; the AI generates actions, the automation engine executes them. Types:
OPEN_VSCODE, OPEN_FOLDER, CREATE_FILE, CREATE_FOLDER, OPEN_FILE, TYPE_TEXT, TYPE_CODE, MOVE_CURSOR,
SELECT_TEXT, HIGHLIGHT_TEXT, DELETE_TEXT, PRESS_KEY, KEYBOARD_SHORTCUT, WAIT, OPEN_TERMINAL,
TYPE_TERMINAL, RUN_COMMAND, WAIT_FOR_COMMAND, OPEN_BROWSER, NAVIGATE_BROWSER, CLICK_BROWSER,
WAIT_FOR_BROWSER, SCROLL, FOCUS_EDITOR, FOCUS_TERMINAL, SAVE_FILE, PLAY_AUDIO, START_RECORDING,
STOP_RECORDING.

Action: id, type, parameters, start_after, expected_duration, wait_after, failure_behavior.
e.g. `{"type": "OPEN_FILE", "parameters": {"path": "app/main.py"}}`,
`{"type": "RUN_COMMAND", "parameters": {"command": "uv run fastapi dev"}}`.
AI-generated shell commands never execute without validation.

## Command safety

Command validator with allowlist/denylist. Allow in V1: uv, python, pytest, git, npm, npx, pnpm,
node, fastapi, django. Reject (or require explicit permission): `rm -rf /`, destructive sudo, disk
formatting, shutdown, reboot, `curl | bash`, unknown executable downloads.

## Human typing engine (one of the most important features)

`HumanTypingEngine`: never paste whole blocks. Supports base speed, variation, punctuation pauses,
newline pauses, code-block pauses, optional minor corrections, different speeds for prose / code /
terminal. Example config: typing_speed_chars_per_second=7, variation=0.20, newline_pause=0.25,
section_pause=1.0, terminal_speed=8. Natural but not painfully slow; no lots of fake mistakes.
Presets: slow / normal / fast.

## Voice + typing synchronization

Never generate narration live. Pipeline: script → TTS → audio files → measure durations → build
timeline → record. Per scene compute narration, typing, terminal, browser durations and pauses.
Narration longer than action → screen stays visible. Action longer → insert narration pause or
adjust typing speed. Produces a deterministic, reproducible `Timeline`
(e.g. 00:00 play narration, 00:03 open file, 00:05 start typing, 00:18 finish, 00:18 highlight
UploadFile, 00:20 continue narration, 00:26 open terminal, 00:28 type command, 00:31 Enter,
00:32 wait for server).

## VS Code automation

Works with the real VS Code on Mac. Simplest reliable approach: subprocess + `code` CLI + keyboard
shortcuts + pyautogui + AppleScript if necessary. No VS Code extension unless clearly necessary.
`VSCodeController`: launch, open_folder, open_file, create_file, focus_editor, open_terminal,
save_file, type_text, run_command, press_shortcut.

## Workspace safety

Never record against the original repo. `workspace/original/` (reference) and
`workspace/tutorial_build/` (reconstruction).

## Project state

ProjectState: current_scene, current_file, existing_files, created_files, terminal_state,
running_processes, application_url, recording_active. Validate expected state before actions run.

## Teaching repository

The AI may simplify the repo for teaching (introduce routes/services/DI/Docker/CI gradually) via a
TeachingPlan, without changing final functionality without reason; converge toward the real
architecture.

## Terminal automation

`TerminalController`: open terminal, type naturally, execute, capture output, wait for process,
recognize success/failure. Capture actual process output separately for important commands.

## Error handling during recording

Expect failures (install fails, port occupied, test fails, server crash, missing file, VS Code
loses focus, browser doesn't load, typing into wrong window). States: SUCCESS, RETRY, REPAIR, PAUSE,
ABORT. V1 prefers: pause workflow, log, let developer resume. No magical recovery.

## Browser automation

Playwright for frontends / API docs (FastAPI Swagger, React, Django, local web apps), in the same
action system.

## Recording

`RecordingController`; simplest reliable Mac option (FFmpeg / OBS / native capture). Capture screen
+ narration audio; 1920x1080, 30fps. No webcam.

## Audio

Narration = clean source audio, muxed directly into output; no microphone dependency.

## Video processing

`VideoProcessor` via FFmpeg: combine video + narration, normalize audio, trim, concatenate scenes,
encode MP4, add silence. Not in V1: intros, music, captions, B-roll, zooms, transitions.

## Scene recording

Record scenes separately (scene_001.mp4, ...) then concatenate into tutorial.mp4 — allows retrying
individual scenes, repairing failures, easier sync.

## Output directory

```
output/<project-name>/
  repository_analysis.json
  tutorial_plan.json
  script.json
  timeline.json
  audio/scene_001.wav ...
  scenes/scene_001.mp4 ...
  logs/
  tutorial.mp4
```

## Logging

Log AI operations, repository operations, scene generation, actions, commands, typing, recording,
failures, timings. Never log secrets.

## Configuration

`.env` (GEMINI_API_KEY, TTS_API_KEY) plus options: typing_speed, voice, resolution, fps,
recording_directory, workspace_directory, audience, target_duration.

## CLI (before any web app)

`tutorial-ai analyze | plan | script | prepare | record | build`, e.g.
`uv run tutorial-ai analyze https://github.com/user/project`, eventually
`uv run tutorial-ai build --repo ... --audience beginner --title "..."`.

## Project structure (target)

```
tutorial_ai/
  cli.py  config.py
  models/ repository.py tutorial.py scene.py action.py timeline.py recording.py
  providers/ai/ base.py gemini.py
  providers/tts/ base.py provider.py
  repository/ clone.py scanner.py analyzer.py
  tutorial/ planner.py script_writer.py scene_builder.py
  automation/ orchestrator.py vscode.py terminal.py browser.py typing.py actions.py state.py
  audio/ generator.py duration.py
  timeline/ builder.py scheduler.py
  recording/ recorder.py scenes.py
  video/ processor.py
  security/ command_validator.py
  utils/
tests/  output/  workspace/  pyproject.toml  README.md  .env.example
```

## Testing

Test non-GUI parts: repo scanning, file filtering, Pydantic validation, scene/action schemas,
dangerous command rejection, timeline calculations, typing-duration calculations, configuration,
AI response validation. Never call Gemini in normal tests — mock the provider.

## Phases

- **Phase 1** — uv project, structure, config, Pydantic models, AIProvider, GeminiProvider,
  clone/scanner, repository analysis, `tutorial-ai analyze` → `repository_analysis.json`.
- **Phase 2** — TutorialPlanner, ScriptWriter, SceneBuilder; `tutorial-ai plan`, `tutorial-ai script`
  → tutorial_plan.json, script.json, scenes.json.
- **Phase 3** — TTS; narration audio per scene; measure real durations → audio/scene_NNN.wav.
- **Phase 4** — Action schema, action generator, timeline builder, HumanTypingEngine → timeline.json.
  No VS Code control until the structured plan can be inspected.
- **Phase 5** — VS Code local automation. Trivial test: open VS Code, create main.py, type
  `print("Hello from AI")`, save, open terminal, run `python main.py`, confirm output.
- **Phase 6** — Narration sync; test a 60–90 second recorded video.
- **Phase 7** — First real tutorial: FastAPI Hello World, then FastAPI File Upload API.
  Don't test on a huge repo first.
- **Phase 8** — Scene retries, browser automation, video concatenation, error handling, recording
  recovery.

## Only after local V1 works: cloud

Control plane (Web UI, FastAPI API, PostgreSQL, job queue, object storage, AI providers, GitHub
integration) + ephemeral Linux render workers (Chromium, code-server, Playwright, Git, Python, Node,
uv, FFmpeg, virtual display/audio), one isolated environment per job: clone, load tutorial, record,
upload, terminate. Don't build yet.

Security for cloud: run repos in isolated containers/VMs; never run user code on the API server;
CPU/RAM/disk limits, execution timeouts, restricted credentials and network, ephemeral FS; never
expose platform credentials to repo code.

## GitHub

V1: public GitHub URL or local path. Private repos via GitHub App later — not a blocker.

## Quality standard

One excellent tutorial over hundreds of bad ones. Prioritize correct teaching, natural narration,
accurate code, good pacing, stable automation, recoverability, clear architecture — over scale,
feature count, infrastructure.

## Working agreement

- Work incrementally; don't generate 100 files at once.
- Before each phase: briefly explain what, why, which files. Then implement.
- After: run tests, run the CLI, fix errors.
- No unidentified placeholders, no fake API responses, don't claim things work untested.
- Readable code, type hints, async only where it helps, Pydantic where appropriate, clear names,
  comments only where they add value.
- Stop after each phase and report: architecture, important files, install steps, env vars, exact
  commands, example output, tests performed, known limitations, what the next phase adds.
