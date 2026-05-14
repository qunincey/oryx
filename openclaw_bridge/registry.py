from __future__ import annotations

import json
from pathlib import Path

from .models import TaskRecord


def load_registry(path: Path) -> list[TaskRecord]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    items = payload.get("tasks", [])
    return [TaskRecord.from_dict(item) for item in items]


def save_registry(path: Path, tasks: list[TaskRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"tasks": [task.to_dict() for task in tasks]}
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def find_existing_task(
    tasks: list[TaskRecord], source_ref: str, target_repo: str
) -> TaskRecord | None:
    for task in tasks:
        if task.source_ref == source_ref and task.target_repo == target_repo:
            return task
    return None


def next_task_id(tasks: list[TaskRecord]) -> str:
    if not tasks:
        return "task-0001"
    highest = max(int(task.task_id.split("-")[-1]) for task in tasks)
    return f"task-{highest + 1:04d}"
