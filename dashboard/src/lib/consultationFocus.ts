import type { ConceptGraph as ConceptGraphT, GraphEdge, GraphNode } from "./consultationTypes";

/**
 * The question-led FOCUS view (rule 135, section 7 of the readability brief):
 * root/current question, its proposed answers (`answers` edges targeting it),
 * and each proposal's own reasons (`supports`/`elaborates`) and concerns
 * (`challenges`) -- a small, meaningful neighbourhood, never the whole
 * `contains` topic tree.
 *
 * Deliberately a LOCAL, client-side layout, separate from the persisted
 * topic-tree positions `graph_node_view` stores (rule 124): this is a
 * different structural question (who answers what, not what sits under which
 * topic) over the SAME edges, and it never writes a position back — nothing
 * here is draggable or pinned, so there is nothing to persist and no risk of
 * a focus-view coordinate landing in the topic map's own layout.
 *
 * Only ever reads `graph`; it proposes no edges and stores nothing. A session
 * with no `answers` edges yet (every consultation held before this relation
 * existed) reports `found: false` so the caller can show an honest waiting
 * state rather than a manufactured diagram (section 8).
 */

const MAX_PROPOSALS = 4;
const PER_PROPOSAL_CAP = 2;
const GAP_X = 32;
const GAP_Y = 48;
const CHILD_GAP_Y = 16;

export interface FocusCard {
  node: GraphNode;
  x: number;
  y: number;
  role: "focus" | "proposal" | "reason" | "concern";
  hiddenSiblingCount?: number; // reasons/concerns of the SAME proposal not shown, for a "+N more" hint
}

export interface FocusView {
  focusId: string;
  found: boolean;
  totalProposalCount: number;
  shownProposalCount: number;
  cards: FocusCard[];
  edges: GraphEdge[];
}

function sizeOf(n: GraphNode | undefined): { w: number; h: number } {
  return { w: n?.width ?? 240, h: n?.height ?? 72 };
}

export function buildFocusView(graph: ConceptGraphT, focusId = "root"): FocusView {
  const byId = new Map(graph.nodes.map((n) => [n.id, n]));
  const focus = byId.get(focusId);
  if (!focus) {
    return { focusId, found: false, totalProposalCount: 0, shownProposalCount: 0, cards: [], edges: [] };
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
    return { focusId, found: false, totalProposalCount: 0, shownProposalCount: 0, cards: [], edges: [] };
  }

  const shownEdges = answersEdges.slice(0, MAX_PROPOSALS);
  const cards: FocusCard[] = [];
  const shownEdgeSet = new Set<GraphEdge>();

  const focusSize = sizeOf(focus);
  const columns: { proposalId: string; width: number; proposalCard: FocusCard; children: FocusCard[] }[] = [];

  for (const ae of shownEdges) {
    const proposal = byId.get(ae.from_id);
    if (!proposal) continue;
    shownEdgeSet.add(ae);
    const proposalSize = sizeOf(proposal);

    const concernEdges = graph.edges.filter(
      (e) => e.relation === "challenges" && e.to_id === proposal.id && byId.has(e.from_id));
    const reasonEdges = graph.edges.filter(
      (e) => (e.relation === "supports" || e.relation === "elaborates")
        && e.to_id === proposal.id && byId.has(e.from_id));

    // Concerns are shown first and are never the ones dropped for a reason --
    // a minority objection surviving is worth more than a second reason for
    // the same proposal (section 2/6: preserve concerns even when compressing).
    const shownConcerns = concernEdges.slice(0, PER_PROPOSAL_CAP);
    const remainingSlots = PER_PROPOSAL_CAP - shownConcerns.length;
    const shownReasons = reasonEdges.slice(0, remainingSlots);
    const hiddenConcerns = concernEdges.length - shownConcerns.length;
    const hiddenReasons = reasonEdges.length - shownReasons.length;

    const children: FocusCard[] = [];
    let childWidth = proposalSize.w;
    for (const ce of [...shownConcerns, ...shownReasons]) {
      const child = byId.get(ce.from_id);
      if (!child) continue;
      shownEdgeSet.add(ce);
      const cs = sizeOf(child);
      childWidth = Math.max(childWidth, cs.w);
      children.push({
        node: child, x: 0, y: 0,
        role: ce.relation === "challenges" ? "concern" : "reason",
      });
    }

    // A folded-away CONCERN has to show on the proposal ("the option") itself
    // -- section 7: "that option must show the concern indicator" -- not
    // buried on whichever supporting card happened to be drawn last.
    const proposalCard: FocusCard = {
      node: proposal, x: 0, y: 0, role: "proposal",
      hiddenSiblingCount: hiddenConcerns + hiddenReasons > 0 ? hiddenConcerns + hiddenReasons : undefined,
    };
    columns.push({ proposalId: proposal.id, width: Math.max(proposalSize.w, childWidth), proposalCard, children });
  }

  const totalWidth = columns.reduce((sum, c) => sum + c.width, 0) + GAP_X * Math.max(0, columns.length - 1);
  let cursorX = -totalWidth / 2;
  const proposalY = focusSize.h + GAP_Y;

  cards.push({ node: focus, x: -focusSize.w / 2, y: 0, role: "focus" });

  for (const col of columns) {
    const pSize = sizeOf(col.proposalCard.node);
    col.proposalCard.x = cursorX + (col.width - pSize.w) / 2;
    col.proposalCard.y = proposalY;
    cards.push(col.proposalCard);

    let childY = proposalY + pSize.h + GAP_Y * 0.7;
    for (const child of col.children) {
      const cSize = sizeOf(child.node);
      child.x = cursorX + (col.width - cSize.w) / 2;
      child.y = childY;
      cards.push(child);
      childY += cSize.h + CHILD_GAP_Y;
    }
    cursorX += col.width + GAP_X;
  }

  const edges = graph.edges.filter((e) => shownEdgeSet.has(e));

  return {
    focusId, found: true, totalProposalCount, shownProposalCount: shownEdges.length, cards, edges,
  };
}
