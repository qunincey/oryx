from __future__ import annotations

import io
import json
import tempfile
import unittest
from urllib.error import URLError
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from orx.models import TaskArtifact, TaskFailureReason, TaskResult, TaskStatus
from openclaw_bridge.cli import (
    _dispatch_issue,
    dispatch_specs,
    launch_workers,
    _load_config,
    reconcile_workers,
    watch_workers,
)
from openclaw_bridge.models import TaskRecord
from openclaw_bridge.scaffold import init_docs_repo


class FakeOrchestrator:
    init_data_roots: list[Path] = []
    started_requests: list[object] = []
    task_by_id: dict[str, object] = {}
    result_by_id: dict[str, object] = {}
    start_task_ids: list[str] = ["orx-task-1"]

    def __init__(self, *, data_root: Path | None = None, **_: object) -> None:
        self.data_root = Path(data_root) if data_root is not None else None
        type(self).init_data_roots.append(self.data_root)

    @classmethod
    def reset(cls) -> None:
        cls.init_data_roots = []
        cls.started_requests = []
        cls.task_by_id = {}
        cls.result_by_id = {}
        cls.start_task_ids = ["orx-task-1"]

    def start_task(self, request) -> SimpleNamespace:
        type(self).started_requests.append(request)
        task_id = type(self).start_task_ids.pop(0) if type(self).start_task_ids else "orx-task-1"
        return SimpleNamespace(task_id=task_id)

    def get_task(self, task_id: str):
        return type(self).task_by_id[task_id]

    def get_result(self, task_id: str):
        return type(self).result_by_id.get(task_id)


