import type { ConceptGraph as ConceptGraphT, GraphEdge, GraphNode, GraphRelation } from "./consultationTypes";

/**
 * The question-led FOCUS view (rule 133/135, section 4 of the detail-and-
 * overview brief): a question, its proposed answers (`answers` edges
 * targeting it), and each proposal's reasons, concerns, clarifying
 * questions, dependencies and adjustments -- a small, meaningful
 * neighbourhood, never the whole `contains` topic tree.
 *
 * Deliberately a LOCAL, client-side layout, separate from the persisted
 * topic-tree positions `graph_node_view` stores (rule 124): this is a
 * different structural question (who answers what, not what sits under which
 * topic) over the SAME edges, and it never writes a position back — nothing
 * here is draggable or pinned, so there is nothing to persist and no risk of
 * a focus-view coordinate landing in the topic map's own layout.
 *
 * Only ever reads `graph`; it proposes no edges and stores nothing. A
 * question with no `answers` edges yet reports `found: false` so the caller
 * can show an honest waiting state rather than a manufactured diagram
 * (section 8).
 *
 * Rewritten (rule 137) against five concrete, reproduced defects in the
 * first version: a shared supporting concept rendered under every proposal
 * that cited it, with the SAME id each time (duplicate React Flow node ids);
 * only `supports`/`elaborates`/`challenges` were ever looked at, so a
 * `clarifies`, `depends_on` or `addresses` connection on a proposal was
 * invisible AND uncounted; a hard `MAX_PROPOSALS = 4` silently dropped a
 * fifth genuine alternative with no indication anything was hidden; a single
 * combined "+N" badge could not say what kind of thing was hidden; and
 * `focusId` always defaulted to root because nothing ever called this with
 * anything else.
 */

const GAP_X = 32;
const GAP_Y = 48;
const CHILD_GAP_Y = 16;
// Proposals wrap into rows instead of one ever-widening row (or being cut off
// after four) -- every genuine alternative is reachable, just not always on
// the same horizontal line. A caller with a wider panel may pass a larger
// value; this is a display choice, never a cap on how many are SHOWN.
const DEFAULT_MAX_PER_ROW = 3;
// A sanity ceiling, not a design cap: guards layout/render cost on a
// pathological map, set well above any case this brief names (six).
const HARD_PROPOSAL_CEILING = 24;

export type FocusRole = "focus" | "proposal" | "concern" | "reason" | "question" | "dependency" | "adjustment";
export type FocusGroupKind = "concern" | "reason" | "question" | "dependency" | "adjustment";
export type DetailLevel = "brief" | "standard" | "detailed";

// How many of each group a proposal shows before "N more" -- Brief hides
// supporting detail behind counts only; Standard aims for "the most useful
// available reasoning... a reason, a relevant concern, and a key question/
// condition where these exist" (section 4A) without forcing three kinds of
// evidence that were never reported; Detailed goes further but is still
// bounded, with local expansion (`expandedGroups`) for the rest.
const GROUP_CAP: Record<DetailLevel, number> = { brief: 0, standard: 1, detailed: 3 };
// When a group is explicitly expanded, show up to this many before even that
// runs out and falls back to a plain count -- a proposal with 40 concerns
// still gets a bounded card, reported honestly.
const EXPANDED_GROUP_CAP = 10;

export const GROUP_META: Record<FocusGroupKind, { label: string; plural: string }> = {
  concern: { label: "concern", plural: "concerns" },
  reason: { label: "reason", plural: "reasons" },
  question: { label: "question to investigate", plural: "questions to investigate" },
  dependency: { label: "dependency", plural: "dependencies" },
  adjustment: { label: "adjustment", plural: "adjustments" },
};

export interface FocusGroupCount {
  kind: FocusGroupKind;
  shownCount: number;
  totalCount: number;
}

export interface FocusCard {
  /** Stable, UNIQUE render id. Equal to `node.id` for a card's primary
   *  placement; a shared node cited by more than one proposal is placed
   *  ONCE (under whichever proposal cites it first) and reached from any
   *  other citing proposal by a routed cross-edge instead of a second card
   *  with the same id (section 7: "no duplicate render IDs... shared
   *  meaning not deleted"). */
  id: string;
  node: GraphNode;
  x: number;
  y: number;
  w: number;
  h: number;
  role: FocusRole;
  /** Set on a proposal card: what it has, and how much of it is shown. */
  groups?: FocusGroupCount[];
}

export interface FocusView {
  focusId: string;
  focusNode: GraphNode | null;
  found: boolean;
  totalProposalCount: number;
  shownProposalCount: number;
  cards: FocusCard[];
  edges: GraphEdge[];
  width: number;
  height: number;
}

