import base64
import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

CONFIG_PATH = ".github/pr-push.yml"


class OIDCClaims(BaseModel):
    repository: str = Field(pattern=r"^[^/]+/[^/]+$")
    repository_id: int
    actor: str
    actor_id: int
    workflow_ref: str
    workflow_sha: str
    event_name: Literal["pull_request", "workflow_dispatch"]
    ref: str
    exp: int
    iat: int
    nbf: int

    @model_validator(mode="after")
    def validate_event_ref(self) -> OIDCClaims:
        if self.event_name == "pull_request":
            if not re.fullmatch(r"refs/pull/[1-9][0-9]*/merge", self.ref):
                raise ValueError("Unexpected pull request ref")
        elif not re.fullmatch(r"refs/heads/.+", self.ref):
            raise ValueError("Unexpected workflow dispatch ref")
        return self

    @property
    def workflow_path(self) -> str:
        prefix = f"{self.repository}/"
        if not self.workflow_ref.startswith(prefix):
            raise ValueError("Unexpected workflow repository")
        path_and_ref = self.workflow_ref.removeprefix(prefix)
        path, separator, _ = path_and_ref.rpartition("@")
        if not separator or not path.startswith(".github/workflows/"):
            raise ValueError("Unexpected workflow path")
        return path

    @property
    def pull_request_number(self) -> int:
        if self.event_name != "pull_request":
            raise ValueError("Identity is not for a pull request")
        return int(self.ref.removeprefix("refs/pull/").removesuffix("/merge"))

    @property
    def branch_name(self) -> str:
        if self.event_name != "workflow_dispatch":
            raise ValueError("Identity is not for a workflow dispatch")
        return self.ref.removeprefix("refs/heads/")


class Repository(BaseModel):
    default_branch: str


class Installation(BaseModel):
    id: int
    app_slug: str


class TokenRepository(BaseModel):
    id: int


class InstallationToken(BaseModel):
    token: str
    expires_at: datetime
    permissions: dict[str, str]
    repository_selection: str
    repositories: list[TokenRepository]


class RepositoryFile(BaseModel):
    type: Literal["file"]
    sha: str
    encoding: Literal["base64"]
    content: str

    def decoded_content(self) -> bytes:
        return base64.b64decode(self.content)


class WorkflowConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflows: list[str] = Field(min_length=1)


class GitHubUser(BaseModel):
    id: int


class CollaboratorPermission(BaseModel):
    permission: Literal["admin", "write", "read", "none"]
    user: GitHubUser


class PullRequestRepository(BaseModel):
    id: int


class PullRequestHead(BaseModel):
    sha: str
    repo: PullRequestRepository


class PullRequestBase(BaseModel):
    sha: str


class PullRequest(BaseModel):
    number: int
    state: Literal["open", "closed"]
    head: PullRequestHead
    base: PullRequestBase


class OIDCTokenResponse(BaseModel):
    value: str


class TokenResponse(BaseModel):
    token: str
    expires_at: datetime
    repository: str
    permissions: dict[str, str]
