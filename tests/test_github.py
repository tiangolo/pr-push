import base64
import json
import logging
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from pr_push.config import Settings
from pr_push.github import (
    GitHubAPIError,
    WorkflowNotAllowedError,
    create_token,
    get_repository_file,
)
from pr_push.models import OIDCClaims


def github_transport(
    claims: OIDCClaims,
    update: Callable[[httpx.Request, Any], None] | None = None,
) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        data: Any
        if path.endswith("/installation"):
            data = {"id": 987, "app_slug": "pr-push"}
        elif path.endswith("/access_tokens"):
            permissions = json.loads(request.content)["permissions"]
            data = {
                "token": (
                    "ghs_authorization"
                    if permissions["contents"] == "read"
                    else "ghs_secret"
                ),
                "expires_at": "2026-08-06T15:00:00Z",
                "permissions": {**permissions, "metadata": "read"},
                "repository_selection": "selected",
                "repositories": [{"id": claims.repository_id}],
            }
        elif path == "/repos/fastapi/fastapi":
            data = {
                "default_branch": "master",
            }
        elif path.endswith("/.github/pr-push.yml"):
            content = f"workflows:\n  - {claims.workflow_path}\n".encode()
            data = {
                "type": "file",
                "sha": "config-sha",
                "encoding": "base64",
                "content": base64.b64encode(content).decode(),
            }
        elif path.endswith("/pulls"):
            data = [pull_request_data(claims)]
        elif "/pulls/" in path:
            data = pull_request_data(claims)
        elif path.endswith("/permission"):
            data = {
                "permission": "write",
                "user": {"id": claims.actor_id},
            }
        elif "/.github/workflows/" in path:
            data = {
                "type": "file",
                "sha": "workflow-blob-sha",
                "encoding": "base64",
                "content": "",
            }
        else:
            raise AssertionError(path)
        if update is not None:
            update(request, data)
        return httpx.Response(200, json=data, request=request)

    return httpx.MockTransport(handle)


def pull_request_data(claims: OIDCClaims) -> dict[str, object]:
    return {
        "number": 123,
        "state": "open",
        "head": {
            "sha": (
                claims.workflow_sha
                if claims.event_name == "workflow_dispatch"
                else "head-sha"
            ),
            "repo": {"id": claims.repository_id},
        },
        "base": {
            "sha": "base-sha",
        },
    }


