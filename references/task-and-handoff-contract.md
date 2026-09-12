# 任务与 Handoff 契约

## 派发前任务卡

每个任务必须明确：

```text
Task ID:
Objective:
Owner:
Dependencies:
Read scope:
Write scope:
Forbidden actions:
Success criteria:
Required validation:
Handoff path: .agent-coordination/handoffs/<task-id>-<owner>.md
```

约束：

- 写范围必须是具体文件或最小目录；空写范围代表只读。
- 一个文件不得同时出现在两个活动任务的写范围。
- 已有未提交改动视为用户资产，禁止 `reset`、`checkout --`、`stash`、清理或覆盖。
- 公用端口、浏览器 profile、服务实例、构建目录和生成物也视为需要唯一 owner 的资源。
- Prompt 只提供完成子任务必需的上下文；优先传路径和摘要，不附整段会话、全量日志或隐藏指令。

## 状态语义

推荐状态：`PLANNED`、`RUNNING`、`WAITING`、`BLOCKED`、`FAILED`、`DONE`。

- `RUNNING`：有真实会话/进程/最近事件证据。
- `WAITING`：明确等待依赖、审批或用户输入。
- `BLOCKED`：阻塞原因与下一步均已记录。
- `FAILED`：执行或验收产生可核验失败。
- `DONE`：交付物存在且协调者已独立验证成功标准。

禁止将任务接受、心跳、模型自述、进程退出、单项测试或生成文件本身升级为 `DONE`。

## Handoff 模板

```markdown
# <task-id> handoff

- Owner:
- State:
- Scope honored:
- Files changed:
- Commands and exit codes:
- Test/build/runtime evidence:
- Actual model metadata:
- Risks and unknowns:
- Unfinished work:
- Next safe action:
```

不得写入 token、Cookie、密码、环境变量值、隐藏 prompt、私有推理或未脱敏工具输入输出。

## 汇聚

Codex 读取所有 handoff 后：

1. 检查任务依赖和文件所有权是否一致；
2. 查看实际 diff，不仅采信摘要；
3. 重跑与风险相称的测试/静态检查；
4. 验证真实启动入口、页面或 API；
5. 把失败和未知保持为失败/未知；
6. 最后更新 `status.md`，保留原始 handoff。

