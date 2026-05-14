# OpenClaw MVP-1 操作文档

本文档按当前仓库实现重写，对齐 `openclaw_bridge.cli`、`openclaw_bridge.specs` 和 `orx` 的真实行为，不再沿用早期设计稿里的旧表述。

当前这版 MVP-1 对外的主操作路径已经是 skill-first，稳定覆盖的链路应理解为：

`初始化文档仓库 -> 通过 skill 讨论需求并沉淀 PRD artifact -> 通过 skill 派发 approved PRD -> 启动本地 worker -> watchdog 收口并自动 commit`

还没有覆盖的部分也要先说清楚：

- 没有自动 push
- 没有自动开 PR 后回写 `pr_url`
- 没有 review / CI fix / merge 闭环
- 没有复杂重试编排，只有最小 `restartable -> relaunch`

## 1. 当前实现到底包含什么

### 1.1 已实现

- `init-docs-repo`
  - 初始化一个 OpenClaw 文档仓库
- `dispatch-approved` / `ingest`
  - 读取 `approved` PRD
  - 校验最小字段
  - 按 `affected_repos` 一仓一 task 拆解
  - 生成 issue artifact 和 worker prompt
  - 可选创建 GitHub / GitLab issue
- `launch-workers`
  - 通过 `orx` 启动本地 coding worker
  - 默认用 git worktree 隔离工作目录
  - 通过 tmux 托管 session
- `reconcile-workers`
  - 将 registry 里的 task 状态与 `orx` 运行时状态对齐
  - 可选重启安全失败的 task
- `watch-workers`
  - 运行一次 watchdog tick
  - 收敛状态
  - 可选自动重启 `restartable`
  - 对成功完成的 task 自动 `git add -A` + `git commit`
  - 记录事件日志
  - 可选向 webhook 发通知

### 1.2 未实现

- 自动创建 PR 并持久化 PR URL
- 自动 review
- 自动修复 review / CI
- 自动 merge
- 多 task 依赖编排

`worker-prompt.txt` 里会提示 worker “实现完成后开 PR”，但 bridge 本身当前不会检测或记录这个动作。

## 2. 快速结论

如果你只想按当前实现跑通 MVP-1，推荐照这个顺序操作：

1. 安装 CLI：`python3 -m pip install -e /Users/qiuxu/opensource-project/ai-code-agent`
2. 用 `openclaw-init-docs-repo` 通过问答方式初始化 docs repo
3. 用 `openclaw-project-bootstrap` / `openclaw-discussion-to-prd` 在 docs repo 里推进需求
4. 需求成熟后，由 `openclaw-prd-to-repo-issues` 落 PRD artifact 并派发
5. 需要排错或手动兜底时，再直接运行 `init-docs-repo` 或 `dispatch-approved`
6. 跑 `launch-workers`
7. 用定时器或人工循环跑 `watch-workers --restart --once`

如果你要的是“只生成 artifact，不真的建 issue”，当前实现也支持，但默认不会把这类 task 自动 launch，因为它们会停留在 `decomposed`，而不是 `dispatched`。

## 2.1 先澄清一件事：现在是 skill-first，不是手工-first

按当前 skill 定义，面向用户的推荐路径已经不是：

- 手动写 PRD
- 手动敲 `dispatch-approved`

而是：

- 用 `openclaw-init-docs-repo` 收集初始化必填项并创建 docs repo
- 用 `openclaw-project-bootstrap` 建立 docs repo 上下文
- 用 `openclaw-discussion-to-prd` 把成熟讨论沉淀成 `docs/prd/*.md`
- 用 `openclaw-prd-to-repo-issues` 直接完成派发

也就是说：

- 对外操作层：skill/agent 驱动
- 底层 bridge 契约层：仍然消费 `docs/prd/*.md`

所以“已经不是手动写 PRD 了”这个理解是对的；更准确地说，是“不再把人工编辑 PRD 当成主流程”，但底层仍然要有 PRD markdown 这个 artifact，供 `dispatch-approved` 消费。

## 3. 前置条件

### 3.1 安装 CLI

```bash
cd /Users/qiuxu/opensource-project/ai-code-agent
python3 -m pip install -e .
```