def test_create_token(
    settings: Settings, claims: OIDCClaims, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    requested_permissions: list[dict[str, str]] = []

    def update(request: httpx.Request, data: Any) -> None:
        if request.url.path.endswith("access_tokens"):
            requested_permissions.append(json.loads(request.content)["permissions"])

    with httpx.Client(
        base_url="https://api.github.test",
        transport=github_transport(claims, update),
    ) as client:
        response = create_token(claims, settings, client)
    assert response.token == "ghs_secret"
    assert response.repository == claims.repository
    assert response.permissions == {
        "contents": "write",
        "metadata": "read",
        "workflows": "write",
    }
    assert requested_permissions == [
        {"contents": "read", "pull_requests": "read"},
        {"contents": "write", "workflows": "write"},
    ]
    assert "Installation token issued" in caplog.text
    assert claims.repository in caplog.text
    assert claims.workflow_path in caplog.text
    assert "ghs_secret" not in caplog.text


def test_create_workflow_dispatch_token(
    settings: Settings,
    workflow_dispatch_claims: OIDCClaims,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    requested_permissions: list[dict[str, str]] = []
    pull_params: dict[str, str] = {}

    def update(request: httpx.Request, data: Any) -> None:
        if request.url.path.endswith("access_tokens"):
            requested_permissions.append(json.loads(request.content)["permissions"])
        elif request.url.path.endswith("/pulls"):
            pull_params.update(request.url.params)

    with httpx.Client(
        base_url="https://api.github.test",
        transport=github_transport(workflow_dispatch_claims, update),
    ) as client:
        response = create_token(workflow_dispatch_claims, settings, client)

    assert response.token == "ghs_secret"
    assert response.permissions == {
        "contents": "write",
        "metadata": "read",
    }
    assert requested_permissions == [
        {"contents": "read", "pull_requests": "read"},
        {"contents": "write"},
    ]
    assert pull_params == {
        "state": "open",
        "head": "fastapi:translate-es",
        "base": "master",
        "per_page": "2",
    }
    assert "event=workflow_dispatch" in caplog.text
    assert "pull_request=123" in caplog.text
    assert "ghs_secret" not in caplog.text
    assert "ghs_authorization" not in caplog.text


def test_allows_own_app_actor_for_pull_request(
    settings: Settings,
    claims: OIDCClaims,
) -> None:
    app_claims = claims.model_copy(
        update={"actor": "pr-push[bot]", "actor_id": 313937575}
    )

    def update(request: httpx.Request, data: Any) -> None:
        if request.url.path.endswith("/permission"):
            assert isinstance(data, dict)
            data["permission"] = "none"

    with httpx.Client(
        base_url="https://api.github.test",
        transport=github_transport(app_claims, update),
    ) as client:
        response = create_token(app_claims, settings, client)
    assert response.token == "ghs_secret"


def test_rejects_own_app_actor_for_workflow_dispatch(
    settings: Settings,
    workflow_dispatch_claims: OIDCClaims,
) -> None:
    app_claims = workflow_dispatch_claims.model_copy(
        update={"actor": "pr-push[bot]", "actor_id": 313937575}
    )

    def update(request: httpx.Request, data: Any) -> None:
        if request.url.path.endswith("/permission"):
            assert isinstance(data, dict)
            data["permission"] = "none"

    with (
        httpx.Client(
            base_url="https://api.github.test",
            transport=github_transport(app_claims, update),
        ) as client,
        pytest.raises(WorkflowNotAllowedError),
    ):
        create_token(app_claims, settings, client)


def test_rejects_default_branch_workflow_dispatch(
    settings: Settings,
    workflow_dispatch_claims: OIDCClaims,
) -> None:
    default_branch_claims = workflow_dispatch_claims.model_copy(
        update={"ref": "refs/heads/master"}
    )
    with (
        httpx.Client(
            base_url="https://api.github.test",
            transport=github_transport(default_branch_claims),
        ) as client,
        pytest.raises(WorkflowNotAllowedError),
    ):
        create_token(default_branch_claims, settings, client)


@pytest.mark.parametrize(
    ("case", "reason"),
    [
        ("missing", "exactly one open pull request"),
        ("multiple", "exactly one open pull request"),
        ("sha", "head SHA does not match"),
    ],
)
def test_rejects_unauthorized_workflow_dispatch(
    settings: Settings,
    workflow_dispatch_claims: OIDCClaims,
    case: str,
    reason: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    write_token_requests = 0

    def update(request: httpx.Request, data: Any) -> None:
        nonlocal write_token_requests
        if request.url.path.endswith("access_tokens"):
            permissions = json.loads(request.content)["permissions"]
            if permissions["contents"] == "write":
                write_token_requests += 1
        elif request.url.path.endswith("/pulls"):
            assert isinstance(data, list)
            if case == "missing":
                data.clear()
                return
            pull_request = data[0]
            if case == "multiple":
                data.append(pull_request.copy())
            elif case == "sha":
                pull_request["head"]["sha"] = "other"

    with (
        httpx.Client(
            base_url="https://api.github.test",
            transport=github_transport(workflow_dispatch_claims, update),
        ) as client,
        pytest.raises(WorkflowNotAllowedError),
    ):
        create_token(workflow_dispatch_claims, settings, client)
    assert reason in caplog.text
    assert write_token_requests == 0


def test_encodes_repository_file_path() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert b"workflow%3Fname.yml" in request.url.raw_path
        assert dict(request.url.params) == {"ref": "main"}
        return httpx.Response(
            200,
            json={
                "type": "file",
                "sha": "sha",
                "encoding": "base64",
                "content": "",
            },
            request=request,
        )

    with httpx.Client(
        base_url="https://api.github.test",
        transport=httpx.MockTransport(handle),
    ) as client:
        get_repository_file(
            "fastapi/fastapi",
            ".github/workflows/workflow?name.yml",
            "main",
            "token",
            client,
        )


@pytest.mark.parametrize(
    ("match_path", "field", "value", "reason"),
    [
        ("/pulls/", "state", "closed", "pull request is not open"),
        (
            "/pulls/",
            "head",
            {"sha": "head-sha", "repo": {"id": 1}},
            "pull request is from a fork",
        ),
        ("/permission", "user", {"id": 1}, "actor ID does not match"),
        ("/permission", "permission", "read", "actor does not have write permission"),
    ],
)
def test_rejects_unauthorized_workflow(
    settings: Settings,
    claims: OIDCClaims,
    match_path: str,
    field: str,
    value: object,
    reason: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def update(request: httpx.Request, data: Any) -> None:
        if match_path in request.url.path:
            assert isinstance(data, dict)
            data[field] = value

    with (
        httpx.Client(
            base_url="https://api.github.test",
            transport=github_transport(claims, update),
        ) as client,
        pytest.raises(WorkflowNotAllowedError),
    ):
        create_token(claims, settings, client)
    assert reason in caplog.text
    assert claims.repository in caplog.text
    assert "ghs_authorization" not in caplog.text


def test_rejects_changed_workflow(settings: Settings, claims: OIDCClaims) -> None:
    calls = 0

    def update(request: httpx.Request, data: Any) -> None:
        nonlocal calls
        if request.url.path.endswith("pre-commit.yml"):
            assert isinstance(data, dict)
            calls += 1
            if calls == 2:
                data["sha"] = "changed"

    with (
        httpx.Client(
            base_url="https://api.github.test",
            transport=github_transport(claims, update),
        ) as client,
        pytest.raises(WorkflowNotAllowedError),
    ):
        create_token(claims, settings, client)


def test_hides_github_http_error(settings: Settings, claims: OIDCClaims) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, request=request)

    with (
        httpx.Client(
            base_url="https://api.github.test",
            transport=httpx.MockTransport(handle),
        ) as client,
        pytest.raises(GitHubAPIError, match="GitHub rejected"),
    ):
        create_token(claims, settings, client)


def test_rejects_unexpected_token_scope(settings: Settings, claims: OIDCClaims) -> None:
    def update(request: httpx.Request, data: Any) -> None:
        if request.url.path.endswith("access_tokens"):
            assert isinstance(data, dict)
            data["repository_selection"] = "all"

    with (
        httpx.Client(
            base_url="https://api.github.test",
            transport=github_transport(claims, update),
        ) as client,
        pytest.raises(GitHubAPIError),
    ):
        create_token(claims, settings, client)


@pytest.mark.parametrize(
    "match_path",
    ["/installation", "/access_tokens", "/.github/pr-push.yml"],
)
def test_rejects_invalid_github_responses(
    settings: Settings, claims: OIDCClaims, match_path: str
) -> None:
    def update(request: httpx.Request, data: Any) -> None:
        if request.url.path.endswith(match_path):
            data.clear()

    with (
        httpx.Client(
            base_url="https://api.github.test",
            transport=github_transport(claims, update),
        ) as client,
        pytest.raises(GitHubAPIError),
    ):
        create_token(claims, settings, client)


def test_rejects_workflow_missing_from_config(
    settings: Settings, claims: OIDCClaims
) -> None:
    def update(request: httpx.Request, data: Any) -> None:
        if request.url.path.endswith("pr-push.yml"):
            assert isinstance(data, dict)
            content = b"workflows:\n  - .github/workflows/other.yml\n"
            data["content"] = base64.b64encode(content).decode()

    with (
        httpx.Client(
            base_url="https://api.github.test",
            transport=github_transport(claims, update),
        ) as client,
        pytest.raises(WorkflowNotAllowedError),
    ):
        create_token(claims, settings, client)


def test_rejects_invalid_config(settings: Settings, claims: OIDCClaims) -> None:
    def update(request: httpx.Request, data: Any) -> None:
        if request.url.path.endswith("pr-push.yml"):
            assert isinstance(data, dict)
            data["content"] = base64.b64encode(b"unexpected: true\n").decode()

    with (
        httpx.Client(
            base_url="https://api.github.test",
            transport=github_transport(claims, update),
        ) as client,
        pytest.raises(WorkflowNotAllowedError),
    ):
        create_token(claims, settings, client)