export interface FocusOptions {
  detailLevel?: DetailLevel;
  /** `${proposalId}::${groupKind}` -- a group a person explicitly asked to
   *  see more of, independent of the ambient detail level. */
  expandedGroups?: Set<string>;
  maxPerRow?: number;
}

function sizeOf(n: GraphNode | undefined): { w: number; h: number } {
  return { w: n?.width ?? 240, h: n?.height ?? 72 };
}

function groupKey(proposalId: string, kind: FocusGroupKind): string {
  return `${proposalId}::${kind}`;
}

interface RawGroup {
  kind: FocusGroupKind;
  /** Each entry: the neighbour node id and the real graph edge that names it. */
  entries: { nodeId: string; edge: GraphEdge }[];
}

function rawGroupsFor(graph: ConceptGraphT, proposalId: string): RawGroup[] {
  const challenges: RawGroup["entries"] = [];
  const reasons: RawGroup["entries"] = [];
  const questions: RawGroup["entries"] = [];
  const dependencies: RawGroup["entries"] = [];
  const adjustments: RawGroup["entries"] = [];
  const concernIds = new Set<string>();

  for (const e of graph.edges) {
    if (e.relation === "challenges" && e.to_id === proposalId) {
      challenges.push({ nodeId: e.from_id, edge: e });
      concernIds.add(e.from_id);
    } else if ((e.relation === "supports" || e.relation === "elaborates") && e.to_id === proposalId) {
      reasons.push({ nodeId: e.from_id, edge: e });
    } else if (e.relation === "clarifies" && e.to_id === proposalId) {
      questions.push({ nodeId: e.from_id, edge: e });
    } else if (e.relation === "depends_on" && e.from_id === proposalId) {
      dependencies.push({ nodeId: e.to_id, edge: e });
    }
  }
  // Adjustments: something that `addresses` the proposal directly, OR
  // addresses one of the proposal's OWN concerns (section 4A: "adjustments
  // that address its concerns") -- gathered as one group under the proposal
  // rather than a third layout level nested inside the concern card.
  for (const e of graph.edges) {
    if (e.relation === "addresses" && (e.to_id === proposalId || concernIds.has(e.to_id))) {
      adjustments.push({ nodeId: e.from_id, edge: e });
    }
  }

  return [
    { kind: "concern", entries: challenges },
    { kind: "reason", entries: reasons },
    { kind: "question", entries: questions },
    { kind: "dependency", entries: dependencies },
    { kind: "adjustment", entries: adjustments },
  ];
}

