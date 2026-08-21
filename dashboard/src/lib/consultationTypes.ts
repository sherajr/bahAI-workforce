// Live Consultation types — the dashboard's mirror of agents/live_consultation.py.
// Kept out of types.ts because this is a subsystem of its own, and because the
// product pipeline's "consultation" is a different thing entirely.

export type ConsultationMode = "scribe" | "on_request" | "facilitator" | "active";

export interface ModeInfo {
  id: ConsultationMode;
  label: string;
  blurb: string;
  speaks: boolean;
  unsolicited: boolean;
}

export type ConsultationPresence = "reserved" | "attentive" | "present";

export interface PresenceLevel {
  id: ConsultationPresence;
  label: string;
  blurb: string;
  waits: number;
  cooldowns: number;
  importance: number;
}

export interface FloorPolicy {
  presence: ConsultationPresence;
  reflective_pause_ms: number;
  floor_open_ms: number;
  invited_grace_ms: number;
  queued_ask_grace_ms: number;
  unsolicited_warmup_ms: number;
  unsolicited_cooldown_ms: number;
  denied_cooldown_ms: number;
  permission_timeout_ms: number;
  min_importance: Record<string, number>;
  stale_revisions: number;
}

export interface AnalysisPolicy {
  min_new_turns: number;
  min_new_words: number;
  min_interval_s: number;
  recent_window_turns: number;
  model: string;
}

export interface ConsultationCapabilities {
  realtime_available: boolean;
  reasoning_available: boolean;
  reasoning_note: string;
  writings_available: boolean;
  recording_supported: boolean;
  realtime_model: string;
  reasoning_model: string;
  transcribe_model: string;
  voice: string;
  calls_url: string;
  modes: ModeInfo[];
  frameworks: { id: string; label: string }[];
  decision_methods: { id: string; label: string }[];
  presence_levels: PresenceLevel[];
  default_mode: ConsultationMode;
  default_framework: string;
  default_presence: ConsultationPresence;
  assistant_name: string;
  assistant_avatar: string;
  floor_policy: FloorPolicy;
  /** One resolved set of numbers per preset — the browser never computes its
   *  own timings (rule 87). */
  floor_policies: Record<string, FloorPolicy>;
  analysis_policy: AnalysisPolicy;
  floor_states: string[];
  state_labels: Record<string, string>;
  spend: { month_total: number | null; monthly_ceiling: number; over_ceiling: boolean; known: boolean };
  missing_key_message: string;
}

export interface ConsultationSession {
  id: string;
  title: string;
  question: string;
  context: string;
  framework: string;
  mode: ConsultationMode;
  decision_method: string;
  presence: ConsultationPresence;
  status: "draft" | "live" | "ended";
  record_audio: number;
  realtime_model: string | null;
  reasoning_model: string | null;
  transcribe_model: string | null;
  voice: string | null;
  state_revision: number;
  created_at: string;
  started_at: string | null;
  ended_at: string | null;
  turn_count?: number;
  decision_confirmed?: boolean;
}

export interface ConsultationTurn {
  id: number;
  session_id: string;
  realtime_item_id: string | null;
  sequence: number;
  role: "human" | "assistant";
  speaker_label: string | null;
  text: string;
  is_final: number;
  started_at: string | null;
  ended_at: string | null;
  created_at: string;
}

export interface MapItem {
  id: string;
  text?: string;
  note?: string;
  status?: string;
  action?: string;
  owner?: string | null;
  due?: string | null;
  source_turn_ids?: string[];
}

export interface ConsultationStateMap {
  question?: string;
  objective?: string;
  summary?: string;
  facts?: MapItem[];
  assumptions?: MapItem[];
  principles?: MapItem[];
  needs_and_concerns?: MapItem[];
  ideas?: MapItem[];
  agreements?: MapItem[];
  tensions?: MapItem[];
  unresolved_questions?: MapItem[];
  questions_to_investigate?: MapItem[];
  possible_syntheses?: MapItem[];
  decision_candidates?: MapItem[];
  confirmed_decision?: { id?: string; text: string } | null;
  action_items?: MapItem[];
  state_revision: number;
}

export interface ConsultationObservation {
  id: string;
  session_id: string;
  kind: string;
  importance: number;
  summary: string;
  detail: string;
  should_request_floor: number;
  permission_request: string;
  speech_brief: string;
  state_revision: number;
  status: "open" | "dismissed" | "surfaced" | "spoken" | "expired";
  created_at: string;
}

export interface ConsultationDecision {
  id: string;
  session_id: string;
  text: string;
  rationale: string;
  support: string;
  concerns: string[];
  status: "candidate" | "confirmed" | "rejected";
  created_at: string;
  confirmed_at: string | null;
}

export interface ConsultationAction {
  id: string;
  session_id: string;
  action: string;
  owner: string | null;
  due: string | null;
  status: "open" | "done";
  created_at: string;
}

export interface VerifiedWriting {
  id: string;
  session_id: string;
  text: string;
  source: string;
  section: string;
  link: string;
  theme: string;
  score: number;
  created_at: string;
}

export interface SpeechEvent {
  id: number;
  session_id: string;
  kind: string;
  allowed: number;
  reason: string;
  observation_id: string | null;
  created_at: string;
}

export interface ConsultationDetail {
  session: ConsultationSession;
  state: ConsultationStateMap;
  turns: ConsultationTurn[];
  observations: ConsultationObservation[];
  decisions: ConsultationDecision[];
  confirmed_decision: ConsultationDecision | null;
  action_items: ConsultationAction[];
  writings: VerifiedWriting[];
  speech_events: SpeechEvent[];
  mode_info: Partial<ModeInfo>;
  note?: string;
}

export interface AnalysisResult {
  ran: boolean;
  ok?: boolean;
  note?: string;
  why?: string;
  state?: ConsultationStateMap;
  observations?: ConsultationObservation[];
  turns_analyzed?: number;
  writings?: { theme: string; available: boolean; note: string; passages: VerifiedWriting[] };
  merge_notes?: string[];
}

/** The governor's answer. `allowed` is the only thing that may start speech. */
export interface SpeechDecision {
  allowed: boolean;
  action: "speak" | "request_permission" | "wait" | "refuse";
  code: string;
  reason: string;
  retry_after_ms: number | null;
  checks: string[];
  say?: string;
  instructions?: string;
  modalities?: string[];
  observation_id?: string;
}

export interface RealtimeCredential {
  client_secret: string;
  expires_at: number;
  calls_url: string;
  model: string;
  voice: string;
  turn_detection: Record<string, unknown>;
  session_id: string;
  spend: ConsultationCapabilities["spend"];
}
