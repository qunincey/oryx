from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from openclaw_bridge.models import TaskRecord
from openclaw_bridge.registry import load_registry

from .config import MonitorConfig, MonitorProject

_TASK_SEVERITY = {
    "blocked": 0,
    "restartable": 1,
    "exited": 2,
    "in_progress": 3,
    "dispatched": 4,
    "decomposed": 5,
    "completed": 6,
}

_EMPTY_OUTPUT_SNAPSHOT = {
    "stdout": "",
    "stderr": "",
    "stdout_seq": 0,
    "stderr_seq": 0,
    "last_seq": 0,
}


def build_dashboard_payload(config: MonitorConfig) -> dict[str, object]:
    projects_payload: list[dict[str, object]] = []
    all_events: list[dict[str, object]] = []

    for project in config.projects:
        project_payload = build_project_payload(project)
        projects_payload.append(project_payload)
        all_events.extend(project_payload.pop("_events", []))

    summary = {
        "running_count": sum(_count_tasks(projects_payload, "in_progress")),
        "blocked_count": sum(_count_tasks(projects_payload, "blocked")),
        "restartable_count": sum(_count_tasks(projects_payload, "restartable")),
        "completed_today_count": sum(_count_completed_today(projects_payload)),
        "degraded_project_count": sum(1 for item in projects_payload if item["health"] == "degraded"),
        "disconnected_project_count": sum(1 for item in projects_payload if item["health"] == "disconnected"),
    }

    recent_events = sorted(all_events, key=_event_timestamp_key, reverse=True)[:10]
    return {
        "generated_at": _timestamp(),
        "poll_interval_seconds": config.poll_interval_seconds,
        "summary": summary,
        "projects": projects_payload,
        "recent_events": recent_events,
    }


def build_task_detail_payload(
    config: MonitorConfig,
    *,
    project_id: str,
    task_id: str,
) -> dict[str, object]:
    project = _find_project(config, project_id)
    loaded = build_project_payload(project)
    for task in loaded["tasks"]:
        if task["task_id"] == task_id:
            output = read_task_output_snapshot(
                project.runtime_root,
                task_id=str(task.get("orx_task_id") or ""),
            )
            return {
                "project": {
                    "id": loaded["id"],
                    "name": loaded["name"],
                    "runtime_root": loaded["runtime_root"],
                    "docs_repo": loaded["docs_repo"],
                    "health": loaded["health"],
                    "error": loaded["error"],
                },
                "task": task,
                "runtime": task["runtime"],
                "output": output,
                "timeline": [event for event in loaded.pop("_events", []) if event["task_id"] == task_id],
            }
    raise KeyError(f"unknown task_id {task_id} in project {project_id}")


def build_project_payload(
    project: MonitorProject,
    *,
    include_events: bool = True,
) -> dict[str, object]:
    return _load_project_payload(project, include_events=include_events)


def read_task_output_snapshot(runtime_root: Path, task_id: str) -> dict[str, object]:
    normalized_task_id = str(task_id or "").strip()
    if not normalized_task_id:
        return dict(_EMPTY_OUTPUT_SNAPSHOT)
    runtime_db_path = runtime_root / "tasks.db"
    if not runtime_db_path.exists():
        return dict(_EMPTY_OUTPUT_SNAPSHOT)
    try:
        return _read_task_output_snapshot(runtime_db_path, task_id=normalized_task_id)
    except (sqlite3.DatabaseError, OSError, ValueError):
        return dict(_EMPTY_OUTPUT_SNAPSHOT)


def _find_project(config: MonitorConfig, project_id: str) -> MonitorProject:
    for project in config.projects:
        if project.id == project_id:
            return project
    raise KeyError(f"unknown project_id: {project_id}")


def runtime_state_from_status(status: str, *, retryable: bool) -> str:
    normalized = str(status or "")
    if normalized in {"PENDING", "STARTING", "RUNNING"}:
        return "in_progress"
    if normalized == "SUCCEEDED":
        return "completed"
    if normalized == "FAILED":
        return "restartable" if retryable else "blocked"
    if normalized == "TIMEOUT":
        return "restartable"
    if normalized == "CANCELED":
        return "exited"
    return "blocked"


