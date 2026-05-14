from __future__ import annotations

import json
import os
from pathlib import Path

from .worker_models import DEFAULT_CODEX_WORKER_MODEL

DEFAULT_CONFIG = {
    "approved_status": "approved",
    "source_repo": "docs-repo",
    "specs_dir": "docs/prd",
    "runtime_dir": ".openclaw-state",
    "issue_template": ".openclaw/prompts/implementation-issue.md.tmpl",
    "worker_template": ".openclaw/prompts/worker-task.txt.tmpl",
    "default_labels": [
        "ai-task",
        "generated-by-openclaw",
        "ready-for-worker",
    ],
    "worker_type": "codex",
    "worker_model": DEFAULT_CODEX_WORKER_MODEL,
    "notifications": {
        "webhook_url": "",
        "events": [
            "completed",
            "blocked",
            "restartable",
            "restarted",
        ],
        "timeout_seconds": 5,
        "headers": {},
        "bearer_token_env": "",
    },
    "launcher": {
        "base_branch": "HEAD",
        "codex_reasoning_effort": "high",
        "repo_roots": [],
        "session_launcher": "tmux",
        "workspace_strategy": "git-worktree",
    },
    "repo_mappings": {
        "repo-a": {
            "github_repo": "org/repo-a",
            "labels": ["backend"],
        },
        "repo-b": {
            "github_repo": "org/repo-b",
            "labels": ["frontend"],
        },
    },
}

AGENTS_TEMPLATE = """# AGENTS.md

## Project Context

- Project name: {project_name}
- Purpose: {project_description}

## Repository Purpose

This repository is the documentation and task-definition source of truth for the project.
Use it to discuss requirements, capture design decisions, and dispatch approved work into
implementation issues for downstream code repositories.

## Document Placement

- `docs/project/project-overview.md`
  Store the high-level project summary, business goals, stakeholders, and important context.
- `docs/modules/module-map.md`
  Store the module catalog. Each module should describe its responsibility, boundaries,
  upstream/downstream dependencies, and the code repository it maps to.
- `docs/prd/`
  Store product requirements or execution-ready demand documents. One file per feature
  or change request. Use `.openclaw/spec-template.md` as the starting point.
- `docs/rfc/`
  Store technical design proposals and solution decisions.
- `docs/meeting-notes/`
  Store raw meeting notes, customer discussions, and discovery inputs.

## Working Rules

- Before the first real PRD exists, or when project/module docs are still placeholders, use the
  repo-local bootstrap guide at `.codex/skills/openclaw-project-bootstrap/SKILL.md`.
- In Claude Code, prefer the bundled agent at `.claude/agents/openclaw-project-bootstrap.md`
  for the same bootstrap/import task.
- When a requirement discussion has converged and the user wants a draft PRD, use the
  repo-local discussion-to-PRD guide at `.codex/skills/openclaw-discussion-to-prd/SKILL.md`.
- In Claude Code, prefer the bundled agent at `.claude/agents/openclaw-discussion-to-prd.md`
  for discussion consolidation, module impact analysis, and PRD drafting.
- When an approved or nearly approved PRD should be turned into real mapped online issues,
  use `.codex/skills/openclaw-prd-to-repo-issues/SKILL.md`.
- In Claude Code, prefer `.claude/agents/openclaw-prd-to-repo-issues.md` for the same repo-targeted
  issue decomposition and dispatch step.
- During discussion, edit or create files under `docs/prd/`, `docs/rfc/`, and
  `docs/meeting-notes/` as needed.
- Use the bootstrap flow to update `docs/project/project-overview.md`,
  `docs/modules/module-map.md`, optional bootstrap notes, and optional draft PRDs.
- When a requirement becomes execution-ready, set `status: approved` in its frontmatter and
  make sure `affected_repos`, `references`, `goal`, and `acceptance_criteria` are complete.
- Before dispatch, make sure the PRD has enough structure for targeted issues:
  `## Agreed Scope`, `## Impacted Modules`, and `## Repo-specific Scope` with one `### <repo>`
  block for each affected repo when the work differs by repository.
- After the user explicitly confirms a requirement should be dispatched, the agent should run
  the installed CLI itself:
  `openclaw-mvp1 dispatch-approved --docs-repo {docs_repo}/docs/prd/<spec>.md`
- If issue creation is desired and `.openclaw/config.json` has real GitHub or GitLab repo mappings, the
  agent should run:
  `openclaw-mvp1 dispatch-approved --docs-repo {docs_repo}/docs/prd/<folder> --create-issues`
- Do not ask the user to manually run these commands unless execution is blocked by missing
  local tooling or credentials.

## Dispatch Boundary

- This docs repo defines work. It does not run coding workers directly.
- Runtime state lives under `.openclaw-state/` and should stay out of versioned docs.
"""

