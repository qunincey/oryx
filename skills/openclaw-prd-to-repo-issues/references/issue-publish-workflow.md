# Issue Publish Workflow

Read this file before running `dispatch-approved --create-issues`, especially when repository mappings and credentials are unclear.

## Preferred Publisher

Publish through the OpenClaw bridge CLI instead of hand-posting issues:

```bash
python3 -m openclaw_bridge.cli dispatch-approved --docs-repo /absolute/path/to/docs-repo/docs/prd/feature-a.md --create-issues
```

Why this path matters:

- it renders `implementation-issue.md` artifacts before posting
- it keeps `.openclaw-state/registry.json` aligned with created issue URLs
- it uses the same repo mapping logic for dry-runs and live runs

## Default Order

1. Verify mappings and credentials.
2. Run with `--create-issues` for real publication.
3. Inspect generated artifacts and registry entries if you need to confirm the published result.
4. Use plain `dispatch-approved` or `--dry-run` only when the user asks for preview-only behavior or when live publishing is blocked.

## Repo Mapping Contracts

Each affected repo in `.openclaw/config.json` should resolve to one issue target:

- GitHub: `github_repo`
- GitLab: `gitlab_host` and `gitlab_repo`
- Generic: `repo` and `issue_provider`, plus optional `github_host` or `gitlab_host`

Never publish to placeholder mappings such as `org/repo-a` without user confirmation.

## GitHub Publishing

The bridge must use `gh issue create` for GitHub-family targets.

Requirements:

- `gh` available on `PATH`
- authenticated `gh` session for the target host

Useful verification command:

```bash
gh auth status
```

## GitLab Publishing

The bridge must use `glab issue create` for GitLab-family targets.

Requirements:

- `glab` available on `PATH`
- authenticated `glab` session for the target host

Useful verification command:

```bash
glab auth status
```

## Blocking Rule

If publishing is blocked by missing mappings, missing CLI auth, or missing `gh`/`glab`, surface the exact blocker immediately. Generate artifact-only output only as a fallback, not as the default success path.