class DispatchSpecsTests(unittest.TestCase):
    def _create_docs_repo(self) -> Path:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        docs_repo = Path(temp_dir.name) / "docs-repo"
        init_docs_repo(
            docs_repo,
            project_name="Example Project",
            project_description="Example description",
        )
        return docs_repo

    def _write_spec(self, path: Path, title: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"""---
title: {title}
status: approved
affected_repos: [repo-a]
references: [docs/project/project-overview.md]
---
# {title}

## Goal
Ship the feature.

## Agreed Scope
- Build the backend path for this feature.

## Impacted Modules
- payments-domain -> repo-a (owner)

## Repo-specific Scope
### repo-a
- Summary: repo-a owns the backend implementation.
- Modules: payments-domain, payment-api
- Change: Add the application service for the new workflow.
- Change: Persist the workflow state transition.
- Boundary: Do not implement frontend screens in this repo.
- Acceptance: The backend exposes the new workflow behavior.
- Risk: Existing migrations may need adjustment.

## Acceptance Criteria
- Create one task artifact.
""",
            encoding="utf-8",
        )

    def _configure_local_repo(self, docs_repo: Path) -> Path:
        local_repo = docs_repo.parent / "repo-a"
        local_repo.mkdir(parents=True, exist_ok=True)
        (local_repo / ".git").mkdir(exist_ok=True)

        config_path = docs_repo / ".openclaw" / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["repo_mappings"]["repo-a"]["local_path"] = str(local_repo)
        config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        return local_repo

    def _configure_notifications(
        self,
        docs_repo: Path,
        **overrides: object,
    ) -> dict[str, object]:
        config_path = docs_repo / ".openclaw" / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        notifications = {
            "webhook_url": "https://notify.example.com/openclaw",
            "events": ["completed", "blocked", "restartable", "restarted"],
            "timeout_seconds": 5,
            "headers": {"X-OpenClaw-Source": "watchdog"},
            "bearer_token_env": "",
        }
        notifications.update(overrides)
        config["notifications"] = notifications
        config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        return notifications

    def _dispatch_single_task(self, docs_repo: Path, *, title: str = "Single Spec") -> Path:
        spec_path = docs_repo / "docs" / "prd" / "single-spec.md"
        self._write_spec(spec_path, title)
        args = SimpleNamespace(
            docs_repo=spec_path,
            config=None,
            create_issues=True,
            dry_run=True,
            launch_workers=False,
        )
        with redirect_stdout(io.StringIO()):
            dispatch_specs(args)
        return spec_path

    def _set_registry_task(self, docs_repo: Path, **updates: object) -> dict[str, object]:
        registry_path = docs_repo / ".openclaw-state" / "registry.json"
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        registry["tasks"][0].update(updates)
        registry_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")
        return registry["tasks"][0]

    def _write_worker_result(self, docs_repo: Path, payload: dict[str, object]) -> Path:
        result_path = (
            docs_repo
            / ".openclaw-state"
            / "artifacts"
            / "task-0001"
            / "result.json"
        )
        result_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return result_path

    def _make_orx_task(
        self,
        *,
        task_id: str,
        status: TaskStatus,
        workspace_path: Path,
        branch_name: str,
        worktree_path: str,
        retryable: bool = False,
        failure_reason: TaskFailureReason | None = None,
        failure_message: str | None = None,
    ) -> SimpleNamespace:
        task_dir = docs_repo_orx_task_dir = workspace_path.parent / ".orx-task-placeholder"
        return SimpleNamespace(
            task_id=task_id,
            worker_type="codex",
            status=status,
            cwd=workspace_path.parent,
            workspace_strategy="git_worktree",
            workspace_path=workspace_path,
            branch_name=branch_name,
            worktree_path=Path(worktree_path),
            session_name=f"orx-{task_id}",
            prompt_path=task_dir / "prompt.txt",
            result_path=task_dir / "result.json",
            stdout_log_path=task_dir / "stdout.log",
            stderr_log_path=task_dir / "stderr.log",
            runtime_meta_path=task_dir / "runtime.json",
            timeout_seconds=60,
            exit_code=None,
            failure_reason=failure_reason,
            failure_message=failure_message,
            retryable=retryable,
            metadata={"model": "gpt-5-codex"},
            created_at=None,
            started_at=None,
            finished_at=None,
            updated_at=None,
            stdout_offset=0,
            stderr_offset=0,
        )

    class _FakeUrlopenResponse:
        def __init__(self, *, payload: dict[str, object] | None = None) -> None:
            self._payload = payload or {"ok": True}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self) -> bytes:
            return json.dumps(self._payload).encode("utf-8")

    def test_dispatch_specs_accepts_single_spec_file_path(self) -> None:
        docs_repo = self._create_docs_repo()
        spec_path = docs_repo / "docs" / "prd" / "single-spec.md"
        self._write_spec(spec_path, "Single Spec")

        args = SimpleNamespace(
            docs_repo=spec_path,
            config=None,
            create_issues=False,
            dry_run=False,
            launch_workers=False,
        )

        with redirect_stdout(io.StringIO()):
            dispatch_specs(args)

        registry_path = docs_repo / ".openclaw-state" / "registry.json"
        registry = json.loads(registry_path.read_text(encoding="utf-8"))["tasks"]
        self.assertEqual([task["source_ref"] for task in registry], ["docs/prd/single-spec.md"])

        issue_body_path = (
            docs_repo
            / ".openclaw-state"
            / "artifacts"
            / "task-0001"
            / "implementation-issue.md"
        )
        self.assertTrue(issue_body_path.exists())
        issue_body = issue_body_path.read_text(encoding="utf-8")
        self.assertIn("## Repo-specific Scope", issue_body)
        self.assertIn("repo-a owns the backend implementation.", issue_body)
        self.assertIn("Persist the workflow state transition.", issue_body)
        self.assertIn("Do not implement frontend screens in this repo.", issue_body)

    def test_dispatch_specs_accepts_single_spec_directory_path(self) -> None:
        docs_repo = self._create_docs_repo()
        selected_dir = docs_repo / "docs" / "prd" / "selected"
        self._write_spec(selected_dir / "in-scope.md", "In Scope")
        self._write_spec(docs_repo / "docs" / "prd" / "out-of-scope.md", "Out Of Scope")

        args = SimpleNamespace(
            docs_repo=selected_dir,
            config=None,
            create_issues=False,
            dry_run=False,
            launch_workers=False,
        )

        with redirect_stdout(io.StringIO()):
            dispatch_specs(args)

        registry_path = docs_repo / ".openclaw-state" / "registry.json"
        registry = json.loads(registry_path.read_text(encoding="utf-8"))["tasks"]
        self.assertEqual([task["source_ref"] for task in registry], ["docs/prd/selected/in-scope.md"])

    def test_dispatch_specs_rejects_docs_repo_root_path(self) -> None:
        docs_repo = self._create_docs_repo()
        args = SimpleNamespace(
            docs_repo=docs_repo,
            config=None,
            create_issues=False,
            dry_run=False,
            launch_workers=False,
        )

        with self.assertRaisesRegex(ValueError, "docs repo root"):
            dispatch_specs(args)

    def test_dispatch_specs_rejects_specs_root_path(self) -> None:
        docs_repo = self._create_docs_repo()
        args = SimpleNamespace(
            docs_repo=docs_repo / "docs" / "prd",
            config=None,
            create_issues=False,
            dry_run=False,
            launch_workers=False,
        )

        with self.assertRaisesRegex(ValueError, "specs root"):
            dispatch_specs(args)

    def test_dispatch_specs_dry_run_supports_gitlab_repo_mapping(self) -> None:
        docs_repo = self._create_docs_repo()
        spec_path = docs_repo / "docs" / "prd" / "single-spec.md"
        self._write_spec(spec_path, "Single Spec")

        config_path = docs_repo / ".openclaw" / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["repo_mappings"]["repo-a"] = {
            "gitlab_host": "gitlab.example.com",
            "gitlab_repo": "group/subgroup/repo-a",
            "labels": ["backend"],
        }
        config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

        args = SimpleNamespace(
            docs_repo=spec_path,
            config=None,
            create_issues=True,
            dry_run=True,
            launch_workers=False,
        )

        with redirect_stdout(io.StringIO()):
            dispatch_specs(args)

        registry_path = docs_repo / ".openclaw-state" / "registry.json"
        task = json.loads(registry_path.read_text(encoding="utf-8"))["tasks"][0]
        self.assertEqual(task["state"], "dispatched")
        self.assertEqual(
            task["target_issue_url"],
            "https://gitlab.example.com/group/subgroup/repo-a/-/issues/DRY-RUN-task-0001",
        )

    def test_dispatch_issue_creates_github_issue_via_gh(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            issue_body_path = temp_path / "issue.md"
            issue_body_path.write_text("Issue body", encoding="utf-8")

            task = TaskRecord(
                task_id="task-0001",
                source_repo="docs-repo",
                source_ref="docs/prd/spec.md",
                source_title="Spec",
                target_repo="repo-a",
                target_issue_number=None,
                target_issue_url=None,
                state="decomposed",
                worker_type="codex",
                worker_model="gpt-5-codex",
                worktree_path="../worktrees/repo-a/task-0001-spec",
                tmux_session="swarm-task-0001",
                branch_name="openclaw/task-0001/spec",
                pr_url=None,
                last_error=None,
                created_at="2026-04-12T00:00:00+00:00",
                updated_at="2026-04-12T00:00:00+00:00",
                issue_title="[OpenClaw] Spec",
                issue_body_path=str(issue_body_path),
                worker_prompt_path=str(temp_path / "worker.txt"),
                launched_at=None,
                launch_attempts=0,
            )
            config = {
                "default_labels": ["ai-task"],
                "repo_mappings": {
                    "repo-a": {
                        "github_repo": "org/repo-a",
                        "labels": ["backend"],
                    }
                },
            }

            with patch("openclaw_bridge.cli.subprocess.run") as mocked_run:
                mocked_run.side_effect = [
                    SimpleNamespace(returncode=0, stdout="", stderr=""),
                    SimpleNamespace(
                        returncode=0,
                        stdout="https://github.com/org/repo-a/issues/42\n",
                        stderr="",
                    ),
                ]
                blocked: list[str] = []
                dispatched = _dispatch_issue(
                    task=task,
                    config=config,
                    docs_repo=temp_path,
                    dry_run=False,
                    blocked=blocked,
                )

            self.assertEqual(dispatched, 1)
            self.assertEqual(task.target_issue_number, "42")
            self.assertEqual(task.target_issue_url, "https://github.com/org/repo-a/issues/42")
            self.assertEqual(blocked, [])
            self.assertEqual(
                mocked_run.call_args_list[1].args[0],
                [
                    "gh",
                    "issue",
                    "create",
                    "--repo",
                    "org/repo-a",
                    "--title",
                    "[OpenClaw] Spec",
                    "--body-file",
                    str(issue_body_path.resolve()),
                    "--label",
                    "ai-task",
                    "--label",
                    "backend",
                ],
            )

    def test_dispatch_specs_backfills_missing_prompt_templates(self) -> None:
        docs_repo = self._create_docs_repo()
        spec_path = docs_repo / "docs" / "prd" / "single-spec.md"
        self._write_spec(spec_path, "Single Spec")

        issue_template_path = docs_repo / ".openclaw" / "prompts" / "implementation-issue.md.tmpl"
        worker_template_path = docs_repo / ".openclaw" / "prompts" / "worker-task.txt.tmpl"
        issue_template_path.unlink()
        worker_template_path.unlink()

        args = SimpleNamespace(
            docs_repo=spec_path,
            config=None,
            create_issues=False,
            dry_run=False,
            launch_workers=False,
        )

        with redirect_stdout(io.StringIO()):
            dispatch_specs(args)

        self.assertTrue(issue_template_path.exists())
        self.assertTrue(worker_template_path.exists())
        self.assertIn("## Background", issue_template_path.read_text(encoding="utf-8"))
        worker_template = worker_template_path.read_text(encoding="utf-8")
        self.assertIn("You are the coding worker", worker_template)
        self.assertIn("append the exact completion contract", worker_template)
        self.assertIn('"outcome": "success"', worker_template)
        self.assertIn("metadata.commit_message", worker_template)
        self.assertIn("merge_request_url", worker_template)
        self.assertIn("pull_request_url", worker_template)

    def test_load_config_normalizes_legacy_gpt_5_codex_worker_model(self) -> None:
        docs_repo = self._create_docs_repo()
        config_path = docs_repo / ".openclaw" / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["worker_model"] = "gpt-5-codex"
        config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

        loaded = _load_config(config_path)

        self.assertEqual(loaded["worker_model"], "default")

    def test_launch_workers_normalizes_legacy_gpt_5_codex_model_for_existing_task(self) -> None:
        docs_repo = self._create_docs_repo()
        self._dispatch_single_task(docs_repo)
        local_repo = self._configure_local_repo(docs_repo)
        self._set_registry_task(docs_repo, state="dispatched", worker_model="gpt-5-codex")

        FakeOrchestrator.reset()
        FakeOrchestrator.task_by_id["orx-task-1"] = self._make_orx_task(
            task_id="orx-task-1",
            status=TaskStatus.RUNNING,
            workspace_path=(local_repo / "../worktrees/repo-a/task-0001-single-spec").resolve(),
            branch_name="openclaw/task-0001/single-spec",
            worktree_path="../worktrees/repo-a/task-0001-single-spec",
        )

        launch_args = SimpleNamespace(
            docs_repo=docs_repo,
            config=None,
            task_id="task-0001",
            state=None,
        )

        with patch("openclaw_bridge.cli.Orchestrator", FakeOrchestrator):
            with redirect_stdout(io.StringIO()):
                launch_workers(launch_args)

        registry = json.loads((docs_repo / ".openclaw-state" / "registry.json").read_text(encoding="utf-8"))["tasks"][0]
        request = FakeOrchestrator.started_requests[-1]
        self.assertIsNone(request.model)
        self.assertEqual(request.metadata["model"], "default")
        self.assertEqual(registry["worker_model"], "default")

    def test_dispatch_specs_reports_legacy_string_repo_mapping_clearly(self) -> None:
        docs_repo = self._create_docs_repo()
        spec_path = docs_repo / "docs" / "prd" / "single-spec.md"
        self._write_spec(spec_path, "Single Spec")

        config_path = docs_repo / ".openclaw" / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["repo_mappings"]["repo-a"] = "/tmp/repo-a"
        config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

        args = SimpleNamespace(
            docs_repo=spec_path,
            config=None,
            create_issues=True,
            dry_run=False,
            launch_workers=False,
        )

        with redirect_stdout(io.StringIO()):
            dispatch_specs(args)

        registry_path = docs_repo / ".openclaw-state" / "registry.json"
        task = json.loads(registry_path.read_text(encoding="utf-8"))["tasks"][0]
        self.assertEqual(
            task["last_error"],
            "repo mapping for target repo repo-a must be an object with github_repo, gitlab_repo, or repo+issue_provider",
        )

    def test_dispatch_issue_creates_gitlab_issue_via_glab(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            issue_body_path = temp_path / "issue.md"
            issue_body_path.write_text("Issue body", encoding="utf-8")

            task = TaskRecord(
                task_id="task-0001",
                source_repo="docs-repo",
                source_ref="docs/prd/spec.md",
                source_title="Spec",
                target_repo="repo-a",
                target_issue_number=None,
                target_issue_url=None,
                state="decomposed",
                worker_type="codex",
                worker_model="gpt-5-codex",
                worktree_path="../worktrees/repo-a/task-0001-spec",
                tmux_session="swarm-task-0001",
                branch_name="openclaw/task-0001/spec",
                pr_url=None,
                last_error=None,
                created_at="2026-04-12T00:00:00+00:00",
                updated_at="2026-04-12T00:00:00+00:00",
                issue_title="[OpenClaw] Spec",
                issue_body_path=str(issue_body_path),
                worker_prompt_path=str(temp_path / "worker.txt"),
                launched_at=None,
                launch_attempts=0,
            )
            config = {
                "default_labels": ["ai-task"],
                "repo_mappings": {
                    "repo-a": {
                        "gitlab_host": "gitlab.example.com",
                        "gitlab_repo": "group/repo-a",
                        "labels": ["backend"],
                    }
                },
            }

            with patch("openclaw_bridge.cli.subprocess.run") as mocked_run:
                mocked_run.side_effect = [
                    SimpleNamespace(returncode=0, stdout="", stderr=""),
                    SimpleNamespace(
                        returncode=0,
                        stdout="https://gitlab.example.com/group/repo-a/-/issues/14\n",
                        stderr="",
                    ),
                ]
                blocked: list[str] = []
                dispatched = _dispatch_issue(
                    task=task,
                    config=config,
                    docs_repo=temp_path,
                    dry_run=False,
                    blocked=blocked,
                )

            self.assertEqual(dispatched, 1)
            self.assertEqual(task.target_issue_number, "14")
            self.assertEqual(
                task.target_issue_url,
                "https://gitlab.example.com/group/repo-a/-/issues/14",
            )
            self.assertEqual(blocked, [])
            self.assertEqual(
                mocked_run.call_args_list[1].args[0],
                [
                    "glab",
                    "issue",
                    "create",
                    "--repo",
                    "https://gitlab.example.com/group/repo-a",
                    "--title",
                    "[OpenClaw] Spec",
                    "--description",
                    "Issue body",
                    "--yes",
                    "--label",
                    "ai-task,backend",
                ],
            )

    def test_dispatch_issue_supports_explicit_http_gitlab_host(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            issue_body_path = temp_path / "issue.md"
            issue_body_path.write_text("Issue body", encoding="utf-8")

            task = TaskRecord(
                task_id="task-0001",
                source_repo="docs-repo",
                source_ref="docs/prd/spec.md",
                source_title="Spec",
                target_repo="repo-a",
                target_issue_number=None,
                target_issue_url=None,
                state="decomposed",
                worker_type="codex",
                worker_model="gpt-5-codex",
                worktree_path="../worktrees/repo-a/task-0001-spec",
                tmux_session="swarm-task-0001",
                branch_name="openclaw/task-0001/spec",
                pr_url=None,
                last_error=None,
                created_at="2026-04-12T00:00:00+00:00",
                updated_at="2026-04-12T00:00:00+00:00",
                issue_title="[OpenClaw] Spec",
                issue_body_path=str(issue_body_path),
                worker_prompt_path=str(temp_path / "worker.txt"),
                launched_at=None,
                launch_attempts=0,
            )
            config = {
                "default_labels": ["ai-task"],
                "repo_mappings": {
                    "repo-a": {
                        "gitlab_host": "http://gitlab.example.com",
                        "gitlab_repo": "group/repo-a",
                    }
                },
            }

            with patch("openclaw_bridge.cli.subprocess.run") as mocked_run:
                mocked_run.side_effect = [
                    SimpleNamespace(returncode=0, stdout="", stderr=""),
                    SimpleNamespace(
                        returncode=0,
                        stdout="http://gitlab.example.com/group/repo-a/-/issues/15\n",
                        stderr="",
                    ),
                ]
                blocked: list[str] = []
                dispatched = _dispatch_issue(
                    task=task,
                    config=config,
                    docs_repo=temp_path,
                    dry_run=False,
                    blocked=blocked,
                )

            self.assertEqual(dispatched, 1)
            self.assertEqual(task.target_issue_number, "15")
            self.assertEqual(
                task.target_issue_url,
                "http://gitlab.example.com/group/repo-a/-/issues/15",
            )
            self.assertEqual(blocked, [])
            self.assertEqual(
                mocked_run.call_args_list[1].args[0],
                [
                    "glab",
                    "issue",
                    "create",
                    "--repo",
                    "http://gitlab.example.com/group/repo-a",
                    "--title",
                    "[OpenClaw] Spec",
                    "--description",
                    "Issue body",
                    "--yes",
                    "--label",
                    "ai-task",
                ],
            )

    def test_dispatch_specs_can_launch_workers_after_dispatch(self) -> None:
        docs_repo = self._create_docs_repo()
        spec_path = docs_repo / "docs" / "prd" / "single-spec.md"
        self._write_spec(spec_path, "Single Spec")

        local_repo = docs_repo.parent / "repo-a"
        local_repo.mkdir(parents=True)
        (local_repo / ".git").mkdir()

        config_path = docs_repo / ".openclaw" / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["repo_mappings"]["repo-a"]["local_path"] = str(local_repo)
        config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

        args = SimpleNamespace(
            docs_repo=spec_path,
            config=None,
            create_issues=True,
            dry_run=True,
            launch_workers=True,
        )

        FakeOrchestrator.reset()
        FakeOrchestrator.task_by_id["orx-task-1"] = self._make_orx_task(
            task_id="orx-task-1",
            status=TaskStatus.RUNNING,
            workspace_path=(local_repo / "../worktrees/repo-a/task-0001-single-spec").resolve(),
            branch_name="openclaw/task-0001/single-spec",
            worktree_path="../worktrees/repo-a/task-0001-single-spec",
        )

        with patch("openclaw_bridge.cli.Orchestrator", FakeOrchestrator):
            with redirect_stdout(io.StringIO()):
                dispatch_specs(args)

        registry_path = docs_repo / ".openclaw-state" / "registry.json"
        task = json.loads(registry_path.read_text(encoding="utf-8"))["tasks"][0]
        self.assertEqual(task["state"], "in_progress")
        self.assertEqual(task["launch_attempts"], 1)
        self.assertIsNotNone(task["launched_at"])
        self.assertEqual(task["orx_task_id"], "orx-task-1")
        self.assertEqual(task["tmux_session"], "orx-orx-task-1")
        self.assertEqual(FakeOrchestrator.started_requests[-1].cwd, local_repo.resolve())

    def test_launch_workers_command_launches_existing_dispatched_task(self) -> None:
        docs_repo = self._create_docs_repo()
        spec_path = docs_repo / "docs" / "prd" / "single-spec.md"
        self._write_spec(spec_path, "Single Spec")

        args = SimpleNamespace(
            docs_repo=spec_path,
            config=None,
            create_issues=True,
            dry_run=True,
            launch_workers=False,
        )
        with redirect_stdout(io.StringIO()):
            dispatch_specs(args)

        local_repo = docs_repo.parent / "repo-a"
        local_repo.mkdir(parents=True)
        (local_repo / ".git").mkdir()

        config_path = docs_repo / ".openclaw" / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["repo_mappings"]["repo-a"]["local_path"] = str(local_repo)
        config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

        launch_args = SimpleNamespace(
            docs_repo=docs_repo,
            config=None,
            task_id="task-0001",
            state=None,
        )

        FakeOrchestrator.reset()
        FakeOrchestrator.task_by_id["orx-task-1"] = self._make_orx_task(
            task_id="orx-task-1",
            status=TaskStatus.RUNNING,
            workspace_path=(local_repo / "../worktrees/repo-a/task-0001-single-spec").resolve(),
            branch_name="openclaw/task-0001/single-spec",
            worktree_path="../worktrees/repo-a/task-0001-single-spec",
        )

        with patch("openclaw_bridge.cli.Orchestrator", FakeOrchestrator):
            with redirect_stdout(io.StringIO()):
                launch_workers(launch_args)

        registry_path = docs_repo / ".openclaw-state" / "registry.json"
        task = json.loads(registry_path.read_text(encoding="utf-8"))["tasks"][0]
        self.assertEqual(task["state"], "in_progress")
        self.assertEqual(task["launch_attempts"], 1)
        self.assertEqual(task["orx_task_id"], "orx-task-1")
        self.assertEqual(task["tmux_session"], "orx-orx-task-1")
        self.assertEqual(FakeOrchestrator.started_requests[-1].cwd, local_repo.resolve())

    def test_launch_workers_requeues_missing_in_progress_session(self) -> None:
        docs_repo = self._create_docs_repo()
        spec_path = docs_repo / "docs" / "prd" / "single-spec.md"
        self._write_spec(spec_path, "Single Spec")

        args = SimpleNamespace(
            docs_repo=spec_path,
            config=None,
            create_issues=True,
            dry_run=True,
            launch_workers=False,
        )
        with redirect_stdout(io.StringIO()):
            dispatch_specs(args)

        local_repo = docs_repo.parent / "repo-a"
        local_repo.mkdir(parents=True)
        (local_repo / ".git").mkdir()

        config_path = docs_repo / ".openclaw" / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["repo_mappings"]["repo-a"]["local_path"] = str(local_repo)
        config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

        registry_path = docs_repo / ".openclaw-state" / "registry.json"
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        registry["tasks"][0]["state"] = "in_progress"
        registry["tasks"][0]["launched_at"] = "2026-04-12T00:00:00+00:00"
        registry_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")

        launch_args = SimpleNamespace(
            docs_repo=docs_repo,
            config=None,
            task_id="task-0001",
            state=None,
        )

        FakeOrchestrator.reset()
        FakeOrchestrator.start_task_ids = ["orx-task-2"]
        FakeOrchestrator.task_by_id["orx-task-2"] = self._make_orx_task(
            task_id="orx-task-2",
            status=TaskStatus.RUNNING,
            workspace_path=(local_repo / "../worktrees/repo-a/task-0001-single-spec").resolve(),
            branch_name="openclaw/task-0001/single-spec",
            worktree_path="../worktrees/repo-a/task-0001-single-spec",
        )

        with patch("openclaw_bridge.cli.Orchestrator", FakeOrchestrator):
            with redirect_stdout(io.StringIO()):
                launch_workers(launch_args)

        task = json.loads(registry_path.read_text(encoding="utf-8"))["tasks"][0]
        self.assertEqual(task["state"], "in_progress")
        self.assertEqual(task["launch_attempts"], 1)
        self.assertIsNone(task["last_error"])
        self.assertEqual(task["orx_task_id"], "orx-task-2")
        self.assertEqual(task["tmux_session"], "orx-orx-task-2")

    def test_launch_workers_restarts_transport_error_session(self) -> None:
        docs_repo = self._create_docs_repo()
        spec_path = docs_repo / "docs" / "prd" / "single-spec.md"
        self._write_spec(spec_path, "Single Spec")

        args = SimpleNamespace(
            docs_repo=spec_path,
            config=None,
            create_issues=True,
            dry_run=True,
            launch_workers=False,
        )
        with redirect_stdout(io.StringIO()):
            dispatch_specs(args)

        local_repo = docs_repo.parent / "repo-a"
        local_repo.mkdir(parents=True)
        (local_repo / ".git").mkdir()

        config_path = docs_repo / ".openclaw" / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["repo_mappings"]["repo-a"]["local_path"] = str(local_repo)
        config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

        registry_path = docs_repo / ".openclaw-state" / "registry.json"
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        registry["tasks"][0]["state"] = "in_progress"
        registry["tasks"][0]["orx_task_id"] = "orx-task-1"
        registry["tasks"][0]["tmux_session"] = "orx-orx-task-1"
        registry["tasks"][0]["launched_at"] = "2026-04-12T00:00:00+00:00"
        registry_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")

        launch_args = SimpleNamespace(
            docs_repo=docs_repo,
            config=None,
            task_id="task-0001",
            state=None,
        )

        FakeOrchestrator.reset()
        FakeOrchestrator.start_task_ids = ["orx-task-2"]
        FakeOrchestrator.task_by_id["orx-task-1"] = self._make_orx_task(
            task_id="orx-task-1",
            status=TaskStatus.FAILED,
            workspace_path=(local_repo / "../worktrees/repo-a/task-0001-single-spec").resolve(),
            branch_name="openclaw/task-0001/single-spec",
            worktree_path="../worktrees/repo-a/task-0001-single-spec",
            retryable=True,
            failure_reason=TaskFailureReason.RUNTIME_ERROR,
            failure_message="worker transport stream disconnected",
        )
        FakeOrchestrator.task_by_id["orx-task-2"] = self._make_orx_task(
            task_id="orx-task-2",
            status=TaskStatus.RUNNING,
            workspace_path=(local_repo / "../worktrees/repo-a/task-0001-single-spec").resolve(),
            branch_name="openclaw/task-0001/single-spec",
            worktree_path="../worktrees/repo-a/task-0001-single-spec",
        )
        FakeOrchestrator.result_by_id["orx-task-1"] = TaskResult(
            task_id="orx-task-1",
            status="completed",
            outcome="failure",
            summary="worker transport stream disconnected",
            artifacts=[],
            error="worker transport stream disconnected",
            metadata={},
        )

        with patch("openclaw_bridge.cli.Orchestrator", FakeOrchestrator):
            with redirect_stdout(io.StringIO()):
                launch_workers(launch_args)

        task = json.loads(registry_path.read_text(encoding="utf-8"))["tasks"][0]
        self.assertEqual(task["state"], "in_progress")
        self.assertEqual(task["launch_attempts"], 1)
        self.assertIsNone(task["last_error"])
        self.assertEqual(task["orx_task_id"], "orx-task-2")
        self.assertEqual(task["tmux_session"], "orx-orx-task-2")

    def test_launch_workers_blocks_when_session_exits_immediately(self) -> None:
        docs_repo = self._create_docs_repo()
        spec_path = docs_repo / "docs" / "prd" / "single-spec.md"
        self._write_spec(spec_path, "Single Spec")

        args = SimpleNamespace(
            docs_repo=spec_path,
            config=None,
            create_issues=True,
            dry_run=True,
            launch_workers=False,
        )
        with redirect_stdout(io.StringIO()):
            dispatch_specs(args)

        local_repo = docs_repo.parent / "repo-a"
        local_repo.mkdir(parents=True)
        (local_repo / ".git").mkdir()

        config_path = docs_repo / ".openclaw" / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["repo_mappings"]["repo-a"]["local_path"] = str(local_repo)
        config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

        launch_args = SimpleNamespace(
            docs_repo=docs_repo,
            config=None,
            task_id="task-0001",
            state="dispatched",
        )

        FakeOrchestrator.reset()
        FakeOrchestrator.task_by_id["orx-task-1"] = self._make_orx_task(
            task_id="orx-task-1",
            status=TaskStatus.FAILED,
            workspace_path=(local_repo / "../worktrees/repo-a/task-0001-single-spec").resolve(),
            branch_name="openclaw/task-0001/single-spec",
            worktree_path="../worktrees/repo-a/task-0001-single-spec",
            retryable=False,
            failure_reason=TaskFailureReason.LAUNCH_ERROR,
            failure_message="task failed during launch",
        )
        FakeOrchestrator.result_by_id["orx-task-1"] = TaskResult(
            task_id="orx-task-1",
            status="completed",
            outcome="failure",
            summary="task failed during launch",
            artifacts=[],
            error="task failed during launch",
            metadata={},
        )

        with patch("openclaw_bridge.cli.Orchestrator", FakeOrchestrator):
            with redirect_stdout(io.StringIO()):
                launch_workers(launch_args)

        task = json.loads((docs_repo / ".openclaw-state" / "registry.json").read_text(encoding="utf-8"))["tasks"][0]
        self.assertEqual(task["state"], "blocked")
        self.assertEqual(task["launch_attempts"], 1)
        self.assertEqual(task["orx_task_id"], "orx-task-1")
        self.assertIn("task failed during launch", task["last_error"])

    def test_reconcile_workers_marks_missing_session_as_restartable(self) -> None:
        docs_repo = self._create_docs_repo()
        spec_path = docs_repo / "docs" / "prd" / "single-spec.md"
        self._write_spec(spec_path, "Single Spec")

        args = SimpleNamespace(
            docs_repo=spec_path,
            config=None,
            create_issues=True,
            dry_run=True,
            launch_workers=False,
        )
        with redirect_stdout(io.StringIO()):
            dispatch_specs(args)

        local_repo = docs_repo.parent / "repo-a"
        local_repo.mkdir(parents=True)
        (local_repo / ".git").mkdir()

        config_path = docs_repo / ".openclaw" / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["repo_mappings"]["repo-a"]["local_path"] = str(local_repo)
        config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

        registry_path = docs_repo / ".openclaw-state" / "registry.json"
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        registry["tasks"][0]["state"] = "in_progress"
        registry["tasks"][0]["launched_at"] = "2026-04-12T00:00:00+00:00"
        registry_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")

        reconcile_args = SimpleNamespace(
            docs_repo=docs_repo,
            config=None,
            task_id="task-0001",
            state=None,
            restart=False,
        )

        with redirect_stdout(io.StringIO()):
            reconcile_workers(reconcile_args)

        task = json.loads(registry_path.read_text(encoding="utf-8"))["tasks"][0]
        self.assertEqual(task["state"], "restartable")
        self.assertEqual(task["last_error"], "task is missing orx runtime handle; relaunch required")

    def test_reconcile_workers_marks_known_pane_error_as_restartable(self) -> None:
        docs_repo = self._create_docs_repo()
        spec_path = docs_repo / "docs" / "prd" / "single-spec.md"
        self._write_spec(spec_path, "Single Spec")

        args = SimpleNamespace(
            docs_repo=spec_path,
            config=None,
            create_issues=True,
            dry_run=True,
            launch_workers=False,
        )
        with redirect_stdout(io.StringIO()):
            dispatch_specs(args)

        local_repo = self._configure_local_repo(docs_repo)
        registry_path = docs_repo / ".openclaw-state" / "registry.json"
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        registry["tasks"][0]["state"] = "in_progress"
        registry["tasks"][0]["orx_task_id"] = "orx-task-1"
        registry["tasks"][0]["tmux_session"] = "orx-orx-task-1"
        registry["tasks"][0]["launched_at"] = "2026-04-12T00:00:00+00:00"
        registry_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")
        registry_task = registry["tasks"][0]

        reconcile_args = SimpleNamespace(
            docs_repo=docs_repo,
            config=None,
            task_id="task-0001",
            state=None,
            restart=False,
        )

        FakeOrchestrator.reset()
        FakeOrchestrator.task_by_id["orx-task-1"] = self._make_orx_task(
            task_id="orx-task-1",
            status=TaskStatus.FAILED,
            workspace_path=(local_repo / registry_task["worktree_path"]).resolve(),
            branch_name=registry_task["branch_name"],
            worktree_path=registry_task["worktree_path"],
            retryable=True,
            failure_reason=TaskFailureReason.RUNTIME_ERROR,
            failure_message="worker transport stream disconnected",
        )
        FakeOrchestrator.result_by_id["orx-task-1"] = TaskResult(
            task_id="orx-task-1",
            status="completed",
            outcome="failure",
            summary="worker transport stream disconnected",
            artifacts=[],
            error="worker transport stream disconnected",
            metadata={},
        )

        with patch("openclaw_bridge.cli.Orchestrator", FakeOrchestrator):
            with redirect_stdout(io.StringIO()):
                reconcile_workers(reconcile_args)

        task = json.loads(registry_path.read_text(encoding="utf-8"))["tasks"][0]
        self.assertEqual(task["state"], "restartable")
        self.assertEqual(task["last_error"], "worker transport stream disconnected")

    def test_reconcile_workers_marks_dirty_worktree_session_exit_as_exited(self) -> None:
        docs_repo = self._create_docs_repo()
        spec_path = docs_repo / "docs" / "prd" / "single-spec.md"
        self._write_spec(spec_path, "Single Spec")

        args = SimpleNamespace(
            docs_repo=spec_path,
            config=None,
            create_issues=True,
            dry_run=True,
            launch_workers=False,
        )
        with redirect_stdout(io.StringIO()):
            dispatch_specs(args)

        local_repo = docs_repo.parent / "repo-a"
        local_repo.mkdir(parents=True)
        (local_repo / ".git").mkdir()

        config_path = docs_repo / ".openclaw" / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["repo_mappings"]["repo-a"]["local_path"] = str(local_repo)
        config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

        registry_path = docs_repo / ".openclaw-state" / "registry.json"
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        registry["tasks"][0]["state"] = "in_progress"
        registry["tasks"][0]["launched_at"] = "2026-04-12T00:00:00+00:00"
        registry_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")

        task = registry["tasks"][0]
        worktree_path = (local_repo / task["worktree_path"]).resolve()
        worktree_path.mkdir(parents=True, exist_ok=True)

        reconcile_args = SimpleNamespace(
            docs_repo=docs_repo,
            config=None,
            task_id="task-0001",
            state=None,
            restart=False,
        )

        with patch("openclaw_bridge.cli.subprocess.run") as mocked_status:
            mocked_status.return_value = SimpleNamespace(returncode=0, stdout=" M foo.py\n", stderr="")
            with redirect_stdout(io.StringIO()):
                reconcile_workers(reconcile_args)

        task = json.loads(registry_path.read_text(encoding="utf-8"))["tasks"][0]
        self.assertEqual(task["state"], "exited")
        self.assertIn("local code changes", task["last_error"])
        self.assertEqual(mocked_status.call_args.args[0][:4], ["git", "-C", str(worktree_path), "status"])

    def test_reconcile_workers_can_safely_restart_restartable_task(self) -> None:
        docs_repo = self._create_docs_repo()
        spec_path = docs_repo / "docs" / "prd" / "single-spec.md"
        self._write_spec(spec_path, "Single Spec")

        args = SimpleNamespace(
            docs_repo=spec_path,
            config=None,
            create_issues=True,
            dry_run=True,
            launch_workers=False,
        )
        with redirect_stdout(io.StringIO()):
            dispatch_specs(args)

        local_repo = docs_repo.parent / "repo-a"
        local_repo.mkdir(parents=True)
        (local_repo / ".git").mkdir()

        config_path = docs_repo / ".openclaw" / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["repo_mappings"]["repo-a"]["local_path"] = str(local_repo)
        config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

        registry_path = docs_repo / ".openclaw-state" / "registry.json"
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        registry["tasks"][0]["state"] = "restartable"
        registry_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")

        reconcile_args = SimpleNamespace(
            docs_repo=docs_repo,
            config=None,
            task_id="task-0001",
            state=None,
            restart=True,
        )

        FakeOrchestrator.reset()
        FakeOrchestrator.start_task_ids = ["orx-task-2"]
        FakeOrchestrator.task_by_id["orx-task-2"] = self._make_orx_task(
            task_id="orx-task-2",
            status=TaskStatus.RUNNING,
            workspace_path=(local_repo / registry["tasks"][0]["worktree_path"]).resolve(),
            branch_name=registry["tasks"][0]["branch_name"],
            worktree_path=registry["tasks"][0]["worktree_path"],
        )

        with patch("openclaw_bridge.cli.Orchestrator", FakeOrchestrator):
            with redirect_stdout(io.StringIO()):
                reconcile_workers(reconcile_args)

        task = json.loads(registry_path.read_text(encoding="utf-8"))["tasks"][0]
        self.assertEqual(task["state"], "in_progress")
        self.assertEqual(task["launch_attempts"], 1)
        self.assertIsNone(task["last_error"])
        self.assertEqual(task["orx_task_id"], "orx-task-2")
        self.assertEqual(task["tmux_session"], "orx-orx-task-2")

    def test_watch_workers_commits_completed_task_and_appends_event(self) -> None:
        docs_repo = self._create_docs_repo()
        self._dispatch_single_task(docs_repo)
        local_repo = self._configure_local_repo(docs_repo)
        task = self._set_registry_task(
            docs_repo,
            state="in_progress",
            orx_task_id="orx-task-1",
            tmux_session="orx-orx-task-1",
            launched_at="2026-04-12T00:00:00+00:00",
        )
        worktree_path = (local_repo / task["worktree_path"]).resolve()
        worktree_path.mkdir(parents=True, exist_ok=True)

        FakeOrchestrator.reset()
        FakeOrchestrator.task_by_id["orx-task-1"] = self._make_orx_task(
            task_id="orx-task-1",
            status=TaskStatus.SUCCEEDED,
            workspace_path=worktree_path,
            branch_name=task["branch_name"],
            worktree_path=task["worktree_path"],
        )
        FakeOrchestrator.result_by_id["orx-task-1"] = TaskResult(
            task_id="orx-task-1",
            status="completed",
            outcome="success",
            summary="Implemented and validated the task.",
            artifacts=[],
            error=None,
            metadata={
                "commit_message": "feat: finish task-0001",
                "tests_run": ["python -m unittest tests.test_cli -v"],
                "notes": ["watchdog verified completion artifact"],
            },
        )

        watch_args = SimpleNamespace(
            docs_repo=docs_repo,
            config=None,
            task_id="task-0001",
            state=None,
            restart=False,
            json=False,
            once=True,
        )

        with patch("openclaw_bridge.cli.Orchestrator", FakeOrchestrator):
            with patch("openclaw_bridge.cli.subprocess.run") as mocked_run:
                mocked_run.side_effect = [
                    SimpleNamespace(returncode=0, stdout=" M foo.py\n", stderr=""),
                    SimpleNamespace(returncode=0, stdout="", stderr=""),
                    SimpleNamespace(returncode=0, stdout="[task-0001] commit\n", stderr=""),
                    SimpleNamespace(returncode=0, stdout="abc123\n", stderr=""),
                ]
                with redirect_stdout(io.StringIO()):
                    watch_workers(watch_args)

        registry_path = docs_repo / ".openclaw-state" / "registry.json"
        saved_task = json.loads(registry_path.read_text(encoding="utf-8"))["tasks"][0]
        self.assertEqual(saved_task["state"], "completed")
        self.assertEqual(saved_task["commit_sha"], "abc123")
        self.assertEqual(saved_task["commit_message"], "feat: finish task-0001")
        self.assertEqual(saved_task["completion_summary"], "Implemented and validated the task.")
        self.assertIsNotNone(saved_task["completed_at"])
        events_path = docs_repo / ".openclaw-state" / "watchdog-events.jsonl"
        events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([event["event"] for event in events], ["completed"])
        self.assertEqual(events[0]["commit_sha"], "abc123")
        commands = [call.args[0] for call in mocked_run.call_args_list]
        self.assertEqual(commands[0][:4], ["git", "-C", str(worktree_path), "status"])
        self.assertEqual(commands[1][:4], ["git", "-C", str(worktree_path), "add"])
        self.assertEqual(commands[2][:4], ["git", "-C", str(worktree_path), "commit"])
        self.assertEqual(commands[3][:4], ["git", "-C", str(worktree_path), "rev-parse"])

    def test_watch_workers_does_not_commit_while_session_is_still_running(self) -> None:
        docs_repo = self._create_docs_repo()
        self._dispatch_single_task(docs_repo)
        local_repo = self._configure_local_repo(docs_repo)
        registry_task = self._set_registry_task(
            docs_repo,
            state="in_progress",
            orx_task_id="orx-task-1",
            tmux_session="orx-orx-task-1",
            launched_at="2026-04-12T00:00:00+00:00",
        )
        worktree_path = (local_repo / registry_task["worktree_path"]).resolve()

        FakeOrchestrator.reset()
        FakeOrchestrator.task_by_id["orx-task-1"] = self._make_orx_task(
            task_id="orx-task-1",
            status=TaskStatus.RUNNING,
            workspace_path=worktree_path,
            branch_name=registry_task["branch_name"],
            worktree_path=registry_task["worktree_path"],
        )
        FakeOrchestrator.result_by_id["orx-task-1"] = TaskResult(
            task_id="orx-task-1",
            status="completed",
            outcome="success",
            summary="Implementation is ready for commit.",
            artifacts=[],
            error=None,
            metadata={"commit_message": "feat: finish task-0001"},
        )

        watch_args = SimpleNamespace(
            docs_repo=docs_repo,
            config=None,
            task_id="task-0001",
            state=None,
            restart=False,
            json=False,
            once=True,
        )

        with patch("openclaw_bridge.cli.Orchestrator", FakeOrchestrator):
            with patch("openclaw_bridge.cli.subprocess.run") as mocked_run:
                with redirect_stdout(io.StringIO()):
                    watch_workers(watch_args)
                mocked_run.assert_not_called()

        registry_path = docs_repo / ".openclaw-state" / "registry.json"
        saved_task = json.loads(registry_path.read_text(encoding="utf-8"))["tasks"][0]
        self.assertEqual(saved_task["state"], "in_progress")
        self.assertIsNone(saved_task["commit_sha"])
        self.assertFalse((docs_repo / ".openclaw-state" / "watchdog-events.jsonl").exists())

    def test_watch_workers_blocks_when_completion_marker_has_no_local_changes(self) -> None:
        docs_repo = self._create_docs_repo()
        self._dispatch_single_task(docs_repo)
        local_repo = self._configure_local_repo(docs_repo)
        task = self._set_registry_task(
            docs_repo,
            state="in_progress",
            orx_task_id="orx-task-1",
            tmux_session="orx-orx-task-1",
            launched_at="2026-04-12T00:00:00+00:00",
        )
        worktree_path = (local_repo / task["worktree_path"]).resolve()
        worktree_path.mkdir(parents=True, exist_ok=True)

        FakeOrchestrator.reset()
        FakeOrchestrator.task_by_id["orx-task-1"] = self._make_orx_task(
            task_id="orx-task-1",
            status=TaskStatus.SUCCEEDED,
            workspace_path=worktree_path,
            branch_name=task["branch_name"],
            worktree_path=task["worktree_path"],
        )
        FakeOrchestrator.result_by_id["orx-task-1"] = TaskResult(
            task_id="orx-task-1",
            status="completed",
            outcome="success",
            summary="Implementation is ready for commit.",
            artifacts=[],
            error=None,
            metadata={"commit_message": "feat: finish task-0001"},
        )

        watch_args = SimpleNamespace(
            docs_repo=docs_repo,
            config=None,
            task_id="task-0001",
            state=None,
            restart=False,
            json=False,
            once=True,
        )

        with patch("openclaw_bridge.cli.Orchestrator", FakeOrchestrator):
            with patch("openclaw_bridge.cli.subprocess.run") as mocked_run:
                mocked_run.return_value = SimpleNamespace(returncode=0, stdout="", stderr="")
                with redirect_stdout(io.StringIO()):
                    watch_workers(watch_args)

        registry_path = docs_repo / ".openclaw-state" / "registry.json"
        saved_task = json.loads(registry_path.read_text(encoding="utf-8"))["tasks"][0]
        self.assertEqual(saved_task["state"], "blocked")
        self.assertIn("declared completion but the worktree has no local changes", saved_task["last_error"])
        events = [
            json.loads(line)
            for line in (docs_repo / ".openclaw-state" / "watchdog-events.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual([event["event"] for event in events], ["blocked"])

    def test_watch_workers_blocks_invalid_completion_marker(self) -> None:
        docs_repo = self._create_docs_repo()
        self._dispatch_single_task(docs_repo)
        local_repo = self._configure_local_repo(docs_repo)
        task = self._set_registry_task(
            docs_repo,
            state="in_progress",
            orx_task_id="orx-task-1",
            tmux_session="orx-orx-task-1",
            launched_at="2026-04-12T00:00:00+00:00",
        )
        worktree_path = (local_repo / task["worktree_path"]).resolve()

        FakeOrchestrator.reset()
        FakeOrchestrator.task_by_id["orx-task-1"] = self._make_orx_task(
            task_id="orx-task-1",
            status=TaskStatus.SUCCEEDED,
            workspace_path=worktree_path,
            branch_name=task["branch_name"],
            worktree_path=task["worktree_path"],
        )
        FakeOrchestrator.result_by_id["orx-task-1"] = TaskResult(
            task_id="orx-task-1",
            status="completed",
            outcome="success",
            summary="Implementation is ready for commit.",
            artifacts=[],
            error=None,
            metadata={},
        )

        watch_args = SimpleNamespace(
            docs_repo=docs_repo,
            config=None,
            task_id="task-0001",
            state=None,
            restart=False,
            json=False,
            once=True,
        )

        with patch("openclaw_bridge.cli.Orchestrator", FakeOrchestrator):
            with redirect_stdout(io.StringIO()):
                watch_workers(watch_args)

        registry_path = docs_repo / ".openclaw-state" / "registry.json"
        saved_task = json.loads(registry_path.read_text(encoding="utf-8"))["tasks"][0]
        self.assertEqual(saved_task["state"], "blocked")
        self.assertIn("metadata.commit_message or metadata.commit_sha", saved_task["last_error"])

    def test_watch_workers_is_idempotent_after_completion(self) -> None:
        docs_repo = self._create_docs_repo()
        self._dispatch_single_task(docs_repo)
        local_repo = self._configure_local_repo(docs_repo)
        task = self._set_registry_task(
            docs_repo,
            state="in_progress",
            orx_task_id="orx-task-1",
            tmux_session="orx-orx-task-1",
            launched_at="2026-04-12T00:00:00+00:00",
        )
        worktree_path = (local_repo / task["worktree_path"]).resolve()
        worktree_path.mkdir(parents=True, exist_ok=True)

        FakeOrchestrator.reset()
        FakeOrchestrator.task_by_id["orx-task-1"] = self._make_orx_task(
            task_id="orx-task-1",
            status=TaskStatus.SUCCEEDED,
            workspace_path=worktree_path,
            branch_name=task["branch_name"],
            worktree_path=task["worktree_path"],
        )
        FakeOrchestrator.result_by_id["orx-task-1"] = TaskResult(
            task_id="orx-task-1",
            status="completed",
            outcome="success",
            summary="Implementation is ready for commit.",
            artifacts=[],
            error=None,
            metadata={"commit_message": "feat: finish task-0001"},
        )

        watch_args = SimpleNamespace(
            docs_repo=docs_repo,
            config=None,
            task_id="task-0001",
            state=None,
            restart=False,
            json=False,
            once=True,
        )

        with patch("openclaw_bridge.cli.Orchestrator", FakeOrchestrator):
            with patch("openclaw_bridge.cli.subprocess.run") as mocked_run:
                mocked_run.side_effect = [
                    SimpleNamespace(returncode=0, stdout=" M foo.py\n", stderr=""),
                    SimpleNamespace(returncode=0, stdout="", stderr=""),
                    SimpleNamespace(returncode=0, stdout="[task-0001] commit\n", stderr=""),
                    SimpleNamespace(returncode=0, stdout="abc123\n", stderr=""),
                ]
                with redirect_stdout(io.StringIO()):
                    watch_workers(watch_args)

        with patch("openclaw_bridge.cli.Orchestrator", FakeOrchestrator):
            with patch("openclaw_bridge.cli.subprocess.run") as mocked_run:
                with redirect_stdout(io.StringIO()):
                    watch_workers(watch_args)
                mocked_run.assert_not_called()

        events_path = docs_repo / ".openclaw-state" / "watchdog-events.jsonl"
        events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event"], "completed")

    def test_watch_workers_blocks_when_result_requests_human_help(self) -> None:
        docs_repo = self._create_docs_repo()
        self._dispatch_single_task(docs_repo)
        local_repo = self._configure_local_repo(docs_repo)
        task = self._set_registry_task(
            docs_repo,
            state="in_progress",
            orx_task_id="orx-task-1",
            tmux_session="orx-orx-task-1",
            launched_at="2026-04-12T00:00:00+00:00",
        )
        worktree_path = (local_repo / task["worktree_path"]).resolve()

        FakeOrchestrator.reset()
        FakeOrchestrator.task_by_id["orx-task-1"] = self._make_orx_task(
            task_id="orx-task-1",
            status=TaskStatus.FAILED,
            workspace_path=worktree_path,
            branch_name=task["branch_name"],
            worktree_path=task["worktree_path"],
            retryable=False,
            failure_reason=TaskFailureReason.RUNTIME_ERROR,
            failure_message="waiting for schema decision",
        )
        FakeOrchestrator.result_by_id["orx-task-1"] = TaskResult(
            task_id="orx-task-1",
            status="completed",
            outcome="needs_human",
            summary="Migration depends on an unresolved schema decision.",
            artifacts=[],
            error="waiting for schema decision",
            metadata={
                "notes": ["needs product clarification before changing schema"],
            },
        )

        watch_args = SimpleNamespace(
            docs_repo=docs_repo,
            config=None,
            task_id="task-0001",
            state=None,
            restart=False,
            json=False,
            once=True,
        )

        with patch("openclaw_bridge.cli.Orchestrator", FakeOrchestrator):
            with redirect_stdout(io.StringIO()):
                watch_workers(watch_args)

        registry_path = docs_repo / ".openclaw-state" / "registry.json"
        saved_task = json.loads(registry_path.read_text(encoding="utf-8"))["tasks"][0]
        self.assertEqual(saved_task["state"], "blocked")
        self.assertIn("waiting for schema decision", saved_task["last_error"])

    def test_watch_workers_json_output_includes_events_and_restarts(self) -> None:
        docs_repo = self._create_docs_repo()
        self._dispatch_single_task(docs_repo)
        local_repo = self._configure_local_repo(docs_repo)
        registry_task = self._set_registry_task(
            docs_repo,
            state="in_progress",
            orx_task_id="orx-task-1",
            tmux_session="orx-orx-task-1",
            launched_at="2026-04-12T00:00:00+00:00",
        )
        worktree_path = (local_repo / registry_task["worktree_path"]).resolve()

        watch_args = SimpleNamespace(
            docs_repo=docs_repo,
            config=None,
            task_id="task-0001",
            state=None,
            restart=True,
            json=True,
            once=True,
        )

        FakeOrchestrator.reset()
        FakeOrchestrator.start_task_ids = ["orx-task-2"]
        FakeOrchestrator.task_by_id["orx-task-1"] = self._make_orx_task(
            task_id="orx-task-1",
            status=TaskStatus.FAILED,
            workspace_path=worktree_path,
            branch_name=registry_task["branch_name"],
            worktree_path=registry_task["worktree_path"],
            retryable=True,
            failure_reason=TaskFailureReason.RUNTIME_ERROR,
            failure_message="worker transport stream disconnected",
        )
        FakeOrchestrator.task_by_id["orx-task-2"] = self._make_orx_task(
            task_id="orx-task-2",
            status=TaskStatus.RUNNING,
            workspace_path=worktree_path,
            branch_name=registry_task["branch_name"],
            worktree_path=registry_task["worktree_path"],
        )
        FakeOrchestrator.result_by_id["orx-task-1"] = TaskResult(
            task_id="orx-task-1",
            status="completed",
            outcome="failure",
            summary="worker transport stream disconnected",
            artifacts=[],
            error="worker transport stream disconnected",
            metadata={},
        )

        with patch("openclaw_bridge.cli.Orchestrator", FakeOrchestrator):
            output = io.StringIO()
            with redirect_stdout(output):
                watch_workers(watch_args)

        payload = json.loads(output.getvalue())
        self.assertEqual(payload["restarted"], 1)
        self.assertEqual([event["event"] for event in payload["events"]], ["restartable", "restarted"])
        self.assertEqual(payload["restarted_tasks"][0]["task_id"], "task-0001")
        self.assertEqual(payload["restarted_tasks"][0]["state"], "in_progress")

    def test_launch_workers_normalizes_legacy_non_ascii_launch_paths(self) -> None:
        docs_repo = self._create_docs_repo()
        spec_path = docs_repo / "docs" / "prd" / "single-spec.md"
        self._write_spec(spec_path, "Single Spec")

        args = SimpleNamespace(
            docs_repo=spec_path,
            config=None,
            create_issues=True,
            dry_run=True,
            launch_workers=False,
        )
        with redirect_stdout(io.StringIO()):
            dispatch_specs(args)

        local_repo = docs_repo.parent / "repo-a"
        local_repo.mkdir(parents=True)
        (local_repo / ".git").mkdir()

        config_path = docs_repo / ".openclaw" / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["repo_mappings"]["repo-a"]["local_path"] = str(local_repo)
        config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

        registry_path = docs_repo / ".openclaw-state" / "registry.json"
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        registry["tasks"][0]["branch_name"] = "openclaw/task-0001/单次任务"
        registry["tasks"][0]["worktree_path"] = "../worktrees/repo-a/task-0001-单次任务"
        registry_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")

        issue_body_path = docs_repo / ".openclaw-state" / "artifacts" / "task-0001" / "implementation-issue.md"
        worker_prompt_path = docs_repo / ".openclaw-state" / "artifacts" / "task-0001" / "worker-prompt.txt"
        worker_prompt_path.write_text(
            worker_prompt_path.read_text(encoding="utf-8").replace("single-spec", "单次任务"),
            encoding="utf-8",
        )
        self.assertIn("单次任务", worker_prompt_path.read_text(encoding="utf-8"))

        launch_args = SimpleNamespace(
            docs_repo=docs_repo,
            config=None,
            task_id="task-0001",
            state="dispatched",
        )

        FakeOrchestrator.reset()
        FakeOrchestrator.task_by_id["orx-task-1"] = self._make_orx_task(
            task_id="orx-task-1",
            status=TaskStatus.RUNNING,
            workspace_path=(local_repo / "../worktrees/repo-a/task-0001-single-spec").resolve(),
            branch_name="openclaw/task-0001/single-spec",
            worktree_path="../worktrees/repo-a/task-0001-single-spec",
        )

        with patch("openclaw_bridge.cli.Orchestrator", FakeOrchestrator):
            with redirect_stdout(io.StringIO()):
                launch_workers(launch_args)

        task = json.loads(registry_path.read_text(encoding="utf-8"))["tasks"][0]
        self.assertEqual(task["branch_name"], "openclaw/task-0001/single-spec")
        self.assertEqual(task["worktree_path"], "../worktrees/repo-a/task-0001-single-spec")
        self.assertIn("single-spec", worker_prompt_path.read_text(encoding="utf-8"))
        request = FakeOrchestrator.started_requests[-1]
        self.assertEqual(request.branch_name, "openclaw/task-0001/single-spec")
        self.assertEqual(request.worktree_path, Path("../worktrees/repo-a/task-0001-single-spec"))

    def test_dispatch_specs_slugifies_non_ascii_titles_for_branch_names(self) -> None:
        docs_repo = self._create_docs_repo()
        spec_path = docs_repo / "docs" / "prd" / "unicode-spec.md"
        self._write_spec(spec_path, "非机动车 上传")

        args = SimpleNamespace(
            docs_repo=spec_path,
            config=None,
            create_issues=False,
            dry_run=False,
            launch_workers=False,
        )

        with redirect_stdout(io.StringIO()):
            dispatch_specs(args)

        registry_path = docs_repo / ".openclaw-state" / "registry.json"
        task = json.loads(registry_path.read_text(encoding="utf-8"))["tasks"][0]
        self.assertEqual(task["branch_name"], "openclaw/task-0001/task")
        self.assertEqual(task["worktree_path"], "../worktrees/repo-a/task-0001-task")

    def test_launch_workers_uses_orx_and_persists_runtime_metadata(self) -> None:
        docs_repo = self._create_docs_repo()
        self._dispatch_single_task(docs_repo)
        local_repo = self._configure_local_repo(docs_repo)
        registry_task = self._set_registry_task(docs_repo, state="dispatched")
        worktree_path = (local_repo / registry_task["worktree_path"]).resolve()

        FakeOrchestrator.reset()
        FakeOrchestrator.task_by_id["orx-task-1"] = self._make_orx_task(
            task_id="orx-task-1",
            status=TaskStatus.RUNNING,
            workspace_path=worktree_path,
            branch_name=registry_task["branch_name"],
            worktree_path=registry_task["worktree_path"],
        )

        launch_args = SimpleNamespace(
            docs_repo=docs_repo,
            config=None,
            task_id="task-0001",
            state=None,
        )

        with patch("openclaw_bridge.cli.Orchestrator", FakeOrchestrator):
            with redirect_stdout(io.StringIO()):
                launch_workers(launch_args)

        registry = json.loads((docs_repo / ".openclaw-state" / "registry.json").read_text(encoding="utf-8"))["tasks"][0]
        self.assertEqual(registry["state"], "in_progress")
        self.assertEqual(registry["orx_task_id"], "orx-task-1")
        self.assertEqual(registry["tmux_session"], "orx-orx-task-1")
        self.assertEqual(
            FakeOrchestrator.init_data_roots[-1],
            (docs_repo / ".openclaw-state" / "orx").resolve(),
        )
        request = FakeOrchestrator.started_requests[-1]
        self.assertEqual(request.cwd, local_repo.resolve())
        self.assertEqual(request.workspace_strategy, "git_worktree")
        self.assertEqual(request.branch_name, registry_task["branch_name"])
        self.assertEqual(request.worktree_path, Path(registry_task["worktree_path"]))

    def test_watch_workers_maps_retryable_failed_orx_task_with_local_changes_to_exited(self) -> None:
        docs_repo = self._create_docs_repo()
        self._dispatch_single_task(docs_repo)
        local_repo = self._configure_local_repo(docs_repo)
        registry_task = self._set_registry_task(
            docs_repo,
            state="in_progress",
            orx_task_id="orx-task-1",
            tmux_session="orx-orx-task-1",
        )
        worktree_path = (local_repo / registry_task["worktree_path"]).resolve()
        worktree_path.mkdir(parents=True, exist_ok=True)

        FakeOrchestrator.reset()
        FakeOrchestrator.task_by_id["orx-task-1"] = self._make_orx_task(
            task_id="orx-task-1",
            status=TaskStatus.FAILED,
            workspace_path=worktree_path,
            branch_name=registry_task["branch_name"],
            worktree_path=registry_task["worktree_path"],
            retryable=True,
            failure_reason=TaskFailureReason.ARTIFACT_MISSING,
            failure_message="worker transport stream disconnected",
        )
        FakeOrchestrator.result_by_id["orx-task-1"] = TaskResult(
            task_id="orx-task-1",
            status="completed",
            outcome="failure",
            summary="worker transport stream disconnected",
            artifacts=[],
            error="worker transport stream disconnected",
            metadata={},
        )

        watch_args = SimpleNamespace(
            docs_repo=docs_repo,
            config=None,
            task_id="task-0001",
            state=None,
            restart=False,
            json=False,
            once=True,
        )

        with patch("openclaw_bridge.cli.Orchestrator", FakeOrchestrator):
            with patch("openclaw_bridge.cli.subprocess.run") as mocked_run:
                mocked_run.return_value = SimpleNamespace(returncode=0, stdout=" M foo.py\n", stderr="")
                with redirect_stdout(io.StringIO()):
                    watch_workers(watch_args)

        registry = json.loads((docs_repo / ".openclaw-state" / "registry.json").read_text(encoding="utf-8"))["tasks"][0]
        self.assertEqual(registry["state"], "exited")
        self.assertIn("local code changes", registry["last_error"])
        self.assertEqual(mocked_run.call_args.args[0][:4], ["git", "-C", str(worktree_path), "status"])

    def test_watch_workers_commits_succeeded_orx_task_and_logs_event(self) -> None:
        docs_repo = self._create_docs_repo()
        self._dispatch_single_task(docs_repo)
        local_repo = self._configure_local_repo(docs_repo)
        registry_task = self._set_registry_task(
            docs_repo,
            state="in_progress",
            orx_task_id="orx-task-1",
            tmux_session="orx-orx-task-1",
        )
        worktree_path = (local_repo / registry_task["worktree_path"]).resolve()
        worktree_path.mkdir(parents=True, exist_ok=True)

        FakeOrchestrator.reset()
        FakeOrchestrator.task_by_id["orx-task-1"] = self._make_orx_task(
            task_id="orx-task-1",
            status=TaskStatus.SUCCEEDED,
            workspace_path=worktree_path,
            branch_name=registry_task["branch_name"],
            worktree_path=registry_task["worktree_path"],
        )
        FakeOrchestrator.result_by_id["orx-task-1"] = TaskResult(
            task_id="orx-task-1",
            status="completed",
            outcome="success",
            summary="Implemented and validated the task.",
            artifacts=[TaskArtifact(name="summary", path="summary.md")],
            error=None,
            metadata={"commit_message": "feat: finish task-0001"},
        )

        watch_args = SimpleNamespace(
            docs_repo=docs_repo,
            config=None,
            task_id="task-0001",
            state=None,
            restart=False,
            json=False,
            once=True,
        )

        with patch("openclaw_bridge.cli.Orchestrator", FakeOrchestrator):
            with patch("openclaw_bridge.cli.subprocess.run") as mocked_run:
                mocked_run.side_effect = [
                    SimpleNamespace(returncode=0, stdout=" M foo.py\n", stderr=""),
                    SimpleNamespace(returncode=0, stdout="", stderr=""),
                    SimpleNamespace(returncode=0, stdout="[task-0001] commit\n", stderr=""),
                    SimpleNamespace(returncode=0, stdout="abc123\n", stderr=""),
                ]
                with redirect_stdout(io.StringIO()):
                    watch_workers(watch_args)

        registry = json.loads((docs_repo / ".openclaw-state" / "registry.json").read_text(encoding="utf-8"))["tasks"][0]
        self.assertEqual(registry["state"], "completed")
        self.assertEqual(registry["commit_sha"], "abc123")
        self.assertEqual(registry["commit_message"], "feat: finish task-0001")
        events = [
            json.loads(line)
            for line in (docs_repo / ".openclaw-state" / "watchdog-events.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual([event["event"] for event in events], ["completed"])

    def test_watch_workers_records_worker_published_gitlab_result_without_local_commit(self) -> None:
        docs_repo = self._create_docs_repo()
        self._dispatch_single_task(docs_repo)
        local_repo = self._configure_local_repo(docs_repo)
        registry_task = self._set_registry_task(
            docs_repo,
            state="in_progress",
            orx_task_id="orx-task-1",
            tmux_session="orx-orx-task-1",
        )
        worktree_path = (local_repo / registry_task["worktree_path"]).resolve()

        FakeOrchestrator.reset()
        FakeOrchestrator.task_by_id["orx-task-1"] = self._make_orx_task(
            task_id="orx-task-1",
            status=TaskStatus.SUCCEEDED,
            workspace_path=worktree_path,
            branch_name=registry_task["branch_name"],
            worktree_path=registry_task["worktree_path"],
        )
        FakeOrchestrator.result_by_id["orx-task-1"] = TaskResult(
            task_id="orx-task-1",
            status="completed",
            outcome="success",
            summary="Implemented and validated the task.",
            artifacts=[TaskArtifact(name="summary", path="summary.md")],
            error=None,
            metadata={
                "commit_sha": "abc123",
                "merge_request_url": "https://gitlab.example.com/group/repo/-/merge_requests/1",
            },
        )

        watch_args = SimpleNamespace(
            docs_repo=docs_repo,
            config=None,
            task_id="task-0001",
            state=None,
            restart=False,
            json=False,
            once=True,
        )

        with patch("openclaw_bridge.cli.Orchestrator", FakeOrchestrator):
            with patch("openclaw_bridge.cli.subprocess.run") as mocked_run:
                with redirect_stdout(io.StringIO()):
                    watch_workers(watch_args)
                mocked_run.assert_not_called()

        registry = json.loads((docs_repo / ".openclaw-state" / "registry.json").read_text(encoding="utf-8"))["tasks"][0]
        self.assertEqual(registry["state"], "completed")
        self.assertEqual(registry["commit_sha"], "abc123")
        self.assertEqual(registry["pr_url"], "https://gitlab.example.com/group/repo/-/merge_requests/1")
        self.assertIsNone(registry["commit_message"])
        self.assertEqual(registry["completion_summary"], "Implemented and validated the task.")
        self.assertIsNotNone(registry["completed_at"])
        events_path = docs_repo / ".openclaw-state" / "watchdog-events.jsonl"
        events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([event["event"] for event in events], ["completed"])
        self.assertEqual(events[0]["commit_sha"], "abc123")
        self.assertEqual(events[0]["pr_url"], "https://gitlab.example.com/group/repo/-/merge_requests/1")

        with patch("openclaw_bridge.cli.Orchestrator", FakeOrchestrator):
            with patch("openclaw_bridge.cli.subprocess.run") as mocked_run:
                with redirect_stdout(io.StringIO()):
                    watch_workers(watch_args)
                mocked_run.assert_not_called()

        events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(events), 1)

    def test_watch_workers_maps_worker_published_github_pull_request_url_to_pr_url(self) -> None:
        docs_repo = self._create_docs_repo()
        self._dispatch_single_task(docs_repo)
        local_repo = self._configure_local_repo(docs_repo)
        registry_task = self._set_registry_task(
            docs_repo,
            state="in_progress",
            orx_task_id="orx-task-1",
            tmux_session="orx-orx-task-1",
        )
        worktree_path = (local_repo / registry_task["worktree_path"]).resolve()

        FakeOrchestrator.reset()
        FakeOrchestrator.task_by_id["orx-task-1"] = self._make_orx_task(
            task_id="orx-task-1",
            status=TaskStatus.SUCCEEDED,
            workspace_path=worktree_path,
            branch_name=registry_task["branch_name"],
            worktree_path=registry_task["worktree_path"],
        )
        FakeOrchestrator.result_by_id["orx-task-1"] = TaskResult(
            task_id="orx-task-1",
            status="completed",
            outcome="success",
            summary="Opened a GitHub pull request.",
            artifacts=[],
            error=None,
            metadata={
                "commit_sha": "abc123",
                "pull_request_url": "https://github.com/example/repo/pull/1",
            },
        )

        watch_args = SimpleNamespace(
            docs_repo=docs_repo,
            config=None,
            task_id="task-0001",
            state=None,
            restart=False,
            json=False,
            once=True,
        )

        with patch("openclaw_bridge.cli.Orchestrator", FakeOrchestrator):
            with patch("openclaw_bridge.cli.subprocess.run") as mocked_run:
                with redirect_stdout(io.StringIO()):
                    watch_workers(watch_args)
                mocked_run.assert_not_called()

        registry = json.loads((docs_repo / ".openclaw-state" / "registry.json").read_text(encoding="utf-8"))["tasks"][0]
        self.assertEqual(registry["state"], "completed")
        self.assertEqual(registry["commit_sha"], "abc123")
        self.assertEqual(registry["pr_url"], "https://github.com/example/repo/pull/1")

    def test_watch_workers_sends_webhook_for_completed_event(self) -> None:
        docs_repo = self._create_docs_repo()
        self._dispatch_single_task(docs_repo)
        local_repo = self._configure_local_repo(docs_repo)
        self._configure_notifications(
            docs_repo,
            headers={"X-OpenClaw-Source": "watchdog", "X-Trace": "notify"},
            bearer_token_env="OPENCLAW_WEBHOOK_TOKEN",
        )
        task = self._set_registry_task(
            docs_repo,
            state="in_progress",
            orx_task_id="orx-task-1",
            tmux_session="orx-orx-task-1",
            launched_at="2026-04-12T00:00:00+00:00",
        )
        worktree_path = (local_repo / task["worktree_path"]).resolve()
        worktree_path.mkdir(parents=True, exist_ok=True)

        FakeOrchestrator.reset()
        FakeOrchestrator.task_by_id["orx-task-1"] = self._make_orx_task(
            task_id="orx-task-1",
            status=TaskStatus.SUCCEEDED,
            workspace_path=worktree_path,
            branch_name=task["branch_name"],
            worktree_path=task["worktree_path"],
        )
        FakeOrchestrator.result_by_id["orx-task-1"] = TaskResult(
            task_id="orx-task-1",
            status="completed",
            outcome="success",
            summary="Implemented and validated the task.",
            artifacts=[],
            error=None,
            metadata={"commit_message": "feat: finish task-0001"},
        )

        watch_args = SimpleNamespace(
            docs_repo=docs_repo,
            config=None,
            task_id="task-0001",
            state=None,
            restart=False,
            json=True,
            once=True,
        )

        with patch("openclaw_bridge.cli.Orchestrator", FakeOrchestrator):
            with patch("openclaw_bridge.cli.subprocess.run") as mocked_run:
                mocked_run.side_effect = [
                    SimpleNamespace(returncode=0, stdout=" M foo.py\n", stderr=""),
                    SimpleNamespace(returncode=0, stdout="", stderr=""),
                    SimpleNamespace(returncode=0, stdout="[task-0001] commit\n", stderr=""),
                    SimpleNamespace(returncode=0, stdout="abc123\n", stderr=""),
                ]
                with patch.dict("os.environ", {"OPENCLAW_WEBHOOK_TOKEN": "secret-token"}):
                    with patch("urllib.request.urlopen", return_value=self._FakeUrlopenResponse()) as mocked_urlopen:
                        output = io.StringIO()
                        with redirect_stdout(output):
                            watch_workers(watch_args)

        payload = json.loads(output.getvalue())
        self.assertEqual(payload["notifications_sent"], 1)
        request = mocked_urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://notify.example.com/openclaw")
        self.assertEqual(request.get_header("Authorization"), "Bearer secret-token")
        self.assertEqual(request.get_header("X-openclaw-source"), "watchdog")
        self.assertEqual(request.get_header("X-trace"), "notify")
        request_payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(request_payload["source"], "openclaw-mvp1")
        self.assertEqual(request_payload["docs_repo"], str(docs_repo.resolve()))
        self.assertEqual(
            request_payload["event_log"],
            str((docs_repo / ".openclaw-state" / "watchdog-events.jsonl").resolve()),
        )
        self.assertEqual([event["event"] for event in request_payload["events"]], ["completed"])
        self.assertEqual(request_payload["events"][0]["commit_sha"], "abc123")
        self.assertEqual(request_payload["events"][0]["source_title"], "Single Spec")
        self.assertEqual(request_payload["events"][0]["target_issue_url"], task["target_issue_url"])
        self.assertEqual(request_payload["events"][0]["branch_name"], task["branch_name"])

    def test_watch_workers_filters_webhook_events_but_keeps_full_local_log(self) -> None:
        docs_repo = self._create_docs_repo()
        self._dispatch_single_task(docs_repo)
        local_repo = self._configure_local_repo(docs_repo)
        self._configure_notifications(docs_repo, events=["restarted"])
        registry_task = self._set_registry_task(
            docs_repo,
            state="in_progress",
            orx_task_id="orx-task-1",
            tmux_session="orx-orx-task-1",
            launched_at="2026-04-12T00:00:00+00:00",
        )
        worktree_path = (local_repo / registry_task["worktree_path"]).resolve()

        watch_args = SimpleNamespace(
            docs_repo=docs_repo,
            config=None,
            task_id="task-0001",
            state=None,
            restart=True,
            json=True,
            once=True,
        )

        FakeOrchestrator.reset()
        FakeOrchestrator.start_task_ids = ["orx-task-2"]
        FakeOrchestrator.task_by_id["orx-task-1"] = self._make_orx_task(
            task_id="orx-task-1",
            status=TaskStatus.FAILED,
            workspace_path=worktree_path,
            branch_name=registry_task["branch_name"],
            worktree_path=registry_task["worktree_path"],
            retryable=True,
            failure_reason=TaskFailureReason.RUNTIME_ERROR,
            failure_message="worker transport stream disconnected",
        )
        FakeOrchestrator.task_by_id["orx-task-2"] = self._make_orx_task(
            task_id="orx-task-2",
            status=TaskStatus.RUNNING,
            workspace_path=worktree_path,
            branch_name=registry_task["branch_name"],
            worktree_path=registry_task["worktree_path"],
        )
        FakeOrchestrator.result_by_id["orx-task-1"] = TaskResult(
            task_id="orx-task-1",
            status="completed",
            outcome="failure",
            summary="worker transport stream disconnected",
            artifacts=[],
            error="worker transport stream disconnected",
            metadata={},
        )

        with patch("openclaw_bridge.cli.Orchestrator", FakeOrchestrator):
            with patch("urllib.request.urlopen", return_value=self._FakeUrlopenResponse()) as mocked_urlopen:
                output = io.StringIO()
                with redirect_stdout(output):
                    watch_workers(watch_args)

        payload = json.loads(output.getvalue())
        self.assertEqual(payload["notifications_sent"], 1)
        request = mocked_urlopen.call_args.args[0]
        request_payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual([event["event"] for event in request_payload["events"]], ["restarted"])
        local_events = [
            json.loads(line)
            for line in (docs_repo / ".openclaw-state" / "watchdog-events.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual([event["event"] for event in local_events], ["restartable", "restarted"])

    def test_watch_workers_reports_webhook_failures_without_blocking_main_flow(self) -> None:
        docs_repo = self._create_docs_repo()
        self._dispatch_single_task(docs_repo)
        local_repo = self._configure_local_repo(docs_repo)
        self._configure_notifications(docs_repo)
        task = self._set_registry_task(
            docs_repo,
            state="in_progress",
            orx_task_id="orx-task-1",
            tmux_session="orx-orx-task-1",
            launched_at="2026-04-12T00:00:00+00:00",
        )
        worktree_path = (local_repo / task["worktree_path"]).resolve()
        worktree_path.mkdir(parents=True, exist_ok=True)

        FakeOrchestrator.reset()
        FakeOrchestrator.task_by_id["orx-task-1"] = self._make_orx_task(
            task_id="orx-task-1",
            status=TaskStatus.SUCCEEDED,
            workspace_path=worktree_path,
            branch_name=task["branch_name"],
            worktree_path=task["worktree_path"],
        )
        FakeOrchestrator.result_by_id["orx-task-1"] = TaskResult(
            task_id="orx-task-1",
            status="completed",
            outcome="success",
            summary="Implemented and validated the task.",
            artifacts=[],
            error=None,
            metadata={"commit_message": "feat: finish task-0001"},
        )

        watch_args = SimpleNamespace(
            docs_repo=docs_repo,
            config=None,
            task_id="task-0001",
            state=None,
            restart=False,
            json=True,
            once=True,
        )

        with patch("openclaw_bridge.cli.Orchestrator", FakeOrchestrator):
            with patch("openclaw_bridge.cli.subprocess.run") as mocked_run:
                mocked_run.side_effect = [
                    SimpleNamespace(returncode=0, stdout=" M foo.py\n", stderr=""),
                    SimpleNamespace(returncode=0, stdout="", stderr=""),
                    SimpleNamespace(returncode=0, stdout="[task-0001] commit\n", stderr=""),
                    SimpleNamespace(returncode=0, stdout="abc123\n", stderr=""),
                ]
                with patch("urllib.request.urlopen", side_effect=URLError("webhook down")):
                    output = io.StringIO()
                    with redirect_stdout(output):
                        watch_workers(watch_args)

        payload = json.loads(output.getvalue())
        self.assertEqual(payload["committed"], 1)
        self.assertEqual(payload["notifications_sent"], 0)
        self.assertIn("webhook down", payload["notification_failures"][0])
        registry = json.loads((docs_repo / ".openclaw-state" / "registry.json").read_text(encoding="utf-8"))["tasks"][0]
        self.assertEqual(registry["state"], "completed")
        self.assertEqual(registry["commit_sha"], "abc123")
        self.assertTrue((docs_repo / ".openclaw-state" / "watchdog-events.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
