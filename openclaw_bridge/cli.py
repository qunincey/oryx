from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from string import Template
from urllib.parse import urlparse

from orx import (
    Orchestrator,
    TaskFailureReason as OrxTaskFailureReason,
    TaskRequest,
    TaskResult as OrxTaskResult,
    TaskStatus as OrxTaskStatus,
)

from .monitor.config import ensure_monitor_config_path
from .monitor.server import run_monitor_server
from .models import TaskRecord
from .notifications import send_watch_notifications
from .registry import find_existing_task, load_registry, next_task_id, save_registry
from .runtime import resolve_local_repo_path
from .scaffold import (
    DEFAULT_CONFIG,
    ISSUE_TEMPLATE,
    WORKER_TEMPLATE,
    init_docs_repo,
)
from .specs import SpecValidationError, parse_markdown_spec
from .worker_models import normalize_worker_model, resolve_explicit_worker_model

_LAUNCHABLE_STATES = {"dispatched", "restartable"}
_RECONCILE_ACTIVE_STATES = {"dispatched", "in_progress", "restartable", "exited"}
_WATCHABLE_STATES = {"dispatched", "in_progress", "restartable", "exited", "blocked"}


def main() -> None:
    parser = argparse.ArgumentParser(description="OpenClaw MVP-1 integration bridge.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser(
        "init-docs-repo",
        help="Scaffold a docs repository with OpenClaw templates and config.",
    )
    init_parser.add_argument("--target-dir", required=True, type=Path)
    init_parser.add_argument("--project-name")
    init_parser.add_argument("--project-description")
    init_parser.add_argument("--force", action="store_true")

    dispatch_parser = subparsers.add_parser(
        "dispatch-approved",
        aliases=["ingest"],
        help="Validate approved specs, create task artifacts, and optionally open GitHub or GitLab issues.",
    )
    dispatch_parser.add_argument(
        "--docs-repo",
        required=True,
        type=Path,
        help="Path to one markdown spec file or one subdirectory inside specs_dir. The docs repo root is not allowed.",
    )
    dispatch_parser.add_argument("--config", type=Path)
    dispatch_parser.add_argument("--create-issues", action="store_true")
    dispatch_parser.add_argument("--dry-run", action="store_true")
    dispatch_parser.add_argument("--launch-workers", action="store_true")

    launch_parser = subparsers.add_parser(
        "launch-workers",
        help="Create worktrees and Orx-managed worker tasks for dispatched tasks.",
    )
    launch_parser.add_argument(
        "--docs-repo",
        required=True,
        type=Path,
        help="Path to an OpenClaw docs repo root or any path inside it.",
    )
    launch_parser.add_argument("--config", type=Path)
    launch_parser.add_argument("--task-id")
    launch_parser.add_argument(
        "--state",
        help="Only launch tasks in this state. Defaults to all launchable states: dispatched,restartable.",
    )

    reconcile_parser = subparsers.add_parser(
        "reconcile-workers",
        help="Reconcile worker task state with Orx runtime state and optionally restart safe failures.",
    )
    reconcile_parser.add_argument(
        "--docs-repo",
        required=True,
        type=Path,
        help="Path to an OpenClaw docs repo root or any path inside it.",
    )
    reconcile_parser.add_argument("--config", type=Path)
    reconcile_parser.add_argument("--task-id")
    reconcile_parser.add_argument(
        "--state",
        help="Only inspect tasks in this state. Defaults to active worker states.",
    )
    reconcile_parser.add_argument(
        "--restart",
        action="store_true",
        help="Relaunch tasks that reconcile into restartable.",
    )

    watch_parser = subparsers.add_parser(
        "watch-workers",
        help="Run one watchdog tick: reconcile, optionally restart, commit completed work, and emit events.",
    )
    watch_parser.add_argument(
        "--docs-repo",
        required=True,
        type=Path,
        help="Path to an OpenClaw docs repo root or any path inside it.",
    )
    watch_parser.add_argument("--config", type=Path)
    watch_parser.add_argument("--task-id")
    watch_parser.add_argument(
        "--state",
        help="Only inspect tasks in this state. Defaults to watchable worker states.",
    )
    watch_parser.add_argument(
        "--restart",
        action="store_true",
        help="Relaunch tasks that settle into restartable during the watchdog tick.",
    )
    watch_parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON instead of the human summary.",
    )
    watch_parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single watchdog tick. This command is already one-shot; the flag is accepted for scheduler clarity.",
    )

    monitor_parser = subparsers.add_parser(
        "monitor",
        help="Serve a read-only multi-project task monitor dashboard.",
    )
    monitor_parser.add_argument("--config", type=Path)
    monitor_parser.add_argument("--host", default="127.0.0.1")
    monitor_parser.add_argument("--port", type=int)

    args = parser.parse_args()
    try:
        if args.command == "init-docs-repo":
            run_init(args)
            return
        if args.command in {"dispatch-approved", "ingest"}:
            dispatch_specs(args)
            return
        if args.command == "launch-workers":
            launch_workers(args)
            return
        if args.command == "reconcile-workers":
            reconcile_workers(args)
            return
        if args.command == "watch-workers":
            watch_workers(args)
            return
        if args.command == "monitor":
            monitor_command(args)
    except (FileNotFoundError, ValueError) as exc:
        parser.exit(2, f"error: {exc}\n")


def run_init(args: argparse.Namespace) -> None:
    created = init_docs_repo(
        args.target_dir,
        project_name=args.project_name,
        project_description=args.project_description,
        force=args.force,
    )
    print(f"initialized={args.target_dir}")
    for path in created:
        print(f"  - {path}")
    print("note=restart Codex and Claude Code after init to load or refresh installed global skills/agents")


def dispatch_specs(args: argparse.Namespace) -> None:
    docs_repo, dispatch_scope = _resolve_dispatch_scope(args.docs_repo)
    config_path = args.config.resolve() if args.config else docs_repo / ".openclaw" / "config.json"
    config = _load_config(config_path)

    specs_dir = (docs_repo / str(config["specs_dir"])).resolve()
    runtime_dir = docs_repo / config["runtime_dir"]
    registry_path = runtime_dir / "registry.json"
    artifacts_dir = runtime_dir / "artifacts"
    issue_template_path = docs_repo / config["issue_template"]
    worker_template_path = docs_repo / config["worker_template"]

    registry = load_registry(registry_path)
    issue_template = _load_template(
        issue_template_path,
        default_content=ISSUE_TEMPLATE,
    )
    worker_template = _load_template(
        worker_template_path,
        default_content=WORKER_TEMPLATE,
    )

    created = 0
    dispatched = 0
    launched = 0
    skipped: list[str] = []
    blocked: list[str] = []

    for spec_path in _resolve_spec_paths(dispatch_scope, specs_dir):
        if spec_path.name.lower() == "readme.md":
            continue
        try:
            spec = parse_markdown_spec(spec_path)
        except SpecValidationError as exc:
            blocked.append(str(exc))
            continue

        if spec.status != config["approved_status"]:
            continue

        source_ref = str(spec_path.relative_to(docs_repo))
        for target_repo in spec.affected_repos:
            existing = find_existing_task(registry, source_ref, target_repo)
            was_existing = existing is not None
            if existing is None:
                created += 1
                existing = _build_task_record(
                    config=config,
                    docs_repo=docs_repo,
                    artifacts_dir=artifacts_dir,
                    issue_template=issue_template,
                    worker_template=worker_template,
                    spec_path=spec_path,
                    spec=spec,
                    target_repo=target_repo,
                    task_id=next_task_id(registry),
                )
                registry.append(existing)
            else:
                _render_artifacts(
                    issue_template=issue_template,
                    worker_template=worker_template,
                    spec=spec,
                    task=existing,
                    docs_repo=docs_repo,
                )

            if args.create_issues and not existing.target_issue_number:
                dispatched_now = _dispatch_issue(
                    task=existing,
                    config=config,
                    docs_repo=docs_repo,
                    dry_run=args.dry_run,
                    blocked=blocked,
                )
                dispatched += dispatched_now
                if dispatched_now:
                    _render_artifacts(
                        issue_template=issue_template,
                        worker_template=worker_template,
                        spec=spec,
                        task=existing,
                        docs_repo=docs_repo,
                    )
            elif was_existing:
                skipped.append(f"{source_ref} -> {target_repo} ({existing.task_id})")

    if args.launch_workers:
        _synchronize_in_progress_tasks(tasks=registry, config=config, docs_repo=docs_repo)
        launched = _launch_tasks(
            tasks=registry,
            config=config,
            docs_repo=docs_repo,
            task_id=None,
            state="dispatched",
            blocked=blocked,
        )

    save_registry(registry_path, registry)
    _print_summary(created, dispatched, launched, skipped, blocked, registry_path)