命令入口是：

```bash
openclaw-mvp1 --help
```

也可以直接用模块方式：

```bash
python3 -m openclaw_bridge.cli --help
```

### 3.2 本机依赖

如果要走完整主链路，至少需要：

- `git`
- `tmux`
- `codex` 或 `claude`
- 目标代码仓库本地可访问，且目录下有 `.git`

如果要自动创建 GitHub issue，还需要：

- `gh`
- `gh auth login` 已完成

如果要自动创建 GitLab issue，还需要：

- `glab`
- `glab auth login` 已完成，且能访问目标 GitLab host

### 3.3 docs repo 如何被识别

当前实现会向上查找包含 `.openclaw/` 的祖先目录，并把它当作 docs repo 根目录。

所以：

- `dispatch-approved` 只接受 `docs/prd/` 下面的单个 Markdown 文件，或其子目录
- `launch-workers` / `reconcile-workers` / `watch-workers` 可以传 docs repo 根目录，或者根目录里的任意路径

## 4. 初始化文档仓库

### 4.1 初始化命令

推荐主路径：

- 在 Codex 或 Claude Code 中使用 `openclaw-init-docs-repo`
- 由它按问答方式收集 `target_dir`、`project_name`、`project_description`
- 收集完成后由 skill 自行执行初始化

CLI 兜底路径：

```bash
openclaw-mvp1 init-docs-repo \
  --target-dir /absolute/path/to/docs-repo \
  --project-name "Example Project" \
  --project-description "Short summary of what this project does"
```

### 4.2 初始化后会生成什么

关键内容包括：

- `AGENTS.md`
- `docs/README.md`
- `docs/project/project-overview.md`
- `docs/modules/module-map.md`
- `docs/prd/README.md`
- `docs/rfc/README.md`
- `docs/meeting-notes/README.md`
- `.openclaw/config.json`
- `.openclaw/spec-template.md`
- `.openclaw/prompts/implementation-issue.md.tmpl`
- `.openclaw/prompts/worker-task.txt.tmpl`
- `.openclaw-state/registry.json`
- `.codex/skills/openclaw-init-docs-repo/`
- `.codex/skills/openclaw-project-bootstrap/`
- `.codex/skills/openclaw-discussion-to-prd/`
- `.codex/skills/openclaw-prd-to-repo-issues/`
- `.claude/agents/openclaw-init-docs-repo.md`
- `.claude/agents/openclaw-project-bootstrap.md`
- `.claude/agents/openclaw-discussion-to-prd.md`
- `.claude/agents/openclaw-prd-to-repo-issues.md`

还会做两件事：

- 往 docs repo 的 `.gitignore` 里追加 `.openclaw-state/`
- 把同样的 Codex skills / Claude agents 安装到全局目录

全局安装位置：

- Codex：`$CODEX_HOME/skills` 或 `~/.codex/skills`
- Claude Code：`$CLAUDE_HOME/agents` 或 `~/.claude/agents`

初始化完成后，当前 CLI 会明确提示重启 Codex 和 Claude Code，让新安装的全局技能生效。

### 4.3 初始化后的默认工作方式

初始化完成后，推荐直接在 docs repo 内用这些 skill 工作：

- `openclaw-init-docs-repo`
  - 用问答方式收集初始化必填信息并创建 docs repo
- `openclaw-project-bootstrap`
  - 补齐 `docs/project/`、`docs/modules/` 和 `.openclaw/config.json`
- `openclaw-discussion-to-prd`
  - 把需求讨论沉淀成 `docs/prd/*.md`
- `openclaw-prd-to-repo-issues`
  - 在 PRD 已批准时，直接完成 repo-scoped issue 派发

这些 skill 的约定已经是：

- 能自动跑 bridge CLI 的地方就自动跑
- 除非本地工具或凭证缺失，否则不把 shell 命令甩回给用户手动执行

## 5. 底层 PRD 输入契约

这一节描述的是 bridge 底层真正消费的 artifact 契约，不代表推荐用户手工维护。

### 5.1 最小必填字段

当前 `dispatch-approved` 在真正拆解前，最少要求：

