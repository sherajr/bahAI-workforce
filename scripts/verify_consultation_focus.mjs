// A runnable check for the Focus view's pure layout/selection logic
// (`dashboard/src/lib/consultationFocus.ts`), in the spirit of this repo's
// `scripts/test_*.py` (root AGENTS.md: "There is no formal test framework:
// scripts/test_*.py are runnable checks"). There is no frontend test
// framework in this repo; this fills the specific gap a review named:
// "backend tests that feed prewritten model patches do not test frontend
// selection or model comprehension." It exercises the ACTUAL TypeScript
// source (transpiled on the fly via the `typescript` package already a
// dashboard devDependency -- no new package, no ts-node), not a
// hand-maintained re-implementation that could drift from the real code.
//
// Run: node scripts/verify_consultation_focus.mjs

import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, "..");
const require = createRequire(import.meta.url);
const ts = require(path.join(repoRoot, "dashboard", "node_modules", "typescript"));

function loadModule(relSourcePath) {
  const srcPath = path.join(repoRoot, relSourcePath);
  const src = fs.readFileSync(srcPath, "utf8");
  const out = ts.transpileModule(src, {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 },
    fileName: srcPath,
  }).outputText;
  const tmpFile = path.join(os.tmpdir(), `verify_${path.basename(relSourcePath)}_${Date.now()}_${Math.random().toString(36).slice(2)}.mjs`);
  fs.writeFileSync(tmpFile, out);
  return import("file://" + tmpFile.split(path.sep).join("/")).finally(() => {
    fs.unlink(tmpFile, () => {});
  });
}

const { buildFocusView, focusableQuestions, GROUP_META } =
  await loadModule("dashboard/src/lib/consultationFocus.ts");

let passed = 0, failed = 0;
const failures = [];

function check(name, cond, detail) {
  if (cond) { passed += 1; return; }
  failed += 1;
  failures.push(detail === undefined ? name : `${name} -- ${JSON.stringify(detail)}`);
}

// ── A small synthetic graph builder ─────────────────────────────────────

let autoId = 0;
function node(kind, label, extra = {}) {
  autoId += 1;
  const id = extra.id || `${kind}_${autoId}`;
  return {
    id, kind, label, detail: extra.detail ?? label, status: null, status_label: null,
    human_edited: false, source_turn_ids: [], record_ref: null, extra: {}, origin: "model",
    has_map_item: true, x: 0, y: 0, pinned: false, collapsed: false,
    width: extra.width ?? 240, height: extra.height ?? 72,
  };
}
function root(question) {
  return { id: "root", kind: "root", label: question, detail: question, status: null,
           status_label: null, human_edited: false, source_turn_ids: [], record_ref: null,
           extra: {}, origin: "root", has_map_item: false, x: 0, y: 0, pinned: false,
           collapsed: false, width: 240, height: 96 };
}
function edge(from_id, to_id, relation, extra = {}) {
  return { id: `${from_id}::${relation}::${to_id}`, from_id, to_id, relation, label: "",
           kind: relation === "contains" ? "hierarchy" : "cross", inferred: true,
           human_edited: false, source_turn_ids: [], synthetic: false, ...extra };
}
function graphOf(nodes, edges) {
  return { schema_version: 2, session_id: "s", content_revision: 1, graph_revision: 1,
           view_revision: 1, record_revision: 1, fallback: false, unplaced_count: 0,
           nodes, edges };
}

// ── Case: six genuinely distinct options, all discoverable ──────────────
{
  const r = root("Which course?");
  const proposals = Array.from({ length: 6 }, (_, i) => node("idea", `Option ${i + 1}`));
  const nodes = [r, ...proposals];
  const edges = proposals.map((p) => edge(p.id, "root", "answers"));
  const view = buildFocusView(graphOf(nodes, edges), "root");
  check("six distinct options: all six are shown, not just the first four",
    view.shownProposalCount === 6 && view.totalProposalCount === 6,
    { shown: view.shownProposalCount, total: view.totalProposalCount });
  const shownIds = new Set(view.cards.filter((c) => c.role === "proposal").map((c) => c.id));
  check("...and every proposal id is actually a card",
    proposals.every((p) => shownIds.has(p.id)), [...shownIds]);
}

// ── Case: one proposal, every relation kind represented ──────────────────
{
  const r = root("Q?");
  const proposal = node("idea", "Take the course");
  const concern1 = node("concern", "Concern one");
  const concern2 = node("concern", "Concern two");
  const reason = node("fact", "A reason");
  const question = node("investigate", "A clarifying question");
  const dependency = node("fact", "A dependency");
  const nodes = [r, proposal, concern1, concern2, reason, question, dependency];
  const edges = [
    edge(proposal.id, "root", "answers"),
    edge(concern1.id, proposal.id, "challenges"),
    edge(concern2.id, proposal.id, "challenges"),
    edge(reason.id, proposal.id, "supports"),
    edge(question.id, proposal.id, "clarifies"),
    edge(proposal.id, dependency.id, "depends_on"),
  ];
  const view = buildFocusView(graphOf(nodes, edges), "root", { detailLevel: "detailed" });
  const proposalCard = view.cards.find((c) => c.id === proposal.id);
  check("every relation KIND is counted on the proposal, not only supports/challenges/elaborates",
    proposalCard.groups.length === 4, proposalCard.groups);
  const byKind = Object.fromEntries(proposalCard.groups.map((g) => [g.kind, g]));
  check("two concerns are both counted", byKind.concern?.totalCount === 2, byKind.concern);
  check("the clarifying question is counted, not dropped", byKind.question?.totalCount === 1, byKind.question);
  check("the dependency (reverse-direction edge) is counted", byKind.dependency?.totalCount === 1, byKind.dependency);
  const shownIds = new Set(view.cards.map((c) => c.id));
  check("at 'detailed', every one of these is actually rendered as a card",
    [concern1, concern2, reason, question, dependency].every((n) => shownIds.has(n.id)),
    [...shownIds]);
}