INIT_DOCS_REPO_SKILL_TEMPLATE = """---
name: openclaw-init-docs-repo
description: Use when the user wants to initialize a new OpenClaw docs repo. This skill collects the required init fields through a short Q&A, confirms the plan, and runs the bridge CLI itself instead of sending the command back to the user.
---

# OpenClaw Init Docs Repo

Use this skill before the docs repo exists.

## Goal

Create a new OpenClaw docs repo scaffold and install the bundled Codex skills and Claude agents.

## Required Inputs

Collect these required fields before running init:

- `target_dir`
- `project_name`
- `project_description`

If the user already provided some of them, do not ask again.

## Question-First Workflow

1. Ask one short question at a time for any missing required field.
2. Prefer concrete proposed defaults when that reduces user effort:
   - `target_dir`: propose an absolute path when the intended location is obvious from context.
   - `project_name`: default to the target folder name when the user has not named it yet.
   - `project_description`: draft a one-sentence summary from the conversation and let the user refine it.
3. Once the required fields are known, summarize them in one compact confirmation before execution.
4. If `target_dir` already exists and is non-empty, explain that init preserves existing files unless `--force` is used. Ask before adding `--force`.
5. Run the CLI yourself. Prefer:

```bash
openclaw-mvp1 init-docs-repo --target-dir /abs/path --project-name "..." --project-description "..."
```

Fallback when the console script is unavailable or when you are working from the source checkout:

```bash
python3 -m openclaw_bridge.cli init-docs-repo --target-dir /abs/path --project-name "..." --project-description "..."
```

6. After success, tell the user which directory was initialized, note that global skills/agents were refreshed, and remind them to restart Codex or Claude Code if they need the new global assets loaded.

## Constraints

- Keep the Q&A short. Do not turn initialization into a long questionnaire.
- Do not ask the user to manually run the init command unless execution is blocked.
- Do not invent missing project context. Offer a draft and let the user correct it.
- Treat `--force` as a risky option and ask before using it.
- If execution fails, report the exact blocker and the attempted command shape.

## Example Requests

- "Use OpenClaw to initialize a docs repo for this project."
- "Help me create a new docs repo through Q&A."
- "Ask me the required fields, then scaffold the repo."
"""

INIT_DOCS_REPO_SKILL_OPENAI_YAML = """interface:
  display_name: "Init Docs Repo"
  short_description: "Ask for required fields and scaffold a docs repo"
  default_prompt: "Use $openclaw-init-docs-repo to initialize a new OpenClaw docs repo. Ask for missing required fields through a short Q&A, confirm the plan, then run the init CLI yourself."
"""

BOOTSTRAP_SKILL_TEMPLATE = """---
name: openclaw-project-bootstrap
description: Use when an OpenClaw docs repo needs to be bootstrapped from existing codebases or from a greenfield discussion before real PRDs exist. Trigger this skill when the user wants AI to scan current modules, import repository context, define project/module docs, or draft the first PRD files.
---

# OpenClaw Project Bootstrap

## Overview

Use this skill to create the first useful docs in an OpenClaw docs repository. It supports
two bootstrap modes: importing an existing project by scanning real repositories, or starting
from a greenfield discussion and writing the initial project structure from scratch.

## Outputs

This skill should primarily create or update:

- `docs/project/project-overview.md`
- `docs/modules/module-map.md` with module responsibilities and module-to-module dependency analysis
- `docs/meeting-notes/bootstrap-*.md` when the conversation or scan needs traceable notes
- `docs/prd/*.md` only when the user wants the first requirement drafts
- `.openclaw/config.json` when repo mappings or source-repo naming are still placeholders

## Mode Selection

Choose one mode before writing files:

1. Existing project import
   Use when the user already has one or more code repositories or modules.
2. Greenfield bootstrap
   Use when the project has not started and the repo should be defined from discussion.

## Existing Project Import

When importing an existing system:

1. Ask for the relevant repository paths if they are not already obvious from the workspace.
2. Scan breadth-first first:
   - top-level directories
   - README files
   - package manifests or build files
   - workspace definitions and internal package references
   - service entrypoints, apps, packages, or modules
   - architecture or deployment docs when available
   - imports, API clients, queue topics, shared libraries, or deployment wiring that reveal module dependencies
3. If the user points to one large folder, first decide whether it contains multiple apps, services, packages,
   or deployable units, then treat those as candidate modules or repositories instead of assuming a single module.
4. Infer candidate modules, ownership boundaries, target repository names, and directional dependency relationships
   between modules.
5. Present the proposed module map and dependency summary with explicit uncertainties.
6. After the user confirms or the evidence is strong enough, update the docs files.

Rules:

- Prefer business modules over raw folder names.
- Do not import every internal folder as a module. Merge low-level implementation details into the owning module.
- Distinguish direct evidence from inference when describing dependencies. Prefer concrete signals such as manifests,
  imports, generated clients, HTTP calls, message topics, or shared database ownership.
- Capture dependency direction explicitly, for example `module-a -> module-b`, and note whether the edge is runtime,
  build-time, data, or operational when that is observable.
- Record unknowns as `TBD` instead of inventing facts.
- Update `.openclaw/config.json` so `repo_mappings` keys match the target repos named in `module-map.md`.

## Greenfield Bootstrap

When the project is still being defined:

1. Draft a first-pass project shape without waiting for a long questionnaire.
2. Cover the minimum useful decisions:
   - project purpose
   - target users or stakeholders
   - expected repositories
   - major modules or bounded contexts
   - key constraints
3. Ask only the highest-value follow-up questions needed to remove major ambiguity.
4. Write the overview and module map once the direction is coherent.
5. Draft the first PRD only if the user wants to move from discussion into a concrete requirement.

## Writing Rules

- Keep `project-overview.md` stable and cross-cutting. Do not turn it into a meeting transcript.
- Keep `module-map.md` implementation-aware but concise. It should help task routing, not document every class.
- Make `module-map.md` explicit about each module's upstream dependencies, downstream consumers, and the highest-value
  cross-module edges in the system. If evidence is weak, mark it as inferred or `TBD`.
- When drafting a PRD, start from `.openclaw/spec-template.md` and keep `status: proposed` until the user explicitly approves it.
- Do not dispatch approved work during bootstrap unless the user explicitly asks to dispatch.
- If the repo still contains placeholder content, replace it rather than appending another placeholder section.

## Example Requests

- "Scan these repos and build the initial module map for this docs repo."
- "We have not started coding yet. Help me define project overview and modules."
- "Import the existing backend and frontend structure, then draft the first PRD."
"""

