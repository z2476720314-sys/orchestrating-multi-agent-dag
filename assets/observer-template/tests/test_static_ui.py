from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import unittest
from html.parser import HTMLParser
from pathlib import Path
from typing import ClassVar

STATIC_DIR = Path(__file__).resolve().parents[1] / "static"


class _ContractParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: set[str] = set()
        self.attributes: list[dict[str, str | None]] = []
        self.scripts: list[str] = []
        self.stylesheets: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attributes = dict(attrs)
        self.attributes.append(attributes)
        if attributes.get("id"):
            self.ids.add(str(attributes["id"]))
        if tag == "script" and attributes.get("src"):
            self.scripts.append(str(attributes["src"]))
        if tag == "link" and attributes.get("rel") == "stylesheet" and attributes.get("href"):
            self.stylesheets.append(str(attributes["href"]))


class StaticUiContractTests(unittest.TestCase):
    html_path: ClassVar[Path]
    css_path: ClassVar[Path]
    js_path: ClassVar[Path]
    html: ClassVar[str]
    css: ClassVar[str]
    js: ClassVar[str]
    parser: ClassVar[_ContractParser]

    @classmethod
    def setUpClass(cls) -> None:
        cls.html_path = STATIC_DIR / "index.html"
        cls.css_path = STATIC_DIR / "app.css"
        cls.js_path = STATIC_DIR / "app.js"
        cls.html = cls.html_path.read_text(encoding="utf-8")
        cls.css = cls.css_path.read_text(encoding="utf-8")
        cls.js = cls.js_path.read_text(encoding="utf-8")
        cls.parser = _ContractParser()
        cls.parser.feed(cls.html)

    def run_renderer(
        self,
        snapshot: dict[str, object],
        event_name: str = "snapshot",
        *,
        source_filter: str = "all",
        status_filter: str = "all",
        width: int = 1200,
    ) -> dict[str, object]:
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js is unavailable for the browser-behavior harness")
        harness = r'''
const fs = require("fs");
const vm = require("vm");

function matches(element, selector) {
  if (selector.startsWith("#")) return element.id === selector.slice(1);
  if (selector.startsWith(".")) {
    return element.className.split(/\s+/).includes(selector.slice(1));
  }
  const attr = selector.match(/^\[([^=\]]+)(?:=["']?([^"'\]]+)["']?)?\]$/);
  if (attr) {
    if (!element.attributes.has(attr[1])) return false;
    return attr[2] === undefined || element.attributes.get(attr[1]) === attr[2];
  }
  return element.tagName.toLowerCase() === selector.toLowerCase();
}

class FakeElement {
  constructor(tagName, id = "") {
    this.tagName = tagName;
    this.id = id;
    this.children = [];
    this.parentElement = null;
    this.attributes = new Map();
    this.listeners = new Map();
    this.queryMap = new Map();
    this.value = "all";
    this.hidden = false;
    this.className = "";
    this.textContent = "";
    this.clientWidth = id === "graph-viewport" ? Number(process.argv[6]) : 1200;
    this.clientHeight = id === "graph-viewport" ? 720 : 0;
    this.scrollLeft = 0;
    this.scrollTop = 0;
    this.style = {
      values: {},
      setProperty: (name, value) => { this.style.values[name] = String(value); },
      getPropertyValue: (name) => this.style.values[name] || "",
    };
    this.classList = {
      add: (...names) => {
        const classes = new Set(this.className.split(/\s+/).filter(Boolean));
        names.forEach((name) => classes.add(name));
        this.className = [...classes].join(" ");
      },
      remove: (...names) => {
        const remove = new Set(names);
        this.className = this.className
          .split(/\s+/)
          .filter((name) => name && !remove.has(name))
          .join(" ");
      },
      toggle: (name, force) => {
        if (force) this.classList.add(name); else this.classList.remove(name);
      },
      contains: (name) => this.className.split(/\s+/).includes(name),
    };
  }
  append(...children) {
    children.forEach((child) => {
      child.parentElement = this;
      this.children.push(child);
    });
  }
  replaceChildren(...children) {
    this.children = [];
    this.append(...children);
  }
  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  getAttribute(name) { return this.attributes.get(name) || null; }
  removeAttribute(name) { this.attributes.delete(name); }
  addEventListener(name, callback) { this.listeners.set(name, callback); }
  querySelector(selector) {
    if (this.queryMap.has(selector)) return this.queryMap.get(selector);
    return this.querySelectorAll(selector)[0] || null;
  }
  querySelectorAll(selector) {
    const result = [];
    const visit = (current) => current.children.forEach((child) => {
      if (matches(child, selector)) result.push(child);
      visit(child);
    });
    visit(this);
    return result;
  }
  getBoundingClientRect() {
    const number = (name, fallback) => {
      const raw = this.style.values[name] ?? this.style[name];
      const value = Number.parseFloat(raw);
      return Number.isFinite(value) ? value : fallback;
    };
    const left = number("left", 0);
    const top = number("top", 0);
    const width = number("width", this.id === "graph-surface" ? 1200 : 264);
    const height = number("height", this.id === "graph-surface" ? 720 : 184);
    let scale = 1;
    let cursor = this;
    while (cursor) {
      const transform = cursor.style?.values?.transform || "";
      const match = transform.match(/scale\(([^)]+)\)/);
      if (match) scale *= Number.parseFloat(match[1]);
      cursor = cursor.parentElement;
    }
    return {
      left: left * scale, top: top * scale,
      right: (left + width) * scale, bottom: (top + height) * scale,
      width: width * scale, height: height * scale,
    };
  }
  scrollTo(options) {
    this.scrollLeft = options.left || 0;
    this.scrollTop = options.top || 0;
  }
}

const ids = new Map();
for (const id of [
  "agent-tree", "task-board", "source-health", "source-filter", "status-filter",
  "graph-viewport", "graph-surface", "graph-edges", "graph-nodes", "graph-empty",
  "node-detail", "graph-scale", "zoom-in", "zoom-out", "zoom-fit", "motion-toggle",
]) ids.set(id, new FakeElement("div", id));
ids.get("source-filter").value = process.argv[4];
ids.get("status-filter").value = process.argv[5];
ids.get("graph-surface").append(ids.get("graph-edges"), ids.get("graph-nodes"));

const connection = new FakeElement("div", "connection-status");
const connectionLabel = new FakeElement("span");
const generatedAt = new FakeElement("time");
connection.queryMap.set("[data-connection-label]", connectionLabel);
connection.queryMap.set("[data-generated-at]", generatedAt);
ids.set("connection-status", connection);

const globalQueries = new Map();
for (const name of ["agent", "task", "graph", "edge"]) {
  globalQueries.set(`[data-${name}-count]`, new FakeElement("span"));
}
for (const lane of ["codex", "dsh", "workbuddy"]) {
  const root = new FakeElement("section");
  root.queryMap.set("[data-lane-state]", new FakeElement("span"));
  root.queryMap.set("[data-lane-events]", new FakeElement("ol"));
  globalQueries.set(`[data-lane="${lane}"]`, root);
}

const documentListeners = new Map();
global.document = {
  getElementById: (id) => ids.get(id) || null,
  createElement: (tagName) => new FakeElement(tagName),
  createElementNS: (_namespace, tagName) => new FakeElement(tagName),
  addEventListener: (name, callback) => documentListeners.set(name, callback),
  querySelector: (selector) => globalQueries.get(selector) || null,
};
global.window = {
  addEventListener: () => {},
  requestAnimationFrame: (callback) => { callback(); return 1; },
  cancelAnimationFrame: () => {},
  matchMedia: () => ({ matches: false }),
};
global.requestAnimationFrame = window.requestAnimationFrame;
global.cancelAnimationFrame = window.cancelAnimationFrame;
global.ResizeObserver = class { observe() {} disconnect() {} };
global.fetch = () => new Promise(() => {});
class FakeEventSource {
  constructor() { this.listeners = new Map(); global.eventSource = this; }
  addEventListener(name, callback) { this.listeners.set(name, callback); }
  emit(name, data) {
    const callback = this.listeners.get(name);
    if (callback) callback({ data });
  }
  close() {}
}
global.EventSource = FakeEventSource;

vm.runInThisContext(fs.readFileSync(process.argv[1], "utf8"), {
  filename: process.argv[1],
});
documentListeners.get("DOMContentLoaded")();
eventSource.emit(process.argv[2], process.argv[3]);

const agentCards = ids.get("agent-tree").children;
const renderedAgentCards = agentCards.filter((item) => item.tagName === "article");
const firstStatus = agentCards[0]?.children[0]?.children[1]?.textContent || null;
const secondDepth = agentCards[1]?.style.values["--agent-depth"] || null;
const nodes = ids.get("graph-nodes").children.map((item) => ({
  id: item.getAttribute("data-node-id"),
  role: item.getAttribute("data-role"),
  status: item.getAttribute("data-status"),
  parentState: item.getAttribute("data-parent-state"),
  x: Number.parseFloat(item.style.values.left),
  y: Number.parseFloat(item.style.values.top),
  width: Number.parseFloat(item.style.values.width),
  height: Number.parseFloat(item.style.values.height),
  text: item.children.map((child) => child.textContent).join(" "),
}));
const svg = ids.get("graph-edges");
const edges = svg.children
  .filter((item) => item.getAttribute("data-edge-id"))
  .map((item) => ({
    id: item.getAttribute("data-edge-id"),
    from: item.getAttribute("data-from"),
    to: item.getAttribute("data-to"),
    layer: item.getAttribute("data-layer"),
    color: item.getAttribute("stroke"),
    d: item.getAttribute("d"),
  }));
const codexTrace = ids.get("graph-nodes").children
  .find((item) => item.getAttribute("data-node-id") === "trace:codex");
if (codexTrace?.listeners.has("click")) codexTrace.listeners.get("click")();
const detailScopes = ids.get("node-detail").querySelectorAll(".detail-event__scope")
  .map((item) => item.textContent);
const detailTimes = ids.get("node-detail").querySelectorAll(".detail-event__time")
  .map((item) => item.textContent);
process.stdout.write(JSON.stringify({
  connectionLabel: connectionLabel.textContent,
  firstStatus,
  secondDepth,
  agentItemCount: renderedAgentCards.length,
  agentNames: renderedAgentCards.map(
    (card) => card.children[0]?.children[0]?.textContent || null,
  ),
  agentOverflow: agentCards.at(-1)?.textContent || null,
  nodes,
  edges,
  viewBox: svg.getAttribute("viewBox"),
  preserveAspectRatio: svg.getAttribute("preserveAspectRatio"),
  detailScopes,
  detailTimes,
}));
'''
        completed = subprocess.run(
            [
                node,
                "-e",
                harness,
                str(self.js_path),
                event_name,
                json.dumps(snapshot, ensure_ascii=False),
                source_filter,
                status_filter,
                str(width),
            ],
            capture_output=True,
            check=False,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        return json.loads(completed.stdout)

    @staticmethod
    def _sample_snapshot() -> dict[str, object]:
        return {
            "connection": "live",
            "agents": [
                {
                    "id": "codex:root", "name": "Codex /root", "kind": "codex",
                    "status": "running", "model": "gpt-6-astra", "effort": "high",
                    "status_evidence": "task_started",
                },
                {
                    "id": "dsh:root", "name": "DSH", "kind": "dsh",
                    "status": "completed", "model": "deepseek-v4.1-flash",
                    "effort": "max", "status_evidence": "turn 已结束",
                },
                {
                    "id": "workbuddy:root", "name": "WorkBuddy",
                    "kind": "workbuddy", "status": "unknown",
                    "status_evidence": "WorkBuddy ps",
                },
            ],
            "tasks": [{
                "id": "task:current", "title": "当前多 Agent 协作",
                "status": "running", "owner_id": "coordination:codex",
                "status_evidence": "协调状态声明",
            }],
            "events": [
                {
                    "id": "evt:tool", "parent_id": "evt:turn",
                    "agent_id": "codex:root", "source": "codex",
                    "task_id": "task:current",
                    "status": "completed", "kind": "tool", "title": "运行测试",
                    "tool_name": "exec_command", "started_at": "2026-09-12T00:00:02Z",
                    "ended_at": "2026-09-12T00:00:04Z",
                },
                {
                    "id": "evt:orphan", "parent_id": "evt:missing",
                    "agent_id": "codex:root", "source": "codex",
                    "status": "unknown", "kind": "lifecycle", "title": "孤立事件",
                    "started_at": "2026-09-12T00:00:03Z",
                },
                {
                    "id": "evt:session", "agent_id": "codex:root", "source": "codex",
                    "status": "running", "kind": "session", "title": "会话",
                    "started_at": "2026-09-12T00:00:00Z",
                },
                {
                    "id": "evt:turn", "parent_id": "evt:session",
                    "agent_id": "codex:root", "source": "codex",
                    "status": "running", "kind": "task_started", "title": "实现 DAG",
                    "started_at": "2026-09-12T00:00:01Z",
                },
                {
                    "id": "dsh:event", "agent_id": "dsh:root", "source": "dsh",
                    "status": "completed", "kind": "lifecycle", "title": "DSH 会话投影",
                },
                {
                    "id": "workbuddy:event", "agent_id": "workbuddy:root",
                    "source": "workbuddy", "status": "unknown", "kind": "lifecycle",
                    "title": "WorkBuddy 进程状态",
                },
            ],
            "source_health": [
                {"source": "codex", "status": "healthy", "detail": "已读取"},
                {"source": "dsh", "status": "healthy", "detail": "已读取"},
                {"source": "workbuddy", "status": "healthy", "detail": "已读取"},
            ],
        }

    def test_static_assets_are_wired_without_dependencies(self) -> None:
        self.assertIn("/static/app.css", self.parser.stylesheets)
        self.assertIn("/static/app.js", self.parser.scripts)
        self.assertNotRegex(self.html, r"https?://")

    def test_semantic_dag_regions_exist(self) -> None:
        expected = {
            "connection-status", "agent-tree", "task-board", "graph-viewport",
            "graph-surface", "graph-edges", "graph-nodes", "node-detail",
            "source-health", "source-filter", "status-filter", "zoom-in",
            "zoom-out", "zoom-fit", "motion-toggle",
        }
        self.assertEqual(expected, expected & self.parser.ids)

    def test_connection_changes_are_announced(self) -> None:
        matching = [attrs for attrs in self.parser.attributes if attrs.get("id") == "connection-status"]
        self.assertEqual("polite", matching[0].get("aria-live"))

    def test_ui_contains_honest_chinese_empty_and_error_language(self) -> None:
        combined = self.html + self.js
        for phrase in ("暂无证据", "未知", "内容已隐藏", "连接中断", "父级未关联"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, combined)

    def test_snapshot_and_sse_contracts_are_consumed(self) -> None:
        self.assertRegex(self.js, r"fetch\s*\(\s*['\"]\/api\/snapshot['\"]")
        self.assertRegex(self.js, r"new\s+EventSource\s*\(\s*['\"]\/api\/events['\"]")
        self.assertRegex(self.js, r"addEventListener\s*\(\s*['\"]snapshot['\"]")

    def test_changed_sse_event_renders_the_new_snapshot(self) -> None:
        rendered = self.run_renderer({
            "connection": "degraded", "agents": [], "tasks": [],
            "events": [], "source_health": [],
        }, "changed")
        self.assertEqual("部分来源证据降级", rendered["connectionLabel"])

    def test_sse_rerender_restores_focus_to_the_same_overview_node(self) -> None:
        self.assertIn("document.activeElement", self.js)
        self.assertIn("activeNodeId", self.js)
        self.assertIn("focus({ preventScroll: true })", self.js)

    def test_viewport_resize_recomputes_the_compact_graph_layout(self) -> None:
        self.assertIn("function scheduleGraphRelayout()", self.js)
        self.assertIn('observer.observe(byId("graph-viewport"))', self.js)
        self.assertIn('window.addEventListener("resize", scheduleGraphRelayout)', self.js)

    def test_agent_tree_keeps_status_and_parent_depth_behavior(self) -> None:
        rendered = self.run_renderer({
            "connection": "live",
            "agents": [
                {"id": "codex:root", "name": "根", "kind": "codex", "status": "started"},
                {"id": "codex:child", "name": "子", "kind": "codex", "parent_id": "root", "status": "running"},
            ],
            "tasks": [], "events": [], "source_health": [],
        })
        self.assertEqual("运行中", rendered["firstStatus"])
        self.assertEqual("1", rendered["secondDepth"])

    def test_declared_planned_and_blocked_statuses_reach_the_overview(self) -> None:
        snapshot = self._sample_snapshot()
        snapshot["tasks"][0]["status"] = "planned"
        snapshot["agents"][0]["status"] = "blocked"
        for event in snapshot["events"]:
            if event["source"] == "codex":
                event["status"] = "blocked"

        rendered = self.run_renderer(snapshot)
        statuses = {node["id"]: node["status"] for node in rendered["nodes"]}

        self.assertEqual("planned", statuses["task:task:current"])
        self.assertEqual("blocked", statuses["agent:codex"])
        self.assertEqual("blocked", statuses["trace:codex"])

    def test_blocked_task_wins_over_a_later_planned_task(self) -> None:
        snapshot = self._sample_snapshot()
        snapshot["tasks"] = [
            {
                "id": "task:blocked", "title": "等待用户选择",
                "status": "blocked", "owner_id": "coordination:codex",
                "status_evidence": "协调状态声明",
            },
            {
                "id": "task:later", "title": "后续计划",
                "status": "planned", "owner_id": "coordination:workbuddy",
                "status_evidence": "协调状态声明",
            },
        ]
        for event in snapshot["events"]:
            event["task_id"] = "task:blocked"

        rendered = self.run_renderer(snapshot)
        statuses = {node["id"]: node["status"] for node in rendered["nodes"]}

        self.assertIn("task:task:blocked", statuses)
        self.assertNotIn("task:task:later", statuses)
        self.assertEqual("blocked", statuses["task:task:blocked"])

    def test_selected_dag_is_task_agents_traces_and_aggregation(self) -> None:
        rendered = self.run_renderer(self._sample_snapshot())
        node_ids = {node["id"] for node in rendered["nodes"]}
        edge_pairs = {
            (edge["from"], edge["to"])
            for edge in rendered["edges"] if edge["layer"] == "track"
        }
        self.assertEqual(
            {
                "task:task:current", "agent:codex", "agent:dsh",
                "agent:workbuddy", "trace:codex", "trace:dsh",
                "trace:workbuddy", "aggregation:sources",
            },
            node_ids,
        )
        self.assertEqual(
            {
                ("task:task:current", "agent:codex"),
                ("task:task:current", "agent:dsh"),
                ("task:task:current", "agent:workbuddy"),
                ("agent:codex", "trace:codex"),
                ("agent:dsh", "trace:dsh"),
                ("agent:workbuddy", "trace:workbuddy"),
                ("trace:codex", "aggregation:sources"),
                ("trace:dsh", "aggregation:sources"),
                ("trace:workbuddy", "aggregation:sources"),
            },
            edge_pairs,
        )

    def test_task_branches_do_not_inherit_unlinked_global_activity(self) -> None:
        rendered = self.run_renderer(self._sample_snapshot())
        nodes = {node["id"]: node for node in rendered["nodes"]}
        self.assertEqual("unknown", nodes["agent:dsh"]["status"])
        self.assertEqual("unknown", nodes["trace:dsh"]["status"])
        self.assertIn("0 个关联会话", nodes["agent:dsh"]["text"])
        self.assertIn("0 条事件", nodes["trace:dsh"]["text"])
        self.assertIn("3 条事件", nodes["trace:codex"]["text"])

    def test_completed_current_coordination_task_stays_the_dag_root(self) -> None:
        snapshot = self._sample_snapshot()
        snapshot["tasks"][0]["status"] = "completed"
        snapshot["tasks"].append({
            "id": "handoff:older",
            "title": "旧交接",
            "status": "unknown",
            "status_evidence": "暂无证据",
        })
        rendered = self.run_renderer(snapshot)
        task_nodes = [node for node in rendered["nodes"] if node["role"] == "match" and node["id"].startswith("task:")]
        self.assertEqual(["task:task:current"], [node["id"] for node in task_nodes])

    def test_detail_filter_does_not_change_selected_overview_structure(self) -> None:
        rendered = self.run_renderer(self._sample_snapshot(), source_filter="dsh")
        roles = {node["id"]: node["role"] for node in rendered["nodes"]}
        self.assertEqual(8, len(roles))
        self.assertEqual({"match"}, set(roles.values()))

    def test_detail_parent_lookup_uses_the_complete_branch_scope(self) -> None:
        lookup = re.search(
            r"function renderBranchEvents\(container, events\).*?"
            r"const byEventId = new Map\((.*?)\);",
            self.js,
            re.DOTALL,
        )
        self.assertIsNotNone(lookup)
        self.assertIn("events.filter", lookup.group(1))
        self.assertNotIn("filteredEvents.filter", lookup.group(1))

    def test_unknown_and_unrecognized_statuses_remain_unknown(self) -> None:
        snapshot = self._sample_snapshot()
        snapshot["agents"] = []
        snapshot["tasks"] = []
        snapshot["events"] = []
        snapshot["source_health"] = []
        rendered = self.run_renderer(snapshot)
        self.assertEqual({"unknown"}, {node["status"] for node in rendered["nodes"]})
        self.assertTrue(all("未知" in node["text"] for node in rendered["nodes"]))

    def test_edge_colors_are_unique_and_stable_by_identity(self) -> None:
        snapshot = self._sample_snapshot()
        first = self.run_renderer(snapshot)
        snapshot["events"] = list(reversed(snapshot["events"]))
        second = self.run_renderer(snapshot)

        def colors(rendered: dict[str, object]) -> dict[str, str]:
            return {
                edge["id"]: edge["color"]
                for edge in rendered["edges"] if edge["layer"] == "track"
            }

        first_colors = colors(first)
        self.assertEqual(len(first_colors), len(set(first_colors.values())))
        self.assertEqual(first_colors, colors(second))

    def test_edge_endpoints_equal_card_border_centers(self) -> None:
        rendered = self.run_renderer(self._sample_snapshot())
        nodes = {node["id"]: node for node in rendered["nodes"]}
        tracks = [edge for edge in rendered["edges"] if edge["layer"] == "track"]
        number = r"-?\d+(?:\.\d+)?"
        for edge in tracks:
            match = re.match(
                rf"M ({number}) ({number}) C ({number}) ({number}), ({number}) ({number}), ({number}) ({number})$",
                edge["d"],
            )
            self.assertIsNotNone(match, edge["d"])
            values = [float(value) for value in match.groups()]
            parent = nodes[edge["from"]]
            child = nodes[edge["to"]]
            self.assertGreaterEqual(values[0], parent["x"])
            self.assertLessEqual(values[0], parent["x"] + parent["width"])
            self.assertAlmostEqual(parent["y"] + parent["height"], values[1])
            self.assertGreaterEqual(values[6], child["x"])
            self.assertLessEqual(values[6], child["x"] + child["width"])
            self.assertAlmostEqual(child["y"], values[7])

    def test_each_edge_has_track_motion_and_terminals_without_duplication(self) -> None:
        rendered = self.run_renderer(self._sample_snapshot(), event_name="changed")
        grouped: dict[str, list[dict[str, str]]] = {}
        for edge in rendered["edges"]:
            grouped.setdefault(edge["id"], []).append(edge)
        self.assertEqual(9, len(grouped))
        for layers in grouped.values():
            self.assertEqual(
                {"track", "motion", "terminal-start", "terminal-end"},
                {layer["layer"] for layer in layers},
            )
            paths = [layer for layer in layers if layer["layer"] in {"track", "motion"}]
            self.assertEqual(paths[0]["d"], paths[1]["d"])
            self.assertEqual(paths[0]["color"], paths[1]["color"])

    def test_svg_coordinates_are_finite_and_viewbox_matches_surface(self) -> None:
        rendered = self.run_renderer(self._sample_snapshot(), width=375)
        self.assertEqual("none", rendered["preserveAspectRatio"])
        values = [float(value) for value in rendered["viewBox"].split()]
        self.assertEqual(4, len(values))
        self.assertEqual(375, values[2])
        self.assertGreater(values[2], 0)
        self.assertGreater(values[3], 0)
        self.assertTrue(all(math.isfinite(value) for value in values))
        for node in rendered["nodes"]:
            self.assertGreaterEqual(node["x"], 0)
            self.assertGreaterEqual(node["y"], 0)
            self.assertLessEqual(node["x"] + node["width"], values[2])
            self.assertLessEqual(node["y"] + node["height"], values[3])

    def test_renderer_consumes_detailed_evidence_fields(self) -> None:
        for field in (
            "owner_id", "status_evidence", "tool_name", "duration_ms", "exit_code",
            "event.evidence", "agent.model", "agent.effort",
        ):
            with self.subTest(field=field):
                self.assertIn(field, self.js)

    def test_non_task_detail_never_renders_boolean_false_as_task_owner(self) -> None:
        self.assertNotIn('node.type === "task" &&', self.js)
        self.assertIn('node.type === "task" ?', self.js)

    def test_event_detail_renders_task_agent_and_separate_start_end_evidence(self) -> None:
        rendered = self.run_renderer(self._sample_snapshot())
        self.assertIn(
            "Task task:current · Agent/session codex:root",
            rendered["detailScopes"],
        )
        matching_times = [
            value for value in rendered["detailTimes"]
            if value.startswith("Start ") and " · End " in value
        ]
        self.assertTrue(matching_times)

    def test_dynamic_content_uses_text_content_not_html_injection(self) -> None:
        self.assertIn("textContent", self.js)
        self.assertNotIn("innerHTML", self.js)
        self.assertNotIn("insertAdjacentHTML", self.js)

    def test_dark_neon_tokens_and_individual_edge_layers_are_present(self) -> None:
        for token in ("#070a12", "#0d1324", "#63f3ff", "#b96cff", "#ffbf47"):
            with self.subTest(token=token):
                self.assertIn(token, self.css.lower())
        self.assertIn(".dag-edge__track", self.css)
        self.assertIn(".dag-edge__motion", self.css)
        self.assertRegex(self.css, r"stroke-dasharray\s*:")
        self.assertRegex(self.css, r"filter\s*:\s*drop-shadow")

    def test_layout_focus_and_reduced_motion_are_accessible(self) -> None:
        self.assertRegex(self.css, r"@media\s*\([^)]*max-width")
        self.assertIn(":focus-visible", self.css)
        self.assertRegex(self.css, r"@media\s*\(\s*prefers-reduced-motion\s*:\s*reduce\s*\)")
        reduced = re.search(
            r"@media\s*\(\s*prefers-reduced-motion\s*:\s*reduce\s*\)\s*\{(.+?)\n\}",
            self.css,
            re.DOTALL,
        )
        self.assertIsNotNone(reduced)
        self.assertIn(".dag-edge__motion", reduced.group(1))
        self.assertNotIn(".dag-edge__track", reduced.group(1))


if __name__ == "__main__":
    unittest.main()
