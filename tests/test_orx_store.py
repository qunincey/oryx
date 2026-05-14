from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from orx.models import TaskRecord, TaskStatus
from orx.store import TaskStore


class TaskStoreTests(unittest.TestCase):
    def _create_store(self) -> tuple[TaskStore, Path]:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        root = Path(temp_dir.name)
        return TaskStore(root / "tasks.db"), root

    def _build_record(self, root: Path) -> TaskRecord:
        now = datetime.now(UTC)
        task_dir = root / "tasks" / "task-123"
        task_dir.mkdir(parents=True, exist_ok=True)
        return TaskRecord(
            task_id="task-123",
            worker_type="codex",
            status=TaskStatus.PENDING,
            cwd=root,
            workspace_strategy="in_place",
            workspace_path=root,
            branch_name=None,
            worktree_path=None,
            session_name="orx-task-123",
            prompt_path=task_dir / "prompt.txt",
            result_path=task_dir / "result.json",
            stdout_log_path=task_dir / "stdout.log",
            stderr_log_path=task_dir / "stderr.log",
            runtime_meta_path=task_dir / "runtime.json",
            timeout_seconds=60,
            exit_code=None,
            failure_reason=None,
            failure_message=None,
            retryable=True,
            metadata={"ticket": "ABC-123"},
            created_at=now,
            started_at=None,
            finished_at=None,
            updated_at=now,
            stdout_offset=0,
            stderr_offset=0,
        )

    def test_store_round_trips_task_record(self) -> None:
        store, root = self._create_store()
        record = self._build_record(root)

        store.create_task(record)
        saved = store.get_task(record.task_id)

        self.assertEqual(saved.task_id, record.task_id)
        self.assertEqual(saved.status, TaskStatus.PENDING)
        self.assertTrue(saved.retryable)
        self.assertEqual(saved.metadata["ticket"], "ABC-123")

    def test_store_appends_and_filters_output_chunks(self) -> None:
        store, root = self._create_store()
        record = self._build_record(root)
        store.create_task(record)

        first = store.append_output(record.task_id, "stdout", "hello\n")
        second = store.append_output(record.task_id, "stderr", "boom\n")

        self.assertEqual(first.seq, 1)
        self.assertEqual(second.seq, 2)
        self.assertEqual(len(store.list_output(record.task_id)), 2)
        self.assertEqual(len(store.list_output(record.task_id, after_seq=1)), 1)
        self.assertEqual(len(store.list_output(record.task_id, stream="stderr")), 1)


if __name__ == "__main__":
    unittest.main()
