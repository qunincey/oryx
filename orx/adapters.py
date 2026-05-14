from __future__ import annotations

import shlex
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from .artifacts import result_contract_text
from .models import TaskRequest


@dataclass(slots=True)
class LaunchContext:
    task_id: str
    request: TaskRequest
    task_dir: Path
    workspace_path: Path
    prompt_path: Path
    result_path: Path


class WorkerAdapter(ABC):
    @abstractmethod
    def worker_type(self) -> str:
        raise NotImplementedError

    def default_result_contract(self, result_path: Path) -> str:
        return result_contract_text(result_path)

    def build_prompt(self, request: TaskRequest, task_dir: Path) -> str:
        result_path = task_dir / "result.json"
        return (
            f"{request.prompt.rstrip()}\n\n"
            "----\n"
            f"{self.default_result_contract(result_path)}\n"
        )

    @abstractmethod
    def build_launch_command(self, context: LaunchContext) -> str:
        raise NotImplementedError


class CodexWorkerAdapter(WorkerAdapter):
    def worker_type(self) -> str:
        return "codex"

    def build_launch_command(self, context: LaunchContext) -> str:
        model_arg = ""
        if context.request.model:
            model_arg = f" --model {shlex.quote(context.request.model)}"
        prompt_file = shlex.quote(str(context.prompt_path))
        return (
            "exec codex exec --skip-git-repo-check "
            "--dangerously-bypass-approvals-and-sandbox"
            f"{model_arg} - < {prompt_file}"
        )


class ClaudeWorkerAdapter(WorkerAdapter):
    def worker_type(self) -> str:
        return "claude"

    def build_launch_command(self, context: LaunchContext) -> str:
        model_arg = ""
        if context.request.model:
            model_arg = f" --model {shlex.quote(context.request.model)}"
        prompt_file = shlex.quote(str(context.prompt_path))
        return (
            f'PROMPT="$(cat {prompt_file})"; '
            f'exec claude --dangerously-skip-permissions{model_arg} -p "$PROMPT"'
        )


def default_adapters() -> dict[str, WorkerAdapter]:
    adapters: dict[str, WorkerAdapter] = {}
    for adapter in (CodexWorkerAdapter(), ClaudeWorkerAdapter()):
        adapters[adapter.worker_type()] = adapter
    return adapters
