// TypeScript interfaces matching the FastAPI responses (agents/api.py).

export interface Listing {
  title: string;
  description: string;
  bookmark_quote?: string;
  tags: string[];
  materials: string[];
  price_note: string;
  // false only after the quote was hand-edited via manual edit (no longer
  // Librarian-verified); absent/true for pipeline-produced quotes.
  quote_verified?: boolean;
}

export interface PrincipleScore {
  score: number;
  note: string;
}

export interface Review {
  scores: Record<string, PrincipleScore>; // keys like "1_work_as_worship"
  overall: number;
  passed: boolean;
  recommendation: string;
  // Diagnostic only — never factor into `overall`. Absent when the Reviewer's
  // output didn't include a valid value for it.
  image_fit?: number;
  quote_quality?: number;
}

export interface ConsultationTurn {
  agent: string; // Artist | Scribe | Reviewer | Librarian | System
  role: string;
  message: string;
  // Optional rendered image attached to the turn — currently the front-face
  // preview on the Reviewer's ask-for-input turn at the post-round-2 pause.
  image?: string | null;
  // Card runs also attach the back-face preview (2026-07-16 — the back
  // carries the artwork now, so Sheraj steers from both faces).
  image_back?: string | null;
}

// A language the card pipeline can translate into (GET /card/languages).
export interface CardLanguage {
  code: string;
  name: string;
  native_name: string;
}

// translator.translate_quote()'s dict, as returned inside a card PipelineResult.
export interface CardTranslation {
  code: string;
  name: string;
  native_name: string;
  rtl: boolean;
  text: string;
  disclaimer_native: string;
  disclaimer_en: string;
}

// listing_copy JSON for product_type === "quote_card" rows.
export interface CardCopy {
  product_kind: "quote_card";
  quote: string;
  quote_grounded: boolean;
  citation: string;
  language: string | null;
  language_name: string | null;
  translation_text: string | null;
  translation_disclaimer_native: string | null;
  translation_disclaimer_en: string | null;
  // Fixed, code-written AI-artwork disclosure (absent on cards saved before it existed).
  artwork_disclosure?: string | null;
  variant_faces?: Record<string, { front: string; back: string }>;
  reflection_question?: string;
  reflection_action?: string;
  reflection_native?: { question?: string; action?: string } | null;
  // Quote provenance (rule 11 update 2026-08-04; absent on older cards =
  // Ruhi Book 1). quote_verified is false ONLY for the risky web tier.
  quote_verified?: boolean;
  quote_provenance?: string;   // "ruhi_book1" | "lib:<slug>" | "web:<url>"
  quote_sources?: string[];
}

// One selectable quote source for the card form (GET /quote-sources).
export interface QuoteSourceOption {
  id: string;           // "ruhi_book1" | "lib:<slug>"
  name: string;
  kind: "verified";     // the risky web option is a client-side row, not listed
  default: boolean;
}

export interface PipelineResult {
  task_id: string;
  product_id: string;
  theme: string;
  image_prompt: string;
  image_path: string;
  image_web: string;
  front_image_path: string;
  front_image_web: string;
  back_image_path: string;
  back_image_web: string;
  compositor_error: string | null;
  // Bookmark runs only — absent on quote-card runs.
  listing?: Listing;
  canva?: { skipped?: boolean; reason?: string; design_url?: string | null };
  // Quote-card runs only — absent on bookmark runs.
  product_type?: string;
  language?: string | null;
  language_name?: string | null;
  quote?: string;
  quote_grounded?: boolean;
  // Provenance of the printed quote (rule 11 update 2026-08-04).
  quote_verified?: boolean;
  quote_provenance?: string;
  citation?: string;
  translation?: CardTranslation | null;
  // Per-language card pairs (quote-card runs), passed explicitly so the
  // results panel never depends on a stale products cache.
  variant_faces?: Record<string, { front: string; back: string }>;
  review: Review;
  attempts: number;
  target_reached: boolean;
  badge: string;
  consultation: ConsultationTurn[];
}

