---
name: openclaw-prd-to-repo-issues
description: Use when an approved OpenClaw docs repo PRD should be turned into repo-specific implementation issue documents and directly published to mapped GitHub or GitLab issues. Patch the PRD only when missing detail would otherwise produce weak issue bodies, and fall back to dry-run or artifact-only output only if the user asks for preview or credentials/mappings are blocked.
---

# OpenClaw PRD To Repo Issues

Use this skill when a PRD already exists and the next step is creating the real repo-targeted online issues from it.

## Read First

Always read:

- the target PRD under `docs/prd/`
- `docs/modules/module-map.md`
- `docs/project/project-overview.md`
- `AGENTS.md`
- `.openclaw/config.json`

Read only if relevant:

- `.openclaw/prompts/implementation-issue.md.tmpl`
- related `docs/rfc/*.md`
- related `docs/meeting-notes/*.md`
- neighboring PRDs that share modules or repos
- `references/issue-publish-workflow.md` before live issue creation or when repo mappings/auth are unclear

## Goal

Turn one approved or nearly approved PRD into:

- one repo-scoped implementation issue artifact per affected repo under `.openclaw-state/artifacts/`
- synced task registry entries under `.openclaw-state/registry.json`
- live GitHub or GitLab issues by default when mappings and credentials are ready

## Preferred Execution Path

1. Check whether the PRD already has enough detail for targeted issue generation.
2. Patch only the missing issue-critical structure in the PRD when needed:
   - `## Agreed Scope`
   - `## Impacted Modules`
   - `## Repo-specific Scope`
3. Generate artifacts first when you need to inspect the rendered issue body before publishing:

```bash
python3 -m openclaw_bridge.cli dispatch-approved --docs-repo /absolute/path/to/docs-repo/docs/prd/feature-a.md
```

4. Verify mappings and credentials, then publish through the bridge CLI by default:

```bash
python3 -m openclaw_bridge.cli dispatch-approved --docs-repo /absolute/path/to/docs-repo/docs/prd/feature-a.md --create-issues
```

5. Use `--dry-run` only when the user asks for a preview or the target mapping still needs confirmation.
6. If live publishing fails, report the exact blocker and, if still useful, fall back to artifact generation so the issue body is not lost.

## Minimum PRD Structure

Only add these sections when they are missing or too vague to produce usable issue bodies:

- `## Agreed Scope`
- `## Impacted Modules`
- `## Repo-specific Scope`

Under `## Repo-specific Scope`, use the exact repo name from `affected_repos`:

```md
### repo-a

- Summary: Why this repo is in scope and what it owns.
- Modules: checkout-domain, payment-api
- Change: Concrete modification 1.
- Change: Concrete modification 2.
- Boundary: What this repo should not change.
- Acceptance: Repo-specific observable outcome.
- Risk: Repo-specific risk or dependency.
- Reference: Optional extra reference for this repo.
```

## Required Workflow

1. Confirm the requirement is explicitly approved by frontmatter or by the user.
2. Verify the PRD still has the required dispatch fields:
   - `affected_repos`
   - `references`
   - `goal`
   - `acceptance_criteria`
3. Tighten issue-critical sections only when the current PRD would otherwise create generic issue bodies.
4. For each repo in `affected_repos`, make sure there is enough repo-specific detail to explain:
   - why this repo must change
   - the concrete modifications expected in that repo
   - scope boundaries for that repo
   - repo-level acceptance expectations
5. Verify `.openclaw/config.json` points each affected repo at a real issue target:
   - GitHub: `github_repo`
   - GitLab: `gitlab_host` + `gitlab_repo`
   - Generic mapping: `repo` + `issue_provider`
6. Verify issue CLI authentication:
   - GitHub targets must use `gh issue create`
   - GitLab targets must use `glab issue create`
7. Publish through `dispatch-approved --create-issues` by default so artifact paths, registry state, and issue URLs stay in sync.
8. Use plain `dispatch-approved` without `--create-issues` only for user-requested previews, dry-runs, or blocked environments.
9. If credentials or mappings are missing, report the blocker clearly and stop after artifact generation.

## Writing Rules

- Use exact repo names in `### <repo>` headings so automation can map them.
- Prefer short labeled bullets over long prose.
- Keep `Change` bullets implementation-oriented and testable.
- Keep `Boundary` bullets sharp enough to prevent workers from expanding scope.
- If two repos need materially different work, do not reuse the same bullets for both unless that is genuinely correct.
- Treat PRD edits as a means to better issue generation, not as the final deliverable.
- Do not create issues directly in the browser or by ad hoc API calls when the bridge CLI can do it.
- If a dedicated GitHub or GitLab skill exists in the current environment, you may use it to verify auth or targets, but keep actual dispatch on the OpenClaw bridge path.
- Unless the user explicitly asks for preview-only behavior, do not stop after preparing artifacts; complete the live issue publication step.
