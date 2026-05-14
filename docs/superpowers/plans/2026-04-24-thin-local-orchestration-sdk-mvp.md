# Thin Local Orchestration SDK MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first thin local orchestration SDK as a Python package that launches Codex or Claude Code tasks in local tmux sessions, persists task state in SQLite, and returns unified `result.json` results.

**Architecture:** Add a new standalone `orx` package instead of extending `openclaw_bridge`, because the spec explicitly removes OpenClaw dispatch and issue semantics from the runtime boundary. The SDK will keep a small object model, a SQLite-backed task store, a tmux session launcher, workspace strategies, worker adapters, and a polling reconciler that advances task state whenever the facade is called.

**Tech Stack:** Python 3.11, `sqlite3`, `dataclasses`, local filesystem, `tmux`, optional `git worktree`, `unittest`

---

## Proposed Package Structure

- Create: `orx/__init__.py`
- Create: `orx/models.py`
- Create: `orx/adapters.py`
- Create: `orx/store.py`
- Create: `orx/runtime.py`
- Create: `orx/artifacts.py`
- Create: `orx/orchestrator.py`
- Create: `orx/worker_wrapper.py`
- Create: `tests/test_orx_adapters.py`
- Create: `tests/test_orx_store.py`
- Create: `tests/test_orx_orchestrator.py`
- Modify: `pyproject.toml`

## Core Object Model

- `TaskRequest`
  - `worker_type`, `prompt`, `cwd`, `model`, `workspace_strategy`, `branch_name`, `worktree_path`, `timeout_seconds`, `metadata`
- `TaskRecord`
  - public runtime state plus task paths, session name, workspace path, timeout, exit code, failure reason, timestamps, log offsets
- `TaskResult`
  - normalized `result.json` payload returned by `wait_task()` and `get_result()`
- `TaskOutputChunk`
  - incremental persisted output chunks with `seq`, `stream`, `content`, `created_at`
- `WorkerAdapter`
  - `worker_type()`, `build_prompt()`, `build_launch_command()`, `default_result_contract()`

## SQLite Schema

```sql
CREATE TABLE IF NOT EXISTS tasks (
  task_id TEXT PRIMARY KEY,
  worker_type TEXT NOT NULL,
  status TEXT NOT NULL,
  cwd TEXT NOT NULL,
  workspace_strategy TEXT NOT NULL,
  workspace_path TEXT NOT NULL,
  branch_name TEXT,
  session_name TEXT NOT NULL,
  prompt_path TEXT NOT NULL,
  result_path TEXT NOT NULL,
  stdout_log_path TEXT NOT NULL,
  stderr_log_path TEXT NOT NULL,
  runtime_meta_path TEXT NOT NULL,
  exit_code INTEGER,
  failure_reason TEXT,
  failure_message TEXT,
  timeout_seconds INTEGER,
  metadata_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT,
  updated_at TEXT NOT NULL,
  stdout_offset INTEGER NOT NULL DEFAULT 0,
  stderr_offset INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS task_events (
  event_id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task_output_chunks (
  task_id TEXT NOT NULL,
  seq INTEGER NOT NULL,
  stream TEXT NOT NULL,
  content TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (task_id, seq)
);
```

## tmux Runtime Shape

- `TmuxSessionLauncher.start()` runs one task per session: `orx-<task_id>`
- Launch target is a runtime-owned Python wrapper that:
  - starts the adapter-generated worker command
  - mirrors worker stdout/stderr to both tmux pane and `stdout.log` / `stderr.log`
  - writes runtime metadata including exit code after the worker exits
- `stop_task()` uses two phases:
  - `send_interrupt()` via `tmux send-keys C-c`
  - `kill_session()` after the grace window
- `session_exists()` is the source of truth for liveness

## Result Contract

`result.json` remains the completion contract:

```json
{
  "status": "completed",
  "outcome": "success",
  "summary": "Implemented the requested change and added tests.",
  "artifacts": [
    {"name": "summary", "path": "summary.md"}
  ],
  "metadata": {
    "worker_type": "codex",
    "model": "gpt-5.4"
  },
  "error": null
}
```

Normalization rules:

- accept only terminal `status="completed"`
- map `outcome=success` to `TaskStatus.SUCCEEDED`
- map `outcome=failure|needs_human` to a terminal task with non-success result
- if the worker exits without `result.json`, synthesize a failure artifact with `failure_reason="artifact_missing"`
- if orchestration itself fails, synthesize a failure artifact with `failure_reason="launch_error"` or `failure_reason="runtime_error"`

## Watcher / Timeout / Stop Semantics

