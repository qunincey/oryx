from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from .adapters import LaunchContext, WorkerAdapter, default_adapters
from .artifacts import (
    load_task_result,
    synthesize_failure_result,
    task_status_from_result,
    write_task_result,
)
from .models import (
    TERMINAL_STATUSES,
    TaskFailureReason,
    TaskHandle,
    TaskRecord,
    TaskRequest,
    TaskResult,
    TaskStatus,
)
from .runtime import (
    SessionLauncher,
    TmuxSessionLauncher,
    WorkspaceManager,
    build_wrapped_command,
    read_runtime_meta,
)
from .store import TaskStore

_RETRYABLE_FAILURE_MARKERS: tuple[tuple[str, str], ...] = (
    ("stream disconnected before completion", "worker transport stream disconnected"),
    ("transport error: network error", "worker transport network error"),
    ("error decoding response body", "worker transport response decode failed"),
    ("utf-8 encoding error", "codex workspace metadata rejected non-ascii path"),
    ("failed to convert header to a str", "codex workspace metadata rejected non-ascii path"),
    ("trust this folder", "codex onboarding prompt requires trust confirmation"),
    ("trust directory", "codex onboarding prompt requires trust confirmation"),
    ("gpt-5.4", "codex onboarding prompt requires model selection confirmation"),
)


