from __future__ import annotations

import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from orx.runtime import build_wrapped_command

from .models import TaskRecord
from .worker_models import resolve_explicit_worker_model


@dataclass(slots=True)
class LaunchRequest:
    task: TaskRecord
    docs_repo: Path
    repo_mapping: dict[str, object]
    launcher_config: dict[str, object]


@dataclass(slots=True)
class LaunchPlan:
    request: LaunchRequest
    repo_root: Path
    worktree_path: Path
    provider: "WorkerProvider"
    session_launcher: "SessionLauncher"


@dataclass(slots=True)
class LaunchResult:
    launched: bool
    already_running: bool


class WorkspacePreparer:
    def name(self) -> str:
        raise NotImplementedError

    def prepare(self, plan: LaunchPlan) -> Path:
        raise NotImplementedError


class GitWorktreePreparer(WorkspacePreparer):
    def name(self) -> str:
        return "git-worktree"

    def prepare(self, plan: LaunchPlan) -> Path:
        worktree_path = plan.worktree_path
        if worktree_path.exists():
            return worktree_path

        worktree_path.parent.mkdir(parents=True, exist_ok=True)
        base_branch = str(plan.request.launcher_config.get("base_branch") or "HEAD")
        result = subprocess.run(
            [
                "git",
                "-C",
                str(plan.repo_root),
                "worktree",
                "add",
                "-b",
                plan.request.task.branch_name,
                str(worktree_path),
                base_branch,
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "git worktree add failed")
        return worktree_path


class WorkspacePreparerRegistry:
    def __init__(self) -> None:
        self._preparers: dict[str, WorkspacePreparer] = {}

    def register(self, preparer: WorkspacePreparer) -> None:
        self._preparers[preparer.name()] = preparer

    def get(self, name: str) -> WorkspacePreparer:
        preparer = self._preparers.get(name)
        if preparer is None:
            raise ValueError(f"unsupported workspace strategy: {name}")
        return preparer


class WorkerProvider:
    def worker_type(self) -> str:
        raise NotImplementedError

    def binary_name(self) -> str:
        raise NotImplementedError

    def resolve_binary(self, launcher_config: dict[str, object]) -> str:
        return self.binary_name()

    def build_command(self, task: TaskRecord, launcher_config: dict[str, object], prompt_path: Path) -> str:
        raise NotImplementedError


class CodexWorkerProvider(WorkerProvider):
    def worker_type(self) -> str:
        return "codex"

    def binary_name(self) -> str:
        return "codex"

    def resolve_binary(self, launcher_config: dict[str, object]) -> str:
        configured_path = str(launcher_config.get("codex_cli_path") or "").strip()
        if configured_path:
            return configured_path
        bundled_binary = Path("/Applications/Codex.app/Contents/Resources/codex")
        if bundled_binary.exists():
            return str(bundled_binary)
        return self.binary_name()

    def build_command(self, task: TaskRecord, launcher_config: dict[str, object], prompt_path: Path) -> str:
        codex_binary = shlex.quote(self.resolve_binary(launcher_config))
        prompt_file = shlex.quote(str(prompt_path.resolve()))
        explicit_model = resolve_explicit_worker_model(worker_type=task.worker_type, worker_model=task.worker_model)
        effort = str(launcher_config.get("codex_reasoning_effort") or "high")
        effort_arg = shlex.quote(f"model_reasoning_effort={effort}")
        model_arg = f" --model {shlex.quote(explicit_model)}" if explicit_model else ""
        return (
            f"exec {codex_binary} exec --disable plugins --ephemeral{model_arg} -c {effort_arg} "
            "--dangerously-bypass-approvals-and-sandbox --skip-git-repo-check "
            f"- < {prompt_file}"
        )


class ClaudeWorkerProvider(WorkerProvider):
    def worker_type(self) -> str:
        return "claude"

    def binary_name(self) -> str:
        return "claude"

    def build_command(self, task: TaskRecord, launcher_config: dict[str, object], prompt_path: Path) -> str:
        prompt_file = shlex.quote(str(prompt_path.resolve()))
        model = shlex.quote(task.worker_model)
        return (
            f'PROMPT="$(cat {prompt_file})"; '
            f'exec claude --model {model} --dangerously-skip-permissions -p "$PROMPT"'
        )


class WorkerProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, WorkerProvider] = {}

    def register(self, provider: WorkerProvider) -> None:
        self._providers[provider.worker_type()] = provider

    def get(self, worker_type: str) -> WorkerProvider:
        provider = self._providers.get(worker_type)
        if provider is None:
            raise ValueError(f"unsupported worker_type: {worker_type}")
        return provider


