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
  /** How readily the detector calls a turn finished. Part of the policy because
   *  it is the biggest single contributor to how long she appears to wait. */
  vad_eagerness: string;
  /** The whole code-owned detector block, to be echoed back verbatim on a
   *  mid-meeting change of presence — never rebuilt here (rule 75). */
  turn_detection: Record<string, unknown>;
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
  /** Served rather than duplicated here, for the same reason the floor policy is
   *  (rule 87): two copies of a list of states disagree eventually. */
  retention_policies: RetentionPolicy[];
  default_retention: string;
  closeout_outcomes: { id: string; label: string }[];
  /** `human_only` marks a state no model may ever set — the epistemic boundary
   *  in rules 96 and 95, carried to the UI so it can show which is which. */
  fact_states: VocabEntry[];
  item_lifecycle: VocabEntry[];
  action_statuses: VocabEntry[];
  map_lists: string[];
}

export interface RetentionPolicy {
  id: string;
  label: string;
  blurb: string;
  days: number | null;
}

export interface VocabEntry {
  id: string;
  label: string;
  human_only: boolean;
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
  /** 0 means the meeting is untimed. */
  duration_minutes: number;
  /** How long before the end she gives the spoken time check. */
  warn_minutes: number;
  report_md: string | null;
  report_at: string | null;
  audio_status: "none" | "pending" | "uploaded" | "transcribing" | "done" | "failed";
  audio_note: string;
  retention_policy: string;
  /** When the host attested that the room had been told. Null means they never
   *  did — which is why an old session reads as "not attested" rather than as
   *  consent nobody gave (rule 94). */
  participants_informed_at: string | null;
  closeout_outcome: string | null;
  closeout_note: string;
  closeout_at: string | null;
  reflection_at: string | null;
  transcript_deleted_at: string | null;
}

export interface ConsultationParticipant {
  id: string;
  session_id: string;
  name: string;
  /** The diarised voice ("A", "B") this person turned out to be — null until a
   *  human maps it. Nothing infers it. */
  speaker_key: string | null;
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
  /** Set when a human fixed what the transcription misheard. The original is
   *  deliberately not kept — see `store.correct_turn`. */
  corrected_at?: string | null;
  source?: "live" | "diarized";
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
  /** Rules 96/97: what the item's status actually claims, and what happened to
   *  a concern. Nothing is deleted from the map any more. */
  lifecycle?: string;
  resolution_note?: string;
  resolved_at?: string;
  evidence_note?: string;
  /** A person edited the words. The reasoner may not overwrite them (rule 95). */
  human_edited?: boolean;
  human_reviewed?: boolean;
}

export interface ConsultationStateMap {
  question?: string;
  objective?: string;
  summary?: string;
  /** Short topic labels — what the group has actually talked about. */
  themes?: MapItem[];
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
  /** Concerns the group chose to carry forward WITH the decision rather than
   *  settle. A decision can be confirmed and still leave dissent standing. */
  retained_concerns: string[];
  human_edited?: boolean;
  map_id?: string | null;
}

export type ActionStatus =
  | "proposed" | "accepted" | "in_progress" | "blocked" | "completed" | "dropped";

export interface ConsultationAction {
  id: string;
  session_id: string;
  action: string;
  owner: string | null;
  due: string | null;
  status: ActionStatus;
  created_at: string;
  /** THE distinction (rule 95). `null` = nobody has recorded an answer, which
   *  is a different and more honest thing than `false` = asked, did not accept.
   *  A named owner is a proposal until this says otherwise. */
  owner_accepted: boolean | null;
  accepted_by: string;
  accepted_at: string | null;
  success_criteria: string;
  support_needed: string;
  blocker: string;
  progress_note: string;
  source_decision_id: string;
  human_edited?: boolean;
  map_id?: string | null;
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
  /** A consultation may settle more than one thing; the singular field above is
   *  kept so every old session and existing caller still reads (rule 98). */
  confirmed_decisions: ConsultationDecision[];
  transcript_deleted: boolean;
  action_items: ConsultationAction[];
  writings: VerifiedWriting[];
  speech_events: SpeechEvent[];
  mode_info: Partial<ModeInfo>;
  participants: ConsultationParticipant[];
  /** The speaker-separated transcript once it exists, the live one until then.
   *  `turns` above always stays the live record. */
  final_turns: ConsultationTurn[];
  has_diarized: boolean;
  report: string;
  open_threads: OpenThreads;
  note?: string;
}

/** What the record says is still hanging. The same shape the spoken time check
 *  is built from, so the screen and her voice cannot disagree. */
export interface OpenThreads {
  unresolved: string[];
  questions: string[];
  unconfirmed: string[];
  confirmed: boolean;
  unowned: string[];
}

export interface DiarizeResult {
  turns_written: number;
  turns_labelled: number;
  speakers: string[];
  duration: number;
  cost: number | null;
  final_turns: ConsultationTurn[];
  participants: ConsultationParticipant[];
  session: ConsultationSession;
}

export interface ReportResult {
  report: string;
  note: string;
  narrated: boolean;
  session: ConsultationSession;
}

/** A governor answer that may also carry what to say, and (for the opening) the
 *  passage to put on screen at the same moment. */
export interface ScheduledSpeech {
  allowed: boolean;
  action: string;
  code: string;
  reason: string;
  retry_after_ms: number | null;
  instructions?: string;
  modalities?: string[];
  passage?: string;
  passage_source?: string;
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
