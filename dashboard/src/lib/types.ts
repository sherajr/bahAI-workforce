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
  citation?: string;
  translation?: CardTranslation | null;
  review: Review;
  attempts: number;
  target_reached: boolean;
  badge: string;
  consultation: ConsultationTurn[];
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

export interface StewardReport {
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
