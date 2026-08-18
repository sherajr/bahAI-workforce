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
  status: "running" | "waiting_for_input" | "done" | "error" | "cancelled";
  /** True from the moment Cancel is pressed until the worker actually stops —
   *  the run is finishing the step it's on, so the UI says "stopping", never
   *  "stopped". */
  cancel_requested?: boolean;
  progress: string;
  steps: JobStep[];
  // Consultation turns streamed live as they happen (round 1, round 2, then
  // the Reviewer's pause-for-input turn and Sheraj's reply if given) — lets
  // the dashboard render the consultation as a live chat while the job runs.
  consultation_live?: ConsultationTurn[];
  // Set while status is "waiting_for_input": what the Reviewer is asking Sheraj.
  pending_prompt?: string | null;
  error: string | null;
  /** Who set this job going: "sheraj" (a dashboard button), "abigail" (a
   *  request of hers he approved), or "colony" (a team goal). The Pipeline tab
   *  adopts running jobs it didn't start, so it has to be able to say whose. */
  started_by?: string;
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

/**
 * A finished video, as the Products shelf sees it (GET /video/finished).
 *
 * Derived on the backend from the video tables every time it's read — a
 * finished video is not a `products` row, so nothing here can drift out of
 * step with the project it came from.
 */
export interface FinishedVideo {
  id: string;                       // the video PROJECT id
  title: string;
  status: string | null;
  created_at: string | null;
  updated_at: string | null;
  source_kind: string | null;       // scene_story | bookmark | quote_card
  source_product_id: string | null;
  video_url: string;
  poster_url: string;
  metadata_url: string;
  subtitles_url: string;
  file_missing: boolean;
  clip_count: number;
  shot_count: number;
  duration_seconds: number;
  /** False = the file couldn't be measured, so this is the plan's length. */
  duration_measured: boolean;
  /** Built from mock clips — never presentable as real generation (rule 32). */
  is_mock: boolean;
}

// ── The Colony (dashboard tab: the workforce as an organisation) ─────────────
// Mirrors agents/colony.py. Handoff edges are DERIVED from task_runs on the
// backend, so an edge on screen is recorded work, never a drawn assumption.

export interface ColonyAgent {
  name: string;
  team: string | null;
  /** A pipeline tool (compositor, consultation), not a person: no chat, no settings. */
  is_instrument: boolean;
  /** False for instruments and for the Secretary — she has her own tab (rules 15/16). */
  chattable: boolean;
  trust_level: number;
  trust_level_name: string;
  trust_score: number;
  total_runs: number;
  clean_runs: number;
  consecutive_failures: number;
  promotion_note: string;
  live: boolean;
  paused: boolean;
  has_instructions: boolean;
}

export interface GoalProgress {
  done: number;
  target: number | null;
  /** False for teams with no product pipeline — their goals steer, not ship. */
  measurable: boolean;
}

export interface TeamGoal {
  id: number;
  team: string;
  goal: string;
  detail: string;
  target_count: number | null;
  status: string;
  created_at: string;
  completed_at: string | null;
  launched_job_id: string | null;
  baseline_products: number;
  /** "sheraj" or "abigail" — a goal steers every agent on the team, so where
   *  it came from must never be ambiguous on screen. */
  set_by?: string;
  progress?: GoalProgress;
}

/** A pipeline job running for a team right now (derived from the live job
 *  store, never a second record of it). */
export interface TeamJob {
  job_id: string;
  kind: string;
  label: string;
  progress: string;
  status: string;
  started_by: string;
  started_by_label: string;
}

export interface ColonyTeam {
  id: string;
  name: string;
  blurb: string;
  accent: string;
  members: string[];
  instruments: string[];
  goal_kinds: string[];
  consultable: boolean;
  active_goals: TeamGoal[];
  jobs?: TeamJob[];
  /** True while a job is running for this team, or one of its agents logged a
   *  step in the last few minutes. */
  working?: boolean;
}

export interface HandoffEdge {
  source: string;
  target: string;
  count: number;
  last_at: string | null;
}

export interface ColonySnapshot {
  agents: ColonyAgent[];
  teams: ColonyTeam[];
  edges: HandoffEdge[];
  pending_actions: number;
  generated_at: string;
  /** Real people on the workforce, derived from the private store on every
   *  read — never a row in workforce.db (rule 68). */
  humans?: NucleiWorkforcePerson[];
}

export interface AgentRun {
  id: number;
  task_id: string;
  agent: string;
  step: string;
  input_summary: string | null;
  output_summary: string | null;
  /** null when the run was mechanical — rule 14, never collapse this to false. */
  passed_review: boolean | null;
  judged: boolean;
  timestamp: string;
}