// One card's outcome inside a CardBatchResult (POST /pipeline/run-card-batch).
export interface CardBatchItem {
  index: number;
  status: "done" | "error";
  quote: string;
  error?: string;
  citation?: string;
  product_id?: string;
  task_id?: string;
  front_image_web?: string;
  back_image_web?: string;
  variant_faces?: Record<string, { front: string; back: string }>;
  overall?: number | null;
  badge?: string;
  attempts?: number;
  target_reached?: boolean;
}

// One Librarian quote suggestion (GET /ruhi-quotes): text from a SELECTED
// source, canonicalized server-side so a verified-tier item passes the batch
// endpoint's verification exactly as returned. `shortened` marks a passage
// the server trimmed at a sentence boundary (with ". . .") to fit the card.
// `verified` is false ONLY for risky web-tier items (origin "web:<url>") —
// their wording is fetched, not machine-verified.
export interface RuhiQuoteSuggestion {
  quote: string;
  source: string;
  score: number | null;
  shortened: boolean;
  origin: string;      // "ruhi_book1" | "lib:<slug>" | "web:<url>"
  verified: boolean;
}

export interface RuhiQuoteSuggestResult {
  topic: string;
  requested: number;
  items: RuhiQuoteSuggestion[];
  skipped_too_long: number;
  web_note?: string | null;
}

// Result of a hands-free multi-quote batch job. Batch runs skip the
// consultation's mid-run pause, so a batch job never enters
// status "waiting_for_input". Discriminated from PipelineResult by `batch`.
export interface CardBatchResult {
  batch: true;
  product_type: "quote_card_batch";
  theme: string;
  language: string | null;
  language_name?: string | null;
  total: number;
  completed: number;
  failed: number;
  items: CardBatchItem[];
}

// ── Visual layout editor ──────────────────────────────────────────────────────
// Presentation-only knobs for a product face (agents/layout.py). Never carries
// any text — the printed quote/citation/translation/disclaimers come from the
// product's stored data at render time, so the editor can't rewrite them.
export interface ProductLayout {
  font: string;
  text_scale: number;
  text_color: string;
  // bookmark-only
  text_offset?: number;
  gradient?: number;
  show_star?: boolean;
  show_rule?: boolean;
  // card-only
  vignette?: number;
}

export interface LayoutChoice {
  key: string;
  label: string;
}

export interface LayoutRange {
  min: number;
  max: number;
  step: number;
}

// GET /products/{id}/layout
export interface LayoutOptions {
  product_id: string;
  product_type: string;
  current: ProductLayout;
  has_saved: boolean;
  defaults: ProductLayout;
  fonts: LayoutChoice[];
  colors: LayoutChoice[];
  ranges: Record<string, LayoutRange>;
}

// POST /products/{id}/layout and .../layout/preview
export interface LayoutRenderResult {
  product_id?: string;
  front_image_web: string;
  back_image_web: string;
  layout: ProductLayout;
}

export interface JobStep {
  ts: string;
  message: string;
}

// Fields shared by every background job regardless of its result payload
// shape (bookmark/card pipelines return PipelineResult; x-post jobs return
// XPostJobResult) — see Job<TResult> below.
export interface JobBase {
  job_id: string;
  kind: string;
  status: "running" | "waiting_for_input" | "done" | "error";
  progress: string;
  steps: JobStep[];
  // Consultation turns streamed live as they happen (round 1, round 2, then
  // the Reviewer's pause-for-input turn and Sheraj's reply if given) — lets
  // the dashboard render the consultation as a live chat while the job runs.
  consultation_live?: ConsultationTurn[];
  // Set while status is "waiting_for_input": what the Reviewer is asking Sheraj.
  pending_prompt?: string | null;
  error: string | null;
  created_at: string;
  updated_at: string;
}

export interface Job<TResult = PipelineResult> extends JobBase {
  result: TResult | null;
}

export interface JobSummary extends JobBase {
  has_result: boolean;
}

