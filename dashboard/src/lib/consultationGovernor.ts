/**
 * The Speech Governor, in the browser.
 *
 * This is the mirror of `agents/live_consultation_governor.py`, which is the
 * specification and the test surface. This copy exists because the realtime
 * events arrive here: cutting the assistant off the instant a person starts
 * speaking cannot wait for a network round trip, and neither can the decision
 * not to speak in the first place.
 *
 * Two things keep the two copies honest:
 *   1. Every NUMBER comes from the server, already resolved for this session's
 *      presence (`capabilities.floor_policies[presence]`, rule 87). There is no
 *      timing constant and no scaling arithmetic in this file — if the dial
 *      moved a number, the server is where it moved.
 *   2. Nothing here can start speech on its own. A local `allowed` only lets the
 *      client ASK the server, and the server evaluates again on facts the page
 *      cannot know (the cooldowns, the mode of record, the current revision).
 *      Both have to say yes.
 *
 * The rule this whole file exists for:
 *
 *     SILENCE IS NOT PERMISSION FOR THE AI TO SPEAK.
 *
 * There is no branch below in which elapsed silence alone returns allowed for
 * an unsolicited contribution.
 */

import type { ConsultationMode, FloorPolicy, ModeInfo } from "./consultationTypes";

export const DISCONNECTED = "disconnected";
export const LISTENING_IDLE = "listening_idle";
export const HUMAN_SPEAKING = "human_speaking";
export const HUMAN_REFLECTIVE_PAUSE = "human_reflective_pause";
export const FLOOR_OPEN = "floor_open";
export const AI_REQUEST_QUEUED = "ai_request_queued";
export const AI_PERMISSION_PENDING = "ai_permission_pending";
export const AI_PREPARING = "ai_preparing";
export const AI_SPEAKING = "ai_speaking";
export const LISTENING_PAUSED = "listening_paused";
export const RECONNECTING = "reconnecting";

export type FloorState =
  | typeof DISCONNECTED | typeof LISTENING_IDLE | typeof HUMAN_SPEAKING
  | typeof HUMAN_REFLECTIVE_PAUSE | typeof FLOOR_OPEN | typeof AI_REQUEST_QUEUED
  | typeof AI_PERMISSION_PENDING | typeof AI_PREPARING | typeof AI_SPEAKING
  | typeof LISTENING_PAUSED | typeof RECONNECTING;

export type FloorEvent =
  | "connected" | "disconnected" | "reconnecting"
  | "human_speech_started" | "human_speech_stopped"
  | "reflective_elapsed" | "floor_open_elapsed"
  | "ask_queued" | "permission_requested" | "permission_granted" | "permission_denied"
  | "permission_expired" | "ai_preparing" | "ai_speech_started" | "ai_speech_done"
  | "ai_cancelled" | "listening_paused" | "listening_resumed";

/** Mirrors governor.advance(). Human speech wins from any state — that ordering
 *  is the barge-in guarantee, not a stylistic choice. */
export function advance(state: FloorState, event: FloorEvent): FloorState {
  if (event === "disconnected") return DISCONNECTED;
  if (event === "reconnecting") return RECONNECTING;
  if (event === "connected") {
    return state === DISCONNECTED || state === RECONNECTING ? LISTENING_IDLE : state;
  }
  if (event === "listening_paused") return LISTENING_PAUSED;
  if (event === "listening_resumed") return LISTENING_IDLE;
  if (state === DISCONNECTED || state === LISTENING_PAUSED) return state;

  if (event === "human_speech_started") return HUMAN_SPEAKING;
  if (event === "human_speech_stopped") {
    return state === HUMAN_SPEAKING ? HUMAN_REFLECTIVE_PAUSE : state;
  }
  if (event === "reflective_elapsed") {
    return state === HUMAN_SPEAKING ? HUMAN_REFLECTIVE_PAUSE : state;
  }
  if (event === "floor_open_elapsed") {
    // A change of LABEL only. Reaching it permits nothing.
    return state === HUMAN_REFLECTIVE_PAUSE || state === LISTENING_IDLE ? FLOOR_OPEN : state;
  }
  if (event === "ask_queued") {
    return state === HUMAN_SPEAKING || state === HUMAN_REFLECTIVE_PAUSE
      ? AI_REQUEST_QUEUED : state;
  }
  if (event === "permission_requested") return AI_PERMISSION_PENDING;
  if (event === "permission_granted") return AI_PREPARING;
  if (event === "permission_denied" || event === "permission_expired") {
    return state === AI_PERMISSION_PENDING ? LISTENING_IDLE : state;
  }
  if (event === "ai_preparing") return AI_PREPARING;
  if (event === "ai_speech_started") return AI_SPEAKING;
  if (event === "ai_speech_done" || event === "ai_cancelled") return LISTENING_IDLE;
  return state;
}

