# Thin Local Orchestration SDK 设计文档

- 日期：2026-04-24
- 状态：Proposed
- 目标形态：先内部自用，设计上预留后续开源能力
- 参考实现：
  - `/Users/qiuxu/opensource-project/holon`
  - `/Users/qiuxu/opensource-project/wegent`

## 1. 摘要

本文档定义一个面向本地 coding worker 的轻量编排 SDK。它不是 agent 平台，也不是需求理解层，而是一个可以被上层程序直接调用的 Python 依赖包，用来稳定地启动、监控、查询、停止本机 `Codex` / `Claude Code` CLI 任务。

它的核心目标是替代当前偏重、偏高 token 消耗的 orchestration 层，只保留真正需要的运行时能力：

- `tmux` session 生命周期管理
- 本机 coding CLI 启动与监控
- 内置 `SQLite` 状态持久化
- 结构化 completion artifact 契约
- 可查询 stdout/stderr 输出
- 可选 `git worktree` 工作区策略

一句话定义：

**让 worker 用 token，让 orchestrator 用状态机。**

## 2. 背景与问题

当前 OpenClaw 风格方案在“文档驱动需求拆解 + 上层 agent 调度”上很强，但如果目标只是补一个本地 coding worker 编排流，它会引入额外复杂度：

- 调度层自身变成长期带上下文的 agent
- prompt 生成、任务理解、业务记忆与运行时控制耦合
- token 消耗集中在 orchestrator，而不是 coding worker
- 对上层调用程序来说，运行时能力不够薄

本项目希望把运行时层剥离出来，形成一个非常纯净的本地编排 SDK，由调用方负责：

- 任务理解
- prompt 生成
- 模型选择
- 需求拆解

由 SDK 负责：

- 启动 worker
- 持久化状态
- 监控 session
- 收集输出
- 返回结构化结果

## 3. 目标

### 3.1 产品目标

- 提供一个给其他程序直接调用的 Python SDK
- 第一版聚焦本地 `Codex` / `Claude Code` 编排
- 通过 `tmux + SQLite` 提供可靠的本地异步任务运行能力
- 用统一的 `result.json` 契约向调用方返回结构化结果
- 让 `git worktree` 成为可选工作区策略，而不是核心前提

### 3.2 设计目标

- 保持 runtime 边界极薄
- 不把任务理解重新塞回 SDK
- 在宿主进程重启后仍然可查询任务状态和历史输出
- 默认实现尽量简单，但为未来开源保留 adapter 和 storage 演进空间

## 4. 非目标

第一版明确不做以下能力：

- 不做 orchestrator agent
- 不做 prompt 模板系统
- 不做 PRD / issue 拆解
- 不做 GitHub issue / PR workflow
- 不做自动 code review / CI fix loop
- 不做模型路由和任务路由
- 不做远程 runner、SSH、Docker、K8s 调度
- 不做 UI、本地 daemon 或 HTTP 服务
- 不做多 agent 协作协议

## 5. 用户与使用方式

第一版的主要用户不是终端最终用户，而是调用该库的上层程序，例如：

- 本地个人 orchestrator
- 自定义 Telegram / WeCom / Slack bot
- issue-driven 自动化程序
- 文档驱动的 task dispatch 工具

调用模式以异步任务式 SDK 为主：

- `start_task()` 启动任务
- `get_task()` 查询任务状态
- `wait_task()` 等待任务完成
- `list_output()` 拉取增量输出
- `stop_task()` 停止任务

## 6. 核心设计原则

- 运行时只理解“如何跑一个 worker”，不理解“为什么跑这个 worker”
- 调用方必须传入完整 prompt
- SDK 内建 `codex` / `claude` adapter，但不内置任务规划能力
- 成功完成以 completion artifact 为准，而不是仅以进程退出为准
- 数据库存状态和索引，文件系统存大对象和真实工件
- 默认优先 `in_place` 工作区策略，`git worktree` 为显式可选

## 7. 总体架构

SDK 建议拆成 4 层：

### 7.1 Worker Adapter

负责将统一请求翻译成具体 CLI 命令。

第一版内置：

- `CodexWorkerAdapter`
- `ClaudeWorkerAdapter`

职责：

- 生成启动命令
- 准备 prompt 文件
- 注入统一 artifact 写入约定
- 暴露 worker 元信息

### 7.2 Session Runtime

负责 `tmux` session 的实际控制。

职责：

- 创建 session
- 检测 session 是否存活
- 抓取 pane 输出
- 温和停止与强制停止
- 维护 session 与 task 的绑定关系

### 7.3 Task Store