export interface AgentSettings {
  agent: string;
  custom_instructions: string;
  paused: boolean;
  /** "" means no override — the router's task-type default applies. */
  model: string;
  updated_at: string | null;
}

export interface ColonyAgentMessage {
  id: number;
  agent: string;
  role: "user" | "assistant";
  content: string;
  ts: string;
}

export interface ColonyAgentDetail {
  name: string;
  team: string | null;
  team_name: string | null;
  is_instrument: boolean;
  chattable: boolean;
  trust_level: number;
  trust_level_name: string;
  trust_score: number;
  total_runs: number;
  clean_runs: number;
  consecutive_failures: number;
  promotion_note: string;
  live: boolean;
  settings: AgentSettings;
  goal_note: string;
  recent_runs: AgentRun[];
  hands_to: HandoffEdge[];
  receives_from: HandoffEdge[];
  messages: ColonyAgentMessage[];
}

export interface ColonyAction {
  id: number;
  agent: string;
  kind: string;
  description: string;
  payload: string;
  status: string;
  result: string | null;
  created_at: string;
  resolved_at: string | null;
}

export interface ColonyChatResult {
  agent: string;
  reply: string;
  queued: { id: number; description: string }[];
  used: string[];
}

export interface GoalLaunchResult {
  result: string;
  job_id?: string;
  kind?: string;
  theme?: string;
  video_project_id?: string;
  message?: string;
}

export interface TeamConsultTurn {
  agent: string;
  text: string;
}

export interface TeamConsultResult {
  team: string;
  team_name: string;
  question: string;
  turns: TeamConsultTurn[];
  summary: string;
}

// ── Per-agent model selection ───────────────────────────────────────────────
// Mirrors agents/models.py. The provider boundary (workforce = ollama|xai,
// Abigail = anthropic only) is enforced on the BACKEND — this is display.

export interface ModelOption {
  id: string;
  provider: "ollama" | "xai" | "anthropic";
  label: string;
  paid: boolean;
  note: string;
}

export interface ModelChoices {
  models: ModelOption[];
  /** Whether each provider actually answered. An empty local list because
   *  Ollama is DOWN is a different fact from nothing being installed. */
  reachable: Record<string, boolean>;
  agent?: string;
  /** "" when the agent is on the default routing. */
  chosen?: string;
  default_model?: string;
  default_provider?: string;
  default_paid?: boolean;
  /** True for the Reviewer and Artist: their image work goes through
   *  call_grok_vision, a separate path a local model choice cannot make free. */
  uses_vision?: boolean;
}

// ── The project wallet (Nora's domain) ──────────────────────────────────────
// Mirrors agents/wallet.py. All safety lives on the backend: owner-only
// allowlist, hard caps, USDC-only for the agent, mainnet opt-in.

export interface AllowlistEntry {
  id: number;
  label: string;
  address: string;
  note?: string;
  created_at: string;
}

export interface WalletLimits {
  auto_send_usdc: string;
  max_per_tx_usdc: string;
  daily_cap_usdc: string;
  spent_today_usdc: string;
}

export interface WalletChain {
  id: string;
  name: string;
  testnet: boolean;
  explorer: string;
  native: string;
}

export interface WalletStatus {
  exists: boolean;
  address: string | null;
  can_send: boolean;
  cannot_send_reason: string;
  mainnet_enabled: boolean;
  default_chain: string;
  chains: WalletChain[];
  limits: WalletLimits;
  allowlist: AllowlistEntry[];
  treasury: AllowlistEntry[];
}

export interface ChainBalance {
  chain: string;
  name: string;
  testnet: boolean;
  /** null when the chain could not be reached — NOT zero. */
  usdc: string | null;
  native: string | null;
  native_symbol: string;
  explorer: string;
  reachable: boolean;
  error?: string;
}

export interface WalletBalances {
  address: string | null;
  chains: ChainBalance[];
  /** REAL money only. Testnet holdings are never folded into this. */
  total_usdc: string;
  total_testnet_usdc?: string;
  treasury?: (AllowlistEntry & { balances: WalletBalances | null })[];
  error?: string;
}

export interface WalletTx {
  id: number;
  chain: string;
  token: string;
  to_address: string;
  to_label: string;
  amount: string;
  tx_hash: string | null;
  status: string;
  initiated_by: string;
  note: string;
  created_at: string;
  explorer_url: string | null;
}

export interface CreatedWallet {
  address: string;
  private_key: string;
  warning: string;
}

export interface WalletSendResult {
  id: number;
  tx_hash: string;
  chain: string;
  amount: string;
  to: string;
  to_label: string;
  explorer_url: string;
}

// Material World (nuclei). Names live only in private/nuclei.db.
export interface NucleiKind {
  id: number;
  slug: string;
  label: string;
  is_nucleus?: number;
  is_core?: number;
  accent?: string;
  axis_slug?: string;
  exclusive?: number;
  axis_exclusive?: number;
}

