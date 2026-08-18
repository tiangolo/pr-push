import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

import httpx
import jwt
import yaml
from pydantic import TypeAdapter, ValidationError

from pr_push.config import Settings
from pr_push.models import (
    CONFIG_PATH,
    CollaboratorPermission,
    Installation,
    InstallationToken,
    OIDCClaims,
    PullRequest,
    Repository,
    RepositoryFile,
    TokenResponse,
    WorkflowConfig,
)

GITHUB_API_URL = "https://api.github.com"
GITHUB_API_VERSION = "2026-03-10"
AUTHORIZATION_TOKEN_PERMISSIONS = {
    "contents": "read",
    "pull_requests": "read",
}
PULL_REQUEST_TOKEN_PERMISSIONS = {
    "contents": "write",
    "workflows": "write",
}
WORKFLOW_DISPATCH_TOKEN_PERMISSIONS = {
    "contents": "write",
}

logger = logging.getLogger(__name__)


class GitHubAPIError(RuntimeError):
    pass


class WorkflowNotAllowedError(ValueError):
    pass


def github_headers(token: str) -> dict[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": GITHUB_API_VERSION,
    }


def create_app_jwt(settings: Settings) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "iat": now - timedelta(seconds=60),
            "exp": now + timedelta(minutes=9),
            "iss": settings.github_client_id,
        },
        settings.github_app_private_key.get_secret_value(),
        algorithm="RS256",
    )


def github_request(
    client: httpx.Client,
    method: str,
    url: str,
    token: str,
    params: dict[str, str | int] | None = None,
    json: Any = None,
) -> httpx.Response:
    request_arguments: dict[str, Any] = {
        "headers": github_headers(token),
        "params": params,
    }
    if json is not None:
        request_arguments["json"] = json
    try:
        response = client.request(method, url, **request_arguments)
        response.raise_for_status()
        return response
    except httpx.HTTPError as error:
        status_code = (
            error.response.status_code
            if isinstance(error, httpx.HTTPStatusError)
            else None
        )
        logger.error(
            "GitHub API request failed: method=%s url=%s status_code=%s error=%s",
            method,
            url,
            status_code,
            type(error).__name__,
        )
        raise GitHubAPIError("GitHub rejected the token request") from error


def get_repository_file(
    repository: str,
    path: str,
    ref: str,
    token: str,
    client: httpx.Client,
) -> RepositoryFile:
    response = github_request(
        client,
        "GET",
        f"/repos/{repository}/contents/{quote(path, safe='/')}",
        token,
        params={"ref": ref},
    )
    try:
        return RepositoryFile.model_validate_json(response.content)
    except ValidationError as error:
        raise GitHubAPIError("GitHub rejected the token request") from error


def get_installation(
    claims: OIDCClaims,
    app_token: str,
    client: httpx.Client,
) -> Installation:
    installation_response = github_request(
        client,
        "GET",
        f"/repos/{claims.repository}/installation",
        app_token,
    )
    try:
        return Installation.model_validate_json(installation_response.content)
    except ValidationError as error:
        raise GitHubAPIError("GitHub rejected the token request") from error


def issue_installation_token(
    claims: OIDCClaims,
    installation: Installation,
    permissions: dict[str, str],
    app_token: str,
    client: httpx.Client,
) -> InstallationToken:
    token_response = github_request(
        client,
        "POST",
        f"/app/installations/{installation.id}/access_tokens",
        app_token,
        json={
            "repository_ids": [claims.repository_id],
            "permissions": permissions,
        },
    )
    try:
        token = InstallationToken.model_validate_json(token_response.content)
    except ValidationError as error:
        raise GitHubAPIError("GitHub rejected the token request") from error
    expected_permissions = {**permissions, "metadata": "read"}
    if (
        token.permissions != expected_permissions
        or token.repository_selection != "selected"
        or [repository.id for repository in token.repositories]
        != [claims.repository_id]
    ):
        logger.error(
            "Installation token has unexpected scope: repository=%s "
            "repository_id=%s permissions=%s repository_selection=%s "
            "repository_ids=%s",
            claims.repository,
            claims.repository_id,
            token.permissions,
            token.repository_selection,
            [repository.id for repository in token.repositories],
        )
        raise GitHubAPIError("GitHub rejected the token request")
    return token