BOOTSTRAP_SKILL_OPENAI_YAML = """interface:
  display_name: "OpenClaw Bootstrap"
  short_description: "Bootstrap docs repo from code or discussion"
  default_prompt: "Use $openclaw-project-bootstrap to bootstrap this docs repo from existing codebases or a greenfield discussion."
"""

DISCUSSION_TO_PRD_SKILL_TEMPLATE = """---
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
- Repo-specific implementation scope for each affected repository when changes differ by repo
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
- If the requirement changes bridge processing, check whether shared libraries or ops/monitoring are also impacted.
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
"""

DISCUSSION_TO_PRD_SKILL_OPENAI_YAML = """interface:
  display_name: "Discussion To PRD"
  short_description: "Turn mature discussion into draft PRD"
  default_prompt: "Use $openclaw-discussion-to-prd to summarize the current requirement discussion, analyze impacted modules, and draft a proposed PRD."
"""

CLAUDE_INIT_DOCS_REPO_AGENT_TEMPLATE = """---
name: openclaw-init-docs-repo
description: Use PROACTIVELY when the user wants to initialize a new OpenClaw docs repo through a short question-and-answer flow instead of being handed a shell command.
model: inherit
---

You are the init-docs-repo specialist for OpenClaw.

Your job is to collect the required initialization inputs with a short Q&A, then run the bridge CLI yourself.

## Required Inputs

- `target_dir`
- `project_name`
- `project_description`

If the user already provided a field, do not ask again.

## Workflow

1. Ask one short question at a time for any missing required field.
2. Propose sensible defaults when helpful:
   - use an absolute `target_dir` when the location is obvious
   - default `project_name` to the folder name
   - draft `project_description` from the project context
3. Summarize the final values before execution.
4. If `target_dir` already exists and is non-empty, explain that init preserves existing files unless `--force` is used, and ask before using `--force`.
5. Run one of:
   - `openclaw-mvp1 init-docs-repo --target-dir ... --project-name ... --project-description ...`
   - `python3 -m openclaw_bridge.cli init-docs-repo --target-dir ... --project-name ... --project-description ...`
6. After success, report the initialized path and remind the user to restart Codex or Claude Code if they need newly installed global skills or agents loaded.

## Constraints

- Keep the interaction short and question-first.
- Do not bounce the command back to the user unless execution is blocked.
- Do not invent missing project context; propose a draft and let the user refine it.
- Treat `--force` as risky and ask before using it.
"""

