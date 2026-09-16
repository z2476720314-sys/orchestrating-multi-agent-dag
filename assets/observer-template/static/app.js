(function () {
  "use strict";

  const UNKNOWN = "暂无证据";
  const HIDDEN = "内容已隐藏";
  const SVG_NS = "http://www.w3.org/2000/svg";
  const MAX_VISIBLE_AGENTS = 12;
  const NODE_WIDTH = 264;
  const COLUMN_GAP = 44;
  const CANVAS_PADDING = 48;
  const EDGE_COLORS = [
    "#6ea8ff", "#ffb454", "#5fd0a5", "#b58cff", "#ff7a90",
    "#4ee8ff", "#b7f36b", "#ff75d8", "#ff8a4c",
  ];
  const AGENT_GROUPS = [
    { id: "codex", label: "Codex", aliases: ["codex"] },
    { id: "dsh", label: "DSH", aliases: ["dsh"] },
    { id: "workbuddy", label: "WorkBuddy", aliases: ["workbuddy", "codebuddy"] },
  ];
  const STATUS_LABELS = {
    running: "运行中",
    completed: "已完成",
    done: "已完成",
    aborted: "已中止",
    failed: "异常",
    blocked: "已阻塞",
    planned: "已计划",
    waiting: "等待授权",
    waiting_for_user: "等待授权",
    degraded: "证据降级",
    live: "实时",
    healthy: "正常",
    ok: "正常",
    unknown: "未知",
  };
  const AGENT_STATUS_PRIORITY = {
    running: 0,
    blocked: 1,
    waiting: 2,
    planned: 3,
    unknown: 4,
    completed: 5,
    aborted: 6,
    failed: 6,
  };

  const state = {
    snapshot: null,
    eventSource: null,
    selectedEventId: null,
    scale: 1,
    motionPaused: false,
    hasUserScale: false,
    layout: null,
    redrawFrame: null,
    relayoutScheduled: false,
  };

  function byId(id) {
    return document.getElementById(id);
  }

  function makeElement(tagName, className, text) {
    const node = document.createElement(tagName);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function makeSvgElement(tagName, attributes) {
    const node = document.createElementNS(SVG_NS, tagName);
    Object.entries(attributes).forEach(([name, value]) => {
      node.setAttribute(name, value);
    });
    return node;
  }

  function safeText(value, fallback) {
    if (typeof value === "string") {
      const trimmed = value.trim();
      return trimmed || (fallback || UNKNOWN);
    }
    if (typeof value === "number" || typeof value === "boolean") return String(value);
    if (value === null || value === undefined) return fallback || UNKNOWN;
    return HIDDEN;
  }

  function asArray(value) {
    return Array.isArray(value) ? value : [];
  }

  function normalizedStatus(value) {
    const status = typeof value === "string" ? value.toLowerCase() : "unknown";
    if (status === "started") return "running";
    if (status === "done") return "completed";
    if (status === "waiting_for_user") return "waiting";
    return Object.prototype.hasOwnProperty.call(STATUS_LABELS, status)
      ? status
      : "unknown";
  }

  function statusLabel(value) {
    return STATUS_LABELS[normalizedStatus(value)] || STATUS_LABELS.unknown;
  }

  function statusClass(value) {
    return `status--${normalizedStatus(value)}`;
  }

  function formatTime(value) {
    if (!value) return "时间未知";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "时间未知";
    return new Intl.DateTimeFormat("zh-CN", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    }).format(date);
  }

  function setConnection(mode, label, generatedAt) {
    const connection = byId("connection-status");
    connection.className = `connection connection--${mode}`;
    connection.querySelector("[data-connection-label]").textContent = label;
    connection.querySelector("[data-generated-at]").textContent = generatedAt
      ? `快照 ${formatTime(generatedAt)}`
      : "更新时间未知";
  }

  function appendEmpty(container, message) {
    container.append(makeElement("p", "empty-state", message));
  }

  function buildAgentIndex(agents) {
    const index = new Map(
      agents.filter((agent) => agent.id).map((agent) => [agent.id, agent]),
    );
    agents.forEach((agent) => {
      if (typeof agent.id !== "string" || !agent.id.startsWith("codex:")) return;
      const rawId = agent.id.slice("codex:".length);
      if (rawId && !index.has(rawId)) index.set(rawId, agent);
    });
    return index;
  }

  function calculateAgentDepth(agent, byAgentId, visited) {
    if (!agent || !agent.parent_id || !byAgentId.has(agent.parent_id)) return 0;
    if (visited.has(agent.id)) return 0;
    const nextVisited = new Set(visited);
    nextVisited.add(agent.id);
    return 1 + calculateAgentDepth(byAgentId.get(agent.parent_id), byAgentId, nextVisited);
  }

  function compareAgents(left, right) {
    const leftPriority = AGENT_STATUS_PRIORITY[normalizedStatus(left.status)] ?? 5;
    const rightPriority = AGENT_STATUS_PRIORITY[normalizedStatus(right.status)] ?? 5;
    if (leftPriority !== rightPriority) return leftPriority - rightPriority;
    return String(right.updated_at || "").localeCompare(String(left.updated_at || ""));
  }

  function renderAgents(agents) {
    const container = byId("agent-tree");
    container.replaceChildren();
    document.querySelector("[data-agent-count]").textContent = String(agents.length);
    if (!agents.length) {
      appendEmpty(container, "暂无证据：等待 Agent 状态。");
      return;
    }
    const index = buildAgentIndex(agents);
    const visible = [...agents].sort(compareAgents).slice(0, MAX_VISIBLE_AGENTS);
    visible.forEach((agent) => {
      const card = makeElement("article", `agent ${statusClass(agent.status)}`);
      card.classList.add("agent__path");
      card.style.setProperty(
        "--agent-depth",
        String(calculateAgentDepth(agent, index, new Set())),
      );
      const top = makeElement("div", "agent__top");
      top.append(makeElement("h3", "agent__name", safeText(agent.name, "未命名 Agent")));
      top.append(makeElement("span", "status-chip", statusLabel(agent.status)));
      card.append(top);
      card.append(
        makeElement(
          "p",
          "agent__detail",
          `${safeText(agent.model, "模型未知")} · ${safeText(agent.effort, "强度未知")}`,
        ),
      );
      card.append(
        makeElement("p", "agent__detail", safeText(agent.status_evidence, "状态依据暂无证据")),
      );
      container.append(card);
    });
    const hiddenCount = agents.length - visible.length;
    if (hiddenCount > 0) {
      container.append(makeElement("p", "agent-overflow", `另有 ${hiddenCount} 个历史会话`));
    }
  }

  function renderTasks(tasks, events) {
    const container = byId("task-board");
    container.replaceChildren();
    document.querySelector("[data-task-count]").textContent = String(tasks.length);
    if (!tasks.length) {
      appendEmpty(container, "暂无证据：协调任务尚未出现。");
      return;
    }
    tasks.forEach((task) => {
      const card = makeElement("article", `task ${statusClass(task.status)}`);
      const top = makeElement("div", "task__top");
      top.append(makeElement("h3", "task__title", safeText(task.title, safeText(task.id, "未命名任务"))));
      top.append(makeElement("span", "status-chip", statusLabel(task.status)));
      card.append(top);
      card.append(
        makeElement("p", "task__detail", safeText(task.status_evidence, "任务状态依据暂无证据")),
      );
      const count = task.id ? events.filter((event) => event.task_id === task.id).length : 0;
      card.append(
        makeElement("p", "task__meta", `${safeText(task.owner_id, "负责人未知")} · ${count} 条关联事件`),
      );
      if (task.phase || task.last_progress) {
        card.append(
          makeElement(
            "p",
            "task__progress",
            `${safeText(task.phase, "阶段未知")} · ${safeText(task.last_progress, "暂无进展说明")}`,
          ),
        );
      }
      container.append(card);
    });
  }

  function eventSortKey(event) {
    return `${safeText(event.started_at || event.ended_at, "")}|${safeText(event.id, "")}`;
  }

  function compareEvents(left, right) {
    return eventSortKey(left).localeCompare(eventSortKey(right));
  }

  function groupForAgent(agent) {
    const haystack = `${safeText(agent && agent.kind, "")} ${safeText(agent && agent.name, "")}`.toLowerCase();
    return AGENT_GROUPS.find((group) => group.aliases.some((alias) => haystack.includes(alias)));
  }

  function groupForEvent(event, agentsById) {
    const fromAgent = groupForAgent(agentsById.get(event.agent_id));
    if (fromAgent) return fromAgent;
    const source = safeText(event.source, "").toLowerCase();
    return AGENT_GROUPS.find((group) => group.aliases.some((alias) => source.includes(alias)));
  }

  function dominantStatus(items) {
    const priority = [
      "running", "blocked", "waiting", "failed", "aborted", "planned", "completed", "unknown",
    ];
    const statuses = items.map((item) => normalizedStatus(item.status));
    return priority.find((status) => statuses.includes(status)) || "unknown";
  }

  function latestItem(items) {
    return [...items].sort((left, right) => compareEvents(right, left))[0] || null;
  }

  function buildOverviewGraph(events, agents, tasks, health) {
    const agentsById = buildAgentIndex(agents);
    const sortedEvents = [...events].sort(compareEvents);
    const declaredTasks = tasks.filter(
      (task) => safeText(task.status_evidence, "") === "协调状态声明",
    );
    const activeStatusPriority = ["running", "blocked", "waiting", "planned"];
    const primaryTask = activeStatusPriority
      .map((status) => tasks.find((task) => normalizedStatus(task.status) === status))
      .find(Boolean)
      || declaredTasks.at(-1)
      || tasks.at(-1)
      || null;
    const taskId = `task:${safeText(primaryTask && primaryTask.id, "unknown")}`;
    const directlyLinkedEvents = primaryTask
      ? sortedEvents.filter((event) => event.task_id === primaryTask.id)
      : [];
    const eventsById = new Map(sortedEvents.filter((event) => event.id).map((event) => [event.id, event]));
    const taskEventIds = new Set(directlyLinkedEvents.map((event) => event.id).filter(Boolean));
    const pendingAncestors = [...directlyLinkedEvents];
    while (pendingAncestors.length) {
      const event = pendingAncestors.pop();
      if (!event || !event.parent_id || taskEventIds.has(event.parent_id)) continue;
      const parent = eventsById.get(event.parent_id);
      if (!parent) continue;
      taskEventIds.add(parent.id);
      pendingAncestors.push(parent);
    }
    const taskEvents = sortedEvents.filter(
      (event) => taskEventIds.has(event.id) || directlyLinkedEvents.includes(event),
    );
    const linkedAgentIds = new Set();
    taskEvents.forEach((event) => {
      const linkedAgent = agentsById.get(event.agent_id);
      if (linkedAgent && linkedAgent.id) linkedAgentIds.add(linkedAgent.id);
      else if (event.agent_id) linkedAgentIds.add(event.agent_id);
    });
    if (primaryTask && primaryTask.owner_id && agentsById.has(primaryTask.owner_id)) {
      linkedAgentIds.add(agentsById.get(primaryTask.owner_id).id);
    }
    const taskNode = {
      id: taskId,
      type: "task",
      role: "match",
      status: primaryTask ? normalizedStatus(primaryTask.status) : "unknown",
      label: safeText(primaryTask && primaryTask.title, "协同任务暂无证据"),
      eyebrow: "TASK / PLAN ROOT",
      owner: safeText(primaryTask && primaryTask.owner_id, "负责人未知"),
      evidence: safeText(primaryTask && primaryTask.status_evidence, "任务状态依据暂无证据"),
      meta: primaryTask ? safeText(primaryTask.id, "任务 ID 未知") : "任务 ID 未知",
      data: { task: primaryTask, events: taskEvents },
    };
    const nodes = [taskNode];
    const edges = [];
    const groups = [];

    AGENT_GROUPS.forEach((group) => {
      const branchAgents = agents.filter(
        (agent) => linkedAgentIds.has(agent.id) && groupForAgent(agent)?.id === group.id,
      );
      const branchEvents = taskEvents.filter(
        (event) => groupForEvent(event, agentsById)?.id === group.id,
      );
      const primaryAgent = [...branchAgents].sort(compareAgents)[0] || null;
      const agentId = `agent:${group.id}`;
      const traceId = `trace:${group.id}`;
      const toolCount = branchEvents.filter((event) => Boolean(event.tool_name)).length;
      const latest = latestItem(branchEvents);
      nodes.push({
        id: agentId,
        type: "agent",
        group: group.id,
        role: "match",
        status: primaryAgent ? normalizedStatus(primaryAgent.status) : "unknown",
        label: group.label,
        eyebrow: "AGENT BRANCH",
        owner: `${branchAgents.length} 个关联会话`,
        evidence: safeText(primaryAgent && primaryAgent.status_evidence, "暂无该任务关联 Agent 证据"),
        meta: `${safeText(primaryAgent && primaryAgent.model, "模型未知")} · ${safeText(primaryAgent && primaryAgent.effort, "强度未知")}`,
        data: { agent: primaryAgent, agents: branchAgents, events: branchEvents },
      });
      nodes.push({
        id: traceId,
        type: "trace",
        group: group.id,
        role: "match",
        status: branchEvents.length ? dominantStatus(branchEvents) : "unknown",
        label: `${group.label} 调用链`,
        eyebrow: "TRACE SUMMARY",
        owner: `${branchEvents.length} 条事件 · ${toolCount} 条工具调用`,
        evidence: latest
          ? safeText(latest.detail || latest.evidence, "最近事件依据暂无证据")
          : "暂无该任务关联事件证据",
        meta: latest
          ? `${safeText(latest.kind, "事件")} · ${formatTime(latest.started_at || latest.ended_at)}`
          : "0 条事件 / 暂无证据",
        data: { events: branchEvents, agent: primaryAgent },
      });
      edges.push(
        { id: `${taskId}>${agentId}`, from: taskId, to: agentId },
        { id: `${agentId}>${traceId}`, from: agentId, to: traceId },
      );
      groups.push(group.id);
    });

    const aggregationStatus = health.length
      ? (health.some((item) => normalizedStatus(item.status) === "degraded") ? "degraded" : "healthy")
      : "unknown";
    const aggregationNode = {
      id: "aggregation:sources",
      type: "aggregation",
      role: "match",
      status: aggregationStatus,
      label: "观测聚合",
      eyebrow: "SSE / SOURCE HEALTH",
      owner: `${health.length} 个来源`,
      evidence: health.length
        ? health.map((item) => `${safeText(item.source, "未知来源")}:${statusLabel(item.status)}`).join(" · ")
        : "来源健康暂无证据",
      meta: "实时快照 · schema v1",
      data: { health, events: sortedEvents },
    };
    nodes.push(aggregationNode);
    groups.forEach((groupId) => {
      edges.push({
        id: `trace:${groupId}>aggregation:sources`,
        from: `trace:${groupId}`,
        to: "aggregation:sources",
      });
    });
    edges.sort((left, right) => left.id.localeCompare(right.id));
    return {
      nodes,
      edges,
      groups,
      visibleById: new Map(nodes.map((node) => [node.id, node])),
    };
  }

  function layoutGraph(graph) {
    const viewport = byId("graph-viewport");
    const branchCount = Math.max(1, graph.groups.length);
    const viewportWidth = Math.max(320, viewport.clientWidth || 1200);
    const compact = viewportWidth <= 760;
    const canvasPadding = compact ? 12 : CANVAS_PADDING;
    const columnGap = compact ? 8 : COLUMN_GAP;
    const nodeWidth = compact
      ? Math.max(
        92,
        Math.floor(
          (viewportWidth - canvasPadding * 2 - (branchCount - 1) * columnGap) / branchCount,
        ),
      )
      : NODE_WIDTH;
    const requiredWidth = branchCount === 1
      ? 620
      : canvasPadding * 2 + branchCount * nodeWidth + (branchCount - 1) * columnGap;
    const width = compact ? viewportWidth : Math.max(viewportWidth, requiredWidth);
    const positions = new Map();
    const centerNodeWidth = compact ? Math.min(340, width - canvasPadding * 2) : 340;
    const centerX = (width - centerNodeWidth) / 2;
    const task = graph.nodes.find((node) => node.type === "task");
    const aggregation = graph.nodes.find((node) => node.type === "aggregation");
    positions.set(task.id, { x: centerX, y: 24, width: centerNodeWidth, height: 96, depth: 0 });
    const branchWidth = branchCount * nodeWidth + (branchCount - 1) * columnGap;
    const branchStart = (width - branchWidth) / 2;
    graph.groups.forEach((groupId, index) => {
      const x = branchStart + index * (nodeWidth + columnGap);
      positions.set(`agent:${groupId}`, { x, y: 154, width: nodeWidth, height: 108, depth: 1 });
      positions.set(`trace:${groupId}`, { x, y: 300, width: nodeWidth, height: 108, depth: 2 });
    });
    positions.set(aggregation.id, { x: centerX, y: 446, width: centerNodeWidth, height: 90, depth: 3 });
    const height = 566;
    return { positions, width, height };
  }

  function updateSourceFilter(events) {
    const select = byId("source-filter");
    const current = select.value;
    const sources = [...new Set(events.map((event) => safeText(event.source, "未知来源")))].sort();
    select.replaceChildren();
    const all = makeElement("option", "", "全部来源");
    all.value = "all";
    select.append(all);
    sources.forEach((source) => {
      const option = makeElement("option", "", source);
      option.value = source;
      select.append(option);
    });
    select.value = sources.includes(current) ? current : "all";
  }

  function renderGraphNode(node, geometry) {
    const button = makeElement(
      "button",
      `dag-node dag-node--${node.type} ${statusClass(node.status)} dag-node--${node.role}`,
    );
    button.type = "button";
    button.setAttribute("data-node-id", node.id);
    button.setAttribute("data-role", node.role);
    button.setAttribute("data-status", normalizedStatus(node.status));
    button.setAttribute("data-parent-state", "resolved");
    if (node.group) button.setAttribute("data-branch", node.group);
    button.setAttribute("aria-selected", String(state.selectedEventId === node.id));
    button.setAttribute(
      "aria-label",
      `${safeText(node.eyebrow, "节点")} ${safeText(node.label, "未命名节点")}，${statusLabel(node.status)}`,
    );
    button.style.setProperty("left", `${geometry.x}px`);
    button.style.setProperty("top", `${geometry.y}px`);
    button.style.setProperty("width", `${geometry.width}px`);
    button.style.setProperty("height", `${geometry.height}px`);

    const eyebrow = makeElement(
      "span",
      "dag-node__eyebrow",
      `${safeText(node.eyebrow, "节点")} · L${geometry.depth}`,
    );
    const status = makeElement("span", "dag-node__status", statusLabel(node.status));
    const title = makeElement("strong", "dag-node__title", safeText(node.label, "未命名节点"));
    const owner = makeElement("span", "dag-node__owner", safeText(node.owner, "负责人未知"));
    const evidence = makeElement(
      "span",
      "dag-node__evidence",
      safeText(node.evidence, "状态依据暂无证据"),
    );
    const meta = makeElement("span", "dag-node__meta", safeText(node.meta, UNKNOWN));
    button.append(eyebrow, status, title, owner, evidence, meta);
    if (node.role === "context") button.append(makeElement("span", "dag-node__relation", "结构上下文"));
    button.addEventListener("click", () => selectEvent(node.id));
    return button;
  }

  function edgeColorMap(edges) {
    const colors = new Map();
    edges.map((edge) => edge.id).sort().forEach((id, index) => {
      colors.set(id, EDGE_COLORS[index % EDGE_COLORS.length]);
    });
    return colors;
  }

  function measuredGeometry(element, surfaceRect) {
    const rect = element.getBoundingClientRect();
    const scale = state.scale || 1;
    return {
      x: (rect.left - surfaceRect.left) / scale,
      y: (rect.top - surfaceRect.top) / scale,
      width: rect.width / scale,
      height: rect.height / scale,
    };
  }

  function edgePortRatio(edge, side) {
    const branchOrder = { codex: 0.24, dsh: 0.5, workbuddy: 0.76 };
    if (side === "start" && edge.from.startsWith("task:")) {
      const branch = edge.to.replace("agent:", "");
      return branchOrder[branch] || 0.5;
    }
    if (side === "end" && edge.to === "aggregation:sources") {
      const branch = edge.from.replace("trace:", "");
      return branchOrder[branch] || 0.5;
    }
    return 0.5;
  }

  function edgePath(parent, child, edge) {
    const startX = parent.x + parent.width * edgePortRatio(edge, "start");
    const startY = parent.y + parent.height;
    const endX = child.x + child.width * edgePortRatio(edge, "end");
    const endY = child.y;
    const bend = Math.max(30, (endY - startY) * 0.46);
    return {
      d: `M ${startX} ${startY} C ${startX} ${startY + bend}, ${endX} ${endY - bend}, ${endX} ${endY}`,
      startX,
      startY,
      endX,
      endY,
    };
  }

  function appendEdgeLayer(svg, edge, layer, color, d) {
    const path = makeSvgElement("path", {
      class: `dag-edge__${layer}`,
      d,
      stroke: color,
      color,
      fill: "none",
      "data-edge-id": edge.id,
      "data-from": edge.from,
      "data-to": edge.to,
      "data-layer": layer,
      "vector-effect": "non-scaling-stroke",
    });
    svg.append(path);
  }

  function appendTerminal(svg, edge, layer, color, cx, cy) {
    svg.append(
      makeSvgElement("circle", {
        class: "dag-edge__terminal",
        cx,
        cy,
        r: 4,
        stroke: color,
        color,
        fill: "#070a12",
        "data-edge-id": edge.id,
        "data-from": edge.from,
        "data-to": edge.to,
        "data-layer": layer,
        "vector-effect": "non-scaling-stroke",
      }),
    );
  }

  function redrawEdges() {
    if (!state.layout) return;
    const svg = byId("graph-edges");
    const surface = byId("graph-surface");
    const nodeLayer = byId("graph-nodes");
    svg.replaceChildren();
    svg.setAttribute("viewBox", `0 0 ${state.layout.width} ${state.layout.height}`);
    svg.setAttribute("preserveAspectRatio", "none");
    svg.setAttribute("width", state.layout.width);
    svg.setAttribute("height", state.layout.height);
    const surfaceRect = surface.getBoundingClientRect();
    const elements = new Map();
    nodeLayer.querySelectorAll("[data-node-id]").forEach((element) => {
      elements.set(element.getAttribute("data-node-id"), element);
    });
    const colors = edgeColorMap(state.layout.graph.edges);
    state.layout.graph.edges.forEach((edge) => {
      const parentElement = elements.get(edge.from);
      const childElement = elements.get(edge.to);
      if (!parentElement || !childElement) return;
      const path = edgePath(
        measuredGeometry(parentElement, surfaceRect),
        measuredGeometry(childElement, surfaceRect),
        edge,
      );
      const color = colors.get(edge.id);
      appendEdgeLayer(svg, edge, "track", color, path.d);
      appendEdgeLayer(svg, edge, "motion", color, path.d);
      appendTerminal(svg, edge, "terminal-start", color, path.startX, path.startY);
      appendTerminal(svg, edge, "terminal-end", color, path.endX, path.endY);
    });
  }

  function scheduleEdgeRedraw() {
    if (state.redrawFrame) cancelAnimationFrame(state.redrawFrame);
    state.redrawFrame = requestAnimationFrame(() => {
      state.redrawFrame = null;
      redrawEdges();
    });
  }

  function detailRow(label, value) {
    const row = makeElement("div", "detail-row");
    row.append(makeElement("dt", "detail-row__label", label));
    row.append(makeElement("dd", "detail-row__value", safeText(value, UNKNOWN)));
    return row;
  }

  function appendEvidenceList(container, label, values) {
    const items = asArray(values);
    if (!items.length) return;
    const row = makeElement("div", "delivery-entry__row");
    row.append(makeElement("strong", "delivery-entry__label", label));
    row.append(
      makeElement(
        "span",
        "delivery-entry__value",
        items.map((item) => safeText(item, UNKNOWN)).join(" · "),
      ),
    );
    container.append(row);
  }

  function renderProgressAndDeliveries(container, events) {
    const ordered = [...events].sort(compareEvents).reverse();
    const progressEvents = ordered.filter(
      (event) => event.kind === "progress" || event.kind === "task",
    );
    const deliveryEvents = ordered.filter((event) => event.kind === "delivery");

    const progressSection = makeElement("section", "progress-feed");
    progressSection.append(
      makeElement("h4", "detail-events__heading", `实时进展 · ${progressEvents.length}`),
    );
    if (!progressEvents.length) {
      progressSection.append(
        makeElement("p", "empty-state", "暂无证据：Agent 尚未发布结构化进展。"),
      );
    }
    progressEvents.slice(0, 30).forEach((event) => {
      const entry = makeElement("article", `progress-entry ${statusClass(event.status)}`);
      entry.append(makeElement("strong", "progress-entry__title", safeText(event.title, "进展")));
      entry.append(
        makeElement(
          "span",
          "progress-entry__meta",
          `${safeText(event.phase, "阶段未知")} · ${formatTime(event.started_at || event.ended_at)}`,
        ),
      );
      entry.append(
        makeElement("p", "progress-entry__detail", safeText(event.detail, "进展说明暂无证据")),
      );
      appendEvidenceList(entry, "证据", event.evidence_refs);
      progressSection.append(entry);
    });
    container.append(progressSection);

    const deliverySection = makeElement("section", "delivery-feed");
    deliverySection.append(
      makeElement("h4", "detail-events__heading", `交付内容 · ${deliveryEvents.length}`),
    );
    if (!deliveryEvents.length) {
      deliverySection.append(
        makeElement("p", "empty-state", "暂无证据：Agent 尚未发布最终交付。"),
      );
    }
    deliveryEvents.forEach((event) => {
      const entry = makeElement("article", `delivery-entry ${statusClass(event.status)}`);
      entry.append(
        makeElement("strong", "delivery-entry__title", safeText(event.detail, "交付摘要暂无证据")),
      );
      entry.append(
        makeElement(
          "span",
          "delivery-entry__meta",
          `${statusLabel(event.status)} · ${formatTime(event.ended_at || event.started_at)}`,
        ),
      );
      appendEvidenceList(entry, "修改文件", event.deliverables);
      appendEvidenceList(entry, "测试", event.tests);
      appendEvidenceList(entry, "产物", event.artifacts);
      if (event.handoff) appendEvidenceList(entry, "Handoff", [event.handoff]);
      appendEvidenceList(entry, "风险/未完成", event.concerns);
      deliverySection.append(entry);
    });
    container.append(deliverySection);
  }

  function branchEventDepth(event, byEventId) {
    let depth = 0;
    let cursor = event;
    const visited = new Set([event.id]);
    while (cursor && cursor.parent_id && byEventId.has(cursor.parent_id)) {
      if (visited.has(cursor.parent_id)) break;
      visited.add(cursor.parent_id);
      cursor = byEventId.get(cursor.parent_id);
      depth += 1;
    }
    return depth;
  }

  function renderBranchEvents(container, events) {
    const sourceValue = byId("source-filter").value;
    const statusValue = byId("status-filter").value;
    const filteredEvents = events.filter((event) => {
      const sourceMatches = sourceValue === "all" || safeText(event.source, "未知来源") === sourceValue;
      const statusMatches = statusValue === "all" || normalizedStatus(event.status) === statusValue;
      return sourceMatches && statusMatches;
    });
    const section = makeElement("section", "detail-events");
    section.append(makeElement("h4", "detail-events__heading", `真实事件明细 · ${filteredEvents.length}`));
    if (!filteredEvents.length) {
      section.append(makeElement("p", "empty-state", "暂无证据：当前分支没有匹配事件。"));
      container.append(section);
      return;
    }
    const byEventId = new Map(events.filter((event) => event.id).map((event) => [event.id, event]));
    filteredEvents.slice(0, 40).forEach((event) => {
      const card = makeElement("article", `detail-event ${statusClass(event.status)}`);
      card.setAttribute("data-event-id", safeText(event.id, "unknown"));
      card.style.setProperty("--event-depth", String(branchEventDepth(event, byEventId)));
      card.append(makeElement("strong", "detail-event__title", safeText(event.title, "未命名事件")));
      card.append(makeElement("span", "detail-event__status", statusLabel(event.status)));
      card.append(
        makeElement(
          "span",
          "detail-event__meta",
          `${safeText(event.kind, "事件")} · Tool ${safeText(event.tool_name, "无工具证据")}`,
        ),
      );
      card.append(
        makeElement(
          "span",
          "detail-event__scope",
          `Task ${safeText(event.task_id, UNKNOWN)} · Agent/session ${safeText(event.agent_id, UNKNOWN)}`,
        ),
      );
      card.append(
        makeElement(
          "span",
          "detail-event__time",
          `Start ${formatTime(event.started_at)} · End ${formatTime(event.ended_at)}`,
        ),
      );
      card.append(
        makeElement(
          "span",
          "detail-event__evidence",
          safeText(event.detail || event.evidence, "状态依据暂无证据"),
        ),
      );
      const parentRelation = event.parent_id
        ? `Event ${safeText(event.id, "ID 未知")} · Parent ${safeText(event.parent_id, "暂无证据")}`
        : `Event ${safeText(event.id, "ID 未知")} · 根事件 / 暂无父级证据`;
      card.append(makeElement("span", "detail-event__parent", parentRelation));
      if (event.parent_id && !byEventId.has(event.parent_id)) {
        card.append(makeElement("span", "detail-event__relation", "父级未关联"));
      }
      const exit = event.exit_code === undefined || event.exit_code === null
        ? "退出码暂无证据"
        : `退出码 ${event.exit_code}`;
      const duration = event.duration_ms === undefined || event.duration_ms === null
        ? "耗时暂无证据"
        : `耗时 ${event.duration_ms} ms`;
      card.append(makeElement("span", "detail-event__facts", `${exit} · ${duration}`));
      section.append(card);
    });
    if (filteredEvents.length > 40) {
      section.append(makeElement("p", "detail-events__overflow", `另有 ${filteredEvents.length - 40} 条事件，可通过筛选缩小范围。`));
    }
    container.append(section);
  }

  function renderNodeDetail(nodeId) {
    const container = byId("node-detail");
    container.replaceChildren();
    if (!state.snapshot || !nodeId || !state.layout) {
      appendEmpty(container, "选择一个节点查看完整白名单证据。");
      return;
    }
    const node = state.layout.graph.visibleById.get(nodeId);
    if (!node) {
      state.selectedEventId = null;
      appendEmpty(container, "所选节点已不在最新快照中。");
      return;
    }
    const header = makeElement("div", `detail-title ${statusClass(node.status)}`);
    header.append(makeElement("span", "detail-title__kind", safeText(node.eyebrow, "节点")));
    header.append(makeElement("h3", "detail-title__text", safeText(node.label, "未命名节点")));
    header.append(makeElement("span", "status-chip", statusLabel(node.status)));
    container.append(header);
    const list = makeElement("dl", "detail-list");
    [
      ["Node ID", node.id],
      ["Type", node.type],
      ["Branch", node.group],
      ["Status", statusLabel(node.status)],
      ["Status evidence", node.evidence],
      ["Model", node.data && node.data.agent && node.data.agent.model],
      ["Effort", node.data && node.data.agent && node.data.agent.effort],
      ["Task owner", node.type === "task" ? node.data && node.data.task && node.data.task.owner_id : null],
      ["Summary", node.meta],
    ].forEach(([label, value]) => list.append(detailRow(label, value)));
    container.append(list);
    renderProgressAndDeliveries(container, node.data.events);
    if (node.type === "trace") renderBranchEvents(container, node.data.events);
    if (node.type === "agent") renderBranchEvents(container, node.data.events);
    if (node.type === "task") renderBranchEvents(container, node.data.events);
    if (node.type === "aggregation") renderBranchEvents(container, node.data.events);
  }

  function selectEvent(eventId) {
    state.selectedEventId = eventId;
    byId("graph-nodes").querySelectorAll("[data-node-id]").forEach((node) => {
      node.setAttribute("aria-selected", String(node.getAttribute("data-node-id") === eventId));
    });
    renderNodeDetail(eventId);
  }

  function renderGraph(events, agents, tasks, health) {
    const graph = buildOverviewGraph(events, agents, tasks, health);
    const layout = layoutGraph(graph);
    const activeElement = document.activeElement;
    const activeNodeId = activeElement && typeof activeElement.getAttribute === "function"
      ? activeElement.getAttribute("data-node-id")
      : null;
    state.layout = { ...layout, graph };
    const surface = byId("graph-surface");
    const nodes = byId("graph-nodes");
    const empty = byId("graph-empty");
    surface.style.setProperty("width", `${layout.width}px`);
    surface.style.setProperty("height", `${layout.height}px`);
    nodes.style.setProperty("width", `${layout.width}px`);
    nodes.style.setProperty("height", `${layout.height}px`);
    nodes.replaceChildren();
    document.querySelector("[data-graph-count]").textContent = String(graph.nodes.length);
    document.querySelector("[data-edge-count]").textContent = String(graph.edges.length);
    empty.hidden = graph.nodes.length > 0;
    if (!graph.nodes.length) {
      byId("graph-edges").replaceChildren();
      renderNodeDetail(null);
      return;
    }
    graph.nodes.forEach((node) => {
      nodes.append(renderGraphNode(node, layout.positions.get(node.id)));
    });
    if (!state.selectedEventId || !graph.visibleById.has(state.selectedEventId)) {
      state.selectedEventId = graph.nodes[0].id;
    }
    selectEvent(state.selectedEventId);
    if (activeNodeId) {
      const focusTarget = Array.from(nodes.querySelectorAll("[data-node-id]"))
        .find((node) => node.getAttribute("data-node-id") === activeNodeId);
      if (focusTarget && typeof focusTarget.focus === "function") {
        focusTarget.focus({ preventScroll: true });
      }
    }
    if (!state.hasUserScale) {
      const viewportWidth = byId("graph-viewport").clientWidth || layout.width;
      state.scale = viewportWidth < layout.width
        ? Math.max(0.35, (viewportWidth - 18) / layout.width)
        : 1;
    }
    applyScale();
    scheduleEdgeRedraw();
  }

  function renderHealth(items) {
    const container = byId("source-health");
    container.replaceChildren();
    if (!items.length) {
      appendEmpty(container, "暂无证据：来源健康状态未知。");
      return;
    }
    items.forEach((item) => {
      const card = makeElement("article", `health ${statusClass(item.status)}`);
      const top = makeElement("div", "health__top");
      top.append(makeElement("h3", "health__name", safeText(item.source, "未知来源")));
      top.append(makeElement("span", "status-chip", statusLabel(item.status)));
      card.append(top);
      card.append(makeElement("p", "health__detail", safeText(item.detail, "无降级说明")));
      card.append(makeElement("p", "health__meta", `观测 ${formatTime(item.updated_at)}`));
      container.append(card);
    });
  }

  function renderSnapshot(snapshot) {
    state.snapshot = snapshot;
    const agents = asArray(snapshot.agents);
    const tasks = asArray(snapshot.tasks);
    const events = asArray(snapshot.events);
    renderAgents(agents);
    renderTasks(tasks, events);
    updateSourceFilter(events);
    const health = asArray(snapshot.source_health);
    renderGraph(events, agents, tasks, health);
    renderHealth(health);
    const mode = snapshot.connection === "live" ? "live" : "degraded";
    setConnection(
      mode,
      mode === "live" ? "实时证据已连接" : "部分来源证据降级",
      snapshot.generated_at,
    );
  }

  function applyScale() {
    const surface = byId("graph-surface");
    surface.style.setProperty("transform", `scale(${state.scale})`);
    surface.style.setProperty("transform-origin", "0 0");
    byId("graph-scale").textContent = `${Math.round(state.scale * 100)}%`;
  }

  function setScale(value, userInitiated = true) {
    if (userInitiated) state.hasUserScale = true;
    state.scale = Math.min(1.5, Math.max(0.35, value));
    applyScale();
    scheduleEdgeRedraw();
  }

  function fitGraph() {
    if (!state.layout) return;
    const viewport = byId("graph-viewport");
    const xScale = (viewport.clientWidth - 24) / state.layout.width;
    const yScale = (viewport.clientHeight - 24) / state.layout.height;
    setScale(Math.min(1, xScale, yScale), true);
    if (typeof viewport.scrollTo === "function") viewport.scrollTo({ left: 0, top: 0 });
  }

  function rerenderDetails() {
    renderNodeDetail(state.selectedEventId);
  }

  function rerenderGraph() {
    if (!state.snapshot) return;
    renderGraph(
      asArray(state.snapshot.events),
      asArray(state.snapshot.agents),
      asArray(state.snapshot.tasks),
      asArray(state.snapshot.source_health),
    );
  }

  function scheduleGraphRelayout() {
    if (state.relayoutScheduled) return;
    state.relayoutScheduled = true;
    requestAnimationFrame(() => {
      state.relayoutScheduled = false;
      rerenderGraph();
    });
  }

  function toggleMotion() {
    state.motionPaused = !state.motionPaused;
    byId("graph-viewport").classList.toggle("is-motion-paused", state.motionPaused);
    const button = byId("motion-toggle");
    button.setAttribute("aria-pressed", String(state.motionPaused));
    button.textContent = state.motionPaused ? "继续流动" : "暂停流动";
  }

  function showLoadFailure() {
    setConnection("offline", "连接中断，正在等待自动重连", null);
  }

  async function fetchSnapshot() {
    try {
      const response = await fetch("/api/snapshot", {
        headers: { Accept: "application/json" },
        cache: "no-store",
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      renderSnapshot(await response.json());
    } catch (error) {
      showLoadFailure();
    }
  }

  function connectEvents() {
    setConnection("connecting", "正在连接实时证据", state.snapshot?.generated_at);
    const eventSource = new EventSource("/api/events");
    state.eventSource = eventSource;
    eventSource.addEventListener("open", () => {
      if (!state.snapshot) {
        setConnection("live", "实时通道已连接，等待快照", null);
        return;
      }
      const mode = state.snapshot.connection === "live" ? "live" : "degraded";
      setConnection(
        mode,
        mode === "live" ? "实时证据已连接" : "部分来源证据降级",
        state.snapshot.generated_at,
      );
    });
    const handleSnapshot = (message) => {
      try {
        renderSnapshot(JSON.parse(message.data));
      } catch (error) {
        setConnection("degraded", "收到无法读取的快照", state.snapshot?.generated_at);
      }
    };
    eventSource.addEventListener("snapshot", handleSnapshot);
    eventSource.addEventListener("changed", handleSnapshot);
    eventSource.addEventListener("error", showLoadFailure);
  }

  document.addEventListener("DOMContentLoaded", () => {
    byId("source-filter").addEventListener("change", rerenderDetails);
    byId("status-filter").addEventListener("change", rerenderDetails);
    byId("zoom-in").addEventListener("click", () => setScale(state.scale + 0.1));
    byId("zoom-out").addEventListener("click", () => setScale(state.scale - 0.1));
    byId("zoom-fit").addEventListener("click", fitGraph);
    byId("motion-toggle").addEventListener("click", toggleMotion);
    fetchSnapshot();
    connectEvents();
    if (typeof ResizeObserver === "function") {
      const observer = new ResizeObserver(scheduleGraphRelayout);
      observer.observe(byId("graph-viewport"));
    }
  });

  window.addEventListener("resize", scheduleGraphRelayout);
  window.addEventListener("pagehide", () => {
    if (state.eventSource) state.eventSource.close();
  });
})();
