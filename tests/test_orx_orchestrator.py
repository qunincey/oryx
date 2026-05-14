from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from orx.models import TaskFailureReason, TaskRequest, TaskStatus
from orx.orchestrator import Orchestrator


class FakeClock:
    def __init__(self, current: datetime) -> None:
        self.current = current

    def now(self) -> datetime:
        return self.current

    def advance(self, seconds: float) -> None:
        self.current += timedelta(seconds=seconds)


class FakeSessionLauncher:
    def __init__(self, *, fail_start: bool = False) -> None:
        self.fail_start = fail_start
        self.sessions: dict[str, bool] = {}
        self.commands: dict[str, str] = {}
        self.interrupted: list[str] = []
        self.killed: list[str] = []

    def session_exists(self, session_name: str) -> bool:
        return self.sessions.get(session_name, False)

    def start(self, session_name: str, cwd: Path, command: str) -> None:
        if self.fail_start:
            raise RuntimeError("tmux new-session failed")
        self.sessions[session_name] = True
        self.commands[session_name] = command

    def send_interrupt(self, session_name: str) -> None:
        self.interrupted.append(session_name)

    def kill_session(self, session_name: str) -> None:
        self.killed.append(session_name)
        self.sessions[session_name] = False


class OrchestratorTests(unittest.TestCase):
    def _create_orchestrator(
        self,
        *,
        fail_start: bool = False,
        clock: FakeClock | None = None,
    ) -> tuple[Orchestrator, FakeSessionLauncher, Path]:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        root = Path(temp_dir.name)
        launcher = FakeSessionLauncher(fail_start=fail_start)
        fake_clock = clock or FakeClock(datetime(2026, 4, 24, tzinfo=UTC))
        orchestrator = Orchestrator(
            data_root=root / ".orx",
            session_launcher=launcher,
            clock=fake_clock.now,
            poll_interval=0.01,
            stop_grace_seconds=0.0,
        )
        return orchestrator, launcher, root

    def _request(self, root: Path, **overrides: object) -> TaskRequest:
        root.mkdir(parents=True, exist_ok=True)
        request = TaskRequest(
            worker_type="codex",
            prompt="Implement the change",
            cwd=root,
            model="gpt-5.4",
            timeout_seconds=30,
        )
        return replace(request, **overrides)

    def _task_dir(self, orchestrator: Orchestrator, task_id: str) -> Path:
        return orchestrator.data_root / "tasks" / task_id

    def _write_result(self, task_dir: Path, *, outcome: str = "success", summary: str = "done") -> None:
        (task_dir / "result.json").write_text(
            json.dumps(
                {
                    "status": "completed",
                    "outcome": outcome,
                    "summary": summary,
                    "artifacts": [{"name": "summary", "path": "summary.md"}],
                    "metadata": {"worker_type": "codex"},
                    "error": None if outcome == "success" else "failed",
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def _write_runtime_meta(self, task_dir: Path, *, exit_code: int) -> None:
        (task_dir / "runtime.json").write_text(
            json.dumps({"exit_code": exit_code}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def test_start_task_persists_running_task_and_output(self) -> None:
        orchestrator, launcher, root = self._create_orchestrator()

        handle = orchestrator.start_task(self._request(root))
        task = orchestrator.get_task(handle.task_id)
        task_dir = self._task_dir(orchestrator, handle.task_id)
        (task_dir / "stdout.log").write_text("hello\n", encoding="utf-8")

        chunks = orchestrator.list_output(handle.task_id)

        self.assertEqual(task.status, TaskStatus.RUNNING)
        self.assertTrue(launcher.session_exists(task.session_name))
        self.assertEqual([chunk.content for chunk in chunks], ["hello\n"])

    def test_wait_task_returns_result_after_completion_artifact(self) -> None:
        orchestrator, launcher, root = self._create_orchestrator()

        handle = orchestrator.start_task(self._request(root))
        task = orchestrator.get_task(handle.task_id)
        task_dir = self._task_dir(orchestrator, handle.task_id)
        self._write_result(task_dir, outcome="success", summary="implemented")
        launcher.sessions[task.session_name] = False

        result = orchestrator.wait_task(handle.task_id, timeout=1)
        refreshed = orchestrator.get_task(handle.task_id)

        self.assertEqual(result.outcome, "success")
        self.assertEqual(result.summary, "implemented")
        self.assertEqual(refreshed.status, TaskStatus.SUCCEEDED)

    def test_artifact_missing_writes_failure_result_when_session_exits(self) -> None:
        orchestrator, launcher, root = self._create_orchestrator()

        handle = orchestrator.start_task(self._request(root))
        task = orchestrator.get_task(handle.task_id)
        task_dir = self._task_dir(orchestrator, handle.task_id)
        self._write_runtime_meta(task_dir, exit_code=1)
        launcher.sessions[task.session_name] = False

        refreshed = orchestrator.get_task(handle.task_id)
        result = orchestrator.get_result(handle.task_id)

        self.assertEqual(refreshed.status, TaskStatus.FAILED)
        self.assertEqual(refreshed.failure_reason, TaskFailureReason.ARTIFACT_MISSING)
        assert result is not None
        self.assertEqual(result.outcome, "failure")
        self.assertIn("completion artifact", result.error or "")
        self.assertTrue(refreshed.retryable)

    def test_retryable_marker_terminates_running_task_with_retryable_failure(self) -> None:
        orchestrator, launcher, root = self._create_orchestrator()

        handle = orchestrator.start_task(self._request(root))
        task = orchestrator.get_task(handle.task_id)
        task_dir = self._task_dir(orchestrator, handle.task_id)
        (task_dir / "stderr.log").write_text("stream disconnected before completion\n", encoding="utf-8")

        refreshed = orchestrator.get_task(handle.task_id)
        result = orchestrator.get_result(handle.task_id)

        self.assertEqual(refreshed.status, TaskStatus.FAILED)
        self.assertTrue(refreshed.retryable)
        self.assertEqual(launcher.killed, [task.session_name])
        assert result is not None
        self.assertIn("transport", (result.error or "").lower())

    def test_stop_task_marks_task_canceled(self) -> None:
        orchestrator, launcher, root = self._create_orchestrator()

        handle = orchestrator.start_task(self._request(root))

        orchestrator.stop_task(handle.task_id)
        task = orchestrator.get_task(handle.task_id)
        result = orchestrator.get_result(handle.task_id)

        self.assertEqual(task.status, TaskStatus.CANCELED)
        self.assertEqual(task.failure_reason, TaskFailureReason.CANCELED)
        self.assertTrue(launcher.interrupted)
        self.assertTrue(launcher.killed)
        assert result is not None
        self.assertEqual(result.outcome, "failure")
        self.assertIn("canceled", (result.error or "").lower())

    def test_timeout_marks_task_timeout_and_writes_failure_result(self) -> None:
        clock = FakeClock(datetime(2026, 4, 24, tzinfo=UTC))
        orchestrator, launcher, root = self._create_orchestrator(clock=clock)

        handle = orchestrator.start_task(self._request(root, timeout_seconds=1))
        clock.advance(5)

        task = orchestrator.get_task(handle.task_id)
        result = orchestrator.get_result(handle.task_id)

        self.assertEqual(task.status, TaskStatus.TIMEOUT)
        self.assertEqual(task.failure_reason, TaskFailureReason.TIMEOUT)
        self.assertFalse(task.retryable)
        self.assertTrue(launcher.killed)
        assert result is not None
        self.assertIn("timed out", (result.error or "").lower())

    def test_start_task_records_launch_error_without_raising(self) -> None:
        orchestrator, _launcher, root = self._create_orchestrator(fail_start=True)

        handle = orchestrator.start_task(self._request(root))
        task = orchestrator.get_task(handle.task_id)
        result = orchestrator.get_result(handle.task_id)

        self.assertEqual(task.status, TaskStatus.FAILED)
        self.assertEqual(task.failure_reason, TaskFailureReason.LAUNCH_ERROR)
        assert result is not None
        self.assertIn("tmux new-session failed", result.error or "")


if __name__ == "__main__":
    unittest.main()