CLAUDE_BOOTSTRAP_AGENT_TEMPLATE = """---
name: openclaw-project-bootstrap
description: Use PROACTIVELY to bootstrap an OpenClaw docs repository from existing codebases or from a greenfield discussion before real PRDs exist.
model: inherit
---

You are the bootstrap specialist for an OpenClaw docs repository.

Your job is to turn an empty or placeholder docs repo into a usable planning repo by scanning
existing repositories or by structuring a new project from discussion.

## Primary Outputs

- `docs/project/project-overview.md`
- `docs/modules/module-map.md` with module responsibilities and module-to-module dependency analysis
- optional `docs/meeting-notes/bootstrap-*.md`
- optional draft `docs/prd/*.md`
- updated `.openclaw/config.json` when repo mappings are still placeholders

## Modes

### 1. Existing Project Import

Use when the user already has code repositories or modules.

Process:

1. Scan top-level directories, README files, package/build manifests, workspace definitions, and obvious service entrypoints.
2. If the user points to one large folder, first split it into candidate apps, services, packages, or deployable units.
3. Infer candidate repositories, modules, responsibilities, boundaries, and directional module dependencies.
4. Summarize uncertain assumptions and the strongest dependency evidence before writing.
5. Update the docs repo after confirmation or when the evidence is already strong.

Rules:

- Prefer business modules over raw folder names.
- Do not mirror every implementation folder into the module map.
- When a module maps to a real local Git repository, inspect `git remote get-url origin` and write the resolved
  forge path into `.openclaw/config.json`.
  Use `github_repo` for GitHub-family remotes and `gitlab_host` + `gitlab_repo` for GitLab-family remotes.
- Only leave repo targets as `TBD/...` when no local Git remote exists or the remote cannot be mapped to a concrete
  forge repository path. Call that uncertainty out explicitly.
- Distinguish direct evidence from inference when describing dependencies, and capture edge direction explicitly.
- Use `TBD` for missing facts.
- Align `.openclaw/config.json` repo mapping keys with the repos named in `module-map.md`.

### 2. Greenfield Bootstrap

Use when the project has not started.

Process:

1. Propose an initial project overview and module split from the user goal.
2. Ask only targeted follow-up questions for major unknowns.
3. Write the overview and module map once the direction is coherent.
4. Draft the first PRD only if the user asks for it.

## Constraints

- Keep `project-overview.md` stable and summary-oriented.
- Keep `module-map.md` concise and useful for downstream task routing.
- PRD drafts must start from `.openclaw/spec-template.md` and stay `status: proposed` until explicit approval.
- Never dispatch approved work during bootstrap unless the user explicitly asks for dispatch.
"""

CLAUDE_DISCUSSION_TO_PRD_AGENT_TEMPLATE = """---
name: openclaw-discussion-to-prd
description: Use PROACTIVELY when an OpenClaw docs repo requirement discussion has converged and needs to be turned into a proposed PRD.
model: inherit
---

You are the discussion-to-PRD specialist for an OpenClaw docs repository.

Your job is to turn a mature requirement discussion into a focused draft PRD that can later be approved and dispatched.

## Always Read First

- `docs/project/project-overview.md`
- `docs/modules/module-map.md`
- `.openclaw/spec-template.md`
- `AGENTS.md`

Read related `docs/meeting-notes/*.md`, existing `docs/prd/*.md`, and `docs/rfc/*.md` only when relevant.

## Required Workflow

1. Consolidate the discussion into agreed changes, constraints, and explicit non-goals.
2. Identify one primary owning module from `docs/modules/module-map.md`.
3. Identify only the necessary supporting modules and translate them into `affected_repos`.
4. Draft one PRD under `docs/prd/` with `status: proposed`.
5. If the change is architectural or cross-module with unsettled design, recommend an RFC in `docs/rfc/`.

## Impact Analysis Heuristics

- UI plus backend change: include both the frontend repo and the owning backend repo.
- Shared schema or model changes: include the owning shared module plus all directly affected consumers when required by the discussion.
- Framework or platform-level behavior changes: include the shared framework/platform repo only when the change is truly cross-module.
- Monitoring, alerting, cleanup, or runtime configuration changes: include the ops/observability module when the requirement changes operability.
- Pick exactly one primary owning module; treat framework, ops, and shared libraries as supporting unless the requirement is explicitly about them.

## PRD Requirements

The PRD must include:

- Frontmatter with `title`, `status: proposed`, `affected_repos`, and `references`
- `## Background`
- `## Goal`
- `## Agreed Scope`
- `## Non-goals`
- `## Acceptance Criteria`
- `## Impacted Modules`
- `## Repo-specific Scope`
- `## Risks`

## Constraints

- Default to one PRD per smallest independently dispatchable change.
- Do not invent facts. Use `TBD` or list a risk if evidence is missing.
- Do not dispatch approved work as part of this agent.
- Keep the PRD tied to the actual discussion rather than generic boilerplate.
"""

PRD_TO_REPO_ISSUES_SKILL_TEMPLATE = """---
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
"""

