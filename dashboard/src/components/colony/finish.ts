// How polished a body looks on the map IS its performance — the shine is data,
// not decoration.
//
// Rules 14/35 draw the whole boundary. Only a JUDGED run — a reviewer verdict
// or a deterministic check — says anything about quality; a mechanical run (a
// render that returned a file) says nothing at all. So instruments have no
// finish, and an agent with nothing judged yet stays PLAIN rather than dull:
// "not scored yet" and "scored badly" must never look the same, or clean-run
// stats quietly become an uptime metric, which is the exact failure rule 14
// exists to prevent.

import type { ColonyAgent, ColonyTeam } from "../../lib/types";

export type FinishGrade =
  | "instrument"  // a pipeline step: never scored, so never polished or dull
  | "unproven"    // nothing judged yet
  | "tarnished"   // dross and scratches — wants a polish
  | "dulled"      // losing its lustre
  | "bright"      // clean and polished
  | "radiant";    // gleaming, with a glimmer

export interface SphereFinish {
  grade: FinishGrade;
  /** 0 = heavy dross, 1 = mirror-bright. null when nothing has been judged. */
  polish: number | null;
  judged: number;
  clean: number;
  /** Consecutive judged runs that came back needing work. */
  slump: number;
  /** 0–1 gloss and 0–1 dross, derived from `polish`. Both are 0 when nothing
   *  has been judged — plain is its own honest state, not a middling one. */
  shine: number;
  wear: number;
  /** Plain language for the hover card, since Sheraj reads the map, not this. */
  label: string;
}

/** A body with no verdict either way sits exactly halfway — the point the
 *  smoothing below pulls a thin record back toward. */
const NEUTRAL = 0.5;

/** Judged runs needed before the finish reaches full strength. Under it, both
 *  the shine and the dross are pulled toward plain: one reviewed run is an
 *  anecdote, and a first failure should read as "barely started", not as "this
 *  agent is bad". It softens the PICTURE only — the exact counts are on the
 *  hover card and in the drawer, so nothing is hidden. */
const CONFIDENCE_RUNS = 3;

/** Each consecutive failure tarnishes on top of the lifetime rate, because a
 *  long-clean agent that has just failed twice needs a polish NOW and its
 *  lifetime average is too slow to say so. The backend already treats two in a
 *  row as serious — that is what costs a trust level. */
const SLUMP_PENALTY = 0.18;
const SLUMP_MAX = 0.45;

const LABELS: Record<FinishGrade, string> = {
  instrument: "A pipeline step, never scored — so never shiny or dull",
  unproven: "Nothing judged yet — plain until reviewed work lands",
  radiant: "Gleaming — nearly everything judged comes back clean",
  bright: "Polished — most judged work comes back clean",
  dulled: "Losing its shine — judged work is coming back mixed",
  tarnished: "Wants a polish — judged work is coming back needing changes",
};

function clamp(n: number, lo = 0, hi = 1): number {
  return Math.min(hi, Math.max(lo, n));
}

function gradeFor(polish: number): FinishGrade {
  if (polish >= 0.8) return "radiant";
  if (polish >= 0.62) return "bright";
  if (polish >= 0.34) return "dulled";
  return "tarnished";
}

function plain(grade: "instrument" | "unproven", judged = 0, clean = 0): SphereFinish {
  return {
    grade, polish: null, judged, clean, slump: 0,
    shine: 0, wear: 0, label: LABELS[grade],
  };
}

/** The finish for a record of judged work. Shared by agents and team cores so
 *  a core can never read brighter than the members it is made of. */
function finishFor(clean: number, judged: number, slump: number): SphereFinish {
  if (judged <= 0) return plain("unproven");

  const rate = clean / judged;
  const confidence = judged / (judged + CONFIDENCE_RUNS);
  const smoothed = NEUTRAL + (rate - NEUTRAL) * confidence;
  // The slump is scaled by the same confidence: a single early failure should
  // not be punished twice over.
  const penalty = Math.min(SLUMP_MAX, slump * SLUMP_PENALTY) * confidence;
  const polish = clamp(smoothed - penalty);

  return {
    grade: gradeFor(polish),
    polish,
    judged,
    clean,
    slump,
    shine: clamp((polish - 0.42) / 0.45),
    wear: clamp((0.6 - polish) / 0.45),
    label: LABELS[gradeFor(polish)],
  };
}

export function agentFinish(agent: ColonyAgent): SphereFinish {
  if (agent.is_instrument) return plain("instrument");
  return finishFor(agent.clean_runs, agent.total_runs, agent.consecutive_failures);
}

/**
 * A team core is finished from the judged work of its MEMBERS, added up — the
 * same derived-not-stored discipline as the handoff graph (rule 35). Its
 * instruments are left out: their runs are mechanical, and folding them in
 * would polish a core with work nobody scored.
 */
export function teamFinish(team: ColonyTeam, agents: ColonyAgent[]): SphereFinish {
  const members = agents.filter((a) => team.members.includes(a.name) && !a.is_instrument);
  const judged = members.reduce((n, a) => n + a.total_runs, 0);
  const clean = members.reduce((n, a) => n + a.clean_runs, 0);
  // The worst current slump on the team, not the sum: one member stuck in a bad
  // patch is the team's problem, but three teammates' separate single failures
  // are not a crisis.
  const slump = members.reduce((n, a) => Math.max(n, a.consecutive_failures), 0);
  return finishFor(clean, judged, slump);
}
