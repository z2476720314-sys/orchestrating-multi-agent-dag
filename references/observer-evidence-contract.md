# A·DAG 观测与证据契约

## 启动

```powershell
Set-Location -LiteralPath '<workspace>\.agent-coordination\observer'
.\start-observer.ps1
```

不打开浏览器或改变端口：

```powershell
.\start-observer.ps1 -NoBrowser -Port 8877
```

机器可读快照：

```powershell
python run.py --workspace '<workspace>' --once
```

默认仅监听 `127.0.0.1`。接口为 `/api/health`、`/api/snapshot`、`/api/events`（SSE）和 `/`。

## 主图语义

中心图是固定四层总览：

```text
任务根
├─ Codex ───── Codex 调用链摘要 ────┐
├─ DSH ─────── DSH 会话摘要 ────────┼─ 观测聚合
└─ WorkBuddy ─ WorkBuddy 进程摘要 ──┘
```

正常为 8 个节点、9 条语义边。九条边表达任务归属和观测汇聚，不冒充原始 `parent_id`。每条边必须有稳定且不同的颜色、持续可见底轨、发光流动层，并从源卡片真实 port 中心连到目标卡片真实 port 中心。缩放、resize、SSE 更新后重新测量 DOM 锚点。

原始事件、真实父链、工具、耗时、退出码、模型和 effort 放在右侧详情。筛选详情不得删除中心三条 lane。`prefers-reduced-motion` 只停止动画，不能隐藏结构边。

## 证据优先级

1. 实际文件与 handoff；
2. 真实任务/会话/进程状态；
3. 命令退出码和测试输出；
4. 真实页面、API 或用户入口验收；
5. Agent 自述仅作线索。

缺少 `task_id`、agent 归属或 parent 时显示 `unknown`/“暂无证据”。不要根据标题、时间接近、模型名称或自然语言摘要猜关联。

## 隐私边界

允许展示任务 ID、Agent/session ID、模型、effort、状态依据、时间、工具名、耗时、退出码和来源健康。禁止展示 system/developer 指令、隐藏推理、原始 prompt、完整工具参数/输出、凭据、Cookie、token、环境变量值和剪贴板内容；无法确认安全的内容显示“内容已隐藏”。

## 验收

- `python run.py --workspace '<workspace>' --once` 返回 `schema_version = 1`；
- `/api/health` 与 `/` 可访问，SSE 能发送初始快照及真实变化；
- 任意数量 raw events 不扩大中心 A·DAG；
- 九条边颜色唯一、端点贴合卡片，详情保留真实父链；
- 空数据、来源故障和断线均显示真实 unknown/degraded/offline，不生成百分比或 ETA。

