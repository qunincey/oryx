# OpenClaw Local Swarm MVP-1 详细步骤

日期：2026-04-05

## 文档目的

基于 `openclaw-local-swarm-plan.md` 中的 MVP-1 定义，整理出一份可直接执行的落地步骤。

MVP-1 只打通这条主链路：

`已确认需求 -> OpenClaw 自动拆解并创建 implementation issue -> 代码仓库自动启动 worker`

这一版不包含：

- 自动 review 闭环
- review 失败后的 fix agent
- 自动 retry
- 主动发现需求

这些能力属于后续 MVP-2 / MVP-3 范围。

## MVP-1 目标

目标是验证本地优先、多 agent 编排链路是否成立，而不是一开始就做完整自治系统。

验收口径：

- 文档仓库中的一个需求被标记为已确认
- orchestrator 能自动识别该需求并拆成 implementation issue
- 对应代码仓库能收到 issue
- 系统能自动启动一个 coding worker 开始执行
- 成功时产出 branch 或 PR
- 失败时能够显式标记状态并通知人工

## 一、范围与状态机

### 1. 固定 MVP-1 范围

MVP-1 只覆盖以下能力：

- 需求触发
- 需求解析
- 任务拆解
- issue 派发
- worker 拉起
- 最小状态监控
- 最小通知

MVP-1 不覆盖以下能力：

- 自动代码 review
- 自动修复回环
- 复杂重试策略
- 跨任务依赖编排
- 主动扫描会议纪要或错误系统自动生单

### 2. 固定最小状态机

建议在 orchestrator 中显式维护以下状态：

- `proposed`
- `approved`
- `decomposed`
- `dispatched`
- `in_progress`
- `pr_opened`
- `blocked`

状态流建议如下：

`proposed -> approved -> decomposed -> dispatched -> in_progress -> pr_opened`

异常流：

`任意状态 -> blocked`

MVP-1 暂不实现以下状态：

- `review_failed`
- `fixing`
- `ready_to_merge`
- `merged`

## 二、仓库与职责划分

### 1. 文档仓库

职责：

- 存放需求文档
- 存放设计文档
- 存放会议纪要
- 存放历史决策
- 提供“需求已确认”的触发信号

建议目录结构示例：

```text
docs/
  prd/
  rfc/
  meeting-notes/
  decisions/
```

### 2. Orchestrator 仓库

职责：

- task registry
- prompt templates
- routing rules
- worker launcher
- watchdog
- notifications

建议目录结构示例：

```text
orchestrator/
  prompts/
  scripts/
  registry/
  configs/
```

### 3. 代码仓库

职责：

- 接收 implementation issue
- 被 coding worker 修改
- 使用已有 CI 做基础验证
- 产出 branch / PR

要求：

- 不承载长期业务记忆
- 不负责需求拆解
- 不负责跨仓库编排

## 三、需求输入契约

### 1. 定义“已确认需求”的唯一触发信号

MVP-1 只选一种触发方式，避免歧义。

推荐优先级：

1. 文档仓库 issue label：`ready-for-breakdown`
2. markdown frontmatter：`status: approved`
3. 设计文档 PR merge

建议第一版优先选择：

- 如果团队主要在 issue 上协作，选 label
- 如果团队主要在 markdown 文档上协作，选 frontmatter

### 2. 定义需求模板

每条“可执行需求”至少包含以下字段：

- `title`
- `background`
- `goal`
- `non_goals`
- `acceptance_criteria`
- `affected_repos`
- `references`

如果使用 markdown，建议 frontmatter 示例：

```yaml
title: Support local swarm issue dispatch
status: approved
affected_repos:
  - repo-a
  - repo-b
references:
  - https://github.com/org/specs/pull/123
```

正文建议固定章节：

- Background
- Goal
- Non-goals
- Acceptance Criteria
- Risks

### 3. 定义输入校验规则

OpenClaw 在接单前先做最小校验：

- `title` 不为空
- `goal` 不为空
- `acceptance_criteria` 不为空
- `affected_repos` 至少有一个
- `references` 至少有一条可追溯链接

校验失败时：

- 不进入拆解
- 记录错误原因
- 状态保持为 `approved` 或转为 `blocked`
- 通知人工补全文档

## 四、Task Registry 设计

### 1. 先做最轻实现

MVP-1 可以用以下任一种：

- SQLite
- JSON 文件
- YAML 文件

如果只有单机运行，推荐 SQLite；如果想先快速验证，也可以先用单个 JSON 文件。

### 2. 每条任务建议字段