PRD_TO_REPO_ISSUES_SKILL_OPENAI_YAML = """interface:
  display_name: "PRD Issue Dispatch"
  short_description: "Create live repo issues from a PRD"
  default_prompt: "Use $openclaw-prd-to-repo-issues to turn this approved PRD into repo-specific online issues and publish them through the OpenClaw bridge. Only use dry-run or artifact-only output if I explicitly ask for preview or if credentials/mappings are blocked."
"""

CLAUDE_PRD_TO_REPO_ISSUES_AGENT_TEMPLATE = """---
name: openclaw-prd-to-repo-issues
description: Use PROACTIVELY when an approved OpenClaw PRD should be decomposed into repo-specific issue artifacts and directly published to mapped GitHub or GitLab issues.
model: inherit
---

You are the PRD-to-issue decomposition specialist for an OpenClaw docs repository.

Your job is to turn an existing PRD into repo-scoped implementation issues and publish them through the OpenClaw bridge by default.

## Always Read First

- the target PRD under `docs/prd/`
- `docs/modules/module-map.md`
- `docs/project/project-overview.md`
- `AGENTS.md`
- `.openclaw/config.json`

Read RFCs, meeting notes, related PRDs, or the bundled issue-publish workflow reference only when they materially clarify repo ownership, publish targets, or credentials.

## Required Workflow

1. Confirm the PRD is approved and still has `affected_repos`, `references`, `goal`, and `acceptance_criteria`.
2. Patch only the missing issue-critical PRD structure when the existing document would produce generic issue bodies.
3. For each repo in `affected_repos`, ensure `## Repo-specific Scope` has one `### <repo>` block with labeled bullets:
   - `Summary`
   - `Modules`
   - repeated `Change`
   - repeated `Boundary`
   - repeated `Acceptance`
   - optional `Risk`
   - optional `Reference`
4. Verify each affected repo has a real issue target in `.openclaw/config.json`.
5. Run `dispatch-approved --create-issues` by default when mappings and credentials are ready.
6. Use plain `dispatch-approved` or `--dry-run` only when the user wants a preview or the target mapping still needs confirmation.
7. If live publishing is blocked, surface the blocker clearly and fall back to artifact generation only if that still helps the user.

## Constraints

- Use exact repo names from `affected_repos` in headings.
- Do not invent repo changes without evidence; use `TBD` plus a risk when necessary.
- Keep repo blocks implementation-oriented and scoped for downstream coding workers.
- Treat PRD rewriting as a supporting step, not the deliverable.
- Prefer bridge-driven publishing over manual browser issue creation so task state stays synced.
- Unless the user explicitly asks for preview-only behavior, completing the task means creating the live issues, not just preparing markdown.
"""

PRD_TO_REPO_ISSUES_PUBLISH_REFERENCE = """# Issue Publish Workflow

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
"""

MODULE_IMPACT_RULES_TEMPLATE = """# Module Impact Rules

Use this file together with `docs/modules/module-map.md` to infer impacted modules and repositories when converting a requirement discussion into a PRD.

## Start From The Module Map

Use these fields from `docs/modules/module-map.md` as the primary evidence:

- `Module`
- `Responsibility`
- `Boundaries`
- `Upstream Dependencies`
- `Downstream Consumers`
- `Target Repo`

Use the `Dependency Overview` section when it exists to confirm cross-module edges before adding supporting repos.

## Primary Owner Selection

Choose exactly one primary owning module:

- Pick the module that owns the main user value or business behavior.
- If two modules feel equally primary, the requirement may be too broad and should likely become two PRDs.

## Supporting Module Heuristics

- UI plus backend change usually impacts the frontend module and the owning backend module.
- Shared schema, model, or client changes usually impact the shared module plus the business module that needs the change.
- Framework or platform-level behavior changes should only pull in the shared framework/platform repo when the change truly affects multiple business modules.
- Monitoring, alerting, cleanup, audit, or runtime configuration changes usually impact an ops/observability module in addition to the owning business module.
- New or changed integration contracts usually impact the owning integration module and any directly coupled consumer/provider modules.

## Repo Mapping Rule

After selecting modules, translate them to repositories using the `Target Repo` column in `docs/modules/module-map.md`.

- Keep `affected_repos` limited to repos that must change for this requirement.
- Do not add transitive repos unless the discussion makes the change explicit.

## When To Escalate

Recommend an RFC if the requirement:

- Re-draws module boundaries
- Introduces a new platform pattern or shared abstraction
- Requires a new protocol or integration contract
- Forces broad shared-library changes without a settled design
"""

