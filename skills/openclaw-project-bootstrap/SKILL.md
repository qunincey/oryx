---
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
- When a module maps to a real local Git repository, inspect `git remote get-url origin` and write the resolved
  forge path into `.openclaw/config.json`.
  Use `github_repo` for GitHub-family remotes and `gitlab_host` + `gitlab_repo` for GitLab-family remotes.
- Only leave repo targets as `TBD/...` when no local Git remote exists or the remote cannot be mapped to a concrete
  forge repository path. In that case, call the uncertainty out explicitly in the bootstrap notes or summary.
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
