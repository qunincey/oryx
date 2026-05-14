---
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