SPEC_TEMPLATE = """---
title: Example approved requirement
status: proposed
affected_repos:
  - repo-a
references:
  - https://github.com/your-org/specs/pull/123
---

# Example approved requirement

## Background

Explain the business context and why this matters.

## Goal

Describe the concrete result OpenClaw should drive in the target repositories.

## Agreed Scope

- Capture the overall implementation scope in concrete bullets.

## Non-goals

- Call out what should stay out of scope.

## Acceptance Criteria

1. List observable outcomes that a coding worker can implement and test.

## Impacted Modules

- Name the owning module, supporting modules, and which repo each maps to.

## Repo-specific Scope

### repo-a

- Summary: Why this repo is in scope and what it should own.
- Modules: example-module
- Change: Concrete modification expected in this repo.
- Boundary: What this repo should not change.
- Acceptance: Repo-specific observable outcome.

## Risks

- Capture known constraints, rollout risk, or missing information.
"""

DOCS_INDEX_TEMPLATE = """# Documentation Structure

## Where To Put Things

- `project/project-overview.md`
  Put project background, goals, glossary, key links, and stable context here.
- `modules/module-map.md`
  Put the module inventory and responsibility map here.
- `prd/`
  Put feature requests, change requests, and execution-ready requirement docs here.
- `rfc/`
  Put architecture decisions, technical tradeoffs, and design proposals here.
- `meeting-notes/`
  Put customer calls, internal discussions, and discovery notes here.

## Typical Flow

1. Use `.codex/skills/openclaw-project-bootstrap/SKILL.md` or the Claude bootstrap agent to create the
   first real project overview and module map.
2. Capture raw input in `meeting-notes/` when needed.
3. When discussion converges, use `.codex/skills/openclaw-discussion-to-prd/SKILL.md` or the Claude
   discussion-to-PRD agent to draft a proposed PRD in `prd/`.
4. Add or update technical decisions in `rfc/`.
5. Keep cross-cutting system understanding in `project/` and `modules/`.
6. When a PRD is approved, dispatch it through the OpenClaw workflow.
"""

PROJECT_OVERVIEW_TEMPLATE = """# Project Overview

## Project Name

{project_name}

## Purpose

{project_description}

## Business Context

- Fill in the target users, stakeholders, and why this project exists.

## Repositories

- Fill in the main application repositories and their roles.

## Key Constraints

- Fill in domain constraints, release constraints, or compliance requirements.
"""

MODULE_MAP_TEMPLATE = """# Module Map

Use this file to describe each business or technical module, the repository that implements it,
and the most important module-to-module dependencies in the system.

| Module | Responsibility | Boundaries | Upstream Dependencies | Downstream Consumers | Target Repo | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| Example module | Replace with real module responsibility | What this module owns and does not own | auth-module, shared-events | api-gateway | repo-a | Optional context |

## Dependency Overview

| From Module | To Module | Relationship | Evidence | Notes |
| --- | --- | --- | --- | --- |
| api-gateway | auth-module | Calls public auth APIs at runtime | README and service imports | Replace with real module edge |

## Per-Module Details

### Example module

- Responsibility:
- Inputs:
- Outputs:
- Upstream dependencies:
- Downstream consumers:
- Dependency evidence:
- Non-goals:
- Related docs:
"""

SECTION_README_TEMPLATE = """# {title}

{description}
"""

ISSUE_TEMPLATE = """## Background

$background

## Goal

$goal

## Agreed Scope

$agreed_scope

## Impacted Modules

$impacted_modules

## Repo-specific Scope

$repo_summary

## Modules In Scope

$repo_modules

## Required Changes

$repo_required_changes

## Boundaries

$repo_boundaries

## Acceptance Criteria

$repo_acceptance_criteria

## Risks

$repo_risks

## References

$references

## Source

- Requirement: `$source_ref`
- Target Repo: `$target_repo`
- Task ID: `$task_id`
"""

