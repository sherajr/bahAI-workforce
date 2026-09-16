import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Background, Controls, Handle, MarkerType, MiniMap, Position, ReactFlow, ReactFlowProvider,
  type Edge, type Node, type NodeProps, useEdgesState, useNodesState, useReactFlow,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import {
  AlertTriangle, ArrowLeft, CheckCircle2, ChevronDown, ChevronRight, Compass, Download, FileCode,
  Folder, HelpCircle, Image as ImageIcon, LayoutGrid, Lightbulb, Link2, List, Loader2, LocateFixed,
  Maximize2, Minimize2, Network, PanelRightClose, PanelRightOpen, Pencil, Pin, Quote, Scan, Search,
  Sparkles, Trash2, X,
} from "lucide-react";
import { api, recordActivity } from "../../lib/api";
import type {
  ConceptGraph as ConceptGraphT, ConsultationAction, ConsultationCapabilities,
  ConsultationDecision, GraphEdge, GraphNode, GraphRelation, GraphRole, OrganizePreview,
  OrganizeTreeNode,
} from "../../lib/consultationTypes";
import { Button, Card } from "../ui";
import {
  buildFocusView, focusableQuestions, GROUP_META, type DetailLevel, type FocusGroupCount,
  type FocusView,
} from "../../lib/consultationFocus";

/**
 * The interactive concept map — a real, connected graph of the consultation,
 * not a list of cards (section 1). Root is the question; themes are the topic
 * branches this particular meeting is actually organised around; everything
 * else is a leaf that may sit under a theme and carry real cross-links to any
 * other leaf.
 *
 * Nodes carry no content of their own — `graph.nodes[].detail` is exactly the
 * wording on the canonical record (`record_ref` says which one), so every
 * correction here goes through the SAME endpoints the rest of the app already
 * uses (`api.editConsultationMapItem`, `api.confirmConsultationDecision`,
 * `api.acceptConsultationAction`, …). This component never invents a second
 * way to edit a fact or a commitment.
 */

const prefersReducedMotion = () =>
  typeof window !== "undefined"
  && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches === true;

type BranchStats = { total: number; open: number; sample: string[] };
type FlowNodeData = {
  graphNode: GraphNode; dimmed: boolean; matched: boolean; color: string; stats?: BranchStats;
  role: GraphRole | null; roleLabel: string;
  /** Overrides the kind-derived icon (`ROLE_ICON[role]`) for a Focus card,
   *  whose role vocabulary (dependency/adjustment) is wider than the six
   *  kind-roles `role` carries. */
  icon?: typeof HelpCircle;
  /** Focus view only, set on a proposal card: what it has and how much of
   *  each relation type is currently shown, so a reader sees "2 more
   *  reasons" / "3 open concerns" separately rather than one opaque "+N"
   *  (rule 137). */
  groups?: FocusGroupCount[];
  /** Focus view only: expand one of this card's groups in place. `key` is
   *  `${proposalId}::${groupKind}`. */
  onToggleGroup?: (key: string) => void;
};
type FlowEdgeData = { relation: GraphRelation; dimmed: boolean };

// Statuses that still count as "open" for a branch's badge, spanning every
// list's own vocabulary (a lifecycle, a fact state, a decision or action
// status) — approximate on purpose, since this only drives a discoverability
// hint on a COLLAPSED card (section 3: "show counts and unresolved-concern/
// action indicators so collapsed content is discoverable"), never a claim
// about the record itself.
const OPEN_STATUSES = new Set([
  "open", "reported", "disputed", "candidate", "proposed", "accepted", "in_progress",
]);

const SAMPLE_PRIORITY: Record<string, number> = { proposal: 0, concern: 1, question: 2, outcome: 3 };
const MAX_BRANCH_SAMPLE = 3;

/** For every theme/bucket branch: how many leaves it holds (through ANY
 *  depth of nested branches), how many of those still look unresolved, and
 *  a short, representative sample of what is actually inside -- so a
 *  COLLAPSED card reads as "Course comparison: 12 items, e.g. Compare both
 *  courses' syllabuses, Neither course's digital relevance is known..."
 *  rather than an empty folder with a number on it (section 5A: "a short
 *  branch summary, representative distinct possibilities... Empty folder
 *  cards plus item counts are insufficient"). Proposals and concerns are
 *  preferred over a generic fact when there is a choice, since those are
 *  usually what a reader wants to know is inside before opening a branch. */
function branchStats(graph: ConceptGraphT, capabilities: ConsultationCapabilities): Map<string, BranchStats> {
  const byId = new Map(graph.nodes.map((n) => [n.id, n]));
  const children = new Map<string, string[]>();
  for (const e of graph.edges) {
    if (e.relation !== "contains") continue;
    const list = children.get(e.from_id) ?? [];
    list.push(e.to_id);
    children.set(e.from_id, list);
  }
  const stats = new Map<string, BranchStats>();
  function walk(id: string, seen: Set<string>): BranchStats & { leaves: GraphNode[] } {
    if (seen.has(id)) return { total: 0, open: 0, sample: [], leaves: [] };
    seen = new Set([...seen, id]);
    let total = 0, open = 0;
    const leaves: GraphNode[] = [];
    for (const childId of children.get(id) ?? []) {
      const child = byId.get(childId);
      if (!child) continue;
      if (child.kind === "theme" || child.kind === "bucket") {
        const sub = walk(childId, seen);
        total += sub.total;
        open += sub.open;
        leaves.push(...sub.leaves);
      } else {
        total += 1;
        if (child.status && OPEN_STATUSES.has(child.status)) open += 1;
        leaves.push(child);
      }
    }
    const ranked = [...leaves].sort((a, b) => {
      const pa = SAMPLE_PRIORITY[roleOf(a.kind, capabilities) ?? ""] ?? 9;
      const pb = SAMPLE_PRIORITY[roleOf(b.kind, capabilities) ?? ""] ?? 9;
      return pa - pb;
    });
    const sample = ranked.slice(0, MAX_BRANCH_SAMPLE).map((n) => n.label);
    const result = { total, open, sample, leaves };
    stats.set(id, { total, open, sample });
    return result;
  }
  for (const n of graph.nodes) {
    if (n.kind === "theme" || n.kind === "bucket") walk(n.id, new Set());
  }
  return stats;
}

function relationStyle(relation: GraphRelation, capabilities: ConsultationCapabilities) {
  const meta = capabilities.edge_relations.find((r) => r.id === relation);
  const dash = meta?.style === "dashed" ? "6 4" : meta?.style === "dotted" ? "1 4" : undefined;
  return { dash, label: meta?.label ?? relation };
}

function kindColor(kind: string, capabilities: ConsultationCapabilities): string {
  return capabilities.node_kinds.find((k) => k.id === kind)?.color ?? "#64748b";
}

// ── The reading roles (rule 133/135): a restrained, six-way lens over the 15
// node kinds, so a card is scannable by what it is ARGUING -- a question, a
// proposed answer, a reason, a concern, an outcome, or a topic container --
// before a reader has to parse which of fifteen specific kinds it is. Colour
// AND an icon carry the distinction (section 5: "distinguishable with words/
// icons as well as color"), never colour alone. Falls back to the node's own
// kind colour when a backend predates rule 133 (`role_of_kind` absent).
const ROLE_COLOR: Record<GraphRole, string> = {
  question: "#94a3b8", proposal: "#34d399", reason: "#38bdf8",
  concern: "#fb923c", outcome: "#facc15", topic: "#a1a1aa",
};
const ROLE_ICON: Record<GraphRole, typeof HelpCircle> = {
  question: HelpCircle, proposal: Lightbulb, reason: CheckCircle2,
  concern: AlertTriangle, outcome: CheckCircle2, topic: Folder,
};

function roleOf(kind: string, capabilities: ConsultationCapabilities): GraphRole | null {
  return capabilities.role_of_kind?.[kind as GraphNode["kind"]] ?? null;
}

function roleColor(kind: string, capabilities: ConsultationCapabilities): string {
  const role = roleOf(kind, capabilities);
  return role ? ROLE_COLOR[role] : kindColor(kind, capabilities);
}

// Labels are SERVED (`capabilities.node_roles`), never duplicated here (the
// same reasoning as the rest of this legend, rule 87) -- a role with no
// server-provided label (an older backend, or the fallback "focus"/"proposal"
// synthesized client-side for the focus view) falls back to the node's kind.
function roleLabelOf(role: GraphRole | null, kind: string, capabilities: ConsultationCapabilities): string {
  const meta = role ? capabilities.node_roles?.find((r) => r.id === role) : undefined;
  if (meta) return meta.label;
  return kind === "bucket" ? "category" : kind;
}

/** Every descendant of a collapsed branch, via hierarchy edges only. */
function collapsedDescendants(graph: ConceptGraphT): Set<string> {
  const children = new Map<string, string[]>();
  for (const e of graph.edges) {
    if (e.relation !== "contains") continue;
    const list = children.get(e.from_id) ?? [];
    list.push(e.to_id);
    children.set(e.from_id, list);
  }
  const collapsedRoots = graph.nodes.filter((n) => n.collapsed).map((n) => n.id);
  const hidden = new Set<string>();
  const stack = [...collapsedRoots];
  while (stack.length) {
    const id = stack.pop()!;
    for (const child of children.get(id) ?? []) {
      if (hidden.has(child)) continue;
      hidden.add(child);
      stack.push(child);
    }
  }
  return hidden;
}

// Every card gets a handle on all four sides, in BOTH directions, so an edge
// can always leave/enter on the side actually facing the other node instead
// of always top/bottom (rule 137: "a reason displayed below its proposal
// must have a visible, correctly directed connection that avoids the
// intervening cards" -- with only top-target/bottom-source, a reason
// stacked BELOW its proposal, itself above a second sibling, had no way to
// reach the proposal except drawing through that sibling). All eight are
// invisible; `pickHandles` below chooses the pair that fits the actual
// relative position of the two real endpoints.
const HANDLE_SPECS: { id: string; type: "source" | "target"; position: Position }[] = [
  { id: "top-source", type: "source", position: Position.Top },
  { id: "top-target", type: "target", position: Position.Top },
  { id: "bottom-source", type: "source", position: Position.Bottom },
  { id: "bottom-target", type: "target", position: Position.Bottom },
  { id: "left-source", type: "source", position: Position.Left },
  { id: "left-target", type: "target", position: Position.Left },
  { id: "right-source", type: "source", position: Position.Right },
  { id: "right-target", type: "target", position: Position.Right },
];

