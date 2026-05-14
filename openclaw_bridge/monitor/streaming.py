from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Queue
from typing import Callable, Protocol

from orx.models import TaskOutputChunk
from orx.orchestrator import Orchestrator

from .config import MonitorConfig
from .service import build_project_payload, read_task_output_snapshot, runtime_state_from_status

DEFAULT_STREAM_POLL_INTERVAL_SECONDS = 1.0
DEFAULT_STREAM_IDLE_INTERVAL_SECONDS = 3.0
DEFAULT_STREAM_HEARTBEAT_SECONDS = 15.0
DEFAULT_STREAM_RETRY_MILLISECONDS = 3000
DEFAULT_STREAM_REPLAY_LIMIT = 200


class RuntimeReader(Protocol):
    def get_task(self, task_id: str): ...

    def list_output(
        self,
        task_id: str,
        *,
        after_seq: int | None = None,
        stream: str | None = None,
    ) -> list[TaskOutputChunk]: ...

RuntimeReaderFactory = Callable[[Path], RuntimeReader]


@dataclass(slots=True)
class StreamEvent:
    event_id: str
    event: str
    data: dict[str, object]


@dataclass(slots=True)
class StreamSubscription:
    subscription_id: int
    queue: Queue[StreamEvent | None]
    project_id: str | None = None
    task_id: str | None = None


@dataclass(slots=True)
class TrackedTask:
    project_id: str
    project_name: str
    task_id: str
    orx_task_id: str
    runtime_root: Path
    last_seq: int
    last_state: str
    last_runtime_status: str
    last_updated_at: str
    last_completed_at: str | None
    last_error: str | None
    sent_initial_update: bool = False

    @property
    def key(self) -> tuple[str, str]:
        return (self.project_id, self.orx_task_id)


class StreamEventHub:
    def __init__(self, *, replay_limit: int = DEFAULT_STREAM_REPLAY_LIMIT) -> None:
        self._lock = threading.Lock()
        self._subscriptions: dict[int, StreamSubscription] = {}
        self._history: deque[StreamEvent] = deque(maxlen=replay_limit)
        self._next_subscription_id = 1
        self._next_event_id = 1
        self._closed = False

    def subscribe(
        self,
        *,
        project_id: str | None = None,
        task_id: str | None = None,
        last_event_id: str | None = None,
    ) -> StreamSubscription:
        queue: Queue[StreamEvent | None] = Queue()
        replay_after = _coerce_last_event_id(last_event_id)
        with self._lock:
            subscription = StreamSubscription(
                subscription_id=self._next_subscription_id,
                queue=queue,
                project_id=project_id,
                task_id=task_id,
            )
            self._next_subscription_id += 1
            if self._closed:
                queue.put(None)
                return subscription
            replay_events = [
                event
                for event in self._history
                if int(event.event_id) > replay_after and self._matches(subscription, event.data)
            ]
            self._subscriptions[subscription.subscription_id] = subscription
        for event in replay_events:
            queue.put(event)
        return subscription

    def unsubscribe(self, subscription: StreamSubscription) -> None:
        with self._lock:
            self._subscriptions.pop(subscription.subscription_id, None)

    def publish_task_update(self, payload: dict[str, object]) -> StreamEvent | None:
        with self._lock:
            if self._closed:
                return None
            event = StreamEvent(
                event_id=str(self._next_event_id),
                event="task_update",
                data=payload,
            )
            self._next_event_id += 1
            self._history.append(event)
            subscribers = [
                subscription
                for subscription in self._subscriptions.values()
                if self._matches(subscription, payload)
            ]
        for subscription in subscribers:
            subscription.queue.put(event)
        return event

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            subscriptions = list(self._subscriptions.values())
            self._subscriptions.clear()
        for subscription in subscriptions:
            subscription.queue.put(None)

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscriptions)

    @staticmethod
    def _matches(subscription: StreamSubscription, payload: dict[str, object]) -> bool:
        if subscription.project_id and subscription.project_id != str(payload.get("project_id") or ""):
            return False
        if subscription.task_id and subscription.task_id != str(payload.get("task_id") or ""):
            return False
        return True