def launch_workers(args: argparse.Namespace) -> None:
    docs_repo = _resolve_docs_repo_root(args.docs_repo)
    config_path = args.config.resolve() if args.config else docs_repo / ".openclaw" / "config.json"
    config = _load_config(config_path)
    registry_path = docs_repo / config["runtime_dir"] / "registry.json"
    registry = load_registry(registry_path)
    blocked: list[str] = []
    _synchronize_in_progress_tasks(tasks=registry, config=config, docs_repo=docs_repo)
    launched = _launch_tasks(
        tasks=registry,
        config=config,
        docs_repo=docs_repo,
        task_id=args.task_id,
        state=args.state,
        blocked=blocked,
    )
    save_registry(registry_path, registry)
    _print_summary(0, 0, launched, [], blocked, registry_path)


def reconcile_workers(args: argparse.Namespace) -> None:
    docs_repo = _resolve_docs_repo_root(args.docs_repo)
    config_path = args.config.resolve() if args.config else docs_repo / ".openclaw" / "config.json"
    config = _load_config(config_path)
    registry_path = docs_repo / config["runtime_dir"] / "registry.json"
    registry = load_registry(registry_path)
    blocked: list[str] = []
    reconciled = _reconcile_tasks(
        tasks=registry,
        config=config,
        docs_repo=docs_repo,
        task_id=args.task_id,
        state=args.state,
        blocked=blocked,
    )
    restarted = 0
    if args.restart:
        restarted = _launch_tasks(
            tasks=registry,
            config=config,
            docs_repo=docs_repo,
            task_id=args.task_id,
            state="restartable",
            blocked=blocked,
        )
    save_registry(registry_path, registry)
    _print_reconcile_summary(
        reconciled=reconciled,
        restarted=restarted,
        blocked=blocked,
        registry_path=registry_path,
    )


def watch_workers(args: argparse.Namespace) -> None:
    docs_repo = _resolve_docs_repo_root(args.docs_repo)
    config_path = args.config.resolve() if args.config else docs_repo / ".openclaw" / "config.json"
    config = _load_config(config_path)
    registry_path = docs_repo / config["runtime_dir"] / "registry.json"
    events_path = docs_repo / config["runtime_dir"] / "watchdog-events.jsonl"
    registry = load_registry(registry_path)
    result = _watch_tasks(
        tasks=registry,
        config=config,
        docs_repo=docs_repo,
        task_id=args.task_id,
        state=args.state,
        restart=args.restart,
        registry_path=registry_path,
        events_path=events_path,
    )
    save_registry(registry_path, registry)
    _append_watchdog_events(events_path, result["events"])
    result.update(
        send_watch_notifications(
            config=config,
            docs_repo=docs_repo,
            events_path=events_path,
            events=result["events"],
        )
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    _print_watch_summary(result=result)


def monitor_command(args: argparse.Namespace) -> None:
    config_path, created = ensure_monitor_config_path(args.config.resolve() if args.config else None)
    if created:
        print(f"created_monitor_config={config_path}")
        print("note=starter config created with no projects; add OpenClaw docs repos to projects[] and rerun monitor, or launch now to view an empty dashboard.")
    run_monitor_server(
        config_path=config_path,
        host=str(args.host or "127.0.0.1"),
        port=args.port,
    )


def _resolve_dispatch_scope(path: Path) -> tuple[Path, Path]:
    resolved = path.resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"{resolved} does not exist")
    docs_repo = _find_docs_repo_root(resolved if resolved.is_dir() else resolved.parent)
    if resolved == docs_repo:
        raise ValueError(
            f"{resolved} is the docs repo root. Pass one markdown spec file or one subdirectory inside {docs_repo / 'docs' / 'prd'}."
        )
    return docs_repo, resolved


def _resolve_docs_repo_root(path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"{resolved} does not exist")
    return _find_docs_repo_root(resolved if resolved.is_dir() else resolved.parent)


def _find_docs_repo_root(start: Path) -> Path:
    for candidate in (start, *start.parents):
        if (candidate / ".openclaw").is_dir():
            return candidate
    raise ValueError(
        f"Could not find an OpenClaw docs repo root above {start}; expected an ancestor containing .openclaw/"
    )


def _resolve_spec_paths(dispatch_scope: Path, specs_dir: Path) -> list[Path]:
    resolved = dispatch_scope.resolve()
    try:
        resolved.relative_to(specs_dir)
    except ValueError as exc:
        raise ValueError(
            f"{resolved} is outside the configured specs_dir {specs_dir}"
        ) from exc

    if resolved.is_file():
        if resolved.suffix.lower() != ".md":
            raise ValueError(f"{resolved} is not a markdown spec file")
        return [resolved]

    if not resolved.is_dir():
        raise ValueError(f"{resolved} is neither a directory nor a markdown spec file")

    if resolved == specs_dir:
        raise ValueError(
            f"{resolved} is the specs root. Pass one markdown spec file or one subdirectory inside {specs_dir}."
        )

    spec_paths = sorted(resolved.rglob("*.md"))
    if not spec_paths:
        raise ValueError(f"No markdown spec files found under {resolved}")
    return spec_paths


def _load_config(path: Path) -> dict[str, object]:
    config = json.loads(json.dumps(DEFAULT_CONFIG))
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        for key, value in payload.items():
            config[key] = value
    config["worker_model"] = normalize_worker_model(
        worker_type=str(config.get("worker_type") or ""),
        worker_model=str(config.get("worker_model") or ""),
    )
    return config


def _load_template(path: Path, *, default_content: str) -> Template:
    if path.exists():
        return Template(path.read_text(encoding="utf-8"))

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(default_content, encoding="utf-8")
    return Template(default_content)


