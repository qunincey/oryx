# OpenClaw 本地 Agent Swarm 方案整理

日期：2026-04-05

## 背景

目标不是单纯找一个会写代码的 agent，而是搭一套本地优先的多 agent 协同系统。系统需要满足以下原则：

- 需求层和实施层分离
- 上层 orchestrator 持有业务上下文、历史决策、需求文档和会议记录
- 下层 coding agents 只聚焦具体代码任务
- 任务可以被拆分、派发、监控、重试和复查
- 最终输出以 GitHub issue / PR / review 为核心闭环

这更接近 OpenClaw 风格的本地编排体系，而不是单次任务执行器。

## 核心判断

### 1. 不建议重写整套工具

没有必要从零重写 runtime、session 管理、worktree 生命周期、review 闭环和 GitHub 自动化。这样投入太大，而且很容易重造已有轮子。

### 2. 不建议以 Holon 作为整套系统的核心

Holon 更像 execution engine，而不是 local-first orchestrator。

Holon 当前更适合：

- `holon run`：一次性执行任务
- `holon solve`：处理 GitHub issue / PR
- `github-review` / `github-pr-fix`：代码审查和修复闭环

Holon 不太像以下系统的中心：

- 本地常驻 orchestrator
- 需求驱动派单中心
- 长期 memory + task registry 中控
- 多 worker 的 worktree / tmux 调度层

结论：Holon 可以保留为可选 worker backend，但不建议放在主架构顶层。

### 3. 更推荐以 OpenClaw 为基础搭建本地编排层

更贴近目标的架构是：

- OpenClaw 做 orchestrator
- Codex / Claude Code 做 coding workers
- GitHub Actions 做验证与策略门禁
- 必要时引入 Holon 承担某些 GitHub execution flow

这条路线更符合“需求和实施分离”的原则，也更像目标中的那篇 OpenClaw 博文描述的系统。

## 推荐架构

### 总体分层

#### A. 需求与编排层

由 OpenClaw 承担，职责包括：

- 读取文档仓库中的需求、设计、会议纪要和历史决策
- 判断影响哪些代码仓库
- 为每个目标仓库生成 implementation issue
- 选择合适的 worker 和模型
- 管理任务状态、重试策略和通知

这层不直接写代码，重点是任务理解和调度。

#### B. 实施层

由 coding workers 承担，职责包括：

- 拉取目标仓库上下文
- 在隔离 worktree 中实现需求
- 提交 commit、push branch、创建 PR
- 根据 review / CI 结果迭代修复

建议优先使用：

- Codex：后端逻辑、复杂修复、多文件改动
- Claude Code：前端、较快的实施任务、部分 git 操作

#### C. 验证与审查层

由 CI 和单独的 review agents 承担，职责包括：

- lint / typecheck / unit test / integration test
- UI 改动截图要求
- PR review
- 失败后触发修复 agent

## 推荐的最小工具组合

### 第一优先级组合

- Orchestrator：OpenClaw
- Workspace isolation：`git worktree`
- Session control：`tmux`
- Coding worker：Codex / Claude Code
- Validation：GitHub Actions
- Notification：Telegram / 飞书 / 企业微信任选其一

这是最 local-first、最干净的版本。

### Holon 的建议角色

只在以下场景引入 Holon：

- 你需要现成的 issue -> PR 自动化流程
- 你希望复用 `holon solve`
- 你想保留 `manifest.json` / `summary.md` / `diff.patch` 这类工件契约
- 你想复用 Holon 的 GitHub review / fix skills

如果没有这些明确诉求，不建议把 Holon 放进第一版主链路。

## 推荐工作流

### 1. 文档仓库沉淀需求

单独维护一个文档仓库，例如：

- `product-brain`
- `specs`
- `strategy`

文档仓库存放：

- 需求文档
- 设计文档
- 会议纪要
- 客户反馈
- 决策记录

建议为“可执行需求”定义明确触发信号，例如：

- issue 打上 `ready-for-breakdown`
- 设计文档 PR merge
- markdown 文档进入 `approved` 状态

### 2. OpenClaw 读取需求并拆解任务

OpenClaw 做以下事情：

