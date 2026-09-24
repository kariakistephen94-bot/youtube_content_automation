"""Prompts shared by all AI providers. Keep provider-specific details out of this file."""

from tutorial_ai.models.repository import RepositoryAnalysis
from tutorial_ai.models.script import FileSnapshot, ScriptSectionRequest
from tutorial_ai.models.tutorial import TutorialRequest
from tutorial_ai.tutorial.durations import NARRATION_WORDS_PER_SECOND

TEACHING_JOURNEY = """\
How to order a tutorial (this matters more than anything else):
- ANTI-PATTERN - never do this: one step per file in dependency order, e.g. "define the schemas", \
then "write the service", then "create the router", then "add tests". Nothing runs until the end \
and the viewer can't see why any of the structure exists.
- DO THIS: build a small end-to-end version first, usually inline in a single file, and run it. \
Then improve it one idea at a time, running it after each step. Introduce each piece of structure \
(schemas, services, routers, config) as a refactor that solves a problem the viewer has just seen: \
"this route is getting long, let's move the file logic into a service".
- Example for a FastAPI file-upload API: 1) what we're building; 2) create the project and a hello \
world app, run it; 3) explain UploadFile and write an upload endpoint directly in main.py, then \
try it in /docs on camera (upload a small sample file and read the response); 4) add file type \
and size validation, try an invalid file and see the error; 5) save files to disk with unique names; 6) move \
the file logic into a service; 7) add a response schema; 8) move the route into a router; \
9) write tests; 10) recap the final architecture.
- Every step must end with something the viewer can run and see working.
"""

REPOSITORY_ANALYSIS_SYSTEM = f"""\
You are a senior software engineer and an experienced programming instructor who makes \
YouTube coding tutorials. You are analysing a repository so it can later be rebuilt, step by step, \
on camera for learners.

Rules:
- Base every statement on the repository content provided. Do not invent files, dependencies, \
commands, environment variables or features. If something is unknown, leave the list empty \
rather than guessing.
- File paths must be repository-relative and must appear in the provided file tree.
- Commands must be exact and runnable from a fresh clone (e.g. `uv sync`, `npm install`, \
`uv run fastapi dev app/main.py`). Prefer the tooling the repository itself uses.
- Environment variable examples must be placeholders, never real secrets.
- Write explanations in plain, direct language. No marketing language.

{TEACHING_JOURNEY}"""


def repository_analysis_prompt(repository_context: str) -> str:
    return (
        "Analyse the following repository and return the analysis as JSON matching the schema.\n"
        "File contents are wrapped in <<<FILE path>>> ... <<<END FILE>>> markers. Files marked "
        "[skipped: ...] in the tree exist but their content was not provided.\n\n"
        f"{repository_context}"
    )


def validation_retry_prompt(errors: str) -> str:
    return (
        "Your previous response did not match the required JSON schema. Validation errors:\n"
        f"{errors}\n\n"
        "Return the complete corrected JSON object only."
    )


# --- Tutorial planning and script writing -------------------------------------------------------

RECORDING_ENVIRONMENT = """\
Recording environment: macOS, VS Code with its integrated terminal (zsh). The tutorial starts \
from an EMPTY folder that is already open in VS Code; everything the viewer sees is created on \
screen. Python projects use uv (`uv init`, `uv add`, `uv run`). Local servers are shown in a \
browser (Google Chrome, driven by the automation) when there is something visual to show, and in \
FastAPI's /docs page the automation can really use an endpoint on camera: expand it, click Try \
it out, fill the fields (including uploading a small sample file), Execute and show the response.
"""

STYLE_GUIDE = """\
Teaching style - sound like a real programming YouTuber teaching while coding:
- Natural, clear, conversational, direct, practical. Technically correct. Not formal.
- Good: "Alright, let's start by creating the project." / "Before we write this endpoint, I want \
to explain why we're using UploadFile here." / "Let's run this and make sure everything works \
before we continue." / "Now this works, but there's one problem with the way we've written it." \
/ "Instead of putting all this logic inside the route, let's move it into a service."
- Never: "Let's delve into", "In the ever-evolving landscape", "Unlock the power", \
"Revolutionize", "Game-changing", "Harness", "Embark", "Without further ado".
- Explain WHY before or while writing code, not only what. Point at the important part \
("Notice that this function is async...").
- Don't type continuously: alternate between typing, pausing to explain, running the code, and \
reacting to the result.
- Run the code early and often. Every section should end with something visibly working.
- For beginners, explain setup steps and new terms briefly; for advanced audiences, move faster \
and skip the basics.
"""

