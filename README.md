# PR Push

A GitHub App that issues short-lived, repository-scoped tokens to approved workflows that update existing pull request branches.

## Use PR Push

Install the [PR Push GitHub App](https://github.com/apps/pr-push) in the repositories where it should be available.

Add `.github/pr-push.yml` listing the workflows allowed to request tokens:

```yaml
workflows:
  - .github/workflows/pre-commit.yml
```

The configuration and the allowed workflow must already be merged into the repository's default branch before the workflow can request a token.

Give the workflow permission to request a GitHub OIDC token:

```yaml
permissions: {}

jobs:
  pre-commit:
    permissions:
      contents: read
      id-token: write
```

Request a token only for pull requests whose branch belongs to the same repository, or for a manually triggered run:

```yaml
- name: Get PR Push token
  id: pr-push
  if: github.event_name == 'workflow_dispatch' || github.event.pull_request.head.repo.full_name == github.repository
  uses: tiangolo/pr-push@0.0.4
```

Use the token only in the step that pushes the changes:

```yaml
- name: Commit and push changes
  if: github.event_name == 'workflow_dispatch' || github.event.pull_request.head.repo.full_name == github.repository
  env:
    GH_TOKEN: ${{ steps.pr-push.outputs.token }}
  run: |
    git config user.name "pr-push[bot]"
    git config user.email "pr-push[bot]@users.noreply.github.com"
    gh auth setup-git
    git add -A
    git commit -m "🎨 Auto format"
    git push
```

Fork pull requests should use a separate service such as [pre-commit.ci Lite](https://pre-commit.ci/lite.html) instead of requesting a PR Push token.

For `workflow_dispatch`, run the workflow on the branch of an existing pull request:

```console
gh workflow run translate.yml --ref my-pr-branch
```

## Security

PR Push accepts GitHub OIDC tokens from `pull_request` and `workflow_dispatch` workflows. The requesting workflow must be listed in `.github/pr-push.yml` and must match the version from the pull request's base commit.

For `pull_request`, the pull request must be open and its branch must belong to the same repository. The actor must currently have write permission or be the PR Push app bot.

For `workflow_dispatch`, the workflow must run from a non-default branch that identifies exactly one open pull request into the default branch. The pull request must belong to the same repository, its head must match the commit in the OIDC token, and the actor must currently have write permission.

PR Push first creates an internal read-only token with `contents: read` and `pull_requests: read` to authorize the request. It then returns a repository-scoped token with `contents: write`; tokens for `pull_request` also have `workflows: write` so formatting commits can update files in `.github/workflows`. GitHub automatically adds `metadata: read`.

Do not add the GitHub App to branch protection or ruleset bypass lists.

## Deploy your own

Create a GitHub App with read and write access to repository contents and workflows and read-only access to pull requests, then generate a private key. It does not need webhooks, user authorization, a client secret, or any other repository permissions.

Deploy this FastAPI app, for example to [FastAPI Cloud](https://fastapicloud.com), and set:

- `GITHUB_CLIENT_ID`: the GitHub App client ID.
- `GITHUB_APP_PRIVATE_KEY`: the GitHub App private key.
- `OIDC_AUDIENCE`: the public URL of the deployed app.

Pass the deployment URL to the Action:

```yaml
with:
  url: https://your-app.fastapicloud.dev
```

## License

This project is licensed under the terms of the MIT license.
