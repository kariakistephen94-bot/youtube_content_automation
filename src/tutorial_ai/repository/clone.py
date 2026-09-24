"""Resolve a user-supplied source (GitHub URL or local path) to a readable directory.

GitHub repositories are shallow-cloned into `workspace/<name>/original/`. Local paths are read in
place and never modified - analysis is read-only.
"""

import base64
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from tutorial_ai.models.repository import RepositorySource
from tutorial_ai.utils.logging import get_logger
from tutorial_ai.utils.text import slugify

log = get_logger("repository.clone")

_GITHUB_URL = re.compile(
    r"^(?:https?://)?(?:www\.)?github\.com/"
    r"(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?"
    r"(?:/tree/(?P<branch>[^/?#]+))?/?(?:[?#].*)?$"
)

CLONE_TIMEOUT_SECONDS = 300


class RepositoryError(RuntimeError):
    pass


@dataclass(frozen=True)
class GitHubRepo:
    owner: str
    name: str
    branch: str | None = None

    @property
    def clone_url(self) -> str:
        return f"https://github.com/{self.owner}/{self.name}.git"

    @property
    def web_url(self) -> str:
        return f"https://github.com/{self.owner}/{self.name}"


def parse_github_url(value: str) -> GitHubRepo | None:
    match = _GITHUB_URL.match(value.strip())
    if not match:
        return None
    return GitHubRepo(owner=match["owner"], name=match["repo"], branch=match["branch"])


def looks_like_url(value: str) -> bool:
    return value.startswith(("http://", "https://", "git@", "github.com/", "www.github.com/"))


def resolve_source(
    source: str,
    workspace_directory: Path,
    refresh: bool = False,
    github_token: str | None = None,
) -> RepositorySource:
    """`github_token` (optional) allows cloning private repositories the token can read."""
    if looks_like_url(source):
        github = parse_github_url(source)
        if github is None:
            raise RepositoryError(
                f"Unsupported repository URL: {source!r}. Supported: GitHub URLs like "
                "https://github.com/owner/repo, or a local directory path."
            )
        return _clone_github(github, source, workspace_directory, refresh, github_token)
    return _local_source(source)


def _local_source(source: str) -> RepositorySource:
    path = Path(source).expanduser().resolve()
    if not path.exists():
        raise RepositoryError(f"Local path does not exist: {path}")
    if not path.is_dir():
        raise RepositoryError(f"Local path is not a directory: {path}")
    commit, branch = git_revision(path)
    log.info("Using local repository %s", path)
    return RepositorySource(
        name=path.name, kind="local", origin=source, local_path=str(path), commit=commit, branch=branch
    )


def _clone_github(
    repo: GitHubRepo,
    origin: str,
    workspace_directory: Path,
    refresh: bool,
    github_token: str | None = None,
) -> RepositorySource:
    workspace_directory = workspace_directory.resolve()
    target = workspace_directory / slugify(repo.name) / "original"

    if target.exists() and refresh:
        _remove_inside(target, workspace_directory)

    if target.exists():
        if not (target / ".git").exists():
            raise RepositoryError(
                f"{target} exists but is not a git clone. Remove it or run with --refresh."
            )
        log.info("Reusing existing clone at %s (use --refresh to re-clone)", target)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        command = ["git", "clone", "--depth", "1"]
        if repo.branch:
            command += ["--branch", repo.branch]
        command += [repo.clone_url, str(target)]
        log.info("Cloning %s into %s%s", repo.web_url, target,
                 " (authenticated)" if github_token else "")
        _run_git(command, cwd=None, extra_env=github_auth_env(github_token))

    commit, branch = git_revision(target)
    return RepositorySource(
        name=repo.name,
        kind="github",
        origin=origin,
        local_path=str(target),
        commit=commit,
        branch=branch,
    )


def _remove_inside(path: Path, allowed_root: Path) -> None:
    """Delete a previous clone, refusing to touch anything outside the workspace directory."""
    resolved = path.resolve()
    if not resolved.is_relative_to(allowed_root) or resolved == allowed_root:
        raise RepositoryError(f"Refusing to delete {resolved}: outside workspace {allowed_root}")
    log.info("Removing previous clone %s", resolved)
    shutil.rmtree(resolved)


def github_auth_env(token: str | None) -> dict[str, str]:
    """Git config passed through the environment: authenticates HTTPS requests to github.com
    without putting the token in the remote URL, .git/config, or the process arguments."""
    if not token:
        return {}
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
        "GIT_CONFIG_VALUE_0": f"Authorization: Basic {basic}",
    }


def _run_git(command: list[str], cwd: Path | None, extra_env: dict[str, str] | None = None) -> str:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", **(extra_env or {})}  # never prompt
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=CLONE_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RepositoryError("git is not installed or not on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise RepositoryError(f"git timed out after {CLONE_TIMEOUT_SECONDS}s: {' '.join(command)}") from exc
    if result.returncode != 0:
        stderr = result.stderr.strip()
        hint = ""
        if "could not read Username" in stderr or "Authentication failed" in stderr or "not found" in stderr:
            hint = (
                " (is the URL correct? Private repositories need a connected GitHub account "
                "with access to them)"
            )
        raise RepositoryError(f"git failed: {stderr}{hint}")
    return result.stdout.strip()


def git_revision(path: Path) -> tuple[str | None, str | None]:
    """Return (commit, branch) for a git working tree, or (None, None)."""
    if not (path / ".git").exists():
        return None, None
    try:
        commit = _run_git(["git", "rev-parse", "HEAD"], cwd=path)
        branch = _run_git(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=path)
    except RepositoryError:
        return None, None
    return commit or None, (branch if branch and branch != "HEAD" else None)
