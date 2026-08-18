import pytest
from pydantic import ValidationError

from pr_push.models import OIDCClaims


def test_claim_properties(claims: OIDCClaims) -> None:
    assert claims.workflow_path == ".github/workflows/pre-commit.yml"
    assert claims.pull_request_number == 123


def test_workflow_dispatch_claim_properties(
    workflow_dispatch_claims: OIDCClaims,
) -> None:
    assert workflow_dispatch_claims.workflow_path == ".github/workflows/translate.yml"
    assert workflow_dispatch_claims.branch_name == "translate-es"


def test_event_specific_properties(
    claims: OIDCClaims, workflow_dispatch_claims: OIDCClaims
) -> None:
    with pytest.raises(ValueError, match="not for a workflow dispatch"):
        _ = claims.branch_name
    with pytest.raises(ValueError, match="not for a pull request"):
        _ = workflow_dispatch_claims.pull_request_number


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("event_name", "push"),
        ("ref", "refs/heads/main"),
    ],
)
def test_claims_reject_unexpected_pull_request(
    claims: OIDCClaims, field: str, value: str
) -> None:
    with pytest.raises(ValidationError):
        OIDCClaims.model_validate({**claims.model_dump(), field: value})


def test_claims_reject_unexpected_workflow_dispatch(
    workflow_dispatch_claims: OIDCClaims,
) -> None:
    with pytest.raises(ValidationError):
        OIDCClaims.model_validate(
            {**workflow_dispatch_claims.model_dump(), "ref": "refs/pull/123/merge"}
        )


def test_claims_reject_workflow_repository(claims: OIDCClaims) -> None:
    invalid = claims.model_copy(update={"workflow_ref": "other/repo/x.yml@main"})
    with pytest.raises(ValueError):
        _ = invalid.workflow_path


def test_claims_reject_workflow_path(claims: OIDCClaims) -> None:
    invalid = claims.model_copy(
        update={"workflow_ref": "fastapi/fastapi/not-a-workflow@main"}
    )
    with pytest.raises(ValueError):
        _ = invalid.workflow_path
