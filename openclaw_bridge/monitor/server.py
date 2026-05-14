from __future__ import annotations

import json
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .config import MonitorConfig, add_project_to_monitor_config, load_monitor_config
from .service import build_dashboard_payload, build_task_detail_payload
from .streaming import (
    DEFAULT_STREAM_HEARTBEAT_SECONDS,
    DEFAULT_STREAM_IDLE_INTERVAL_SECONDS,
    DEFAULT_STREAM_POLL_INTERVAL_SECONDS,
    MonitorStreamController,
)

DEFAULT_MONITOR_PORT = 8421
DEFAULT_MONITOR_PORT_CANDIDATES = tuple(range(DEFAULT_MONITOR_PORT, DEFAULT_MONITOR_PORT + 10))

_ROOT_HTML = """<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Unified Task Monitor</title>
    <style>
      :root {
        color-scheme: light;
        --bg: #f4f7fb;
        --panel: rgba(255, 255, 255, 0.94);
        --panel-alt: #f8fafc;
        --line: #d7e0ea;
        --line-strong: #c7d2df;
        --text: #111827;
        --muted: #667085;
        --muted-soft: #98a2b3;
        --accent: #2563eb;
        --accent-soft: #eef4ff;
        --success: #1f8a5b;
        --success-soft: #e9f7ef;
        --warn: #b45309;
        --warn-soft: #fff4ea;
        --danger: #c2410c;
        --danger-soft: #fff1eb;
        --shadow: 0 20px 50px rgba(15, 23, 42, 0.08);
      }
      * { box-sizing: border-box; }
      body {
        margin: 0;
        font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        background:
          radial-gradient(circle at top left, rgba(37, 99, 235, 0.10), transparent 28%),
          linear-gradient(180deg, #fbfcfe 0%, var(--bg) 100%);
        color: var(--text);
      }
      a { color: var(--accent); }
      .shell {
        max-width: 1480px;
        margin: 0 auto;
        padding: 28px;
      }
      .hero {
        display: flex;
        justify-content: space-between;
        gap: 18px;
        align-items: end;
        margin-bottom: 18px;
      }
      .hero-kicker,
      .pane-kicker,
      .metric-label {
        color: var(--muted);
        font-size: 0.78rem;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.08em;
      }
      .hero h1 {
        margin: 8px 0 0;
        font-size: 2.9rem;
        line-height: 1.05;
      }
      .hero p {
        margin: 10px 0 0;
        max-width: 760px;
        color: var(--muted);
        font-size: 1rem;
        line-height: 1.6;
      }
      .hero-actions {
        display: flex;
        gap: 12px;
        align-items: center;
        flex-wrap: wrap;
        justify-content: flex-end;
      }
      .action-button {
        appearance: none;
        border: 1px solid #111827;
        background: #111827;
        color: white;
        padding: 11px 16px;
        border-radius: 999px;
        font-weight: 700;
        cursor: pointer;
        box-shadow: 0 12px 24px rgba(15, 23, 42, 0.12);
      }
      .action-button.secondary {
        background: white;
        color: var(--text);
        box-shadow: none;
        border-color: var(--line);
      }
      .timezone-pill {
        display: inline-flex;
        align-items: center;
        gap: 8px;
        min-height: 46px;
        padding: 10px 16px;
        border-radius: 999px;
        border: 1px solid var(--line);
        background: rgba(255, 255, 255, 0.86);
        color: var(--text);
        font-weight: 600;
      }
      .workspace {
        display: grid;
        grid-template-columns: 360px minmax(0, 1fr);
        gap: 18px;
        min-height: 72vh;
      }
      .panel {
        background: var(--panel);
        border: 1px solid var(--line);
        border-radius: 24px;
        box-shadow: var(--shadow);
        overflow: hidden;
      }
      .sidebar {
        background: linear-gradient(180deg, rgba(255, 255, 255, 0.95), rgba(248, 250, 252, 0.92));
      }
      .main {
        display: grid;
        gap: 18px;
      }
      .pane-header {
        padding: 18px 20px;
        border-bottom: 1px solid var(--line);
        display: flex;
        justify-content: space-between;
        align-items: center;
        gap: 16px;
      }
      .pane-header h2 {
        margin: 4px 0 0;
        font-size: 1.15rem;
      }
      .muted {
        color: var(--muted);
      }
      .project-list {
        padding: 12px;
      }
      .project-card {
        margin-bottom: 12px;
        border: 1px solid var(--line);
        border-radius: 20px;
        background: rgba(255, 255, 255, 0.78);
      }
      .project-card.expanded {
        border-color: var(--line-strong);
      }
      .project-toggle {
        width: 100%;
        text-align: left;
        background: transparent;
        border: 0;
        padding: 16px;
        cursor: pointer;
        display: flex;
        justify-content: space-between;
        align-items: start;
        gap: 12px;
      }
      .project-toggle:hover,
      .task-item:hover,
      .task-item.active {
        background: var(--accent-soft);
      }
      .project-toggle-main {
        display: flex;
        gap: 12px;
        min-width: 0;
        flex: 1 1 auto;
      }
      .project-caret {
        width: 26px;
        height: 26px;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        border-radius: 999px;
        border: 1px solid var(--line);
        background: var(--panel-alt);
        color: var(--muted);
        flex: 0 0 auto;
      }
      .project-copy {
        min-width: 0;
      }
      .project-name {
        display: block;
        font-size: 1.75rem;
        font-weight: 700;
        line-height: 1.15;
      }
      .project-meta {
        margin-top: 6px;
        color: var(--muted);
        line-height: 1.45;
      }
      .project-note {
        margin-top: 8px;
        color: var(--danger);
        font-size: 0.86rem;
      }
      .badge,
      .state {
        display: inline-flex;
        align-items: center;
        border-radius: 999px;
        padding: 5px 10px;
        font-size: 0.74rem;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        border: 1px solid transparent;
        white-space: nowrap;
      }
      .badge.healthy { color: var(--success); background: var(--success-soft); }
      .badge.degraded { color: var(--warn); background: var(--warn-soft); }
      .badge.disconnected { color: var(--danger); background: var(--danger-soft); }
      .state.blocked { background: var(--danger-soft); color: var(--danger); }
      .state.restartable,
      .state.exited { background: var(--warn-soft); color: var(--warn); }
      .state.in_progress,
      .state.dispatched,
      .state.restarted { background: var(--accent-soft); color: var(--accent); }
      .state.completed { background: #eef7f1; color: var(--success); }
      .task-list {
        margin: 0 16px 12px 54px;
        padding: 2px 0 0;
      }
      .task-list-empty {
        padding: 10px 12px 4px;
        color: var(--muted);
      }
      .task-item {
        width: 100%;
        text-align: left;
        background: transparent;
        border: 0;
        border-radius: 16px;
        padding: 11px 12px;
        cursor: pointer;
        display: flex;
        justify-content: space-between;
        align-items: center;
        gap: 12px;
      }
      .task-item + .task-item {
        border-top: 1px solid rgba(199, 210, 223, 0.55);
      }
      .task-title {
        font-weight: 600;
        line-height: 1.45;
      }
      .empty-stack {
        display: flex;
        flex-direction: column;
        align-items: start;
        gap: 12px;
      }
      .summary-grid {
        display: grid;
        grid-template-columns: repeat(5, minmax(0, 1fr));
        gap: 12px;
        padding: 18px 20px 22px;
      }
      .metric-card {
        padding: 18px;
        border: 1px solid var(--line);
        border-radius: 20px;
        background: rgba(255, 255, 255, 0.82);
      }
      .metric-value {
        margin-top: 10px;
        font-size: 2rem;
        font-weight: 700;
      }
      .detail-empty {
        padding: 34px 28px;
        color: var(--muted);
      }
      .detail-body {
        padding: 22px 24px 28px;
      }
      .detail-top {
        display: flex;
        justify-content: space-between;
        gap: 16px;
        align-items: start;
        margin-bottom: 18px;
      }
      .detail-copy h3 {
        margin: 8px 0 0;
        font-size: 2rem;
        line-height: 1.1;
      }
      .detail-subtitle {
        margin-top: 8px;
        color: var(--muted);
      }
      .detail-statuses {
        display: flex;
        flex-direction: column;
        gap: 10px;
        align-items: flex-end;
      }
      .detail-grid {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 12px;
        margin-bottom: 18px;
      }
      .meta-card {
        border: 1px solid var(--line);
        border-radius: 18px;
        padding: 14px;
        background: rgba(248, 250, 252, 0.72);
      }
      .meta-card p {
        margin: 8px 0 0;
        line-height: 1.5;
        word-break: break-word;
      }
      .task-meta, .timeline {
        border-top: 1px solid var(--line);
        padding-top: 18px;
        margin-top: 18px;
      }
      .event-list,
      .timeline-list {
        display: grid;
        gap: 10px;
        padding: 18px 20px 22px;
      }
      .event-item,
      .timeline-item {
        padding: 14px;
        border: 1px solid var(--line);
        border-radius: 18px;
        background: rgba(248, 250, 252, 0.72);
      }
      .event-title-row,
      .timeline-title-row {
        display: flex;
        justify-content: space-between;
        gap: 12px;
        align-items: center;
      }
      .event-title,
      .timeline-title {
        display: flex;
        align-items: center;
        gap: 10px;
        font-weight: 600;
      }
      .event-meta,
      .timeline-meta {
        margin-top: 8px;
        color: var(--muted);
        line-height: 1.5;
      }
      .task-output {
        border-top: 1px solid var(--line);
        padding-top: 18px;
        margin-top: 18px;
      }
      .log-grid {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 12px;
        margin-top: 14px;
      }
      .log-panel {
        border: 1px solid var(--line);
        border-radius: 18px;
        background: #0f172a;
        color: #e5eefb;
        overflow: hidden;
      }
      .log-panel .timeline-title-row {
        padding: 14px 16px 0;
        align-items: baseline;
      }
      .log-panel .timeline-title,
      .log-panel .muted {
        color: #d7e5ff;
      }
      .log-pre {
        margin: 0;
        padding: 14px 16px 18px;
        min-height: 180px;
        max-height: 320px;
        overflow: auto;
        white-space: pre-wrap;
        word-break: break-word;
        font-size: 0.86rem;
        line-height: 1.55;
        font-family: "SFMono-Regular", "SF Mono", ui-monospace, Menlo, Consolas, monospace;
      }
      .log-pre.stderr {
        color: #ffd7d1;
      }
      code {
        background: rgba(148, 163, 184, 0.12);
        border-radius: 8px;
        padding: 2px 6px;
      }
      .modal-shell[hidden] {
        display: none;
      }
      .modal-shell {
        position: fixed;
        inset: 0;
        background: rgba(15, 23, 42, 0.32);
        display: flex;
        align-items: center;
        justify-content: center;
        padding: 24px;
      }
      .modal {
        width: min(560px, 100%);
        background: white;
        border: 1px solid var(--line);
        border-radius: 24px;
        box-shadow: var(--shadow);
        padding: 22px;
      }
      .modal h3 {
        margin: 0;
      }
      .modal p {
        color: var(--muted);
      }
      .form-grid {
        display: grid;
        gap: 14px;
        margin-top: 18px;
      }
      .field {
        display: grid;
        gap: 6px;
      }
      .field label {
        font-size: 0.86rem;
        font-weight: 700;
      }
      .field input {
        width: 100%;
        border: 1px solid var(--line);
        border-radius: 14px;
        padding: 11px 12px;
        background: white;
        color: var(--text);
      }
      .form-actions {
        display: flex;
        justify-content: flex-end;
        gap: 10px;
        margin-top: 18px;
      }
      .form-status {
        min-height: 1.4em;
        margin-top: 12px;
        color: var(--warn);
      }
      .form-status.error {
        color: var(--danger);
      }
      .form-status.success {
        color: var(--success);
      }
      @media (max-width: 1180px) {
        .summary-grid {
          grid-template-columns: repeat(2, minmax(0, 1fr));
        }
      }
      @media (max-width: 1024px) {
        .workspace,
        .detail-grid,
        .log-grid,
        .summary-grid {
          grid-template-columns: 1fr;
        }
      }
    </style>
  </head>
  <body>
    <div class="shell">
      <section class="hero">
        <div>
          <div class="hero-kicker">Unified Task Monitor</div>
          <h1>Local runtime overview</h1>
          <p>Track orx runtime tasks across multiple local projects with clearer hierarchy and browser-timezone timestamps.</p>
        </div>
        <div class="hero-actions">
          <button class="action-button" id="open-add-project">Add Project</button>
          <div class="timezone-pill" id="generated-at">Loading…</div>
        </div>
      </section>

      <section class="workspace">
        <aside class="sidebar panel">
          <div class="pane-header">
            <div>
              <div class="pane-kicker">Navigation</div>
              <h2>Projects & Tasks</h2>
            </div>
            <span class="muted" id="project-count"></span>
          </div>
          <div id="project-list" class="project-list"></div>
        </aside>

        <main class="main">
          <section class="panel">
            <div class="pane-header">
              <div>
                <div class="pane-kicker">Overview</div>
                <h2>Summary</h2>
              </div>
              <span class="muted" id="poll-interval"></span>
            </div>
            <div class="summary-grid" id="summary-grid">
              <div class="metric-card"><div class="metric-label">Running</div><div class="metric-value">-</div></div>
              <div class="metric-card"><div class="metric-label">Blocked</div><div class="metric-value">-</div></div>
              <div class="metric-card"><div class="metric-label">Restartable</div><div class="metric-value">-</div></div>
              <div class="metric-card"><div class="metric-label">Completed Today</div><div class="metric-value">-</div></div>
              <div class="metric-card"><div class="metric-label">Degraded / Offline</div><div class="metric-value">-</div></div>
            </div>
          </section>

          <section class="panel">
            <div class="pane-header">
              <div>
                <div class="pane-kicker">Selection</div>
                <h2>Task Detail</h2>
              </div>
              <span class="muted" id="detail-project"></span>
            </div>
            <div id="task-detail" class="detail-empty">
              Select a task from the left to inspect its current state, runtime info, and event timeline.
            </div>
          </section>

          <section class="panel">
            <div class="pane-header">
              <div>
                <div class="pane-kicker">Activity</div>
                <h2>Recent Events</h2>
              </div>
            </div>
            <div id="recent-events" class="detail-empty">Loading recent events…</div>
          </section>
        </main>
      </section>
    </div>
    <div class="modal-shell" id="add-project-modal" hidden>
      <div class="modal" role="dialog" aria-modal="true" aria-labelledby="add-project-title">
        <h3 id="add-project-title">Add Project</h3>
        <p>Register an orx runtime root so it appears in this monitor immediately.</p>
        <form id="add-project-form">
          <div class="form-grid">
            <div class="field">
              <label for="runtime-root-input">Orx Runtime Root</label>
              <input id="runtime-root-input" name="runtime_root" placeholder="/absolute/path/to/.openclaw-state/orx" required>
            </div>
            <div class="field">
              <label for="project-name-input">Display Name (optional)</label>
              <input id="project-name-input" name="name" placeholder="Example Project">
            </div>
            <div class="field">
              <label for="project-id-input">Project ID (optional)</label>
              <input id="project-id-input" name="id" placeholder="example-project">
            </div>
          </div>
          <div class="form-status" id="add-project-status"></div>
          <div class="form-actions">
            <button type="button" class="action-button secondary" id="cancel-add-project">Cancel</button>
            <button type="submit" class="action-button" id="submit-add-project">Save Project</button>
          </div>
        </form>
      </div>
    </div>
    <script>
      const browserTimeZone = Intl.DateTimeFormat(undefined, { timeZoneName: "short" }).resolvedOptions().timeZone || "UTC";
      const timeFormatter = new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" });
      const dateTimeFormatter = new Intl.DateTimeFormat(undefined, {
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
      });

      const state = {
        dashboard: null,
        selectedProjectId: null,
        selectedTaskId: null,
        projectCollapse: {},
        currentDetail: null,
        stream: {
          source: null,
          connected: false,
          everConnected: false,
        },
      };

      const taskSeverity = {
        blocked: 0,
        restartable: 1,
        exited: 2,
        in_progress: 3,
        dispatched: 4,
        decomposed: 5,
        completed: 6,
      };

      function escapeHtml(value) {
        return String(value ?? "").replace(/[&<>"]/g, (char) => ({
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
        }[char]));
      }

      function toDate(value) {
        if (!value) {
          return null;
        }
        const parsed = new Date(value);
        return Number.isNaN(parsed.getTime()) ? null : parsed;
      }

      function formatTimestamp(value, options = {}) {
        const { includeDate = true, fallback = "Unknown" } = options;
        const parsed = toDate(value);
        if (!parsed) {
          return fallback;
        }
        return includeDate ? dateTimeFormatter.format(parsed) : timeFormatter.format(parsed);
      }

      function formatHeaderTimestamp(value) {
        return `Updated ${formatTimestamp(value, { includeDate: false, fallback: timeFormatter.format(new Date()) })} · ${browserTimeZone}`;
      }

      function formatStateLabel(value) {
        const labels = {
          blocked: "Blocked",
          completed: "Completed",
          decomposed: "Decomposed",
          dispatched: "Queued",
          exited: "Exited",
          in_progress: "Running",
          restartable: "Restartable",
          restarted: "Restarted",
        };
        return labels[value] || String(value || "Unknown").replaceAll("_", " ");
      }

      function formatHealthLabel(value) {
        if (!value) {
          return "Unknown";
        }
        return value.charAt(0).toUpperCase() + value.slice(1);
      }

      function isStreamingTask(task) {
        return task && task.state === "in_progress" && task.orx_task_id;
      }

      function compareTasks(left, right) {
        const leftSeverity = taskSeverity[left.state] ?? 99;
        const rightSeverity = taskSeverity[right.state] ?? 99;
        if (leftSeverity !== rightSeverity) {
          return leftSeverity - rightSeverity;
        }
        const leftUpdated = toDate(left.updated_at)?.getTime() || 0;
        const rightUpdated = toDate(right.updated_at)?.getTime() || 0;
        return rightUpdated - leftUpdated;
      }

      function computeProjectCounts(project) {
        const tasks = project.tasks || [];
        return {
          total: tasks.length,
          in_progress: tasks.filter((task) => task.state === "in_progress").length,
          blocked: tasks.filter((task) => task.state === "blocked").length,
          restartable: tasks.filter((task) => task.state === "restartable").length,
          completed: tasks.filter((task) => task.state === "completed").length,
        };
      }

      function renderMonitorStatus(pollIntervalSeconds = state.dashboard?.poll_interval_seconds || 3) {
        let liveLabel = "Live disconnected";
        if (state.stream.connected) {
          liveLabel = "Live connected";
        } else if (state.stream.everConnected) {
          liveLabel = "Live reconnecting";
        }
        document.getElementById("poll-interval").textContent = `${liveLabel} · Snapshot fallback every ${pollIntervalSeconds}s`;
      }

      function detailStreamLabel(task) {
        if (!task?.orx_task_id) {
          return "Snapshot";
        }
        if (task.state !== "in_progress") {
          return "Stream complete";
        }
        return state.stream.connected ? "Streaming" : "Reconnecting";
      }

      function detailStreamTone(task) {
        if (!task?.orx_task_id) {
          return "degraded";
        }
        if (task.state !== "in_progress") {
          return "healthy";
        }
        return state.stream.connected ? "healthy" : "degraded";
      }

      function formatEventTone(value) {
        if (value === "completed") {
          return "completed";
        }
        if (value === "restartable" || value === "exited") {
          return value;
        }
        if (value === "blocked" || value === "failed") {
          return "blocked";
        }
        return "in_progress";
      }

      function completedTodayCount(projects) {
        const now = new Date();
        return projects.reduce((count, project) => count + project.tasks.filter((task) => {
          const completedAt = toDate(task.completed_at);
          return completedAt
            && completedAt.getFullYear() === now.getFullYear()
            && completedAt.getMonth() === now.getMonth()
            && completedAt.getDate() === now.getDate();
        }).length, 0);
      }

      function selectionExists(projects, projectId, taskId) {
        return projects.some((project) => project.id === projectId && project.tasks.some((task) => task.task_id === taskId));
      }

      function pickDefaultSelection(projects) {
        const firstProject = projects.find((project) => project.tasks.length > 0);
        return firstProject ? { projectId: firstProject.id, taskId: firstProject.tasks[0].task_id } : null;
      }

      async function fetchJson(path) {
        const response = await fetch(path, { headers: { "Accept": "application/json" } });
        const payload = await response.json();
        if (!response.ok) {
          throw new Error(payload.message || payload.error || "request failed");
        }
        return payload;
      }

      async function sendJson(path, method, body) {
        const response = await fetch(path, {
          method,
          headers: {
            "Accept": "application/json",
            "Content-Type": "application/json",
          },
          body: JSON.stringify(body),
        });
        const payload = await response.json();
        if (!response.ok) {
          throw new Error(payload.message || payload.error || "request failed");
        }
        return payload;
      }

      function renderSummary(summary, projects) {
        const runningCount = projects.reduce((count, project) => count + project.tasks.filter((task) => task.state === "in_progress").length, 0);
        const blockedCount = projects.reduce((count, project) => count + project.tasks.filter((task) => task.state === "blocked").length, 0);
        const restartableCount = projects.reduce((count, project) => count + project.tasks.filter((task) => task.state === "restartable").length, 0);
        const degradedCount = projects.filter((project) => project.health === "degraded").length;
        const disconnectedCount = projects.filter((project) => project.health === "disconnected").length;
        const items = [
          ["Running", runningCount],
          ["Blocked", blockedCount],
          ["Restartable", restartableCount],
          ["Completed Today", completedTodayCount(projects)],
          ["Degraded / Offline", degradedCount + disconnectedCount],
        ];
        document.getElementById("summary-grid").innerHTML = items.map(([label, value]) => `
          <div class="metric-card">
            <div class="metric-label">${escapeHtml(label)}</div>
            <div class="metric-value">${escapeHtml(value)}</div>
          </div>
        `).join("");
      }

      function renderRecentEvents(events, pollIntervalSeconds) {
        renderMonitorStatus(pollIntervalSeconds);
        const container = document.getElementById("recent-events");
        if (!events.length) {
          container.innerHTML = `<div class="detail-empty">No watchdog events yet.</div>`;
          return;
        }
        container.innerHTML = `<div class="event-list">${events.map((event) => `
          <article class="event-item">
            <div class="event-title-row">
              <div class="event-title">
                <span>${escapeHtml(event.project_name || event.project_id || "Project")}</span>
                <span class="state ${escapeHtml(formatEventTone(event.event))}">${escapeHtml(formatStateLabel(event.event))}</span>
              </div>
              <span class="muted">${escapeHtml(formatTimestamp(event.timestamp))}</span>
            </div>
            <div class="event-meta">${escapeHtml(event.task_id)} · ${escapeHtml(event.message || "")}</div>
          </article>
        `).join("")}</div>`;
      }

      function renderProjects(projects) {
        const container = document.getElementById("project-list");
        document.getElementById("project-count").textContent = `${projects.length} project${projects.length === 1 ? "" : "s"}`;
        if (!projects.length) {
          container.innerHTML = `
            <div class="detail-empty empty-stack">
              <div>No projects configured yet.</div>
              <button class="action-button" type="button" data-add-project-inline>Add Project</button>
            </div>
          `;
          const inlineButton = container.querySelector("[data-add-project-inline]");
          if (inlineButton) {
            inlineButton.addEventListener("click", openAddProjectModal);
          }
          return;
        }
        container.innerHTML = projects.map((project) => {
          const counts = computeProjectCounts(project);
          const sortedTasks = [...project.tasks].sort(compareTasks);
          const isCollapsed = Boolean(state.projectCollapse[project.id]);
          const isExpanded = !isCollapsed;
          const tasksMarkup = isExpanded ? `
            <div class="task-list" role="list">
              ${sortedTasks.map((task) => `
                <button
                  class="task-item ${state.selectedTaskId === task.task_id && state.selectedProjectId === project.id ? "active" : ""}"
                  data-project-id="${escapeHtml(project.id)}"
                  data-task-id="${escapeHtml(task.task_id)}"
                  type="button"
                >
                  <span class="task-title">${escapeHtml(task.source_title || task.task_id)}</span>
                  <span class="state ${escapeHtml(formatEventTone(task.state))}">${escapeHtml(formatStateLabel(task.state))}</span>
                </button>
              `).join("") || `<div class="task-list-empty">No tasks available.</div>`}
            </div>
          ` : "";
          return `
            <section class="project-card ${isExpanded ? "expanded" : ""}">
              <button
                class="project-toggle"
                data-project-toggle="${escapeHtml(project.id)}"
                type="button"
                aria-expanded="${String(!isCollapsed)}"
              >
                <div class="project-toggle-main">
                  <span class="project-caret">${isExpanded ? "▾" : "▸"}</span>
                  <div class="project-copy">
                    <span class="project-name">${escapeHtml(project.name)}</span>
                    <div class="project-meta">Tasks ${counts.total} · Running ${counts.in_progress} · Blocked ${counts.blocked}</div>
                    ${project.error ? `<div class="project-note">${escapeHtml(project.error)}</div>` : ""}
                  </div>
                </div>
                <span class="badge ${escapeHtml(project.health)}">${escapeHtml(formatHealthLabel(project.health))}</span>
              </button>
              ${tasksMarkup}
            </section>
          `;
        }).join("");

        container.querySelectorAll("[data-project-toggle]").forEach((element) => {
          element.addEventListener("click", () => {
            const projectId = element.getAttribute("data-project-toggle");
            state.projectCollapse[projectId] = !state.projectCollapse[projectId];
            renderProjects(state.dashboard.projects);
          });
        });
        container.querySelectorAll(".task-item").forEach((element) => {
          element.addEventListener("click", () => {
            state.selectedProjectId = element.getAttribute("data-project-id");
            state.selectedTaskId = element.getAttribute("data-task-id");
            renderProjects(state.dashboard.projects);
            void refreshTaskDetail();
          });
        });
      }

      function renderEmptyDetail(message) {
        document.getElementById("detail-project").textContent = "";
        document.getElementById("task-detail").innerHTML = `<div class="detail-empty">${escapeHtml(message)}</div>`;
      }

      function setAddProjectStatus(message, kind = "") {
        const element = document.getElementById("add-project-status");
        element.className = `form-status ${kind}`.trim();
        element.textContent = message || "";
      }

      function openAddProjectModal() {
        document.getElementById("add-project-modal").hidden = false;
        setAddProjectStatus("");
        document.getElementById("runtime-root-input").focus();
      }

      function closeAddProjectModal() {
        document.getElementById("add-project-modal").hidden = true;
        document.getElementById("add-project-form").reset();
        setAddProjectStatus("");
      }

      function renderMetaCard(label, content) {
        return `<div class="meta-card"><div class="metric-label">${escapeHtml(label)}</div><p>${content}</p></div>`;
      }

      function updateDetailStreamBadge() {
        if (!state.currentDetail) {
          return;
        }
        const badge = document.getElementById("stream-status");
        if (!badge) {
          return;
        }
        badge.className = `badge ${detailStreamTone(state.currentDetail.task)}`;
        badge.textContent = detailStreamLabel(state.currentDetail.task);
      }

      function renderTaskDetail(payload) {
        document.getElementById("detail-project").textContent = payload.project.name;
        const task = payload.task;
        const runtime = payload.runtime;
        const output = payload.output || { stdout: "", stderr: "" };
        const timeline = payload.timeline || [];
        document.getElementById("task-detail").innerHTML = `
          <div class="detail-body">
            <div class="detail-top">
              <div class="detail-copy">
                <div class="pane-kicker">${escapeHtml(payload.project.name)}</div>
                <h3>${escapeHtml(task.source_title)}</h3>
                <div class="detail-subtitle">${escapeHtml(task.task_id)} · ${escapeHtml(task.target_repo)} · Updated <span id="detail-updated-at">${escapeHtml(formatTimestamp(task.updated_at))}</span></div>
              </div>
              <div class="detail-statuses">
                <span class="state ${escapeHtml(formatEventTone(task.state))}" id="selected-task-state">${escapeHtml(formatStateLabel(task.state))}</span>
                <span class="badge ${escapeHtml(detailStreamTone(task))}" id="stream-status">${escapeHtml(detailStreamLabel(task))}</span>
              </div>
            </div>
            <div class="detail-grid">
              ${renderMetaCard("Last Error", `<span id="detail-last-error">${escapeHtml(task.last_error || "None")}</span>`)}
              ${renderMetaCard("Worker", `${escapeHtml(task.worker_type || "Unknown")} · ${escapeHtml(task.worker_model || "Unknown")}`)}
              ${renderMetaCard("Issue", task.target_issue_url ? `<a href="${escapeHtml(task.target_issue_url)}" target="_blank" rel="noreferrer">${escapeHtml(task.target_issue_url)}</a>` : "None")}
              ${renderMetaCard("Branch", `<code>${escapeHtml(task.branch_name || "")}</code>`)}
              ${renderMetaCard("Worktree", `<code>${escapeHtml(task.worktree_path || "")}</code>`)}
              ${renderMetaCard("Orx Task", escapeHtml(task.orx_task_id || "None"))}
              ${renderMetaCard("Launched", escapeHtml(formatTimestamp(task.launched_at, { fallback: "Unknown" })))}
              ${renderMetaCard("Completed", `<span id="detail-completed-at">${escapeHtml(formatTimestamp(task.completed_at, { fallback: "Not completed" }))}</span>`)}
            </div>
            <section class="task-meta">
              <div class="pane-kicker">Runtime Snapshot</div>
              <div class="timeline-list">
                <article class="timeline-item">
                  <div class="timeline-title-row">
                    <div class="timeline-title">Status</div>
                    <span class="state ${escapeHtml(formatEventTone(task.state))}" id="detail-runtime-status">${escapeHtml(runtime?.status || "Unavailable")}</span>
                  </div>
                  <div class="timeline-meta" id="detail-runtime-meta">Session ${escapeHtml(runtime?.session_name || task.tmux_session || "Unknown")} · Workspace ${escapeHtml(runtime?.workspace_path || task.worktree_path || "Unavailable")}</div>
                </article>
                <article class="timeline-item">
                  <div class="timeline-title">Failure</div>
                  <div class="timeline-meta" id="detail-runtime-failure">${escapeHtml(runtime?.failure_message || task.last_error || "None")}</div>
                </article>
              </div>
            </section>
            <section class="task-output">
              <div class="pane-kicker">Live Output</div>
              <div class="log-grid">
                <article class="log-panel">
                  <div class="timeline-title-row">
                    <div class="timeline-title">Stdout</div>
                    <span class="muted">Incremental stream</span>
                  </div>
                  <pre class="log-pre" id="stdout-log">${escapeHtml(output.stdout || "")}</pre>
                </article>
                <article class="log-panel">
                  <div class="timeline-title-row">
                    <div class="timeline-title">Stderr</div>
                    <span class="muted">Incremental stream</span>
                  </div>
                  <pre class="log-pre stderr" id="stderr-log">${escapeHtml(output.stderr || "")}</pre>
                </article>
              </div>
            </section>
            <section class="timeline">
              <div class="pane-kicker">Task Timeline</div>
              ${timeline.length ? `<div class="timeline-list">${timeline.map((event) => `
                <article class="timeline-item">
                  <div class="timeline-title-row">
                    <div class="timeline-title">${escapeHtml(formatStateLabel(event.event))}</div>
                    <span class="muted">${escapeHtml(formatTimestamp(event.timestamp))}</span>
                  </div>
                  <div class="timeline-meta">${escapeHtml(event.message || "")}</div>
                </article>
              `).join("")}</div>` : `<div class="detail-empty">No watchdog timeline events for this task.</div>`}
            </section>
          </div>
        `;
        updateDetailStreamBadge();
      }

      function applyTaskUpdateToDashboard(update) {
        if (!state.dashboard) {
          return;
        }
        const project = state.dashboard.projects.find((item) => item.id === update.project_id);
        if (!project) {
          void refreshDashboard();
          return;
        }
        const task = project.tasks.find((item) => item.task_id === update.task_id);
        if (!task) {
          void refreshDashboard();
          return;
        }
        task.state = update.state;
        task.updated_at = update.updated_at;
        task.completed_at = update.completed_at;
        task.last_error = update.last_error;
        task.runtime = task.runtime || {};
        task.runtime.status = update.runtime_status;
        task.runtime.retryable = Boolean(update.retryable);
        task.runtime.updated_at = update.updated_at;
        task.runtime.finished_at = update.completed_at;
        task.runtime.failure_message = update.last_error;
        renderSummary(state.dashboard.summary, state.dashboard.projects);
        renderProjects(state.dashboard.projects);
      }

      function appendChunksToLog(elementId, chunks) {
        if (!chunks?.length) {
          return;
        }
        const element = document.getElementById(elementId);
        if (!element) {
          return;
        }
        const shouldStickToBottom = element.scrollTop + element.clientHeight >= element.scrollHeight - 24;
        element.textContent += chunks.map((chunk) => chunk.content || "").join("");
        if (shouldStickToBottom) {
          element.scrollTop = element.scrollHeight;
        }
      }

      function applyTaskUpdateToDetail(update) {
        if (!state.currentDetail) {
          return;
        }
        if (state.currentDetail.project.id !== update.project_id || state.currentDetail.task.task_id !== update.task_id) {
          return;
        }
        state.currentDetail.task.state = update.state;
        state.currentDetail.task.updated_at = update.updated_at;
        state.currentDetail.task.completed_at = update.completed_at;
        state.currentDetail.task.last_error = update.last_error;
        state.currentDetail.runtime = state.currentDetail.runtime || {};
        state.currentDetail.runtime.status = update.runtime_status;
        state.currentDetail.runtime.failure_message = update.last_error;
        state.currentDetail.runtime.updated_at = update.updated_at;
        state.currentDetail.runtime.finished_at = update.completed_at;
        state.currentDetail.output = state.currentDetail.output || {
          stdout: "",
          stderr: "",
          stdout_seq: 0,
          stderr_seq: 0,
          last_seq: 0,
        };
        for (const chunk of update.stdout_chunks || []) {
          state.currentDetail.output.stdout += chunk.content || "";
          state.currentDetail.output.stdout_seq = chunk.seq;
          state.currentDetail.output.last_seq = Math.max(state.currentDetail.output.last_seq || 0, chunk.seq || 0);
        }
        for (const chunk of update.stderr_chunks || []) {
          state.currentDetail.output.stderr += chunk.content || "";
          state.currentDetail.output.stderr_seq = chunk.seq;
          state.currentDetail.output.last_seq = Math.max(state.currentDetail.output.last_seq || 0, chunk.seq || 0);
        }

        const taskState = document.getElementById("selected-task-state");
        if (taskState) {
          taskState.className = `state ${formatEventTone(update.state)}`;
          taskState.textContent = formatStateLabel(update.state);
        }
        const runtimeStatus = document.getElementById("detail-runtime-status");
        if (runtimeStatus) {
          runtimeStatus.className = `state ${formatEventTone(update.state)}`;
          runtimeStatus.textContent = update.runtime_status || "Unavailable";
        }
        const updatedAt = document.getElementById("detail-updated-at");
        if (updatedAt) {
          updatedAt.textContent = formatTimestamp(update.updated_at);
        }
        const completedAt = document.getElementById("detail-completed-at");
        if (completedAt) {
          completedAt.textContent = formatTimestamp(update.completed_at, { fallback: "Not completed" });
        }
        const lastError = document.getElementById("detail-last-error");
        if (lastError) {
          lastError.textContent = update.last_error || "None";
        }
        const runtimeFailure = document.getElementById("detail-runtime-failure");
        if (runtimeFailure) {
          runtimeFailure.textContent = update.last_error || "None";
        }
        appendChunksToLog("stdout-log", update.stdout_chunks);
        appendChunksToLog("stderr-log", update.stderr_chunks);
        updateDetailStreamBadge();
      }

      async function refreshDashboard() {
        const payload = await fetchJson("/api/dashboard");
        state.dashboard = payload;
        document.getElementById("generated-at").textContent = formatHeaderTimestamp(payload.generated_at);
        renderSummary(payload.summary, payload.projects);
        renderRecentEvents(payload.recent_events, payload.poll_interval_seconds);

        if (!selectionExists(payload.projects, state.selectedProjectId, state.selectedTaskId)) {
          const fallbackSelection = pickDefaultSelection(payload.projects);
          state.selectedProjectId = fallbackSelection ? fallbackSelection.projectId : null;
          state.selectedTaskId = fallbackSelection ? fallbackSelection.taskId : null;
        }

        renderProjects(payload.projects);
        await refreshTaskDetail();
      }

      async function refreshTaskDetail() {
        if (!state.selectedProjectId || !state.selectedTaskId) {
          state.currentDetail = null;
          renderEmptyDetail("Select a task from the left to inspect its details.");
          return;
        }
        try {
          const payload = await fetchJson(`/api/tasks/${encodeURIComponent(state.selectedProjectId)}/${encodeURIComponent(state.selectedTaskId)}`);
          state.currentDetail = payload;
          renderTaskDetail(payload);
        } catch (error) {
          state.currentDetail = null;
          renderEmptyDetail(error.message || "Failed to load task detail.");
        }
      }

      function connectStream() {
        if (typeof EventSource === "undefined") {
          renderMonitorStatus();
          return;
        }
        if (state.stream.source) {
          state.stream.source.close();
        }
        const source = new EventSource("/api/stream");
        state.stream.source = source;
        source.addEventListener("open", () => {
          const shouldRefresh = state.stream.everConnected;
          state.stream.connected = true;
          state.stream.everConnected = true;
          renderMonitorStatus();
          updateDetailStreamBadge();
          if (shouldRefresh) {
            void refreshDashboard();
          }
        });
        source.addEventListener("heartbeat", () => {});
        source.addEventListener("task_update", (event) => {
          let payload = null;
          try {
            payload = JSON.parse(event.data || "{}");
          } catch (_error) {
            return;
          }
          if (!payload) {
            return;
          }
          applyTaskUpdateToDashboard(payload);
          applyTaskUpdateToDetail(payload);
          if (payload.updated_at) {
            document.getElementById("generated-at").textContent = formatHeaderTimestamp(payload.updated_at);
          }
        });
        source.addEventListener("error", () => {
          state.stream.connected = false;
          renderMonitorStatus();
          updateDetailStreamBadge();
        });
      }

      async function handleAddProjectSubmit(event) {
        event.preventDefault();
        const submitButton = document.getElementById("submit-add-project");
        const runtimeRoot = document.getElementById("runtime-root-input").value.trim();
        const name = document.getElementById("project-name-input").value.trim();
        const projectId = document.getElementById("project-id-input").value.trim();
        if (!runtimeRoot) {
          setAddProjectStatus("Orx runtime root is required.", "error");
          return;
        }
        submitButton.disabled = true;
        setAddProjectStatus("Saving project…");
        try {
          const payload = await sendJson("/api/projects", "POST", {
            runtime_root: runtimeRoot,
            name: name || null,
            id: projectId || null,
          });
          state.selectedProjectId = payload.project.id;
          state.selectedTaskId = null;
          closeAddProjectModal();
          await refreshDashboard();
        } catch (error) {
          setAddProjectStatus(error.message || "Failed to add project.", "error");
        } finally {
          submitButton.disabled = false;
        }
      }

      async function boot() {
        try {
          document.getElementById("open-add-project").addEventListener("click", openAddProjectModal);
          document.getElementById("cancel-add-project").addEventListener("click", closeAddProjectModal);
          document.getElementById("add-project-form").addEventListener("submit", handleAddProjectSubmit);
          document.getElementById("add-project-modal").addEventListener("click", (event) => {
            if (event.target.id === "add-project-modal") {
              closeAddProjectModal();
            }
          });
          await refreshDashboard();
          connectStream();
          setInterval(() => {
            void refreshDashboard();
          }, 30000);
        } catch (error) {
          renderEmptyDetail(error.message || "Failed to load dashboard.");
        }
      }

      void boot();
    </script>
  </body>
</html>
"""


class MonitorHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        config: MonitorConfig,
        *,
        orchestrator_factory=None,
        stream_poll_interval_seconds: float = DEFAULT_STREAM_POLL_INTERVAL_SECONDS,
        stream_idle_interval_seconds: float = DEFAULT_STREAM_IDLE_INTERVAL_SECONDS,
        stream_heartbeat_seconds: float = DEFAULT_STREAM_HEARTBEAT_SECONDS,
    ) -> None:
        super().__init__(server_address, MonitorRequestHandler)
        self.config = config
        self.stream_controller = MonitorStreamController(
            lambda: self.config,
            orchestrator_factory=orchestrator_factory,
            poll_interval_seconds=stream_poll_interval_seconds,
            idle_interval_seconds=stream_idle_interval_seconds,
            heartbeat_seconds=stream_heartbeat_seconds,
        )
        self.stream_controller.start()

    @property
    def url(self) -> str:
        host, port = self.server_address[:2]
        return f"http://{host}:{port}"

    def shutdown(self) -> None:
        self.stream_controller.stop()
        super().shutdown()

    def server_close(self) -> None:
        self.stream_controller.stop()
        super().server_close()


class MonitorRequestHandler(BaseHTTPRequestHandler):
    server: MonitorHTTPServer
    protocol_version = "HTTP/1.1"

    def handle(self) -> None:
        try:
            super().handle()
        except ConnectionResetError:
            return

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send_html(_ROOT_HTML)
            return
        if parsed.path == "/healthz":
            self._send_json(HTTPStatus.OK, {"ok": True})
            return
        if parsed.path == "/api/dashboard":
            self._send_json(HTTPStatus.OK, build_dashboard_payload(self.server.config))
            return
        if parsed.path == "/api/stream":
            self._serve_stream(parsed)
            return
        if parsed.path.startswith("/api/tasks/"):
            parts = [item for item in parsed.path.split("/") if item]
            if len(parts) != 4:
                self._send_error_json(HTTPStatus.NOT_FOUND, "not_found", "unknown API route")
                return
            _, _, project_id, task_id = parts
            try:
                payload = build_task_detail_payload(
                    self.server.config,
                    project_id=project_id,
                    task_id=task_id,
                )
            except KeyError as exc:
                self._send_error_json(HTTPStatus.NOT_FOUND, "not_found", str(exc))
                return
            self._send_json(HTTPStatus.OK, payload)
            return
        self._send_error_json(HTTPStatus.NOT_FOUND, "not_found", "unknown route")

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/projects":
            if self.server.config.config_path is None:
                self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, "server_error", "monitor config path is unavailable")
                return
            try:
                payload = self._read_json_body()
                runtime_root = str(payload.get("runtime_root") or "").strip()
                docs_repo = str(payload.get("docs_repo") or "").strip()
                name = payload.get("name")
                project_id = payload.get("id")
                runtime_dir = str(payload.get("runtime_dir") or "").strip() or None
                if not runtime_root and not docs_repo:
                    raise ValueError("runtime_root is required")
                project = add_project_to_monitor_config(
                    self.server.config.config_path,
                    runtime_root=None if not runtime_root else Path(runtime_root),
                    docs_repo=None if not docs_repo else Path(docs_repo),
                    name=None if name is None else str(name),
                    project_id=None if project_id is None else str(project_id),
                    runtime_dir=runtime_dir or ".openclaw-state",
                )
                self.server.config = load_monitor_config(self.server.config.config_path)
            except (ValueError, json.JSONDecodeError) as exc:
                self._send_error_json(HTTPStatus.BAD_REQUEST, "invalid_request", str(exc))
                return
            self._send_json(
                HTTPStatus.CREATED,
                {
                    "project": {
                        "id": project.id,
                        "name": project.name,
                        "runtime_root": str(project.runtime_root),
                    }
                },
            )
            return
        self._send_error_json(HTTPStatus.NOT_FOUND, "not_found", "unknown route")

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        return

    def _send_html(self, body: str) -> None:
        encoded = body.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _send_json(self, status: HTTPStatus, payload: dict[str, object]) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _send_error_json(self, status: HTTPStatus, error: str, message: str) -> None:
        self._send_json(status, {"error": error, "message": message})

    def _serve_stream(self, parsed) -> None:
        query = parse_qs(parsed.query or "")
        project_id = _query_value(query, "project_id")
        task_id = _query_value(query, "task_id")
        subscription = self.server.stream_controller.subscribe(
            project_id=project_id,
            task_id=task_id,
            last_event_id=self.headers.get("Last-Event-ID"),
        )
        try:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            self.wfile.write(
                f"retry: {self.server.stream_controller.retry_milliseconds}\n\n".encode("utf-8")
            )
            self.wfile.flush()
            while True:
                has_item, event = self.server.stream_controller.next_event(
                    subscription,
                    timeout=self.server.stream_controller.heartbeat_seconds,
                )
                if has_item and event is None:
                    break
                if not has_item:
                    self._write_sse_event(
                        event="heartbeat",
                        payload={"generated_at": datetime.now(UTC).replace(microsecond=0).isoformat()},
                    )
                    continue
                assert event is not None
                self._write_sse_event(
                    event=event.event,
                    payload=event.data,
                    event_id=event.event_id,
                )
        except (BrokenPipeError, ConnectionResetError, OSError, ValueError):
            return
        finally:
            self.server.stream_controller.unsubscribe(subscription)

    def _write_sse_event(
        self,
        *,
        event: str,
        payload: dict[str, object],
        event_id: str | None = None,
    ) -> None:
        lines: list[str] = []
        if event_id:
            lines.append(f"id: {event_id}\n")
        lines.append(f"event: {event}\n")
        encoded = json.dumps(payload, ensure_ascii=False)
        for line in encoded.splitlines() or [""]:
            lines.append(f"data: {line}\n")
        lines.append("\n")
        self.wfile.write("".join(lines).encode("utf-8"))
        self.wfile.flush()

    def _read_json_body(self) -> dict[str, object]:
        content_length = int(self.headers.get("Content-Length") or "0")
        raw_body = self.rfile.read(content_length).decode("utf-8")
        payload = json.loads(raw_body or "{}")
        if not isinstance(payload, dict):
            raise ValueError("request body must be a JSON object")
        return payload


