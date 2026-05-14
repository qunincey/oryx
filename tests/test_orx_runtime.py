from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from orx.models import TaskRequest
from orx.runtime import WorkspaceManager


class WorkspaceManagerTests(unittest.TestCase):
    def _root(self) -> Path:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        return Path(temp_dir.name)

    def test_in_place_strategy_uses_request_cwd(self) -> None:
        root = self._root()
        request = TaskRequest(
            worker_type="codex",
            prompt="Implement the change",
            cwd=root,
            workspace_strategy="in_place",
        )
        manager = WorkspaceManager()

        prepared = manager.prepare(request, task_id="task-123", data_root=root / ".orx")

        self.assertEqual(prepared.path, root.resolve())
        self.assertIsNone(prepared.worktree_path)

    def test_git_worktree_strategy_runs_git_worktree_add(self) -> None:
        root = self._root()
        data_root = root / ".orx"
        commands: list[list[str]] = []

        def fake_run(args: list[str], **_kwargs: object) -> SimpleNamespace:
            commands.append(args)
            return SimpleNamespace(returncode=0, stderr="", stdout="")

        request = TaskRequest(
            worker_type="codex",
            prompt="Implement the change",
            cwd=root,
            workspace_strategy="git_worktree",
        )
        manager = WorkspaceManager(command_runner=fake_run)

        prepared = manager.prepare(request, task_id="task-123", data_root=data_root)

        self.assertEqual(commands[0][:4], ["git", "-C", str(root), "worktree"])
        self.assertEqual(prepared.branch_name, "orx/task-123")
        self.assertEqual(prepared.worktree_path, (data_root / "worktrees" / "task-123").resolve())

    def test_git_worktree_strategy_reuses_existing_worktree_without_recreating_branch(self) -> None:
        root = self._root()
        data_root = root / ".orx"
        existing_worktree = (data_root / "worktrees" / "task-123").resolve()
        existing_worktree.mkdir(parents=True)
        commands: list[list[str]] = []

        def fake_run(args: list[str], **_kwargs: object) -> SimpleNamespace:
            commands.append(args)
            return SimpleNamespace(returncode=0, stderr="", stdout="")

        request = TaskRequest(
            worker_type="codex",
            prompt="Implement the change",
            cwd=root,
            workspace_strategy="git_worktree",
        )
        manager = WorkspaceManager(command_runner=fake_run)

        prepared = manager.prepare(request, task_id="task-123", data_root=data_root)

        self.assertEqual(commands, [])
        self.assertEqual(prepared.path, existing_worktree)
        self.assertEqual(prepared.branch_name, "orx/task-123")
        self.assertEqual(prepared.worktree_path, existing_worktree)


if __name__ == "__main__":
    unittest.main()
