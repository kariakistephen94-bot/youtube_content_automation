import json
import stat

import httpx
import pytest

from tutorial_ai import github
from tutorial_ai.github import GitHubClient, GitHubError, GitHubToken


def _http(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_start_device_flow_sends_client_id_and_scope():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = request.content.decode()
        return httpx.Response(200, json={
            "device_code": "dc", "user_code": "ABCD-1234",
            "verification_uri": "https://github.com/login/device", "expires_in": 900, "interval": 5,
        })

    code = github.start_device_flow("Iv1.client", http=_http(handler))
    assert code.user_code == "ABCD-1234"
    assert seen["url"] == "https://github.com/login/device/code"
    assert "client_id=Iv1.client" in seen["body"] and "scope=repo+read%3Auser" in seen["body"]


def test_device_flow_disabled_gives_actionable_error():
    handler = lambda r: httpx.Response(200, json={"error": "device_flow_disabled"})
    with pytest.raises(GitHubError, match="Enable 'Device Flow'"):
        github.start_device_flow("id", http=_http(handler))


@pytest.mark.parametrize(
    ("payload", "status"),
    [
        ({"error": "authorization_pending"}, "pending"),
        ({"error": "slow_down", "interval": 10}, "slow_down"),
        ({"error": "expired_token"}, "expired"),
        ({"error": "access_denied"}, "denied"),
        ({"access_token": "gho_abc", "token_type": "bearer", "scope": "repo"}, "authorized"),
    ],
)
def test_poll_device_flow_states(payload, status):
    result = github.poll_device_flow("id", "dc", http=_http(lambda r: httpx.Response(200, json=payload)))
    assert result.status == status
    if status == "authorized":
        assert result.token.access_token == "gho_abc"
    if status == "slow_down":
        assert result.interval == 10


def test_token_is_stored_with_owner_only_permissions(isolated_config_dir):
    from datetime import UTC, datetime

    token = GitHubToken(access_token="gho_secret", login="me", created_at=datetime.now(UTC))
    github.save_token(token)
    path = isolated_config_dir / github.TOKEN_FILE
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert github.load_token() == token
    github.delete_token()
    assert github.load_token() is None


def test_list_repos_paginates_and_maps_fields():
    def repo(i: int) -> dict:
        return {"full_name": f"me/r{i}", "name": f"r{i}", "owner": {"login": "me"},
                "private": i % 2 == 0, "html_url": f"https://github.com/me/r{i}"}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer gho_x"
        page = int(request.url.params["page"])
        return httpx.Response(200, json=[repo(i) for i in range(100)] if page == 1 else [repo(100)])

    repos = GitHubClient("gho_x", http=_http(handler)).list_repos()
    assert len(repos) == 101
    assert repos[0].private and repos[0].owner == "me"


def test_revoked_token_gives_clear_error():
    client = GitHubClient("bad", http=_http(lambda r: httpx.Response(401, json={})))
    with pytest.raises(GitHubError, match="Connect again"):
        client.get_user()