def get_pull_request(
    claims: OIDCClaims,
    repository: Repository,
    token: str,
    client: httpx.Client,
) -> PullRequest:
    if claims.event_name == "pull_request":
        pull_response = github_request(
            client,
            "GET",
            f"/repos/{claims.repository}/pulls/{claims.pull_request_number}",
            token,
        )
        return PullRequest.model_validate_json(pull_response.content)

    if claims.branch_name == repository.default_branch:
        raise WorkflowNotAllowedError("workflow is running from the default branch")
    owner, _, _ = claims.repository.partition("/")
    pulls_response = github_request(
        client,
        "GET",
        f"/repos/{claims.repository}/pulls",
        token,
        params={
            "state": "open",
            "head": f"{owner}:{claims.branch_name}",
            "base": repository.default_branch,
            "per_page": 2,
        },
    )
    pull_requests = TypeAdapter(list[PullRequest]).validate_json(pulls_response.content)
    if len(pull_requests) != 1:
        raise WorkflowNotAllowedError(
            "branch does not identify exactly one open pull request"
        )
    pull_request = pull_requests[0]
    if pull_request.head.sha != claims.workflow_sha:
        raise WorkflowNotAllowedError("pull request head SHA does not match")
    return pull_request


def authorize_workflow(
    claims: OIDCClaims,
    installation: Installation,
    token: str,
    client: httpx.Client,
) -> int:
    try:
        repository_response = github_request(
            client, "GET", f"/repos/{claims.repository}", token
        )
        repository = Repository.model_validate_json(repository_response.content)

        config_file = get_repository_file(
            claims.repository,
            CONFIG_PATH,
            repository.default_branch,
            token,
            client,
        )
        config = WorkflowConfig.model_validate(
            yaml.safe_load(config_file.decoded_content())
        )
        if claims.workflow_path not in config.workflows:
            raise WorkflowNotAllowedError("workflow is not listed in the configuration")

        pull_request = get_pull_request(claims, repository, token, client)
        if pull_request.state != "open":
            raise WorkflowNotAllowedError("pull request is not open")
        if pull_request.head.repo.id != claims.repository_id:
            raise WorkflowNotAllowedError("pull request is from a fork")

        permission_response = github_request(
            client,
            "GET",
            f"/repos/{claims.repository}/collaborators/{quote(claims.actor, safe='')}/permission",
            token,
        )
        permission = CollaboratorPermission.model_validate_json(
            permission_response.content
        )
        if permission.user.id != claims.actor_id:
            raise WorkflowNotAllowedError("actor ID does not match")
        is_app_actor = (
            claims.event_name == "pull_request"
            and claims.actor == f"{installation.app_slug}[bot]"
        )
        if permission.permission not in {"admin", "write"} and not is_app_actor:
            raise WorkflowNotAllowedError("actor does not have write permission")

        trusted_workflow = get_repository_file(
            claims.repository,
            claims.workflow_path,
            pull_request.base.sha,
            token,
            client,
        )
        executed_workflow = get_repository_file(
            claims.repository,
            claims.workflow_path,
            claims.workflow_sha,
            token,
            client,
        )
        if trusted_workflow.sha != executed_workflow.sha:
            raise WorkflowNotAllowedError("workflow differs from the trusted version")
        return pull_request.number
    except WorkflowNotAllowedError as error:
        logger.warning(
            "Workflow authorization rejected: reason=%s repository=%s "
            "repository_id=%s actor=%s actor_id=%s event=%s ref=%s workflow=%s",
            str(error),
            claims.repository,
            claims.repository_id,
            claims.actor,
            claims.actor_id,
            claims.event_name,
            claims.ref,
            claims.workflow_path,
        )
        raise
    except (ValidationError, ValueError, yaml.YAMLError) as error:
        logger.warning(
            "Workflow authorization data is invalid: repository=%s "
            "repository_id=%s actor_id=%s error=%s",
            claims.repository,
            claims.repository_id,
            claims.actor_id,
            type(error).__name__,
        )
        raise WorkflowNotAllowedError(
            "The GitHub Actions workflow is not allowed"
        ) from error


def create_token(
    claims: OIDCClaims,
    settings: Settings,
    client: httpx.Client,
) -> TokenResponse:
    app_token = create_app_jwt(settings)
    installation = get_installation(claims, app_token, client)
    authorization_token = issue_installation_token(
        claims,
        installation,
        AUTHORIZATION_TOKEN_PERMISSIONS,
        app_token,
        client,
    )
    pull_request_number = authorize_workflow(
        claims, installation, authorization_token.token, client
    )
    permissions = (
        PULL_REQUEST_TOKEN_PERMISSIONS
        if claims.event_name == "pull_request"
        else WORKFLOW_DISPATCH_TOKEN_PERMISSIONS
    )
    installation_token = issue_installation_token(
        claims,
        installation,
        permissions,
        app_token,
        client,
    )
    logger.info(
        "Installation token issued: repository=%s repository_id=%s actor=%s "
        "actor_id=%s event=%s ref=%s workflow=%s pull_request=%s expires_at=%s",
        claims.repository,
        claims.repository_id,
        claims.actor,
        claims.actor_id,
        claims.event_name,
        claims.ref,
        claims.workflow_path,
        pull_request_number,
        installation_token.expires_at.isoformat(),
    )
    return TokenResponse(
        token=installation_token.token,
        expires_at=installation_token.expires_at,
        repository=claims.repository,
        permissions=installation_token.permissions,
    )