- `task_id`
- `source_repo`
- `source_ref`
- `source_title`
- `target_repo`
- `target_issue_number`
- `state`
- `worker_type`
- `worker_model`
- `worktree_path`
- `tmux_session`
- `branch_name`
- `pr_url`
- `last_error`
- `created_at`
- `updated_at`

### 3. registry 的用途

- 避免重复派单
- 记录每个需求拆成了哪些实现任务
- 跟踪 worker 是否已启动
- 为 watchdog 提供巡检依据
- 为通知系统提供上下文

## 五、需求摄取与拆解

### 1. 实现需求摄取器

摄取器职责：

- 扫描文档仓库中的 `approved` 需求
- 判断该需求是否已处理过
- 读取正文和结构化字段
- 提取目标、约束、非目标和验收标准

最小实现方式：

- 定时脚本轮询
- 或由 GitHub webhook 触发

MVP-1 建议优先：

- 先做定时轮询，简单稳定，便于本地验证

### 2. 实现需求拆解器

拆解器职责：

- 识别影响仓库
- 为每个目标仓库生成一条 implementation task
- 写入 task registry
- 将任务状态更新为 `decomposed`

第一版拆解策略要保守：

- 一个目标仓库对应一个 implementation issue
- 暂不做复杂子任务树
- 暂不做跨 repo 依赖图优化

### 3. 为每个任务补齐实施上下文

拆解后每条任务至少要包含：

- 目标仓库
- 需求摘要
- 目标
- 非目标
- 验收标准
- 风险点
- 原始文档链接

这样后续 worker prompt 不需要再去拿整份业务文档。

## 六、Implementation Issue 派发

### 1. 固定 issue 模板

每个自动创建的 issue 必须包含以下部分：

- Background
- Goal
- Non-goals
- Acceptance Criteria
- Risks
- References

建议模板：

```md
## Background

...

## Goal

...

## Non-goals

...

## Acceptance Criteria

- ...

## Risks

- ...

## References

- ...
```

### 2. 固定 issue labels

建议统一打以下 labels：

- `ai-task`
- `generated-by-openclaw`
- `ready-for-worker`

可选 labels：

- `backend`
- `frontend`
- `infra`

### 3. 派单成功后的状态更新

当目标仓库 issue 创建成功后：

- 写入 `target_issue_number`
- 状态更新为 `dispatched`
- 记录目标 repo 与 issue 链接

当 issue 创建失败时：

- 状态转 `blocked`
- 记录 `last_error`
- 发通知给人工

## 七、Worker 启动与隔离执行

### 1. 选择最小 worker 组合

MVP-1 推荐固定：

- Codex 处理后端逻辑、多文件改动、复杂修复
- Claude Code 处理前端、小型功能或较快交付任务

如果第一版想减少路由复杂度，也可以先只支持一个 worker。

### 2. 实现 worker launcher

收到 `ready-for-worker` 的 implementation issue 后，执行以下动作：

1. clone 或定位目标仓库本地路径
2. 创建独立 `git worktree`
3. 创建独立分支
4. 创建独立 `tmux session`
5. 在 tmux 内启动指定 coding worker
6. 注入任务 prompt
7. 更新 registry 状态为 `in_progress`

### 3. worktree 命名规范

建议命名格式：

`../worktrees/{repo}/{task_id}-{short-slug}`

例如：

`../worktrees/repo-a/task-102-add-user-search`

### 4. branch 命名规范

建议命名格式：

`openclaw/{task_id}/{short-slug}`

例如：

`openclaw/task-102/add-user-search`

### 5. tmux session 命名规范

建议命名格式：

`swarm-{task_id}`

例如：

`swarm-task-102`

## 八、Worker Prompt 模板

### 1. Prompt 必须包含的信息

每次派发给 worker 的 prompt 至少要带：

- 当前任务标题
- 目标仓库
- 本地 worktree 路径
- GitHub issue 链接
- 背景摘要
- 目标
- 非目标
- 验收标准
- 风险点
- 输出要求

### 2. Prompt 中的输出要求

建议明确要求 worker：

- 先阅读 issue 与相关代码
- 仅修改与当前任务相关的代码
- 运行必要测试
- 提交 branch
- 创建 PR
- 在无法推进时输出阻塞原因

### 3. Prompt 中的边界约束

必须明确：

- 不要自行扩 scope
- 不要重写无关模块
- 不要修改需求定义
- 不要处理其他 issue
- 遇到 blocker 时停止并汇报

### 4. 推荐的 prompt 骨架

