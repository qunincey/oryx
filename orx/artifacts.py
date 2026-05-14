from __future__ import annotations

import json
from pathlib import Path

from .models import TaskArtifact, TaskFailureReason, TaskResult, TaskStatus


_DEFAULT_ERRORS = {
    TaskFailureReason.ARTIFACT_MISSING: "worker exited without completion artifact",
    TaskFailureReason.CANCELED: "task was canceled before completion",
    TaskFailureReason.TIMEOUT: "task timed out before completion",
    TaskFailureReason.LAUNCH_ERROR: "task failed during launch",
    TaskFailureReason.RUNTIME_ERROR: "task failed due to orchestration runtime error",
}


def result_contract_text(result_path: Path) -> str:
    return (
        "Runtime completion contract:\n"
        f"- When you finish, write a UTF-8 JSON file to {result_path}.\n"
        "- The JSON must include status, outcome, summary, artifacts, metadata, and error.\n"
        '- Set "status" to "completed".\n'
        '- Set "outcome" to one of "success", "failure", or "needs_human".\n'
        "- Artifact paths must be relative to the task directory.\n"
        "Use this shape:\n"
        "{\n"
        '  "status": "completed",\n'
        '  "outcome": "success",\n'
        '  "summary": "Implemented the requested change and added tests.",\n'
        '  "artifacts": [{"name": "summary", "path": "summary.md"}],\n'
        '  "metadata": {"worker_type": "codex"},\n'
        '  "error": null\n'
        "}\n"
        "If you cannot complete the task, still write result.json with outcome failure or needs_human."
    )


def load_task_result(result_path: Path, *, task_id: str) -> TaskResult:
    raw = json.loads(result_path.read_text(encoding="utf-8"))
    status = str(raw.get("status") or "")
    outcome = str(raw.get("outcome") or "")
    summary = str(raw.get("summary") or "")
    if status != "completed":
        raise ValueError(f"invalid result status in {result_path}: {status!r}")
    if outcome not in {"success", "failure", "needs_human"}:
        raise ValueError(f"invalid result outcome in {result_path}: {outcome!r}")
    artifacts = _normalize_artifacts(raw.get("artifacts") or [])
    metadata = raw.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise ValueError(f"invalid result metadata in {result_path}: expected object")
    error = raw.get("error")
    return TaskResult(
        task_id=task_id,
        status=status,
        outcome=outcome,
        summary=summary,
        artifacts=artifacts,
        error=None if error is None else str(error),
        metadata=dict(metadata),
    )


def write_task_result(result_path: Path, result: TaskResult) -> None:
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def synthesize_failure_result(
    *,
    task_id: str,
    reason: TaskFailureReason,
    summary: str | None = None,
    error: str | None = None,
    metadata: dict[str, object] | None = None,
    artifacts: list[TaskArtifact] | None = None,
) -> TaskResult:
    resolved_error = error or _DEFAULT_ERRORS[reason]
    return TaskResult(
        task_id=task_id,
        status="completed",
        outcome="failure",
        summary=summary or resolved_error,
        artifacts=list(artifacts or []),
        error=resolved_error,
        metadata=dict(metadata or {}),
    )


def task_status_from_result(result: TaskResult) -> TaskStatus:
    if result.outcome == "success":
        return TaskStatus.SUCCEEDED
    return TaskStatus.FAILED


def _normalize_artifacts(raw_artifacts: object) -> list[TaskArtifact]:
    if not isinstance(raw_artifacts, list):
        raise ValueError("result artifacts must be a list")
    normalized: list[TaskArtifact] = []
    for index, raw in enumerate(raw_artifacts):
        if isinstance(raw, str):
            path = raw
            name = Path(path).name or f"artifact-{index + 1}"
            normalized.append(TaskArtifact(name=name, path=path))
            continue
        if not isinstance(raw, dict):
            raise ValueError("result artifact entries must be strings or objects")
        name = str(raw.get("name") or Path(str(raw.get("path") or "")).name or f"artifact-{index + 1}")
        path = str(raw.get("path") or "")
        if not path:
            raise ValueError("result artifact object is missing path")
        normalized.append(TaskArtifact(name=name, path=path))
    return normalized
