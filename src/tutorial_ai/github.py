"""GitHub connection: OAuth device flow sign-in, token storage, and repository listing.

Device flow (https://docs.github.com/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps#device-flow):
the app shows a short code, the user enters it at github.com/login/device, and the app receives
an access token. No client secret or callback server is needed, so it works for a local app.

The token is stored in the config directory (default ~/.config/tutorial-ai/) with owner-only
permissions, and is never returned to the browser.
"""

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import httpx
from pydantic import BaseModel

from tutorial_ai.config import get_settings
from tutorial_ai.utils.logging import get_logger

log = get_logger("github")

GITHUB_URL = "https://github.com"
API_URL = "https://api.github.com"
# `repo` is required for OAuth Apps to read private repositories (GitHub offers no read-only
# variant for OAuth Apps). A GitHub App client ID works too and uses its own permissions instead.
DEFAULT_SCOPE = "repo read:user"
TOKEN_FILE = "github_token.json"
MAX_REPOS = 300


class GitHubError(RuntimeError):
    pass


class DeviceCode(BaseModel):
    device_code: str
    user_code: str
    verification_uri: str
    expires_in: int
    interval: int


class GitHubToken(BaseModel):
    access_token: str
    token_type: str = "bearer"
    scope: str = ""
    login: str | None = None
    created_at: datetime


class PollResult(BaseModel):
    status: Literal["pending", "slow_down", "authorized", "expired", "denied"]
    token: GitHubToken | None = None
    interval: int | None = None


class GitHubUser(BaseModel):
    login: str
    name: str | None = None
    avatar_url: str | None = None
    html_url: str


class GitHubRepoInfo(BaseModel):
    full_name: str
    name: str
    owner: str
    private: bool
    description: str | None = None
    html_url: str
    default_branch: str | None = None
    language: str | None = None
    updated_at: str | None = None
    stargazers_count: int = 0


# --- token storage --------------------------------------------------------------------------


def _config_dir(config_dir: Path | None) -> Path:
    return (config_dir or get_settings().config_directory).expanduser()


def load_token(config_dir: Path | None = None) -> GitHubToken | None:
    path = _config_dir(config_dir) / TOKEN_FILE
    if not path.exists():
        return None
    try:
        return GitHubToken.model_validate_json(path.read_text(encoding="utf-8"))
    except ValueError:
        log.warning("Ignoring unreadable GitHub token file %s", path)
        return None


def save_token(token: GitHubToken, config_dir: Path | None = None) -> None:
    directory = _config_dir(config_dir)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / TOKEN_FILE
    # Create with owner-only permissions before writing the secret.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(token.model_dump_json())
    os.chmod(path, 0o600)
    log.info("Saved GitHub token for %s", token.login or "user")


def delete_token(config_dir: Path | None = None) -> None:
    (_config_dir(config_dir) / TOKEN_FILE).unlink(missing_ok=True)
    log.info("Disconnected GitHub")


# --- device flow ----------------------------------------------------------------------------


def _client(http: httpx.Client | None) -> httpx.Client:
    return http or httpx.Client(timeout=20)


def start_device_flow(
    client_id: str, scope: str = DEFAULT_SCOPE, http: httpx.Client | None = None
) -> DeviceCode:
    response = _client(http).post(
        f"{GITHUB_URL}/login/device/code",
        data={"client_id": client_id, "scope": scope},
        headers={"Accept": "application/json"},
    )
    data = _json(response)
    if "error" in data:
        hint = ""
        if data["error"] in ("device_flow_disabled", "unauthorized_client"):
            hint = " Enable 'Device Flow' in your GitHub OAuth App settings."
        raise GitHubError(f"GitHub refused to start sign-in: {data.get('error_description') or data['error']}.{hint}")
    return DeviceCode.model_validate(data)


def poll_device_flow(
    client_id: str, device_code: str, http: httpx.Client | None = None
) -> PollResult:
    response = _client(http).post(
        f"{GITHUB_URL}/login/oauth/access_token",
        data={
            "client_id": client_id,
            "device_code": device_code,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        },
        headers={"Accept": "application/json"},
    )
    data = _json(response)
    error = data.get("error")
    if error == "authorization_pending":
        return PollResult(status="pending")
    if error == "slow_down":
        return PollResult(status="slow_down", interval=data.get("interval"))
    if error == "expired_token":
        return PollResult(status="expired")
    if error == "access_denied":
        return PollResult(status="denied")
    if error:
        raise GitHubError(f"GitHub sign-in failed: {data.get('error_description') or error}")
    token = GitHubToken(
        access_token=data["access_token"],
        token_type=data.get("token_type", "bearer"),
        scope=data.get("scope", ""),
        created_at=datetime.now(UTC),
    )
    return PollResult(status="authorized", token=token)


# --- API ------------------------------------------------------------------------------------


class GitHubClient:
    def __init__(self, token: str, http: httpx.Client | None = None) -> None:
        self._http = _client(http)
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _get(self, path: str, params: dict | None = None) -> httpx.Response:
        response = self._http.get(f"{API_URL}{path}", headers=self._headers, params=params)
        if response.status_code == 401:
            raise GitHubError("GitHub rejected the saved token (expired or revoked). Connect again.")
        if response.status_code >= 400:
            raise GitHubError(f"GitHub API error {response.status_code}: {response.text[:200]}")
        return response

    def get_user(self) -> GitHubUser:
        return GitHubUser.model_validate(self._get("/user").json(), from_attributes=False)

    def list_repos(self, limit: int = MAX_REPOS) -> list[GitHubRepoInfo]:
        repos: list[GitHubRepoInfo] = []
        page = 1
        while len(repos) < limit:
            batch = self._get(
                "/user/repos",
                {"per_page": 100, "page": page, "sort": "updated",
                 "affiliation": "owner,collaborator,organization_member"},
            ).json()
            if not batch:
                break
            for item in batch:
                repos.append(GitHubRepoInfo(
                    full_name=item["full_name"], name=item["name"], owner=item["owner"]["login"],
                    private=item["private"], description=item.get("description"),
                    html_url=item["html_url"], default_branch=item.get("default_branch"),
                    language=item.get("language"), updated_at=item.get("updated_at"),
                    stargazers_count=item.get("stargazers_count", 0),
                ))
            if len(batch) < 100:
                break
            page += 1
        return repos[:limit]


def _json(response: httpx.Response) -> dict:
    try:
        return response.json()
    except json.JSONDecodeError as exc:
        raise GitHubError(f"Unexpected response from GitHub ({response.status_code})") from exc