// Row from the products table. listing_copy and reviewer_scores are JSON strings.
export interface ProductRow {
  id: string;
  task_id: string | null;
  title: string | null;
  status: string | null;
  etsy_listing_id: string | null;
  image_url: string | null;
  listing_copy: string | null;
  reviewer_scores: string | null;
  revenue: number | null;
  created_at: string | null;
  image_prompt: string | null;
  theme: string | null;
  front_image: string | null;
  back_image: string | null;
  consultation: string | null;
  product_type: string | null; // "bookmark" (default) | "quote_card"
  // 1 = review target reached; 0 = shipped as best effort (stall/max attempts);
  // null = saved before this was tracked.
  target_reached?: number | null;
  attempts?: number | null;
  // Sheraj's note on how the product landed with a real person.
  recipient_feedback?: string | null;
}

export interface AgentStatus {
  name: string;
  trust_level: number;
  trust_score: number;
  total_runs: number;
  clean_runs: number;
  consecutive_failures: number;
  trust_level_name: string;
}

export interface TrustReportRow {
  product_id: string;
  title: string;
  status: string;
  created_at: string;
  overall: number;
  passed: boolean;
  badge: string; // "BEST EFFORT" when the product shipped below its target score
  target_reached?: number | null;
  attempts?: number | null;
  recommendation: string;
  principle_scores: Record<string, PrincipleScore>;
}

export interface TrustReport {
  total: number;
  passed: number;
  rejected: number;
  average_score: number;
  products: TrustReportRow[];
}

export interface RecentDeed {
  id: number;
  product_id: string | null;
  kind: "gift" | "gathering" | "digital";
  count: number;
  note: string;
  created_at: string;
  product_title?: string | null;
}

export interface DeedsReport {
  cards_gifted: number;
  gatherings_served: number;
  digital_shares: number;
  feedback_count: number;
  recent: RecentDeed[];
}

export interface StewardReport {
  deeds: DeedsReport;
  total_products: number;
  total_revenue: number;
  // Hybrid costs: runs since metering shipped are metered per call
  // (state.record_spend); older products carry a flat labeled estimate
  // (legacy_estimated_costs) instead of a misleading $0.
  estimated_costs: number;
  estimated_profit: number;
  cost_per_product: number;
  month_spend: number;
  monthly_ceiling: number;
  over_ceiling: boolean;
  spend_by_kind: Record<string, number>;
  legacy_products: number;
  legacy_estimated_costs: number;
  products: {
    id: string;
    title: string | null;
    status: string | null;
    revenue: number;
    etsy_listing_id: string | null;
    created_at: string | null;
  }[];
  error?: string;
}

export interface CanvaStatus {
  authorised: boolean;
  template_id: string;
  template_fields?: unknown;
  template_fields_error?: string;
}

export interface EtsyStatus {
  configured: boolean;
  authorised: boolean;
  shop_id: string | null;
}

export interface ImproveResult {
  product_id: string;
  improved: boolean;
  old_score: number;
  new_score: number;
  target_reached: boolean;
  attempts: number;
  listing: Listing;
  review: Review;
}

export interface RegenerateQuoteResult {
  product_id: string;
  old_quote: string;
  new_quote: string;
  source: string;
  old_score: number;
  new_score: number;
  listing: Listing;
  review: Review;
  front_image_web: string;
  back_image_web: string;
}

export interface RegenerateImageResult {
  product_id: string;
  old_score: number;
  new_score: number;
  listing: Listing;
  review: Review;
  image_web: string;
  front_image_web: string;
  back_image_web: string;
}

// Quote card "redirect the team" — same idea as bookmarks above, but no
// listing text exists, so these carry a card `review` rubric instead.
export interface RegenerateCardQuoteResult {
  product_id: string;
  old_quote: string;
  new_quote: string;
  citation: string;
  old_score: number;
  new_score: number;
  review: Review;
  front_image_web: string;
  back_image_web: string;
}

export interface RegenerateCardImageResult {
  product_id: string;
  old_score: number;
  new_score: number;
  review: Review;
  image_web: string;
  front_image_web: string;
  back_image_web: string;
}

// All fields optional — only the ones the user actually changed are sent.
export interface EditProductPayload {
  title?: string;
  description?: string;
  bookmark_quote?: string;
  tags?: string[];
  materials?: string[];
  price_note?: string;
}

export interface EditProductResult {
  product_id: string;
  listing: Listing;
  // false when the hand edit changed the quote (no longer Librarian-verified).
  quote_verified?: boolean;
  // set if the printed face couldn't be re-rendered after a quote change.
  rerender_note?: string | null;
}