def _load_project_payload(
    project: MonitorProject,
    *,
    include_events: bool,
) -> dict[str, object]:
    runtime_root = project.runtime_root
    openclaw_state_root = project.openclaw_state_root
    registry_path = openclaw_state_root / "registry.json"
    events_path = openclaw_state_root / "watchdog-events.jsonl"
    runtime_db_path = runtime_root / "tasks.db"

    health = "healthy"
    error: str | None = None

    try:
        registry_tasks = load_registry(registry_path)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        registry_tasks = []
        health = "degraded"
        error = f"failed to load registry: {exc}"

    events: list[dict[str, object]] = []
    if include_events:
        try:
            events = _read_events(events_path, project=project)
        except (json.JSONDecodeError, OSError, ValueError) as exc:
            health = _merge_health(health, "degraded")
            error = error or f"failed to load watchdog events: {exc}"

    runtime_rows: dict[str, dict[str, Any]] = {}
    if not runtime_root.exists():
        health = _merge_health(health, "disconnected")
        error = error or f"missing runtime root at {runtime_root}"
    elif not runtime_root.is_dir():
        health = _merge_health(health, "disconnected")
        error = error or f"runtime_root is not a directory: {runtime_root}"
    elif runtime_db_path.exists():
        try:
            runtime_rows = _read_runtime_rows(runtime_db_path)
        except (sqlite3.DatabaseError, OSError, ValueError) as exc:
            health = _merge_health(health, "degraded")
            error = error or f"failed to load runtime db: {exc}"

    task_payloads = _merge_task_payloads(registry_tasks, runtime_rows)
    task_payloads.sort(key=_task_sort_key)

    return {
        "id": project.id,
        "name": project.name,
        "runtime_root": str(runtime_root.resolve()),
        "docs_repo": None if project.docs_repo is None else str(project.docs_repo),
        "health": health,
        "error": error,
        "counts": {
            "total": len(task_payloads),
            "in_progress": sum(1 for item in task_payloads if item["state"] == "in_progress"),
            "blocked": sum(1 for item in task_payloads if item["state"] == "blocked"),
            "restartable": sum(1 for item in task_payloads if item["state"] == "restartable"),
            "completed": sum(1 for item in task_payloads if item["state"] == "completed"),
        },
        "tasks": task_payloads,
        "_events": events,
    }


def _merge_task_payloads(
    registry_tasks: list[TaskRecord],
    runtime_rows: dict[str, dict[str, Any]],
) -> list[dict[str, object]]:
    payloads: list[dict[str, object]] = []
    matched_runtime_ids: set[str] = set()

    registry_by_orx_task_id = {
        task.orx_task_id: task for task in registry_tasks if task.orx_task_id
    }

    for runtime_task_id, runtime_row in runtime_rows.items():
        registry_task = registry_by_orx_task_id.get(runtime_task_id)
        if registry_task is not None:
            payloads.append(_serialize_hybrid_task(registry_task, runtime_row))
            matched_runtime_ids.add(runtime_task_id)
        else:
            payloads.append(_serialize_runtime_task(runtime_row))

    for registry_task in registry_tasks:
        if registry_task.orx_task_id and registry_task.orx_task_id in matched_runtime_ids:
            continue
        payloads.append(_serialize_openclaw_task(registry_task))

    return payloads


def _serialize_openclaw_task(task: TaskRecord) -> dict[str, object]:
    return {
        **_serialize_openclaw_fields(task),
        "task_source": "openclaw",
        "runtime": None,
    }


