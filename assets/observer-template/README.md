# 多 Agent 实时观测台

版本：`1.1.2`

这是一个仅在本机运行的只读观测台。它汇总共享工作区中的 Codex、DSH、WorkBuddy 和协调状态，提供 JSON 快照、SSE 事件流与实时调用 DAG。缺少证据时显示 `unknown` 或“暂无证据”，不会生成完成百分比和预计时间。

调用图采用用户选定的 A·DAG 四层总览：当前任务为根，分出 Codex、DSH、WorkBuddy，再经三张调用链摘要汇入观测聚合。九条总览边表达归属/汇聚关系，不冒充原始事件父链；真实 `events[].parent_id → events[].id` 在右侧事件明细中保留。每条总览边都有独立稳定的颜色、持续可见底轨、流动高光与落在卡片边框上的端子。工具栏可缩放、适配画布及暂停流动；右侧可筛选来源/状态，并展示 Task ID、Agent/session ID、模型、effort、状态依据、开始/结束时间、工具、耗时、退出码和来源健康等实际可用字段。窗口或面板变窄时会重算三分支布局而不是把整图压成不可读的缩略图，实时刷新后也会保留当前 DAG 节点的键盘焦点。

## 环境要求

- Windows PowerShell 5.1 或 PowerShell 7；
- Python 3.11 或更高版本；
- 通过 CLI 的 `--workspace '<workspace>'` 显式指定目标工作区；
- 运行时只使用 Python 标准库，不需要安装第三方包。

PowerShell 启动器会按 `PATH` 顺序逐个探测所有 `python` 应用，并使用首个可运行的 Python 3.11+；因此多个同名 Python 或不可用的 Windows App Alias 不会被拼接成一个命令。启动器脚本保持 ASCII 源码，可由 Windows PowerShell 5.1 和 PowerShell 7 直接解析。

## 启动

在本目录执行：

```powershell
.\start-observer.ps1
```

脚本等待健康接口可用后打开浏览器，服务固定监听 loopback。若不希望自动打开浏览器：

```powershell
.\start-observer.ps1 -NoBrowser
```

指定端口：

```powershell
.\start-observer.ps1 -NoBrowser -Port 8877
```

也可以直接运行 Python CLI：

```powershell
python run.py --workspace '<workspace>' --host 127.0.0.1 --port 8767 --poll-seconds 1.0
```

CLI 拒绝 `127.0.0.1` 以外的监听地址，不能通过参数暴露到局域网或公网。按 `Ctrl+C` 停止前台服务。

## 单次快照

只采集一次并向标准输出写入 JSON，不启动 HTTP 服务：

```powershell
python run.py --workspace '<workspace>' --once
```

顶层字段包括 `schema_version`、`generated_at`、`workspace`、`connection`、`agents`、`tasks`、`events` 和 `source_health`。

## 本地接口

- `GET /api/health`：服务与当前连接状态；
- `GET /api/snapshot`：当前规范化快照；
- `GET /api/events`：SSE 初始快照、变化事件和心跳；
- `GET /`：观测台页面。

默认地址为 `http://127.0.0.1:8767/`。服务只读访问本地状态源；单个来源异常会显示为 `degraded`，不会把缺失数据伪装成失败或完成。

## 隐私边界

观测台只展示允许的元数据，不展示 system/developer 指令、隐藏推理、原始 prompt、完整工具参数或输出、凭据、cookie、token、环境变量及剪贴板内容。无法确认安全的内容显示“内容已隐藏”。

## 开发验证

在本目录执行：

```powershell
python -m unittest discover -s tests -p 'test_*.py' -v
python -m compileall -q observer run.py tests
ruff check observer run.py tests
mypy observer run.py
bandit -q -r -ll observer run.py
```

CLI 测试分别通过 Windows PowerShell 5.1 和当前首选 PowerShell 运行真实子进程启动、`/api/health` 检查、终止与端口释放。发布验收另从共享工作区启动真实 DSH、WorkBuddy 与 Codex 活动，确认 SSE `changed` 事件、桌面/窄屏布局、调用详情展开，以及浏览器 `SEVERE` 控制台错误为零。验收截图保存在 `artifacts/`。
