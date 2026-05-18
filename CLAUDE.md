# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install in editable mode (required before running anything)
pip install -e .

# Run all tests
python -m pytest tests/

# Run a single test file
python -m pytest tests/test_cli.py

# Run a single test case
python -m pytest tests/test_cli.py::DispatchSpecsTests::test_dispatch_specs_accepts_single_spec_file_path

# Run via unittest directly
python -m unittest tests.test_cli
python -m unittest tests.test_scaffold

# Use the CLI directly (after pip install -e .)
oryx init-docs-repo --target-dir /path/to/docs-repo --project-name "..." --project-description "..."
oryx dispatch-approved --docs-repo /path/to/docs-repo/docs/prd/feature.md
oryx dispatch-approved --docs-repo /path/to/docs-repo/docs/prd/batch/ --create-issues
oryx dispatch-approved --docs-repo /path/to/docs-repo/docs/prd/batch/ --create-issues --dry-run

# Backward-compatible legacy CLI name
openclaw-mvp1 init-docs-repo --target-dir /path/to/docs-repo --project-name "..." --project-description "..."
openclaw-mvp1 dispatch-approved --docs-repo /path/to/docs-repo/docs/prd/feature.md
openclaw-mvp1 dispatch-approved --docs-repo /path/to/docs-repo/docs/prd/batch/ --create-issues
openclaw-mvp1 dispatch-approved --docs-repo /path/to/docs-repo/docs/prd/batch/ --create-issues --dry-run

# Or run the module directly
python -m openclaw_bridge.cli <subcommand>
```

No linter or type-checker is configured in `pyproject.toml`. No external runtime dependencies — stdlib only.

## Architecture

Oryx currently has two roles:

**1. Docs-repo scaffolding** (`init-docs-repo`): Initializes a separate "docs repo" (a plain directory, not this repo) with OpenClaw contracts — AGENTS.md, config, prompt templates, skill files, and Claude/Codex agent installs. The docs repo is where requirement documents live. Skills and agents are installed both repo-locally (`.codex/skills/`, `.claude/agents/`) and globally (`CODEX_HOME/skills` or `~/.codex/skills`, `CLAUDE_HOME/agents` or `~/.claude/agents`).

**2. Spec dispatch** (`dispatch-approved` / `ingest`): Reads approved PRD markdown files from a docs repo's `docs/prd/` subtree, validates them, generates two artifact files per (spec × target_repo) pair, and optionally creates GitHub (`gh` CLI) or GitLab (`glab` CLI) issues.

### Module map

- `openclaw_bridge/specs.py` — Parses markdown PRD files. Extracts YAML frontmatter and `##`-headed sections. Section names are normalized via `SECTION_ALIASES` (including Chinese aliases). `## Repo-specific Scope` contains `###`-headed sub-sections, one per target repo, parsed via `REPO_SCOPE_FIELD_ALIASES`.
- `openclaw_bridge/models.py` — Two spec dataclasses (`ApprovedSpec`, `RepoScope`) and `TaskRecord` (one per spec×repo pair), with `to_dict`/`from_dict` for registry persistence.
- `openclaw_bridge/registry.py` — Loads/saves `registry.json` (a `{"tasks": [...]}` JSON file). Deduplicates by `source_ref + target_repo`. Issues monotonically increasing `task-NNNN` IDs.
- `openclaw_bridge/scaffold.py` — All template strings (AGENTS.md, config.json, spec template, prompt templates, skill SKILL.md content, Claude agent markdown). `SKILL_DEFINITIONS` dict drives what gets installed where. `init_docs_repo()` writes everything.
- `openclaw_bridge/cli.py` — Argument parsing and orchestration. `dispatch_specs()` resolves scope (single file or subdirectory, never the docs root or specs root), loads config, iterates specs, calls `_build_task_record()`, writes artifacts, and optionally calls `_dispatch_issue()` which routes to GitHub (`gh` CLI) or GitLab (`glab` CLI).

### Key constraints

- Dispatch scope must be a single `.md` file or a subdirectory inside `docs/prd/` — never the docs repo root or the `docs/prd/` root itself.
- `README.md` files inside `docs/prd/` are silently skipped.
- Registry is append-only with deduplication: re-running dispatch on an existing `source_ref + target_repo` pair re-renders artifacts but does not create a new registry entry or re-open an issue.
- GitHub issue creation requires the `gh` CLI. GitLab issue creation requires the `glab` CLI.
- The `build/` directory contains a stale copy of the package from a previous `python -m build` run; the live source is `openclaw_bridge/`.

### Docs-repo layout (what `init-docs-repo` creates)

```
<target-dir>/
  AGENTS.md                        # Codex behavior contract
  .openclaw/
    config.json                    # Bridge config (edit repo_mappings here)
    spec-template.md               # Blank PRD template
    prompts/
      implementation-issue.md.tmpl # Issue body template (Python string.Template)
      worker-task.txt.tmpl         # Worker prompt template
  .openclaw-state/
    registry.json                  # Written by dispatch-approved
    artifacts/<task-id>/
      implementation-issue.md
      worker-prompt.txt
  docs/
    README.md
    project/project-overview.md
    modules/module-map.md
    prd/                           # Put PRD files here
  .codex/skills/<skill-name>/      # Repo-local Codex skills
  .claude/agents/<agent>.md        # Repo-local Claude agents
```
