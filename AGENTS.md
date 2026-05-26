# AGENTS.md

## Project Purpose

Oryx is the local bridge for OpenClaw-style agent orchestration. Its main job is to initialize separate docs projects, preserve project and business context there, split approved requirements into repo-specific implementation work, and prepare that work for coding agents.

Current implementation should be treated as the first slice of that larger goal:

- Initialize docs repos for different projects with OpenClaw contracts, PRD templates, prompts, skills, agent instructions, and project/module context.
- Turn requirements and approved PRDs into concrete artifacts for target repositories, including implementation issue bodies and worker prompts.
- Keep strategic context in the docs repo/orchestration layer while coding agents stay focused on code-level tasks.
- Support a future workflow where an orchestrator can pick the right agent or model, spawn isolated worktrees or sessions, monitor progress, retry with improved prompts, and notify the user only when work is ready for review.
- Treat "ready" as more than PR creation: branch sync, CI/tests, automated review, screenshots for UI work, and human review handoff all matter.
- Learn from successful and failed agent runs by recording what prompts, context, files, and validation steps helped work ship.

The original product idea in `docs/idea.txt` is a two-tier agent system: an orchestrator such as Zoe holds long-lived business context, meeting notes, customer history, past decisions, and retry lessons, then translates that context into precise prompts for specialized coding agents such as Codex, Claude Code, or Gemini. Oryx should evolve toward that workflow while keeping the current codebase simple and verifiable.

## Startup Workflow

1. Confirm the working directory is this repo.
2. Read `CLAUDE.md` for commands, architecture, and project constraints.
3. Check `feature_list.json` and `progress.md` for current state.
4. Run `./init.sh` before claiming a change is complete, or record why a narrower check was used.

## Working Rules

- One feature at a time: work on one feature or fix at a time.
- Stay in scope: do not refactor unrelated code or generated output.
- Keep changes surgical and match the surrounding Python style.
- Treat `openclaw_bridge/` as the live source; `build/` is stale generated output.
- Record verification evidence in `progress.md` or `feature_list.json` when changing tracked work.

## Definition of Done

A task is done only when the requested behavior is implemented, relevant verification has run, and any changed harness state is updated.

## Verification

Use `./init.sh` for the standard check. It installs the package in editable mode, runs the test suite, and compiles the live Python packages.

## End of Session

Update `progress.md` and `feature_list.json` when tracked work changes. Leave any blockers or next steps in `session-handoff.md` for longer tasks.