def create_monitor_server(
    config: MonitorConfig,
    *,
    host: str = "127.0.0.1",
    port: int | None = None,
    orchestrator_factory=None,
    stream_poll_interval_seconds: float = DEFAULT_STREAM_POLL_INTERVAL_SECONDS,
    stream_idle_interval_seconds: float = DEFAULT_STREAM_IDLE_INTERVAL_SECONDS,
    stream_heartbeat_seconds: float = DEFAULT_STREAM_HEARTBEAT_SECONDS,
) -> MonitorHTTPServer:
    if port is not None:
        return MonitorHTTPServer(
            (host, port),
            config,
            orchestrator_factory=orchestrator_factory,
            stream_poll_interval_seconds=stream_poll_interval_seconds,
            stream_idle_interval_seconds=stream_idle_interval_seconds,
            stream_heartbeat_seconds=stream_heartbeat_seconds,
        )

    last_error: OSError | None = None
    for candidate in DEFAULT_MONITOR_PORT_CANDIDATES:
        try:
            return MonitorHTTPServer(
                (host, candidate),
                config,
                orchestrator_factory=orchestrator_factory,
                stream_poll_interval_seconds=stream_poll_interval_seconds,
                stream_idle_interval_seconds=stream_idle_interval_seconds,
                stream_heartbeat_seconds=stream_heartbeat_seconds,
            )
        except OSError as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    return MonitorHTTPServer(
        (host, 0),
        config,
        orchestrator_factory=orchestrator_factory,
        stream_poll_interval_seconds=stream_poll_interval_seconds,
        stream_idle_interval_seconds=stream_idle_interval_seconds,
        stream_heartbeat_seconds=stream_heartbeat_seconds,
    )


def run_monitor_server(
    *,
    config_path: Path | None = None,
    host: str = "127.0.0.1",
    port: int | None = None,
) -> None:
    config = load_monitor_config(config_path)
    server = create_monitor_server(config, host=host, port=port)
    print(f"monitor_url={server.url}")
    print(f"projects={len(config.projects)}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()


def _query_value(query: dict[str, list[str]], key: str) -> str | None:
    values = query.get(key)
    if not values:
        return None
    value = str(values[0]).strip()
    return value or None
