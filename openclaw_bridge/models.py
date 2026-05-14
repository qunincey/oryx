from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class ApprovedSpec:
    title: str
    source_ref: str
    status: str
    background: str
    goal: str
    non_goals: list[str]
    acceptance_criteria: list[str]
    affected_repos: list[str]
    references: list[str]
    risks: list[str] = field(default_factory=list)
    agreed_scope: list[str] = field(default_factory=list)
    impacted_modules: list[str] = field(default_factory=list)
    repo_scopes: dict[str, "RepoScope"] = field(default_factory=dict)
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class RepoScope:
    repo: str
    summary: str = ""
    modules: list[str] = field(default_factory=list)
    required_changes: list[str] = field(default_factory=list)
    boundaries: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    references: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class TaskRecord:
    task_id: str
    source_repo: str
    source_ref: str
    source_title: str
    target_repo: str
    target_issue_number: str | None
    target_issue_url: str | None
    state: str
    worker_type: str
    worker_model: str
    worktree_path: str
    tmux_session: str
    branch_name: str
    pr_url: str | None
    last_error: str | None
    created_at: str
    updated_at: str
    issue_title: str
    issue_body_path: str
    worker_prompt_path: str
    orx_task_id: str | None = None
    launched_at: str | None = None
    launch_attempts: int = 0
    completed_at: str | None = None
    commit_sha: str | None = None
    commit_message: str | None = None
    last_notified_at: str | None = None
    completion_summary: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "source_repo": self.source_repo,
            "source_ref": self.source_ref,
            "source_title": self.source_title,
            "target_repo": self.target_repo,
            "target_issue_number": self.target_issue_number,
            "target_issue_url": self.target_issue_url,
            "state": self.state,
            "worker_type": self.worker_type,
            "worker_model": self.worker_model,
            "worktree_path": self.worktree_path,
            "tmux_session": self.tmux_session,
            "branch_name": self.branch_name,
            "pr_url": self.pr_url,
            "last_error": self.last_error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "issue_title": self.issue_title,
            "issue_body_path": self.issue_body_path,
            "worker_prompt_path": self.worker_prompt_path,
            "orx_task_id": self.orx_task_id,
            "launched_at": self.launched_at,
            "launch_attempts": self.launch_attempts,
            "completed_at": self.completed_at,
            "commit_sha": self.commit_sha,
            "commit_message": self.commit_message,
            "last_notified_at": self.last_notified_at,
            "completion_summary": self.completion_summary,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, object]) -> "TaskRecord":
        return cls(
            task_id=str(raw["task_id"]),
            source_repo=str(raw["source_repo"]),
            source_ref=str(raw["source_ref"]),
            source_title=str(raw["source_title"]),
            target_repo=str(raw["target_repo"]),
            target_issue_number=raw.get("target_issue_number") and str(raw["target_issue_number"]),
            target_issue_url=raw.get("target_issue_url") and str(raw["target_issue_url"]),
            state=str(raw["state"]),
            worker_type=str(raw["worker_type"]),
            worker_model=str(raw["worker_model"]),
            worktree_path=str(raw["worktree_path"]),
            tmux_session=str(raw["tmux_session"]),
            branch_name=str(raw["branch_name"]),
            pr_url=raw.get("pr_url") and str(raw["pr_url"]),
            last_error=raw.get("last_error") and str(raw["last_error"]),
            created_at=str(raw["created_at"]),
            updated_at=str(raw["updated_at"]),
            issue_title=str(raw["issue_title"]),
            issue_body_path=str(raw["issue_body_path"]),
            worker_prompt_path=str(raw["worker_prompt_path"]),
            orx_task_id=raw.get("orx_task_id") and str(raw["orx_task_id"]),
            launched_at=raw.get("launched_at") and str(raw["launched_at"]),
            launch_attempts=int(raw.get("launch_attempts", 0)),
            completed_at=raw.get("completed_at") and str(raw["completed_at"]),
            commit_sha=raw.get("commit_sha") and str(raw["commit_sha"]),
            commit_message=raw.get("commit_message") and str(raw["commit_message"]),
            last_notified_at=raw.get("last_notified_at") and str(raw["last_notified_at"]),
            completion_summary=raw.get("completion_summary") and str(raw["completion_summary"]),
        )
