from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from orx.adapters import ClaudeWorkerAdapter, CodexWorkerAdapter, LaunchContext
from orx.models import TaskRequest


class AdapterTests(unittest.TestCase):
    def _build_context(self, worker_type: str) -> LaunchContext:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        root = Path(temp_dir.name)
        task_dir = root / "tasks" / "task-123"
        task_dir.mkdir(parents=True)
        prompt_path = task_dir / "prompt.txt"
        result_path = task_dir / "result.json"
        prompt_path.write_text("implement the change", encoding="utf-8")
        request = TaskRequest(
            worker_type=worker_type,
            prompt="implement the change",
            cwd=root,
            model="test-model",
        )
        return LaunchContext(
            task_id="task-123",
            request=request,
            task_dir=task_dir,
            workspace_path=root,
            prompt_path=prompt_path,
            result_path=result_path,
        )

    def test_codex_adapter_builds_exec_command_with_prompt_path_and_model(self) -> None:
        context = self._build_context("codex")

        command = CodexWorkerAdapter().build_launch_command(context)

        self.assertIn("codex", command)
        self.assertIn("test-model", command)
        self.assertIn(str(context.prompt_path), command)

    def test_claude_adapter_builds_exec_command_with_prompt_path_and_model(self) -> None:
        context = self._build_context("claude")

        command = ClaudeWorkerAdapter().build_launch_command(context)

        self.assertIn("claude", command)
        self.assertIn("test-model", command)
        self.assertIn(str(context.prompt_path), command)

    def test_adapter_prompt_includes_result_contract(self) -> None:
        context = self._build_context("codex")
        adapter = CodexWorkerAdapter()

        prompt = adapter.build_prompt(context.request, context.task_dir)

        self.assertIn(context.request.prompt, prompt)
        self.assertIn(str(context.result_path), prompt)
        self.assertIn('"status": "completed"', prompt)
        self.assertIn('"outcome"', prompt)


if __name__ == "__main__":
    unittest.main()