export interface EtsyPublishResult {
  product_id?: string;
  etsy_listing_id?: string;
  state?: string;
  url?: string | null;
  image_uploaded?: boolean;
  image_error?: string | null;
  skipped?: boolean;
  reason?: string;
  // Trust gate: the Reviewer hasn't earned Human-on-the-loop yet, so the
  // dashboard must ask Sheraj to confirm and retry with confirm=true.
  requires_confirmation?: boolean;
  trust_level?: number;
  trust_level_name?: string;
}

// ── Secretary (Phase 1: chat + private memory) ────────────────────────────────
// Privacy: message content renders ONLY inside the Secretary tab.

export interface SecretaryMessage {
  role: "user" | "assistant";
  content: string;
  channel: string;
  ts: string;
}

export interface SecretaryChatResult {
  reply: string;
  remembered: string[];
  tasks_added: string[];
  actions: string[];
}

export interface SecretaryStatus {
  enabled: boolean;
  model: string;
  notes: number;
  open_tasks: number;
  // One shared Google connection (Calendar + Gmail/Drive/Docs/Sheets/
  // Slides-read) — see agents/google_auth.py.
  google_configured: boolean;
  google_authorised: boolean;
  whatsapp_configured: boolean;
  pending_reminders: number;
  pending_approvals: number;
}

export interface SecretaryEvent {
  id: string;
  summary: string;
  start: string;
  end: string;
  all_day: boolean;
  location: string;
  calendar_id: string;
  calendar_name: string;
  tags: string[];
  editable_by_secretary: boolean;
}

export interface BadiEvent {
  date: string;
  name: string;
  kind: "holy_day" | "feast";
  work_suspended: boolean;
}

export interface SecretaryReminder {
  id: number;
  message: string;
  fire_at: string;
  recurrence: string | null;
  wake_me: number;
}

export interface SecretaryUpcoming {
  events: SecretaryEvent[];
  badi_events: BadiEvent[];
  reminders: SecretaryReminder[];
  badi_source: string;
}

export interface SecretaryNotification {
  id: number;
  kind: string;
  title: string;
  created_at: string;
}

export interface PendingApproval {
  id: number;
  kind: string;
  description: string;
  created_at: string;
}

export interface GoogleStatus {
  configured: boolean;
  authorised: boolean;
  secretary_calendar: string | null;
}

export interface WhatsAppStatus {
  configured: boolean;
  owner_number_set: boolean;
}

export interface Contact {
  id: number;
  name: string;
  phone: string;
  allowlisted: number;
  last_inbound_at: string | null;
  created_at: string;
}

export interface NoteRow {
  name: string;
  content: string;
}

export interface TaskRow {
  id: number;
  description: string;
  due: string | null;
  done: number;
  created_at: string;
}

export interface ReminderRow {
  id: number;
  message: string;
  fire_at: string;
  recurrence: string | null;
  wake_me: number;
  fired: number;
  created_at: string;
}

// ── Post to X (@peaceAntz) — giveaway outreach, never sold, never auto-posted ─
// A background job like the bookmark/card pipelines: the team's consultation
// (agents/consultation.py, product="x_post") includes the same round-2 human
// pause, so POST /x-post returns {job_id} and the dashboard polls/responds
// exactly the way PipelinePanel does.

// Reviewer QA's deterministic mechanical checks (agents/x_post.py review_tweet).
export interface XPostReview extends Review {
  checks?: Record<string, { ok: boolean; detail: string }>;
}

// The x-post job's `result` payload once status is "done" (see
// api._run_x_post_job) — the draft is already saved to pending_x_posts by
// this point, keyed by `id`.
export interface XPostJobResult {
  id: string;
  topic: string;
  tweet_text: string;
  image_path: string | null;
  image_web: string | null;
  // false: an original reflection — inspired by retrieved passages, but
  // nothing is quoted or attributed in the tweet itself.
  include_quote: boolean;
  quote_locked: string;
  quote_author: string;
  citation: string;
  inspired_by: string;
  attempts: number;
  review: XPostReview;
  consultation: ConsultationTurn[];
}