class SessionLauncher:
    def name(self) -> str:
        raise NotImplementedError

    def session_exists(self, session_name: str) -> bool:
        raise NotImplementedError

    def start(self, session_name: str, cwd: Path, command: str) -> None:
        raise NotImplementedError

    def capture_output(self, session_name: str, lines: int = 120) -> str:
        raise NotImplementedError

    def stop(self, session_name: str) -> None:
        raise NotImplementedError


class TmuxSessionLauncher(SessionLauncher):
    def name(self) -> str:
        return "tmux"

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

    def capture_output(self, session_name: str, lines: int = 120) -> str:
        result = subprocess.run(
            ["tmux", "capture-pane", "-pt", session_name],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "tmux capture-pane failed")
        if lines <= 0:
            return ""
        captured_lines = result.stdout.splitlines()
        return "\n".join(captured_lines[-lines:])

    def stop(self, session_name: str) -> None:
        result = subprocess.run(
            ["tmux", "kill-session", "-t", session_name],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "tmux kill-session failed")


class SessionLauncherRegistry:
    def __init__(self) -> None:
        self._launchers: dict[str, SessionLauncher] = {}

    def register(self, launcher: SessionLauncher) -> None:
        self._launchers[launcher.name()] = launcher

    def get(self, name: str) -> SessionLauncher:
        launcher = self._launchers.get(name)
        if launcher is None:
            raise ValueError(f"unsupported session launcher: {name}")
        return launcher


class LaunchPreflight:
    def __init__(
        self,
        *,
        workspace_preparers: WorkspacePreparerRegistry,
        worker_providers: WorkerProviderRegistry,
        session_launchers: SessionLauncherRegistry,
    ) -> None:
        self._workspace_preparers = workspace_preparers
        self._worker_providers = worker_providers
        self._session_launchers = session_launchers

    def build_plan(self, request: LaunchRequest) -> LaunchPlan:
        repo_root = resolve_local_repo_path(
            target_repo=request.task.target_repo,
            mapping=request.repo_mapping,
            launcher=request.launcher_config,
            docs_repo=request.docs_repo,
        )
        prompt_path = Path(request.task.worker_prompt_path).resolve()
        if not prompt_path.exists():
            raise FileNotFoundError(f"worker prompt does not exist: {prompt_path}")

        ensure_command_exists("git", "git is required for launch-workers")
        ensure_command_exists("tmux", "tmux is required for launch-workers")

        provider = self._worker_providers.get(request.task.worker_type)
        provider_binary = provider.resolve_binary(request.launcher_config)
        ensure_command_exists(
            provider_binary,
            f"{provider.binary_name()} CLI is required for worker_type={request.task.worker_type}",
        )

        workspace_strategy = str(request.launcher_config.get("workspace_strategy") or "git-worktree")
        self._workspace_preparers.get(workspace_strategy)

        session_launcher_name = str(request.launcher_config.get("session_launcher") or "tmux")
        session_launcher = self._session_launchers.get(session_launcher_name)

        worktree_path = (repo_root / request.task.worktree_path).resolve()
        return LaunchPlan(
            request=request,
            repo_root=repo_root,
            worktree_path=worktree_path,
            provider=provider,
            session_launcher=session_launcher,
        )


