from __future__ import annotations

import json
import shlex
import subprocess
import sys
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .models import TaskRequest


@dataclass(slots=True)
class PreparedWorkspace:
    path: Path
    branch_name: str | None
    worktree_path: Path | None


class SessionLauncher(ABC):
    @abstractmethod
    def session_exists(self, session_name: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    def start(self, session_name: str, cwd: Path, command: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def send_interrupt(self, session_name: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def kill_session(self, session_name: str) -> None:
        raise NotImplementedError


class TmuxSessionLauncher(SessionLauncher):
    def session_exists(self, session_name: str) -> bool:
        result = subprocess.run(
            ["tmux", "has-session", "-t", session_name],
            check=False,
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

    def start(self, session_name: str, cwd: Path, command: str) -> None:
        result = subprocess.run(
            [
                "tmux",
                "new-session",
                "-d",
                "-s",
                session_name,
                "-c",
                str(cwd),
                "sh",
                "-lc",
                command,
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "tmux new-session failed")

    def send_interrupt(self, session_name: str) -> None:
        result = subprocess.run(
            ["tmux", "send-keys", "-t", session_name, "C-c"],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "tmux send-keys failed")

    def kill_session(self, session_name: str) -> None:
        result = subprocess.run(
            ["tmux", "kill-session", "-t", session_name],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0 and "can't find session" not in (result.stderr or result.stdout).lower():
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "tmux kill-session failed")


class WorkspaceManager:
    def __init__(self, *, command_runner: Callable[..., object] | None = None) -> None:
        self._command_runner = command_runner or subprocess.run

    def resolve(self, request: TaskRequest, *, task_id: str, data_root: Path) -> PreparedWorkspace:
        if request.workspace_strategy == "in_place":
            return PreparedWorkspace(path=request.cwd.resolve(), branch_name=request.branch_name, worktree_path=None)
        if request.workspace_strategy != "git_worktree":
            raise ValueError(f"unsupported workspace_strategy: {request.workspace_strategy}")
        branch_name = request.branch_name or f"orx/{task_id}"
        if request.worktree_path is None:
            worktree_path = (data_root / "worktrees" / task_id).resolve()
        elif request.worktree_path.is_absolute():
            worktree_path = request.worktree_path.resolve()
        else:
            worktree_path = (request.cwd / request.worktree_path).resolve()
        return PreparedWorkspace(path=worktree_path, branch_name=branch_name, worktree_path=worktree_path)

    def prepare(self, request: TaskRequest, *, task_id: str, data_root: Path) -> PreparedWorkspace:
        workspace = self.resolve(request, task_id=task_id, data_root=data_root)
        if request.workspace_strategy == "in_place":
            if not workspace.path.exists():
                raise RuntimeError(f"cwd does not exist: {workspace.path}")
            return workspace
        if workspace.path.exists():
            return workspace
        workspace.path.parent.mkdir(parents=True, exist_ok=True)
        result = self._command_runner(
            [
                "git",
                "-C",
                str(request.cwd),
                "worktree",
                "add",
                "-b",
                str(workspace.branch_name),
                str(workspace.path),
                "HEAD",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "git worktree add failed")
        return workspace


def build_wrapped_command(
    *,
    worker_command: str,
    stdout_log_path: Path,
    stderr_log_path: Path,
    runtime_meta_path: Path,
) -> str:
    wrapper_path = Path(__file__).with_name("worker_wrapper.py").resolve()
    return (
        f"exec {shlex.quote(sys.executable)} {shlex.quote(str(wrapper_path))} "
        f"--stdout-log {shlex.quote(str(stdout_log_path))} "
        f"--stderr-log {shlex.quote(str(stderr_log_path))} "
        f"--runtime-meta {shlex.quote(str(runtime_meta_path))} "
        f"--shell-command {shlex.quote(worker_command)}"
    )


def read_runtime_meta(runtime_meta_path: Path) -> dict[str, object]:
    if not runtime_meta_path.exists():
        return {}
    try:
        raw = json.loads(runtime_meta_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    if not isinstance(raw, dict):
        return {}
    return dict(raw)
