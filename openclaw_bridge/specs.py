from __future__ import annotations

import re
from pathlib import Path

from .models import ApprovedSpec, RepoScope

SECTION_ALIASES = {
    "background": "background",
    "goal": "goal",
    "agreed scope": "agreed_scope",
    "agreed_scope": "agreed_scope",
    "non-goals": "non_goals",
    "non goals": "non_goals",
    "non_goals": "non_goals",
    "acceptance criteria": "acceptance_criteria",
    "impacted modules": "impacted_modules",
    "impacted_modules": "impacted_modules",
    "repo-specific scope": "repo_specific_scope",
    "repo specific scope": "repo_specific_scope",
    "repo scope": "repo_specific_scope",
    "repository scope": "repo_specific_scope",
    "repository-specific scope": "repo_specific_scope",
    "implementation scope": "repo_specific_scope",
    "references": "references",
    "risks": "risks",
    "背景": "background",
    "目标": "goal",
    "约定范围": "agreed_scope",
    "非目标": "non_goals",
    "验收标准": "acceptance_criteria",
    "影响模块": "impacted_modules",
    "仓库实施范围": "repo_specific_scope",
    "仓库范围": "repo_specific_scope",
    "按仓库拆解": "repo_specific_scope",
    "参考": "references",
    "参考资料": "references",
    "风险": "risks",
}

REPO_SCOPE_FIELD_ALIASES = {
    "summary": "summary",
    "why": "summary",
    "why this repo is affected": "summary",
    "reason": "summary",
    "摘要": "summary",
    "原因": "summary",
    "影响原因": "summary",
    "modules": "modules",
    "module": "modules",
    "模块": "modules",
    "required changes": "required_changes",
    "required change": "required_changes",
    "change": "required_changes",
    "changes": "required_changes",
    "specific modification": "required_changes",
    "specific modifications": "required_changes",
    "修改": "required_changes",
    "修改项": "required_changes",
    "具体修改": "required_changes",
    "boundaries": "boundaries",
    "boundary": "boundaries",
    "scope boundary": "boundaries",
    "边界": "boundaries",
    "范围边界": "boundaries",
    "acceptance": "acceptance_criteria",
    "acceptance criteria": "acceptance_criteria",
    "acceptance note": "acceptance_criteria",
    "acceptance notes": "acceptance_criteria",
    "验收": "acceptance_criteria",
    "验收标准": "acceptance_criteria",
    "risks": "risks",
    "risk": "risks",
    "风险": "risks",
    "references": "references",
    "reference": "references",
    "参考": "references",
    "参考资料": "references",
    "notes": "notes",
    "note": "notes",
    "备注": "notes",
}


class SpecValidationError(ValueError):
    """Raised when a markdown requirement document is incomplete."""


def parse_markdown_spec(path: Path) -> ApprovedSpec:
    raw = path.read_text(encoding="utf-8")
    metadata, body = _extract_frontmatter(raw)
    raw_sections = _extract_raw_sections(body)
    sections = {key: _normalize_section(lines) for key, lines in raw_sections.items()}

    title = _coerce_string(metadata.get("title")) or _extract_h1(body)
    status = (_coerce_string(metadata.get("status")) or "").strip()
    affected_repos = _coerce_list(metadata.get("affected_repos")) or _coerce_list(
        sections.get("affected_repos")
    )
    references = _coerce_list(metadata.get("references")) or _coerce_list(
        sections.get("references")
    )
    background = _coerce_string(sections.get("background")) or _coerce_string(
        metadata.get("background")
    )
    goal = _coerce_string(sections.get("goal")) or _coerce_string(metadata.get("goal"))
    agreed_scope = _coerce_list(sections.get("agreed_scope")) or _coerce_list(
        metadata.get("agreed_scope")
    )
    non_goals = _coerce_list(sections.get("non_goals")) or _coerce_list(
        metadata.get("non_goals")
    )
    acceptance_criteria = _coerce_list(
        sections.get("acceptance_criteria")
    ) or _coerce_list(metadata.get("acceptance_criteria"))
    impacted_modules = _coerce_list(sections.get("impacted_modules")) or _coerce_list(
        metadata.get("impacted_modules")
    )
    risks = _coerce_list(sections.get("risks")) or _coerce_list(metadata.get("risks"))
    repo_scopes = _extract_repo_scopes(raw_sections.get("repo_specific_scope", []))

    missing: list[str] = []
    if not title:
        missing.append("title")
    if not goal:
        missing.append("goal")
    if not acceptance_criteria:
        missing.append("acceptance_criteria")
    if not affected_repos:
        missing.append("affected_repos")
    if not references:
        missing.append("references")

    if missing:
        joined = ", ".join(missing)
        raise SpecValidationError(f"{path} is missing required fields: {joined}")

    return ApprovedSpec(
        title=title,
        source_ref=str(path),
        status=status,
        background=background or "",
        goal=goal,
        agreed_scope=agreed_scope,
        non_goals=non_goals,
        acceptance_criteria=acceptance_criteria,
        impacted_modules=impacted_modules,
        affected_repos=affected_repos,
        references=references,
        risks=risks,
        repo_scopes=repo_scopes,
        metadata=metadata,
    )


