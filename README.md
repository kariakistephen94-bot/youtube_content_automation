# tutorial-ai

Turn a GitHub repository or local project into a YouTube-style programming tutorial video.

The full product brief and phase plan live in [docs/PROJECT_BRIEF.md](docs/PROJECT_BRIEF.md).

**Status:** the whole local V1 pipeline exists: analysis → plan → script → scenes → rehearsal →
narration audio → timeline → VS Code playback + screen recording → tutorial.mp4, with a local web
UI (GitHub sign-in, live job logs, a stage-by-stage pipeline view, audio/video players).

## Setup

Requirements: macOS, [uv](https://docs.astral.sh/uv/), git. (uv installs Python 3.12 itself.)

```bash
uv sync                  # install dependencies into .venv
cp .env.example .env     # then set GEMINI_API_KEY (or put it in .env.local)
```

The default AI provider is **OpenAI** (`OPENAI_API_KEY`, model `OPENAI_MODEL`, default
`gpt-5.4-mini`; narration via `gpt-4o-mini-tts`). Set `AI_PROVIDER=gemini` with `GEMINI_API_KEY`
to use Gemini instead (AI Studio key; `GOOGLE_GENAI_USE_VERTEXAI=true` for a Vertex AI key).
`TTS_PROVIDER` picks the voice: `openai-tts` (default), `macos-say` (free, offline) or `gemini-tts`.
`.env.local` overrides `.env`.

## Web UI

Two processes: the Python API and the Next.js frontend.

```bash
uv run tutorial-ai serve            # API on http://127.0.0.1:8765 (local only)
cd web && npm install && npm run dev   # UI on http://localhost:3100
```

Or start both processes with the included runner:

```bash
./run.sh
```

The runner installs the web dependencies when needed and stops the API when you press `Ctrl-C`.
Set `API_PORT` to use a different local API port.

The UI lets you connect GitHub, pick a repository (including private ones), run analyze → plan →
script with a live log, read the script as a screenplay, and run the VS Code test.

### Connect with GitHub (one-time setup)

Sign-in uses GitHub's OAuth **device flow**, so there is no callback server and no client secret.

1. Go to https://github.com/settings/developers → **OAuth Apps** → **New OAuth App**.
2. Application name: `tutorial-ai (local)`. Homepage URL and Authorization callback URL:
   `http://localhost:3100` (required fields; not used by device flow).
3. Click **Register application**, then tick **Enable Device Flow** and save.
4. Copy the **Client ID** into `.env.local`: `GITHUB_CLIENT_ID=...` and restart `tutorial-ai serve`.
5. In the UI click **Connect with GitHub**, enter the code shown on github.com/login/device.

The token is stored in `~/.config/tutorial-ai/github_token.json` (owner-only permissions) and never
sent to the browser. OAuth Apps need the `repo` scope to read private repositories (GitHub has no
read-only variant for OAuth Apps); tutorial-ai only ever reads and clones. When cloning, the token is
passed to git through environment config, not the remote URL, so it is not saved in the clone.

## Usage

```bash
# Analyze a public GitHub repository
uv run tutorial-ai analyze https://github.com/owner/repo

# Analyze a local project (read-only; the directory is never modified)
uv run tutorial-ai analyze examples/fastapi-file-upload

# See exactly what would be sent to the AI, without calling it (no API key needed)
uv run tutorial-ai analyze examples/fastapi-file-upload --dry-run
```

Options: `--name` (output folder name), `--output-dir`, `--refresh` (re-clone a GitHub repo),
`--dry-run`, `--verbose`.

```bash
# Plan the tutorial (PROJECT is the folder name under output/)
uv run tutorial-ai plan fastapi-file-upload \
  --title "Build a File Upload API with FastAPI" --audience beginner --duration 25

# Write the script section by section, then build scenes.json.
# Resumable: re-running continues after the last completed section.
uv run tutorial-ai script fastapi-file-upload
uv run tutorial-ai script fastapi-file-upload --from-section 4   # rewrite 4 onwards
uv run tutorial-ai script fastapi-file-upload --restart          # rewrite everything

# Rebuild scenes.json from script.json without calling the AI
uv run tutorial-ai scenes fastapi-file-upload --max-scene-seconds 120

# Run the script's code off camera (files, commands, servers, tests) - no AI, no recording
uv run tutorial-ai rehearse fastapi-file-upload

# Narration audio, one clip per segment (macOS voice by default; --provider gemini-tts for Gemini)
uv run tutorial-ai narrate fastapi-file-upload

# Compile actions (typing only the diff) and the timeline from the measured audio
uv run tutorial-ai timeline fastapi-file-upload

# Record in VS Code (screen capture + narration muxed). Hands off the keyboard!
uv run tutorial-ai record fastapi-file-upload                 # all scenes
uv run tutorial-ai record fastapi-file-upload --scene scene_002
uv run tutorial-ai record fastapi-file-upload --from-scene scene_003

# Join the scenes into output/<project>/tutorial.mp4
uv run tutorial-ai build fastapi-file-upload
```

### macOS permissions for recording

Give the app you run tutorial-ai from (Terminal, iTerm, Claude, VS Code) these permissions in
System Settings > Privacy & Security, then quit and reopen that app:

- **Accessibility** - to type into VS Code and move the mouse in the browser.
- **Screen & System Audio Recording** - for FFmpeg's screen capture.

Browser scenes (`open_browser`, `try_endpoint`) drive **Google Chrome** (install it in
/Applications) through Playwright, on a separate profile under `~/.tutorial-ai/chrome`, so your own
Chrome windows are never touched.

Ports the tutorial uses (e.g. 8000 for FastAPI, 3000 for Next.js) must be free; recording and
rehearsal check this first and tell you which port is taken. Only one recording can run at a time
(CLI or web UI): a second one is refused instead of killing the first one's screen capture.

### Output

```
output/<project>/
  repository_analysis.json   # the analysis (RepositoryAnalysis model)
  repository_context.md      # exactly what the AI was shown
  tutorial_plan.json         # sections, simplifications, final architecture
  script.json                # beats: narration + one on-screen action each; convergence report
  scenes.json                # recordable scenes: narration segments, compiled actions
  rehearsal.json             # what worked / failed when the script's code was run off camera
  narration.json + audio/    # one WAV per narration segment, measured durations
  timeline.json              # when each narration line and action happens, per scene
  recordings.json + scenes/  # scene_NNN.mp4 (narration muxed) and scene_NNN.raw.mp4 (silent capture)
  tutorial.mp4               # the final video
  logs/<command>.log         # full debug log per command (secrets redacted)
~/.tutorial-ai/workspace/<repo>/original/   # shallow clone (reference); tutorial_build/ and rehearsal/ live beside it
```

## Configuration

Set in `.env` or the environment. See [.env.example](.env.example) for all options.

| Variable | Default | Purpose |
| --- | --- | --- |
| `AI_PROVIDER` | `openai` | `openai` or `gemini` |
| `OPENAI_API_KEY` | — | Required when the provider is openai |
| `OPENAI_MODEL` | `gpt-5.4-mini` | Any chat model with JSON-schema output |
| `TTS_PROVIDER` | `openai-tts` | `openai-tts`, `macos-say`, `gemini-tts` |
| `GEMINI_API_KEY` / `GEMINI_MODEL` | — / `gemini-3.6-flash` | Only when the provider is gemini |
| `TUTORIAL_AI_MAX_CONTEXT_TOKENS` | `120000` | Budget for repository content sent to the AI |
| `TUTORIAL_AI_MAX_FILE_BYTES` | `200000` | Larger files are listed but never sent |
| `TUTORIAL_AI_OUTPUT_DIRECTORY` | `output` | |
| `TUTORIAL_AI_WORKSPACE_DIRECTORY` | `~/.tutorial-ai/workspace` | outside the repo so `uv init` in tutorials cannot touch this project |

## How analysis works

1. **Resolve source** (`repository/clone.py`) — shallow-clone a public GitHub URL into
   `workspace/<name>/original/`, or read a local directory in place.
2. **Scan** (`repository/scanner.py`) — list files via `git ls-files` (respects `.gitignore`) or a
   pruned directory walk, then classify each file. Content is never sent for: ignored directories
   (`.git`, `node_modules`, `.venv`, `dist`, `build`, `coverage`, ...), possible secrets (`.env`,
   keys, certificates), lockfiles, binaries/media, generated code, large data files, and files
   over the size limit. They still appear in the file tree, marked with the reason.
3. **Build context** (`repository/context.py`) — include file contents in teaching-priority order
   (README → manifests → entry points → source → config → tests → docs) until the token budget is
   reached, truncating or omitting what doesn't fit.
4. **Analyze** (`providers/ai/`) — the AI returns JSON constrained by the `RepositoryInsights`
   schema; responses that fail Pydantic validation are sent back with the errors (up to 2 retries).
5. **Verify** (`repository/analyzer.py`) — AI claims are checked against the real repository:
   non-existent files are removed, dependencies are reconciled with the parsed manifests, and every
   correction is recorded in `validation_warnings`.

## How planning and scripting work

- **Plan** (`tutorial/planner.py`) — one AI call turns the analysis into sections ordered for
  understanding (minimal running version first, structure introduced when it's needed), plus the
  simplifications made and the final architecture. Your title/audience are applied, section
  durations are summed, and unknown files are reported.
- **Script** (`tutorial/script_writer.py`) — one AI call per section, in order. Each call sees the
  exact workspace files produced so far and the relevant original repository files. The output is
  a list of *beats*: `talk`, `write_code` (with the complete file content after the beat — Phase 4
  types only the diff), `highlight`, `run_command`, `stop_process`, `delete_file`, `open_browser`.
  Beats are checked by replaying the workspace (highlighted text must exist, processes must be
  running before they are stopped, …) and problems are sent back to the AI once for repair.
  AI-sounding phrases ("delve", "unlock the power", …) fail validation and are rewritten.
  The script is saved after every section; the final files are compared with the repository
  (`convergence` in script.json).
- **Scenes** (`tutorial/scene_builder.py`, no AI) — sections become scenes (split at narrated beats
  when longer than `--max-scene-seconds`), narration becomes segments of whole sentences, and
  durations are estimated (≈150 wpm narration, 7 chars/s typing, overlapping).

## How recording works

- **Rehearsal** (`tutorial/rehearsal.py`) replays every section in a throwaway folder: writes files
  (syntax-checked), runs allowlisted commands, starts servers and probes their URLs, runs the
  repository's test commands at the end. Nothing is recorded, no AI is called.
- **Narration** (`audio/generator.py`, `providers/tts/`) generates one WAV per narration segment
  and measures it. Providers: `macos-say` (default, offline) and `gemini-tts`.
- **Actions** (`automation/actions.py`) compile each script beat into strict actions. A
  `write_code` beat becomes line-level edits against the file as it is on disk (go to line / go to
  end, type only the new lines, delete removed lines), so the viewer never sees a file retyped.
- **Timeline** (`timeline/builder.py`) schedules narration segments and actions per beat from the
  measured audio lengths.
- **Playback** (`automation/executor.py`, `automation/orchestrator.py`) performs the actions in
  the real VS Code, beat by beat, waiting for each beat's narration to finish; it records the
  actual start time of every narration segment. The integrated shell reports each command’s actual exit status before the next command
  is typed. Failed commands pause the recording immediately, including missing executables.
- **Browser** (`automation/browser.py`) shows the running app in Google Chrome and, for a
  `try_endpoint` beat, uses FastAPI's Swagger UI on camera: expands the endpoint, clicks Try it
  out, fills the fields (file uploads use a small sample file, no OS dialog), clicks Execute and
  shows the response. The real pointer travels to each button (Quartz mouse events) so the
  recording shows the clicks; the rehearsal sends the same request directly and checks the path
  and method against the app's `/openapi.json`.
- **Recording** (`recording/`) captures the screen with FFmpeg (AVFoundation, 1080p30, no
  microphone) per scene, then `video/processor.py` muxes the clean narration clips at their
  recorded offsets (loudness-normalized) and concatenates the scenes into `tutorial.mp4`.
- Failure = pause: the session stops at the failing scene with the reason; re-run from that scene
  and the earlier scenes' workspace state is rebuilt off camera first.

## Project layout

```
src/tutorial_ai/
  cli.py                 # Typer CLI
  config.py              # Settings (env / .env)
  storage.py             # output layout, atomic JSON persistence
  models/                # Pydantic models: repository, tutorial, script, scene, action, timeline, recording
  providers/ai/          # AIProvider interface, shared prompts, GeminiProvider
  repository/            # clone, scanner, manifests, context, analyzer
  tutorial/              # planner, script_writer, scene_builder, workspace_state, rehearsal
  automation/            # typing engine, macOS input, VS Code/terminal controllers, action compiler, executor, player
  audio/, timeline/      # narration generation, timeline builder
  recording/, video/     # FFmpeg screen recorder, recording session, mux/concat
  api/                   # FastAPI backend for the web UI (jobs with live logs)
  providers/tts/         # TTSProvider interface: macos-say, gemini-tts
  utils/                 # logging (with secret redaction), text helpers
tests/                   # no network / no real AI calls
examples/fastapi-file-upload/   # small real FastAPI project used for testing
```

## Tests

```bash
uv run pytest
```

Tests never call Gemini; the provider is tested with a fake client and the analyzer with a fake
provider.

## Notes

- **Intel Macs:** `cryptography>=49` ships no x86_64 macOS wheels, so `pyproject.toml` constrains it
  to `<49` on that platform only.

### Recording environment checks

The sandbox image includes Node.js 22 with npm/npx, Python, uv, FFmpeg, git, and yt-dlp.
After updating the Dockerfile, run `uv run tutorial-ai sandbox build` once to download and install
the updated tools in Docker. Base tools used by a tutorial are checked before recording starts;
project dependencies still belong to the tutorial's install commands. Missing prerequisites
stop the run with their names instead of wasting a recording.

The isolated VS Code profile uses a Bash prompt hook to report the exit status of each command
actually typed on screen. A nonzero exit status pauses recording immediately; no completion report
is a timeout, never success. Working-directory changes persist in that shell. Terminal focus uses
a dedicated shortcut, without opening or typing in the command palette.