def _build_task_record(
    *,
    config: dict[str, object],
    docs_repo: Path,
    artifacts_dir: Path,
    issue_template: Template,
    worker_template: Template,
    spec_path: Path,
    spec,
    target_repo: str,
    task_id: str,
) -> TaskRecord:
    now = _timestamp()
    slug = _slugify(spec.title)
    branch_name = f"openclaw/{task_id}/{slug}"
    worktree_path = f"../worktrees/{target_repo}/{task_id}-{slug}"
    tmux_session = f"swarm-{task_id}"
    issue_title = f"[OpenClaw] {spec.title}"
    artifact_dir = artifacts_dir / task_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    issue_body_path = artifact_dir / "implementation-issue.md"
    worker_prompt_path = artifact_dir / "worker-prompt.txt"

    task = TaskRecord(
        task_id=task_id,
        source_repo=str(config["source_repo"]),
        source_ref=str(spec_path.relative_to(docs_repo)),
        source_title=spec.title,
        target_repo=target_repo,
        target_issue_number=None,
        target_issue_url=None,
        state="decomposed",
        worker_type=str(config["worker_type"]),
        worker_model=str(config["worker_model"]),
        worktree_path=worktree_path,
        tmux_session=tmux_session,
        branch_name=branch_name,
        pr_url=None,
        last_error=None,
        created_at=now,
        updated_at=now,
        issue_title=issue_title,
        issue_body_path=str(issue_body_path),
        worker_prompt_path=str(worker_prompt_path),
    )
    _render_artifacts(
        issue_template=issue_template,
        worker_template=worker_template,
        spec=spec,
        task=task,
        docs_repo=docs_repo,
    )
    return task


def _render_artifacts(
    *,
    issue_template: Template,
    worker_template: Template,
    spec,
    task: TaskRecord,
    docs_repo: Path,
) -> None:
    issue_body_path = Path(task.issue_body_path)
    worker_prompt_path = Path(task.worker_prompt_path)
    issue_url = task.target_issue_url or "pending-issue"
    repo_scope = spec.repo_scopes.get(task.target_repo)
    repo_summary = _build_repo_summary(repo_scope)
    repo_modules = _resolve_repo_modules(spec, repo_scope)
    repo_required_changes = _resolve_repo_required_changes(spec, repo_scope)
    repo_boundaries = _resolve_repo_boundaries(spec, repo_scope)
    repo_acceptance = _resolve_repo_acceptance(spec, repo_scope)
    repo_risks = _merge_lists(
        repo_scope.risks if repo_scope else [],
        spec.risks or ["No explicit risks captured yet."],
    )
    repo_references = _merge_lists(
        repo_scope.references if repo_scope else [],
        spec.references,
    )
    context = {
        "task_id": task.task_id,
        "title": spec.title,
        "target_repo": task.target_repo,
        "background": spec.background or "See source requirement document.",
        "goal": spec.goal,
        "agreed_scope": _as_bullets(
            spec.agreed_scope
            or ["No explicit agreed scope captured beyond the goal and acceptance criteria."]
        ),
        "non_goals": _as_bullets(spec.non_goals),
        "impacted_modules": _as_bullets(
            spec.impacted_modules or ["See repo-specific scope and module map for dispatch context."]
        ),
        "acceptance_criteria": _as_bullets(spec.acceptance_criteria),
        "risks": _as_bullets(spec.risks or ["No explicit risks captured yet."]),
        "references": _as_bullets(repo_references),
        "repo_summary": repo_summary,
        "repo_modules": _as_bullets(repo_modules),
        "repo_required_changes": _as_bullets(repo_required_changes),
        "repo_boundaries": _as_bullets(repo_boundaries),
        "repo_acceptance_criteria": _as_bullets(repo_acceptance),
        "repo_risks": _as_bullets(repo_risks),
        "source_ref": task.source_ref,
        "branch_name": task.branch_name,
        "worktree_path": task.worktree_path,
        "tmux_session": task.tmux_session,
        "worker_type": task.worker_type,
        "worker_model": task.worker_model,
        "issue_url": issue_url,
        "docs_repo": str(docs_repo),
    }
    issue_body_path.parent.mkdir(parents=True, exist_ok=True)
    issue_body_path.write_text(issue_template.substitute(context), encoding="utf-8")
    worker_prompt_path.write_text(worker_template.substitute(context), encoding="utf-8")


def _dispatch_issue(
    *,
    task: TaskRecord,
    config: dict[str, object],
    docs_repo: Path,
    dry_run: bool,
    blocked: list[str],
) -> int:
    repo_mappings = config.get("repo_mappings", {})
    if not isinstance(repo_mappings, dict):
        task.state = "blocked"
        task.last_error = "config.repo_mappings must be an object"
        task.updated_at = _timestamp()
        blocked.append(f"{task.task_id}: {task.last_error}")
        return 0

    mapping = repo_mappings.get(task.target_repo)
    if mapping is None:
        task.state = "blocked"
        task.last_error = f"missing repo mapping for target repo {task.target_repo}"
        task.updated_at = _timestamp()
        blocked.append(f"{task.task_id}: {task.last_error}")
        return 0
    if not isinstance(mapping, dict):
        task.state = "blocked"
        task.last_error = (
            f"repo mapping for target repo {task.target_repo} must be an object with "
            "github_repo, gitlab_repo, or repo+issue_provider"
        )
        task.updated_at = _timestamp()
        blocked.append(f"{task.task_id}: {task.last_error}")
        return 0

    try:
        provider, host, repo = _resolve_issue_destination(mapping)
    except ValueError as exc:
        task.state = "blocked"
        task.last_error = str(exc)
        task.updated_at = _timestamp()
        blocked.append(f"{task.task_id}: {task.last_error}")
        return 0

    labels = list(config.get("default_labels", []))
    labels.extend(str(label) for label in mapping.get("labels", []))
    labels = [label for label in labels if label]
    unique_labels = list(dict.fromkeys(labels))

    if dry_run:
        task.state = "dispatched"
        task.target_issue_url = _build_dry_run_issue_url(provider, host, repo, task.task_id)
        task.target_issue_number = f"DRY-RUN-{task.task_id}"
        task.updated_at = _timestamp()
        task.last_error = None
        return 1

    try:
        issue_url, issue_number = _create_issue(
            provider=provider,
            host=host,
            repo=repo,
            title=task.issue_title,
            body_path=Path(task.issue_body_path).resolve(),
            labels=unique_labels,
            docs_repo=docs_repo,
        )
    except RuntimeError as exc:
        task.state = "blocked"
        task.last_error = str(exc)
        task.updated_at = _timestamp()
        blocked.append(f"{task.task_id}: {task.last_error}")
        return 0

    task.target_issue_url = issue_url
    task.target_issue_number = issue_number
    task.state = "dispatched"
    task.last_error = None
    task.updated_at = _timestamp()
    return 1


def _orx_data_root(*, docs_repo: Path, config: dict[str, object]) -> Path:
    return (docs_repo / str(config["runtime_dir"]) / "orx").resolve()


def _build_orchestrator(*, docs_repo: Path, config: dict[str, object]) -> Orchestrator:
    return Orchestrator(data_root=_orx_data_root(docs_repo=docs_repo, config=config))


def _resolve_orx_workspace_strategy(config: dict[str, object]) -> str:
    launcher = _load_launcher_config(config)
    raw = str(launcher.get("workspace_strategy") or "git-worktree").strip()
    if raw in {"git-worktree", "git_worktree"}:
        return "git_worktree"
    if raw in {"in-place", "in_place"}:
        return "in_place"
    raise ValueError(f"unsupported launcher.workspace_strategy for orx: {raw}")