- `title`
- `goal`
- `acceptance_criteria`
- `affected_repos`
- `references`

审批判断来自：

```yaml
status: approved
```

这里的 `approved` 可以通过 `.openclaw/config.json` 里的 `approved_status` 改掉，默认值就是 `approved`。

### 5.2 skill 产出的目标结构

正常路径下，这份结构应由 `openclaw-discussion-to-prd` 生成或补齐；只有在排错、迁移旧文档、或手工兜底时，才需要你亲自编辑。

当前实现最稳妥的结构是：

```md
---
title: Support local swarm issue dispatch
status: approved
affected_repos:
  - repo-a
references:
  - https://github.com/your-org/specs/pull/123
---

# Support local swarm issue dispatch

## Background

背景说明。

## Goal

明确要达成的结果。

## Agreed Scope

- 全局范围 1

## Non-goals

- 不做的内容

## Acceptance Criteria

1. 可验证结果 1

## Impacted Modules

- owner-module -> repo-a

## Repo-specific Scope

### repo-a

- Summary: 这个仓库为什么在范围内
- Modules: owner-module
- Change: 需要改什么
- Boundary: 不要改什么
- Acceptance: 仓库级验收标准

## Risks

- 已知风险
```

### 5.3 当前 parser 的几个注意点

- frontmatter 是自定义简化解析，不是完整 YAML parser
- 最稳妥的是简单标量和简单数组，不要写复杂嵌套
- `title` 可以来自 frontmatter，也可以来自正文的 `# H1`
- `references` 可以放 frontmatter，也可以放 `## References` / `## 参考`
- `affected_repos` 按当前实现仍然建议放在 frontmatter
- 正文章节支持中英文别名

当前已经支持的中文章节别名包括：

- `背景`
- `目标`
- `约定范围`
- `非目标`
- `验收标准`
- `影响模块`
- `仓库实施范围`
- `仓库范围`
- `按仓库拆解`
- `参考`
- `参考资料`
- `风险`

### 5.4 什么文件会被跳过

`docs/prd/**/README.md` 会在 dispatch 时被自动跳过。

## 6. 派发 approved PRD

### 6.0 这一节主要用于排错和手动兜底

按当前 skill 定义，批准后的派发动作原则上应由：

- `openclaw-prd-to-repo-issues`

直接执行。

下面这些 CLI 细节仍然重要，因为 skill 最终调用的就是它们；但它们更适合作为：

- 排错说明
- 凭证异常时的兜底手册
- 验证 bridge 行为的底层参考

### 6.1 `dispatch-approved` 的输入边界

当前命令只接受两种输入：

- `docs/prd/` 下的单个 `.md` 文件
- `docs/prd/` 下的单个子目录

明确不接受：

- docs repo 根目录
- `docs/prd` 根目录

这是实现里故意加的保护，避免一次误派发整个规格树。

### 6.2 只做拆解，不建 issue

```bash
openclaw-mvp1 dispatch-approved \
  --docs-repo /absolute/path/to/docs-repo/docs/prd/feature-a.md
```

这一步会：

- 扫描传入文件，或传入子目录下的所有 `*.md`
- 跳过 `README.md`
- 校验 `status == approved_status`
- 按 `affected_repos` 一仓一 task 拆解
- 写入 `.openclaw-state/registry.json`
- 生成 `.openclaw-state/artifacts/<task-id>/implementation-issue.md`
- 生成 `.openclaw-state/artifacts/<task-id>/worker-prompt.txt`

此时 task 的状态是：

- `decomposed`

也就是说，这一步只完成“拆解”，并没有进入“可 launch”状态。

### 6.3 正式创建 issue

```bash
openclaw-mvp1 dispatch-approved \
  --docs-repo /absolute/path/to/docs-repo/docs/prd/feature-a.md \
  --create-issues
```

创建 issue 成功后，task 会变成：

- `dispatched`

同时会把这些信息写回 registry：

- `target_issue_number`
- `target_issue_url`
- `updated_at`

并且会重新渲染 artifact，让 `worker-prompt.txt` 里的 issue 链接从 `pending-issue` 变成真实 URL。

### 6.4 dry-run 的真实语义

