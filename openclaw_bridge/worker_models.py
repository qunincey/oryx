from __future__ import annotations

DEFAULT_CODEX_WORKER_MODEL = "default"

_LEGACY_CODEX_MODEL_ALIASES = {
    "gpt-5-codex": DEFAULT_CODEX_WORKER_MODEL,
}


def normalize_worker_model(*, worker_type: str, worker_model: str | None) -> str:
    raw = str(worker_model or "").strip()
    if worker_type != "codex":
        return raw
    if not raw:
        return DEFAULT_CODEX_WORKER_MODEL
    return _LEGACY_CODEX_MODEL_ALIASES.get(raw.lower(), raw)


def resolve_explicit_worker_model(*, worker_type: str, worker_model: str | None) -> str | None:
    normalized = normalize_worker_model(worker_type=worker_type, worker_model=worker_model)
    if worker_type == "codex" and normalized == DEFAULT_CODEX_WORKER_MODEL:
        return None
    return normalized or None
