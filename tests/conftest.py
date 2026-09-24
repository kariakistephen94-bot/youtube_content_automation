from pathlib import Path

import pytest

from tutorial_ai.config import Settings
from tutorial_ai.models.repository import RepositoryInsights
from tutorial_ai.models.script import ScriptSectionDraft, ScriptSectionRequest
from tutorial_ai.models.tutorial import TutorialPlan, TutorialRequest
from tutorial_ai.providers.ai.base import AIProvider

EXAMPLE_REPO = Path(__file__).parent.parent / "examples" / "fastapi-file-upload"


def write(root: Path, rel: str, content: str | bytes) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content)
    return path


@pytest.fixture
def sample_repo(tmp_path: Path) -> Path:
    """A small repo containing both useful files and everything that must be filtered out."""
    root = tmp_path / "sample"
    write(root, "README.md", "# Sample\nA sample app.\n")
    write(root, "pyproject.toml", '[project]\nname="sample"\ndependencies=["fastapi>=0.110", "httpx"]\n')
    write(root, "app/main.py", "from fastapi import FastAPI\napp = FastAPI()\n")
    write(root, "app/routes/items.py", "def list_items():\n    return []\n")
    write(root, "tests/test_main.py", "def test_ok():\n    assert True\n")
    write(root, "uv.lock", "version = 1\n" * 100)
    write(root, ".env", "SECRET_KEY=super-secret-value\n")
    write(root, ".env.example", "SECRET_KEY=change-me\n")
    write(root, "static/logo.png", b"\x89PNG\r\n\x1a\n" + b"\0" * 100)
    write(root, "data/big.csv", "a,b\n" + "1,2\n" * 10_000)
    write(root, "node_modules/left-pad/index.js", "module.exports = 1\n")
    write(root, ".venv/lib/site.py", "x = 1\n")
    write(root, "dist/bundle.js", "var a;\n")
    write(root, "app/vendor.min.js", "var a;\n")
    write(root, "blob.dat", b"abc\0def")
    return root


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        output_directory=tmp_path / "output",
        workspace_directory=tmp_path / "workspace",
    )


def make_insights(**overrides) -> RepositoryInsights:
    data = {
        "description": "A FastAPI upload API.",
        "primary_language": "Python",
        "frameworks": ["FastAPI"],
        "dependencies": [{"name": "fastapi", "version": ">=0.115", "purpose": "Web framework"}],
        "entry_points": [{"path": "app/main.py", "description": "ASGI app"}],
        "important_files": [
            {"path": "app/main.py", "role": "App setup", "teaching_notes": "Start here"}
        ],
        "important_concepts": [
            {"name": "UploadFile", "explanation": "Streams uploads", "related_files": ["app/main.py"]}
        ],
        "application_flow": ["Request hits /uploads", "File is validated and saved"],
        "setup_steps": ["uv sync"],
        "run_commands": [{"command": "uv run fastapi dev app/main.py", "description": "Dev server"}],
        "test_commands": [{"command": "uv run pytest", "description": "Run tests"}],
        "environment_variables": [],
        "external_services": [],
        "difficulty": "beginner",
        "prerequisites": ["Basic Python"],
        "potential_teaching_order": [
            {"title": "Create the app", "goal": "A running server", "files": ["app/main.py"]}
        ],
    }
    data.update(overrides)
    return RepositoryInsights.model_validate(data)


def make_plan(**overrides) -> TutorialPlan:
    def section(n: int, title: str, files: list[str], seconds: float = 120) -> dict:
        return {
            "section_number": n, "title": title, "goal": f"goal {n}", "files_involved": files,
            "expected_result": f"result {n}", "estimated_duration_seconds": seconds,
        }

    data = {
        "title": "Build a File Upload API with FastAPI",
        "audience": "beginner",
        "difficulty": "beginner",
        "summary": "We build an upload API.",
        "learning_objectives": ["Handle uploads"],
        "final_architecture": "app/ with routes and services",
        "estimated_duration_seconds": 360,
        "sections": [
            section(1, "What we're building", []),
            section(2, "First FastAPI app", ["app/main.py"]),
            section(3, "Upload endpoint", ["app/main.py"]),
        ],
    }
    data.update(overrides)
    return TutorialPlan.model_validate(data)