def _extract_frontmatter(raw: str) -> tuple[dict[str, object], str]:
    if not raw.startswith("---\n"):
        return {}, raw

    parts = raw.split("\n---\n", 1)
    if len(parts) != 2:
        return {}, raw

    _, remainder = parts
    fm_lines = parts[0].splitlines()[1:]
    metadata: dict[str, object] = {}
    current_key: str | None = None

    for line in fm_lines:
        if not line.strip():
            continue
        if line.startswith("  ") and current_key:
            value = line.strip()
            metadata.setdefault(current_key, [])
            if isinstance(metadata[current_key], list):
                metadata[current_key].append(value.lstrip("- ").strip())
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        current_key = key
        if not value:
            metadata[key] = []
            continue
        if value.startswith("[") and value.endswith("]"):
            items = [item.strip().strip("'\"") for item in value[1:-1].split(",") if item.strip()]
            metadata[key] = items
        else:
            metadata[key] = value.strip("'\"")

    return metadata, remainder


def _extract_raw_sections(body: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current_key: str | None = None
    buffer: list[str] = []

    for line in body.splitlines():
        heading_match = re.match(r"^##\s+(.+?)\s*$", line)
        if heading_match:
            if current_key is not None:
                sections[current_key] = buffer
            heading = heading_match.group(1).strip().lower()
            current_key = SECTION_ALIASES.get(heading)
            buffer = []
            continue
        if current_key is not None:
            buffer.append(line)

    if current_key is not None:
        sections[current_key] = buffer

    return sections


def _normalize_section(lines: list[str]) -> object:
    cleaned = [line.rstrip() for line in lines]
    bullets: list[str] = []
    text_lines: list[str] = []

    for line in cleaned:
        stripped = line.strip()
        if not stripped:
            continue
        if re.match(r"^[-*]\s+", stripped):
            bullets.append(re.sub(r"^[-*]\s+", "", stripped))
            continue
        if re.match(r"^\d+\.\s+", stripped):
            bullets.append(re.sub(r"^\d+\.\s+", "", stripped))
            continue
        text_lines.append(stripped)

    if bullets and not text_lines:
        return bullets
    if bullets and text_lines:
        return text_lines + bullets
    return "\n".join(text_lines).strip()


def _coerce_string(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "\n".join(str(item) for item in value if str(item).strip()).strip()
    return str(value).strip()


def _coerce_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    if not text:
        return []
    return [text]


def _extract_repo_scopes(lines: list[str]) -> dict[str, RepoScope]:
    scopes: dict[str, RepoScope] = {}
    current_repo: str | None = None
    buffer: list[str] = []

    for line in lines:
        heading_match = re.match(r"^###\s+(.+?)\s*$", line)
        if heading_match:
            if current_repo is not None:
                scopes[current_repo] = _parse_repo_scope(current_repo, buffer)
            current_repo = _normalize_repo_heading(heading_match.group(1))
            buffer = []
            continue
        if current_repo is not None:
            buffer.append(line)

    if current_repo is not None:
        scopes[current_repo] = _parse_repo_scope(current_repo, buffer)

    return scopes


def _normalize_repo_heading(raw: str) -> str:
    cleaned = raw.replace("`", "").strip()
    if not cleaned:
        return ""
    return cleaned.split()[0]


def _parse_repo_scope(repo: str, lines: list[str]) -> RepoScope:
    scope = RepoScope(repo=repo)

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        labeled_match = re.match(r"^(?:[-*]|\d+\.)\s+([^:]+):\s*(.*)$", stripped)
        if labeled_match:
            raw_key = labeled_match.group(1).strip().lower()
            value = labeled_match.group(2).strip()
            key = REPO_SCOPE_FIELD_ALIASES.get(raw_key)
            if key is None:
                scope.notes.append(stripped)
                continue
            _assign_repo_scope_value(scope, key, value)
            continue

        if re.match(r"^(?:[-*]|\d+\.)\s+", stripped):
            scope.notes.append(re.sub(r"^(?:[-*]|\d+\.)\s+", "", stripped))
            continue

        if scope.summary:
            scope.notes.append(stripped)
        else:
            scope.summary = stripped

    return scope


def _assign_repo_scope_value(scope: RepoScope, key: str, value: str) -> None:
    if key == "summary":
        if value:
            scope.summary = f"{scope.summary}\n{value}".strip() if scope.summary else value
        return

    if not value:
        return

    if key == "modules":
        scope.modules.extend(_split_inline_list(value))
        return

    getattr(scope, key).append(value)


def _split_inline_list(value: str) -> list[str]:
    parts = re.split(r"[,\u3001\uff0c]", value)
    return [part.strip() for part in parts if part.strip()]


def _extract_h1(body: str) -> str:
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""