export interface NucleiGrouping {
  id: number;
  name: string;
  kind_slug: string;
  kind_label: string;
  is_nucleus: number;
  accent: string;
  created_at: string;
  slug?: string | null;
  pos_x?: number | null;
  pos_y?: number | null;
}

export interface NucleiActor {
  id: number;
  kind: "person" | "household" | "collective" | string;
  display_name: string;
  how_we_met: string | null;
  created_at: string;
  archived_at: string | null;
}

export interface NucleiFacet {
  id: number;
  slug: string;
  label: string;
  is_core: number;
  axis_slug: string;
}

export interface NucleiMembership {
  id: number;
  actor_id: number;
  grouping_id: number;
  orbit_index: number;
  introduced_as: string | null;
  facets?: NucleiFacet[];
  grouping_name?: string;
  grouping_kind?: string;
  is_nucleus?: boolean;
}

export interface NucleiTie {
  id: number;
  slug: string;
  label: string;
  from_actor_id: number;
  to_actor_id: number;
  grouping_id: number | null;
  grouping_name?: string | null;
  from_name?: string;
  to_name?: string;
  draw_style: string;
}

export interface NucleiLayoutGrouping {
  id: number;
  cx: number;
  cy: number;
  r: number;
  is_nucleus: boolean;
  is_institution?: boolean;
  slug?: string | null;
  accent: string;
}

export interface NucleiLayoutActor {
  id: number;
  x: number;
  y: number;
  home_grouping_id: number | null;
  accent: string;
}

export interface NucleiSnapshot {
  owner_actor_id: number;
  kinds: {
    grouping_kinds: NucleiKind[];
    axes: NucleiKind[];
    facet_kinds: NucleiKind[];
    activity_kinds: NucleiKind[];
  };
  groupings: NucleiGrouping[];
  actors: NucleiActor[];
  memberships: NucleiMembership[];
  facets: Record<string, NucleiFacet[]>;
  ties: NucleiTie[];
  household_members?: NucleiHouseholdMember[];
  embers: Record<string, number | null>;
  layout: {
    workforce: { cx: number; cy: number };
    groupings: NucleiLayoutGrouping[];
    actors: NucleiLayoutActor[];
  };
  quiet_after_days: number;
  workforce: { cx: number; cy: number };
  /** The Bahá'í Workforce is a real grouping — it just keeps its own light. */
  workforce_grouping_id?: number;
  workforce_members?: NucleiWorkforcePerson[];
  channels?: NucleiChannel[];
}

export interface NucleiWorkforcePerson {
  membership_id: number;
  actor_id: number;
  display_name: string;
  kind: string;
  role: string;
  since?: string | null;
}

/** The WhatsApp GROUP a nucleus already talks in. Never a person's number. */
export interface NucleiChannel {
  id: number;
  grouping_id: number;
  kind: string;
  label: string | null;
  link: string | null;
}

export interface WorkforcePicture {
  grouping_id: number;
  name: string;
  agents: ColonyAgent[];
  instruments: ColonyAgent[];
  teams: ColonyTeam[];
  people: NucleiWorkforcePerson[];
  running_jobs: { job_id: string; kind: string; progress?: string; started_by?: string;
                  team?: string; team_name?: string }[];
  pending_actions: number;
  recent_work: { id: string; title: string; kind: string; created_at?: string }[];
}

export interface WorkforceDraft {
  message: string;
  drafted_by: string;
  model: string;
  /** Concrete details the draft asserts that were never supplied. */
  warnings?: string[];
}

export interface WorkforceSendResult {
  status: "sent" | "queued";
  to: string;
  note: string;
  action_id?: number;
  as_template?: boolean;
}

export interface NucleiHouseholdMember {
  id: number;
  household_id: number;
  person_id: number;
  person_name?: string;
  household_name?: string;
}

export interface NucleiActorDetail {
  actor: NucleiActor;
  memberships: NucleiMembership[];
  ties: NucleiTie[];
  recent_activities: { id: number; kind_label: string; happened_at: string; title: string | null }[];
  days_since_sat: number | null;
  sat_sentence: string | null;
  family?: { id: number; household_id: number; household_name: string } | null;
  family_members?: NucleiHouseholdMember[];
}

export interface NucleiGroupingDetail {
  grouping: NucleiGrouping;
  members: (NucleiMembership & {
    actor: NucleiActor;
    facets: NucleiFacet[];
    family_members?: NucleiHouseholdMember[];
  })[];
  recent_activities: { id: number; kind_label: string; happened_at: string; title: string | null }[];
  accompaniments?: NucleiTie[];
}

export interface NucleiQuietLights {
  items: { actor_id: number; display_name: string; days: number; sentence: string }[];
}