// Row from pending_x_posts (GET /x-post/pending and GET /x-post/posted).
export interface PendingXPost {
  id: string;
  topic: string | null;
  tweet_text: string | null;
  image_path: string | null;
  image_web: string | null;
  image_prompt: string | null;
  quote_locked: string | null;
  quote_author: string | null;
  // 1: tweet weaves in an unaltered locked quote (default/legacy rows).
  // 0: an original reflection — inspired by inspired_by, nothing quoted.
  include_quote: number;
  inspired_by: string | null;
  constitution_score: number | null;
  status: string;
  created_at: string | null;
  posted_tweet_id: string | null;
  // Only present on GET /x-post/posted rows — reconstructed from
  // posted_tweet_id, null for a dry-run post that never really went out.
  posted_url?: string | null;
}

export interface XPostApproveResult {
  id: string;
  status: string;
  dry_run: boolean;
  posted_tweet_id: string | null;
  url: string | null;
  text: string | null;
}

export interface XPostEditResult {
  id: string;
  tweet_text: string;
}

export interface XPostRegenerateImageResult {
  id: string;
  image_path: string | null;
  image_web: string | null;
}

export interface XPostStatusResult {
  id: string;
  status: string;
}

// ── Video Generation pipeline (agents/api.py, /video/*) ──────────────────────
// Turns a scene, story, historical account or passage into many simple 3-4
// second shots. Bookmarks and quote cards are secondary source options.

export interface VideoDirection {
  target_seconds: number;
  aspect_ratio: string;
  visual_style: string;
  historical_period: string;
  setting: string;
  mood: string;
  color_palette: string;
  audience: string;
  narration: string;
  on_screen_text: string;
  shot_seconds: number;
  /** "standard" fills the target length; "cinematic" plans fewer, longer,
   *  non-overlapping moments and cuts only where the story changes. */
  pacing?: "standard" | "cinematic";
  provider: string;
  low_resource: boolean;
}

export interface PacingOption {
  id: string;
  label: string;
  description: string;
}

export interface VideoBeat {
  id: string;
  title: string;
  summary: string;
  emotion?: string;
  suggested_shots?: number;
  /** How many genuinely different things a viewer would SEE happen in the
   *  beat — the ceiling on its shot count under cinematic pacing. */
  distinct_moments?: number;
}

export interface SacredFlags {
  figures: string[];
  has_reference: boolean;
  depiction_risk: boolean;
}

export interface VideoAnalysis {
  summary: string;
  central_message: string;
  characters: { id: string; name: string; description?: string; role?: string }[];
  locations: { id: string; name: string; description?: string }[];
  props: { id: string; name: string; description?: string }[];
  beats: VideoBeat[];
  emotional_progression: string;
  narration_notes: string;
  continuity_risks: string[];
  do_not_depict_literally: string[];
  sacred_flags?: SacredFlags;
}

export interface ContinuityCharacter {
  id: string; name: string; appearance?: string; age?: string; hair?: string;
  clothing?: string; colors?: string; accessories?: string; relationships?: string;
}
export interface ContinuityLocation {
  id: string; name: string; architecture?: string; geography?: string;
  time_of_day?: string; weather?: string;
}
export interface ContinuityProp { id: string; name: string; description?: string }

export interface ContinuityBible {
  characters: ContinuityCharacter[];
  locations: ContinuityLocation[];
  props: ContinuityProp[];
  style: Record<string, string>;
  locked: string[];
}

export interface VideoShotData {
  beat_id?: string;
  duration?: number;
  narrative_purpose?: string;
  subject?: string;
  primary_action?: string;
  setting?: string;
  time_of_day?: string;
  framing?: string;
  camera_angle?: string;
  camera_movement?: string;
  lighting?: string;
  mood?: string;
  character_ids?: string[];
  location_ids?: string[];
  prop_ids?: string[];
  first_frame_prompt?: string;
  last_frame_prompt?: string;
  motion_prompt?: string;
  negative_prompt?: string;
  narration?: string;
  dialogue?: string;
  sound_notes?: string;
  transition?: string;
  continuity_notes?: string;
  continuity_mode?: string;
  complexity_score?: number;
  sacred_treatment?: { figures: string[]; treatment: string; rule: string };
  // Detail fields — raise render quality without adding shot complexity.
  subject_detail?: string;
  setting_detail?: string;
  texture_notes?: string;
  atmosphere?: string;
  depth_notes?: string;
  lens?: string;
  // Set when the Director failed on this beat and a placeholder was inserted
  // so the rest of the plan survived.
  needs_replanning?: boolean;
}

