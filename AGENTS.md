# AGENTS.md

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

## Related Projects

- `orx`
  Path: `/Users/qiuxu/opensource-project/orx`
  Purpose: thin local orchestration SDK for Codex / Claude Code task execution via local `tmux` and SQLite
