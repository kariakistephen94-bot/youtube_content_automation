"""The recording sandbox: record a tutorial in a container instead of on this Mac's screen.

The container (sandbox/Dockerfile) has its own virtual screen with VS Code, a terminal and Chrome.
`tutorial-ai record` runs *inside* it, exactly as it would on the Mac, and writes the recorded
scenes into the project's output folder, which is mounted from the host. The Mac's keyboard,
mouse and windows are never touched, and nothing can steal focus mid-recording. A read-only live
view (noVNC) is published on localhost so the web UI can show what is happening.
"""

import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel

from tutorial_ai.utils.logging import get_logger

log = get_logger("sandbox")

IMAGE = "tutorial-ai-sandbox:latest"
CONTAINER = "tutorial-ai-sandbox"
LIVE_VIEW_PORT = 6080
LIVE_VIEW_URL = (f"http://localhost:{LIVE_VIEW_PORT}/vnc.html"
                 "?autoconnect=1&view_only=1&resize=scale&reconnect=1&reconnect_delay=1000")
REPO_ROOT = Path(__file__).resolve().parents[2]
CONTAINER_APP = "/opt/tutorial-ai"

# Where Docker Desktop and OrbStack put the CLI when it is not on the PATH of the process that
# runs the API (a server started before the install, or from an app without a login shell).
DOCKER_CANDIDATES = (
    Path("~/.orbstack/bin/docker").expanduser(),
    Path("/usr/local/bin/docker"),
    Path("/opt/homebrew/bin/docker"),
    Path("/Applications/OrbStack.app/Contents/MacOS/xbin/docker"),
    Path("/Applications/Docker.app/Contents/Resources/bin/docker"),
)

INSTALL_HELP = ("The recording sandbox needs a container runtime. Install OrbStack (orbstack.dev) or "
                "Docker Desktop, start it once, then try again.")


class SandboxError(RuntimeError):
    pass


class SandboxStatus(BaseModel):
    docker: str | None
    daemon_running: bool
    image_built: bool
    running: bool
    live_view_url: str = LIVE_VIEW_URL
    message: str


def find_docker() -> str | None:
    found = shutil.which("docker")
    if found:
        return found
    return next((str(p) for p in DOCKER_CANDIDATES if p.exists()), None)


def _docker(docker: str, *args: str, timeout: float = 15) -> subprocess.CompletedProcess:
    return subprocess.run([docker, *args], capture_output=True, text=True, timeout=timeout)


def status() -> SandboxStatus:
    docker = find_docker()
    if docker is None:
        return SandboxStatus(docker=None, daemon_running=False, image_built=False, running=False,
                             message=INSTALL_HELP)
    try:
        daemon = _docker(docker, "info", "--format", "{{.ServerVersion}}", timeout=10).returncode == 0
    except subprocess.TimeoutExpired:
        daemon = False
    if not daemon:
        return SandboxStatus(docker=docker, daemon_running=False, image_built=False, running=False,
                             message="Docker is installed but not running. Open OrbStack or Docker Desktop.")
    image = _docker(docker, "image", "inspect", IMAGE, "--format", "{{.Id}}").returncode == 0
    running = bool(_docker(docker, "ps", "-q", "--filter", f"name=^{CONTAINER}$").stdout.strip())
    message = ("Ready." if image else
               "Build the sandbox image once (about 2-3 GB, takes a few minutes).")
    return SandboxStatus(docker=docker, daemon_running=True, image_built=image, running=running, message=message)


def require_ready() -> str:
    state = status()
    if state.docker is None or not state.daemon_running:
        raise SandboxError(state.message)
    if not state.image_built:
        raise SandboxError("The sandbox image is not built yet: run `tutorial-ai sandbox build` "
                           "(or use Build sandbox in the web UI).")
    return state.docker


def _stream(command: list[str], on_line: Callable[[str], None], cwd: Path | None = None) -> int:
    process = subprocess.Popen(command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               stdin=subprocess.DEVNULL, text=True, bufsize=1, errors="replace")
    assert process.stdout is not None
    for line in process.stdout:
        line = _ANSI.sub("", line).rstrip()
        if line:
            on_line(line)
    return process.wait()


_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def build_image(on_line: Callable[[str], None] = log.info) -> None:
    docker = find_docker()
    if docker is None:
        raise SandboxError(INSTALL_HELP)
    dockerfile = REPO_ROOT / "sandbox" / "Dockerfile"
    if not dockerfile.exists():
        raise SandboxError(f"{dockerfile} is missing")
    on_line(f"Building {IMAGE} (downloads VS Code, Chrome and Python packages the first time)")
    code = _stream([docker, "build", "--progress=plain", "-t", IMAGE, "-f", str(dockerfile), str(REPO_ROOT)],
                   on_line)
    if code != 0:
        raise SandboxError(f"Building the sandbox image failed (docker exit code {code}); see the log above.")
    on_line("Sandbox image ready.")


def run_command(docker: str, args: list[str], output_dir: Path) -> list[str]:
    """`docker run` for one tutorial-ai command inside the sandbox."""
    return [
        docker, "run", "--rm", "--name", CONTAINER, "--init",
        "--shm-size=2g",  # Chrome and VS Code need more shared memory than Docker's 64 MB default
        "-p", f"127.0.0.1:{LIVE_VIEW_PORT}:6080",
        "-v", f"{REPO_ROOT / 'src'}:{CONTAINER_APP}/src:ro",
        "-v", f"{output_dir.resolve()}:{CONTAINER_APP}/output",
        "-e", "COLUMNS=200",  # log lines unwrapped (the host strips any colour codes)
        IMAGE, *args,
    ]


def record_args(project: str, scenes: list[str] | None, from_scene: str | None, speed: str) -> list[str]:
    args = ["record", project, "--speed", speed]
    for scene in scenes or []:
        args += ["--scene", scene]
    if from_scene:
        args += ["--from-scene", from_scene]
    return args


def run(args: list[str], output_dir: Path, on_line: Callable[[str], None] = log.info) -> int:
    """Run `tutorial-ai <args>` in a fresh sandbox container; returns its exit code."""
    docker = require_ready()
    stop()  # a container left over from an interrupted run holds the name and the port
    on_line(f"Sandbox: starting (live view at {LIVE_VIEW_URL.split('?')[0]})")
    return _stream(run_command(docker, args, output_dir), on_line)


def stop() -> bool:
    """Stop and remove the sandbox container. Returns True if one was running."""
    docker = find_docker()
    if docker is None:
        return False
    try:
        removed = _docker(docker, "rm", "-f", CONTAINER, timeout=30)
    except subprocess.TimeoutExpired:
        return False
    return removed.returncode == 0 and CONTAINER in removed.stdout
