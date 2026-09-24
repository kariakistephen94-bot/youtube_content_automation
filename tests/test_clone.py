from pathlib import Path

import pytest

from tutorial_ai.repository.clone import (
    RepositoryError,
    _remove_inside,
    parse_github_url,
    resolve_source,
)


@pytest.mark.parametrize(
    ("url", "owner", "name", "branch"),
    [
        ("https://github.com/tiangolo/fastapi", "tiangolo", "fastapi", None),
        ("https://github.com/tiangolo/fastapi.git", "tiangolo", "fastapi", None),
        ("https://github.com/tiangolo/fastapi/", "tiangolo", "fastapi", None),
        ("github.com/user/my-repo", "user", "my-repo", None),
        ("https://github.com/user/repo/tree/dev", "user", "repo", "dev"),
        ("https://www.github.com/user/repo.name", "user", "repo.name", None),
    ],
)
def test_parse_github_url(url, owner, name, branch):
    repo = parse_github_url(url)
    assert repo is not None
    assert (repo.owner, repo.name, repo.branch) == (owner, name, branch)
    assert repo.clone_url == f"https://github.com/{owner}/{name}.git"


@pytest.mark.parametrize(
    "url",
    ["https://gitlab.com/user/repo", "https://github.com/user", "https://github.com/"],
)
def test_rejects_unsupported_urls(url, tmp_path: Path):
    assert parse_github_url(url) is None
    with pytest.raises(RepositoryError, match="Unsupported repository URL"):
        resolve_source(url, tmp_path)


def test_local_source(sample_repo: Path, tmp_path: Path):
    source = resolve_source(str(sample_repo), tmp_path / "workspace")
    assert source.kind == "local"
    assert source.name == "sample"
    assert Path(source.local_path) == sample_repo.resolve()
    assert source.commit is None


def test_local_source_errors(tmp_path: Path):
    with pytest.raises(RepositoryError, match="does not exist"):
        resolve_source(str(tmp_path / "missing"), tmp_path)
    file = tmp_path / "file.txt"
    file.write_text("x")
    with pytest.raises(RepositoryError, match="not a directory"):
        resolve_source(str(file), tmp_path)


def test_refresh_refuses_to_delete_outside_workspace(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "precious"
    outside.mkdir()
    with pytest.raises(RepositoryError, match="Refusing to delete"):
        _remove_inside(outside, workspace)
    with pytest.raises(RepositoryError, match="Refusing to delete"):
        _remove_inside(workspace, workspace)
    assert outside.exists() and workspace.exists()


def test_github_auth_env_keeps_token_out_of_urls_and_args():
    import base64

    from tutorial_ai.repository.clone import github_auth_env

    assert github_auth_env(None) == {}
    env = github_auth_env("gho_secret123")
    assert env["GIT_CONFIG_KEY_0"] == "http.https://github.com/.extraheader"
    header = env["GIT_CONFIG_VALUE_0"].removeprefix("Authorization: Basic ")
    assert base64.b64decode(header).decode() == "x-access-token:gho_secret123"


def test_clone_passes_token_only_via_environment(tmp_path, monkeypatch):
    from tutorial_ai.repository import clone

    calls = []

    def fake_run_git(command, cwd, extra_env=None):
        calls.append((command, extra_env))
        target = Path(command[-1])
        (target / ".git").mkdir(parents=True)
        return ""

    monkeypatch.setattr(clone, "_run_git", fake_run_git)
    monkeypatch.setattr(clone, "git_revision", lambda path: ("abc", "main"))
    source = clone.resolve_source(
        "https://github.com/me/private-repo", tmp_path, github_token="gho_secret123"
    )
    command, env = calls[0]
    assert source.name == "private-repo"
    assert not any("gho_secret123" in part for part in command)
    assert "GIT_CONFIG_VALUE_0" in env