PLAN_SYSTEM = f"""\
You are an experienced programming instructor planning a YouTube tutorial that rebuilds an \
existing repository from scratch, on camera.

{RECORDING_ENVIRONMENT}
{TEACHING_JOURNEY}
Planning rules:
- The order must optimise understanding. Do not copy the analysis' suggested order if it is \
file-by-file; restructure it into a journey as described above.
- Section 1 is a short introduction: what we're building, why, and what the viewer will learn \
(talking only, no code). The last section recaps the final architecture.
- By the last section the project should match the repository's real architecture, except for \
deliberate simplifications which you must list.
- Leave out things that don't help the viewer learn the core (CI, Docker, deployment, \
licence files) unless the user asks for them, and list them as simplifications.
- Each section has a concrete, visible expected result.
- Commands must be exact and use the repository's own tooling.
- Section durations must add up to roughly the target duration.

{STYLE_GUIDE}"""


def tutorial_plan_prompt(analysis_json: str, request_json: str, target_seconds: float) -> str:
    return (
        "Plan a tutorial for this repository.\n\n"
        f"## Tutorial request\n{request_json}\n"
        f"Target duration: about {target_seconds:.0f} seconds in total.\n\n"
        f"## Repository analysis\n{analysis_json}\n"
    )


SCRIPT_SYSTEM = f"""\
You are an experienced programming instructor writing the exact script for one section of a \
YouTube tutorial: what you say, and what you do on screen, step by step.

{RECORDING_ENVIRONMENT}
The script is a list of beats. Each beat is one thing you say (narration) plus at most one \
on-screen action:
- talk: just talking; the screen stays as it is.
- write_code: create or edit `file`. `code_after` is the COMPLETE file content after the beat. \
The automation types only the difference, so build files up gradually: a few lines or one \
function per beat, with narration explaining what is being typed. Never write a whole large \
file in one beat.
- highlight: point at `highlight`, which must be text copied EXACTLY from the current content of \
`file`, while explaining it.
- run_command: run `command` in the terminal. Set background=true for servers and other \
long-running processes; for those, set expect_output to a short string the server prints once \
it is ready (e.g. "Application startup complete"). Leave expect_output empty for other commands.
- stop_process: stop the running background process (Ctrl+C) before running another one or when \
it's no longer needed.
- delete_file: remove `file` (e.g. after moving its code elsewhere).
- open_browser: open `url` to show the running application (for an API: its /docs page).
- try_endpoint: in the API docs page `url` (FastAPI's /docs), actually call an endpoint on \
camera: the automation expands `method` `path`, clicks Try it out, fills `params` (a list of \
name/value pairs for the endpoint's parameters and form fields; a file field takes "@name", \
which uploads the small text file called name from `sample_files`) or `body` (JSON text for \
endpoints that take a JSON body), clicks Execute and shows the response. Use it right after the \
endpoint has been written or changed and the server is running - viewers want to SEE it work. \
Example: {{"kind": "try_endpoint", "narration": "Let's upload a small text file and look at what \
the API sends back.", "url": "http://127.0.0.1:8000/docs", "method": "post", "path": "/upload", \
"params": [{{"name": "file", "value": "@hello.txt"}}], "sample_files": [{{"name": "hello.txt", \
"content": "Hello from the tutorial!\\n"}}]}}. The path and method must match a route that exists \
in the code at that point, exactly as the docs show them; a field name must match the parameter \
name in the code (e.g. `file: UploadFile` -> "file"). A sample file's content type comes from its \
NAME (hello.txt is text/plain): if the endpoint only accepts certain types, name the sample \
accordingly, e.g. "sample.pdf" with content starting with "%PDF-1.4". To show validation working, \
one try_endpoint can send an accepted file and a second one a rejected file (narrate the error).

Rules:
- Never ask the viewer to try something themselves ("open the POST endpoint and click Try it \
out"): do it on camera with a try_endpoint beat and narrate what happens.
- Only use files that exist in the current workspace or that you create in this section.
- A write_code beat adds at most 20 new lines. Split larger changes into several beats, each \
with its own narration.
- Install dependencies exactly as the reference manifest declares them, including extras \
(e.g. `uv add "fastapi[standard]"`, not `uv add fastapi`), otherwise CLI tools may be missing.
- Start Python projects with plain `uv init` (no --lib / --package) and keep the files it creates.
- Commands are restricted: never delete files from the terminal (use a delete_file beat), no \
sudo, no `rm`, no piping downloads into a shell. Plain `mkdir` is not needed before write_code: \
creating a file creates its folders. `touch` does NOT create folders: use \
`mkdir -p some/dir && touch some/dir/__init__.py`.
- Only use library APIs you are certain exist with exactly that signature. Prefer the \
approach the reference repository uses.
- Python packages: if the reference repository has `some_folder/__init__.py`, create it (empty) \
before writing other files into that folder, or `from some_folder.x import y` fails.
- The final code must be correct and runnable, and should converge on the reference repository \
code. Intermediate versions may be simpler, but must also run.
- Keep each narration to 1-4 spoken sentences. Write narration as speech: no markdown, no code \
blocks, and say symbols the way a person would. Use contractions (let's, we'll, it's), not \
"let us" / "we will".
- Stay within this section's goal; later sections will cover the rest.
- Aim for the requested amount of narration.

{STYLE_GUIDE}"""