- 提取需求目标和约束
- 判断影响哪些仓库
- 拆成多个 implementation issues
- 写入验收标准和非目标说明
- 记录任务依赖关系

### 3. 向代码仓库派发 GitHub issues

每个 issue 建议包含：

- 背景
- 目标
- 非目标
- 验收标准
- 风险点
- 相关文档链接

### 4. 启动实施 agent

对每个 issue：

- 创建独立 worktree
- 启动独立 tmux session
- 向 Codex 或 Claude Code 发送定制 prompt

### 5. 自动监控执行状态

由 watchdog 定时巡检：

- tmux session 是否存活
- branch 是否已 push
- PR 是否已创建
- CI 是否通过
- review 是否存在 blocker
- 是否需要 retry / escalate

### 6. 自动 review 和 fix

PR 创建后启动 review agents：

- AI review
- 必要时再触发 fix agent

### 7. 人工最终确认

当以下条件满足时通知人工：

- PR 已创建
- CI 通过
- review 通过或仅剩低优先级建议
- 截图 / 说明齐全

## 关键设计原则

### 1. 不要让 orchestrator 直接承担 coding worker 的职责

上层 orchestrator 应该负责：

- 理解需求
- 提供上下文
- 派发任务
- 监控和重试

下层 worker 才负责：

- 修改代码
- 运行测试
- 提交 PR

### 2. 不要把业务上下文和代码上下文塞进同一个 agent

这是上下文污染问题。

推荐做法：

- orchestrator 持有长期业务记忆
- worker 只拿当前任务需要的代码上下文
- review agent 只拿 PR diff 和验证标准

### 3. 状态机必须显式化

不要只靠 issue / PR 状态隐式推断。建议维护任务状态表，例如：

- `proposed`
- `approved`
- `decomposed`
- `dispatched`
- `in_progress`
- `pr_opened`
- `review_failed`
- `fixing`
- `ready_to_merge`
- `merged`
- `blocked`

## 第一版 MVP 建议

### MVP-1：半自动派单

目标：

- 文档仓库需求一旦被标记为已确认
- OpenClaw 自动生成目标 issue
- 代码仓库收到 issue 后启动 worker

这一版不要求完全自动 retry，只要先打通链路。

### MVP-2：自动 review 闭环

目标：

- PR 打开后自动触发 review
- review / CI 失败后自动触发 fix agent
- 最终只在 ready-to-merge 时通知人工

### MVP-3：主动发现任务

目标：

- 扫描 meeting notes
- 扫描 error backlog / Sentry
- 扫描 changelog / docs drift
- 自动生成任务并进入派发流程

## 实施建议

### 建议仓库分工

#### 1. 文档仓库

建议单独维护一个需求仓库，专门承载：

- PRD
- RFC
- 会议纪要
- 客户反馈
- 需求讨论

#### 2. Orchestrator 仓库

建议单独维护一个轻量 orchestrator 仓库，负责：

- task registry
- prompt templates
- routing rules
- retry policy
- notifications
- watchdog scripts

#### 3. 代码仓库

各业务仓库只负责：

- 接收 issues
- 被 worker 修改
- 跑 CI
- 接受 review / fix

## 具体选型建议

### 最推荐

- OpenClaw：中控与记忆层
- Codex / Claude Code：实施层
- git worktree + tmux：任务隔离与过程控制
- GitHub Actions：验证与门禁

### 次优方案

- OpenClaw：中控
- Holon：特定 GitHub execution flow
- Codex / Claude Code：日常 coding worker

适合已经明确想复用 Holon issue/PR 执行能力的情况。

### 不推荐

- 直接围绕 Holon 搭整套 orchestrator
- 一开始就重写完整 runtime
- 让一个 agent 同时负责需求理解、编码、review 和运营通知

## 一句话结论

推荐路线是：

**以 OpenClaw 为基础，在本地搭一层干净的 orchestrator；以 Codex / Claude Code 为主要 coding workers；必要时把 Holon 作为可选 execution backend 接入，而不是把 Holon 作为整套系统的中心。**

这条路线最贴近目标中的 OpenClaw 式工作流，也最符合“多 agent 协同、需求和实施分离”的原则。