WORKER_TEMPLATE = """You are the coding worker for a single implementation task.

Task:
- Title: $title
- Repo: $target_repo
- Task ID: $task_id
- Issue: $issue_url

Context:
- Background:
$background

- Goal:
$goal

- Agreed Scope:
$agreed_scope

- Impacted Modules:
$impacted_modules

- Repo-specific Scope:
$repo_summary

- Modules In Scope:
$repo_modules

- Required Changes:
$repo_required_changes

- Boundaries:
$repo_boundaries

- Non-goals:
$non_goals

- Acceptance Criteria:
$repo_acceptance_criteria

- Risks:
$repo_risks

- References:
$references

Execution rules:
- Work only on this task.
- Use the assigned worktree: $worktree_path
- Use the assigned branch: $branch_name
- Use the assigned runtime session name: $tmux_session
- Run relevant tests before finishing.
- The runtime will append the exact completion contract, including the `result.json` path you must write before exiting.
- When implementation is complete, write a UTF-8 JSON result with:
  {
    "status": "completed",
    "outcome": "success",
    "summary": "Implemented the requested change and validated it.",
    "artifacts": [],
    "metadata": {
      "commit_message": "feat: finish $task_id",
      "commit_sha": "optional when you already committed locally",
      "pull_request_url": "optional when you already opened a GitHub PR",
      "merge_request_url": "optional when you already opened a GitLab MR",
      "pr_url": "optional normalized PR/MR URL field",
      "tests_run": ["python -m unittest"],
      "notes": ["optional notes"]
    },
    "error": null
  }
- When `outcome` is `"success"`, include either:
  - `metadata.commit_message` if you want the bridge/watchdog to create the local commit, or
  - `metadata.commit_sha` when you already committed yourself. Include `metadata.pull_request_url`, `metadata.merge_request_url`, or `metadata.pr_url` too if you already opened a PR/MR.
- If you are blocked or need help, still write the runtime `result.json`, set `outcome` to `"failure"` or `"needs_human"`, and explain the blocker in `error`.
- Open a PR when implementation is ready.
- If blocked, stop and report the blocker clearly.
"""

INIT_DOCS_REPO_SKILL_NAME = "openclaw-init-docs-repo"
BOOTSTRAP_SKILL_NAME = "openclaw-project-bootstrap"
DISCUSSION_TO_PRD_SKILL_NAME = "openclaw-discussion-to-prd"
PRD_TO_REPO_ISSUES_SKILL_NAME = "openclaw-prd-to-repo-issues"

SKILL_DEFINITIONS = {
    INIT_DOCS_REPO_SKILL_NAME: {
        "codex_files": {
            "SKILL.md": INIT_DOCS_REPO_SKILL_TEMPLATE,
            "agents/openai.yaml": INIT_DOCS_REPO_SKILL_OPENAI_YAML,
        },
        "claude_agent_filename": f"{INIT_DOCS_REPO_SKILL_NAME}.md",
        "claude_agent_content": CLAUDE_INIT_DOCS_REPO_AGENT_TEMPLATE,
    },
    BOOTSTRAP_SKILL_NAME: {
        "codex_files": {
            "SKILL.md": BOOTSTRAP_SKILL_TEMPLATE,
            "agents/openai.yaml": BOOTSTRAP_SKILL_OPENAI_YAML,
        },
        "claude_agent_filename": f"{BOOTSTRAP_SKILL_NAME}.md",
        "claude_agent_content": CLAUDE_BOOTSTRAP_AGENT_TEMPLATE,
    },
    DISCUSSION_TO_PRD_SKILL_NAME: {
        "codex_files": {
            "SKILL.md": DISCUSSION_TO_PRD_SKILL_TEMPLATE,
            "agents/openai.yaml": DISCUSSION_TO_PRD_SKILL_OPENAI_YAML,
            "references/module-impact-rules.md": MODULE_IMPACT_RULES_TEMPLATE,
        },
        "claude_agent_filename": f"{DISCUSSION_TO_PRD_SKILL_NAME}.md",
        "claude_agent_content": CLAUDE_DISCUSSION_TO_PRD_AGENT_TEMPLATE,
    },
    PRD_TO_REPO_ISSUES_SKILL_NAME: {
        "codex_files": {
            "SKILL.md": PRD_TO_REPO_ISSUES_SKILL_TEMPLATE,
            "agents/openai.yaml": PRD_TO_REPO_ISSUES_SKILL_OPENAI_YAML,
            "references/issue-publish-workflow.md": PRD_TO_REPO_ISSUES_PUBLISH_REFERENCE,
        },
        "claude_agent_filename": f"{PRD_TO_REPO_ISSUES_SKILL_NAME}.md",
        "claude_agent_content": CLAUDE_PRD_TO_REPO_ISSUES_AGENT_TEMPLATE,
    },
}