def _serialize_hybrid_task(task: TaskRecord, runtime: dict[str, Any]) -> dict[str, object]:
    model = _runtime_model(runtime) or task.worker_model
    worktree_path = task.worktree_path or runtime.get("worktree_path") or runtime.get("workspace_path") or ""
    return {
        **_serialize_openclaw_fields(task),
        "state": _state_from_runtime(runtime),
        "worker_type": str(runtime.get("worker_type") or task.worker_type),
        "worker_model": model,
        "worktree_path": str(worktree_path),
        "tmux_session": str(task.tmux_session or runtime.get("session_name") or ""),
        "branch_name": str(task.branch_name or runtime.get("branch_name") or ""),
        "last_error": runtime.get("failure_message") or task.last_error,
        "updated_at": str(runtime.get("updated_at") or task.updated_at),
        "orx_task_id": task.orx_task_id or str(runtime.get("task_id") or ""),
        "launched_at": runtime.get("started_at") or task.launched_at,
        "completed_at": runtime.get("finished_at") or task.completed_at,
        "task_source": "hybrid",
        "runtime": runtime,
    }


def _serialize_runtime_task(runtime: dict[str, Any]) -> dict[str, object]:
    metadata = runtime.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    task_id = str(runtime["task_id"])
    created_at = str(runtime.get("created_at") or runtime.get("updated_at") or _timestamp())
    updated_at = str(runtime.get("updated_at") or created_at)
    source_title = str(metadata.get("source_title") or task_id)
    target_repo = str(metadata.get("target_repo") or "runtime")
    return {
        "task_id": task_id,
        "source_repo": str(metadata.get("source_repo") or ""),
        "source_ref": str(metadata.get("source_ref") or ""),
        "source_title": source_title,
        "target_repo": target_repo,
        "target_issue_number": None,
        "target_issue_url": None,
        "state": _state_from_runtime(runtime),
        "worker_type": str(runtime.get("worker_type") or ""),
        "worker_model": _runtime_model(runtime),
        "worktree_path": str(runtime.get("worktree_path") or runtime.get("workspace_path") or ""),
        "tmux_session": str(runtime.get("session_name") or ""),
        "branch_name": str(runtime.get("branch_name") or ""),
        "pr_url": None,
        "last_error": runtime.get("failure_message"),
        "created_at": created_at,
        "updated_at": updated_at,
        "issue_title": source_title,
        "issue_body_path": "",
        "worker_prompt_path": "",
        "orx_task_id": task_id,
        "launched_at": runtime.get("started_at") or created_at,
        "launch_attempts": 0,
        "completed_at": runtime.get("finished_at"),
        "commit_sha": None,
        "commit_message": None,
        "last_notified_at": None,
        "completion_summary": None,
        "task_source": "runtime",
        "runtime": runtime,
    }


def _serialize_openclaw_fields(task: TaskRecord) -> dict[str, object]:
    return {
        "task_id": task.task_id,
        "source_repo": task.source_repo,
        "source_ref": task.source_ref,
        "source_title": task.source_title,
        "target_repo": task.target_repo,
        "target_issue_number": task.target_issue_number,
        "target_issue_url": task.target_issue_url,
        "state": task.state,
        "worker_type": task.worker_type,
        "worker_model": task.worker_model,
        "worktree_path": task.worktree_path,
        "tmux_session": task.tmux_session,
        "branch_name": task.branch_name,
        "pr_url": task.pr_url,
        "last_error": task.last_error,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "issue_title": task.issue_title,
        "issue_body_path": task.issue_body_path,
        "worker_prompt_path": task.worker_prompt_path,
        "orx_task_id": task.orx_task_id,
        "launched_at": task.launched_at,
        "launch_attempts": task.launch_attempts,
        "completed_at": task.completed_at,
        "commit_sha": task.commit_sha,
        "commit_message": task.commit_message,
        "last_notified_at": task.last_notified_at,
        "completion_summary": task.completion_summary,
    }


def _state_from_runtime(runtime: dict[str, Any]) -> str:
    return runtime_state_from_status(
        str(runtime.get("status") or ""),
        retryable=bool(runtime.get("retryable")),
    )


def _runtime_model(runtime: dict[str, Any]) -> str:
    metadata = runtime.get("metadata")
    if isinstance(metadata, dict):
        model = metadata.get("model")
        if model is not None:
            return str(model)
    return ""


def _read_events(path: Path, *, project: MonitorProject) -> list[dict[str, object]]:
    if not path.exists():
        return []
    events: list[dict[str, object]] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip():
            continue
        payload = json.loads(raw_line)
        if not isinstance(payload, dict):
            raise ValueError("watchdog event line is not an object")
        item = dict(payload)
        item["project_id"] = project.id
        item["project_name"] = project.name
        events.append(item)
    events.sort(key=_event_timestamp_key, reverse=True)
    return events