export type SpeechKind = "invited" | "queued_ask" | "unsolicited" | "permission_granted";

export interface LocalSpeechRequest {
  kind: SpeechKind;
  mode: ConsultationMode;
  modes: ModeInfo[];
  policy: FloorPolicy;
  floorState: FloorState;
  muted: boolean;
  listeningPaused: boolean;
  connected: boolean;
  msSinceHumanSpeechEnded: number | null;
  msSinceSessionStart: number;
  permissionPending: boolean;
  observation?: {
    importance: number;
    shouldRequestFloor: boolean;
    status: string;
    stateRevision: number;
  } | null;
  currentRevision?: number;
}

export interface LocalDecision {
  allowed: boolean;
  action: "speak" | "request_permission" | "wait" | "refuse";
  code: string;
  reason: string;
  retryAfterMs: number | null;
}

const refuse = (code: string, reason: string): LocalDecision =>
  ({ allowed: false, action: "refuse", code, reason, retryAfterMs: null });
const wait = (code: string, reason: string, retryAfterMs: number | null = null): LocalDecision =>
  ({ allowed: false, action: "wait", code, reason, retryAfterMs });

export function evaluate(req: LocalSpeechRequest): LocalDecision {
  const mode = req.modes.find((m) => m.id === req.mode);
  const speaks = mode ? mode.speaks : true;
  const unsolicited = mode ? mode.unsolicited : false;
  const p = req.policy;

  // 1. Categorical. Answered without ever looking at the floor, so no amount of
  //    conversational evidence can reach these paths.
  if (!speaks) {
    return refuse("scribe_mode", "This session is in scribe mode: she never speaks.");
  }
  if (req.muted) return refuse("muted", "She is muted. She is still listening.");
  if (req.listeningPaused) {
    return refuse("listening_paused", "Listening is paused, so there is nothing to answer.");
  }
  if (!req.connected || req.floorState === DISCONNECTED || req.floorState === RECONNECTING) {
    return refuse("not_connected", "Not connected to the realtime service.");
  }

  // 2. A human holds the floor. Nothing gets past this.
  if (req.floorState === HUMAN_SPEAKING) return wait("human_speaking", "Someone is speaking.");
  if (req.floorState === AI_SPEAKING && req.kind !== "permission_granted") {
    return wait("already_speaking", "She already has the floor.");
  }

  const since = req.msSinceHumanSpeechEnded;

  // 3. Someone asked. Only the floor is still in question.
  if (req.kind === "invited" || req.kind === "queued_ask" || req.kind === "permission_granted") {
    const grace = req.kind === "queued_ask" ? p.queued_ask_grace_ms : p.invited_grace_ms;
    if (since !== null && since < grace) {
      return wait("grace", "Waiting a moment in case the speaker continues.", grace - since);
    }
    return { allowed: true, action: "speak", code: "invited",
             reason: "A person asked her to speak.", retryAfterMs: null };
  }

  // 4. Unsolicited. Everything from here is restraint.
  if (!unsolicited) return refuse("mode_no_unsolicited", "In this mode she only speaks when asked.");
  if (req.permissionPending || req.floorState === AI_PERMISSION_PENDING) {
    return refuse("permission_pending", "She has already asked for the floor and is waiting.");
  }
  if (req.msSinceSessionStart < p.unsolicited_warmup_ms) {
    return wait("warmup", "Too early in the session to offer anything unasked.",
                p.unsolicited_warmup_ms - req.msSinceSessionStart);
  }

  // The observation is what makes an intervention possible — never the clock.
  const obs = req.observation;
  if (!obs || !obs.shouldRequestFloor) {
    return refuse("nothing_to_say",
                  "Nothing has been noticed that is worth interrupting for.");
  }
  if (obs.status !== "open") {
    return refuse("observation_not_open", "That observation has already been dealt with.");
  }
  const threshold = p.min_importance[req.mode] ?? p.min_importance.facilitator ?? 0.75;
  if (obs.importance < threshold) {
    return refuse("below_threshold",
                  "What was noticed is not important enough to interrupt for.");
  }
  if (req.currentRevision !== undefined &&
      req.currentRevision - obs.stateRevision > p.stale_revisions) {
    return refuse("stale", "The consultation has moved on since that was noticed.");
  }

  // The floor, checked LAST, and only ever able to withhold.
  if (since === null) return wait("floor_unknown", "Waiting for a natural opening.");
  if (since < p.floor_open_ms) {
    return wait("reflective_pause",
                "Someone is thinking. A pause belongs to the person who paused.",
                p.floor_open_ms - since);
  }
  return { allowed: true, action: "request_permission", code: "may_request_floor",
           reason: "She may briefly ask whether what she noticed would help.",
           retryAfterMs: null };
}

