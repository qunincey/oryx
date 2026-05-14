from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

from .models import TaskFailureReason, TaskOutputChunk, TaskRecord, TaskStatus


class TaskStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                  task_id TEXT PRIMARY KEY,
                  worker_type TEXT NOT NULL,
                  status TEXT NOT NULL,
                  cwd TEXT NOT NULL,
                  workspace_strategy TEXT NOT NULL,
                  workspace_path TEXT NOT NULL,
                  branch_name TEXT,
                  worktree_path TEXT,
                  session_name TEXT NOT NULL,
                  prompt_path TEXT NOT NULL,
                  result_path TEXT NOT NULL,
                  stdout_log_path TEXT NOT NULL,
                  stderr_log_path TEXT NOT NULL,
                  runtime_meta_path TEXT NOT NULL,
                  timeout_seconds INTEGER,
                  exit_code INTEGER,
                  failure_reason TEXT,
                  failure_message TEXT,
                  retryable INTEGER NOT NULL DEFAULT 0,
                  metadata_json TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  started_at TEXT,
                  finished_at TEXT,
                  updated_at TEXT NOT NULL,
                  stdout_offset INTEGER NOT NULL DEFAULT 0,
                  stderr_offset INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS task_events (
                  event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                  task_id TEXT NOT NULL,
                  event_type TEXT NOT NULL,
                  payload_json TEXT NOT NULL,
                  created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS task_output_chunks (
                  task_id TEXT NOT NULL,
                  seq INTEGER NOT NULL,
                  stream TEXT NOT NULL,
                  content TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  PRIMARY KEY (task_id, seq)
                );
                """
            )
            columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(tasks)").fetchall()
            }
            if "retryable" not in columns:
                connection.execute(
                    "ALTER TABLE tasks ADD COLUMN retryable INTEGER NOT NULL DEFAULT 0"
                )
            connection.commit()

    def create_task(self, record: TaskRecord) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO tasks (
                  task_id, worker_type, status, cwd, workspace_strategy, workspace_path,
                  branch_name, worktree_path, session_name, prompt_path, result_path,
                  stdout_log_path, stderr_log_path, runtime_meta_path, timeout_seconds,
                  exit_code, failure_reason, failure_message, retryable, metadata_json, created_at,
                  started_at, finished_at, updated_at, stdout_offset, stderr_offset
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                self._record_values(record),
            )
            connection.commit()

    def get_task(self, task_id: str) -> TaskRecord:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"unknown task_id: {task_id}")
        return self._row_to_record(row)

    def update_task(self, task_id: str, **fields: object) -> TaskRecord:
        if not fields:
            return self.get_task(task_id)
        assignments: list[str] = []
        values: list[object] = []
        for key, value in fields.items():
            assignments.append(f"{key} = ?")
            values.append(self._serialize_field(key, value))
        values.append(task_id)
        with self._connect() as connection:
            connection.execute(
                f"UPDATE tasks SET {', '.join(assignments)} WHERE task_id = ?",
                values,
            )
            connection.commit()
        return self.get_task(task_id)

    def append_event(
        self,
        task_id: str,
        event_type: str,
        payload: dict[str, object] | None = None,
        *,
        created_at: datetime | None = None,
    ) -> None:
        timestamp = (created_at or datetime.now().astimezone()).isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO task_events (task_id, event_type, payload_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (task_id, event_type, json.dumps(payload or {}, ensure_ascii=False), timestamp),
            )
            connection.commit()

    def append_output(
        self,
        task_id: str,
        stream: str,
        content: str,
        *,
        created_at: datetime | None = None,
    ) -> TaskOutputChunk:
        timestamp = created_at or datetime.now().astimezone()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(seq), 0) AS max_seq FROM task_output_chunks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            seq = int(row["max_seq"]) + 1
            connection.execute(
                """
                INSERT INTO task_output_chunks (task_id, seq, stream, content, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (task_id, seq, stream, content, timestamp.isoformat()),
            )
            connection.commit()
        return TaskOutputChunk(
            task_id=task_id,
            seq=seq,
            stream=stream,
            content=content,
            created_at=timestamp,
        )

    def list_output(
        self,
        task_id: str,
        *,
        after_seq: int | None = None,
        stream: str | None = None,
    ) -> list[TaskOutputChunk]:
        clauses = ["task_id = ?"]
        values: list[object] = [task_id]
        if after_seq is not None:
            clauses.append("seq > ?")
            values.append(after_seq)
        if stream is not None:
            clauses.append("stream = ?")
            values.append(stream)
        query = (
            "SELECT task_id, seq, stream, content, created_at "
            "FROM task_output_chunks WHERE "
            + " AND ".join(clauses)
            + " ORDER BY seq ASC"
        )
        with self._connect() as connection:
            rows = connection.execute(query, values).fetchall()
        return [
            TaskOutputChunk(
                task_id=str(row["task_id"]),
                seq=int(row["seq"]),
                stream=str(row["stream"]),
                content=str(row["content"]),
                created_at=datetime.fromisoformat(str(row["created_at"])),
            )
            for row in rows
        ]

    def _record_values(self, record: TaskRecord) -> tuple[object, ...]:
        return (
            record.task_id,
            record.worker_type,
            record.status.value,
            str(record.cwd),
            record.workspace_strategy,
            str(record.workspace_path),
            record.branch_name,
            None if record.worktree_path is None else str(record.worktree_path),
            record.session_name,
            str(record.prompt_path),
            str(record.result_path),
            str(record.stdout_log_path),
            str(record.stderr_log_path),
            str(record.runtime_meta_path),
            record.timeout_seconds,
            record.exit_code,
            None if record.failure_reason is None else record.failure_reason.value,
            record.failure_message,
            int(record.retryable),
            json.dumps(record.metadata, ensure_ascii=False),
            record.created_at.isoformat(),
            None if record.started_at is None else record.started_at.isoformat(),
            None if record.finished_at is None else record.finished_at.isoformat(),
            record.updated_at.isoformat(),
            record.stdout_offset,
            record.stderr_offset,
        )

    def _serialize_field(self, key: str, value: object) -> object:
        if key in {"cwd", "workspace_path", "worktree_path", "prompt_path", "result_path", "stdout_log_path", "stderr_log_path", "runtime_meta_path"}:
            return None if value is None else str(value)
        if key in {"status"} and isinstance(value, TaskStatus):
            return value.value
        if key in {"failure_reason"} and isinstance(value, TaskFailureReason):
            return value.value
        if key == "retryable" and isinstance(value, bool):
            return int(value)
        if key in {"created_at", "started_at", "finished_at", "updated_at"} and isinstance(value, datetime):
            return value.isoformat()
        if key == "metadata":
            return json.dumps(value or {}, ensure_ascii=False)
        return value

    def _row_to_record(self, row: sqlite3.Row) -> TaskRecord:
        return TaskRecord(
            task_id=str(row["task_id"]),
            worker_type=str(row["worker_type"]),
            status=TaskStatus(str(row["status"])),
            cwd=Path(str(row["cwd"])),
            workspace_strategy=str(row["workspace_strategy"]),
            workspace_path=Path(str(row["workspace_path"])),
            branch_name=row["branch_name"] and str(row["branch_name"]),
            worktree_path=row["worktree_path"] and Path(str(row["worktree_path"])),
            session_name=str(row["session_name"]),
            prompt_path=Path(str(row["prompt_path"])),
            result_path=Path(str(row["result_path"])),
            stdout_log_path=Path(str(row["stdout_log_path"])),
            stderr_log_path=Path(str(row["stderr_log_path"])),
            runtime_meta_path=Path(str(row["runtime_meta_path"])),
            timeout_seconds=row["timeout_seconds"] if row["timeout_seconds"] is None else int(row["timeout_seconds"]),
            exit_code=row["exit_code"] if row["exit_code"] is None else int(row["exit_code"]),
            failure_reason=row["failure_reason"] and TaskFailureReason(str(row["failure_reason"])),
            failure_message=row["failure_message"] and str(row["failure_message"]),
            retryable=bool(int(row["retryable"])),
            metadata=json.loads(str(row["metadata_json"])),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            started_at=row["started_at"] and datetime.fromisoformat(str(row["started_at"])),
            finished_at=row["finished_at"] and datetime.fromisoformat(str(row["finished_at"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
            stdout_offset=int(row["stdout_offset"]),
            stderr_offset=int(row["stderr_offset"]),
        )