export interface VideoShot {
  id: string;
  project_id: string;
  shot_number: number;
  beat_id: string | null;
  data: VideoShotData;
  locked_fields: string[];
  status: string;
  approved: boolean;
  continuity_mode: string;
  first_frame_path: string | null;
  last_frame_path: string | null;
  clip_path: string | null;
  first_frame_url: string;
  last_frame_url: string;
  clip_url: string;
  error: string | null;
}

export interface VideoProject {
  id: string;
  task_id: string | null;
  title: string;
  status: string;
  stage: string;
  source_kind: string;
  source_text: string;
  source_brief: string;
  source_instructions: string;
  source_product_id: string | null;
  direction: VideoDirection | null;
  analysis: VideoAnalysis | null;
  continuity: ContinuityBible | null;
  safety: { source_scan?: SacredFlags; notes?: string[] } | null;
  export: VideoExportResult | null;
  error: string | null;
  created_at: string;
  updated_at: string;
  shot_count?: number;
}

export interface VideoResumeState {
  shots: number;
  needs_frames: string[];
  needs_clips: string[];
  failed: string[];
  frames_job: Record<string, unknown> | null;
  clips_job: Record<string, unknown> | null;
  complete: boolean;
}

export interface VideoProjectDetail {
  project: VideoProject;
  shots: VideoShot[];
  resume: VideoResumeState;
}

export interface VideoProviderCaps {
  id: string;
  provider?: string;
  model?: string;
  label?: string;
  available: boolean;
  unavailable_reason?: string;
  text_to_video?: boolean;
  image_to_video?: boolean;
  first_last_frame?: boolean;
  first_last_frame_note?: string;
  max_seconds?: number;
  width?: number;
  height?: number;
  fps?: number;
  typical_seconds_per_clip?: number;
  is_mock?: boolean;
}

export interface VideoProvidersResult {
  providers: VideoProviderCaps[];
  default: string;
  strategies: Record<string, string>;
  ffmpeg: boolean;
}

export interface VideoDefaults {
  direction: VideoDirection;
  min_shot_seconds: number;
  max_shot_seconds: number;
  complexity_limit: number;
  aspect_ratios: string[];
  visual_styles: string[];
  narration_options: string[];
  pacing_options?: PacingOption[];
}

export interface VideoPlanResult {
  project_id: string;
  analysis: VideoAnalysis;
  continuity: ContinuityBible;
  shot_count: number;
  notes: string[];
  safety: { source_scan?: SacredFlags; notes?: string[] };
  estimated_seconds: number;
}

export interface VideoGenerateResult {
  job_id: string;
  done: number;
  total: number;
  errors: { shot_id: string; shot_number: number; error: string }[];
  cancelled?: boolean;
  provider?: VideoProviderCaps;
  strategies?: string[];
}

export interface ValidationFinding {
  shot_number: number;
  shot_id: string;
  kind: string;
  severity: "error" | "warning" | "info";
  message: string;
}

export interface VideoValidation {
  findings: ValidationFinding[];
  summary: { errors: number; warnings: number; info: number };
  shot_count: number;
  vision_used: boolean;
}

/** Result of the free, deterministic movement-description repair pass. */
export interface VideoMotionRepair {
  project_id: string;
  shots_changed: number;
  total_shots: number;
  notes: string[];
  warnings: string[];
  /** Present when the cinematic cut policy was applied too. */
  recut?: boolean;
  shots_recut?: number;
  cut_count?: number;
  seconds_per_cut?: number;
}

export interface VideoExportResult {
  project_id: string;
  metadata_path: string;
  subtitles_path: string | null;
  clip_count: number;
  video_path: string | null;
  reason: string;
  video_url?: string;
  metadata_url?: string;
  subtitles_url?: string;
}