```bash
openclaw-mvp1 dispatch-approved \
  --docs-repo /absolute/path/to/docs-repo/docs/prd/feature-a.md \
  --create-issues \
  --dry-run
```

当前实现里：

- `--dry-run` 只有和 `--create-issues` 一起使用时才有意义
- 它会把 task 直接标记成 `dispatched`
- 会写入一个假的 issue URL
- 会写入 `target_issue_number = DRY-RUN-task-xxxx`

这意味着一个非常重要的边界：

- 如果你先跑了 `--create-issues --dry-run`
- 后面再直接跑一次真实的 `--create-issues`

当前实现不会自动补建真实 issue，因为 task 已经有 `target_issue_number` 了。

所以 dry-run 更适合：

- 在临时 docs repo 副本上验证
- 或验证后手动清理对应 registry task，再正式派发

### 6.5 幂等与去重

dispatch 的去重键是：

- `source_ref + target_repo`

同一份 PRD 对同一个 repo 重跑时：

- 不会重复创建 task
- 会重新渲染 artifact
- 如果还没有 `target_issue_number`，那么 `--create-issues` 仍然会尝试补建 issue
- 如果已经有 `target_issue_number`，这条 task 会被标记到 `skipped`

## 7. issue 派发配置

### 7.1 `repo_mappings` 支持的形式

当前实现支持：

- `github_repo`
- `gitlab_repo`
- `repo + issue_provider`

例如：

```json
{
  "repo_mappings": {
    "repo-a": {
      "github_repo": "org/repo-a",
      "local_path": "/absolute/path/to/repo-a",
      "labels": ["backend"]
    }
  }
}
```

或：

```json
{
  "repo_mappings": {
    "repo-a": {
      "gitlab_repo": "group/subgroup/repo-a",
      "gitlab_host": "gitlab.example.com",
      "local_path": "/absolute/path/to/repo-a"
    }
  }
}
```

### 7.2 GitHub 行为

GitHub issue 必须通过 `gh issue create` 创建。

需要：

- 本机可执行 `gh`
- 已完成 GitHub 认证

### 7.3 GitLab 行为

GitLab issue 必须通过 `glab issue create` 创建。

需要：

- `gitlab_repo`
- 本机可执行 `glab`
- 已完成 GitLab 认证

## 8. 启动 worker

### 8.1 推荐命令

```bash
openclaw-mvp1 launch-workers \
  --docs-repo /absolute/path/to/docs-repo
```

默认只会拾取这两种状态：

- `dispatched`
- `restartable`

### 8.2 和 dispatch 连用

也可以这样一把做完：

```bash
openclaw-mvp1 dispatch-approved \
  --docs-repo /absolute/path/to/docs-repo/docs/prd/feature-a.md \
  --create-issues \
  --launch-workers
```

但要注意当前实现细节：

- `dispatch-approved --launch-workers` 会 launch registry 里当前处于 `dispatched` 的 task
- 如果你没带 `--create-issues`，新 task 还是 `decomposed`
- 所以默认不会被 launch

换句话说，标准路径里“能不能启动 worker”的分水岭不是 PRD 是否 approved，而是 task 是否已经进入 `dispatched`。

### 8.3 worker 是怎么启动的

当前主路径已经不是老的“bridge 自己拼命令直接拉 tmux”，而是：

1. bridge 把 task 转成 `orx.TaskRequest`
2. `orx` 在 `.openclaw-state/orx/` 下创建运行时目录和 `tasks.db`
3. `orx` 创建 worktree
4. `orx` 把 prompt 写到自己的 task 目录
5. `orx` 通过 tmux 启动 worker
6. worker 完成时必须写 `result.json`

当前默认 worker 命令是：

- Codex：`codex exec --skip-git-repo-check --dangerously-bypass-approvals-and-sandbox`
- Claude：`claude --dangerously-skip-permissions`

### 8.4 worktree、branch、session

dispatch 时就会先生成这些元数据：

- branch：`openclaw/<task-id>/<ascii-slug>`
- worktree：`../worktrees/<target_repo>/<task-id>-<ascii-slug>`

`ascii-slug` 来自 PRD 标题，非 ASCII 字符会被归一掉。