```text
You are the coding worker for a single implementation task.

Task:
- Title: ...
- Repo: ...
- Issue: ...

Context:
- Background: ...
- Goal: ...
- Non-goals: ...
- Acceptance Criteria: ...
- Risks: ...

Execution rules:
- Work only on this task.
- Use the assigned worktree.
- Run relevant tests before finishing.
- Open a PR when implementation is ready.
- If blocked, stop and report the blocker clearly.
```

## 九、最小 Watchdog

### 1. MVP-1 只巡检最关键的 5 个信号

- `tmux session` 是否存活
- `worktree` 是否存在
- `branch` 是否已创建
- `branch` 是否已 push
- `PR` 是否已创建

### 2. Watchdog 的输出动作

巡检结果只做以下事情：

- 更新 task registry
- 标记 `blocked`
- 发通知

MVP-1 不做：

- 自动重启 worker
- 自动 retry
- 自动换模型
- 自动切换到其他 worker

### 3. blocked 条件建议

出现以下情况之一就标记为 `blocked`：

- tmux session 意外退出
- worktree 创建失败
- git push 失败
- worker 在限定时间内无进展
- PR 创建失败

## 十、通知链路

### 1. 通知只保留最少事件

建议只通知以下事件：

- 新 implementation issue 已创建
- worker 已启动
- PR 已创建
- 任务进入 `blocked`

### 2. 通知内容建议字段

- `task_id`
- `title`
- `target_repo`
- `state`
- `issue_url`
- `pr_url`
- `last_error`

### 3. 通知渠道

第一版只接一个渠道即可：

- Telegram
- 飞书
- 企业微信

重点不是渠道丰富，而是链路可见。

## 十一、人工介入点

MVP-1 只保留两个必要人工点：

### 1. 需求确认

人工负责把需求从 `proposed` 变成 `approved`。

### 2. 异常处理与 PR 最终查看

人工负责处理：

- `blocked` 任务
- PR 打开后的最终确认

MVP-1 不追求无人值守合并。

## 十二、端到端实施顺序

建议按下面顺序推进，避免同时开太多口子。

### 阶段 1：先打通数据流

1. 定义需求模板
2. 定义 `approved` 触发规则
3. 建立 task registry
4. 实现需求摄取器
5. 实现需求拆解器

完成标准：

- 一个 `approved` 需求能被识别
- 能在 registry 中生成 `decomposed` 任务

### 阶段 2：打通 issue 派发

1. 实现 implementation issue 模板
2. 实现 GitHub issue 创建脚本
3. 将结果写回 registry

完成标准：

- 每个目标 repo 能收到标准化 issue
- 任务状态从 `decomposed` 进入 `dispatched`

### 阶段 3：打通 worker 启动

1. 实现 worktree 创建脚本
2. 实现 tmux session 创建脚本
3. 实现 worker launcher
4. 实现 prompt 注入

完成标准：

- `dispatched` 任务能进入 `in_progress`
- 本地能看到独立 worktree 和 tmux session

### 阶段 4：打通结果可见性

1. 实现最小 watchdog
2. 实现通知脚本
3. 记录 PR 链接或 blocker

完成标准：

- 成功任务能显示 `pr_opened`
- 失败任务能显示 `blocked`
- 人工能收到通知

## 十三、建议的实现任务清单

### P0

- 需求模板定稿
- `approved` 触发机制定稿
- task registry 落地
- 文档仓库扫描脚本
- 需求拆解脚本
- GitHub issue 创建脚本
- worker launcher 脚本
- worktree 创建脚本
- tmux 启动脚本

### P1

- worker prompt 模板细化
- watchdog 脚本
- 通知集成
- blocker 归档规则
- 基础运行日志

### P2

- 路由规则优化
- 多 worker 选择策略
- 进度可视化面板

## 十四、MVP-1 验收标准

满足以下条件即可认为 MVP-1 成立：

- 文档仓库中一个 `approved` 需求能被自动识别
- 系统能自动为目标代码仓库创建 implementation issue
- issue 创建后系统能自动拉起 worker
- worker 在隔离 worktree 中开始执行
- 成功时至少产出 branch 或 PR
- 失败时状态显式可见，并能通知人工
- orchestrator 本身不直接承担编码职责

## 十五、明确不做的事情

为了防止第一版失控，以下内容明确不做：

- 自动 review agent
- review 失败自动修复
- 自动 merge
- 主动扫描 Sentry / meeting notes 生单
- 复杂任务依赖图
- 自动多轮 retry
- 单个 agent 同时承担需求理解、编码、review、通知

## 十六、一句话落地策略

MVP-1 的正确做法不是“把所有自动化都做出来”，而是“用最少组件打通需求到 issue 到 worker 的链路，并把状态和失败原因显式化”。
