from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openclaw_bridge.scaffold import SKILL_DEFINITIONS, init_docs_repo


class InitDocsRepoScaffoldTests(unittest.TestCase):
    def test_all_registered_skills_are_scaffolded_and_globally_installed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            target_dir = temp_path / "docs-repo"
            codex_home = temp_path / ".codex-home"
            claude_home = temp_path / ".claude-home"

            with patch.dict(
                os.environ,
                {"CODEX_HOME": str(codex_home), "CLAUDE_HOME": str(claude_home)},
            ):
                init_docs_repo(
                    target_dir,
                    project_name="Example Project",
                    project_description="Example description",
                )

            for skill_name, definition in SKILL_DEFINITIONS.items():
                for relative_path in definition["codex_files"]:
                    self.assertTrue(
                        (target_dir / ".codex" / "skills" / skill_name / relative_path).exists()
                    )
                    self.assertTrue(
                        (codex_home / "skills" / skill_name / relative_path).exists()
                    )
                self.assertTrue(
                    (target_dir / ".claude" / "agents" / definition["claude_agent_filename"]).exists()
                )
                self.assertTrue(
                    (claude_home / "agents" / definition["claude_agent_filename"]).exists()
                )

            skill_text = (
                target_dir
                / ".codex"
                / "skills"
                / "openclaw-init-docs-repo"
                / "SKILL.md"
            ).read_text(encoding="utf-8")
            bootstrap_skill_text = (
                target_dir
                / ".codex"
                / "skills"
                / "openclaw-project-bootstrap"
                / "SKILL.md"
            ).read_text(encoding="utf-8")
            discussion_skill_text = (
                target_dir
                / ".codex"
                / "skills"
                / "openclaw-discussion-to-prd"
                / "SKILL.md"
            ).read_text(encoding="utf-8")
            repo_issue_skill_text = (
                target_dir
                / ".codex"
                / "skills"
                / "openclaw-prd-to-repo-issues"
                / "SKILL.md"
            ).read_text(encoding="utf-8")
            repo_issue_publish_text = (
                target_dir
                / ".codex"
                / "skills"
                / "openclaw-prd-to-repo-issues"
                / "references"
                / "issue-publish-workflow.md"
            ).read_text(encoding="utf-8")
            discussion_rules_text = (
                target_dir
                / ".codex"
                / "skills"
                / "openclaw-discussion-to-prd"
                / "references"
                / "module-impact-rules.md"
            ).read_text(encoding="utf-8")
            module_map_text = (target_dir / "docs" / "modules" / "module-map.md").read_text(
                encoding="utf-8"
            )
            agents_text = (target_dir / "AGENTS.md").read_text(encoding="utf-8")
            claude_init_agent_text = (
                target_dir / ".claude" / "agents" / "openclaw-init-docs-repo.md"
            ).read_text(encoding="utf-8")
            claude_agent_text = (
                target_dir / ".claude" / "agents" / "openclaw-project-bootstrap.md"
            ).read_text(encoding="utf-8")
            claude_discussion_agent_text = (
                target_dir / ".claude" / "agents" / "openclaw-discussion-to-prd.md"
            ).read_text(encoding="utf-8")
            claude_repo_issue_agent_text = (
                target_dir / ".claude" / "agents" / "openclaw-prd-to-repo-issues.md"
            ).read_text(encoding="utf-8")
            global_codex_init = (
                codex_home / "skills" / "openclaw-init-docs-repo" / "SKILL.md"
            ).read_text(encoding="utf-8")
            global_codex_bootstrap = (
                codex_home / "skills" / "openclaw-project-bootstrap" / "SKILL.md"
            ).read_text(encoding="utf-8")
            global_codex_discussion = (
                codex_home / "skills" / "openclaw-discussion-to-prd" / "SKILL.md"
            ).read_text(encoding="utf-8")
            global_codex_repo_issue = (
                codex_home / "skills" / "openclaw-prd-to-repo-issues" / "SKILL.md"
            ).read_text(encoding="utf-8")
            global_claude_init = (
                claude_home / "agents" / "openclaw-init-docs-repo.md"
            ).read_text(encoding="utf-8")
            global_claude_bootstrap = (
                claude_home / "agents" / "openclaw-project-bootstrap.md"
            ).read_text(encoding="utf-8")
            global_claude_discussion = (
                claude_home / "agents" / "openclaw-discussion-to-prd.md"
            ).read_text(encoding="utf-8")
            global_claude_repo_issue = (
                claude_home / "agents" / "openclaw-prd-to-repo-issues.md"
            ).read_text(encoding="utf-8")

            self.assertIn("Question-First Workflow", skill_text)
            self.assertIn("project_description", skill_text)
            self.assertIn("one large folder", bootstrap_skill_text)
            self.assertIn("directional dependency relationships", bootstrap_skill_text)
            self.assertIn("affected_repos", discussion_skill_text)
            self.assertIn("Repo-specific Scope", repo_issue_skill_text)
            self.assertIn("real repo-targeted online issues", repo_issue_skill_text)
            self.assertIn("dispatch-approved --create-issues", repo_issue_skill_text)
            self.assertIn("dispatch-approved --create-issues", repo_issue_publish_text)
            self.assertIn("module-impact-rules.md", discussion_skill_text)
            self.assertIn("Start From The Module Map", discussion_rules_text)
            self.assertIn("Upstream Dependencies", module_map_text)
            self.assertIn("Dependency Overview", module_map_text)
            self.assertIn("upstream/downstream dependencies", agents_text)
            self.assertIn(".codex/skills/openclaw-prd-to-repo-issues/SKILL.md", agents_text)
            self.assertIn(".codex/skills/openclaw-discussion-to-prd/SKILL.md", agents_text)
            self.assertIn("run the bridge CLI yourself", claude_init_agent_text)
            self.assertIn("directional module dependencies", claude_agent_text)
            self.assertIn("focused draft PRD", claude_discussion_agent_text)
            self.assertIn("publish them through the OpenClaw bridge by default", claude_repo_issue_agent_text)
            self.assertIn("OpenClaw Init Docs Repo", global_codex_init)
            self.assertIn("OpenClaw Project Bootstrap", global_codex_bootstrap)
            self.assertIn("OpenClaw Discussion To PRD", global_codex_discussion)
            self.assertIn("OpenClaw PRD To Repo Issues", global_codex_repo_issue)
            self.assertIn("init-docs-repo specialist", global_claude_init)
            self.assertIn("bootstrap specialist", global_claude_bootstrap)
            self.assertIn("discussion-to-PRD specialist", global_claude_discussion)
            self.assertIn("PRD-to-issue decomposition specialist", global_claude_repo_issue)


if __name__ == "__main__":
    unittest.main()