class Orchestrator:
    def __init__(
        self,
        *,
        data_root: Path | None = None,
        store: TaskStore | None = None,
        session_launcher: SessionLauncher | None = None,
        adapters: dict[str, WorkerAdapter] | None = None,
        workspace_manager: WorkspaceManager | None = None,
        clock: Callable[[], datetime] | None = None,
        poll_interval: float = 0.5,
        stop_grace_seconds: float = 2.0,
    ) -> None:
        self.data_root = Path(data_root or Path.home() / ".orx").resolve()
        self.data_root.mkdir(parents=True, exist_ok=True)
        (self.data_root / "tasks").mkdir(parents=True, exist_ok=True)
        self.store = store or TaskStore(self.data_root / "tasks.db")
        self.session_launcher = session_launcher or TmuxSessionLauncher()
        self.adapters = adapters or default_adapters()
        self.workspace_manager = workspace_manager or WorkspaceManager()
        self._clock = clock or self._default_now
        self.poll_interval = poll_interval
        self.stop_grace_seconds = stop_grace_seconds

    def start_task(self, request: TaskRequest) -> TaskHandle:
        adapter = self.adapters.get(request.worker_type)
        if adapter is None:
            raise ValueError(f"unsupported worker_type: {request.worker_type}")

        now = self._now()
        task_id = self._new_task_id()
        session_name = f"orx-{task_id}"
        task_dir = self.data_root / "tasks" / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        workspace = self.workspace_manager.resolve(request, task_id=task_id, data_root=self.data_root)

        prompt_path = task_dir / "prompt.txt"
        result_path = task_dir / "result.json"
        stdout_log_path = task_dir / "stdout.log"
        stderr_log_path = task_dir / "stderr.log"
        runtime_meta_path = task_dir / "runtime.json"
        stdout_log_path.touch()
        stderr_log_path.touch()

        prompt_text = adapter.build_prompt(request, task_dir)
        prompt_path.write_text(prompt_text, encoding="utf-8")

        record = TaskRecord(
            task_id=task_id,
            worker_type=request.worker_type,
            status=TaskStatus.PENDING,
            cwd=request.cwd.resolve(),
            workspace_strategy=request.workspace_strategy,
            workspace_path=workspace.path,
            branch_name=workspace.branch_name,
            worktree_path=workspace.worktree_path,
            session_name=session_name,
            prompt_path=prompt_path,
            result_path=result_path,
            stdout_log_path=stdout_log_path,
            stderr_log_path=stderr_log_path,
            runtime_meta_path=runtime_meta_path,
            timeout_seconds=request.timeout_seconds,
            exit_code=None,
            failure_reason=None,
            failure_message=None,
            retryable=False,
            metadata=dict(request.metadata),
            created_at=now,
            started_at=None,
            finished_at=None,
            updated_at=now,
            stdout_offset=0,
            stderr_offset=0,
        )
        self.store.create_task(record)
        self.store.append_event(task_id, "task_created", {"worker_type": request.worker_type})

        try:
            prepared_workspace = self.workspace_manager.prepare(
                request,
                task_id=task_id,
                data_root=self.data_root,
            )
            record = self.store.update_task(
                task_id,
                workspace_path=prepared_workspace.path,
                branch_name=prepared_workspace.branch_name,
                worktree_path=prepared_workspace.worktree_path,
                status=TaskStatus.STARTING,
                started_at=now,
                updated_at=now,
            )
            context = LaunchContext(
                task_id=task_id,
                request=request,
                task_dir=task_dir,
                workspace_path=prepared_workspace.path,
                prompt_path=prompt_path,
                result_path=result_path,
            )
            worker_command = adapter.build_launch_command(context)
            wrapped_command = build_wrapped_command(
                worker_command=worker_command,
                stdout_log_path=stdout_log_path,
                stderr_log_path=stderr_log_path,
                runtime_meta_path=runtime_meta_path,
            )
            self.session_launcher.start(session_name, prepared_workspace.path, wrapped_command)
            if self.session_launcher.session_exists(session_name):
                self.store.append_event(task_id, "session_created", {"session_name": session_name})
                self.store.update_task(task_id, status=TaskStatus.RUNNING, updated_at=self._now())
        except Exception as exc:
            self._finalize_failure(
                task_id=task_id,
                status=TaskStatus.FAILED,
                reason=TaskFailureReason.LAUNCH_ERROR,
                message=str(exc),
            )

        return TaskHandle(task_id=task_id)

    def get_task(self, task_id: str) -> TaskRecord:
        return self._reconcile_task(task_id)

    def wait_task(self, task_id: str, timeout: float | None = None) -> TaskResult:
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            task = self._reconcile_task(task_id)
            if task.status in TERMINAL_STATUSES:
                result = self._load_result(task)
                if result is None:
                    raise RuntimeError(f"terminal task {task_id} is missing result.json")
                return result
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError(f"task {task_id} did not finish within {timeout} seconds")
            time.sleep(self.poll_interval)

    def stop_task(self, task_id: str) -> None:
        task = self._reconcile_task(task_id)
        if task.status in TERMINAL_STATUSES:
            return
        self.store.append_event(task_id, "task_stopped", {})
        try:
            if self.session_launcher.session_exists(task.session_name):
                self.session_launcher.send_interrupt(task.session_name)
                deadline = time.monotonic() + self.stop_grace_seconds
                while self.stop_grace_seconds > 0 and time.monotonic() < deadline:
                    if not self.session_launcher.session_exists(task.session_name):
                        break
                    time.sleep(min(self.poll_interval, 0.1))
                if self.session_launcher.session_exists(task.session_name):
                    self.session_launcher.kill_session(task.session_name)
            self._finalize_failure(
                task_id=task_id,
                status=TaskStatus.CANCELED,
                reason=TaskFailureReason.CANCELED,
                message="task was canceled before completion",
            )
        except Exception as exc:
            self._finalize_failure(
                task_id=task_id,
                status=TaskStatus.FAILED,
                reason=TaskFailureReason.RUNTIME_ERROR,
                message=str(exc),
            )

    def list_output(
        self,
        task_id: str,
        *,
        after_seq: int | None = None,
        stream: str | None = None,
    ) -> list:
        self._reconcile_task(task_id)
        return self.store.list_output(task_id, after_seq=after_seq, stream=stream)

    def get_result(self, task_id: str) -> TaskResult | None:
        task = self._reconcile_task(task_id)
        return self._load_result(task)

    def _reconcile_task(self, task_id: str) -> TaskRecord:
        task = self.store.get_task(task_id)
        task = self._ingest_output(task)
        if task.status in TERMINAL_STATUSES:
            return task

        if task.result_path.exists():
            return self._finalize_from_result(task)

        if self._has_timed_out(task):
            try:
                if self.session_launcher.session_exists(task.session_name):
                    self.session_launcher.kill_session(task.session_name)
            except Exception as exc:
                return self._finalize_failure(
                    task_id=task.task_id,
                    status=TaskStatus.FAILED,
                    reason=TaskFailureReason.RUNTIME_ERROR,
                    message=str(exc),
                )
            return self._finalize_failure(
                task_id=task.task_id,
                status=TaskStatus.TIMEOUT,
                reason=TaskFailureReason.TIMEOUT,
                message="task timed out before completion",
            )

        retryable_message = self._find_retryable_failure_message(task)
        if retryable_message is not None:
            try:
                if self.session_launcher.session_exists(task.session_name):
                    self.session_launcher.kill_session(task.session_name)
            except Exception:
                pass
            return self._finalize_failure(
                task_id=task.task_id,
                status=TaskStatus.FAILED,
                reason=TaskFailureReason.RUNTIME_ERROR,
                message=retryable_message,
                retryable=True,
            )

        try:
            session_alive = self.session_launcher.session_exists(task.session_name)
        except Exception as exc:
            return self._finalize_failure(
                task_id=task.task_id,
                status=TaskStatus.FAILED,
                reason=TaskFailureReason.RUNTIME_ERROR,
                message=str(exc),
            )

        if session_alive and task.status in {TaskStatus.PENDING, TaskStatus.STARTING}:
            task = self.store.update_task(task.task_id, status=TaskStatus.RUNNING, updated_at=self._now())
            return task

        if not session_alive and task.status in {TaskStatus.STARTING, TaskStatus.RUNNING, TaskStatus.PENDING}:
            runtime_meta = read_runtime_meta(task.runtime_meta_path)
            exit_code = runtime_meta.get("exit_code")
            message = "worker exited without completion artifact"
            return self._finalize_failure(
                task_id=task.task_id,
                status=TaskStatus.FAILED,
                reason=TaskFailureReason.ARTIFACT_MISSING,
                message=message,
                retryable=True,
                exit_code=None if exit_code is None else int(exit_code),
            )

        return task

    def _finalize_from_result(self, task: TaskRecord) -> TaskRecord:
        try:
            result = load_task_result(task.result_path, task_id=task.task_id)
        except Exception as exc:
            return self._finalize_failure(
                task_id=task.task_id,
                status=TaskStatus.FAILED,
                reason=TaskFailureReason.RUNTIME_ERROR,
                message=str(exc),
            )

        if self.session_launcher.session_exists(task.session_name):
            try:
                self.session_launcher.kill_session(task.session_name)
            except Exception:
                pass

        runtime_meta = read_runtime_meta(task.runtime_meta_path)
        exit_code = runtime_meta.get("exit_code")
        status = task_status_from_result(result)
        failure_message = result.error if status == TaskStatus.FAILED else None
        return self.store.update_task(
            task.task_id,
            status=status,
            finished_at=self._now(),
            updated_at=self._now(),
            exit_code=None if exit_code is None else int(exit_code),
            failure_reason=None,
            failure_message=failure_message,
            retryable=False,
        )

    def _finalize_failure(
        self,
        *,
        task_id: str,
        status: TaskStatus,
        reason: TaskFailureReason,
        message: str,
        retryable: bool | None = None,
        exit_code: int | None = None,
    ) -> TaskRecord:
        task = self.store.get_task(task_id)
        resolved_retryable = self._resolve_retryable(
            reason=reason,
            task=task,
            message=message,
            override=retryable,
        )
        result = synthesize_failure_result(
            task_id=task_id,
            reason=reason,
            error=message,
            metadata={
                "worker_type": task.worker_type,
                "model": task.metadata.get("model") if isinstance(task.metadata, dict) else None,
            },
        )
        write_task_result(task.result_path, result)
        return self.store.update_task(
            task_id,
            status=status,
            finished_at=self._now(),
            updated_at=self._now(),
            exit_code=exit_code,
            failure_reason=reason,
            failure_message=message,
            retryable=resolved_retryable,
        )

    def _load_result(self, task: TaskRecord) -> TaskResult | None:
        if task.status not in TERMINAL_STATUSES:
            return None
        if not task.result_path.exists():
            fallback = synthesize_failure_result(
                task_id=task.task_id,
                reason=TaskFailureReason.RUNTIME_ERROR,
                error="terminal task is missing result.json",
            )
            write_task_result(task.result_path, fallback)
            return fallback
        return load_task_result(task.result_path, task_id=task.task_id)

    def _find_retryable_failure_message(self, task: TaskRecord) -> str | None:
        for path in (task.stdout_log_path, task.stderr_log_path):
            if not path.exists():
                continue
            content = path.read_text(encoding="utf-8", errors="replace").lower()
            for marker, message in _RETRYABLE_FAILURE_MARKERS:
                if marker in content:
                    return message
        return None

    def _resolve_retryable(
        self,
        *,
        reason: TaskFailureReason,
        task: TaskRecord,
        message: str,
        override: bool | None,
    ) -> bool:
        if override is not None:
            return override
        if reason == TaskFailureReason.ARTIFACT_MISSING:
            return True
        if reason in {
            TaskFailureReason.TIMEOUT,
            TaskFailureReason.CANCELED,
            TaskFailureReason.LAUNCH_ERROR,
        }:
            return False
        if self._find_retryable_failure_message(task) is not None:
            return True
        lowered = message.lower()
        return any(marker in lowered for marker, _ in _RETRYABLE_FAILURE_MARKERS)

    def _ingest_output(self, task: TaskRecord) -> TaskRecord:
        updates: dict[str, object] = {}
        stdout_offset = self._ingest_stream(task, task.stdout_log_path, task.stdout_offset, "stdout")
        stderr_offset = self._ingest_stream(task, task.stderr_log_path, task.stderr_offset, "stderr")
        if stdout_offset != task.stdout_offset:
            updates["stdout_offset"] = stdout_offset
        if stderr_offset != task.stderr_offset:
            updates["stderr_offset"] = stderr_offset
        if updates:
            updates["updated_at"] = self._now()
            task = self.store.update_task(task.task_id, **updates)
        return task

    def _ingest_stream(self, task: TaskRecord, path: Path, offset: int, stream: str) -> int:
        if not path.exists():
            return offset
        with path.open("rb") as handle:
            handle.seek(offset)
            data = handle.read()
        if not data:
            return offset
        content = data.decode("utf-8", errors="replace")
        self.store.append_output(task.task_id, stream, content, created_at=self._now())
        self.store.append_event(task.task_id, "output_captured", {"stream": stream, "bytes": len(data)}, created_at=self._now())
        return offset + len(data)

    def _has_timed_out(self, task: TaskRecord) -> bool:
        if task.timeout_seconds is None or task.started_at is None:
            return False
        return self._now() >= task.started_at + timedelta(seconds=task.timeout_seconds)

    def _new_task_id(self) -> str:
        return f"task-{uuid4().hex[:12]}"

    def _now(self) -> datetime:
        current = self._clock()
        if current.tzinfo is None:
            return current.replace(tzinfo=UTC)
        return current

    @staticmethod
    def _default_now() -> datetime:
        return datetime.now(UTC)
