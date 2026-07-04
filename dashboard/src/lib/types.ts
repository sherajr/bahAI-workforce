// TypeScript interfaces matching the FastAPI responses (agents/api.py).

export interface Listing {
  title: string;
  description: string;
  bookmark_quote?: string;
  tags: string[];
  materials: string[];
  price_note: string;
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
  listing: Listing;
  review: Review;
  attempts: number;
  target_reached: boolean;
  badge: string;
  consultation: ConsultationTurn[];
  canva: { skipped?: boolean; reason?: string; design_url?: string | null };
}

export interface JobStep {
  ts: string;
  message: string;
}

export interface Job {
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
  result: PipelineResult | null;
  error: string | null;
  created_at: string;
  updated_at: string;
}

export interface JobSummary extends Omit<Job, "result"> {
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
  badge: string;
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
  estimated_costs: number;
  estimated_profit: number;
  cost_per_product: number;
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
}
