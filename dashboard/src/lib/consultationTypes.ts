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
  /** The concept map's vocabulary — colours and labels served from the API
   *  rather than duplicated here (rule 87's reasoning: two copies of a legend
   *  disagree eventually). */
  graph_schema_version: number;
  node_kinds: GraphNodeKindInfo[];
  edge_relations: GraphRelationInfo[];
}

export interface GraphNodeKindInfo {
  id: GraphNodeKind;
  label: string;
  plural: string;
  color: string;
}

export interface GraphRelationInfo {
  id: GraphRelation;
  label: string;
  style: "solid" | "dashed" | "dotted";
  hierarchy: boolean;
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
  /** The record a person reviewed and approved, which is a different thing from
   *  the draft above (rule 102). */
  approved_report?: string;
  record?: RecordStatus;
  /** Recording files a deletion could not remove, so it can be retried and
   *  SEEN rather than swallowed (rule 103). */
  cleanup_pending?: string;
  /** Cursors a delta poll starts from (rule 115). */
  turns_rev?: number;
  record_revision?: number;
  deletion_generation?: number;
  open_threads: OpenThreads;
  note?: string;
  deleted?: TranscriptDeletion;
  /** An immutable map snapshot exists from the last approval, distinct from
   *  whatever the live map has become since (section 2). */
  has_approved_graph?: boolean;
  /** Persists across a page reload, unlike a one-off response `note` — set
   *  when the closing analysis pass did not finish, cleared by a successful
   *  `finish-analysis` retry (section 4). Empty means nothing outstanding. */
  final_pass_note?: string;
}

/** Is what you would export the thing somebody actually approved? */
export interface RecordStatus {
  approved: boolean;
  stale: boolean;
  revision: number;
  approved_revision: number;
  approved_at: string | null;
  note: string;
}

/** What a transcript deletion actually removed and actually kept (rule 103). */
export interface TranscriptDeletion {
  deleted: boolean;
  turns: number;
  audio_files: number;
  observations: number;
  map_items_removed: number;
  map_items_kept: number;
  cleanup_failed: string[];
  kept: string[];
  removed: string[];
}

/** A bounded poll: only what changed (rule 115). */
export interface ConsultationUpdates {
  resync: boolean;
  reason?: string;
  changed: boolean;
  turns_rev: number;
  turns_total: number;
  turns_source: string;
  turns: ConsultationTurn[];
  more: boolean;
  state_revision: number;
  record_revision: number;
  deletion_generation: number;
  transcript_deleted: boolean;
  has_diarized: boolean;
  state?: ConsultationStateMap;
  open_threads?: OpenThreads;
  decisions?: ConsultationDecision[];
  confirmed_decisions?: ConsultationDecision[];
  action_items?: ConsultationAction[];
  participants?: ConsultationParticipant[];
  writings?: VerifiedWriting[];
  record?: RecordStatus;
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

// ── The concept map ──────────────────────────────────────────────────────
//
// Mirrors `agents/live_consultation_graph.py`. The graph is DERIVED, never a
// second copy of a decision's text or an action's owner: `record_ref` points
// back at the canonical map item (or `null` for the root/a fallback bucket),
// and `label` is a display-only truncation computed fresh on every read —
// `detail` always carries the exact, currently-approved wording.

export type GraphNodeKind =
  | "root" | "theme" | "fact" | "assumption" | "principle" | "concern" | "idea"
  | "agreement" | "tension" | "question" | "investigate" | "synthesis"
  | "decision" | "action" | "bucket";

export type GraphRelation =
  | "contains" | "supports" | "challenges" | "depends_on" | "addresses"
  | "leads_to" | "related_to";

export interface GraphNode {
  id: string;
  kind: GraphNodeKind;
  /** A short DISPLAY truncation — never the whole story. Read `detail` for the
   *  exact, currently-approved wording. */
  label: string;
  detail: string;
  status: string | null;
  status_label: string | null;
  human_edited: boolean;
  source_turn_ids: string[];
  /** Which list and item this node is derived from, so a correction goes
   *  through the EXISTING map-item / decision / action endpoints — there is no
   *  separate "edit this node" write path. `null` for the root and a fallback
   *  category bucket, neither of which is a real map item. */
  record_ref: { list: string; id: string } | null;
  /** Kind-specific authoritative fields (an action's owner/due/acceptance, a
   *  decision's rationale/retained concerns, a fact's evidence note). */
  extra: Record<string, unknown>;
  origin: "model" | "human" | "root" | "fallback_grouping";
  /** False for a decision/action built directly from its canonical row with
   *  no working-map item behind it (a human-created action, or one whose map
   *  item was stripped by transcript deletion) — merge and delete-map-item
   *  both operate on map items, so the UI has to know which nodes have one. */
  has_map_item: boolean;
  x: number;
  y: number;
  pinned: boolean;
  collapsed: boolean;
}

export interface GraphEdge {
  id: string;
  from_id: string;
  to_id: string;
  relation: GraphRelation;
  label: string;
  /** "hierarchy" is the tree (`contains`, theme/root only); everything else is
   *  a cross-link and does not affect layout. */
  kind: "hierarchy" | "cross";
  inferred: boolean;
  human_edited: boolean;
  source_turn_ids: string[];
  /** True for an implicit root→theme or root→orphan attachment that is never
   *  a stored row — drawn so the tree has no dangling branch, not a claim
   *  anyone made a connection. */
  synthetic: boolean;
}

export interface ConceptGraph {
  schema_version: number;
  session_id: string;
  /** = the consultation map's own `state_revision`. */
  content_revision: number;
  graph_revision: number;
  view_revision: number;
  /** A decision's status, an action's owner/acceptance, and a semantic
   *  connection a human just drew or rejected can all change WITHOUT moving
   *  any of the three revisions above — confirming a decision or accepting an
   *  action only bumps this one. Include it in any client-side resync key or
   *  a correction can sit unrefreshed on screen. */
  record_revision: number;
  /** True when there are no themes and no extracted connections yet, so the
   *  tree shown is CATEGORY grouping (a fallback), not something the group
   *  discussed — the screen must say so plainly (section 3). */
  fallback: boolean;
  /** How many real items sit in a provisional (not-yet-themed) bucket even
   *  though this is NOT whole-graph fallback — some themes exist, but these
   *  items are not under one yet. 0 whenever nothing is unplaced, and always
   *  0 in fallback mode itself (which already says so for the whole map). */
  unplaced_count: number;
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface OrganizePreview {
  summary: string[];
  proposed_theme_count: number;
  proposed_edge_count: number;
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