// ── Direct address ──────────────────────────────────────────────────────────
// Conservative, and the same shape as the Python. "I think AI is going to
// transform education" is meeting content, not a command.

const WAKE = "(?:abigail|ai|assistant|consultation assistant)";
const LEAD = "(?:hey|ok|okay|so)\\s+";
const ASK_VERB =
  "(?:can|could|would|will|please|what|which|where|when|how|why|who|summar\\w+|tell|give|" +
  "help|find|list|remind|show|read|do|is|are|any)";
const DIRECT_ADDRESS = new RegExp(
  `(?:^|[.!?]\\s+)(?:${LEAD})?${WAKE}\\s*(?:,|:|\\s+${ASK_VERB}\\b)`, "i");

export function isDirectAddress(text: string): boolean {
  return !!text && DIRECT_ADDRESS.test(text.trim());
}

const YES = /^\s*(?:yes|yeah|yep|sure|ok|okay|go ahead|please do|please|go on|do it|let's hear it|i'd like that|that would help)\b/i;
const NO = /^\s*(?:no|nope|not now|not yet|later|hold on|hold off|let's not|maybe later|wait)\b/i;

/** true = yes, false = no, null = not an answer. Ambiguity is never consent. */
export function permissionAnswer(text: string): boolean | null {
  if (!text) return null;
  if (YES.test(text)) return true;
  if (NO.test(text)) return false;
  return null;
}

/** What the room is shown. Silence has to look deliberate, never like a crash. */
export const STATE_LABELS: Record<string, string> = {
  [DISCONNECTED]: "Not connected",
  [RECONNECTING]: "Reconnecting",
  [LISTENING_PAUSED]: "Listening paused",
  [LISTENING_IDLE]: "Listening silently",
  [HUMAN_SPEAKING]: "Someone is speaking",
  [HUMAN_REFLECTIVE_PAUSE]: "Reflective pause — she will not interrupt",
  [FLOOR_OPEN]: "Listening silently",
  [AI_REQUEST_QUEUED]: "Will answer when the floor is free",
  [AI_PERMISSION_PENDING]: "Waiting for permission",
  [AI_PREPARING]: "Preparing a short response",
  [AI_SPEAKING]: "Speaking",
};