MAIN_V1 = 'from fastapi import FastAPI\n\napp = FastAPI()\n'
MAIN_V2 = MAIN_V1 + '\n\n@app.get("/")\ndef health():\n    return {"status": "ok"}\n'
MAIN_V3 = MAIN_V2 + '\n\n@app.post("/uploads")\nasync def upload(file: UploadFile):\n    return {"name": file.filename}\n'


def make_sections() -> dict[int, ScriptSectionDraft]:
    """A small but realistic 3-section script matching make_plan()."""
    return {
        1: ScriptSectionDraft.model_validate({
            "beats": [
                {"kind": "talk", "narration": "Alright, today we're building a file upload API with FastAPI."},
                {"kind": "talk", "narration": "By the end you'll be able to upload PDFs and images. Let's get started."},
            ],
            "state_summary": "Nothing created yet.",
        }),
        2: ScriptSectionDraft.model_validate({
            "beats": [
                {"kind": "run_command", "narration": "First, let's add FastAPI to the project and create the app package.",
                 "command": "uv add 'fastapi[standard]' && mkdir -p app && touch app/__init__.py"},
                {"kind": "write_code", "narration": "Now let's create the app.", "file": "app/main.py",
                 "code_after": MAIN_V1},
                {"kind": "write_code", "narration": "And a simple health check route.", "file": "app/main.py",
                 "code_after": MAIN_V2},
                {"kind": "highlight", "narration": "Notice this decorator registers the route.",
                 "file": "app/main.py", "highlight": '@app.get("/")'},
                {"kind": "run_command", "narration": "Let's run it.", "command": "uv run fastapi dev app/main.py",
                 "background": True, "expect_output": "Application startup complete"},
                {"kind": "open_browser", "narration": "", "url": "http://127.0.0.1:8000/docs"},
            ],
            "state_summary": "app/main.py has a health route; the dev server is running.",
        }),
        3: ScriptSectionDraft.model_validate({
            "beats": [
                {"kind": "write_code", "narration": "Now the upload endpoint.", "file": "app/main.py",
                 "code_after": MAIN_V3},
                {"kind": "stop_process", "narration": "Let's stop the server for now."},
            ],
            "state_summary": "Upload endpoint added.",
        }),
    }


class FakeProvider(AIProvider):
    """Scripted provider. `sections` maps section number to a draft, or to a list of drafts
    returned on successive calls (to exercise the repair loop)."""

    name = "fake"
    model = "fake-model"

    def __init__(
        self,
        insights: RepositoryInsights | None = None,
        plan: TutorialPlan | None = None,
        sections: dict | None = None,
    ) -> None:
        self.insights = insights or make_insights()
        self.plan = plan or make_plan()
        self.sections = sections if sections is not None else make_sections()
        self.received_context: str | None = None
        self.plan_requests: list[TutorialRequest] = []
        self.section_requests: list[ScriptSectionRequest] = []

    def analyze_repository(self, repository_context: str) -> RepositoryInsights:
        self.received_context = repository_context
        return self.insights

    def create_tutorial_plan(self, analysis, request: TutorialRequest) -> TutorialPlan:
        self.plan_requests.append(request)
        return self.plan

    def create_script_section(self, request: ScriptSectionRequest) -> ScriptSectionDraft:
        self.section_requests.append(request)
        outcome = self.sections[request.section_number]
        if isinstance(outcome, list):
            return outcome.pop(0) if len(outcome) > 1 else outcome[0]
        return outcome


@pytest.fixture(autouse=True)
def isolated_config_dir(tmp_path_factory, monkeypatch):
    """Never read or write the real ~/.config/tutorial-ai (e.g. a real GitHub token) in tests."""
    from tutorial_ai import github

    config_dir = tmp_path_factory.mktemp("config")
    monkeypatch.setattr(github, "get_settings", lambda: Settings(_env_file=None, config_directory=config_dir))
    return config_dir