启动后，registry 里还会补充：

- `orx_task_id`
- 实际 tmux session 名
- `launched_at`
- `launch_attempts`

### 8.5 本地代码仓库如何定位

当前 repo 定位顺序大致是：

1. `repo_mappings.<repo>.local_path`
2. `launcher.repo_roots` 下的 `<root>/<target_repo>`
3. docs repo 附近的一组默认候选目录

最稳妥的做法仍然是显式配置：

- `repo_mappings.<repo>.local_path`

或者：

- `launcher.repo_roots`

## 9. worker 完成契约

这是当前实现最关键的一段。

`worker-prompt.txt` 会要求 worker 在完成时写一个 UTF-8 的 `result.json`。真正的绝对路径由 `orx` 在运行时追加到 prompt 里。

成功完成的最小形状应类似：

```json
{
  "status": "completed",
  "outcome": "success",
  "summary": "Implemented the requested change and validated it.",
  "artifacts": [],
  "metadata": {
    "commit_message": "feat: finish task-0001",
    "tests_run": ["python -m unittest"],
    "notes": ["optional notes"]
  },
  "error": null
}
```

当前实现的硬约束：

- `status` 必须是 `completed`
- `outcome` 只能是 `success`、`failure`、`needs_human`
- `metadata` 必须是对象
- 如果 `outcome == success`，`metadata.commit_message` 必须非空
- `tests_run` 如果存在，必须是数组
- `notes` 如果存在，必须是数组

当前收口逻辑不是“看 worker 退出码”，而是“看 `result.json` + worktree 是否真的有本地改动”。

这意味着：

- worker 成功但没写 `result.json`，运行时会把它当失败处理
- worker 声称成功，但 worktree 没有本地改动，task 会被标成 `blocked`

## 10. reconcile 与 watch 的区别

### 10.1 `reconcile-workers`

```bash
openclaw-mvp1 reconcile-workers \
  --docs-repo /absolute/path/to/docs-repo
```

它只做状态对齐，不做 commit，不写 watchdog 事件，不发通知。

可选：

```bash
openclaw-mvp1 reconcile-workers \
  --docs-repo /absolute/path/to/docs-repo \
  --restart
```

这会在 reconcile 后，自动拉起进入 `restartable` 的 task。

### 10.2 `watch-workers`

```bash
openclaw-mvp1 watch-workers \
  --docs-repo /absolute/path/to/docs-repo \
  --restart \
  --once
```

这是当前最接近“watchdog tick”的命令。它会：

- reconcile 当前 task
- 对 `restartable` task 做安全重启
- 对成功完成的 task 自动 commit
- 追加事件到 `.openclaw-state/watchdog-events.jsonl`
- 如果配置了 webhook，再 best-effort 发通知

`--once` 当前只是为了调度器可读性而保留，命令本身本来就是 one-shot。

如果你要机器可读输出，可以加：

```bash
openclaw-mvp1 watch-workers \
  --docs-repo /absolute/path/to/docs-repo \
  --restart \
  --json
```

## 11. 当前状态机

这版实现里最值得关心的状态是：

- `decomposed`
  - 已拆解，未真正派发 issue
- `dispatched`
  - issue 已创建，或 dry-run issue 已写回
  - 默认可 launch
- `in_progress`
  - `orx` 里处于 `pending` / `starting` / `running`
- `restartable`
  - 失败可安全重启，且当前 worktree 没有本地改动
- `exited`
  - worker 已退出，但 worktree 已有本地改动
  - 不自动重启，等人工检查
- `blocked`
  - 配置错误、非可重试失败、结果非法、成功但无改动等
- `completed`
  - `watch-workers` 已自动 commit 收口

### 11.1 典型成功链路

`decomposed -> dispatched -> in_progress -> completed`

### 11.2 典型恢复链路

`in_progress -> restartable -> in_progress`

### 11.3 典型人工介入链路

`in_progress -> exited`

或：

`in_progress -> blocked`

## 12. 事件与通知

### 12.1 本地事件日志

`watch-workers` 会把事件追加到：

```text
.openclaw-state/watchdog-events.jsonl
```

当前会写出的事件类型主要包括：

- `completed`
- `blocked`
- `restartable`
- `restarted`

### 12.2 webhook 通知

在 `.openclaw/config.json` 里配置：

```json
{
  "notifications": {
    "webhook_url": "https://notify.example.com/openclaw",
    "events": ["completed", "blocked", "restartable", "restarted"],
    "timeout_seconds": 5,
    "headers": {},
    "bearer_token_env": ""
  }
}
```

几个实现细节：

- webhook 发送是 best-effort
- 通知失败不会阻塞主流程
- `headers` 适合静态非敏感头
- `bearer_token_env` 指向环境变量名，发送时会组装成 `Authorization: Bearer ...`
- 本地事件日志保留完整事件；通知只发 `notifications.events` 选中的子集

## 13. 关键运行时路径

按当前实现，最重要的路径有这些：

- registry：`.openclaw-state/registry.json`
- dispatch artifact：`.openclaw-state/artifacts/<task-id>/`
- issue markdown：`.openclaw-state/artifacts/<task-id>/implementation-issue.md`
- worker prompt：`.openclaw-state/artifacts/<task-id>/worker-prompt.txt`
- watchdog 事件：`.openclaw-state/watchdog-events.jsonl`
- `orx` 数据根：`.openclaw-state/orx/`
- `orx` 任务数据库：`.openclaw-state/orx/tasks.db`
- `orx` 单任务目录：`.openclaw-state/orx/tasks/<orx-task-id>/`

`orx` 单任务目录里通常会有：

- `prompt.txt`
- `result.json`
- `stdout.log`
- `stderr.log`
- `runtime.json`

## 14. 当前实现里最容易踩的坑

### 14.1 只 dispatch 不 create issue，不会默认启动 worker

因为 task 会停在 `decomposed`，而默认 launch 只拾取 `dispatched` 和 `restartable`。

### 14.2 dry-run 会污染 registry

`--create-issues --dry-run` 会写入 `DRY-RUN-task-xxxx`，后续真实创建不会自动补建。

### 14.3 frontmatter 不要写复杂 YAML

当前解析器是简化实现，复杂嵌套、花哨语法都不推荐。

### 14.4 非 ASCII 标题会被改写成 ASCII-safe branch/worktree

这是当前实现的预期行为，不是 bug。

如果 registry 里还有旧的非 ASCII launch 元数据，reconcile/watch 会先迁移，再把 task 置成 `restartable` 等待重启。

### 14.5 成功完成不等于自动收口

worker 结束后，真正把 task 变成 `completed` 的动作发生在 `watch-workers` 的 commit 阶段，而不是 `launch-workers`。

### 14.6 `launcher` 下面不是每个字段都已生效

当前 `orx` 主路径真正会用到的主要是：

- `launcher.repo_roots`
- `launcher.workspace_strategy`

默认配置里还能看到 `base_branch`、`codex_reasoning_effort`、`session_launcher`，但它们在当前 `orx` 驱动的 launch 主路径里还没有成为有效控制面。

## 15. 推荐日常操作方式

默认把 skill 当成主入口，把 CLI 当成底层执行面。

如果你要把它当一个“最小可运行”的本地 swarm bridge，用下面这套习惯最稳：

1. 在 docs repo 里优先通过 `openclaw-project-bootstrap`、`openclaw-discussion-to-prd`、`openclaw-prd-to-repo-issues` 工作。
2. 把 `docs/prd/*.md` 视为 skill 产出的执行契约，而不是默认的人手维护对象。
3. 只有在验证 bridge 行为、排查 mapping/credential 问题、或做 dry-run 预演时，才直接调用 `dispatch-approved`。
4. worker 启动后，把 `watch-workers --restart --once` 交给 cron、launchd 或你自己的 scheduler。
5. `exited` 一律人工看 worktree，再决定是否手动保留、提交或重启。

如果只按一句话总结当前实现：

它已经是“skill 驱动的需求讨论/派发 + 文档契约落盘 + 本地 worker 执行 + watchdog 最小收口”的 MVP-1，不再只是一个生成 issue 文本的脚手架；但它还不是一个能自动把代码一路送到 merge 的完整自治系统。