def _read_runtime_rows(path: Path) -> dict[str, dict[str, Any]]:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT
              task_id,
              status,
              worker_type,
              workspace_path,
              branch_name,
              worktree_path,
              session_name,
              started_at,
              finished_at,
              updated_at,
              created_at,
              timeout_seconds,
              exit_code,
              failure_reason,
              failure_message,
              retryable,
              metadata_json
            FROM tasks
            """
        ).fetchall()
    finally:
        connection.close()

    payload: dict[str, dict[str, Any]] = {}
    for row in rows:
        metadata = json.loads(str(row["metadata_json"])) if row["metadata_json"] else {}
        payload[str(row["task_id"])] = {
            "task_id": str(row["task_id"]),
            "status": str(row["status"]),
            "worker_type": str(row["worker_type"]),
            "workspace_path": str(row["workspace_path"]),
            "branch_name": row["branch_name"] and str(row["branch_name"]),
            "worktree_path": row["worktree_path"] and str(row["worktree_path"]),
            "session_name": str(row["session_name"]),
            "started_at": row["started_at"] and str(row["started_at"]),
            "finished_at": row["finished_at"] and str(row["finished_at"]),
            "updated_at": str(row["updated_at"]),
            "created_at": row["created_at"] and str(row["created_at"]),
            "timeout_seconds": row["timeout_seconds"],
            "exit_code": row["exit_code"],
            "failure_reason": row["failure_reason"] and str(row["failure_reason"]),
            "failure_message": row["failure_message"] and str(row["failure_message"]),
            "retryable": bool(int(row["retryable"])),
            "metadata": metadata,
        }
    return payload


def _read_task_output_snapshot(path: Path, *, task_id: str) -> dict[str, object]:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT seq, stream, content
            FROM task_output_chunks
            WHERE task_id = ?
            ORDER BY seq ASC
            """,
            (task_id,),
        ).fetchall()
    finally:
        connection.close()

    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    stdout_seq = 0
    stderr_seq = 0
    last_seq = 0
    for row in rows:
        seq = int(row["seq"])
        stream = str(row["stream"])
        content = str(row["content"])
        last_seq = seq
        if stream == "stdout":
            stdout_parts.append(content)
            stdout_seq = seq
            continue
        if stream == "stderr":
            stderr_parts.append(content)
            stderr_seq = seq

    return {
        "stdout": "".join(stdout_parts),
        "stderr": "".join(stderr_parts),
        "stdout_seq": stdout_seq,
        "stderr_seq": stderr_seq,
        "last_seq": last_seq,
    }


def _task_sort_key(task: dict[str, object]) -> tuple[int, float]:
    severity = _TASK_SEVERITY.get(str(task["state"]), 99)
    return (severity, -_timestamp_to_epoch(str(task["updated_at"])))


def _event_timestamp_key(event: dict[str, object]) -> float:
    return _timestamp_to_epoch(str(event.get("timestamp") or ""))


def _timestamp_to_epoch(value: str) -> float:
    if not value:
        return 0.0
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return 0.0


def _count_tasks(projects: list[dict[str, object]], state: str) -> list[int]:
    return [sum(1 for task in project["tasks"] if task["state"] == state) for project in projects]


def _count_completed_today(projects: list[dict[str, object]]) -> list[int]:
    today = datetime.now(UTC).date()
    counts: list[int] = []
    for project in projects:
        count = 0
        for task in project["tasks"]:
            completed_at = task.get("completed_at")
            if not completed_at:
                continue
            try:
                if datetime.fromisoformat(str(completed_at)).astimezone(UTC).date() == today:
                    count += 1
            except ValueError:
                continue
        counts.append(count)
    return counts


def _timestamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _merge_health(current: str, new_state: str) -> str:
    order = {"healthy": 0, "disconnected": 1, "degraded": 2}
    return new_state if order[new_state] > order[current] else current
