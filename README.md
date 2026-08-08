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

Request a token only for pull requests whose branch belongs to the same repository:

```yaml
- name: Get PR Push token
  id: pr-push
  if: github.event.pull_request.head.repo.full_name == github.repository
  uses: tiangolo/pr-push@0.0.2
```

Use the token only in the step that pushes the changes:

```yaml
- name: Commit and push changes
  if: github.event.pull_request.head.repo.full_name == github.repository
  env:
    PR_PUSH_TOKEN: ${{ steps.pr-push.outputs.token }}
  run: |
    git config user.name "github-actions[bot]"
    git config user.email "github-actions[bot]@users.noreply.github.com"
    git remote set-url origin "https://x-access-token:${PR_PUSH_TOKEN}@github.com/${GITHUB_REPOSITORY}.git"
    git add -A
    git commit -m "🎨 Auto format"
    git push
```

Fork pull requests should use a separate service such as [pre-commit.ci Lite](https://pre-commit.ci/lite.html) instead of requesting a PR Push token.

## Security

PR Push accepts GitHub OIDC tokens only from `pull_request` workflows. The requesting workflow must be listed in `.github/pr-push.yml`, must match the version from the pull request's base commit, and must run for an open pull request from the same repository by an actor who currently has write permission.

The returned GitHub App installation token has `contents: write`, `pull_requests: read`, and `workflows: write` permissions and is scoped to the repository that requested it. The pull requests permission allows PR Push to verify that the pull request is open and comes from the same repository. The workflows permission allows formatting commits to update files in `.github/workflows`.

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
  url: https://pr-push.example.com
```

## License

This project is licensed under the terms of the MIT license.
