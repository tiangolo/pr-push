from pathlib import Path

import httpx
import pytest

from pr_push.action import ActionError, ActionSettings, main, run


def test_action_gets_and_masks_token(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "output"
    settings = ActionSettings(
        input_url="https://pr-push.example.com",
        actions_id_token_request_url="https://oidc.example.com/token",
        actions_id_token_request_token="request-token",
        github_output=output,
    )
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "oidc.example.com":
            return httpx.Response(200, json={"value": "oidc-token"})
        return httpx.Response(
            200,
            json={
                "token": "ghs_secret",
                "expires_at": "2026-08-06T15:00:00Z",
                "repository": "fastapi/fastapi",
                "permissions": {
                    "contents": "write",
                    "workflows": "write",
                },
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        run(settings, client)

    assert requests[0].headers["Authorization"] == "Bearer request-token"
    assert requests[0].url.params["audience"] == settings.input_url
    assert requests[1].url == httpx.URL("https://pr-push.example.com/token")
    assert requests[1].headers["Authorization"] == "Bearer oidc-token"
    assert capsys.readouterr().out == "::add-mask::ghs_secret\n"
    assert output.read_text() == "token=ghs_secret\n"


@pytest.mark.parametrize("host", ["oidc.example.com", "pr-push.example.com"])
def test_action_hides_http_errors(tmp_path: Path, host: str) -> None:
    settings = ActionSettings(
        input_url="https://pr-push.example.com",
        actions_id_token_request_url="https://oidc.example.com/token",
        actions_id_token_request_token="request-token",
        github_output=tmp_path / "output",
    )

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.host == host:
            return httpx.Response(500, request=request)
        return httpx.Response(200, json={"value": "oidc-token"}, request=request)

    with (
        httpx.Client(transport=httpx.MockTransport(handle)) as client,
        pytest.raises(ActionError),
    ):
        run(settings, client)


def test_action_main_requires_environment(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("ACTIONS_ID_TOKEN_REQUEST_URL", raising=False)
    with pytest.raises(SystemExit):
        main()
    assert capsys.readouterr().out.startswith("::error::")


def test_action_main(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "output"
    monkeypatch.delenv("INPUT_URL", raising=False)
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_URL", "https://oidc.example.com/token")
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "request-token")
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    real_client = httpx.Client

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oidc.example.com":
            assert request.url.params["audience"] == (
                "https://pr-push.fastapicloud.dev"
            )
            return httpx.Response(200, json={"value": "oidc-token"})
        assert request.url == httpx.URL("https://pr-push.fastapicloud.dev/token")
        return httpx.Response(
            200,
            json={
                "token": "ghs_secret",
                "expires_at": "2026-08-06T15:00:00Z",
                "repository": "fastapi/fastapi",
                "permissions": {
                    "contents": "write",
                    "workflows": "write",
                },
            },
        )

    monkeypatch.setattr(
        "pr_push.action.httpx.Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handle)),
    )
    main()
    assert capsys.readouterr().out == "::add-mask::ghs_secret\n"
    assert output.read_text() == "token=ghs_secret\n"