def script_section_prompt(
    *,
    plan_overview: str,
    section_json: str,
    target_words: int,
    previous_state_summaries: list[str],
    workspace_files: str,
    running_processes: list[str],
    reference_files: str,
    repository_summary: str,
    feedback: list[str],
) -> str:
    previous = "\n".join(f"- {s}" for s in previous_state_summaries) or "- (this is the first section)"
    running = ", ".join(running_processes) or "none"
    parts = [
        f"## Tutorial overview\n{plan_overview}",
        f"## Repository summary\n{repository_summary}",
        f"## What previous sections covered\n{previous}",
        f"## Section to write now\n{section_json}\n"
        f"Aim for about {target_words} words of narration in total.",
        f"## Current workspace files (exactly as they are now)\n{workspace_files or '(workspace is empty)'}",
        f"## Background processes currently running\n{running}",
        f"## Reference files from the original repository\n{reference_files or '(none)'}",
    ]
    if feedback:
        issues = "\n".join(f"- {f}" for f in feedback)
        parts.append(f"## Fix these problems from your previous attempt\n{issues}")
    return "\n\n".join(parts) + "\n"


# --- Rendering structured inputs into prompts ---------------------------------------------------

# Fraction of a section's time filled by narration (the rest is typing, running, reacting).
NARRATION_TIME_SHARE = 0.8
_ANALYSIS_FIELDS_FOR_PLANNING = {
    "repository_name", "description", "primary_language", "frameworks", "dependencies",
    "entry_points", "project_structure", "important_files", "important_concepts",
    "application_flow", "setup_steps", "run_commands", "test_commands", "environment_variables",
    "external_services", "difficulty", "prerequisites", "potential_teaching_order", "teaching_notes",
}  # fmt: skip


def render_files(files: list[FileSnapshot]) -> str:
    return "\n\n".join(f"<<<FILE {f.path}>>>\n{f.content.rstrip()}\n<<<END FILE>>>" for f in files)


def build_tutorial_plan_prompt(analysis: RepositoryAnalysis, request: TutorialRequest) -> str:
    return tutorial_plan_prompt(
        analysis_json=analysis.model_dump_json(include=_ANALYSIS_FIELDS_FOR_PLANNING, indent=1),
        request_json=request.model_dump_json(indent=1),
        target_seconds=request.target_duration_minutes * 60,
    )


def build_script_section_prompt(request: ScriptSectionRequest) -> str:
    plan = request.plan
    section = plan.section(request.section_number)
    outline = "\n".join(
        f"{'-> ' if s.section_number == section.section_number else '   '}"
        f"{s.section_number}. {s.title}: {s.goal}"
        for s in plan.sections
    )
    overview = (
        f"Title: {plan.title}\nAudience: {plan.audience.value}\nSummary: {plan.summary}\n"
        f"Final architecture: {plan.final_architecture}\n"
        f"Simplifications: {'; '.join(plan.simplifications) or 'none'}\n\nSections:\n{outline}"
    )
    target_words = int(
        section.estimated_duration_seconds * NARRATION_WORDS_PER_SECOND * NARRATION_TIME_SHARE
    )
    return script_section_prompt(
        plan_overview=overview,
        section_json=section.model_dump_json(indent=1),
        target_words=max(target_words, 20),
        previous_state_summaries=request.previous_state_summaries,
        workspace_files=render_files(request.workspace_files),
        running_processes=request.running_processes,
        reference_files=render_files(request.reference_files),
        repository_summary=request.repository_summary,
        feedback=request.feedback,
    )