def init_docs_repo(
    target_dir: Path,
    *,
    project_name: str | None = None,
    project_description: str | None = None,
    force: bool = False,
) -> list[Path]:
    target_dir.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    resolved_project_name = project_name or target_dir.name
    resolved_project_description = (
        project_description or "Fill in a short summary of what this project is for."
    )

    dirs = [
        target_dir / "docs" / "prd",
        target_dir / "docs" / "rfc",
        target_dir / "docs" / "meeting-notes",
        target_dir / "docs" / "project",
        target_dir / "docs" / "modules",
        target_dir / ".codex" / "skills" / BOOTSTRAP_SKILL_NAME / "agents",
        target_dir / ".codex" / "skills" / DISCUSSION_TO_PRD_SKILL_NAME / "agents",
        target_dir / ".codex" / "skills" / DISCUSSION_TO_PRD_SKILL_NAME / "references",
        target_dir / ".codex" / "skills" / PRD_TO_REPO_ISSUES_SKILL_NAME / "agents",
        target_dir / ".codex" / "skills" / PRD_TO_REPO_ISSUES_SKILL_NAME / "references",
        target_dir / ".openclaw" / "prompts",
        target_dir / ".openclaw-state" / "artifacts",
        target_dir / ".claude" / "agents",
    ]
    for directory in dirs:
        directory.mkdir(parents=True, exist_ok=True)

    files = {
        target_dir / "AGENTS.md": AGENTS_TEMPLATE.format(
            project_name=resolved_project_name,
            project_description=resolved_project_description,
            docs_repo=str(target_dir),
        ),
        target_dir / "docs" / "README.md": DOCS_INDEX_TEMPLATE,
        target_dir / "docs" / "project" / "project-overview.md": PROJECT_OVERVIEW_TEMPLATE.format(
            project_name=resolved_project_name,
            project_description=resolved_project_description,
        ),
        target_dir / "docs" / "modules" / "module-map.md": MODULE_MAP_TEMPLATE,
        target_dir / "docs" / "prd" / "README.md": SECTION_README_TEMPLATE.format(
            title="PRD",
            description="Store one execution-ready requirement document per feature or change request.",
        ),
        target_dir / "docs" / "rfc" / "README.md": SECTION_README_TEMPLATE.format(
            title="RFC",
            description="Store technical proposals, architecture decisions, and implementation design notes.",
        ),
        target_dir / "docs" / "meeting-notes" / "README.md": SECTION_README_TEMPLATE.format(
            title="Meeting Notes",
            description="Store raw notes from customer calls, internal discussions, and discovery sessions.",
        ),
        target_dir / ".openclaw" / "config.json": json.dumps(
            DEFAULT_CONFIG, indent=2, ensure_ascii=False
        )
        + "\n",
        target_dir / ".openclaw" / "spec-template.md": SPEC_TEMPLATE,
        target_dir / ".openclaw" / "prompts" / "implementation-issue.md.tmpl": ISSUE_TEMPLATE,
        target_dir / ".openclaw" / "prompts" / "worker-task.txt.tmpl": WORKER_TEMPLATE,
        target_dir / ".openclaw-state" / "registry.json": json.dumps(
            {"tasks": []}, indent=2, ensure_ascii=False
        )
        + "\n",
    }

    for skill_name, definition in SKILL_DEFINITIONS.items():
        for relative_path, content in definition["codex_files"].items():
            files[target_dir / ".codex" / "skills" / skill_name / relative_path] = content
        files[
            target_dir / ".claude" / "agents" / definition["claude_agent_filename"]
        ] = definition["claude_agent_content"]

    for path, content in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and not force:
            continue
        path.write_text(content, encoding="utf-8")
        created.append(path)

    created.extend(_install_codex_skills(force=force))
    created.extend(_install_claude_agents(force=force))

    gitignore_path = target_dir / ".gitignore"
    gitignore_entries = [".openclaw-state/"]
    if gitignore_path.exists():
        existing_lines = gitignore_path.read_text(encoding="utf-8").splitlines()
    else:
        existing_lines = []
    updated = list(existing_lines)
    for entry in gitignore_entries:
        if entry not in updated:
            updated.append(entry)
    if updated != existing_lines:
        gitignore_path.write_text("\n".join(updated).rstrip() + "\n", encoding="utf-8")
        created.append(gitignore_path)

    return created


def _install_codex_skills(*, force: bool) -> list[Path]:
    created: list[Path] = []
    for skill_name, definition in SKILL_DEFINITIONS.items():
        for relative_path, content in definition["codex_files"].items():
            path = _codex_skills_dir() / skill_name / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and not force:
                continue
            path.write_text(content, encoding="utf-8")
            created.append(path)
    return created


def _codex_skills_dir() -> Path:
    codex_home = os.environ.get("CODEX_HOME")
    if codex_home:
        return Path(codex_home).expanduser() / "skills"
    return Path.home() / ".codex" / "skills"


def _install_claude_agents(*, force: bool) -> list[Path]:
    agents_dir = _claude_agents_dir()
    agents_dir.mkdir(parents=True, exist_ok=True)

    created: list[Path] = []
    for definition in SKILL_DEFINITIONS.values():
        path = agents_dir / definition["claude_agent_filename"]
        if path.exists() and not force:
            continue
        path.write_text(definition["claude_agent_content"], encoding="utf-8")
        created.append(path)
    return created


def _claude_agents_dir() -> Path:
    claude_home = os.environ.get("CLAUDE_HOME")
    if claude_home:
        return Path(claude_home).expanduser() / "agents"
    return Path.home() / ".claude" / "agents"
