from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MONITOR_CONFIG_PATH = Path.home() / ".openclaw-monitor" / "config.json"
DEFAULT_MONITOR_RUNTIME_DIR = ".openclaw-state"
DEFAULT_POLL_INTERVAL_SECONDS = 3
DEFAULT_MONITOR_CONFIG_TEMPLATE = {
    "projects": [],
    "poll_interval_seconds": DEFAULT_POLL_INTERVAL_SECONDS,
}


@dataclass(frozen=True, slots=True)
class MonitorProject:
    id: str
    name: str
    runtime_root: Path

    @property
    def openclaw_state_root(self) -> Path:
        return self.runtime_root.parent

    @property
    def docs_repo(self) -> Path | None:
        candidate = self.openclaw_state_root.parent
        if (candidate / ".openclaw").is_dir():
            return candidate
        return None


@dataclass(frozen=True, slots=True)
class MonitorConfig:
    projects: tuple[MonitorProject, ...]
    poll_interval_seconds: int = DEFAULT_POLL_INTERVAL_SECONDS
    config_path: Path | None = None


def ensure_monitor_config_path(path: Path | None = None) -> tuple[Path, bool]:
    config_path = Path(path or DEFAULT_MONITOR_CONFIG_PATH).expanduser().resolve()
    if config_path.exists():
        return config_path, False
    if path is not None:
        raise FileNotFoundError(f"monitor config not found: {config_path}")
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(DEFAULT_MONITOR_CONFIG_TEMPLATE, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return config_path, True


def load_monitor_config(path: Path | None = None) -> MonitorConfig:
    config_path, _ = ensure_monitor_config_path(path)

    payload = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("monitor config must be a JSON object")

    raw_projects = payload.get("projects")
    if not isinstance(raw_projects, list):
        raise ValueError("monitor config.projects must be a list")

    poll_interval = payload.get("poll_interval_seconds", DEFAULT_POLL_INTERVAL_SECONDS)
    if not isinstance(poll_interval, int) or poll_interval <= 0:
        raise ValueError("monitor config.poll_interval_seconds must be a positive integer")

    seen_ids: set[str] = set()
    seen_runtime_roots: set[str] = set()
    projects: list[MonitorProject] = []
    for raw in raw_projects:
        if not isinstance(raw, dict):
            raise ValueError("each monitor project must be an object")
        project_id = str(raw.get("id") or "").strip()
        name = str(raw.get("name") or "").strip()

        if not project_id:
            raise ValueError("monitor project id is required")
        if project_id in seen_ids:
            raise ValueError(f"duplicate project id: {project_id}")
        if not name:
            raise ValueError(f"monitor project {project_id} is missing name")

        runtime_root = _resolve_runtime_root_from_raw(raw, project_id=project_id)
        runtime_root_key = str(runtime_root)
        if runtime_root_key in seen_runtime_roots:
            raise ValueError(f"duplicate runtime_root: {runtime_root}")

        projects.append(
            MonitorProject(
                id=project_id,
                name=name,
                runtime_root=runtime_root,
            )
        )
        seen_ids.add(project_id)
        seen_runtime_roots.add(runtime_root_key)

    return MonitorConfig(
        projects=tuple(projects),
        poll_interval_seconds=poll_interval,
        config_path=config_path,
    )


def add_project_to_monitor_config(
    path: Path,
    *,
    runtime_root: Path | None = None,
    docs_repo: Path | None = None,
    name: str | None = None,
    project_id: str | None = None,
    runtime_dir: str = DEFAULT_MONITOR_RUNTIME_DIR,
) -> MonitorProject:
    config_path, _ = ensure_monitor_config_path(path)
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("monitor config must be a JSON object")

    raw_projects = payload.get("projects")
    if not isinstance(raw_projects, list):
        raise ValueError("monitor config.projects must be a list")

    runtime_root_path = _coerce_runtime_root(
        runtime_root=runtime_root,
        docs_repo=docs_repo,
        runtime_dir=runtime_dir,
        error_prefix="invalid",
    )

    existing_ids = {str(item.get("id") or "").strip() for item in raw_projects if isinstance(item, dict)}
    existing_runtime_roots = {
        str(_resolve_runtime_root_from_raw(item, project_id=str(item.get("id") or "<unknown>")))
        for item in raw_projects
        if isinstance(item, dict)
    }
    if str(runtime_root_path) in existing_runtime_roots:
        raise ValueError(f"runtime_root already registered: {runtime_root_path}")

    normalized_name = (name or _default_project_name(runtime_root_path)).strip()
    if not normalized_name:
        normalized_name = "Project"

    if project_id is not None and project_id.strip():
        normalized_id = project_id.strip()
        if normalized_id in existing_ids:
            raise ValueError(f"duplicate project id: {normalized_id}")
    else:
        normalized_id = _allocate_project_id(_default_project_seed(runtime_root_path), existing_ids)

    project_payload = {
        "id": normalized_id,
        "name": normalized_name,
        "runtime_root": str(runtime_root_path),
    }
    raw_projects.append(project_payload)
    payload["projects"] = raw_projects
    payload.setdefault("poll_interval_seconds", DEFAULT_POLL_INTERVAL_SECONDS)
    config_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return MonitorProject(
        id=normalized_id,
        name=normalized_name,
        runtime_root=runtime_root_path,
    )


def _resolve_runtime_root_from_raw(raw: dict[str, object], *, project_id: str) -> Path:
    runtime_root_value = str(raw.get("runtime_root") or "").strip()
    if runtime_root_value:
        return _normalize_runtime_root(Path(runtime_root_value), error_prefix=f"monitor project {project_id} has invalid")

    docs_repo_value = str(raw.get("docs_repo") or "").strip()
    runtime_dir = str(raw.get("runtime_dir") or DEFAULT_MONITOR_RUNTIME_DIR).strip()
    if not docs_repo_value:
        raise ValueError(f"monitor project {project_id} is missing runtime_root")
    docs_repo = Path(docs_repo_value).expanduser().resolve()
    if not docs_repo.exists() or not docs_repo.is_dir():
        raise ValueError(f"monitor project {project_id} has invalid docs_repo: {docs_repo}")
    if not (docs_repo / ".openclaw").is_dir():
        raise ValueError(f"monitor project {project_id} docs_repo is not an OpenClaw docs repo: {docs_repo}")
    if not runtime_dir:
        raise ValueError(f"monitor project {project_id} has invalid runtime_dir")
    return _normalize_runtime_root(
        docs_repo / runtime_dir / "orx",
        error_prefix=f"monitor project {project_id} has invalid",
    )


def _coerce_runtime_root(
    *,
    runtime_root: Path | None,
    docs_repo: Path | None,
    runtime_dir: str,
    error_prefix: str,
) -> Path:
    if runtime_root is not None:
        return _normalize_runtime_root(runtime_root, error_prefix=f"{error_prefix} runtime_root")

    if docs_repo is None:
        raise ValueError("runtime_root is required")

    docs_repo_path = Path(docs_repo).expanduser().resolve()
    if not docs_repo_path.exists() or not docs_repo_path.is_dir():
        raise ValueError(f"{error_prefix} docs_repo: {docs_repo_path}")
    if not (docs_repo_path / ".openclaw").is_dir():
        raise ValueError(f"docs_repo is not an OpenClaw docs repo: {docs_repo_path}")

    normalized_runtime_dir = str(runtime_dir or DEFAULT_MONITOR_RUNTIME_DIR).strip()
    if not normalized_runtime_dir:
        raise ValueError("runtime_dir must not be empty")
    return _normalize_runtime_root(
        docs_repo_path / normalized_runtime_dir / "orx",
        error_prefix="invalid runtime_root",
    )


def _normalize_runtime_root(path: Path, *, error_prefix: str) -> Path:
    runtime_root = Path(path).expanduser().resolve()
    if runtime_root.exists() and not runtime_root.is_dir():
        raise ValueError(f"{error_prefix}: {runtime_root}")
    return runtime_root


def _default_project_name(runtime_root: Path) -> str:
    docs_repo = _infer_docs_repo(runtime_root)
    if docs_repo is not None:
        return docs_repo.name
    return runtime_root.parent.parent.name or runtime_root.name


def _default_project_seed(runtime_root: Path) -> str:
    docs_repo = _infer_docs_repo(runtime_root)
    if docs_repo is not None:
        return docs_repo.name
    return runtime_root.parent.parent.name or runtime_root.name


def _infer_docs_repo(runtime_root: Path) -> Path | None:
    candidate = runtime_root.parent.parent
    if (candidate / ".openclaw").is_dir():
        return candidate
    return None


def _allocate_project_id(seed: str, existing_ids: set[str]) -> str:
    base = _slugify(seed) or "project"
    candidate = base
    index = 2
    while candidate in existing_ids:
        candidate = f"{base}-{index}"
        index += 1
    return candidate


def _slugify(value: str) -> str:
    lowered = value.strip().lower()
    result: list[str] = []
    last_was_dash = False
    for char in lowered:
        if char.isalnum():
            result.append(char)
            last_was_dash = False
            continue
        if char in {" ", "_", "-", "."} and not last_was_dash and result:
            result.append("-")
            last_was_dash = True
    return "".join(result).strip("-")
