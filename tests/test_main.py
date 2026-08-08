from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient

from pr_push.config import Settings, get_settings
from pr_push.github import GITHUB_API_URL, GitHubAPIError, WorkflowNotAllowedError
from pr_push.main import app, get_claims, get_github_client
from pr_push.models import OIDCClaims

from .test_github import github_transport


@pytest.fixture
def client(
    settings: Settings,
    claims: OIDCClaims,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    monkeypatch.setenv("GITHUB_CLIENT_ID", settings.github_client_id)
    monkeypatch.setenv(
        "GITHUB_APP_PRIVATE_KEY",
        settings.github_app_private_key.get_secret_value(),
    )
    monkeypatch.setenv("OIDC_AUDIENCE", settings.oidc_audience)
    get_settings.cache_clear()
    github_client = httpx.Client(
        base_url="https://api.github.test",
        transport=github_transport(claims),
    )
    app.dependency_overrides[get_claims] = lambda: claims
    app.dependency_overrides[get_github_client] = lambda: github_client
    yield TestClient(app)
    app.dependency_overrides.clear()
    get_settings.cache_clear()
    github_client.close()


@pytest.fixture
def identity_client(settings: Settings) -> Iterator[TestClient]:
    app.dependency_overrides[get_settings] = lambda: settings
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_system_routes(client: TestClient) -> None:
    assert client.get("/").json() == {"name": "PR Push", "version": app.version}
    assert client.get("/health").json() == {"status": "ok"}


def test_token(client: TestClient) -> None:
    response = client.post("/token")
    assert response.status_code == 200
    assert response.json()["token"] == "ghs_secret"
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Pragma"] == "no-cache"


def test_token_requires_bearer(identity_client: TestClient) -> None:
    response = identity_client.post("/token")
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_token_rejects_invalid_identity(identity_client: TestClient) -> None:
    response = identity_client.post(
        "/token", headers={"Authorization": "Bearer invalid"}
    )
    assert response.status_code == 403


@pytest.mark.parametrize(
    ("error", "status_code"),
    [
        (WorkflowNotAllowedError(), 403),
        (GitHubAPIError("GitHub rejected the token request"), 502),
    ],
)
def test_token_hides_service_errors(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    status_code: int,
) -> None:
    def fail(*args: object) -> None:
        raise error

    monkeypatch.setattr("pr_push.main.create_token", fail)
    assert client.post("/token").status_code == status_code


def test_github_client_uses_github_api() -> None:
    clients = get_github_client()
    client = next(clients)
    assert client.base_url == httpx.URL(GITHUB_API_URL)
    assert next(clients, None) is None
