from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError

_DEFAULT_NOTIFICATION_EVENTS = (
    "completed",
    "blocked",
    "restartable",
    "restarted",
)


@dataclass(slots=True)
class NotificationSettings:
    webhook_url: str
    events: tuple[str, ...]
    timeout_seconds: float
    headers: dict[str, str]
    bearer_token_env: str


def send_watch_notifications(
    *,
    config: dict[str, object],
    docs_repo: Path,
    events_path: Path,
    events: list[dict[str, object]],
) -> dict[str, object]:
    try:
        settings = _load_notification_settings(config)
    except ValueError as exc:
        return {
            "notifications_sent": 0,
            "notification_failures": [str(exc)],
        }

    if settings is None:
        return {
            "notifications_sent": 0,
            "notification_failures": [],
        }

    selected_events = [
        event for event in events if str(event.get("event") or "") in settings.events
    ]
    if not selected_events:
        return {
            "notifications_sent": 0,
            "notification_failures": [],
        }

    payload = {
        "source": "openclaw-mvp1",
        "docs_repo": str(docs_repo),
        "event_log": str(events_path),
        "sent_at": _timestamp(),
        "events": selected_events,
    }
    headers = {
        "Content-Type": "application/json",
        **settings.headers,
    }

    if settings.bearer_token_env:
        token = os.getenv(settings.bearer_token_env, "").strip()
        if not token:
            return {
                "notifications_sent": 0,
                "notification_failures": [
                    f"notifications.bearer_token_env is set but {settings.bearer_token_env} is missing or empty"
                ],
            }
        headers["Authorization"] = f"Bearer {token}"

    request = urllib.request.Request(
        settings.webhook_url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=settings.timeout_seconds) as response:
            response.read()
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip()
        return {
            "notifications_sent": 0,
            "notification_failures": [
                detail or f"webhook request failed with HTTP {exc.code}"
            ],
        }
    except URLError as exc:
        return {
            "notifications_sent": 0,
            "notification_failures": [f"webhook request failed: {exc.reason}"],
        }

    return {
        "notifications_sent": 1,
        "notification_failures": [],
    }


def _load_notification_settings(
    config: dict[str, object],
) -> NotificationSettings | None:
    raw = config.get("notifications")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("config.notifications must be an object when present")

    webhook_url = str(raw.get("webhook_url") or "").strip()
    if not webhook_url:
        return None

    raw_events = raw.get("events", list(_DEFAULT_NOTIFICATION_EVENTS))
    if not isinstance(raw_events, list) or any(not isinstance(item, str) for item in raw_events):
        raise ValueError("notifications.events must be a list of strings")
    events = tuple(item.strip() for item in raw_events if item.strip())
    if not events:
        return None

    raw_timeout = raw.get("timeout_seconds", 5)
    if not isinstance(raw_timeout, (int, float)) or raw_timeout <= 0:
        raise ValueError("notifications.timeout_seconds must be a positive number")

    raw_headers = raw.get("headers", {})
    if not isinstance(raw_headers, dict):
        raise ValueError("notifications.headers must be an object")
    headers = {str(key): str(value) for key, value in raw_headers.items()}

    raw_bearer_token_env = raw.get("bearer_token_env", "")
    if not isinstance(raw_bearer_token_env, str):
        raise ValueError("notifications.bearer_token_env must be a string")

    return NotificationSettings(
        webhook_url=webhook_url,
        events=events,
        timeout_seconds=float(raw_timeout),
        headers=headers,
        bearer_token_env=raw_bearer_token_env.strip(),
    )


def _timestamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()
