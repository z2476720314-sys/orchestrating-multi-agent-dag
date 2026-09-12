# 角色与路由

## Codex

职责：结构化计划、依赖编排、任务卡、单写者约束、交接汇总、冲突处理和最终验收。内部子 Agent 适合当前 Codex 会话内的独立子任务；不要把它们伪装成 DSH 或 WorkBuddy。

## DSH

默认任务：调研、方案验证、日志/调用链分析、只读代码审查。仅当任务卡列出唯一可写路径时才能修改文件。

默认路由：

- provider：`workbuddy`
- model：`deepseek-v4.1-flash`
- reasoning effort：`max`

先用 `Get-Command dsh` 和 `dsh --profile headless --help` 发现真实入口。Headless 的任务调用是：

```powershell
Set-Location -LiteralPath '<workspace>'
dsh --profile headless '<bounded task prompt>'
```

Headless 没有逐次 `--model`/`--effort` 参数。派发前只读取 DSH 设置中的 `agent-default-model` 块，确认 provider/model/reasoningEffort；不要打印整个设置文件或凭据。若不匹配，只有在用户配置变更已获授权时才能修正，否则报告阻塞。派发后再用会话投影或安全元数据核验；没有模型字段就标为“未验证”。

遇到 `CONTEXT_WINDOW_EXCEEDED`：

1. 记录真实错误、当前 profile 与是否继承旧会话；
2. 使用 fresh、边界明确的短 prompt，只附必要文件路径和输出格式；
3. 检查目标 profile 是否装配 `dsh-workbuddy-auth` 和 WorkBuddy provider；
4. Web 可用而 Headless 不可用时，比较 profile 的插件/配置装配，不复制浏览器 profile、订阅或整套 Skills；
5. 仍失败则保持真实 blocked 状态，不静默换模型或降低 effort。

## WorkBuddy

默认任务：范围清晰的实现、测试、打包和真实入口验收。先用 `Get-Command codebuddy` 与 `codebuddy --help` 验证当前 CLI。

```powershell
Set-Location -LiteralPath '<workspace>'
codebuddy --bg --name '<task-id>-workbuddy' --model glm-5.3-flash --effort high '<bounded task prompt>'
codebuddy ps --json
codebuddy logs -f '<task-id>-workbuddy'
```

需要交互时使用 `codebuddy attach '<task-id>-workbuddy'`。不要把 `--bg` 接受任务、进程存在或退出码 0 单独视为业务完成。

## 调度决策

| 工作类型 | 首选 owner | 可并行条件 |
|---|---|---|
| 方案/风险/资料核对 | DSH | 与写入任务无共享可变资源 |
| 边界实现/测试 | WorkBuddy | 写路径唯一，依赖输入已冻结 |
| 集成/冲突/最终验收 | Codex | 通常位于汇聚阶段 |
| 第二视角只读审查 | 任一非作者 Agent | 不能改作者正在写的文件 |