def _build_orx_request(
    *,
    task: TaskRecord,
    config: dict[str, object],
    docs_repo: Path,
) -> TaskRequest:
    normalized_model = normalize_worker_model(
        worker_type=task.worker_type,
        worker_model=task.worker_model,
    )
    if task.worker_model != normalized_model:
        task.worker_model = normalized_model
        task.updated_at = _timestamp()

    repo_mapping = _repo_mapping_for_task(task, config)
    repo_root = resolve_local_repo_path(
        target_repo=task.target_repo,
        mapping=repo_mapping,
        launcher=_load_launcher_config(config),
        docs_repo=docs_repo,
    )
    prompt_path = Path(task.worker_prompt_path).resolve()
    if not prompt_path.exists():
        raise FileNotFoundError(f"worker prompt does not exist: {prompt_path}")
    return TaskRequest(
        worker_type=task.worker_type,
        prompt=prompt_path.read_text(encoding="utf-8"),
        cwd=repo_root,
        model=resolve_explicit_worker_model(
            worker_type=task.worker_type,
            worker_model=task.worker_model,
        ),
        workspace_strategy=_resolve_orx_workspace_strategy(config),
        branch_name=task.branch_name,
        worktree_path=Path(task.worktree_path),
        metadata={
            "model": task.worker_model,
            "bridge_task_id": task.task_id,
            "target_repo": task.target_repo,
        },
    )


def _sync_task_runtime_metadata(task: TaskRecord, orx_task: object) -> None:
    task.orx_task_id = str(getattr(orx_task, "task_id"))
    session_name = getattr(orx_task, "session_name", None)
    if session_name:
        task.tmux_session = str(session_name)
    branch_name = getattr(orx_task, "branch_name", None)
    if branch_name:
        task.branch_name = str(branch_name)


def _resolve_orx_workspace_path(
    *,
    task: TaskRecord,
    orx_task: object,
    config: dict[str, object],
    docs_repo: Path,
) -> Path:
    workspace_path = getattr(orx_task, "workspace_path", None)
    if workspace_path:
        return Path(workspace_path)
    return _resolve_task_worktree_path(
        task=task,
        config=config,
        docs_repo=docs_repo,
        launcher=_load_launcher_config(config),
    )


