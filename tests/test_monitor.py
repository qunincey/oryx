from __future__ import annotations

import io
import json
import shutil
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from openclaw_bridge.models import TaskRecord
from openclaw_bridge.registry import save_registry
from openclaw_bridge.scaffold import init_docs_repo


class FakeSessionLauncher:
    def __init__(self) -> None:
        self.sessions: dict[str, bool] = {}

    def session_exists(self, session_name: str) -> bool:
        return self.sessions.get(session_name, False)

    def start(self, session_name: str, cwd: Path, command: str) -> None:
        self.sessions[session_name] = True

    def send_interrupt(self, session_name: str) -> None:
        self.sessions[session_name] = False

    def kill_session(self, session_name: str) -> None:
        self.sessions[session_name] = False


class MonitorConfigAndAggregationTests(unittest.TestCase):
    def _create_docs_repo(self, name: str) -> Path:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        docs_repo = Path(temp_dir.name) / name
        init_docs_repo(
            docs_repo,
            project_name=name,
            project_description=f"{name} description",
        )
        return docs_repo

    def _create_runtime_root(self, name: str) -> Path:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        runtime_root = Path(temp_dir.name) / name / ".openclaw-state" / "orx"
        runtime_root.mkdir(parents=True, exist_ok=True)
        return runtime_root

    def _runtime_root_for_docs_repo(self, docs_repo: Path) -> Path:
        return docs_repo / ".openclaw-state" / "orx"

    def _make_task(
        self,
        *,
        task_id: str,
        state: str,
        updated_at: str,
        created_at: str | None = None,
        completed_at: str | None = None,
        orx_task_id: str | None = None,
        last_error: str | None = None,
    ) -> TaskRecord:
        timestamp = created_at or updated_at
        return TaskRecord(
            task_id=task_id,
            source_repo="docs-repo",
            source_ref=f"docs/prd/{task_id}.md",
            source_title=f"Task {task_id}",
            target_repo="repo-a",
            target_issue_number="123",
            target_issue_url=f"https://example.com/issues/{task_id}",
            state=state,
            worker_type="codex",
            worker_model="gpt-5-codex",
            worktree_path=f"../worktrees/repo-a/{task_id}",
            tmux_session=f"orx-{task_id}",
            branch_name=f"openclaw/{task_id}/task",
            pr_url=None,
            last_error=last_error,
            created_at=timestamp,
            updated_at=updated_at,
            issue_title=f"Issue {task_id}",
            issue_body_path=f"/tmp/{task_id}/issue.md",
            worker_prompt_path=f"/tmp/{task_id}/prompt.txt",
            orx_task_id=orx_task_id,
            launched_at=timestamp,
            launch_attempts=1,
            completed_at=completed_at,
            commit_sha=None,
            commit_message=None,
            last_notified_at=None,
            completion_summary=None,
        )

    def _write_event(
        self,
        docs_repo: Path,
        *,
        task_id: str,
        event_type: str,
        timestamp: str,
        message: str = "",
    ) -> None:
        path = docs_repo / ".openclaw-state" / "watchdog-events.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "timestamp": timestamp,
            "task_id": task_id,
            "source_ref": f"docs/prd/{task_id}.md",
            "source_title": f"Task {task_id}",
            "target_repo": "repo-a",
            "target_issue_url": f"https://example.com/issues/{task_id}",
            "branch_name": f"openclaw/{task_id}/task",
            "event": event_type,
            "state": event_type,
            "message": message or event_type,
        }
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def _write_runtime_task(
        self,
        runtime_root: Path,
        *,
        task_id: str,
        status: str = "RUNNING",
        failure_message: str | None = None,
        retryable: bool = False,
        metadata: dict[str, object] | None = None,
    ) -> None:
        from orx.models import TaskRecord as OrxTaskRecord
        from orx.models import TaskStatus
        from orx.store import TaskStore

        task_dir = runtime_root / "tasks" / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        now = datetime.now(UTC).replace(microsecond=0)
        terminal_statuses = {"SUCCEEDED", "FAILED", "CANCELED", "TIMEOUT"}
        workspace_root = runtime_root.parent.parent
        record = OrxTaskRecord(
            task_id=task_id,
            worker_type="codex",
            status=TaskStatus(status),
            cwd=workspace_root,
            workspace_strategy="git_worktree",
            workspace_path=workspace_root / "worktree",
            branch_name=f"openclaw/{task_id}/task",
            worktree_path=workspace_root / "worktree",
            session_name=f"orx-{task_id}",
            prompt_path=task_dir / "prompt.txt",
            result_path=task_dir / "result.json",
            stdout_log_path=task_dir / "stdout.log",
            stderr_log_path=task_dir / "stderr.log",
            runtime_meta_path=task_dir / "runtime.json",
            timeout_seconds=60,
            exit_code=None,
            failure_reason=None,
            failure_message=failure_message,
            retryable=retryable,
            metadata=metadata or {"model": "gpt-5-codex"},
            created_at=now,
            started_at=now,
            finished_at=now if status in terminal_statuses else None,
            updated_at=now,
            stdout_offset=0,
            stderr_offset=0,
        )
        store = TaskStore(runtime_root / "tasks.db")
        store.create_task(record)

    def test_load_monitor_config_normalizes_legacy_docs_repo_projects(self) -> None:
        from openclaw_bridge.monitor.config import load_monitor_config

        docs_repo = self._create_docs_repo("alpha")
        config_path = docs_repo.parent / "monitor.json"
        config_path.write_text(
            json.dumps(
                {
                    "projects": [
                        {
                            "id": "alpha",
                            "name": "Alpha",
                            "docs_repo": str(docs_repo),
                        }
                    ]
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        config = load_monitor_config(config_path)

        self.assertEqual(config.poll_interval_seconds, 3)
        self.assertEqual(len(config.projects), 1)
        self.assertEqual(
            config.projects[0].runtime_root,
            (docs_repo / ".openclaw-state" / "orx").resolve(),
        )

    def test_load_monitor_config_accepts_runtime_root_projects(self) -> None:
        from openclaw_bridge.monitor.config import load_monitor_config

        runtime_root = self._create_runtime_root("alpha")
        config_path = runtime_root.parent.parent / "monitor.json"
        config_path.write_text(
            json.dumps(
                {
                    "projects": [
                        {
                            "id": "alpha",
                            "name": "Alpha",
                            "runtime_root": str(runtime_root),
                        }
                    ],
                    "poll_interval_seconds": 5,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        config = load_monitor_config(config_path)

        self.assertEqual(config.poll_interval_seconds, 5)
        self.assertEqual(config.projects[0].runtime_root, runtime_root.resolve())

    def test_load_monitor_config_rejects_duplicate_project_ids(self) -> None:
        from openclaw_bridge.monitor.config import load_monitor_config

        runtime_root = self._create_runtime_root("alpha")
        config_path = runtime_root.parent.parent / "monitor.json"
        config_path.write_text(
            json.dumps(
                {
                    "projects": [
                        {"id": "dup", "name": "One", "runtime_root": str(runtime_root)},
                        {"id": "dup", "name": "Two", "runtime_root": str(runtime_root.parent / "other-orx")},
                    ]
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(ValueError, "duplicate project id"):
            load_monitor_config(config_path)

    def test_load_monitor_config_rejects_duplicate_runtime_roots(self) -> None:
        from openclaw_bridge.monitor.config import load_monitor_config

        runtime_root = self._create_runtime_root("alpha")
        config_path = runtime_root.parent.parent / "monitor.json"
        config_path.write_text(
            json.dumps(
                {
                    "projects": [
                        {"id": "alpha", "name": "Alpha", "runtime_root": str(runtime_root)},
                        {"id": "beta", "name": "Beta", "runtime_root": str(runtime_root)},
                    ]
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(ValueError, "duplicate runtime_root"):
            load_monitor_config(config_path)

    def test_load_monitor_config_accepts_empty_project_list(self) -> None:
        from openclaw_bridge.monitor.config import load_monitor_config

        config_path = Path(tempfile.mkdtemp()) / "monitor.json"
        self.addCleanup(lambda: shutil.rmtree(config_path.parent, ignore_errors=True))
        config_path.write_text(
            json.dumps(
                {
                    "projects": [],
                    "poll_interval_seconds": 3,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        config = load_monitor_config(config_path)

        self.assertEqual(config.projects, ())

    def test_add_project_to_monitor_config_persists_runtime_root_format(self) -> None:
        from openclaw_bridge.monitor.config import add_project_to_monitor_config, load_monitor_config

        runtime_root = self._create_runtime_root("alpha")
        config_path = runtime_root.parent.parent / "monitor.json"
        config_path.write_text(
            json.dumps(
                {
                    "projects": [],
                    "poll_interval_seconds": 3,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        project = add_project_to_monitor_config(
            config_path,
            runtime_root=runtime_root,
            name="Alpha Runtime",
        )
        config = load_monitor_config(config_path)
        raw_payload = json.loads(config_path.read_text(encoding="utf-8"))

        self.assertEqual(project.id, "alpha")
        self.assertEqual(project.name, "Alpha Runtime")
        self.assertEqual(len(config.projects), 1)
        self.assertEqual(config.projects[0].runtime_root, runtime_root.resolve())
        self.assertEqual(
            raw_payload["projects"][0],
            {
                "id": "alpha",
                "name": "Alpha Runtime",
                "runtime_root": str(runtime_root.resolve()),
            },
        )

    def test_build_dashboard_aggregates_runtime_hybrid_and_registry_only_tasks(self) -> None:
        from openclaw_bridge.monitor.config import MonitorConfig, MonitorProject
        from openclaw_bridge.monitor.service import build_dashboard_payload

        alpha = self._create_docs_repo("alpha")
        beta_runtime_root = self._create_runtime_root("beta")
        alpha_runtime_root = self._runtime_root_for_docs_repo(alpha)
        now = datetime.now(UTC).replace(microsecond=0)

        save_registry(
            alpha / ".openclaw-state" / "registry.json",
            [
                self._make_task(
                    task_id="task-0001",
                    state="dispatched",
                    updated_at=(now - timedelta(minutes=2)).isoformat(),
                    orx_task_id="orx-alpha-1",
                ),
                self._make_task(
                    task_id="task-0002",
                    state="dispatched",
                    updated_at=(now - timedelta(minutes=1)).isoformat(),
                ),
            ],
        )
        self._write_event(
            alpha,
            task_id="task-0002",
            event_type="dispatched",
            timestamp=(now - timedelta(minutes=1)).isoformat(),
        )
        self._write_runtime_task(alpha_runtime_root, task_id="orx-alpha-1", status="RUNNING")
        self._write_runtime_task(
            beta_runtime_root,
            task_id="orx-beta-1",
            status="FAILED",
            failure_message="worker transport network error",
            retryable=True,
        )

        payload = build_dashboard_payload(
            MonitorConfig(
                projects=(
                    MonitorProject(id="alpha", name="Alpha", runtime_root=alpha_runtime_root),
                    MonitorProject(id="beta", name="Beta", runtime_root=beta_runtime_root),
                ),
                poll_interval_seconds=3,
            )
        )

        self.assertEqual(payload["summary"]["running_count"], 1)
        self.assertEqual(payload["summary"]["blocked_count"], 0)
        self.assertEqual(payload["summary"]["restartable_count"], 1)
        self.assertEqual(payload["summary"]["disconnected_project_count"], 0)
        self.assertEqual(payload["projects"][0]["id"], "alpha")
        self.assertEqual(payload["projects"][0]["runtime_root"], str(alpha_runtime_root.resolve()))
        self.assertEqual(
            [task["task_source"] for task in payload["projects"][0]["tasks"]],
            ["hybrid", "openclaw"],
        )
        self.assertEqual(payload["projects"][0]["tasks"][0]["runtime"]["status"], "RUNNING")
        self.assertEqual(payload["projects"][0]["tasks"][1]["runtime"], None)
        self.assertEqual(payload["projects"][1]["tasks"][0]["task_source"], "runtime")
        self.assertEqual(payload["projects"][1]["tasks"][0]["state"], "restartable")
        self.assertEqual(payload["recent_events"][0]["task_id"], "task-0002")

    def test_build_dashboard_treats_existing_runtime_root_without_tasks_as_connected(self) -> None:
        from openclaw_bridge.monitor.config import MonitorConfig, MonitorProject
        from openclaw_bridge.monitor.service import build_dashboard_payload

        runtime_root = self._create_runtime_root("empty")

        payload = build_dashboard_payload(
            MonitorConfig(
                projects=(MonitorProject(id="empty", name="Empty", runtime_root=runtime_root),),
                poll_interval_seconds=3,
            )
        )

        self.assertEqual(payload["projects"][0]["health"], "healthy")
        self.assertEqual(payload["projects"][0]["tasks"], [])
        self.assertEqual(payload["summary"]["disconnected_project_count"], 0)

    def test_build_dashboard_marks_project_degraded_when_runtime_db_is_invalid(self) -> None:
        from openclaw_bridge.monitor.config import MonitorConfig, MonitorProject
        from openclaw_bridge.monitor.service import build_dashboard_payload

        docs_repo = self._create_docs_repo("alpha")
        runtime_root = self._runtime_root_for_docs_repo(docs_repo)
        now = datetime.now(UTC).replace(microsecond=0).isoformat()
        save_registry(
            docs_repo / ".openclaw-state" / "registry.json",
            [self._make_task(task_id="task-0001", state="dispatched", updated_at=now)],
        )
        db_path = runtime_root / "tasks.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        db_path.write_text("not a sqlite database", encoding="utf-8")

        payload = build_dashboard_payload(
            MonitorConfig(
                projects=(MonitorProject(id="alpha", name="Alpha", runtime_root=runtime_root),),
                poll_interval_seconds=3,
            )
        )

        self.assertEqual(payload["projects"][0]["health"], "degraded")
        self.assertEqual(payload["projects"][0]["tasks"][0]["task_source"], "openclaw")
        self.assertIsNone(payload["projects"][0]["tasks"][0]["runtime"])
        self.assertEqual(payload["summary"]["degraded_project_count"], 1)

    def test_build_dashboard_marks_project_disconnected_when_runtime_root_missing(self) -> None:
        from openclaw_bridge.monitor.config import MonitorConfig, MonitorProject
        from openclaw_bridge.monitor.service import build_dashboard_payload

        runtime_root = Path(tempfile.mkdtemp()) / "missing" / ".openclaw-state" / "orx"
        self.addCleanup(lambda: shutil.rmtree(runtime_root.parents[2], ignore_errors=True))

        payload = build_dashboard_payload(
            MonitorConfig(
                projects=(MonitorProject(id="missing", name="Missing", runtime_root=runtime_root),),
                poll_interval_seconds=3,
            )
        )

        self.assertEqual(payload["projects"][0]["health"], "disconnected")
        self.assertEqual(payload["projects"][0]["tasks"], [])
        self.assertEqual(payload["summary"]["disconnected_project_count"], 1)

    def test_build_task_detail_returns_runtime_and_timeline_for_hybrid_task(self) -> None:
        from openclaw_bridge.monitor.config import MonitorConfig, MonitorProject
        from openclaw_bridge.monitor.service import build_task_detail_payload

        docs_repo = self._create_docs_repo("alpha")
        runtime_root = self._runtime_root_for_docs_repo(docs_repo)
        now = datetime.now(UTC).replace(microsecond=0)
        save_registry(
            docs_repo / ".openclaw-state" / "registry.json",
            [
                self._make_task(
                    task_id="task-0001",
                    state="dispatched",
                    updated_at=now.isoformat(),
                    orx_task_id="orx-1",
                )
            ],
        )
        self._write_event(
            docs_repo,
            task_id="task-0001",
            event_type="restarted",
            timestamp=(now - timedelta(minutes=1)).isoformat(),
        )
        self._write_event(
            docs_repo,
            task_id="task-0001",
            event_type="completed",
            timestamp=now.isoformat(),
        )
        self._write_runtime_task(runtime_root, task_id="orx-1", status="RUNNING")

        payload = build_task_detail_payload(
            MonitorConfig(
                projects=(MonitorProject(id="alpha", name="Alpha", runtime_root=runtime_root),),
                poll_interval_seconds=3,
            ),
            project_id="alpha",
            task_id="task-0001",
        )

        self.assertEqual(payload["project"]["id"], "alpha")
        self.assertEqual(payload["project"]["runtime_root"], str(runtime_root.resolve()))
        self.assertEqual(payload["task"]["task_id"], "task-0001")
        self.assertEqual(payload["task"]["task_source"], "hybrid")
        self.assertEqual(payload["runtime"]["status"], "RUNNING")
        self.assertEqual([event["event"] for event in payload["timeline"]], ["completed", "restarted"])

    def test_build_task_detail_includes_output_snapshot_from_runtime_store(self) -> None:
        from openclaw_bridge.monitor.config import MonitorConfig, MonitorProject
        from openclaw_bridge.monitor.service import build_task_detail_payload
        from orx.store import TaskStore

        docs_repo = self._create_docs_repo("alpha")
        runtime_root = self._runtime_root_for_docs_repo(docs_repo)
        now = datetime.now(UTC).replace(microsecond=0).isoformat()
        save_registry(
            docs_repo / ".openclaw-state" / "registry.json",
            [
                self._make_task(
                    task_id="task-0001",
                    state="dispatched",
                    updated_at=now,
                    orx_task_id="orx-1",
                )
            ],
        )
        self._write_runtime_task(runtime_root, task_id="orx-1", status="RUNNING")
        store = TaskStore(runtime_root / "tasks.db")
        store.append_output("orx-1", "stdout", "hello stdout\n")
        store.append_output("orx-1", "stderr", "hello stderr\n")

        payload = build_task_detail_payload(
            MonitorConfig(
                projects=(MonitorProject(id="alpha", name="Alpha", runtime_root=runtime_root),),
                poll_interval_seconds=3,
            ),
            project_id="alpha",
            task_id="task-0001",
        )

        self.assertEqual(payload["output"]["stdout"], "hello stdout\n")
        self.assertEqual(payload["output"]["stderr"], "hello stderr\n")
        self.assertEqual(payload["output"]["last_seq"], 2)


class MonitorHttpAndCliTests(unittest.TestCase):
    def _create_docs_repo(self, name: str) -> Path:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        docs_repo = Path(temp_dir.name) / name
        init_docs_repo(
            docs_repo,
            project_name=name,
            project_description=f"{name} description",
        )
        return docs_repo

    def _create_runtime_root(self, name: str) -> Path:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        runtime_root = Path(temp_dir.name) / name / ".openclaw-state" / "orx"
        runtime_root.mkdir(parents=True, exist_ok=True)
        return runtime_root

    def _runtime_root_for_docs_repo(self, docs_repo: Path) -> Path:
        return docs_repo / ".openclaw-state" / "orx"

    def _make_task(self, *, task_id: str, state: str, updated_at: str, orx_task_id: str | None = None) -> TaskRecord:
        return TaskRecord(
            task_id=task_id,
            source_repo="docs-repo",
            source_ref=f"docs/prd/{task_id}.md",
            source_title=f"Task {task_id}",
            target_repo="repo-a",
            target_issue_number="123",
            target_issue_url=f"https://example.com/issues/{task_id}",
            state=state,
            worker_type="codex",
            worker_model="gpt-5-codex",
            worktree_path=f"../worktrees/repo-a/{task_id}",
            tmux_session=f"orx-{task_id}",
            branch_name=f"openclaw/{task_id}/task",
            pr_url=None,
            last_error=None,
            created_at=updated_at,
            updated_at=updated_at,
            issue_title=f"Issue {task_id}",
            issue_body_path=f"/tmp/{task_id}/issue.md",
            worker_prompt_path=f"/tmp/{task_id}/prompt.txt",
            orx_task_id=orx_task_id,
            launched_at=updated_at,
            launch_attempts=1,
            completed_at=None,
            commit_sha=None,
            commit_message=None,
            last_notified_at=None,
            completion_summary=None,
        )

    def _write_event(self, docs_repo: Path, *, task_id: str, event_type: str, timestamp: str) -> None:
        path = docs_repo / ".openclaw-state" / "watchdog-events.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "timestamp": timestamp,
                        "task_id": task_id,
                        "source_ref": f"docs/prd/{task_id}.md",
                        "source_title": f"Task {task_id}",
                        "target_repo": "repo-a",
                        "target_issue_url": f"https://example.com/issues/{task_id}",
                        "branch_name": f"openclaw/{task_id}/task",
                        "event": event_type,
                        "state": "in_progress",
                        "message": event_type,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

    def _write_runtime_task(
        self,
        runtime_root: Path,
        *,
        task_id: str,
        status: str = "RUNNING",
        retryable: bool = False,
    ) -> None:
        from orx.models import TaskRecord as OrxTaskRecord
        from orx.models import TaskStatus
        from orx.store import TaskStore

        task_dir = runtime_root / "tasks" / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        now = datetime.now(UTC).replace(microsecond=0)
        terminal_statuses = {"SUCCEEDED", "FAILED", "CANCELED", "TIMEOUT"}
        workspace_root = runtime_root.parent.parent
        record = OrxTaskRecord(
            task_id=task_id,
            worker_type="codex",
            status=TaskStatus(status),
            cwd=workspace_root,
            workspace_strategy="git_worktree",
            workspace_path=workspace_root / "worktree",
            branch_name=f"openclaw/{task_id}/task",
            worktree_path=workspace_root / "worktree",
            session_name=f"orx-{task_id}",
            prompt_path=task_dir / "prompt.txt",
            result_path=task_dir / "result.json",
            stdout_log_path=task_dir / "stdout.log",
            stderr_log_path=task_dir / "stderr.log",
            runtime_meta_path=task_dir / "runtime.json",
            timeout_seconds=60,
            exit_code=None,
            failure_reason=None,
            failure_message=None,
            retryable=retryable,
            metadata={"model": "gpt-5-codex"},
            created_at=now,
            started_at=now,
            finished_at=now if status in terminal_statuses else None,
            updated_at=now,
            stdout_offset=0,
            stderr_offset=0,
        )
        store = TaskStore(runtime_root / "tasks.db")
        store.create_task(record)

    def _write_config(self, runtime_root: Path) -> Path:
        config_path = runtime_root.parent.parent / "monitor.json"
        config_path.write_text(
            json.dumps(
                {
                    "projects": [
                        {
                            "id": "alpha",
                            "name": "Alpha",
                            "runtime_root": str(runtime_root),
                        }
                    ],
                    "poll_interval_seconds": 3,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return config_path

    def _write_result(self, runtime_root: Path, *, task_id: str, outcome: str = "success", summary: str = "done") -> None:
        (runtime_root / "tasks" / task_id / "result.json").write_text(
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

    def _append_runtime_output(self, runtime_root: Path, *, task_id: str, stream: str, content: str) -> None:
        path = runtime_root / "tasks" / task_id / f"{stream}.log"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(content)

    def _create_orchestrator_factory(self, launcher: FakeSessionLauncher):
        from orx.orchestrator import Orchestrator

        cache: dict[Path, Orchestrator] = {}

        def factory(runtime_root: Path) -> Orchestrator:
            resolved = runtime_root.resolve()
            orchestrator = cache.get(resolved)
            if orchestrator is None:
                orchestrator = Orchestrator(
                    data_root=resolved,
                    session_launcher=launcher,
                    poll_interval=0.01,
                    stop_grace_seconds=0.0,
                )
                cache[resolved] = orchestrator
            return orchestrator

        return factory

    def _start_server(self, config_path: Path, *, launcher: FakeSessionLauncher | None = None):
        from openclaw_bridge.monitor.config import load_monitor_config
        from openclaw_bridge.monitor.server import create_monitor_server

        config = load_monitor_config(config_path)
        server = create_monitor_server(
            config,
            host="127.0.0.1",
            port=0,
            orchestrator_factory=None if launcher is None else self._create_orchestrator_factory(launcher),
            stream_poll_interval_seconds=0.05,
            stream_idle_interval_seconds=0.05,
            stream_heartbeat_seconds=0.05,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 1)
        return server

    def _open_stream(self, server) -> object:
        request = Request(
            f"{server.url}/api/stream",
            headers={"Accept": "text/event-stream"},
        )
        response = urlopen(request, timeout=1)
        self.addCleanup(response.close)
        return response

    def _read_sse_event(self, response, *, timeout: float = 1.0) -> tuple[str, str | None, dict[str, object] | None]:
        deadline = time.monotonic() + timeout
        lines: list[str] = []
        while time.monotonic() < deadline:
            remaining = max(deadline - time.monotonic(), 0.05)
            response.fp.raw._sock.settimeout(remaining)
            try:
                raw_line = response.readline()
            except TimeoutError as exc:  # pragma: no cover - platform-specific socket timeout subclass
                raise AssertionError("timed out waiting for SSE event") from exc
            if not raw_line:
                raise AssertionError("SSE stream closed unexpectedly")
            line = raw_line.decode("utf-8")
            if line in {"\n", "\r\n"}:
                if not lines:
                    continue
                event_type = "message"
                event_id: str | None = None
                data_lines: list[str] = []
                for entry in lines:
                    if entry.startswith(":"):
                        continue
                    if entry.startswith("event:"):
                        event_type = entry.partition(":")[2].strip() or "message"
                    elif entry.startswith("id:"):
                        event_id = entry.partition(":")[2].strip() or None
                    elif entry.startswith("data:"):
                        data_lines.append(entry.partition(":")[2].lstrip())
                lines = []
                if not data_lines and event_type == "message":
                    continue
                payload = json.loads("\n".join(data_lines)) if data_lines else None
                return event_type, event_id, payload
            lines.append(line.rstrip("\r\n"))
        raise AssertionError("timed out waiting for SSE event")

    def _wait_for_task_update(
        self,
        response,
        *,
        timeout: float = 1.0,
        predicate=None,
    ) -> dict[str, object]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            event_type, _event_id, payload = self._read_sse_event(response, timeout=max(deadline - time.monotonic(), 0.05))
            if event_type != "task_update" or payload is None:
                continue
            if predicate is None or predicate(payload):
                return payload
        raise AssertionError("timed out waiting for task_update")

    def _assert_no_task_update(self, response, *, timeout: float = 0.25) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                event_type, _event_id, _payload = self._read_sse_event(
                    response,
                    timeout=max(deadline - time.monotonic(), 0.05),
                )
            except AssertionError:
                return
            if event_type == "task_update":
                self.fail("unexpected duplicate task_update event")

    def _wait_for(self, predicate, *, timeout: float = 1.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.01)
        self.fail("timed out waiting for condition")

    def test_monitor_server_serves_root_and_dashboard_api(self) -> None:
        from openclaw_bridge.monitor.config import load_monitor_config
        from openclaw_bridge.monitor.server import create_monitor_server

        docs_repo = self._create_docs_repo("alpha")
        runtime_root = self._runtime_root_for_docs_repo(docs_repo)
        now = datetime.now(UTC).replace(microsecond=0).isoformat()
        save_registry(
            docs_repo / ".openclaw-state" / "registry.json",
            [self._make_task(task_id="task-0001", state="dispatched", updated_at=now, orx_task_id="orx-1")],
        )
        self._write_event(docs_repo, task_id="task-0001", event_type="restarted", timestamp=now)
        self._write_runtime_task(runtime_root, task_id="orx-1", status="RUNNING")
        config = load_monitor_config(self._write_config(runtime_root))
        server = create_monitor_server(config, host="127.0.0.1", port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 1)

        with urlopen(f"{server.url}/") as response:
            html = response.read().decode("utf-8")
        with urlopen(f"{server.url}/api/dashboard") as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertIn("Unified Task Monitor", html)
        self.assertIn("Add Project", html)
        self.assertIn("Orx Runtime Root", html)
        self.assertIn('Intl.DateTimeFormat(undefined', html)
        self.assertIn('resolvedOptions().timeZone', html)
        self.assertIn('class="project-card', html)
        self.assertIn('class="task-list"', html)
        self.assertIn('aria-expanded="${String(!isCollapsed)}"', html)
        self.assertEqual(payload["projects"][0]["id"], "alpha")
        self.assertEqual(payload["projects"][0]["runtime_root"], str(runtime_root.resolve()))
        self.assertEqual(payload["summary"]["running_count"], 1)

    def test_monitor_server_serves_task_detail_and_404_for_unknown_task(self) -> None:
        from openclaw_bridge.monitor.config import load_monitor_config
        from openclaw_bridge.monitor.server import create_monitor_server

        docs_repo = self._create_docs_repo("alpha")
        runtime_root = self._runtime_root_for_docs_repo(docs_repo)
        now = datetime.now(UTC).replace(microsecond=0).isoformat()
        save_registry(
            docs_repo / ".openclaw-state" / "registry.json",
            [self._make_task(task_id="task-0001", state="dispatched", updated_at=now, orx_task_id="orx-1")],
        )
        self._write_event(docs_repo, task_id="task-0001", event_type="restarted", timestamp=now)
        self._write_runtime_task(runtime_root, task_id="orx-1", status="RUNNING")
        config = load_monitor_config(self._write_config(runtime_root))
        server = create_monitor_server(config, host="127.0.0.1", port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 1)

        with urlopen(f"{server.url}/api/tasks/alpha/task-0001") as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(payload["task"]["task_id"], "task-0001")
        self.assertEqual(payload["task"]["task_source"], "hybrid")
        self.assertEqual(payload["timeline"][0]["event"], "restarted")

        with self.assertRaises(HTTPError) as exc:
            urlopen(f"{server.url}/api/tasks/alpha/task-9999")
        self.assertEqual(exc.exception.code, 404)
        error_payload = json.loads(exc.exception.read().decode("utf-8"))
        self.assertEqual(error_payload["error"], "not_found")

    def test_monitor_server_can_add_project_via_runtime_root_post(self) -> None:
        from openclaw_bridge.monitor.config import load_monitor_config
        from openclaw_bridge.monitor.server import create_monitor_server

        alpha_runtime_root = self._create_runtime_root("alpha")
        beta_runtime_root = self._create_runtime_root("beta")
        config = load_monitor_config(self._write_config(alpha_runtime_root))
        server = create_monitor_server(config, host="127.0.0.1", port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 1)

        request = Request(
            f"{server.url}/api/projects",
            data=json.dumps(
                {
                    "runtime_root": str(beta_runtime_root),
                    "name": "Beta Runtime",
                },
                ensure_ascii=False,
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request) as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(payload["project"]["id"], "beta")
        self.assertEqual(payload["project"]["name"], "Beta Runtime")
        self.assertEqual(payload["project"]["runtime_root"], str(beta_runtime_root.resolve()))

        with urlopen(f"{server.url}/api/dashboard") as response:
            dashboard = json.loads(response.read().decode("utf-8"))
        self.assertEqual([project["id"] for project in dashboard["projects"]], ["alpha", "beta"])

    def test_monitor_server_accepts_legacy_docs_repo_project_post(self) -> None:
        from openclaw_bridge.monitor.config import load_monitor_config
        from openclaw_bridge.monitor.server import create_monitor_server

        alpha_runtime_root = self._create_runtime_root("alpha")
        beta_docs_repo = self._create_docs_repo("beta")
        config = load_monitor_config(self._write_config(alpha_runtime_root))
        server = create_monitor_server(config, host="127.0.0.1", port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 1)

        request = Request(
            f"{server.url}/api/projects",
            data=json.dumps(
                {
                    "docs_repo": str(beta_docs_repo),
                    "name": "Beta Docs",
                },
                ensure_ascii=False,
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request) as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(payload["project"]["id"], "beta")
        self.assertEqual(
            payload["project"]["runtime_root"],
            str((beta_docs_repo / ".openclaw-state" / "orx").resolve()),
        )

    def test_monitor_server_rejects_invalid_project_post(self) -> None:
        from openclaw_bridge.monitor.config import load_monitor_config
        from openclaw_bridge.monitor.server import create_monitor_server

        alpha_runtime_root = self._create_runtime_root("alpha")
        config = load_monitor_config(self._write_config(alpha_runtime_root))
        server = create_monitor_server(config, host="127.0.0.1", port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 1)

        invalid_root = alpha_runtime_root.parent.parent / "runtime-root.txt"
        invalid_root.write_text("not a directory", encoding="utf-8")

        request = Request(
            f"{server.url}/api/projects",
            data=json.dumps(
                {
                    "runtime_root": str(invalid_root),
                },
                ensure_ascii=False,
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(HTTPError) as exc:
            urlopen(request)
        self.assertEqual(exc.exception.code, 400)
        payload = json.loads(exc.exception.read().decode("utf-8"))
        self.assertEqual(payload["error"], "invalid_request")

    def test_monitor_server_stream_endpoint_returns_event_stream_content_type(self) -> None:
        runtime_root = self._create_runtime_root("alpha")
        server = self._start_server(self._write_config(runtime_root))

        response = self._open_stream(server)

        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers.get_content_type(), "text/event-stream")

    def test_monitor_stream_emits_stdout_and_stderr_deltas_for_active_task(self) -> None:
        from openclaw_bridge.monitor.config import load_monitor_config

        docs_repo = self._create_docs_repo("alpha")
        runtime_root = self._runtime_root_for_docs_repo(docs_repo)
        now = datetime.now(UTC).replace(microsecond=0).isoformat()
        save_registry(
            docs_repo / ".openclaw-state" / "registry.json",
            [self._make_task(task_id="task-0001", state="dispatched", updated_at=now, orx_task_id="orx-1")],
        )
        self._write_runtime_task(runtime_root, task_id="orx-1", status="RUNNING")
        launcher = FakeSessionLauncher()
        launcher.sessions["orx-orx-1"] = True
        server = self._start_server(self._write_config(runtime_root), launcher=launcher)
        response = self._open_stream(server)

        self._append_runtime_output(runtime_root, task_id="orx-1", stream="stdout", content="hello stdout\n")
        self._append_runtime_output(runtime_root, task_id="orx-1", stream="stderr", content="boom stderr\n")

        payload = self._wait_for_task_update(
            response,
            predicate=lambda item: bool(item["stdout_chunks"]) and bool(item["stderr_chunks"]),
        )

        self.assertEqual(payload["project_id"], "alpha")
        self.assertEqual(payload["task_id"], "task-0001")
        self.assertEqual(payload["orx_task_id"], "orx-1")
        self.assertEqual(payload["state"], "in_progress")
        self.assertEqual(payload["runtime_status"], "RUNNING")
        self.assertEqual([chunk["content"] for chunk in payload["stdout_chunks"]], ["hello stdout\n"])
        self.assertEqual([chunk["content"] for chunk in payload["stderr_chunks"]], ["boom stderr\n"])

    def test_monitor_stream_emits_terminal_state_when_task_completes(self) -> None:
        docs_repo = self._create_docs_repo("alpha")
        runtime_root = self._runtime_root_for_docs_repo(docs_repo)
        now = datetime.now(UTC).replace(microsecond=0).isoformat()
        save_registry(
            docs_repo / ".openclaw-state" / "registry.json",
            [self._make_task(task_id="task-0001", state="dispatched", updated_at=now, orx_task_id="orx-1")],
        )
        self._write_runtime_task(runtime_root, task_id="orx-1", status="RUNNING")
        launcher = FakeSessionLauncher()
        launcher.sessions["orx-orx-1"] = True
        server = self._start_server(self._write_config(runtime_root), launcher=launcher)
        response = self._open_stream(server)

        self._write_result(runtime_root, task_id="orx-1", summary="implemented")
        launcher.sessions["orx-orx-1"] = False

        payload = self._wait_for_task_update(
            response,
            predicate=lambda item: item["state"] == "completed",
        )

        self.assertEqual(payload["runtime_status"], "SUCCEEDED")
        self.assertEqual(payload["state"], "completed")
        self.assertEqual(payload["last_error"], None)
        self.assertTrue(payload["completed_at"])

    def test_monitor_stream_does_not_repeat_same_log_chunks_when_no_new_output_arrives(self) -> None:
        docs_repo = self._create_docs_repo("alpha")
        runtime_root = self._runtime_root_for_docs_repo(docs_repo)
        now = datetime.now(UTC).replace(microsecond=0).isoformat()
        save_registry(
            docs_repo / ".openclaw-state" / "registry.json",
            [self._make_task(task_id="task-0001", state="dispatched", updated_at=now, orx_task_id="orx-1")],
        )
        self._write_runtime_task(runtime_root, task_id="orx-1", status="RUNNING")
        launcher = FakeSessionLauncher()
        launcher.sessions["orx-orx-1"] = True
        server = self._start_server(self._write_config(runtime_root), launcher=launcher)
        response = self._open_stream(server)

        self._append_runtime_output(runtime_root, task_id="orx-1", stream="stdout", content="one line\n")

        payload = self._wait_for_task_update(
            response,
            predicate=lambda item: bool(item["stdout_chunks"]),
        )
        self.assertEqual([chunk["content"] for chunk in payload["stdout_chunks"]], ["one line\n"])

        self._assert_no_task_update(response)

    def test_monitor_stream_cleans_up_subscription_after_client_disconnect(self) -> None:
        runtime_root = self._create_runtime_root("alpha")
        server = self._start_server(self._write_config(runtime_root))
        response = self._open_stream(server)

        self._wait_for(lambda: server.stream_controller.subscriber_count() == 1)
        response.close()
        self._wait_for(lambda: server.stream_controller.subscriber_count() == 0)

    def test_monitor_stream_skips_bad_runtime_roots_without_breaking_other_projects(self) -> None:
        docs_repo = self._create_docs_repo("alpha")
        runtime_root = self._runtime_root_for_docs_repo(docs_repo)
        missing_runtime_root = Path(tempfile.mkdtemp()) / "missing" / ".openclaw-state" / "orx"
        self.addCleanup(lambda: shutil.rmtree(missing_runtime_root.parents[2], ignore_errors=True))
        now = datetime.now(UTC).replace(microsecond=0).isoformat()
        save_registry(
            docs_repo / ".openclaw-state" / "registry.json",
            [self._make_task(task_id="task-0001", state="dispatched", updated_at=now, orx_task_id="orx-1")],
        )
        self._write_runtime_task(runtime_root, task_id="orx-1", status="RUNNING")
        config_path = runtime_root.parent.parent / "monitor.json"
        config_path.write_text(
            json.dumps(
                {
                    "projects": [
                        {
                            "id": "broken",
                            "name": "Broken",
                            "runtime_root": str(missing_runtime_root),
                        },
                        {
                            "id": "alpha",
                            "name": "Alpha",
                            "runtime_root": str(runtime_root),
                        },
                    ],
                    "poll_interval_seconds": 3,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        launcher = FakeSessionLauncher()
        launcher.sessions["orx-orx-1"] = True
        server = self._start_server(config_path, launcher=launcher)
        response = self._open_stream(server)

        self._append_runtime_output(runtime_root, task_id="orx-1", stream="stdout", content="still works\n")

        payload = self._wait_for_task_update(
            response,
            predicate=lambda item: bool(item["stdout_chunks"]),
        )

        self.assertEqual(payload["project_id"], "alpha")
        self.assertEqual([chunk["content"] for chunk in payload["stdout_chunks"]], ["still works\n"])

    def test_monitor_cli_invokes_server_with_host_port_and_config(self) -> None:
        from openclaw_bridge.cli import monitor_command

        config_path = Path("/tmp/monitor-config.json")
        args = SimpleNamespace(
            config=config_path,
            host="127.0.0.1",
            port=9001,
        )

        with patch("openclaw_bridge.cli.ensure_monitor_config_path", return_value=(config_path.resolve(), False)):
            with patch("openclaw_bridge.cli.run_monitor_server") as mocked_run:
                monitor_command(args)

        mocked_run.assert_called_once_with(
            config_path=config_path.resolve(),
            host="127.0.0.1",
            port=9001,
        )

    def test_monitor_cli_bootstraps_default_config_when_missing(self) -> None:
        from openclaw_bridge.cli import monitor_command

        config_root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(config_root, ignore_errors=True))
        config_path = config_root / "config.json"
        args = SimpleNamespace(
            config=None,
            host="127.0.0.1",
            port=9001,
        )

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            with patch("openclaw_bridge.cli.ensure_monitor_config_path", return_value=(config_path, True)):
                with patch("openclaw_bridge.cli.run_monitor_server") as mocked_run:
                    monitor_command(args)

        output = stdout.getvalue()
        self.assertIn("created_monitor_config=", output)
        self.assertIn("starter config created", output)
        mocked_run.assert_called_once_with(
            config_path=config_path,
            host="127.0.0.1",
            port=9001,
        )


if __name__ == "__main__":
    unittest.main()