使用 `SQLite` 记录任务主状态、输出索引和事件日志。

职责：

- 持久化任务
- 持久化生命周期事件
- 持久化输出分段索引
- 支持宿主程序重启后的状态恢复

### 7.4 SDK Facade

提供给调用方的 Python API。

职责：

- 接收 `TaskRequest`
- 创建 task 目录和数据库记录
- 调用 adapter 和 runtime 启动任务
- 提供查询、等待、停止等上层接口

## 8. 核心对象模型

### 8.1 `TaskRequest`

调用方传入的启动参数。

建议字段：

- `worker_type`: `codex | claude`
- `prompt`: 完整 prompt 文本
- `cwd`: 工作目录
- `model`: 可选模型名
- `workspace_strategy`: `in_place | git_worktree`
- `branch_name`: 可选
- `worktree_path`: 可选
- `timeout_seconds`: 可选
- `metadata`: 可选扩展字段

### 8.2 `TaskRecord`

SQLite 中的主记录。

建议字段：

- `task_id`
- `worker_type`
- `status`
- `cwd`
- `workspace_strategy`
- `session_name`
- `prompt_path`
- `result_path`
- `exit_code`
- `failure_reason`
- `created_at`
- `started_at`
- `finished_at`

### 8.3 `TaskResult`

最终返回给调用方的结构化结果。

建议字段：

- `task_id`
- `status`
- `outcome`
- `summary`
- `artifacts`
- `error`
- `metadata`

### 8.4 `TaskOutputChunk`

任务增量输出片段。

建议字段：

- `task_id`
- `seq`
- `stream`
- `content`
- `created_at`

### 8.5 `WorkerAdapter`

统一 adapter 接口，供 `codex` 与 `claude` 实现。

最小职责：

- `build_launch_command()`
- `worker_type()`
- `default_result_contract()`

## 9. 任务状态模型

第一版建议只保留运行时真正需要的状态：

- `PENDING`
- `STARTING`
- `RUNNING`
- `SUCCEEDED`
- `FAILED`
- `CANCELED`
- `TIMEOUT`

补充说明：

- `STARTING` 用于 session 已创建但尚未稳定进入运行态
- `SUCCEEDED` / `FAILED` / `CANCELED` / `TIMEOUT` 都是终态
- 不单独引入复杂的 review、publish、restartable 等上层业务态

## 10. Python SDK API 草案

第一版建议接口如下：

```python
handle = orchestrator.start_task(request)

task = orchestrator.get_task(handle.task_id)

chunks = orchestrator.list_output(handle.task_id, after_seq=120)

result = orchestrator.wait_task(handle.task_id, timeout=600)

orchestrator.stop_task(handle.task_id)
```

建议对外暴露的方法：

- `start_task(request) -> TaskHandle`
- `get_task(task_id) -> TaskRecord`
- `wait_task(task_id, timeout=None) -> TaskResult`
- `stop_task(task_id) -> None`
- `list_output(task_id, after_seq=None, stream=None) -> list[TaskOutputChunk]`
- `get_result(task_id) -> TaskResult | None`

## 11. 任务生命周期

标准生命周期如下：

1. `start_task()` 被调用
2. SQLite 写入 `PENDING`
3. 生成 task 目录与 `prompt.txt`
4. adapter 生成 worker 启动命令
5. 创建 `tmux` session
6. 状态进入 `RUNNING`
7. watcher 后台抓取 stdout/stderr 和 session 状态
8. 检测到 `result.json` 后完成收口
9. 返回 `TaskResult`

关键约束：

- 任务完成不以 session 退出为准
- 任务成功以 `result.json` 为准
- exit code 是辅助信号，不是唯一完成信号

## 12. `tmux` 运行模型

第一版建议每个任务独占一个 session。

基本约定：

- session 名由 SDK 自动生成，例如 `orx-<task_id>`
- 输出通过 `tmux capture-pane` 抓取
- 输出按增量写入 `stdout.log` / `stderr.log` 与 SQLite 索引
- `stop_task()` 先温和停止，再超时强杀

这样可以保留 `tmux` 的核心价值：

- 稳定后台运行
- 中途可观测
- 可中断
- 可在宿主程序之外独立存在

## 13. Completion Artifact 契约

### 13.1 统一的 `result.json`

第一版要求 worker 结束时必须产出统一的 `result.json`。

建议结构：

```json
{
  "status": "completed",
  "outcome": "success",
  "summary": "Implemented the requested change and added tests.",
  "artifacts": [
    {"name": "summary", "path": "summary.md"},
    {"name": "patch", "path": "diff.patch"}
  ],
  "metadata": {
    "worker_type": "codex",
    "model": "gpt-5.4",
    "duration_seconds": 182.4
  },
  "error": null
}
```

