# Oryx

Oryx is a local orchestration runtime for AI coding workers. It packages the
current OpenClaw-style MVP flow as two things:

- a lightweight bridge CLI for docs repo initialization and approved-spec dispatch
- reusable local skills at `skills/openclaw-init-docs-repo/`,
  `skills/openclaw-project-bootstrap/`,
  `skills/openclaw-discussion-to-prd/`, and `skills/openclaw-prd-to-repo-issues/`

It still does not try to rebuild OpenClaw itself. The goal is to support the split workflow
you described:

- discuss and approve requirements in a docs repo with Codex or Claude Code
- initialize that docs repo once with the required OpenClaw contracts
- dispatch approved requirements into real implementation issues only when you decide to run them
- launch and reconcile local coding workers through the integrated `orx` runtime

## Structure

```text
configs/            Sample config shape
examples/           Verification fixtures
openclaw_bridge/    Python bridge CLI
orx/                Thin local orchestration runtime integrated into this repo
prompts/            Root template copies
skills/             Reusable local skill
```

## Usage

Initialize a docs repo with the skill-first path:

- Use `openclaw-init-docs-repo`
- Let it ask for `target_dir`, `project_name`, and `project_description`
- It should run the init CLI itself instead of handing the command back to you

Command fallback when you need a direct CLI path:

```bash
oryx init-docs-repo \
  --target-dir /absolute/path/to/docs-repo \
  --project-name "Example Project" \
  --project-description "Short summary of what this project does"
```

This also installs `openclaw-init-docs-repo`, `openclaw-project-bootstrap`,
`openclaw-discussion-to-prd`, and `openclaw-prd-to-repo-issues` into:

- Codex: `$CODEX_HOME/skills` when `CODEX_HOME` is set, otherwise `~/.codex/skills`
- Claude Code: `$CLAUDE_HOME/agents` when `CLAUDE_HOME` is set, otherwise `~/.claude/agents`

The initialized docs repo also gets repo-local copies under `.codex/skills/` and `.claude/agents/`.
Restart Codex and Claude Code after init so the new global assets are loaded.

Dispatch one approved spec into registry and prompt artifacts:

```bash
oryx dispatch-approved \
  --docs-repo /absolute/path/to/docs-repo/docs/prd/feature-a.md
```

Dispatch all approved specs in one PRD subdirectory and open mapped GitHub or GitLab issues:

```bash
oryx dispatch-approved \
  --docs-repo /absolute/path/to/docs-repo/docs/prd/batch-db \
  --create-issues
```

The docs repo root and the `docs/prd` specs root are both intentionally rejected to avoid ambiguous bulk dispatch.

## Current Scope

Implemented now:

- root `AGENTS.md` scaffold for docs-repo behavior
- repo-local Codex init skill scaffold for docs-repo creation
- repo-local Codex skill scaffolds for bootstrap and discussion-to-PRD
- automatic Codex global skill install to `$CODEX_HOME/skills` or `~/.codex/skills`
- Claude Code init agent scaffold for docs-repo creation
- Claude Code agent scaffolds for bootstrap and discussion-to-PRD
- automatic Claude Code global agent install to `$CLAUDE_HOME/agents` or `~/.claude/agents`
- `docs/README.md` placement guide
- `docs/project/project-overview.md` template
- `docs/modules/module-map.md` template
- docs repo scaffold with `.openclaw/` and `.openclaw-state/`
- markdown frontmatter trigger: `status: approved`
- required field validation
- `README.md` files under `docs/prd/` are ignored during dispatch
- registry write with deduplication by `source_ref + target_repo`
- task decomposition per affected repo
- issue/prompt artifact generation
- richer issue generation from `Agreed Scope`, `Impacted Modules`, and `Repo-specific Scope`
- optional GitHub issue creation through `gh issue create`
- optional GitLab issue creation through `glab issue create`
- integrated `orx` package for thin local task orchestration
- unified monitor that can register multiple projects by `orx` runtime root
- worktree preparation, tmux launch, and runtime wrapper logging
- watchdog/reconcile flow driven by unified `result.json` completion artifacts
- optional webhook sidecar for watchdog event delivery

## Notifications

`watch-workers` still writes local watchdog events to
`.openclaw-state/watchdog-events.jsonl`. When `.openclaw/config.json` includes a
non-empty `notifications.webhook_url`, the same watchdog tick will also make one
best-effort webhook `POST` containing the newly emitted events selected by
`notifications.events`.

- Notification failures do not block task state changes, safe restarts, or commit handling.
- `notifications.headers` is for static non-secret headers.
- `notifications.bearer_token_env` points to an environment variable whose value is sent as
  `Authorization: Bearer ...`.
- This webhook is intentionally generic. Route it through n8n, a small relay, or your chat
  platform's incoming-webhook adapter instead of coupling the CLI to a specific IM vendor.
