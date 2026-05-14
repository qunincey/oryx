from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path


class TaskStatus(str, Enum):
    PENDING = "PENDING"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELED = "CANCELED"
    TIMEOUT = "TIMEOUT"


class TaskFailureReason(str, Enum):
    LAUNCH_ERROR = "launch_error"
    RUNTIME_ERROR = "runtime_error"
    ARTIFACT_MISSING = "artifact_missing"
    TIMEOUT = "timeout"
    CANCELED = "canceled"


TERMINAL_STATUSES = {
    TaskStatus.SUCCEEDED,
    TaskStatus.FAILED,
    TaskStatus.CANCELED,
    TaskStatus.TIMEOUT,
}


@dataclass(slots=True)
class TaskHandle:
    task_id: str


@dataclass(slots=True)
class TaskArtifact:
    name: str
    path: str

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "path": self.path}


@dataclass(slots=True)
class TaskRequest:
    worker_type: str
    prompt: str
    cwd: Path
    model: str | None = None
    workspace_strategy: str = "in_place"
    branch_name: str | None = None
    worktree_path: Path | None = None
    timeout_seconds: int | None = None
    metadata: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.cwd = Path(self.cwd)
        if self.worktree_path is not None:
            self.worktree_path = Path(self.worktree_path)


@dataclass(slots=True)
class TaskOutputChunk:
    task_id: str
    seq: int
    stream: str
    content: str
    created_at: datetime


@dataclass(slots=True)
class TaskResult:
    task_id: str
    status: str
    outcome: str
    summary: str
    artifacts: list[TaskArtifact]
    error: str | None
    metadata: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "outcome": self.outcome,
            "summary": self.summary,
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
            "metadata": self.metadata,
            "error": self.error,
        }


@dataclass(slots=True)
class TaskRecord:
    task_id: str
    worker_type: str
    status: TaskStatus
    cwd: Path
    workspace_strategy: str
    workspace_path: Path
    branch_name: str | None
    worktree_path: Path | None
    session_name: str
    prompt_path: Path
    result_path: Path
    stdout_log_path: Path
    stderr_log_path: Path
    runtime_meta_path: Path
    timeout_seconds: int | None
    exit_code: int | None
    failure_reason: TaskFailureReason | None
    failure_message: str | None
    retryable: bool
    metadata: dict[str, object]
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    updated_at: datetime
    stdout_offset: int = 0
    stderr_offset: int = 0

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES
