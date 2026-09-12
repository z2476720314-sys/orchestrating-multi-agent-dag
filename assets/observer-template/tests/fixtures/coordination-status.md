# 实时协作状态

| Agent | 当前状态 | 默认职责 | 会话或入口 | 当前写入范围 |
|---|---|---|---|---|
| Codex | 在线，协调中 | 协调 | 当前任务 | `.agent-coordination/` |
| DSH | 暂无证据 | 调查 | Headless | 无 |
| WorkBuddy | waiting | 实现 | Background | observer |
| Prompt Runner | DONE | developer instructions: private | raw | raw |

## 当前任务

| 任务 ID | 负责人 | 状态 | 范围 | 产出 |
|---|---|---|---|---|
| observer-model | Codex | DONE | observer | 标准化数据模型 |
| unsafe-title | DSH | IN_PROGRESS（已完成 1/3） | observer | developer instructions: never expose |