- The SDK stays thin by reconciling tasks inside facade calls instead of introducing a daemon.
- Each public read path calls a single watcher tick:
  - ingest new log bytes into SQLite output chunks
  - inspect `result.json`
  - inspect session liveness
  - enforce timeout if `started_at + timeout_seconds` has elapsed
- Terminal semantics:
  - `SUCCEEDED`: valid completion artifact with `outcome=success`
  - `FAILED`: launch/runtime error, invalid artifact, or artifact missing
  - `CANCELED`: explicit `stop_task()` after best-effort interrupt/kill
  - `TIMEOUT`: timeout detected and session stopped

## Test Strategy

- Storage tests:
  - schema creation
  - create/update/get task
  - append/list output chunks with `after_seq`
- Adapter tests:
  - Codex command construction
  - Claude command construction
  - prompt contract injection includes `result.json` instructions
- Reconciliation tests:
  - running task becomes succeeded when valid `result.json` appears
  - exited task without artifact becomes failed with synthesized result
  - timeout transitions to `TIMEOUT`
  - stop transitions to `CANCELED`
  - runtime launcher failure becomes `launch_error`
- Runtime tests avoid real tmux by swapping in a fake `SessionLauncher`

### Task 1: Scaffold the new thin SDK package

**Files:**
- Create: `orx/__init__.py`
- Create: `orx/models.py`
- Modify: `pyproject.toml`
- Test: `tests/test_orx_store.py`

- [ ] **Step 1: Write the failing package import test**

```python
from orx import Orchestrator, TaskRequest
```

- [ ] **Step 2: Run the import-focused test to verify it fails**

Run: `python -m unittest tests.test_orx_store -v`
Expected: `ModuleNotFoundError: No module named 'orx'`

- [ ] **Step 3: Add the package skeleton and export surface**

```python
from .models import TaskRequest, TaskRecord, TaskResult, TaskOutputChunk
from .orchestrator import Orchestrator
```

- [ ] **Step 4: Update setuptools packaging**

```toml
[tool.setuptools]
packages = ["openclaw_bridge", "orx"]
```

- [ ] **Step 5: Re-run the import-focused test**

Run: `python -m unittest tests.test_orx_store -v`
Expected: import succeeds and later assertions fail on missing implementation

### Task 2: Define the object model and result contract helpers

**Files:**
- Create: `orx/models.py`
- Create: `orx/artifacts.py`
- Test: `tests/test_orx_adapters.py`
- Test: `tests/test_orx_orchestrator.py`

- [ ] **Step 1: Write failing tests for result parsing and fallback synthesis**

```python
result = load_task_result(result_path, task_id="task-1")
assert result.outcome == "success"

fallback = synthesize_failure_result(task_id="task-2", reason="artifact_missing")
assert fallback.error == "worker exited without completion artifact"
```

- [ ] **Step 2: Run the focused tests to verify they fail**

Run: `python -m unittest tests.test_orx_adapters tests.test_orx_orchestrator -v`
Expected: import or symbol errors for missing result helpers

- [ ] **Step 3: Implement dataclasses/enums and artifact helpers**

```python
class TaskStatus(str, Enum):
    PENDING = "PENDING"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELED = "CANCELED"
    TIMEOUT = "TIMEOUT"
```

```python
def synthesize_failure_result(...):
    return TaskResult(
        task_id=task_id,
        status="completed",
        outcome="failure",
        summary=summary,
        artifacts=[],
        error=error,
        metadata=metadata,
    )
```

- [ ] **Step 4: Re-run the focused tests**

Run: `python -m unittest tests.test_orx_adapters tests.test_orx_orchestrator -v`
Expected: result-contract tests pass and facade tests now fail deeper

### Task 3: Implement worker adapters for Codex and Claude

**Files:**
- Create: `orx/adapters.py`
- Test: `tests/test_orx_adapters.py`

- [ ] **Step 1: Write failing adapter command tests**

```python
command = CodexWorkerAdapter().build_launch_command(context)
assert "codex" in command
assert str(context.prompt_path) in command

command = ClaudeWorkerAdapter().build_launch_command(context)
assert "claude" in command
assert "--print" not in command
```

- [ ] **Step 2: Run the adapter tests to verify they fail**

Run: `python -m unittest tests.test_orx_adapters -v`
Expected: adapter symbols missing or command mismatch failures

- [ ] **Step 3: Implement adapter registry and prompt contract injection**

```python
class WorkerAdapter(Protocol):
    def worker_type(self) -> str: ...
    def build_prompt(self, request: TaskRequest, task_dir: Path) -> str: ...
    def build_launch_command(self, context: LaunchContext) -> str: ...
```

