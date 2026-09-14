import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Background, Controls, Handle, MiniMap, Position, ReactFlow, ReactFlowProvider,
  type Edge, type Node, type NodeProps, useEdgesState, useNodesState, useReactFlow,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import {
  Download, FileCode, Image as ImageIcon, LayoutGrid, List, Loader2, Maximize2,
  Minimize2, Network, Pin, Quote, Search, Trash2, X,
} from "lucide-react";
import { api } from "../../lib/api";
import type {
  ConceptGraph as ConceptGraphT, ConsultationAction, ConsultationCapabilities,
  ConsultationDecision, GraphNode, GraphRelation,
} from "../../lib/consultationTypes";
import { Button, Card } from "../ui";

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

type FlowNodeData = { graphNode: GraphNode; dimmed: boolean; matched: boolean };
type FlowEdgeData = { relation: GraphRelation; dimmed: boolean };

function relationStyle(relation: GraphRelation, capabilities: ConsultationCapabilities) {
  const meta = capabilities.edge_relations.find((r) => r.id === relation);
  const dash = meta?.style === "dashed" ? "6 4" : meta?.style === "dotted" ? "1 4" : undefined;
  return { dash, label: meta?.label ?? relation };
}

function kindColor(kind: string, capabilities: ConsultationCapabilities): string {
  return capabilities.node_kinds.find((k) => k.id === kind)?.color ?? "#64748b";
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

function ConceptNode({ data, selected }: NodeProps<Node<FlowNodeData, "concept">>) {
  const { graphNode: n, dimmed, matched } = data;
  const isRoot = n.kind === "root";
  return (
    <div
      role="button"
      tabIndex={0}
      aria-label={`${n.kind}: ${n.label}`}
      style={{
        borderColor: (n as GraphNode).origin === "fallback_grouping" ? "#71717a" : undefined,
        opacity: dimmed ? 0.25 : 1,
      }}
      className={`w-[200px] rounded-lg border-2 bg-slate-900 px-3 py-2 text-left shadow-sm transition-opacity
        ${selected ? "ring-2 ring-amber-300" : ""} ${matched ? "ring-2 ring-sky-400" : ""}
        ${isRoot ? "bg-slate-800" : ""}`}
    >
      <Handle type="target" position={Position.Top} className="!opacity-0" />
      <Handle type="source" position={Position.Bottom} className="!opacity-0" />
      <div className="flex items-center justify-between gap-1">
        <span className="truncate text-[10px] font-semibold uppercase tracking-wide"
              style={{ color: n.origin === "fallback_grouping" ? "#a1a1aa" : undefined }}>
          {n.kind === "bucket" ? "category" : n.kind}
        </span>
        {n.human_edited && <Pin className="h-3 w-3 shrink-0 text-emerald-400" aria-label="corrected by hand" />}
      </div>
      <p className="mt-0.5 line-clamp-3 text-sm leading-snug text-slate-100">{n.label}</p>
      {n.status_label && (
        <p className="mt-1 truncate text-[10px] text-slate-400">{n.status_label}</p>
      )}
    </div>
  );
}

const NODE_TYPES = { concept: ConceptNode };

function toFlowNodes(graph: ConceptGraphT, hidden: Set<string>, selectedId: string | null,
                     matches: Set<string>): Node<FlowNodeData, "concept">[] {
  return graph.nodes.filter((n) => !hidden.has(n.id)).map((n) => ({
    id: n.id,
    type: "concept",
    position: { x: n.x, y: n.y },
    data: { graphNode: n, dimmed: matches.size > 0 && !matches.has(n.id), matched: matches.has(n.id) },
    selected: n.id === selectedId,
    draggable: n.kind !== "root",
  }));
}

function toFlowEdges(graph: ConceptGraphT, hidden: Set<string>, capabilities: ConsultationCapabilities,
                     focusId: string | null): Edge<FlowEdgeData>[] {
  return graph.edges
    .filter((e) => !hidden.has(e.from_id) && !hidden.has(e.to_id))
    .map((e) => {
      const style = relationStyle(e.relation, capabilities);
      const touchesFocus = focusId != null && (e.from_id === focusId || e.to_id === focusId);
      const showLabel = e.kind === "cross" && (focusId == null || touchesFocus);
      return {
        id: e.id,
        source: e.from_id,
        target: e.to_id,
        type: e.kind === "hierarchy" ? "smoothstep" : "straight",
        animated: false,
        label: showLabel ? (e.label || style.label) : undefined,
        labelStyle: { fill: "#cbd5e1", fontSize: 10 },
        labelBgStyle: { fill: "#0f172a", fillOpacity: 0.8 },
        style: {
          stroke: e.kind === "hierarchy" ? "#475569" : "#eab308",
          strokeWidth: e.kind === "hierarchy" ? 1.5 : touchesFocus ? 2.5 : 1.5,
          strokeDasharray: style.dash,
          opacity: focusId != null && !touchesFocus && e.kind === "cross" ? 0.15 : 0.9,
        },
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
  onShowSource, onSelect,
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
}) {
  const byId = useMemo(() => new Map(graph.nodes.map((n) => [n.id, n])), [graph.nodes]);
  const [draft, setDraft] = useState(node.detail);
  const [busy, setBusy] = useState(false);
  const [connectTo, setConnectTo] = useState("");
  const [connectRelation, setConnectRelation] = useState<GraphRelation>("related_to");
  const [mergeWith, setMergeWith] = useState("");

  useEffect(() => setDraft(node.detail), [node.id, node.detail]);

  const outgoing = graph.edges.filter((e) => e.from_id === node.id);
  const incoming = graph.edges.filter((e) => e.to_id === node.id);
  const decisionRow = node.kind === "decision" && node.record_ref
    ? decisions.find((d) => d.map_id === node.record_ref!.id) : undefined;
  const actionRow = node.kind === "action" && node.record_ref
    ? actions.find((a) => a.map_id === node.record_ref!.id) : undefined;

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    try { await fn(); onChanged(); } finally { setBusy(false); }
  };

  const canEditText = !readOnly && node.record_ref && node.kind !== "decision" && node.kind !== "action";
  const relationOptions = capabilities.edge_relations.filter(
    (r) => r.id !== "contains" || node.kind === "theme");
  const otherNodes = graph.nodes.filter((n) => n.id !== node.id && n.kind !== "root");
  const sameKindNodes = otherNodes.filter((n) => n.kind === node.kind && n.record_ref);

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
          <ul className="space-y-1">
            {[...incoming.map((e) => ({ e, other: e.from_id, dir: "from" as const })),
              ...outgoing.map((e) => ({ e, other: e.to_id, dir: "to" as const }))]
              .filter(({ other }) => byId.has(other))
              .map(({ e, other, dir }) => (
                <li key={e.id} className="flex items-center justify-between gap-2 text-xs">
                  <button onClick={() => onSelect(other)}
                          className="min-w-0 flex-1 truncate text-left text-slate-300 hover:text-amber-200">
                    {relationStyle(e.relation, capabilities).label}
                    {dir === "from" ? " ← " : " → "}
                    {byId.get(other)?.label}
                  </button>
                  {!readOnly && !e.synthetic && (
                    <button
                      title="Reject this connection"
                      onClick={() => void run(() => api.rejectGraphEdge(sessionId, e.id))}
                      className="shrink-0 text-slate-600 hover:text-rose-400"
                    >
                      <X className="h-3 w-3" />
                    </button>
                  )}
                </li>
              ))}
            {incoming.length + outgoing.length === 0 && (
              <li className="text-xs text-slate-600">No connections yet.</li>
            )}
          </ul>
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
  const draggingRef = useRef(false);
  const lastSyncedRef = useRef<string>("");

  useEffect(() => {
    const onResize = () => setNarrow(window.innerWidth < 640);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  const hidden = useMemo(() => collapsedDescendants(graph), [graph]);
  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return new Set<string>();
    return new Set(graph.nodes.filter((n) =>
      n.label.toLowerCase().includes(q) || n.detail.toLowerCase().includes(q)).map((n) => n.id));
  }, [graph.nodes, query]);

  const [nodes, setNodes, onNodesChange] = useNodesState<Node<FlowNodeData, "concept">>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge<FlowEdgeData>>([]);

  // Re-sync from the server whenever the graph actually changed, but never
  // while a drag is in progress -- and never reshuffle a node whose position
  // has not changed (section 5: "do not recenter the whole diagram").
  useEffect(() => {
    const key = `${graph.content_revision}:${graph.graph_revision}:${graph.view_revision}`;
    if (draggingRef.current || key === lastSyncedRef.current) return;
    lastSyncedRef.current = key;
    setNodes(toFlowNodes(graph, hidden, selectedId, matches));
    setEdges(toFlowEdges(graph, hidden, capabilities, selectedId));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [graph, hidden, capabilities]);

  // Selection/search only ever restyle existing nodes; they never move one.
  useEffect(() => {
    setNodes((prev) => prev.map((n) => ({
      ...n, selected: n.id === selectedId,
      data: { ...n.data, dimmed: matches.size > 0 && !matches.has(n.id), matched: matches.has(n.id) },
    })));
    setEdges(toFlowEdges(graph, hidden, capabilities, selectedId));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId, matches]);

  const selectedNode = selectedId ? graph.nodes.find((n) => n.id === selectedId) ?? null : null;

  const focusNode = useCallback((id: string) => {
    setSelectedId(id);
    const n = graph.nodes.find((x) => x.id === id);
    if (n) rf.setCenter(n.x + 100, n.y + 32,
      { zoom: 1, duration: prefersReducedMotion() ? 0 : 400 });
  }, [graph.nodes, rf]);

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

  return (
    <div className={`flex flex-col gap-2 ${expanded ? "fixed inset-0 z-40 bg-slate-950 p-4" : "h-full"}`}>
      {graph.fallback && (
        <div className="rounded-lg border border-slate-700 bg-slate-900/60 px-3 py-1.5 text-xs text-slate-400">
          No themes or connections have been drawn yet — grouped automatically by category
          for now.
        </div>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-[10rem] flex-1">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-600" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
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
          <Button variant="secondary" className="text-xs" disabled={arrange.busy}
                  onClick={() => void arrange.run()}>
            <LayoutGrid className="h-3.5 w-3.5" /> Arrange map
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
      </div>

      <div className="grid min-h-0 flex-1 gap-3 lg:grid-cols-[1fr_20rem]">
        <div className="min-h-[22rem] overflow-hidden rounded-xl border border-slate-800 bg-slate-950">
          {showOutline ? (
            <div className="h-full overflow-y-auto p-3">
              {graph.nodes.length <= 1 ? (
                <EmptyState />
              ) : (
                <Outline graph={graph} onSelect={focusNode} selectedId={selectedId} />
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
              onNodeClick={(_, n) => setSelectedId(n.id)}
              onPaneClick={() => setSelectedId(null)}
              onNodeDragStart={() => { draggingRef.current = true; }}
              onNodeDragStop={(_, n) => {
                draggingRef.current = false;
                if (!readOnly) void api.setGraphNodeView(
                  sessionId, n.id, { x: n.position.x, y: n.position.y, pinned: true });
              }}
              onNodeDoubleClick={(_, n) => {
                const gn = graph.nodes.find((x) => x.id === n.id);
                if (gn && (gn.kind === "theme" || gn.kind === "bucket")) {
                  void api.setGraphNodeView(sessionId, n.id, { collapsed: !gn.collapsed })
                    .then(onChanged);
                }
              }}
              nodesDraggable={!readOnly}
              nodesConnectable={false}
              fitView
              fitViewOptions={{ padding: 0.3, duration: prefersReducedMotion() ? 0 : 400 }}
              proOptions={{ hideAttribution: true }}
              minZoom={0.15}
            >
              <Background gap={20} color="#1e293b" />
              <Controls showInteractive={false} />
              <MiniMap
                pannable zoomable
                nodeColor={(n) => kindColor((n.data as unknown as FlowNodeData).graphNode.kind, capabilities)}
                maskColor="rgba(2,6,23,0.75)"
                style={{ backgroundColor: "#0f172a" }}
              />
            </ReactFlow>
          )}
        </div>

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
              onSelect={focusNode}
            />
          ) : (
            <Legend capabilities={capabilities} graph={graph} />
          )}
        </div>
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
  return (
    <Card className="h-full overflow-y-auto p-4">
      <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Legend</p>
      <ul className="space-y-1.5">
        {capabilities.node_kinds.filter((k) => usedKinds.has(k.id)).map((k) => (
          <li key={k.id} className="flex items-center gap-2 text-xs text-slate-300">
            <span className="h-2.5 w-2.5 shrink-0 rounded-sm" style={{ backgroundColor: k.color }} />
            {k.plural}
          </li>
        ))}
      </ul>
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