字段定义：

- `status`: 当前仅支持 `completed`
- `outcome`: `success | failure | needs_human`
- `summary`: 面向调用方展示的简短结果摘要
- `artifacts`: 附属产物清单，路径相对当前 task 输出目录
- `metadata`: worker、模型、耗时等补充信息
- `error`: 失败时的人类可读错误

### 13.2 保底行为

如果 worker 正常退出但没有写 `result.json`，SDK 自动补写一个 failure 结果，错误原因为：

`worker exited without completion artifact`

这个约定借鉴了 Holon 的 manifest 思路，但比 Holon 更聚焦本地 coding worker 场景。

## 14. SQLite 与目录布局

### 14.1 数据根目录

建议默认使用：

```text
~/.orx/
  tasks.db
  tasks/
    <task_id>/
      prompt.txt
      result.json
      summary.md
      diff.patch
      stdout.log
      stderr.log
      meta.json
```

### 14.2 持久化原则

- `SQLite` 存状态和索引
- 文件系统存 prompt、artifact、日志正文

避免把大对象直接塞进数据库。

### 14.3 SQLite 表结构

第一版建议只需要 3 张核心表：

- `tasks`
  - 任务主记录
- `task_events`
  - 生命周期事件
- `task_output_chunks`
  - 输出索引与增量内容

建议的事件类型包括：

- `task_started`
- `session_created`
- `output_captured`
- `artifact_detected`
- `task_finished`
- `task_stopped`
- `task_timed_out`

## 15. 工作区策略

`git worktree` 在第一版中应被建模为可选的 `workspace_strategy`，而不是核心能力。

支持两种策略：

- `in_place`
- `git_worktree`

默认行为：

- 默认使用 `in_place`
- 只有调用方显式指定时才启用 `git_worktree`
- SDK 只负责机械性的 worktree 创建和清理，不负责复杂 git 策略判断

产品边界必须明确：

**SDK 保证任务编排，不保证代码工作区隔离。工作区隔离是可选能力。**

## 16. 错误处理与结束语义

第一版建议统一处理以下错误类型：

- `launch_error`
- `runtime_error`
- `artifact_missing`
- `timeout`
- `canceled`

### 16.1 取消语义

`stop_task()` 建议使用两段式停止：

1. 标记任务进入取消流程
2. 尝试温和停止 session
3. 超过宽限期后强制 kill
4. 最终状态写成 `CANCELED`

### 16.2 超时语义

超时后的行为建议统一为：

1. watcher 发现超过超时阈值
2. 写入超时事件
3. 停止 `tmux` session
4. 如无 `result.json`，SDK 自动补写 failure result
5. 最终状态设为 `TIMEOUT`

原则是：

**无论任务如何结束，调用方都能拿到统一结构的结果对象。**

## 17. 借鉴点

### 17.1 从 Holon 借鉴

- runtime 与 agent/skill 的边界分离
- 统一的 runtime-owned completion artifact 思路
- 结果与附属 artifact 分离

### 17.2 从 Wegent 借鉴

- 任务状态与 executor 抽象
- 任务处理器与执行器分层
- 任务可被外部程序查询和追踪的模型

### 17.3 不直接继承的部分

- 不引入 Holon 的容器执行和完整 workflow 层
- 不引入 Wegent 的服务端任务中心和远程执行器体系

## 18. MVP 验收标准

第一版完成的最低标准：

1. 可以通过 Python SDK 启动一个 `codex` 或 `claude` worker
2. 任务能自动创建并管理独立 `tmux` session
3. 调用方可以查询任务状态和增量输出
4. worker 写入 `result.json` 后，`wait_task()` 可返回结构化 `TaskResult`
5. 状态持久化到 `SQLite`，宿主程序重启后仍可查询历史任务
6. 支持停止任务与超时收口
7. 支持可选 `git worktree` 策略，但默认不依赖它
8. 不内置 prompt 生成、任务拆解、模型路由或业务记忆

## 19. 后续演进方向

如果第一版内部验证通过，后续可以按下面顺序扩展：

1. 抽象通用 command worker adapter
2. 增加事件订阅接口
3. 增加更细粒度输出游标与回放
4. 抽象 storage backend
5. 增加本地 CLI 诊断工具
6. 增加本地 daemon / JSON-RPC 作为可选外层

这些都属于后续能力，不影响第一版聚焦“本地 coding worker orchestration SDK”的核心定位。