class MonitorStreamController:
    def __init__(
        self,
        config_getter,
        *,
        orchestrator_factory=None,
        poll_interval_seconds: float = DEFAULT_STREAM_POLL_INTERVAL_SECONDS,
        idle_interval_seconds: float = DEFAULT_STREAM_IDLE_INTERVAL_SECONDS,
        heartbeat_seconds: float = DEFAULT_STREAM_HEARTBEAT_SECONDS,
        retry_milliseconds: int = DEFAULT_STREAM_RETRY_MILLISECONDS,
        replay_limit: int = DEFAULT_STREAM_REPLAY_LIMIT,
    ) -> None:
        self._config_getter = config_getter
        self._orchestrator_factory = orchestrator_factory or self._build_default_orchestrator
        self.poll_interval_seconds = max(float(poll_interval_seconds), 0.05)
        self.idle_interval_seconds = max(float(idle_interval_seconds), self.poll_interval_seconds)
        self.heartbeat_seconds = max(float(heartbeat_seconds), 0.05)
        self.retry_milliseconds = max(int(retry_milliseconds), 1)
        self._hub = StreamEventHub(replay_limit=replay_limit)
        self._tracked: dict[tuple[str, str], TrackedTask] = {}
        self._orchestrators: dict[Path, RuntimeReader] = {}
        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name="openclaw-monitor-stream",
            daemon=True,
        )

    def start(self) -> None:
        if not self._thread.is_alive():
            self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._hub.close()
        if self._thread.is_alive():
            self._thread.join(timeout=1)

    def subscribe(
        self,
        *,
        project_id: str | None = None,
        task_id: str | None = None,
        last_event_id: str | None = None,
    ) -> StreamSubscription:
        return self._hub.subscribe(
            project_id=project_id,
            task_id=task_id,
            last_event_id=last_event_id,
        )

    def unsubscribe(self, subscription: StreamSubscription) -> None:
        self._hub.unsubscribe(subscription)

    def subscriber_count(self) -> int:
        return self._hub.subscriber_count()

    def next_event(
        self,
        subscription: StreamSubscription,
        *,
        timeout: float,
    ) -> tuple[bool, StreamEvent | None]:
        try:
            item = subscription.queue.get(timeout=timeout)
        except Empty:
            return False, None
        return True, item

    def _run(self) -> None:
        while not self._stop_event.is_set():
            config = self._config_getter()
            active_tasks = self._discover_active_tasks(config)
            for key, task in active_tasks.items():
                if key not in self._tracked:
                    self._tracked[key] = self._bootstrap_task(task)

            keys_to_poll = list({*self._tracked.keys(), *active_tasks.keys()})
            for key in keys_to_poll:
                tracked = self._tracked.get(key)
                if tracked is None:
                    continue
                try:
                    self._poll_task(tracked)
                except Exception:
                    self._tracked.pop(key, None)

            sleep_seconds = self.poll_interval_seconds if keys_to_poll else max(
                self.idle_interval_seconds,
                float(config.poll_interval_seconds),
            )
            self._stop_event.wait(sleep_seconds)

    def _discover_active_tasks(self, config: MonitorConfig) -> dict[tuple[str, str], TrackedTask]:
        active: dict[tuple[str, str], TrackedTask] = {}
        for project in config.projects:
            try:
                project_payload = build_project_payload(project, include_events=False)
            except Exception:
                continue
            for task in project_payload["tasks"]:
                runtime = task.get("runtime")
                orx_task_id = str(task.get("orx_task_id") or "").strip()
                if runtime is None or not orx_task_id:
                    continue
                if str(task.get("state") or "") != "in_progress":
                    continue
                tracked = TrackedTask(
                    project_id=str(project_payload["id"]),
                    project_name=str(project_payload["name"]),
                    task_id=str(task["task_id"]),
                    orx_task_id=orx_task_id,
                    runtime_root=project.runtime_root,
                    last_seq=0,
                    last_state=str(task.get("state") or ""),
                    last_runtime_status=str(runtime.get("status") or ""),
                    last_updated_at=str(task.get("updated_at") or ""),
                    last_completed_at=_optional_str(task.get("completed_at")),
                    last_error=_optional_str(task.get("last_error")),
                )
                active[tracked.key] = tracked
        return active

    def _bootstrap_task(self, task: TrackedTask) -> TrackedTask:
        snapshot = read_task_output_snapshot(task.runtime_root, task.orx_task_id)
        return TrackedTask(
            project_id=task.project_id,
            project_name=task.project_name,
            task_id=task.task_id,
            orx_task_id=task.orx_task_id,
            runtime_root=task.runtime_root,
            last_seq=int(snapshot["last_seq"]),
            last_state=task.last_state,
            last_runtime_status=task.last_runtime_status,
            last_updated_at=task.last_updated_at,
            last_completed_at=task.last_completed_at,
            last_error=task.last_error,
            sent_initial_update=False,
        )

    def _poll_task(self, tracked: TrackedTask) -> None:
        orchestrator = self._orchestrator(tracked.runtime_root)
        chunks = orchestrator.list_output(tracked.orx_task_id, after_seq=tracked.last_seq)
        runtime_task = orchestrator.get_task(tracked.orx_task_id)

        payload = self._build_payload(tracked, runtime_task, chunks)
        state_changed = (
            not tracked.sent_initial_update
            or
            payload["state"] != tracked.last_state
            or payload["runtime_status"] != tracked.last_runtime_status
            or payload["updated_at"] != tracked.last_updated_at
            or payload["completed_at"] != tracked.last_completed_at
            or payload["last_error"] != tracked.last_error
        )
        if chunks or state_changed:
            self._hub.publish_task_update(payload)

        tracked.last_seq = max((chunk.seq for chunk in chunks), default=tracked.last_seq)
        tracked.last_state = str(payload["state"])
        tracked.last_runtime_status = str(payload["runtime_status"])
        tracked.last_updated_at = str(payload["updated_at"])
        tracked.last_completed_at = _optional_str(payload.get("completed_at"))
        tracked.last_error = _optional_str(payload.get("last_error"))
        tracked.sent_initial_update = True

        if runtime_task.is_terminal:
            self._tracked.pop(tracked.key, None)

    def _build_payload(self, tracked: TrackedTask, runtime_task, chunks: list[TaskOutputChunk]) -> dict[str, object]:
        state = runtime_state_from_status(
            runtime_task.status.value,
            retryable=bool(runtime_task.retryable),
        )
        return {
            "project_id": tracked.project_id,
            "project_name": tracked.project_name,
            "task_id": tracked.task_id,
            "orx_task_id": tracked.orx_task_id,
            "state": state,
            "runtime_status": runtime_task.status.value,
            "retryable": bool(runtime_task.retryable),
            "updated_at": runtime_task.updated_at.isoformat(),
            "completed_at": None if runtime_task.finished_at is None else runtime_task.finished_at.isoformat(),
            "last_error": runtime_task.failure_message,
            "stdout_chunks": [_serialize_chunk(chunk) for chunk in chunks if chunk.stream == "stdout"],
            "stderr_chunks": [_serialize_chunk(chunk) for chunk in chunks if chunk.stream == "stderr"],
        }

    def _orchestrator(self, runtime_root: Path) -> RuntimeReader:
        resolved = runtime_root.resolve()
        orchestrator = self._orchestrators.get(resolved)
        if orchestrator is None:
            orchestrator = self._orchestrator_factory(resolved)
            self._orchestrators[resolved] = orchestrator
        return orchestrator

    @staticmethod
    def _build_default_orchestrator(runtime_root: Path) -> Orchestrator:
        return Orchestrator(data_root=runtime_root)


def _coerce_last_event_id(value: str | None) -> int:
    if value is None:
        return 0
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 0


def _optional_str(value: object) -> str | None:
    if value in {None, ""}:
        return None
    return str(value)


def _serialize_chunk(chunk: TaskOutputChunk) -> dict[str, object]:
    return {
        "seq": chunk.seq,
        "stream": chunk.stream,
        "content": chunk.content,
        "created_at": chunk.created_at.isoformat(),
    }