// ── Case: one shared concept supports two proposals -- no duplicate ids ──
{
  const r = root("Q?");
  const propA = node("idea", "Proposal A");
  const propB = node("idea", "Proposal B");
  const shared = node("fact", "A fact both cite");
  const nodes = [r, propA, propB, shared];
  const edges = [
    edge(propA.id, "root", "answers"),
    edge(propB.id, "root", "answers"),
    edge(shared.id, propA.id, "supports"),
    edge(shared.id, propB.id, "supports"),
  ];
  const view = buildFocusView(graphOf(nodes, edges), "root", { detailLevel: "detailed" });
  const ids = view.cards.map((c) => c.id);
  const uniqueIds = new Set(ids);
  check("no duplicate render ids even though one fact supports two proposals",
    ids.length === uniqueIds.size, ids);
  check("the shared fact still appears (once)", uniqueIds.has(shared.id));
  const edgesToShared = view.edges.filter((e) => e.from_id === shared.id || e.to_id === shared.id);
  check("both relationships to the shared fact are still represented as edges",
    edgesToShared.length === 2, edgesToShared);
}

// ── Case: same concern reachable by two relation paths is one distinct card ──
{
  const r = root("Q?");
  const proposal = node("idea", "Proposal");
  const concern = node("concern", "One concern");
  const nodes = [r, proposal, concern];
  // both a `challenges` and (contrived) a `clarifies` name the same node
  const edges = [
    edge(proposal.id, "root", "answers"),
    edge(concern.id, proposal.id, "challenges"),
    edge(concern.id, proposal.id, "clarifies"),
  ];
  const view = buildFocusView(graphOf(nodes, edges), "root", { detailLevel: "detailed" });
  const concernCards = view.cards.filter((c) => c.id === concern.id);
  check("reachable via two relation paths, the concern is still rendered exactly once",
    concernCards.length === 1, concernCards);
}

// ── Case: detail levels change what is shown, never what is counted ──────
{
  const r = root("Q?");
  const proposal = node("idea", "Proposal");
  const concerns = [node("concern", "c1"), node("concern", "c2"), node("concern", "c3")];
  const nodes = [r, proposal, ...concerns];
  const edges = [edge(proposal.id, "root", "answers"),
                 ...concerns.map((c) => edge(c.id, proposal.id, "challenges"))];
  const g = graphOf(nodes, edges);
  const brief = buildFocusView(g, "root", { detailLevel: "brief" });
  const standard = buildFocusView(g, "root", { detailLevel: "standard" });
  const detailed = buildFocusView(g, "root", { detailLevel: "detailed" });
  const shownCount = (v) => v.cards.filter((c) => c.role === "concern").length;
  check("brief shows none of the concerns as cards, but still counts them",
    shownCount(brief) === 0
    && brief.cards.find((c) => c.id === proposal.id).groups.find((x) => x.kind === "concern").totalCount === 3);
  check("standard shows fewer concerns than detailed", shownCount(standard) < shownCount(detailed),
    { standard: shownCount(standard), detailed: shownCount(detailed) });
  check("detailed shows all three", shownCount(detailed) === 3, shownCount(detailed));
  const expanded = buildFocusView(g, "root", {
    detailLevel: "brief", expandedGroups: new Set([`${proposal.id}::concern`]),
  });
  check("explicitly expanding a group overrides a 'brief' default",
    shownCount(expanded) === 3, shownCount(expanded));
}

// ── Case: focusing a NON-ROOT question works the same way ────────────────
{
  const r = root("Main question?");
  const subQuestion = node("investigate", "A sub-question");
  const proposal = node("idea", "An answer to the sub-question");
  const nodes = [r, subQuestion, proposal];
  const edges = [edge(proposal.id, subQuestion.id, "answers")];
  const view = buildFocusView(graphOf(nodes, edges), subQuestion.id);
  check("a non-root focusId finds its own proposals", view.found && view.shownProposalCount === 1,
    view);
  const list = focusableQuestions(graphOf(nodes, edges));
  check("focusableQuestions reports the sub-question, not just root",
    list.some((q) => q.id === subQuestion.id), list);
}

// ── Case: no answers at all reports found:false honestly ─────────────────
{
  const r = root("Q?");
  const view = buildFocusView(graphOf([r], []), "root");
  check("no proposals -> found is false, not a manufactured diagram", view.found === false);
  check("GROUP_META covers every kind buildFocusView can report",
    ["concern", "reason", "question", "dependency", "adjustment"].every((k) => !!GROUP_META[k]));
}

console.log(`\nconsultation-focus: ${passed} passed, ${failed} failed  (${passed + failed} checks)`);
if (failed) {
  console.log("\nFailures:");
  for (const f of failures) console.log("  - " + f);
  process.exit(1);
}
