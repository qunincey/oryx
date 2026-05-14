---
title: Support local swarm issue dispatch
status: approved
affected_repos:
  - repo-a
  - repo-b
references:
  - https://github.com/example/specs/pull/123
---

# Support local swarm issue dispatch

## Background

The team wants OpenClaw to turn approved requirements into implementation issues without
copying full business context into coding workers.

## Goal

Build the MVP-1 bridge that turns one approved requirement document into one implementation
task per affected repository, persists those tasks in a registry, and renders issue and worker
prompt artifacts for later execution by OpenClaw.

## Non-goals

- Create GitHub issues directly in this step
- Start tmux sessions automatically in this step
- Implement review or retry loops

## Acceptance Criteria

1. Approved markdown specs are discovered from a local directory.
2. Required fields are validated before decomposition.
3. One target repository produces one registry task.
4. Each task renders an implementation issue artifact.
5. Each task renders a worker prompt artifact.

## Risks

- Frontmatter may be incomplete in early drafts.
- The same requirement might be ingested twice without deduplication.