export function buildFocusView(graph: ConceptGraphT, focusId = "root", options: FocusOptions = {}): FocusView {
  const detailLevel = options.detailLevel ?? "standard";
  const expandedGroups = options.expandedGroups ?? new Set<string>();
  const maxPerRow = Math.max(1, options.maxPerRow ?? DEFAULT_MAX_PER_ROW);

  const byId = new Map(graph.nodes.map((n) => [n.id, n]));
  const focus = byId.get(focusId) ?? null;
  if (!focus) {
    return { focusId, focusNode: null, found: false, totalProposalCount: 0, shownProposalCount: 0,
             cards: [], edges: [], width: 0, height: 0 };
  }

  // Stable order: the order nodes already appear in the graph payload, not a
  // score -- frequency/recency must not decide what counts as a main
  // possibility (section 2: "detachment... no popularity scores").
  const order = new Map(graph.nodes.map((n, i) => [n.id, i]));
  const answersEdges = graph.edges
    .filter((e) => e.relation === "answers" && e.to_id === focusId && byId.has(e.from_id))
    .sort((a, b) => (order.get(a.from_id) ?? 0) - (order.get(b.from_id) ?? 0));

  const totalProposalCount = answersEdges.length;
  if (totalProposalCount === 0) {
    return { focusId, focusNode: focus, found: false, totalProposalCount: 0, shownProposalCount: 0,
             cards: [], edges: [], width: 0, height: 0 };
  }

  const shownEdges = answersEdges.slice(0, HARD_PROPOSAL_CEILING);
  const cards: FocusCard[] = [];
  const usedEdges = new Set<GraphEdge>();
  // Every node already placed as its OWN card, by canonical node id -- the
  // dedup guard. A node cited by a second proposal is never given a second
  // card; that proposal instead gets a routed edge straight to the existing
  // one (section 7).
  const placedCardByNodeId = new Map<string, FocusCard>();

  const focusSize = sizeOf(focus);
  const focusCard: FocusCard = { id: focus.id, node: focus, x: 0, y: 0, ...focusSize, role: "focus" };
  cards.push(focusCard);
  placedCardByNodeId.set(focus.id, focusCard);

  type Column = { width: number; height: number; proposalCard: FocusCard; children: FocusCard[] };
  const columns: Column[] = [];

  for (const ae of shownEdges) {
    const proposal = byId.get(ae.from_id);
    if (!proposal) continue;
    usedEdges.add(ae);
    const proposalSize = sizeOf(proposal);

    const groups = rawGroupsFor(graph, proposal.id);
    const groupCounts: FocusGroupCount[] = [];
    const children: FocusCard[] = [];
    let childWidth = proposalSize.w;

    for (const group of groups) {
      const total = group.entries.length;
      const expanded = expandedGroups.has(groupKey(proposal.id, group.kind));
      const cap = expanded ? EXPANDED_GROUP_CAP : GROUP_CAP[detailLevel];
      let shown = 0;
      for (const entry of group.entries.slice(0, cap)) {
        usedEdges.add(entry.edge);
        const existing = placedCardByNodeId.get(entry.nodeId);
        if (existing) {
          // Already rendered under an earlier proposal -- do not duplicate
          // the card; the edge itself (added above) is enough to route a
          // second connection to the SAME instance.
          shown += 1;
          continue;
        }
        const node = byId.get(entry.nodeId);
        if (!node) continue;
        const cs = sizeOf(node);
        childWidth = Math.max(childWidth, cs.w);
        const card: FocusCard = { id: node.id, node, x: 0, y: 0, ...cs, role: group.kind };
        children.push(card);
        placedCardByNodeId.set(node.id, card);
        shown += 1;
      }
      if (total > 0) groupCounts.push({ kind: group.kind, shownCount: Math.min(shown, total), totalCount: total });
    }

    const proposalCard: FocusCard = {
      id: proposal.id, node: proposal, x: 0, y: 0, ...proposalSize, role: "proposal", groups: groupCounts,
    };
    cards.push(proposalCard);
    placedCardByNodeId.set(proposal.id, proposalCard);
    for (const c of children) cards.push(c);

    let stackH = 0;
    for (const c of children) stackH += c.h + CHILD_GAP_Y;
    const columnHeight = proposalSize.h + (children.length ? GAP_Y * 0.7 + stackH - CHILD_GAP_Y : 0);
    columns.push({ width: Math.max(proposalSize.w, childWidth), height: columnHeight, proposalCard, children });
  }

  // Wrap columns into rows (section 4A: "a visible selector, paging, or
  // wrapping" -- every proposal is placed, never silently dropped past a
  // fixed count).
  const rows: Column[][] = [];
  let row: Column[] = [];
  for (const col of columns) {
    row.push(col);
    if (row.length >= maxPerRow) { rows.push(row); row = []; }
  }
  if (row.length) rows.push(row);

  let rowTop = focusSize.h + GAP_Y;
  let maxWidth = focusSize.w;
  for (const cols of rows) {
    const rowWidth = cols.reduce((s, c) => s + c.width, 0) + GAP_X * Math.max(0, cols.length - 1);
    maxWidth = Math.max(maxWidth, rowWidth);
    let cursorX = -rowWidth / 2;
    const rowHeight = Math.max(...cols.map((c) => c.height));
    for (const col of cols) {
      col.proposalCard.x = cursorX + (col.width - col.proposalCard.w) / 2;
      col.proposalCard.y = rowTop;
      let childY = rowTop + col.proposalCard.h + GAP_Y * 0.7;
      for (const child of col.children) {
        child.x = cursorX + (col.width - child.w) / 2;
        child.y = childY;
        childY += child.h + CHILD_GAP_Y;
      }
      cursorX += col.width + GAP_X;
    }
    rowTop += rowHeight + GAP_Y;
  }
  focusCard.x = -focusSize.w / 2;
  focusCard.y = 0;

  const edges = graph.edges.filter((e) => usedEdges.has(e));

  return {
    focusId, focusNode: focus, found: true,
    totalProposalCount, shownProposalCount: shownEdges.length,
    cards, edges, width: maxWidth, height: rowTop,
  };
}

/** Every question-role node currently reachable as a Focus target: root
 *  plus any `question`/`investigate` item that itself has at least one
 *  `answers` edge -- used to populate "Focus on this question" navigation
 *  and to say plainly when a question has nothing answering it yet. */
export function focusableQuestions(graph: ConceptGraphT): { id: string; label: string; proposalCount: number }[] {
  const counts = new Map<string, number>();
  for (const e of graph.edges) {
    if (e.relation !== "answers") continue;
    counts.set(e.to_id, (counts.get(e.to_id) ?? 0) + 1);
  }
  const out: { id: string; label: string; proposalCount: number }[] = [];
  for (const n of graph.nodes) {
    if (n.kind !== "root" && n.kind !== "question" && n.kind !== "investigate") continue;
    const proposalCount = counts.get(n.id) ?? 0;
    if (proposalCount > 0) out.push({ id: n.id, label: n.label, proposalCount });
  }
  return out;
}

export function isCrossRelation(relation: GraphRelation): boolean {
  return relation !== "contains";
}