class RuntimeLauncher:
    def __init__(
        self,
        *,
        preflight: LaunchPreflight,
        workspace_preparers: WorkspacePreparerRegistry,
    ) -> None:
        self._preflight = preflight
        self._workspace_preparers = workspace_preparers

    def launch(self, request: LaunchRequest) -> LaunchResult:
        plan = self._preflight.build_plan(request)
        workspace_strategy = str(request.launcher_config.get("workspace_strategy") or "git-worktree")
        preparer = self._workspace_preparers.get(workspace_strategy)
        worktree_path = preparer.prepare(plan)
        if plan.session_launcher.session_exists(request.task.tmux_session):
            return LaunchResult(launched=False, already_running=True)

        worker_command = plan.provider.build_command(
            request.task,
            request.launcher_config,
            Path(request.task.worker_prompt_path),
        )
        stdout_log_path, stderr_log_path, runtime_meta_path = _runtime_artifact_paths(request.task)
        command = build_wrapped_command(
            worker_command=worker_command,
            stdout_log_path=stdout_log_path,
            stderr_log_path=stderr_log_path,
            runtime_meta_path=runtime_meta_path,
        )
        plan.session_launcher.start(request.task.tmux_session, worktree_path, command)
        post_launch_check_seconds = float(request.launcher_config.get("post_launch_check_seconds") or 2.0)
        if post_launch_check_seconds > 0:
            time.sleep(post_launch_check_seconds)
        if not plan.session_launcher.session_exists(request.task.tmux_session):
            raise RuntimeError(
                f"worker session {request.task.tmux_session} exited immediately after launch"
            )
        return LaunchResult(launched=True, already_running=False)


def build_session_launcher_registry() -> SessionLauncherRegistry:
    session_launchers = SessionLauncherRegistry()
    session_launchers.register(TmuxSessionLauncher())
    return session_launchers


def resolve_session_launcher(launcher_config: dict[str, object]) -> SessionLauncher:
    session_launcher_name = str(launcher_config.get("session_launcher") or "tmux")
    return build_session_launcher_registry().get(session_launcher_name)


def build_runtime_launcher() -> RuntimeLauncher:
    workspace_preparers = WorkspacePreparerRegistry()
    workspace_preparers.register(GitWorktreePreparer())

    worker_providers = WorkerProviderRegistry()
    worker_providers.register(CodexWorkerProvider())
    worker_providers.register(ClaudeWorkerProvider())

    session_launchers = build_session_launcher_registry()

    preflight = LaunchPreflight(
        workspace_preparers=workspace_preparers,
        worker_providers=worker_providers,
        session_launchers=session_launchers,
    )
    return RuntimeLauncher(preflight=preflight, workspace_preparers=workspace_preparers)


def _runtime_artifact_paths(task: TaskRecord) -> tuple[Path, Path, Path]:
    artifact_dir = Path(task.worker_prompt_path).resolve().parent
    artifact_dir.mkdir(parents=True, exist_ok=True)
    stdout_log_path = artifact_dir / "stdout.log"
    stderr_log_path = artifact_dir / "stderr.log"
    runtime_meta_path = artifact_dir / "runtime.json"
    stdout_log_path.touch(exist_ok=True)
    stderr_log_path.touch(exist_ok=True)
    return stdout_log_path, stderr_log_path, runtime_meta_path


def resolve_local_repo_path(
    *,
    target_repo: str,
    mapping: dict[str, object],
    launcher: dict[str, object],
    docs_repo: Path,
) -> Path:
    candidates: list[Path] = []
    local_path = str(mapping.get("local_path") or "").strip()
    if local_path:
        candidates.append(Path(local_path).expanduser())

    repo_roots = launcher.get("repo_roots", [])
    if isinstance(repo_roots, list):
        for root in repo_roots:
            text = str(root).strip()
            if text:
                candidates.append(Path(text).expanduser() / target_repo)

    candidates.extend(
        [
            docs_repo.parent / target_repo,
            docs_repo.parent / "repos" / target_repo,
            docs_repo.parent / "refactor-bridge" / target_repo,
            docs_repo.parent.parent / target_repo,
        ]
    )

    for candidate in candidates:
        resolved = candidate.resolve()
        if (resolved / ".git").exists():
            return resolved

    searched = ", ".join(str(path.resolve()) for path in candidates)
    raise FileNotFoundError(
        f"could not locate local repo for {target_repo}; checked {searched}. "
        "Set repo_mappings.<repo>.local_path or launcher.repo_roots."
    )


def ensure_command_exists(name: str, error_message: str) -> None:
    if not command_exists(name):
        raise RuntimeError(error_message)


def command_exists(name: str) -> bool:
    result = subprocess.run(
        ["sh", "-lc", f"command -v {name} >/dev/null 2>&1"],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0