- [ ] **Step 4: Re-run the adapter tests**

Run: `python -m unittest tests.test_orx_adapters -v`
Expected: adapter tests pass

### Task 4: Implement the SQLite task store

**Files:**
- Create: `orx/store.py`
- Test: `tests/test_orx_store.py`

- [ ] **Step 1: Write failing store tests for create/update/output indexing**

```python
store.create_task(record)
saved = store.get_task(record.task_id)
assert saved.status == TaskStatus.PENDING

seqs = store.append_output_chunks("task-1", [("stdout", "hello")])
assert seqs == [1]
```

- [ ] **Step 2: Run the store tests to verify they fail**

Run: `python -m unittest tests.test_orx_store -v`
Expected: missing `TaskStore` or SQLite implementation failures

- [ ] **Step 3: Implement schema bootstrapping and CRUD helpers**

```python
class TaskStore:
    def create_task(self, record: TaskRecord) -> None: ...
    def update_task(self, task_id: str, **fields: object) -> TaskRecord: ...
    def append_output(self, task_id: str, stream: str, content: str) -> TaskOutputChunk: ...
```

- [ ] **Step 4: Re-run the store tests**

Run: `python -m unittest tests.test_orx_store -v`
Expected: store tests pass

### Task 5: Implement workspace preparation and tmux session launching

**Files:**
- Create: `orx/runtime.py`
- Create: `orx/worker_wrapper.py`
- Test: `tests/test_orx_orchestrator.py`

- [ ] **Step 1: Write failing tests for in-place and git-worktree preparation plus launch errors**

```python
workspace = WorkspaceManager(...).prepare(task)
assert workspace.path == request.cwd

with self.assertRaises(RuntimeError):
    launcher.start("orx-task-1", cwd, command)
```

- [ ] **Step 2: Run the runtime-focused tests to verify they fail**

Run: `python -m unittest tests.test_orx_orchestrator -v`
Expected: missing runtime classes or launch handling failures

- [ ] **Step 3: Implement runtime abstractions**

```python
class SessionLauncher(Protocol):
    def session_exists(self, session_name: str) -> bool: ...
    def start(self, session_name: str, cwd: Path, command: str) -> None: ...
    def send_interrupt(self, session_name: str) -> None: ...
    def kill_session(self, session_name: str) -> None: ...
```

- [ ] **Step 4: Re-run the runtime-focused tests**

Run: `python -m unittest tests.test_orx_orchestrator -v`
Expected: fake-launcher tests pass, remaining watcher assertions still fail

### Task 6: Implement the facade and watcher-driven state machine

**Files:**
- Create: `orx/orchestrator.py`
- Modify: `orx/store.py`
- Modify: `orx/artifacts.py`
- Test: `tests/test_orx_orchestrator.py`

- [ ] **Step 1: Write failing facade tests for the MVP API**

```python
handle = orchestrator.start_task(request)
task = orchestrator.get_task(handle.task_id)
chunks = orchestrator.list_output(handle.task_id)
result = orchestrator.wait_task(handle.task_id, timeout=1)
```

- [ ] **Step 2: Run the orchestrator tests to verify they fail**

Run: `python -m unittest tests.test_orx_orchestrator -v`
Expected: missing API methods or incorrect terminal-state behavior

- [ ] **Step 3: Implement facade methods and reconciliation**

```python
class Orchestrator:
    def start_task(self, request: TaskRequest) -> TaskHandle: ...
    def get_task(self, task_id: str) -> TaskRecord: ...
    def wait_task(self, task_id: str, timeout: float | None = None) -> TaskResult: ...
    def stop_task(self, task_id: str) -> None: ...
    def list_output(self, task_id: str, after_seq: int | None = None, stream: str | None = None) -> list[TaskOutputChunk]: ...
    def get_result(self, task_id: str) -> TaskResult | None: ...
```

- [ ] **Step 4: Re-run the orchestrator tests**

Run: `python -m unittest tests.test_orx_orchestrator -v`
Expected: orchestrator tests pass

### Task 7: Run full verification and document scope

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add a short README section for the new SDK package**

```md
## Thin Local Orchestration SDK

Import `orx.Orchestrator` for local tmux-backed worker orchestration.
```

- [ ] **Step 2: Run the full Python test suite**

Run: `python -m unittest discover -s tests -v`
Expected: all tests pass

- [ ] **Step 3: Record remaining non-MVP items**

```text
- no daemon process
- no HTTP or UI layer
- no remote runners
- no event subscription API yet
```