def _worktree_has_local_changes(worktree_path: Path) -> bool:
    if not worktree_path.exists():
        return False
    result = subprocess.run(
        ["git", "-C", str(worktree_path), "status", "--short"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "git status failed")
    return bool(result.stdout.strip())


def _metadata_text(metadata: dict[str, object], key: str) -> str | None:
    value = metadata.get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _metadata_pr_url(metadata: dict[str, object]) -> str | None:
    for key in ("pr_url", "pull_request_url", "merge_request_url"):
        value = _metadata_text(metadata, key)
        if value:
            return value
    return None


def _orx_result_payload(
    *,
    task: TaskRecord,
    result: OrxTaskResult | None,
) -> dict[str, object] | None:
    if result is None:
        return None
    metadata = result.metadata if isinstance(result.metadata, dict) else {}
    commit_message = _metadata_text(metadata, "commit_message")
    commit_sha = _metadata_text(metadata, "commit_sha")
    pr_url = _metadata_pr_url(metadata)
    branch_name = _metadata_text(metadata, "branch")
    tests_run = metadata.get("tests_run")
    if tests_run is not None and not isinstance(tests_run, list):
        raise ValueError(
            f"invalid worker result for {task.task_id}: metadata.tests_run must be a JSON array when present"
        )
    notes = metadata.get("notes")
    if notes is not None and not isinstance(notes, list):
        raise ValueError(
            f"invalid worker result for {task.task_id}: metadata.notes must be a JSON array when present"
        )
    if result.outcome == "success" and not commit_message and not commit_sha and not pr_url:
        raise ValueError(
            f"invalid worker result for {task.task_id}: metadata.commit_message or metadata.commit_sha/metadata.pr_url must be non-empty"
        )
    return {
        "status": result.status,
        "outcome": result.outcome,
        "commit_message": commit_message,
        "commit_sha": commit_sha,
        "pr_url": pr_url,
        "branch_name": branch_name,
        "summary": str(result.summary or "").strip() or None,
        "tests_run": tests_run or [],
        "notes": notes or [],
        "error": result.error,
    }


def _orx_failure_message(
    *,
    task: TaskRecord,
    orx_task: object,
    result: OrxTaskResult | None,
) -> str:
    if result is not None:
        text = str(result.error or result.summary or "").strip()
        if text:
            return text
    failure_message = getattr(orx_task, "failure_message", None)
    if failure_message:
        return str(failure_message)
    failure_reason = getattr(orx_task, "failure_reason", None)
    if failure_reason:
        return str(failure_reason)
    return task.last_error or "worker task failed"


def _settle_task_without_orx_handle(
    *,
    task: TaskRecord,
    config: dict[str, object],
    docs_repo: Path,
    blocked: list[str],
    default_reason: str,
) -> bool:
    try:
        worktree_path = _resolve_task_worktree_path(
            task=task,
            config=config,
            docs_repo=docs_repo,
            launcher=_load_launcher_config(config),
        )
        has_local_changes = _worktree_has_local_changes(worktree_path)
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        blocked.append(f"{task.task_id}: {exc}")
        return _set_task_state(task, "blocked", str(exc))
    if has_local_changes:
        reason = "worker session exited after local code changes; inspect worktree before relaunch"
        return _set_task_state(task, "exited", reason)
    return _set_task_state(task, "restartable", default_reason)


def _apply_failed_orx_state(
    *,
    task: TaskRecord,
    orx_task: object,
    result: OrxTaskResult | None,
    config: dict[str, object],
    docs_repo: Path,
    blocked: list[str],
) -> bool:
    retryable = bool(getattr(orx_task, "retryable", False))
    message = _orx_failure_message(task=task, orx_task=orx_task, result=result)
    if not retryable:
        if _set_task_state(task, "blocked", message):
            blocked.append(f"{task.task_id}: {message}")
            return True
        return False

    try:
        workspace_path = _resolve_orx_workspace_path(
            task=task,
            orx_task=orx_task,
            config=config,
            docs_repo=docs_repo,
        )
        has_local_changes = _worktree_has_local_changes(workspace_path)
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        blocked.append(f"{task.task_id}: {exc}")
        return _set_task_state(task, "blocked", str(exc))

    if has_local_changes:
        reason = "worker session exited after local code changes; inspect worktree before relaunch"
        return _set_task_state(task, "exited", reason)
    return _set_task_state(task, "restartable", message)


def _commit_completed_orx_task(
    *,
    task: TaskRecord,
    orx_task: object,
    config: dict[str, object],
    docs_repo: Path,
    worker_result: dict[str, object],
) -> dict[str, object]:
    published_commit_sha = str(worker_result.get("commit_sha") or "").strip() or None
    published_pr_url = str(worker_result.get("pr_url") or "").strip() or None
    published_branch_name = str(worker_result.get("branch_name") or "").strip() or None
    if published_commit_sha or published_pr_url:
        updated = False
        if published_branch_name and task.branch_name != published_branch_name:
            task.branch_name = published_branch_name
            updated = True
        if published_commit_sha and task.commit_sha != published_commit_sha:
            task.commit_sha = published_commit_sha
            updated = True
        if published_pr_url and task.pr_url != published_pr_url:
            task.pr_url = published_pr_url
            updated = True
        commit_message = str(worker_result.get("commit_message") or "").strip() or None
        if commit_message and task.commit_message != commit_message:
            task.commit_message = commit_message
            updated = True
        summary = str(worker_result.get("summary") or "").strip() or None
        if summary and task.completion_summary != summary:
            task.completion_summary = summary
            updated = True
        if task.completed_at is None:
            task.completed_at = _timestamp()
            updated = True
        if task.state != "completed":
            task.state = "completed"
            updated = True
        if task.last_error is not None:
            task.last_error = None
            updated = True
        if updated:
            task.updated_at = _timestamp()
        return {"blocked": False, "message": "recorded published result", "completion_mode": "published"}

    if task.commit_sha or task.state == "completed":
        if task.state != "completed":
            task.state = "completed"
            task.completed_at = task.completed_at or _timestamp()
            task.updated_at = _timestamp()
        return {"blocked": False, "message": "already committed", "completion_mode": "already_committed"}

    try:
        worktree_path = _resolve_orx_workspace_path(
            task=task,
            orx_task=orx_task,
            config=config,
            docs_repo=docs_repo,
        )
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        return {"blocked": True, "message": str(exc)}

    if not worktree_path.exists():
        return {"blocked": True, "message": f"worktree does not exist for {task.task_id}: {worktree_path}"}

    try:
        has_local_changes = _worktree_has_local_changes(worktree_path)
    except RuntimeError as exc:
        return {"blocked": True, "message": str(exc)}

    if not has_local_changes:
        return {
            "blocked": True,
            "message": f"worker result for {task.task_id} declared completion but the worktree has no local changes",
        }

    add_result = subprocess.run(
        ["git", "-C", str(worktree_path), "add", "-A"],
        check=False,
        capture_output=True,
        text=True,
    )
    if add_result.returncode != 0:
        return {
            "blocked": True,
            "message": add_result.stderr.strip() or add_result.stdout.strip() or "git add failed",
        }

    commit_result = subprocess.run(
        ["git", "-C", str(worktree_path), "commit", "-m", str(worker_result["commit_message"])],
        check=False,
        capture_output=True,
        text=True,
    )
    if commit_result.returncode != 0:
        return {
            "blocked": True,
            "message": commit_result.stderr.strip() or commit_result.stdout.strip() or "git commit failed",
        }

    rev_result = subprocess.run(
        ["git", "-C", str(worktree_path), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    if rev_result.returncode != 0:
        return {
            "blocked": True,
            "message": rev_result.stderr.strip() or rev_result.stdout.strip() or "git rev-parse failed",
        }

    task.commit_sha = rev_result.stdout.strip()
    task.commit_message = str(worker_result["commit_message"])
    task.completion_summary = str(worker_result.get("summary") or "").strip() or None
    task.completed_at = _timestamp()
    task.state = "completed"
    task.last_error = None
    task.updated_at = _timestamp()
    return {"blocked": False, "message": "committed", "completion_mode": "committed"}


def _launch_tasks(
    *,
    tasks: list[TaskRecord],
    config: dict[str, object],
    docs_repo: Path,
    task_id: str | None,
    state: str | None,
    blocked: list[str],
) -> int:
    orchestrator = _build_orchestrator(docs_repo=docs_repo, config=config)
    launched = 0
    for task in tasks:
        if task_id and task.task_id != task_id:
            continue
        if not _matches_launch_state(task.state, state):
            continue
        launched += _launch_worker(
            task=task,
            config=config,
            docs_repo=docs_repo,
            blocked=blocked,
            orchestrator=orchestrator,
        )
    return launched


def _matches_launch_state(task_state: str, requested_state: str | None) -> bool:
    if requested_state:
        return task_state == requested_state
    return task_state in _LAUNCHABLE_STATES


def _matches_reconcile_state(task_state: str, requested_state: str | None) -> bool:
    if requested_state:
        return task_state == requested_state
    return task_state in _RECONCILE_ACTIVE_STATES


def _matches_watch_state(task_state: str, requested_state: str | None) -> bool:
    if requested_state:
        return task_state == requested_state
    return task_state in _WATCHABLE_STATES


def _reconcile_tasks(
    *,
    tasks: list[TaskRecord],
    config: dict[str, object],
    docs_repo: Path,
    task_id: str | None,
    state: str | None,
    blocked: list[str],
) -> int:
    orchestrator = _build_orchestrator(docs_repo=docs_repo, config=config)
    reconciled = 0
    for task in tasks:
        if task_id and task.task_id != task_id:
            continue
        if not _matches_reconcile_state(task.state, state):
            continue
        if _reconcile_orx_task(
            task=task,
            config=config,
            docs_repo=docs_repo,
            blocked=blocked,
            orchestrator=orchestrator,
        ):
            reconciled += 1
    return reconciled


def _watch_tasks(
    *,
    tasks: list[TaskRecord],
    config: dict[str, object],
    docs_repo: Path,
    task_id: str | None,
    state: str | None,
    restart: bool,
    registry_path: Path,
    events_path: Path,
) -> dict[str, object]:
    orchestrator = _build_orchestrator(docs_repo=docs_repo, config=config)
    blocked: list[str] = []
    changed_tasks: list[dict[str, object]] = []
    restarted_tasks: list[dict[str, object]] = []
    committed_tasks: list[dict[str, object]] = []
    blocked_tasks: list[dict[str, object]] = []
    events: list[dict[str, object]] = []

    for task in tasks:
        if task_id and task.task_id != task_id:
            continue
        if task.state == "completed":
            continue
        if not _matches_watch_state(task.state, state):
            continue
        before = _task_snapshot(task)
        task_events, restarted_task, committed_task = _watch_orx_task(
            task=task,
            config=config,
            docs_repo=docs_repo,
            blocked=blocked,
            restart=restart,
            orchestrator=orchestrator,
        )
        events.extend(task_events)
        after = _task_snapshot(task)
        if before != after:
            changed_tasks.append(
                {
                    "task_id": task.task_id,
                    "from_state": before["state"],
                    "to_state": after["state"],
                    "last_error": task.last_error,
                    "commit_sha": task.commit_sha,
                }
            )
        if restarted_task is not None:
            restarted_tasks.append(restarted_task)
        if committed_task is not None:
            committed_tasks.append(committed_task)
        if task.state == "blocked" and (before["state"] != "blocked" or before["last_error"] != task.last_error):
            blocked_tasks.append(
                {
                    "task_id": task.task_id,
                    "last_error": task.last_error,
                }
            )

    return {
        "changed": len(changed_tasks),
        "restarted": len(restarted_tasks),
        "committed": len(committed_tasks),
        "events_emitted": len(events),
        "registry": str(registry_path),
        "event_log": str(events_path),
        "blocked": blocked,
        "changed_tasks": changed_tasks,
        "restarted_tasks": restarted_tasks,
        "committed_tasks": committed_tasks,
        "blocked_tasks": blocked_tasks,
        "events": events,
    }


def _synchronize_in_progress_tasks(
    *,
    tasks: list[TaskRecord],
    config: dict[str, object],
    docs_repo: Path,
) -> None:
    orchestrator = _build_orchestrator(docs_repo=docs_repo, config=config)
    for task in tasks:
        if task.state != "in_progress":
            continue
        _reconcile_orx_task(
            task=task,
            config=config,
            docs_repo=docs_repo,
            blocked=[],
            orchestrator=orchestrator,
        )


def _reconcile_orx_task(
    *,
    task: TaskRecord,
    config: dict[str, object],
    docs_repo: Path,
    blocked: list[str],
    orchestrator: Orchestrator,
) -> bool:
    if _normalize_task_launch_targets(task):
        if task.orx_task_id:
            task.orx_task_id = None
        return _set_task_state(task, "restartable", "worker launch metadata migrated to ascii-safe paths")

    if not task.orx_task_id:
        return _settle_task_without_orx_handle(
            task=task,
            config=config,
            docs_repo=docs_repo,
            blocked=blocked,
            default_reason="task is missing orx runtime handle; relaunch required",
        )

    try:
        orx_task = orchestrator.get_task(task.orx_task_id)
    except KeyError:
        task.orx_task_id = None
        return _settle_task_without_orx_handle(
            task=task,
            config=config,
            docs_repo=docs_repo,
            blocked=blocked,
            default_reason="task is missing orx runtime handle; relaunch required",
        )
    _sync_task_runtime_metadata(task, orx_task)

    status = getattr(orx_task, "status")
    if status in {OrxTaskStatus.PENDING, OrxTaskStatus.STARTING, OrxTaskStatus.RUNNING}:
        return _set_task_state(task, "in_progress", None)
    if status == OrxTaskStatus.FAILED:
        result = orchestrator.get_result(task.orx_task_id)
        return _apply_failed_orx_state(
            task=task,
            orx_task=orx_task,
            result=result,
            config=config,
            docs_repo=docs_repo,
            blocked=blocked,
        )
    if status in {OrxTaskStatus.TIMEOUT, OrxTaskStatus.CANCELED}:
        message = _orx_failure_message(
            task=task,
            orx_task=orx_task,
            result=orchestrator.get_result(task.orx_task_id),
        )
        if _set_task_state(task, "blocked", message):
            blocked.append(f"{task.task_id}: {message}")
            return True
        return False
    return False


def _watch_orx_task(
    *,
    task: TaskRecord,
    config: dict[str, object],
    docs_repo: Path,
    blocked: list[str],
    restart: bool,
    orchestrator: Orchestrator,
) -> tuple[list[dict[str, object]], dict[str, object] | None, dict[str, object] | None]:
    events: list[dict[str, object]] = []
    restarted_task: dict[str, object] | None = None
    committed_task: dict[str, object] | None = None

    if _normalize_task_launch_targets(task):
        task.orx_task_id = None
        if _set_task_state(task, "restartable", "worker launch metadata migrated to ascii-safe paths"):
            events.append(
                _build_watch_event(
                    task,
                    event_type="restartable",
                    message="worker launch metadata migrated to ascii-safe paths",
                )
            )

    if not task.orx_task_id:
        if task.state in {"in_progress", "exited", "restartable"}:
            settled = _settle_task_without_orx_handle(
                task=task,
                config=config,
                docs_repo=docs_repo,
                blocked=blocked,
                default_reason="task is missing orx runtime handle; relaunch required",
            )
            if settled and task.state in {"restartable", "exited", "blocked"}:
                event_type = "blocked" if task.state == "blocked" else task.state
                events.append(_build_watch_event(task, event_type=event_type, message=str(task.last_error or task.state)))
            if task.state == "restartable" and restart:
                restarted_task = _restart_task_for_watch(
                    task=task,
                    config=config,
                    docs_repo=docs_repo,
                    blocked=blocked,
                    events=events,
                )
        return events, restarted_task, committed_task

    try:
        orx_task = orchestrator.get_task(task.orx_task_id)
    except KeyError:
        task.orx_task_id = None
        settled = _settle_task_without_orx_handle(
            task=task,
            config=config,
            docs_repo=docs_repo,
            blocked=blocked,
            default_reason="task is missing orx runtime handle; relaunch required",
        )
        if settled and task.state in {"restartable", "exited", "blocked"}:
            event_type = "blocked" if task.state == "blocked" else task.state
            events.append(_build_watch_event(task, event_type=event_type, message=str(task.last_error or task.state)))
        if task.state == "restartable" and restart:
            restarted_task = _restart_task_for_watch(
                task=task,
                config=config,
                docs_repo=docs_repo,
                blocked=blocked,
                events=events,
            )
        return events, restarted_task, committed_task
    _sync_task_runtime_metadata(task, orx_task)
    status = getattr(orx_task, "status")

    if status in {OrxTaskStatus.PENDING, OrxTaskStatus.STARTING, OrxTaskStatus.RUNNING}:
        _set_task_state(task, "in_progress", None)
        return events, restarted_task, committed_task

    result = orchestrator.get_result(task.orx_task_id)

    if status == OrxTaskStatus.SUCCEEDED:
        try:
            worker_result = _orx_result_payload(task=task, result=result)
        except ValueError as exc:
            if _set_task_state(task, "blocked", str(exc)):
                blocked.append(f"{task.task_id}: {exc}")
                events.append(_build_watch_event(task, event_type="blocked", message=str(exc)))
            return events, restarted_task, committed_task
        assert worker_result is not None
        commit_outcome = _commit_completed_orx_task(
            task=task,
            orx_task=orx_task,
            config=config,
            docs_repo=docs_repo,
            worker_result=worker_result,
        )
        if commit_outcome["blocked"]:
            message = str(commit_outcome["message"])
            if _set_task_state(task, "blocked", message):
                blocked.append(f"{task.task_id}: {message}")
                events.append(_build_watch_event(task, event_type="blocked", message=message))
            return events, restarted_task, committed_task
        committed_task = {
            "task_id": task.task_id,
            "commit_sha": task.commit_sha,
            "commit_message": task.commit_message,
            "pr_url": task.pr_url,
        }
        completion_mode = str(commit_outcome.get("completion_mode") or "committed")
        if completion_mode == "published":
            location = task.commit_sha or task.pr_url or task.task_id
            event_message = f"recorded worker-published completion at {location}"
        else:
            event_message = f"committed completed task at {task.commit_sha}"
        events.append(
            _build_watch_event(
                task,
                event_type="completed",
                message=event_message,
                extra={
                    "commit_sha": task.commit_sha,
                    "pr_url": task.pr_url,
                    "completion_summary": task.completion_summary,
                },
            )
        )
        return events, restarted_task, committed_task

    if status == OrxTaskStatus.FAILED:
        changed = _apply_failed_orx_state(
            task=task,
            orx_task=orx_task,
            result=result,
            config=config,
            docs_repo=docs_repo,
            blocked=blocked,
        )
        if changed and task.state in {"restartable", "exited", "blocked"}:
            event_type = "blocked" if task.state == "blocked" else task.state
            events.append(_build_watch_event(task, event_type=event_type, message=str(task.last_error or task.state)))
        if task.state == "restartable" and restart:
            restarted_task = _restart_task_for_watch(
                task=task,
                config=config,
                docs_repo=docs_repo,
                blocked=blocked,
                events=events,
            )
        return events, restarted_task, committed_task

    if status in {OrxTaskStatus.TIMEOUT, OrxTaskStatus.CANCELED}:
        message = _orx_failure_message(task=task, orx_task=orx_task, result=result)
        if _set_task_state(task, "blocked", message):
            blocked.append(f"{task.task_id}: {message}")
            events.append(_build_watch_event(task, event_type="blocked", message=message))
        return events, restarted_task, committed_task

    return events, restarted_task, committed_task


def _restart_task_for_watch(
    *,
    task: TaskRecord,
    config: dict[str, object],
    docs_repo: Path,
    blocked: list[str],
    events: list[dict[str, object]],
) -> dict[str, object] | None:
    before = _task_snapshot(task)
    task.orx_task_id = None
    launched = _launch_worker(task=task, config=config, docs_repo=docs_repo, blocked=blocked)
    if launched:
        events.append(
            _build_watch_event(
                task,
                event_type="restarted",
                message="worker relaunched after watchdog recovery",
            )
        )
        return {"task_id": task.task_id, "state": task.state}
    if task.state == "blocked" and (before["state"] != "blocked" or before["last_error"] != task.last_error):
        events.append(_build_watch_event(task, event_type="blocked", message=str(task.last_error or "restart failed")))
    return None


def _resolve_task_worktree_path(
    *,
    task: TaskRecord,
    config: dict[str, object],
    docs_repo: Path,
    launcher: dict[str, object],
) -> Path:
    mapping = _repo_mapping_for_task(task, config)
    repo_root = resolve_local_repo_path(
        target_repo=task.target_repo,
        mapping=mapping,
        launcher=launcher,
        docs_repo=docs_repo,
    )
    return (repo_root / task.worktree_path).resolve()


def _repo_mapping_for_task(task: TaskRecord, config: dict[str, object]) -> dict[str, object]:
    repo_mappings = config.get("repo_mappings", {})
    if not isinstance(repo_mappings, dict):
        raise ValueError("config.repo_mappings must be an object")
    mapping = repo_mappings.get(task.target_repo)
    if mapping is None or not isinstance(mapping, dict):
        raise ValueError(f"missing repo mapping for target repo {task.target_repo}")
    return mapping


def _task_snapshot(task: TaskRecord) -> dict[str, object]:
    return {
        "state": task.state,
        "last_error": task.last_error,
        "commit_sha": task.commit_sha,
        "completed_at": task.completed_at,
        "last_notified_at": task.last_notified_at,
    }


def _build_watch_event(
    task: TaskRecord,
    *,
    event_type: str,
    message: str,
    extra: dict[str, object] | None = None,
) -> dict[str, object]:
    timestamp = _timestamp()
    task.last_notified_at = timestamp
    task.updated_at = timestamp
    event: dict[str, object] = {
        "timestamp": timestamp,
        "task_id": task.task_id,
        "source_ref": task.source_ref,
        "source_title": task.source_title,
        "target_repo": task.target_repo,
        "target_issue_url": task.target_issue_url,
        "branch_name": task.branch_name,
        "event": event_type,
        "state": task.state,
        "message": message,
    }
    if extra:
        event.update(extra)
    return event


def _set_task_state(task: TaskRecord, state: str, reason: str | None) -> bool:
    changed = task.state != state or task.last_error != reason
    if not changed:
        return False
    task.state = state
    task.last_error = reason
    task.updated_at = _timestamp()
    if state == "in_progress" and task.launched_at is None:
        task.launched_at = _timestamp()
    return True


def _launch_worker(
    *,
    task: TaskRecord,
    config: dict[str, object],
    docs_repo: Path,
    blocked: list[str],
    orchestrator: Orchestrator | None = None,
) -> int:
    try:
        _normalize_task_launch_targets(task)
        active_orchestrator = orchestrator or _build_orchestrator(docs_repo=docs_repo, config=config)
        handle = active_orchestrator.start_task(
            _build_orx_request(
                task=task,
                config=config,
                docs_repo=docs_repo,
            )
        )
        orx_task = active_orchestrator.get_task(handle.task_id)
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        task.state = "blocked"
        task.last_error = str(exc)
        task.updated_at = _timestamp()
        blocked.append(f"{task.task_id}: {task.last_error}")
        return 0

    _sync_task_runtime_metadata(task, orx_task)
    task.launch_attempts += 1
    task.launched_at = _timestamp()
    task.updated_at = _timestamp()
    status = getattr(orx_task, "status")
    if status in {OrxTaskStatus.PENDING, OrxTaskStatus.STARTING, OrxTaskStatus.RUNNING}:
        task.state = "in_progress"
        task.last_error = None
        return 1

    result = active_orchestrator.get_result(handle.task_id)
    if status == OrxTaskStatus.FAILED:
        _apply_failed_orx_state(
            task=task,
            orx_task=orx_task,
            result=result,
            config=config,
            docs_repo=docs_repo,
            blocked=blocked,
        )
        return 1

    if status in {OrxTaskStatus.TIMEOUT, OrxTaskStatus.CANCELED}:
        message = _orx_failure_message(task=task, orx_task=orx_task, result=result)
        task.state = "blocked"
        task.last_error = message
        task.updated_at = _timestamp()
        blocked.append(f"{task.task_id}: {message}")
        return 1

    task.state = "in_progress"
    task.last_error = None
    task.updated_at = _timestamp()
    return 1


def _load_launcher_config(config: dict[str, object]) -> dict[str, object]:
    launcher = config.get("launcher", {})
    if launcher is None:
        return {}
    if not isinstance(launcher, dict):
        raise ValueError("config.launcher must be an object when present")
    return launcher


def _normalize_task_launch_targets(task: TaskRecord) -> bool:
    safe_slug = _slugify(task.source_title)
    desired_branch_name = f"openclaw/{task.task_id}/{safe_slug}"
    desired_worktree_path = f"../worktrees/{task.target_repo}/{task.task_id}-{safe_slug}"
    if task.branch_name == desired_branch_name and task.worktree_path == desired_worktree_path:
        return False

    old_branch_name = task.branch_name
    old_worktree_path = task.worktree_path
    task.branch_name = desired_branch_name
    task.worktree_path = desired_worktree_path
    task.updated_at = _timestamp()

    _rewrite_launch_reference(Path(task.issue_body_path), old_branch_name, desired_branch_name)
    _rewrite_launch_reference(Path(task.issue_body_path), old_worktree_path, desired_worktree_path)
    _rewrite_launch_reference(Path(task.worker_prompt_path), old_branch_name, desired_branch_name)
    _rewrite_launch_reference(Path(task.worker_prompt_path), old_worktree_path, desired_worktree_path)
    return True


def _rewrite_launch_reference(path: Path, old_value: str, new_value: str) -> None:
    if old_value == new_value or not path.exists():
        return
    content = path.read_text(encoding="utf-8")
    if old_value not in content:
        return
    path.write_text(content.replace(old_value, new_value), encoding="utf-8")


def _build_repo_summary(repo_scope) -> str:
    if repo_scope is None:
        return "No repo-specific summary was captured in the PRD. Use the required changes and module map below."

    parts = [part for part in [repo_scope.summary, _as_bullets(repo_scope.notes)] if part.strip()]
    if parts:
        return "\n\n".join(parts)
    return "No repo-specific summary was captured in the PRD. Use the required changes and module map below."


def _resolve_repo_modules(spec, repo_scope) -> list[str]:
    if repo_scope and repo_scope.modules:
        return repo_scope.modules
    if spec.impacted_modules:
        return spec.impacted_modules
    return ["No module-level scope captured yet."]


def _resolve_repo_required_changes(spec, repo_scope) -> list[str]:
    if repo_scope and repo_scope.required_changes:
        return repo_scope.required_changes
    if spec.agreed_scope:
        return spec.agreed_scope
    return spec.acceptance_criteria


def _resolve_repo_boundaries(spec, repo_scope) -> list[str]:
    combined = _merge_lists(
        repo_scope.boundaries if repo_scope else [],
        spec.non_goals,
    )
    if combined:
        return combined
    return ["No explicit boundaries captured yet."]


def _resolve_repo_acceptance(spec, repo_scope) -> list[str]:
    if repo_scope and repo_scope.acceptance_criteria:
        return repo_scope.acceptance_criteria
    return spec.acceptance_criteria


def _merge_lists(*groups: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            cleaned = item.strip()
            if not cleaned or cleaned in seen:
                continue
            seen.add(cleaned)
            merged.append(cleaned)
    return merged


def _resolve_issue_destination(mapping: dict[str, object]) -> tuple[str, str, str]:
    gitlab_repo = mapping.get("gitlab_repo")
    if gitlab_repo:
        host, repo = _parse_repo_locator(
            str(gitlab_repo),
            default_host=str(mapping.get("gitlab_host") or "gitlab.com"),
        )
        return "gitlab", host, repo

    github_repo = mapping.get("github_repo")
    if github_repo:
        preferred_gitlab_host = mapping.get("gitlab_host")
        host, repo = _parse_repo_locator(
            str(github_repo),
            default_host=str(
                preferred_gitlab_host or mapping.get("github_host") or "github.com"
            ),
        )
        if preferred_gitlab_host or _looks_like_gitlab_host(host):
            return "gitlab", str(preferred_gitlab_host or host), repo
        return "github", host, repo

    repo = mapping.get("repo")
    if repo:
        forge = str(mapping.get("issue_provider") or mapping.get("forge") or "").strip().lower()
        if forge not in {"github", "gitlab"}:
            raise ValueError(
                "repo mappings using 'repo' must also declare issue_provider as 'github' or 'gitlab'"
            )
        host_key = "gitlab_host" if forge == "gitlab" else "github_host"
        host, normalized_repo = _parse_repo_locator(
            str(repo),
            default_host=str(mapping.get(host_key) or mapping.get("host") or ""),
        )
        return forge, host, normalized_repo

    raise ValueError(
        "missing repository mapping; expected github_repo, gitlab_repo, or repo+issue_provider"
    )


def _create_issue(
    *,
    provider: str,
    host: str,
    repo: str,
    title: str,
    body_path: Path,
    labels: list[str],
    docs_repo: Path,
) -> tuple[str, str]:
    if provider == "github":
        return _create_github_issue(
            host=host,
            repo=repo,
            title=title,
            body_path=body_path,
            labels=labels,
            docs_repo=docs_repo,
        )
    if provider == "gitlab":
        return _create_gitlab_issue(
            host=host,
            repo=repo,
            title=title,
            body_path=body_path,
            labels=labels,
        )
    raise RuntimeError(f"unsupported issue provider: {provider}")


def _create_github_issue(
    *,
    host: str,
    repo: str,
    title: str,
    body_path: Path,
    labels: list[str],
    docs_repo: Path,
) -> tuple[str, str]:
    if not _command_exists("gh"):
        raise RuntimeError("gh CLI is required for GitHub --create-issues")

    repo_ref = repo if host == "github.com" else f"{host}/{repo}"
    command = [
        "gh",
        "issue",
        "create",
        "--repo",
        repo_ref,
        "--title",
        title,
        "--body-file",
        str(body_path),
    ]
    for label in labels:
        command.extend(["--label", label])

    result = subprocess.run(
        command,
        cwd=docs_repo,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "gh issue create failed")

    issue_url = result.stdout.strip().splitlines()[-1].strip()
    issue_number = issue_url.rstrip("/").split("/")[-1]
    return issue_url, issue_number


def _create_gitlab_issue(
    *,
    host: str,
    repo: str,
    title: str,
    body_path: Path,
    labels: list[str],
) -> tuple[str, str]:
    if not _command_exists("glab"):
        raise RuntimeError("glab CLI is required for GitLab --create-issues")

    repo_ref = _build_gitlab_repo_ref(host, repo)
    command = [
        "glab",
        "issue",
        "create",
        "--repo",
        repo_ref,
        "--title",
        title,
        "--description",
        body_path.read_text(encoding="utf-8"),
        "--yes",
    ]
    if labels:
        command.extend(["--label", ",".join(labels)])

    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "glab issue create failed")

    issue_url = result.stdout.strip().splitlines()[-1].strip()
    issue_number = issue_url.rstrip("/").split("/")[-1]
    if not issue_url or not issue_number:
        raise RuntimeError("glab issue create response is missing issue URL")
    return issue_url, issue_number


def _build_dry_run_issue_url(provider: str, host: str, repo: str, task_id: str) -> str:
    if provider == "gitlab":
        base_url, _ = _split_host_and_base_url(host)
        return f"{base_url}/{repo}/-/issues/DRY-RUN-{task_id}"
    return f"https://{host}/{repo}/issues/DRY-RUN-{task_id}"


def _build_gitlab_repo_ref(host: str, repo: str) -> str:
    if host == "gitlab.com":
        return repo
    base_url, _ = _split_host_and_base_url(host)
    return f"{base_url}/{repo}"


def _split_host_and_base_url(host: str) -> tuple[str, str]:
    text = host.strip().rstrip("/")
    parsed = urlparse(text)
    if parsed.scheme and parsed.netloc:
        return text, parsed.netloc
    return f"https://{text}", text


def _parse_repo_locator(value: str, *, default_host: str) -> tuple[str, str]:
    text = value.strip()
    if not text:
        raise ValueError("repository mapping value cannot be empty")

    parsed = urlparse(text)
    if parsed.scheme and parsed.netloc:
        host = parsed.netloc
        repo = parsed.path.lstrip("/")
    elif text.startswith("git@") and ":" in text:
        host, repo = text[4:].split(":", 1)
    elif _looks_like_host_prefixed_repo(text):
        host, repo = text.split("/", 1)
    else:
        host = default_host.strip()
        repo = text

    host = host.strip().rstrip("/")
    repo = repo.strip().strip("/")
    if repo.endswith(".git"):
        repo = repo[:-4]
    if not host:
        raise ValueError(f"repository mapping '{value}' is missing a host")
    if "/" not in repo:
        raise ValueError(f"repository mapping '{value}' is missing an owner/group and repo path")
    return host, repo


def _looks_like_host_prefixed_repo(value: str) -> bool:
    first_segment = value.split("/", 1)[0]
    return "." in first_segment or ":" in first_segment or first_segment == "localhost"


def _looks_like_gitlab_host(host: str) -> bool:
    lowered = host.lower()
    return "gitlab" in lowered


def _timestamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _slugify(value: str) -> str:
    ascii_value = value.encode("ascii", "ignore").decode("ascii")
    normalized = "".join(ch.lower() if ch.isalnum() else "-" for ch in ascii_value)
    collapsed = "-".join(part for part in normalized.split("-") if part)
    return (collapsed or "task")[:48]


def _as_bullets(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def _print_summary(
    created: int,
    dispatched: int,
    launched: int,
    skipped: list[str],
    blocked: list[str],
    registry_path: Path,
) -> None:
    print(f"created={created}")
    print(f"dispatched={dispatched}")
    print(f"launched={launched}")
    print(f"registry={registry_path}")
    if skipped:
        print("skipped:")
        for item in skipped:
            print(f"  - {item}")
    if blocked:
        print("blocked:")
        for item in blocked:
            print(f"  - {item}")


def _print_reconcile_summary(
    *,
    reconciled: int,
    restarted: int,
    blocked: list[str],
    registry_path: Path,
) -> None:
    print(f"reconciled={reconciled}")
    print(f"restarted={restarted}")
    print(f"registry={registry_path}")
    if blocked:
        print("blocked:")
        for item in blocked:
            print(f"  - {item}")


def _print_watch_summary(*, result: dict[str, object]) -> None:
    print(f"changed={result['changed']}")
    print(f"restarted={result['restarted']}")
    print(f"committed={result['committed']}")
    print(f"events={result['events_emitted']}")
    print(f"notifications={result['notifications_sent']}")
    print(f"registry={result['registry']}")
    print(f"event_log={result['event_log']}")
    blocked = result["blocked"]
    if blocked:
        print("blocked:")
        for item in blocked:
            print(f"  - {item}")
    notification_failures = result["notification_failures"]
    if notification_failures:
        print("notification_failures:")
        for item in notification_failures:
            print(f"  - {item}")


def _append_watchdog_events(path: Path, events: list[dict[str, object]]) -> None:
    if not events:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")


def _command_exists(name: str) -> bool:
    result = subprocess.run(
        ["sh", "-lc", f"command -v {name} >/dev/null 2>&1"],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


if __name__ == "__main__":
    main()