function ConceptNode({ data, selected }: NodeProps<Node<FlowNodeData, "concept">>) {
  const { graphNode: n, dimmed, matched, color, stats, role, roleLabel, icon, groups, onToggleGroup } = data;
  const isRoot = n.kind === "root";
  const isBranch = n.kind === "theme" || n.kind === "bucket";
  const RoleIcon = icon ?? (role ? ROLE_ICON[role] : null);
  return (
    <div
      role="button"
      tabIndex={0}
      aria-label={`${roleLabel}: ${n.label}`}
      style={{
        borderColor: color,
        opacity: dimmed ? 0.25 : 1,
        // The card's own SERVED width/height (`agents/live_consultation_graph.py`'s
        // `_estimate_size`) is the single source of truth for its box, the
        // same one collision-avoidance and export layout already use --
        // rendering a fixed 200px box regardless is what made a full sentence
        // read as three clipped words (rule 135).
        width: n.width ?? 240,
        minHeight: n.height ?? 72,
      }}
      className={`rounded-lg border-2 bg-slate-900 px-3 py-2 text-left shadow-sm transition-opacity
        ${selected ? "ring-2 ring-amber-300" : ""} ${matched ? "ring-2 ring-sky-400" : ""}
        ${isRoot ? "bg-slate-800" : ""}`}
    >
      {HANDLE_SPECS.map((h) => (
        <Handle key={h.id} id={h.id} type={h.type} position={h.position} className="!opacity-0" />
      ))}
      <div className="flex items-center justify-between gap-1">
        <span className="flex min-w-0 items-center gap-1 truncate text-[10px] font-semibold uppercase tracking-wide"
              style={{ color: n.origin === "fallback_grouping" ? "#a1a1aa" : color }}>
          {RoleIcon && <RoleIcon className="h-3 w-3 shrink-0" />}
          <span className="truncate">{roleLabel}</span>
        </span>
        <div className="flex shrink-0 items-center gap-1">
          {/* A collapsed branch's own card is the only thing still on screen
             for everything it hides, so it carries a count and an unresolved
             hint (section 3: "show counts... so collapsed content is
             discoverable") rather than reading as a dead end. */}
          {isBranch && n.collapsed && stats && stats.total > 0 && (
            <span
              title={stats.open > 0 ? `${stats.total} item(s), ${stats.open} still open`
                                    : `${stats.total} item(s)`}
              className={`rounded-full px-1.5 py-0.5 text-[9px] font-semibold ${
                stats.open > 0 ? "bg-amber-400/20 text-amber-300" : "bg-slate-800 text-slate-400"}`}
            >
              {stats.total}{stats.open > 0 ? ` · ${stats.open} open` : ""}
            </span>
          )}
          {/* Two different facts, two different symbols (rule 137): a pencil
             is "a person corrected this wording"; a pin is "a person fixed
             this CARD'S POSITION" -- conflating them under one Pin icon
             meant a reader could not tell which was true. */}
          {n.human_edited && <Pencil className="h-3 w-3 text-emerald-400" aria-label="wording corrected by hand" />}
          {n.pinned && <Pin className="h-3 w-3 text-amber-400" aria-label="position pinned by hand" />}
        </div>
      </div>
      {/* No `line-clamp` here: the label is already a bounded DISPLAY
         truncation (`_short_label`, now a full sentence's worth of
         characters, rule 135) and the card's own height was sized for
         exactly this text by the same server pass -- clamping again on top
         of a server truncation is the double-truncation this rule fixes. */}
      <p className="mt-0.5 whitespace-pre-wrap text-[15px] leading-snug text-slate-100">{n.label}</p>
      {n.status_label && (
        <p className="mt-1 truncate text-[10px] text-slate-400">{n.status_label}</p>
      )}
      {/* A collapsed branch shows what is actually inside it, not just how
         many (section 5A: "Empty folder cards plus item counts are
         insufficient"). */}
      {isBranch && n.collapsed && stats && stats.sample.length > 0 && (
        <ul className="mt-1 space-y-0.5 border-t border-slate-800 pt-1">
          {stats.sample.map((s, i) => (
            <li key={i} className="truncate text-[11px] text-slate-500">• {s}</li>
          ))}
        </ul>
      )}
      {/* Focus view only: per-relation-type counts with their own local
         expand control (rule 137) -- "2 more reasons", "3 open concerns",
         never one opaque combined "+N". Stops the click from also
         selecting/dragging the card underneath it. */}
      {groups && groups.length > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-1 border-t border-slate-800 pt-1.5">
          {groups.map((g) => {
            const meta = GROUP_META[g.kind];
            const hidden = g.totalCount - g.shownCount;
            if (hidden <= 0) return null;
            const label = g.shownCount > 0
              ? `${hidden} more ${hidden === 1 ? meta.label : meta.plural}`
              : `${g.totalCount} ${g.totalCount === 1 ? meta.label : meta.plural}`;
            return (
              <button
                key={g.kind}
                onClick={(e) => { e.stopPropagation(); onToggleGroup?.(`${n.id}::${g.kind}`); }}
                className="rounded-full bg-slate-800 px-1.5 py-0.5 text-[9px] font-medium text-slate-300 hover:bg-slate-700 hover:text-amber-200"
              >
                {label}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

/** Which handle pair to route an edge through, based on where the two real
 *  endpoints actually sit -- never top/bottom by default (rule 137). Boxes
 *  are `{x, y, w, h}` in the SAME coordinate space (either the topic map's
 *  own layout, or one Focus neighbourhood's local layout; never mixed). */
function pickHandles(from: { x: number; y: number; w: number; h: number },
                     to: { x: number; y: number; w: number; h: number }
                     ): { sourceHandle: string; targetHandle: string } {
  const fx = from.x + from.w / 2, fy = from.y + from.h / 2;
  const tx = to.x + to.w / 2, ty = to.y + to.h / 2;
  const dx = tx - fx, dy = ty - fy;
  if (Math.abs(dy) >= Math.abs(dx)) {
    return dy < 0
      ? { sourceHandle: "top-source", targetHandle: "bottom-target" }
      : { sourceHandle: "bottom-source", targetHandle: "top-target" };
  }
  return dx > 0
    ? { sourceHandle: "right-source", targetHandle: "left-target" }
    : { sourceHandle: "left-source", targetHandle: "right-target" };
}

const NODE_TYPES = { concept: ConceptNode };

function toFlowNodes(graph: ConceptGraphT, hidden: Set<string>, selectedId: string | null,
                     matches: Set<string>, capabilities: ConsultationCapabilities,
                     stats: Map<string, BranchStats>): Node<FlowNodeData, "concept">[] {
  return graph.nodes.filter((n) => !hidden.has(n.id)).map((n) => ({
    id: n.id,
    type: "concept",
    position: { x: n.x, y: n.y },
    data: {
      graphNode: n, dimmed: matches.size > 0 && !matches.has(n.id), matched: matches.has(n.id),
      // A node's kind colour used to appear only in the legend and the
      // minimap, never on the box itself — every card looked the same shade
      // of slate regardless of whether it was a concern, an idea or a
      // decision (section 6: "restrained colours actually applied to
      // nodes"). A fallback-grouping bucket keeps its own neutral grey so it
      // still reads as scaffolding, not something the group said.
      color: n.origin === "fallback_grouping" ? "#71717a" : roleColor(n.kind, capabilities),
      stats: stats.get(n.id),
      role: roleOf(n.kind, capabilities),
      roleLabel: roleLabelOf(roleOf(n.kind, capabilities), n.kind, capabilities),
    },
    selected: n.id === selectedId,
    draggable: n.kind !== "root",
  }));
}

function toFlowEdges(graph: ConceptGraphT, hidden: Set<string>, capabilities: ConsultationCapabilities,
                     focusId: string | null): Edge<FlowEdgeData>[] {
  const byId = new Map(graph.nodes.map((n) => [n.id, n]));
  return graph.edges
    .filter((e) => !hidden.has(e.from_id) && !hidden.has(e.to_id))
    // Do not draw every cross-branch edge at once (section 5B): with
    // nothing selected, a cross-link that touches neither side of the
    // current selection is dropped entirely rather than merely dimmed --
    // "many long connections, scattered cards" was largely this, at 15%
    // opacity, still fully present and still crossing the whole canvas.
    .filter((e) => e.kind !== "cross" || focusId == null || e.from_id === focusId || e.to_id === focusId)
    .map((e) => {
      const style = relationStyle(e.relation, capabilities);
      const touchesFocus = focusId != null && (e.from_id === focusId || e.to_id === focusId);
      const colour = e.kind === "hierarchy" ? "#475569" : "#eab308";
      const from = byId.get(e.from_id);
      const to = byId.get(e.to_id);
      const handles = (e.kind === "cross" && from && to)
        ? pickHandles({ x: from.x, y: from.y, w: from.width ?? 240, h: from.height ?? 72 },
                      { x: to.x, y: to.y, w: to.width ?? 240, h: to.height ?? 72 })
        : null;
      return {
        id: e.id,
        source: e.from_id,
        target: e.to_id,
        ...(handles ?? {}),
        type: e.kind === "hierarchy" ? "smoothstep" : "straight",
        animated: false,
        label: e.kind === "cross" ? (e.label || style.label) : undefined,
        labelStyle: { fill: "#cbd5e1", fontSize: 10 },
        labelBgStyle: { fill: "#0f172a", fillOpacity: 0.8 },
        style: {
          stroke: colour,
          strokeWidth: e.kind === "hierarchy" ? 1.5 : touchesFocus ? 2.5 : 1.5,
          strokeDasharray: style.dash,
          opacity: 0.9,
        },
        // A cross-link has no shape to read direction from the way a
        // hierarchy edge's tree layout already implies it — "depends_on" and
        // "leads_to" point somewhere specific, and nothing on screen used to
        // say which way (section 6: "visible direction on directional
        // relationships").
        markerEnd: e.kind === "cross"
          ? { type: MarkerType.ArrowClosed, color: colour, width: 14, height: 14 } : undefined,
        data: { relation: e.relation, dimmed: false },
      };
    });
}

// Focus's own reading-role vocabulary is WIDER than the six kind-roles
// (`dependency`, `adjustment` have no equivalent in `ROLE_COLOR`/`ROLE_ICON`,
// which describe a node's KIND, not its position in one neighbourhood) --
// its own small, complete table rather than overloading the kind-role one.
const FOCUS_ROLE_META: Record<FocusView["cards"][number]["role"],
  { color: string; icon: typeof HelpCircle; label: string }> = {
  focus: { color: ROLE_COLOR.question, icon: HelpCircle, label: "Question" },
  proposal: { color: ROLE_COLOR.proposal, icon: Lightbulb, label: "Proposed answer" },
  concern: { color: ROLE_COLOR.concern, icon: AlertTriangle, label: "Concern" },
  reason: { color: ROLE_COLOR.reason, icon: CheckCircle2, label: "Reason" },
  question: { color: ROLE_COLOR.question, icon: HelpCircle, label: "Question to investigate" },
  dependency: { color: "#a78bfa", icon: Link2, label: "Depends on" },
  adjustment: { color: "#22d3ee", icon: Compass, label: "Adjustment" },
};

function toFocusFlowNodes(focus: FocusView, selectedId: string | null, matches: Set<string>,
                          onToggleGroup: (key: string) => void): Node<FlowNodeData, "concept">[] {
  return focus.cards.map((c) => {
    const meta = FOCUS_ROLE_META[c.role];
    return {
      id: c.id,
      type: "concept",
      position: { x: c.x, y: c.y },
      data: {
        graphNode: c.node,
        dimmed: matches.size > 0 && !matches.has(c.node.id),
        matched: matches.has(c.node.id),
        color: meta.color,
        role: null,
        icon: meta.icon,
        roleLabel: meta.label,
        groups: c.groups,
        onToggleGroup,
      },
      selected: c.node.id === selectedId,
      // Never draggable: this layout is computed fresh every render and never
      // persisted (unlike the topic map's `graph_node_view`), so a drag here
      // would have nowhere real to go and no way to be remembered.
      draggable: false,
    };
  });
}

function toFocusFlowEdges(focus: FocusView, capabilities: ConsultationCapabilities): Edge<FlowEdgeData>[] {
  const byId = new Map(focus.cards.map((c) => [c.id, c]));
  return focus.edges.map((e) => {
    const style = relationStyle(e.relation, capabilities);
    const colour = e.kind === "hierarchy" ? "#475569" : "#eab308";
    const from = byId.get(e.from_id);
    const to = byId.get(e.to_id);
    const handles = (from && to) ? pickHandles(from, to) : null;
    return {
      id: e.id,
      source: e.from_id,
      target: e.to_id,
      ...(handles ?? {}),
      type: "straight",
      label: e.label || style.label,
      labelStyle: { fill: "#cbd5e1", fontSize: 10 },
      labelBgStyle: { fill: "#0f172a", fillOpacity: 0.8 },
      style: { stroke: colour, strokeWidth: 2, strokeDasharray: style.dash, opacity: 0.9 },
      markerEnd: { type: MarkerType.ArrowClosed, color: colour, width: 14, height: 14 },
      data: { relation: e.relation, dimmed: false },
    };
  });
}

// ── The outline: a compact list/outline alternative (narrow screens, and
//    accessibility) — the same walk as the server's export, done client-side
//    so it always matches whatever collapse state is on screen right now. ──

function Outline({ graph, onSelect, selectedId }: {
  graph: ConceptGraphT; onSelect: (id: string) => void; selectedId: string | null;
}) {
  const byId = useMemo(() => new Map(graph.nodes.map((n) => [n.id, n])), [graph.nodes]);
  const children = useMemo(() => {
    const m = new Map<string, string[]>();
    for (const e of graph.edges) {
      if (e.relation !== "contains") continue;
      const list = m.get(e.from_id) ?? [];
      list.push(e.to_id);
      m.set(e.from_id, list);
    }
    return m;
  }, [graph.edges]);

  function render(id: string, seen: Set<string>): React.ReactNode {
    if (seen.has(id) || !byId.has(id)) return null;
    const node = byId.get(id)!;
    const kids = (children.get(id) ?? []).filter((c) => !seen.has(c));
    return (
      <li key={id}>
        <button
          onClick={() => onSelect(id)}
          className={`rounded px-1.5 py-0.5 text-left text-sm hover:bg-slate-800 ${
            id === selectedId ? "bg-slate-800 text-amber-200" : "text-slate-200"}`}
        >
          {node.label}
          {node.status_label && <span className="ml-1.5 text-xs text-slate-500">({node.status_label})</span>}
        </button>
        {kids.length > 0 && (
          <ul className="ml-4 border-l border-slate-800 pl-2">
            {kids.map((c) => render(c, new Set([...seen, id])))}
          </ul>
        )}
      </li>
    );
  }

  return <ul className="space-y-0.5 text-sm">{render("root", new Set())}</ul>;
}

// ── The detail panel ─────────────────────────────────────────────────────

function NodeDetail({
  node, graph, capabilities, sessionId, readOnly, decisions, actions, onClose, onChanged,
  onShowSource, onSelect, onFocusBranch, onFocusQuestion,
}: {
  node: GraphNode;
  graph: ConceptGraphT;
  capabilities: ConsultationCapabilities;
  sessionId: string;
  readOnly: boolean;
  decisions: ConsultationDecision[];
  actions: ConsultationAction[];
  onClose: () => void;
  onChanged: () => void;
  onShowSource: (ids: string[]) => void;
  onSelect: (id: string) => void;
  onFocusBranch?: (id: string) => void;
  /** Re-center the Focus view on THIS node, when it is itself a question
   *  (root, or a `question`/`investigate` item) -- non-root Focus navigation
   *  (rule 137: "Provide Focus on this question... breadcrumbs, and a clear
   *  return to the main question"). */
  onFocusQuestion?: (id: string) => void;
}) {
  const byId = useMemo(() => new Map(graph.nodes.map((n) => [n.id, n])), [graph.nodes]);
  const [draft, setDraft] = useState(node.detail);
  const [busy, setBusy] = useState(false);
  const [connectTo, setConnectTo] = useState("");
  const [connectRelation, setConnectRelation] = useState<GraphRelation>("related_to");
  const [mergeWith, setMergeWith] = useState("");
  const [editingEdgeId, setEditingEdgeId] = useState<string | null>(null);
  const [editRelation, setEditRelation] = useState<GraphRelation>("related_to");
  const [editLabel, setEditLabel] = useState("");

  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set());

  useEffect(() => setDraft(node.detail), [node.id, node.detail]);
  useEffect(() => { setEditingEdgeId(null); setExpandedGroups(new Set()); }, [node.id]);

  const outgoing = graph.edges.filter((e) => e.from_id === node.id);
  const incoming = graph.edges.filter((e) => e.to_id === node.id);
  // `record_ref.id` is always the canonical row's OWN id now (never the
  // working-map id) — matched on `.id` first. `.map_id` stays as a fallback
  // for any stale cached graph payload still carrying the older shape.
  const decisionRow = node.kind === "decision" && node.record_ref
    ? decisions.find((d) => d.id === node.record_ref!.id || d.map_id === node.record_ref!.id)
    : undefined;
  const actionRow = node.kind === "action" && node.record_ref
    ? actions.find((a) => a.id === node.record_ref!.id || a.map_id === node.record_ref!.id)
    : undefined;

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    try { await fn(); onChanged(); } finally { setBusy(false); }
  };

  const canEditText = !readOnly && node.record_ref && node.kind !== "decision" && node.kind !== "action";
  const relationOptions = capabilities.edge_relations.filter(
    (r) => r.id !== "contains" || node.kind === "theme");
  const otherNodes = graph.nodes.filter((n) => n.id !== node.id && n.kind !== "root");
  // Merging is a working-map operation (`merge_map_items` combines two map
  // items); a canonical-only node — a human-created action, or one whose map
  // item was stripped by transcript deletion — has none to merge, on either
  // side, so both this node and the candidate must have one.
  const sameKindNodes = node.has_map_item
    ? otherNodes.filter((n) => n.kind === node.kind && n.record_ref && n.has_map_item)
    : [];

  // Grouped by relation and capped per group (section 3: "group the root's
  // connection list by topic so it does not repeat dozens of rows") — a
  // theme with many children, or the root itself before the provisional-
  // bucket fix, could otherwise print one row per connection with nothing
  // to tell them apart at a glance.
  type Conn = { e: GraphEdge; other: string; dir: "from" | "to" };
  const allConns: Conn[] = [
    ...incoming.map((e) => ({ e, other: e.from_id, dir: "from" as const })),
    ...outgoing.map((e) => ({ e, other: e.to_id, dir: "to" as const })),
  ].filter(({ other }) => byId.has(other));
  const connGroups = useMemo(() => {
    const groups = new Map<GraphRelation, Conn[]>();
    for (const c of allConns) {
      const list = groups.get(c.e.relation) ?? [];
      list.push(c);
      groups.set(c.e.relation, list);
    }
    return groups;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [node.id, graph.edges]);
  const CONN_GROUP_LIMIT = 8;

  return (
    <Card className="flex h-full flex-col overflow-hidden">
      <div className="flex items-center justify-between gap-2 border-b border-slate-800 px-4 py-3">
        <span className="text-xs font-semibold uppercase tracking-wide text-slate-400">
          {capabilities.node_kinds.find((k) => k.id === node.kind)?.label ?? node.kind}
        </span>
        <button onClick={onClose} className="text-slate-500 hover:text-slate-200" aria-label="Close">
          <X className="h-4 w-4" />
        </button>
      </div>
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-4 py-3">
        {canEditText ? (
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={() => {
              if (draft.trim() && draft.trim() !== node.detail && node.record_ref) {
                void run(() => api.editConsultationMapItem(
                  sessionId, node.record_ref!.list, node.record_ref!.id, { text: draft.trim() }));
              }
            }}
            rows={3}
            className="w-full rounded border border-slate-700 bg-slate-950 px-2 py-1.5 text-sm text-slate-100"
          />
        ) : (
          <p className="whitespace-pre-wrap text-sm leading-relaxed text-slate-100">{node.detail}</p>
        )}

        {node.status_label && (
          <p className="text-xs text-slate-400">Status: <span className="text-slate-200">{node.status_label}</span></p>
        )}
        {(node.kind === "theme" || node.kind === "bucket") && onFocusBranch && (
          <Button variant="secondary" className="text-xs" onClick={() => onFocusBranch(node.id)}>
            <LocateFixed className="h-3.5 w-3.5" /> Focus branch
          </Button>
        )}
        {(node.kind === "root" || node.kind === "question" || node.kind === "investigate") && onFocusQuestion && (
          <Button variant="secondary" className="text-xs" onClick={() => onFocusQuestion(node.id)}>
            <Compass className="h-3.5 w-3.5" /> Focus on this question
          </Button>
        )}

        {node.kind === "decision" && decisionRow && (
          <div className="space-y-2 rounded border border-slate-800 bg-slate-950/60 p-2">
            {decisionRow.rationale && (
              <p className="text-xs text-slate-400"><em>Why:</em> {decisionRow.rationale}</p>
            )}
            {(decisionRow.retained_concerns ?? []).length > 0 && (
              <div>
                <p className="text-xs text-slate-500">Concerns carried forward:</p>
                <ul className="ml-3 list-disc text-xs text-amber-200/80">
                  {decisionRow.retained_concerns.map((c, i) => <li key={i}>{c}</li>)}
                </ul>
              </div>
            )}
            {!readOnly && decisionRow.status !== "confirmed" && (
              <div className="flex gap-2">
                <Button className="text-xs" disabled={busy}
                        onClick={() => void run(() => api.confirmConsultationDecision(sessionId, decisionRow.id))}>
                  Confirm this decision
                </Button>
                <Button variant="ghost" className="text-xs" disabled={busy}
                        onClick={() => void run(() => api.rejectConsultationDecision(sessionId, decisionRow.id))}>
                  Not a decision
                </Button>
              </div>
            )}
          </div>
        )}

        {node.kind === "action" && actionRow && (
          <div className="space-y-1.5 rounded border border-slate-800 bg-slate-950/60 p-2 text-xs text-slate-300">
            <p>Owner: {actionRow.owner ?? "not assigned"}
              {actionRow.owner && (
                actionRow.owner_accepted === true ? " · accepted"
                : actionRow.owner_accepted === false ? " · did not accept" : " · not yet accepted")}
            </p>
            {actionRow.due && <p>Due: {actionRow.due}</p>}
            {actionRow.blocker && <p className="text-amber-300/80">Blocked: {actionRow.blocker}</p>}
            {!readOnly && (
              <div className="flex gap-2 pt-1">
                <Button className="text-xs" disabled={busy}
                        onClick={() => void run(() => api.acceptConsultationAction(
                          sessionId, actionRow.id, true))}>
                  Accepted
                </Button>
                <Button variant="ghost" className="text-xs" disabled={busy}
                        onClick={() => void run(() => api.acceptConsultationAction(
                          sessionId, actionRow.id, false))}>
                  Did not accept
                </Button>
              </div>
            )}
          </div>
        )}

        {node.source_turn_ids.length > 0 && (
          <button onClick={() => onShowSource(node.source_turn_ids)}
                  className="inline-flex items-center gap-1 text-xs text-slate-500 hover:text-amber-300">
            <Quote className="h-3.5 w-3.5" /> What was actually said
          </button>
        )}

        <div>
          <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">Connections</p>
          {allConns.length === 0 ? (
            <p className="text-xs text-slate-600">No connections yet.</p>
          ) : (
            <div className="space-y-2">
              {[...connGroups.entries()].map(([relation, conns]) => {
                const expanded = expandedGroups.has(relation);
                const shown = expanded ? conns : conns.slice(0, CONN_GROUP_LIMIT);
                return (
                  <div key={relation}>
                    {connGroups.size > 1 && (
                      <p className="mb-0.5 text-[10px] uppercase tracking-wide text-slate-600">
                        {relationStyle(relation, capabilities).label} ({conns.length})
                      </p>
                    )}
                    <ul className="space-y-1">
                      {shown.map(({ e, other, dir }) => (
                        <li key={e.id} className="flex items-center justify-between gap-2 text-xs">
                          <button onClick={() => onSelect(other)}
                                  className="min-w-0 flex-1 truncate text-left text-slate-300 hover:text-amber-200">
                            {connGroups.size === 1 && relationStyle(e.relation, capabilities).label + " "}
                            {dir === "from" ? "← " : "→ "}
                            {byId.get(other)?.label}
                          </button>
                          {!readOnly && !e.synthetic && (
                            <>
                              <button
                                title="Edit this connection's relation or label"
                                onClick={() => {
                                  setEditingEdgeId(e.id);
                                  setEditRelation(e.relation);
                                  setEditLabel(e.label);
                                }}
                                className="shrink-0 text-slate-600 hover:text-amber-300"
                              >
                                <Pencil className="h-3 w-3" />
                              </button>
                              <button
                                title="Reject this connection"
                                onClick={() => void run(() => api.rejectGraphEdge(sessionId, e.id))}
                                className="shrink-0 text-slate-600 hover:text-rose-400"
                              >
                                <X className="h-3 w-3" />
                              </button>
                            </>
                          )}
                        </li>
                      ))}
                    </ul>
                    {conns.length > CONN_GROUP_LIMIT && (
                      <button
                        onClick={() => setExpandedGroups((prev) => {
                          const next = new Set(prev);
                          if (expanded) next.delete(relation); else next.add(relation);
                          return next;
                        })}
                        className="mt-0.5 flex items-center gap-1 text-[11px] text-slate-500 hover:text-amber-300"
                      >
                        {expanded
                          ? <><ChevronDown className="h-3 w-3" /> Show fewer</>
                          : <><ChevronRight className="h-3 w-3" /> Show {conns.length - CONN_GROUP_LIMIT} more</>}
                      </button>
                    )}
                  </div>
                );
              })}
            </div>
          )}
          {editingEdgeId && (
            <div className="mt-2 space-y-1.5 rounded border border-slate-800 bg-slate-950/60 p-2">
              <div className="flex flex-wrap gap-1.5">
                <select value={editRelation}
                        onChange={(e) => setEditRelation(e.target.value as GraphRelation)}
                        className="rounded border border-slate-700 bg-slate-950 px-1.5 py-1 text-xs text-slate-200">
                  {relationOptions.map((r) => <option key={r.id} value={r.id}>{r.label}</option>)}
                </select>
                <input value={editLabel} onChange={(e) => setEditLabel(e.target.value)}
                       placeholder="Optional label"
                       className="min-w-0 flex-1 rounded border border-slate-700 bg-slate-950 px-1.5 py-1 text-xs text-slate-200" />
              </div>
              <div className="flex gap-1.5">
                <Button className="text-xs" disabled={busy}
                        onClick={() => void run(() => api.editGraphEdge(sessionId, editingEdgeId, {
                          relation: editRelation, label: editLabel,
                        })).then(() => setEditingEdgeId(null))}>
                  Save
                </Button>
                <Button variant="ghost" className="text-xs" onClick={() => setEditingEdgeId(null)}>
                  Cancel
                </Button>
              </div>
            </div>
          )}
        </div>

        {!readOnly && (
          <div className="space-y-1.5 border-t border-slate-800 pt-3">
            <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
              Connect to…
            </p>
            <div className="flex flex-wrap gap-1.5">
              <select value={connectRelation}
                      onChange={(e) => setConnectRelation(e.target.value as GraphRelation)}
                      className="rounded border border-slate-700 bg-slate-950 px-1.5 py-1 text-xs text-slate-200">
                {relationOptions.map((r) => <option key={r.id} value={r.id}>{r.label}</option>)}
              </select>
              <select value={connectTo} onChange={(e) => setConnectTo(e.target.value)}
                      className="min-w-0 flex-1 rounded border border-slate-700 bg-slate-950 px-1.5 py-1 text-xs text-slate-200">
                <option value="">— pick a node —</option>
                {otherNodes.map((n) => <option key={n.id} value={n.id}>{n.label}</option>)}
              </select>
              <Button className="text-xs" disabled={!connectTo || busy}
                      onClick={() => void run(() => api.addGraphEdge(sessionId, {
                        from_id: node.id, to_id: connectTo, relation: connectRelation,
                      })).then(() => setConnectTo(""))}>
                Connect
              </Button>
            </div>
          </div>
        )}

        {!readOnly && sameKindNodes.length > 0 && (
          <div className="space-y-1.5 border-t border-slate-800 pt-3">
            <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
              Merge with another {node.kind}
            </p>
            <div className="flex flex-wrap gap-1.5">
              <select value={mergeWith} onChange={(e) => setMergeWith(e.target.value)}
                      className="min-w-0 flex-1 rounded border border-slate-700 bg-slate-950 px-1.5 py-1 text-xs text-slate-200">
                <option value="">— pick a node —</option>
                {sameKindNodes.map((n) => <option key={n.id} value={n.id}>{n.label}</option>)}
              </select>
              <Button variant="secondary" className="text-xs" disabled={!mergeWith || busy}
                      onClick={() => void run(() => api.mergeGraphNodes(sessionId, {
                        list_name: node.record_ref!.list, keep_id: node.id, remove_id: mergeWith,
                      })).then(() => setMergeWith(""))}>
                Merge
              </Button>
            </div>
            <p className="text-[11px] text-slate-600">
              Combines the two into this one. The other is removed; its provenance is kept.
            </p>
          </div>
        )}

        {!readOnly && node.record_ref && node.kind !== "decision" && node.kind !== "action"
          && node.kind !== "theme" && (
          <Button variant="ghost" className="text-xs text-rose-300" disabled={busy}
                  onClick={() => void run(() => api.deleteConsultationMapItem(
                    sessionId, node.record_ref!.list, node.record_ref!.id)).then(onClose)}>
            <Trash2 className="h-3.5 w-3.5" /> Remove — she should not have written this down
          </Button>
        )}

        <label className="flex items-center gap-2 border-t border-slate-800 pt-3 text-xs text-slate-400">
          <input
            type="checkbox"
            checked={node.pinned}
            disabled={node.kind === "root"}
            onChange={(e) => void api.setGraphNodeView(sessionId, node.id, { pinned: e.target.checked })
              .then(onChanged)}
            className="accent-amber-400"
          />
          Pin this position (kept exactly here when the map rearranges)
        </label>
      </div>
    </Card>
  );
}

// ── "Organize ideas" (section 4/rule 132) ───────────────────────────────────
//
// A separate, explicit, paid action from "Arrange map": this one can change
// WHICH topic something sits under, so it previews before it commits, rather
// than applying silently the way a drag or a click on Arrange does.

const previewInflight = new Map<string, Promise<OrganizePreview>>();

function organizeErrorMessage(err: unknown, caps: ConsultationCapabilities): string {
  const raw = err instanceof Error ? err.message : String(err);
  const status = raw.match(/^(\d{3}):/)?.[1];
  const detail = raw.replace(/^\d{3}:\s*/, "");
  const wholeMap = caps.graph_capabilities?.organize_whole_map === true;
  if (status === "404" && !wholeMap) {
    return "The Secretary API running now does not include this map organisation. It is an older process. Restart it: stop whatever is listening on port 8765, then start the “bahAI Secretary API” scheduled task (or python -m uvicorn agents.api:app --host 127.0.0.1 --port 8765). Then try Organize ideas again.";
  }
  if (status === "404") {
    return "This consultation could not be found, so nothing was organised.";
  }
  if (status === "409" && /already has a topic/i.test(detail) && !wholeMap) {
    return "This backend can only place items that have no topic yet. Restart the Secretary API to reorganise a map that already has topics, or use Arrange map to only move cards.";
  }
  return detail || raw;
}

function loadOrganizePreview(sessionId: string): Promise<OrganizePreview> {
  const existing = previewInflight.get(sessionId);
  if (existing) return existing;
  const pending = api.getPendingOrganizeGraph(sessionId).then((res) => {
    if (res.pending && (res.pending.summary?.length || res.pending.proposed_tree)) {
      return res.pending;
    }
    return api.previewOrganizeGraph(sessionId);
  }).catch(() => api.previewOrganizeGraph(sessionId));
  previewInflight.set(sessionId, pending);
  pending.finally(() => {
    window.setTimeout(() => {
      if (previewInflight.get(sessionId) === pending) previewInflight.delete(sessionId);
    }, 800);
  });
  return pending;
}

function ProposedTree({ nodes }: { nodes: OrganizeTreeNode[] }) {
  return (
    <ul className="space-y-0.5 text-sm text-slate-200">
      {nodes.map((n) => (
        <li key={n.id}>
          <span className="text-slate-100">{n.label}</span>
          {n.kind && n.kind !== "root" && (
            <span className="ml-1.5 text-[10px] uppercase tracking-wide text-slate-500">{n.kind}</span>
          )}
          {n.children && n.children.length > 0 && (
            <div className="ml-3 border-l border-slate-800 pl-2">
              <ProposedTree nodes={n.children} />
            </div>
          )}
        </li>
      ))}
    </ul>
  );
}

function OrganizePanel({ sessionId, capabilities, onClose, onChanged }: {
  sessionId: string; capabilities: ConsultationCapabilities;
  onClose: () => void; onChanged: () => void;
}) {
  const [phase, setPhase] = useState<"loading" | "preview" | "applying" | "error">("loading");
  const [preview, setPreview] = useState<OrganizePreview | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setPhase("loading");
    loadOrganizePreview(sessionId).then((p) => {
      if (cancelled) return;
      setPreview(p);
      setPhase("preview");
    }).catch((e: unknown) => {
      if (cancelled) return;
      const message = organizeErrorMessage(e, capabilities);
      setError(message);
      setPhase("error");
      recordActivity(message, `/live-consultation/sessions/${sessionId}/graph/organize/preview`);
    });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  const apply = async () => {
    setPhase("applying");
    try {
      await api.applyOrganizeGraph(sessionId);
      onChanged();
      onClose();
    } catch (e) {
      const message = organizeErrorMessage(e, capabilities);
      setError(message);
      setPhase("error");
      recordActivity(message, `/live-consultation/sessions/${sessionId}/graph/organize/apply`);
    }
  };

  const discard = async () => {
    try { await api.discardOrganizeGraph(sessionId); } finally { onClose(); }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/70 p-4">
      <Card className="flex max-h-[80vh] w-full max-w-lg flex-col overflow-hidden">
        <div className="flex items-center justify-between gap-2 border-b border-slate-800 px-4 py-3">
          <span className="flex items-center gap-1.5 text-sm font-semibold text-slate-100">
            <Sparkles className="h-4 w-4 text-amber-300" /> Organize ideas
          </span>
          <button onClick={() => void discard()} className="text-slate-500 hover:text-slate-200"
                  aria-label="Close">
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
          {phase === "loading" && (
            <p className="flex items-center gap-2 text-sm text-slate-400">
              <Loader2 className="h-4 w-4 animate-spin" /> Looking at what still needs a topic…
            </p>
          )}
          {phase === "error" && (
            <p className="text-sm text-rose-300">{error}</p>
          )}
          {(phase === "preview" || phase === "applying") && preview && (
            <>
              <p className="mb-2 text-xs text-slate-400">
                Nothing is changed yet. This is the validated proposal — not a raw model count:
              </p>
              {preview.coverage && (
                <p className="mb-2 text-[11px] text-slate-500">
                  {preview.accepted_edge_count ?? preview.coverage.edges_accepted ?? 0} connection(s) would apply
                  {preview.coverage.edges_dropped ? ` · ${preview.coverage.edges_dropped} dropped` : ""}
                  {preview.coverage.items_unplaced ? ` · ${preview.coverage.items_unplaced} still unplaced` : ""}
                  {preview.coverage.truncated ? " · proposal was truncated — run again for the rest" : ""}
                </p>
              )}
              <ul className="mb-3 space-y-1 text-sm text-slate-200">
                {preview.summary.map((line, i) => <li key={i}>• {line}</li>)}
                {preview.summary.length === 0 && (
                  <li className="text-slate-500">No confident placement was found.</li>
                )}
              </ul>
              {preview.omissions && preview.omissions.length > 0 && (
                <div className="mb-3 rounded border border-amber-900/50 bg-amber-950/30 p-2 text-[11px] text-amber-200/90">
                  {preview.omissions.map((n, i) => <p key={i}>{n}</p>)}
                </div>
              )}
              {preview.conflicts && preview.conflicts.length > 0 && (
                <div className="mb-3 rounded border border-rose-900/50 bg-rose-950/20 p-2 text-[11px] text-rose-200/90">
                  {preview.conflicts.map((n, i) => <p key={i}>{n}</p>)}
                </div>
              )}
              {preview.proposed_tree && preview.proposed_tree.length > 0 && (
                <div>
                  <p className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-slate-500">
                    Proposed map
                  </p>
                  <ProposedTree nodes={preview.proposed_tree} />
                </div>
              )}
            </>
          )}
        </div>
        {(phase === "preview" || phase === "applying") && preview && (
          <div className="flex gap-2 border-t border-slate-800 px-4 py-3">
            <Button className="text-xs" disabled={phase === "applying"
                    || ((preview.accepted_edge_count ?? preview.summary.length) === 0
                        && preview.proposed_theme_count === 0)}
                    onClick={() => void apply()}>
              {phase === "applying" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
              Apply
            </Button>
            <Button variant="ghost" className="text-xs" disabled={phase === "applying"}
                    onClick={() => void discard()}>
              Discard
            </Button>
          </div>
        )}
      </Card>
    </div>
  );
}

const MIN_READABLE_ZOOM = 0.8;

function overviewNodeIds(graph: ConceptGraphT): Set<string> {
  // The first view is the question and a handful of topic branches — never
  // every supporting card. Fitting the whole tree is what made dozens of
  // notes unreadable.
  const ids = new Set<string>(["root"]);
  const root = graph.nodes.find((n) => n.id === "root");
  const rx = root?.x ?? 0;
  const tops = graph.nodes
    .filter((n) => (n.kind === "theme" || n.kind === "bucket")
      && (n.parent_id === "root" || n.depth === 1))
    .sort((a, b) => Math.abs(a.x - rx) - Math.abs(b.x - rx));
  const themes = tops.filter((n) => n.kind === "theme").slice(0, 8);
  const buckets = tops.filter((n) => n.kind === "bucket").slice(0, 4);
  for (const n of [...themes, ...buckets].slice(0, 12)) ids.add(n.id);
  return ids;
}

function ancestorIds(graph: ConceptGraphT, id: string): string[] {
  const parent = new Map<string, string>();
  for (const e of graph.edges) {
    if (e.relation === "contains") parent.set(e.to_id, e.from_id);
  }
  const path: string[] = [];
  let cur = parent.get(id);
  const seen = new Set<string>();
  while (cur && !seen.has(cur)) {
    seen.add(cur);
    path.push(cur);
    cur = parent.get(cur);
  }
  return path;
}

const NEIGHBOUR_RELATIONS = new Set<GraphRelation>(
  ["supports", "challenges", "elaborates", "clarifies", "depends_on", "addresses"]);

/** The nearest question Focus could sensibly be re-centred on to reach `id`,
 *  or `null` when there is none (rule 137: a search/outline result outside
 *  the CURRENT focus neighbourhood is not necessarily outside every one --
 *  it may simply belong to a different question). Deliberately one hop, the
 *  same bounded reach as the backend's own `semantic_anchor` (rule 136):
 *  `id` itself if it already has proposals answering it; the question `id`
 *  directly answers; or, one hop further, the question answered by
 *  whichever proposal `id` bears on (its own reason/concern/dependency/
 *  clarifying-question/adjustment). */
function nearestFocusableQuestion(graph: ConceptGraphT, id: string): string | null {
  const isQuestionKind = graph.nodes.some((n) => n.id === id
    && (n.kind === "root" || n.kind === "question" || n.kind === "investigate"));
  if (isQuestionKind && graph.edges.some((e) => e.relation === "answers" && e.to_id === id)) {
    return id;
  }
  const answersEdge = graph.edges.find((e) => e.relation === "answers" && e.from_id === id);
  if (answersEdge) return answersEdge.to_id;
  const toProposal = graph.edges.find((e) =>
    (e.from_id === id || e.to_id === id) && NEIGHBOUR_RELATIONS.has(e.relation));
  if (toProposal) {
    const proposalId = toProposal.from_id === id ? toProposal.to_id : toProposal.from_id;
    const proposalAnswers = graph.edges.find((e) => e.relation === "answers" && e.from_id === proposalId);
    if (proposalAnswers) return proposalAnswers.to_id;
  }
  return null;
}

// ── The canvas ───────────────────────────────────────────────────────────

function Canvas({
  graph, capabilities, sessionId, title, readOnly, expanded, onToggleExpand, decisions, actions,
  onChanged, onShowSource,
}: {
  graph: ConceptGraphT;
  capabilities: ConsultationCapabilities;
  sessionId: string;
  title: string;
  readOnly: boolean;
  expanded: boolean;
  onToggleExpand: () => void;
  decisions: ConsultationDecision[];
  actions: ConsultationAction[];
  onChanged: () => void;
  onShowSource: (ids: string[]) => void;
}) {
  const rf = useReactFlow();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [narrow, setNarrow] = useState(
    typeof window !== "undefined" ? window.innerWidth < 640 : false);
  const [showOutline, setShowOutline] = useState(narrow);
  const [exportBusy, setExportBusy] = useState<string | null>(null);
  const [organizeOpen, setOrganizeOpen] = useState(false);
  // The legend/detail column is useful but not always needed — collapsing it
  // gives the canvas the full width rather than always reserving 20rem
  // (section 3: "make the detail/legend panel collapsible rather than
  // permanently reserving a wide column when it is unnecessary").
  const [showSidePanel, setShowSidePanel] = useState(false);
  // The default reading mode (section 7): a question-led neighbourhood, not
  // the whole `contains` topic tree. "focus" is the intent; whether it can
  // actually be shown depends on `focusView.found` below -- a session with no
  // `answers` edges yet degrades to the full map with an honest note rather
  // than a manufactured diagram (section 8).
  const [mode, setMode] = useState<"focus" | "full">("focus");
  // WHICH question Focus is centred on -- root by default, but any question/
  // investigate item with its own `answers` edges is reachable too (rule 137:
  // "Focus on this question... non-root Focus navigation").
  const [focusQuestionId, setFocusQuestionId] = useState("root");
  const [detailLevel, setDetailLevel] = useState<DetailLevel>("standard");
  // `${proposalId}::${groupKind}` a person explicitly asked to see more of,
  // independent of `detailLevel` (rule 137: "2 more reasons" style local
  // expansion that keeps the selected option and main question in context).
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set());
  const draggingRef = useRef(false);
  const lastSyncedRef = useRef<string>("");
  const fittedSessionRef = useRef<string>("");

  useEffect(() => {
    const onResize = () => setNarrow(window.innerWidth < 640);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  const hidden = useMemo(() => collapsedDescendants(graph), [graph]);
  const stats = useMemo(() => branchStats(graph, capabilities), [graph, capabilities]);
  const focusView = useMemo(
    () => buildFocusView(graph, focusQuestionId, { detailLevel, expandedGroups }),
    [graph, focusQuestionId, detailLevel, expandedGroups]);
  const effectiveMode: "focus" | "full" = mode === "focus" && focusView.found ? "focus" : "full";
  const otherQuestions = useMemo(
    () => focusableQuestions(graph).filter((q) => q.id !== focusQuestionId),
    [graph, focusQuestionId]);
  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return new Set<string>();
    return new Set(graph.nodes.filter((n) =>
      n.label.toLowerCase().includes(q) || n.detail.toLowerCase().includes(q)).map((n) => n.id));
  }, [graph.nodes, query]);

  const [nodes, setNodes, onNodesChange] = useNodesState<Node<FlowNodeData, "concept">>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge<FlowEdgeData>>([]);

  const onToggleGroup = useCallback((key: string) => {
    setExpandedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  }, []);

  const buildEdges = useCallback((focusId: string | null) =>
    effectiveMode === "focus"
      ? toFocusFlowEdges(focusView, capabilities)
      : toFlowEdges(graph, hidden, capabilities, focusId),
    [effectiveMode, focusView, graph, hidden, capabilities]);

  // Re-sync from the server whenever the graph actually changed, but never
  // while a drag is in progress -- and never reshuffle a node whose position
  // has not changed (section 5: "do not recenter the whole diagram").
  useEffect(() => {
    // `record_revision` moves on a decision confirmed, an action accepted, an
    // owner corrected, or a connection a human drew/edited/rejected — none of
    // which bump content/graph/view. Leaving it out of the key meant a
    // correction could sit on screen unrefreshed until something else
    // happened to move one of the other three (section 1). `effectiveMode`
    // has to be in the key too: switching Focus/Full map is not a graph
    // change, but it has to rebuild the same way one is -- and so does
    // `focusView` itself, since re-centering on a different question or
    // changing detail level changes what Focus renders without the
    // underlying graph revisions moving at all.
    const key = `${graph.content_revision}:${graph.graph_revision}:${graph.view_revision}:` +
      `${graph.record_revision}:${effectiveMode}:${focusQuestionId}:${detailLevel}:` +
      `${[...expandedGroups].sort().join(",")}`;
    if (draggingRef.current || key === lastSyncedRef.current) return;
    lastSyncedRef.current = key;
    if (effectiveMode === "focus") {
      setNodes(toFocusFlowNodes(focusView, selectedId, matches, onToggleGroup));
    } else {
      setNodes(toFlowNodes(graph, hidden, selectedId, matches, capabilities, stats));
    }
    setEdges(buildEdges(selectedId));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [graph, hidden, capabilities, stats, effectiveMode, focusView, onToggleGroup]);

  // Selection/search only ever restyle existing nodes; they never move one.
  useEffect(() => {
    setNodes((prev) => prev.map((n) => ({
      ...n, selected: n.id === selectedId,
      data: { ...n.data, dimmed: matches.size > 0 && !matches.has(n.id), matched: matches.has(n.id) },
    })));
    setEdges(buildEdges(selectedId));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId, matches]);

  const selectedNode = selectedId ? graph.nodes.find((n) => n.id === selectedId) ?? null : null;

  const fitOverview = useCallback(() => {
    // Rule 135: this used to center on root at a fixed zoom and RETURN
    // whenever root existed -- i.e. on every non-empty map -- so
    // `overviewNodeIds`'s own subset (root plus a handful of top branches)
    // was computed and then never used. A readable root alone is not an
    // overview of the discussion; it always has to fit the actual subset,
    // root included, so the group's main topics are on screen too.
    const live = rf.getNodes();
    const duration = prefersReducedMotion() ? 0 : 300;
    if (effectiveMode === "focus") {
      // Already a small, deliberate neighbourhood (rule 135) -- fit all of it.
      void rf.fitView({ nodes: live, padding: 0.25, minZoom: MIN_READABLE_ZOOM, maxZoom: 1.1, duration });
      return;
    }
    const ids = overviewNodeIds(graph);
    const subset = live.filter((n) => ids.has(n.id));
    void rf.fitView({
      nodes: subset.length ? subset : live,
      padding: 0.2,
      minZoom: MIN_READABLE_ZOOM,
      maxZoom: 1.05,
      duration,
    });
  }, [graph, rf, effectiveMode]);

  useEffect(() => {
    if (fittedSessionRef.current === sessionId) return;
    if (nodes.length <= 1) return;
    fittedSessionRef.current = sessionId;
    const id = window.requestAnimationFrame(() => {
      window.requestAnimationFrame(() => fitOverview());
    });
    return () => window.cancelAnimationFrame(id);
  }, [sessionId, nodes.length, fitOverview]);

  // Centers on whatever is actually ON SCREEN right now (`rf.getNodes()`),
  // never `graph.nodes`' own x/y -- those are the FULL topic-map coordinates,
  // which are meaningless while the focus view's own local layout is what is
  // actually rendered (section 5: layout is a presentation concern, kept
  // separate from a node's semantic identity).
  const focusNode = useCallback((id: string) => {
    setSelectedId(id);
    setShowSidePanel(true);
    const live = rf.getNodes().find((x) => x.id === id);
    if (live) rf.setCenter(
      live.position.x + (live.width ?? 200) / 2, live.position.y + (live.height ?? 64) / 2,
      { zoom: Math.max(MIN_READABLE_ZOOM, 1), duration: prefersReducedMotion() ? 0 : 400 });
  }, [rf]);

  // A search/outline/connection target may sit outside the current focus
  // neighbourhood entirely -- it still has to be reachable, so this switches
  // to the full map first and waits for that node to actually be on screen
  // before centering on it (`pendingFocusIdRef`), rather than centering on
  // stale coordinates from before the switch.
  const pendingFocusIdRef = useRef<string | null>(null);
  useEffect(() => {
    const id = pendingFocusIdRef.current;
    if (!id || !nodes.some((n) => n.id === id)) return;
    pendingFocusIdRef.current = null;
    const raf = window.requestAnimationFrame(() => focusNode(id));
    return () => window.cancelAnimationFrame(raf);
  }, [nodes, focusNode]);

  // Re-centering Focus on a different question rebuilds `nodes` on the NEXT
  // render (via the sync effect above); fitting immediately would still be
  // looking at the outgoing question's cards. This waits for that rebuild --
  // the same one-render-later pattern as `pendingFocusIdRef` just above --
  // rather than depending on `nodes.length` happening to change.
  const pendingFitRef = useRef(false);
  useEffect(() => {
    if (!pendingFitRef.current) return;
    pendingFitRef.current = false;
    const raf = window.requestAnimationFrame(() => fitOverview());
    return () => window.cancelAnimationFrame(raf);
  }, [nodes, fitOverview]);

  // A search/outline/connection target outside the CURRENT focus
  // neighbourhood is not necessarily outside every focus neighbourhood --
  // it may simply belong to a different question. Re-centering Focus on
  // that question keeps the person in a small, meaningful view instead of
  // stranding them in the full tangled map the moment they search for
  // anything not already on screen (section 4A: "Search should reveal a
  // meaningful neighbourhood around a result and preserve a way back").
  // Only falls through to Full map when no such question can be found (a
  // theme, a fact with no semantic connection, and so on).
  const revealAndFocus = useCallback(async (id: string) => {
    if (effectiveMode === "focus" && !focusView.cards.some((c) => c.id === id)) {
      const nearestQuestion = nearestFocusableQuestion(graph, id);
      if (nearestQuestion && nearestQuestion !== focusQuestionId) {
        pendingFocusIdRef.current = id;
        setFocusQuestionId(nearestQuestion);
        return;
      }
      pendingFocusIdRef.current = id;
      setMode("full");
      return;
    }
    const path = ancestorIds(graph, id);
    const collapsedPath = path.filter((pid) => graph.nodes.find((n) => n.id === pid)?.collapsed);
    for (const pid of collapsedPath) {
      await api.setGraphNodeView(sessionId, pid, { collapsed: false });
    }
    if (collapsedPath.length) onChanged();
    focusNode(id);
  }, [graph, sessionId, onChanged, focusNode, effectiveMode, focusView, focusQuestionId]);

  // "Focus on this question" / the other-questions selector (rule 137):
  // deliberately narrow -- it only ever changes WHICH question Focus reads,
  // never the mode-switch-to-full-map fallback logic above.
  const focusOnQuestion = useCallback((id: string) => {
    setFocusQuestionId(id);
    setMode("focus");
    setSelectedId(null);
    pendingFitRef.current = true;
  }, []);

  const focusBranch = useCallback((id: string) => {
    const n = graph.nodes.find((x) => x.id === id);
    if (!n) return;
    setSelectedId(id);
    const descendant = new Set<string>([id]);
    const kids = new Map<string, string[]>();
    for (const e of graph.edges) {
      if (e.relation !== "contains") continue;
      const list = kids.get(e.from_id) ?? [];
      list.push(e.to_id);
      kids.set(e.from_id, list);
    }
    const stack = [id];
    while (stack.length) {
      const cur = stack.pop()!;
      for (const c of kids.get(cur) ?? []) {
        if (descendant.has(c)) continue;
        descendant.add(c);
        stack.push(c);
      }
    }
    const subset = nodes.filter((fn) => descendant.has(fn.id) && !hidden.has(fn.id));
    void rf.fitView({
      nodes: subset.length ? subset : nodes.filter((fn) => fn.id === id),
      padding: 0.3,
      minZoom: MIN_READABLE_ZOOM,
      maxZoom: 1.15,
      duration: prefersReducedMotion() ? 0 : 300,
    });
  }, [graph, nodes, hidden, rf]);

  const arrange = useMutationRunner(() => api.arrangeConsultationGraph(sessionId), onChanged);

  const doExport = async (kind: "svg" | "png" | "html") => {
    setExportBusy(kind);
    try {
      if (kind === "svg") await api.downloadGraphSvg(sessionId, title);
      else if (kind === "png") await api.downloadGraphPng(sessionId, title);
      else await api.downloadGraphHtml(sessionId, title);
    } finally {
      setExportBusy(null);
    }
  };

  const rootNode = graph.nodes.find((n) => n.id === "root");
  // The question stays fully visible always; a long objective collapses by
  // default so the orientation area does not itself become the "tall header"
  // the detail-and-overview brief named (section 7: "Keep the full question
  // readable in a compact orientation area, with the objective expandable
  // when long"). A short objective just shows -- collapsing two sentences
  // would cost a click to save no real space.
  const OBJECTIVE_INLINE_LIMIT = 90;
  const [rootQuestionText, objectiveText] = rootNode
    ? (() => {
        const marker = "\n\nObjective: ";
        const idx = rootNode.detail.indexOf(marker);
        return idx === -1
          ? [rootNode.detail, ""]
          : [rootNode.detail.slice(0, idx), rootNode.detail.slice(idx + marker.length)];
      })()
    : ["", ""];

  return (
    <div className={`flex flex-col gap-2 ${expanded ? "fixed inset-0 z-40 bg-slate-950 p-4" : "h-full"}`}>
      {organizeOpen && (
        <OrganizePanel sessionId={sessionId} capabilities={capabilities}
                       onClose={() => setOrganizeOpen(false)}
                       onChanged={() => { fittedSessionRef.current = ""; onChanged(); }} />
      )}
      {/* Persistent orientation (section 7): the MAIN question and its
         purpose sit OUTSIDE the zooming canvas, in full, always -- so
         panning, zooming or re-centering Focus on a sub-question never
         hides what the group is actually investigating overall. */}
      {rootNode && (
        <div className="rounded-lg border border-slate-800 bg-slate-900/60 px-3 py-2">
          <p className="text-[10px] font-semibold uppercase tracking-wide text-slate-500">
            The question
          </p>
          <p className="whitespace-pre-wrap text-sm leading-relaxed text-slate-100">{rootQuestionText}</p>
          {objectiveText && (
            objectiveText.length > OBJECTIVE_INLINE_LIMIT ? (
              <details className="mt-1">
                <summary className="cursor-pointer text-xs text-slate-500 hover:text-slate-300">
                  Objective
                </summary>
                <p className="mt-0.5 whitespace-pre-wrap text-xs leading-relaxed text-slate-400">{objectiveText}</p>
              </details>
            ) : (
              <p className="mt-1 text-xs text-slate-400">Objective: {objectiveText}</p>
            )
          )}
          {/* Breadcrumb (rule 137: "breadcrumbs, and a clear return to the
             main question") -- only appears once Focus is actually centred
             on something other than the main question. */}
          {effectiveMode === "focus" && focusQuestionId !== "root" && focusView.focusNode && (
            <div className="mt-1.5 flex items-center gap-1.5 border-t border-slate-800 pt-1.5 text-xs text-amber-200/90">
              <button onClick={() => focusOnQuestion("root")}
                      className="flex items-center gap-1 text-slate-400 hover:text-amber-200">
                <ArrowLeft className="h-3 w-3" /> Main question
              </button>
              <span className="text-slate-600">/</span>
              <span className="truncate">Now focused on: {focusView.focusNode.label}</span>
            </div>
          )}
        </div>
      )}
      {/* Other focusable questions (rule 137: non-root Focus navigation) --
         reachable without detouring through Full map. */}
      {effectiveMode === "focus" && otherQuestions.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5 text-xs text-slate-500">
          <Compass className="h-3.5 w-3.5 shrink-0" />
          <span>Other questions:</span>
          {otherQuestions.slice(0, 6).map((q) => (
            <button key={q.id} onClick={() => focusOnQuestion(q.id)}
                    className="rounded-full border border-slate-800 bg-slate-900/60 px-2 py-0.5 text-slate-300 hover:border-amber-400/50 hover:text-amber-200">
              {q.label} <span className="text-slate-600">({q.proposalCount})</span>
            </button>
          ))}
        </div>
      )}
      {mode === "focus" && !focusView.found && (
        <div className="rounded-lg border border-slate-700 bg-slate-900/60 px-3 py-1.5 text-xs text-slate-400">
          No proposed answer is connected to {focusQuestionId === "root" ? "the question" : "this question"} yet,
          so there is nothing to focus on — showing the full map instead. Once a proposal is linked
          with “answers”, it will appear here as its own reading view.
        </div>
      )}
      {effectiveMode === "focus" && focusView.shownProposalCount < focusView.totalProposalCount && (
        <div className="rounded-lg border border-slate-800 bg-slate-900/40 px-3 py-1.5 text-xs text-slate-500">
          Showing {focusView.shownProposalCount} of {focusView.totalProposalCount} proposed answers —
          open Full map to see the rest.
        </div>
      )}
      {graph.fallback && (
        <div className="rounded-lg border border-slate-700 bg-slate-900/60 px-3 py-1.5 text-xs text-slate-400">
          No themes or connections have been drawn yet — grouped automatically by category
          for now.
        </div>
      )}
      {/* Pin conflicts and topic coverage are both properties of the FULL
         topic-tree layout -- Focus positions are computed fresh every render
         and never pinned, so these would be reporting on a layout that is
         not even the one on screen (rule 137). */}
      {effectiveMode === "full" && graph.pin_conflicts && graph.pin_conflicts.length > 0 && (
        <div className="rounded-lg border border-amber-900/60 bg-amber-950/40 px-3 py-1.5 text-xs text-amber-200">
          {graph.pin_conflicts.length} pinned card{graph.pin_conflicts.length === 1 ? "" : "s"} overlap.
          Unpin one, or drag it, then Arrange map — pins are never moved for you.
        </div>
      )}
      {/* Coverage (section 5C): three honest, distinct counts rather than
         one "unplaced" number that used to conflate "no topic, but
         genuinely connected to something" with "no connection at all". */}
      {effectiveMode === "full" && !graph.fallback
        && (graph.unplaced_count > 0 || (graph.connected_no_topic_count ?? 0) > 0) && (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border
                        border-slate-800 bg-slate-900/40 px-3 py-1.5 text-xs text-slate-500">
          <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
            {(graph.connected_no_topic_count ?? 0) > 0 && (
              <span className="flex items-center gap-1" title="Shown under the topic they connect to, but nobody has explicitly grouped them there">
                <Link2 className="h-3 w-3 text-sky-400" />
                {graph.connected_no_topic_count} connected, not explicitly grouped
              </span>
            )}
            {graph.unplaced_count > 0 && (
              <span>
                {graph.unplaced_count} item{graph.unplaced_count === 1 ? "" : "s"} {
                  graph.unplaced_count === 1 ? "has" : "have"} no topic and no connection yet
              </span>
            )}
          </span>
          {!readOnly && (
            <button onClick={() => setOrganizeOpen(true)}
                    className="font-medium text-amber-300/80 hover:text-amber-200">
              Organize ideas
            </button>
          )}
        </div>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-[10rem] flex-1">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-600" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key !== "Enter") return;
              const first = [...matches][0];
              if (first) void revealAndFocus(first);
            }}
            placeholder="Search the map…"
            aria-label="Search the concept map"
            className="w-full rounded-lg border border-slate-800 bg-slate-950 py-1.5 pl-8 pr-2 text-xs text-slate-100 placeholder:text-slate-600 focus:border-amber-400/50 focus:outline-none"
          />
        </div>
        <Button variant="secondary" className="text-xs" onClick={() => setShowOutline((v) => !v)}
                title="Compact outline view">
          {showOutline ? <Network className="h-3.5 w-3.5" /> : <List className="h-3.5 w-3.5" />}
          {showOutline ? "Show map" : "Show outline"}
        </Button>
        {!showOutline && (
          <>
            {/* The default reading mode (section 7): the question and its
               proposed answers, not the whole topic tree -- a toggle rather
               than a replacement, since the full connected map (rules
               121-132) is still what "Organize ideas" and dragging/pinning
               act on. */}
            <div className="flex items-center gap-0.5 rounded-lg border border-slate-800 bg-slate-950 p-0.5">
              <button
                onClick={() => { setMode("focus"); setSelectedId(null); }}
                title="The question and its proposed answers, with their reasons and concerns"
                className={`rounded px-2 py-1 text-xs font-medium transition-colors ${
                  mode === "focus" ? "bg-slate-800 text-amber-200" : "text-slate-400 hover:text-slate-200"}`}
              >
                Focus
              </button>
              <button
                onClick={() => { setMode("full"); setSelectedId(null); }}
                title="Everything on the map, organised by topic"
                className={`rounded px-2 py-1 text-xs font-medium transition-colors ${
                  mode === "full" ? "bg-slate-800 text-amber-200" : "text-slate-400 hover:text-slate-200"}`}
              >
                Full map
              </button>
            </div>
            {/* How much supporting detail Focus shows per proposal by
               default (rule 137, section 4A) -- an accessible, explicit
               control rather than shrinking text to force more in. Never
               changes what is COUNTED, only what starts expanded; anything
               hidden is still reachable via its own "N more" chip. */}
            {effectiveMode === "focus" && (
              <div className="flex items-center gap-0.5 rounded-lg border border-slate-800 bg-slate-950 p-0.5">
                {(["brief", "standard", "detailed"] as DetailLevel[]).map((level) => (
                  <button
                    key={level}
                    onClick={() => setDetailLevel(level)}
                    title={level === "brief" ? "Just the proposals, with counts"
                          : level === "standard" ? "The most useful reasoning for each"
                          : "As much supporting detail as fits"}
                    className={`rounded px-2 py-1 text-xs font-medium capitalize transition-colors ${
                      detailLevel === level ? "bg-slate-800 text-amber-200" : "text-slate-400 hover:text-slate-200"}`}
                  >
                    {level}
                  </button>
                ))}
              </div>
            )}
            <Button variant="secondary" className="text-xs" onClick={fitOverview}
                    title="Fit the overview at a readable size — never shrink cards to fit every note">
              <Scan className="h-3.5 w-3.5" /> Fit overview
            </Button>
            {effectiveMode === "full" && (
              <Button variant="secondary" className="text-xs" disabled={arrange.busy}
                      onClick={() => {
                        fittedSessionRef.current = "";
                        void arrange.run();
                      }}
                      title="A deterministic, free reflow -- never an AI call">
                <LayoutGrid className="h-3.5 w-3.5" /> Arrange map
              </Button>
            )}
          </>
        )}
        {!readOnly && (
          <Button variant="secondary" className="text-xs" onClick={() => setOrganizeOpen(true)}
                  title="Propose topics and connections for unplaced items -- previews before it commits">
            <Sparkles className="h-3.5 w-3.5" /> Organize ideas
          </Button>
        )}
        <div className="flex items-center gap-1 rounded-lg border border-slate-800 bg-slate-950 px-1 py-1">
          <button title="Download SVG" disabled={!!exportBusy} onClick={() => void doExport("svg")}
                  className="rounded p-1 text-slate-400 hover:bg-slate-800 hover:text-slate-200">
            {exportBusy === "svg" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Download className="h-3.5 w-3.5" />}
          </button>
          <button title="Download PNG" disabled={!!exportBusy} onClick={() => void doExport("png")}
                  className="rounded p-1 text-slate-400 hover:bg-slate-800 hover:text-slate-200">
            {exportBusy === "png" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ImageIcon className="h-3.5 w-3.5" />}
          </button>
          <button title="Download self-contained HTML report" disabled={!!exportBusy}
                  onClick={() => void doExport("html")}
                  className="rounded p-1 text-slate-400 hover:bg-slate-800 hover:text-slate-200">
            {exportBusy === "html" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <FileCode className="h-3.5 w-3.5" />}
          </button>
        </div>
        <Button variant="secondary" className="text-xs" onClick={onToggleExpand}
                title={expanded ? "Exit expanded view" : "Expand — for a laptop or projected display"}>
          {expanded ? <Minimize2 className="h-3.5 w-3.5" /> : <Maximize2 className="h-3.5 w-3.5" />}
        </Button>
        <Button variant="secondary" className="text-xs" onClick={() => setShowSidePanel((v) => !v)}
                title={showSidePanel ? "Hide the detail panel" : "Show the detail panel"}>
          {showSidePanel ? <PanelRightClose className="h-3.5 w-3.5" /> : <PanelRightOpen className="h-3.5 w-3.5" />}
        </Button>
      </div>

      <div className={`grid min-h-0 flex-1 gap-3 ${showSidePanel ? "lg:grid-cols-[1fr_20rem]" : "grid-cols-1"}`}>
        <div className="min-h-[22rem] overflow-hidden rounded-xl border border-slate-800 bg-slate-950">
          {showOutline ? (
            <div className="h-full overflow-y-auto p-3">
              {graph.nodes.length <= 1 ? (
                <EmptyState />
              ) : (
                <Outline graph={graph} onSelect={(id) => void revealAndFocus(id)} selectedId={selectedId} />
              )}
            </div>
          ) : graph.nodes.length <= 1 ? (
            <EmptyState />
          ) : (
            <ReactFlow
              nodes={nodes}
              edges={edges}
              onNodesChange={onNodesChange}
              onEdgesChange={onEdgesChange}
              nodeTypes={NODE_TYPES}
              onInit={() => {
                if (!rf.getNodes().some((n) => n.id === "root")) return;
                if (fittedSessionRef.current === sessionId) return;
                fittedSessionRef.current = sessionId;
                fitOverview();
              }}
              onNodeClick={(_, n) => { setSelectedId(n.id); setShowSidePanel(true); }}
              onPaneClick={() => setSelectedId(null)}
              onNodeDragStart={() => { draggingRef.current = true; }}
              onNodeDragStop={(_, n) => {
                draggingRef.current = false;
                // Focus view already refuses to drag (`nodesDraggable` below);
                // this guard is a second line of defence against ever
                // persisting a FOCUS-computed position into the topic map's
                // own `graph_node_view` (rule 124).
                if (!readOnly && effectiveMode === "full") void api.setGraphNodeView(
                  sessionId, n.id, { x: n.position.x, y: n.position.y, pinned: true });
              }}
              onNodeDoubleClick={(_, n) => {
                const gn = graph.nodes.find((x) => x.id === n.id);
                if (gn && (gn.kind === "theme" || gn.kind === "bucket")) {
                  void api.setGraphNodeView(sessionId, n.id, { collapsed: !gn.collapsed })
                    .then(onChanged);
                }
              }}
              nodesDraggable={!readOnly && effectiveMode === "full"}
              nodesConnectable={false}
              proOptions={{ hideAttribution: true }}
              minZoom={0.4}
              maxZoom={1.6}
            >
              <Background gap={20} color="#1e293b" />
              <Controls showInteractive={false} />
              <MiniMap
                pannable zoomable
                nodeColor={(n) => roleColor((n.data as unknown as FlowNodeData).graphNode.kind, capabilities)}
                maskColor="rgba(2,6,23,0.75)"
                style={{ backgroundColor: "#0f172a" }}
              />
            </ReactFlow>
          )}
        </div>

        {showSidePanel && (
          <div className="min-h-0">
            {selectedNode ? (
              <NodeDetail
                node={selectedNode}
                graph={graph}
                capabilities={capabilities}
                sessionId={sessionId}
                readOnly={readOnly}
                decisions={decisions}
                actions={actions}
                onClose={() => setSelectedId(null)}
                onChanged={onChanged}
                onShowSource={onShowSource}
                onSelect={(id) => void revealAndFocus(id)}
                onFocusBranch={focusBranch}
                onFocusQuestion={focusOnQuestion}
              />
            ) : (
              <Legend capabilities={capabilities} graph={graph} />
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function EmptyState() {
  return (
    <div className="flex h-full min-h-[18rem] flex-col items-center justify-center gap-2 text-center">
      <Network className="h-8 w-8 text-slate-700" />
      <p className="text-sm text-slate-500">
        Nothing to map yet. The map fills in as the discussion develops.
      </p>
    </div>
  );
}

function Legend({ capabilities, graph }: { capabilities: ConsultationCapabilities; graph: ConceptGraphT }) {
  const usedKinds = new Set(graph.nodes.map((n) => n.kind));
  const usedRoles = new Set(
    [...usedKinds].map((k) => roleOf(k, capabilities)).filter((r): r is GraphRole => r != null));
  return (
    <Card className="h-full overflow-y-auto p-4">
      {/* The role legend is primary (section 5: "de-emphasize internal
         taxonomy labels") -- what a card IS ARGUING, before which of fifteen
         specific kinds it happens to be. */}
      <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
        What the map shows
      </p>
      <ul className="space-y-1.5">
        {(capabilities.node_roles ?? []).filter((r) => usedRoles.has(r.id)).map((r) => {
          const RoleIcon = ROLE_ICON[r.id];
          return (
            <li key={r.id} className="flex items-center gap-2 text-xs text-slate-300">
              <RoleIcon className="h-3.5 w-3.5 shrink-0" style={{ color: ROLE_COLOR[r.id] }} />
              {r.plural}
            </li>
          );
        })}
      </ul>
      <details className="mt-3">
        <summary className="cursor-pointer text-[11px] text-slate-600 hover:text-slate-400">
          Specific kinds ({usedKinds.size})
        </summary>
        <ul className="mt-1.5 space-y-1.5">
          {capabilities.node_kinds.filter((k) => usedKinds.has(k.id)).map((k) => (
            <li key={k.id} className="flex items-center gap-2 text-xs text-slate-400">
              <span className="h-2.5 w-2.5 shrink-0 rounded-sm" style={{ backgroundColor: k.color }} />
              {k.plural}
            </li>
          ))}
        </ul>
      </details>
      <p className="mb-2 mt-4 text-xs font-semibold uppercase tracking-wide text-slate-500">
        Connections
      </p>
      <ul className="space-y-1.5">
        {capabilities.edge_relations.map((r) => (
          <li key={r.id} className="flex items-center gap-2 text-xs text-slate-400">
            <span className="inline-block h-0 w-4 border-t-2"
                  style={{ borderColor: r.hierarchy ? "#475569" : "#eab308",
                          borderStyle: r.style === "dashed" ? "dashed" : r.style === "dotted" ? "dotted" : "solid" }} />
            {r.label}
          </li>
        ))}
      </ul>
      <p className="mt-4 text-[11px] leading-relaxed text-slate-600">
        Click a node for its full wording and connections. Drag to move; double-click a theme
        to collapse its branch. A pin (
        <Pin className="inline h-3 w-3 align-text-bottom" />) means it was corrected by hand.
      </p>
    </Card>
  );
}

function useMutationRunner(fn: () => Promise<unknown>, onDone: () => void) {
  const [busy, setBusy] = useState(false);
  const run = useCallback(async () => {
    setBusy(true);
    try { await fn(); onDone(); } finally { setBusy(false); }
  }, [fn, onDone]);
  return { busy, run };
}

export function ConceptGraphView(props: {
  graph: ConceptGraphT;
  capabilities: ConsultationCapabilities;
  sessionId: string;
  title: string;
  readOnly: boolean;
  decisions: ConsultationDecision[];
  actions: ConsultationAction[];
  onChanged: () => void;
  onShowSource: (ids: string[]) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  return (
    <ReactFlowProvider>
      <Canvas {...props} expanded={expanded} onToggleExpand={() => setExpanded((v) => !v)} />
    </ReactFlowProvider>
  );
}
