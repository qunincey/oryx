---
name: openclaw-discussion-to-prd
description: Use when working in an OpenClaw docs repo and a requirement discussion is mature enough to be turned into a draft PRD. This skill summarizes the agreed changes, analyzes impacted modules and affected repositories using the initialized project/module docs and dependency map, identifies cross-module dependencies and open questions, and drafts an OpenClaw-ready PRD in docs/prd with status proposed.
---

# OpenClaw Discussion To PRD

Use this skill when the user has already discussed a requirement in the docs repo and wants the conversation turned into an execution-ready draft.

## Read First

Always read these files before drafting:

- `docs/project/project-overview.md`
- `docs/modules/module-map.md`
- `.openclaw/spec-template.md`
- `AGENTS.md`

Read these only if relevant:

- `docs/meeting-notes/*.md`
- existing related files under `docs/prd/`
- `docs/rfc/*.md`
- `references/module-impact-rules.md`

## Goal

Turn a mature discussion into one clear `docs/prd/*.md` draft that downstream OpenClaw dispatch can use after approval.

## Core Workflow

1. Consolidate the discussion first.
2. Infer impacted modules and target repositories from `docs/modules/module-map.md`.
3. Draft one focused PRD in `docs/prd/` with `status: proposed`.
4. Surface unclear items as risks or open questions instead of inventing answers.

## What To Produce

The PRD should capture at least these outputs:

- A concise summary of all agreed changes from the discussion
- The affected business modules
- The affected repositories in `affected_repos`
- The primary user/business goal
- Explicit scope boundaries
- Concrete acceptance criteria that a coding worker can implement
- Repo-specific implementation scope for each affected repository when work differs by repo
- Risks, dependency assumptions, and unresolved questions

## Required Analysis

Before writing the PRD, summarize the discussion into these buckets:

- Business objective
- User-visible change
- Required system behavior
- Data/interface changes
- Constraints or compatibility requirements
- Explicitly out-of-scope items

Then analyze impact in this order:

1. Which module owns the change
2. Which supporting modules are likely touched
3. Which repositories map to those modules
4. Whether the change also needs an RFC before implementation

Use `references/module-impact-rules.md` for heuristics.

## Impact Analysis Rules

- Always name one primary owning module.
- Add supporting modules only when the requirement truly crosses boundaries.
- Translate modules to repos using the `Target Repo` column in `docs/modules/module-map.md`.
- If the requirement changes UI plus backend, include both repos.
- If the requirement changes cross-module behavior, check whether shared libraries, framework repos, or ops/observability modules are also impacted.
- If the requirement only changes documentation or analysis, do not create a product PRD.

## PRD Structure

Start from `.openclaw/spec-template.md` and adapt it. The final document should include:

- Frontmatter:
  - `title`
  - `status: proposed`
  - `affected_repos`
  - `references`
- Body sections:
  - `# <title>`
  - `## Background`
  - `## Goal`
  - `## Agreed Scope`
  - `## Non-goals`
  - `## Acceptance Criteria`
  - `## Impacted Modules`
  - `## Repo-specific Scope`
  - `## Risks`
