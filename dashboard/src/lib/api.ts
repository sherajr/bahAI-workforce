// Typed API client for the bahAI Workforce FastAPI server.
// In dev, Vite proxies /api/* → http://localhost:8765/* (see vite.config.ts).
// Every call reports into the activity-log event bus below.

import type {
  AgentStatus, CanvaStatus, CardLanguage, EditProductPayload, EditProductResult,
  EtsyPublishResult, EtsyStatus, ImproveResult, Job, JobStep, JobSummary, PipelineResult,
  ProductRow, RegenerateImageResult, RegenerateQuoteResult, StewardReport, TrustReport,
} from "./types";

export const BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "/api";
// The API server itself (used for OAuth links opened in a new tab, outside the proxy).
export const API_ORIGIN =
  (import.meta.env.VITE_API_ORIGIN as string | undefined) ?? "http://localhost:8765";

// ── Activity log event bus ────────────────────────────────────────────────────

export interface ActivityEntry {
  ts: string;
  method: string;
  path: string;
  status: number | "ERR" | "OK" | "PARTIAL" | "SKIPPED" | "";
  ms: number;
  // Human-readable description of what actually happened. When present, the
  // log renders this instead of the raw method/path/status/ms columns.
  detail?: string;
}

const MAX_LOG = 60;
let activityLog: ActivityEntry[] = [];
const listeners = new Set<() => void>();

function pushActivity(entry: ActivityEntry) {
  activityLog = [...activityLog, entry].slice(-MAX_LOG);
  listeners.forEach((l) => l());
}

export function subscribeActivity(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function getActivityLog(): ActivityEntry[] {
  return activityLog;
}

// ── Fetch helpers ─────────────────────────────────────────────────────────────

async function request<T>(
  method: "GET" | "POST" | "PATCH",
  path: string,
  body?: unknown,
  opts?: { silent?: boolean }
): Promise<T> {
  const started = performance.now();
  const ts = new Date().toLocaleTimeString();
  try {
    const res = await fetch(`${BASE}${path}`, {
      method,
      headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
    // Silent calls (e.g. 2.5s pipeline-status polling) skip the routine trace —
    // logJobProgress() below surfaces what actually happened instead. Failures
    // still get logged even when silent.
    if (!opts?.silent || !res.ok) {
      pushActivity({ ts, method, path, status: res.status, ms: Math.round(performance.now() - started) });
    }
    if (!res.ok) {
      let detail = res.statusText;
      try {
        const data = await res.json();
        detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail ?? data);
      } catch {
        /* keep statusText */
      }
      throw new Error(`${res.status}: ${detail}`);
    }
    return (await res.json()) as T;
  } catch (err) {
    if (err instanceof Error && !err.message.match(/^\d{3}:/)) {
      pushActivity({ ts, method, path, status: "ERR", ms: Math.round(performance.now() - started) });
    }
    throw err;
  }
}

const get = <T>(path: string) => request<T>("GET", path);
const post = <T>(path: string, body?: unknown) => request<T>("POST", path, body);
const patch = <T>(path: string, body?: unknown) => request<T>("PATCH", path, body);

// ── Pipeline job progress — turns the backend's step-by-step narration
//    (Librarian retrieving, Artist painting, Reviewer challenging, etc.)
//    into activity log entries as it actually happens. ─────────────────────────

const jobStepsSeen = new Map<string, number>();
const jobsCompletionLogged = new Set<string>();

function logJobProgress(job: Job) {
  const seen = jobStepsSeen.get(job.job_id) ?? 0;
  const newSteps: JobStep[] = job.steps.slice(seen);
  for (const step of newSteps) {
    pushActivity({
      ts: new Date(step.ts).toLocaleTimeString(),
      method: "STEP",
      path: job.kind,
      status: "",
      ms: 0,
      detail: step.message,
    });
  }
  jobStepsSeen.set(job.job_id, job.steps.length);

  if (job.status !== "running" && !jobsCompletionLogged.has(job.job_id)) {
    jobsCompletionLogged.add(job.job_id);
    pushActivity({
      ts: new Date().toLocaleTimeString(),
      method: job.status === "done" ? "DONE" : "ERROR",
      path: job.kind,
      status: job.status === "done" ? "OK" : "ERR",
      ms: 0,
      detail: job.status === "done" ? "Pipeline finished." : `Pipeline failed: ${job.error}`,
    });
  }
}

// ── Image helper ──────────────────────────────────────────────────────────────

/** Convert any stored image path (Windows or POSIX) into a servable URL via /outputs. */
export function imageUrl(path: string | null | undefined): string {
  if (!path) return "";
  if (path.startsWith("/outputs/")) return `${BASE}${path}`;
  const name = path.replace(/\\/g, "/").split("/").pop() ?? "";
  return `${BASE}/outputs/${name}`;
}

/** Guess the Compositor's front-render URL for a product's original artwork path. */
export function frontImageUrl(originalPath: string | null | undefined): string {
  if (!originalPath) return "";
  const name = originalPath.replace(/\\/g, "/").split("/").pop() ?? "";
  const stem = name.replace(/\.[^.]+$/, "");
  return `${BASE}/outputs/${stem}-front.png`;
}

// ── API surface ───────────────────────────────────────────────────────────────

export const api = {
  // Pipeline
  runPipeline: async (theme: string, targetScore = 9.0, maxAttempts = 3) => {
    const res = await post<{ job_id: string; status: string }>("/pipeline/run", {
      theme,
      target_score: targetScore,
      max_attempts: maxAttempts,
    });
    pushActivity({
      ts: new Date().toLocaleTimeString(),
      method: "RUN", path: "pipeline", status: "", ms: 0,
      detail: `Started "${theme}" (target ${targetScore.toFixed(1)}/10, up to ${maxAttempts} attempt${maxAttempts > 1 ? "s" : ""}) — job ${res.job_id}`,
    });
    return res;
  },
  runCardPipeline: async (theme: string, language: string | null, targetScore = 9.0, maxAttempts = 3) => {
    const res = await post<{ job_id: string; status: string }>("/pipeline/run-card", {
      theme,
      language,
      target_score: targetScore,
      max_attempts: maxAttempts,
    });
    pushActivity({
      ts: new Date().toLocaleTimeString(),
      method: "RUN", path: "card-pipeline", status: "", ms: 0,
      detail: `Started quote card "${theme}"${language ? ` with ${language} translation` : " (English only)"} — job ${res.job_id}`,
    });
    return res;
  },
  getCardLanguages: () => get<CardLanguage[]>("/card/languages"),
  getPipelineStatus: async (jobId: string) => {
    const job = await request<Job>("GET", `/pipeline/status/${jobId}`, undefined, { silent: true });
    logJobProgress(job);
    return job;
  },
  getJobs: () => get<JobSummary[]>("/pipeline/jobs"),
  respondToJob: async (jobId: string, text: string) => {
    const res = await post<{ status: string }>(`/pipeline/status/${jobId}/respond`, { text });
    pushActivity({
      ts: new Date().toLocaleTimeString(),
      method: "REPLY", path: jobId, status: "OK", ms: 0,
      detail: text ? `Sent guidance to job ${jobId}: "${text}"` : `Continued job ${jobId} with no guidance.`,
    });
    return res;
  },

  // Products
  getProducts: () => get<ProductRow[]>("/products"),
  getProduct: (id: string) => get<ProductRow>(`/products/${id}`),
  improveProduct: async (id: string, humanNotes = "") => {
    const res = await post<ImproveResult>(`/products/${id}/improve`, {
      human_notes: humanNotes,
      target_score: 9.0,
      max_attempts: 2,
    });
    pushActivity({
      ts: new Date().toLocaleTimeString(),
      method: "IMPROVE", path: id, status: res.target_reached ? "OK" : "PARTIAL", ms: 0,
      detail: `Product ${id}: ${res.old_score.toFixed(1)} → ${res.new_score.toFixed(1)} over ${res.attempts} attempt${res.attempts > 1 ? "s" : ""}`
        + (res.target_reached ? " — target reached." : "."),
    });
    return res;
  },
  editProduct: async (id: string, payload: EditProductPayload) => {
    const res = await patch<EditProductResult>(`/products/${id}`, payload);
    pushActivity({
      ts: new Date().toLocaleTimeString(),
      method: "EDIT", path: id, status: "OK", ms: 0,
      detail: `Manually edited product ${id}: ${Object.keys(payload).join(", ")}.`,
    });
    return res;
  },
  regenerateQuote: async (id: string, guidance: string) => {
    const res = await post<RegenerateQuoteResult>(`/products/${id}/regenerate-quote`, { guidance });
    pushActivity({
      ts: new Date().toLocaleTimeString(),
      method: "QUOTE", path: id, status: "OK", ms: 0,
      detail: `New quote for ${id} (${res.old_score.toFixed(1)} → ${res.new_score.toFixed(1)}): "${res.new_quote.slice(0, 60)}..."`,
    });
    return res;
  },
  regenerateImage: async (id: string, guidance: string) => {
    const res = await post<RegenerateImageResult>(`/products/${id}/regenerate-image`, { guidance });
    pushActivity({
      ts: new Date().toLocaleTimeString(),
      method: "IMAGE", path: id, status: "OK", ms: 0,
      detail: `New artwork for ${id}: ${res.old_score.toFixed(1)} → ${res.new_score.toFixed(1)}.`,
    });
    return res;
  },
  regenerateAll: async (id: string, guidance: string) => {
    const res = await post<{ job_id: string; status: string }>(`/products/${id}/regenerate-all`, {
      guidance,
    });
    pushActivity({
      ts: new Date().toLocaleTimeString(),
      method: "REDO", path: id, status: "", ms: 0,
      detail: `Started full redo of ${id}${guidance ? `: "${guidance}"` : ""} — job ${res.job_id}`,
    });
    return res;
  },
  recordRevenue: async (id: string, amount: number) => {
    const res = await post<{ product_id: string; revenue: number }>(`/products/${id}/revenue`, { amount });
    pushActivity({
      ts: new Date().toLocaleTimeString(),
      method: "REVENUE", path: id, status: "OK", ms: 0,
      detail: `Recorded $${amount.toFixed(2)} revenue for product ${id}.`,
    });
    return res;
  },

  // Trust + agents
  getTrustReport: () => get<TrustReport>("/trust/report"),
  getAgents: () => get<AgentStatus[]>("/agents"),

  // Integrations
  getCanvaStatus: () => get<CanvaStatus>("/canva/status"),
  getEtsyStatus: () => get<EtsyStatus>("/etsy/status"),
  publishToEtsy: async (productId: string) => {
    const res = await post<EtsyPublishResult>("/etsy/publish", { product_id: productId });
    pushActivity({
      ts: new Date().toLocaleTimeString(),
      method: "ETSY", path: productId, status: res.skipped ? "SKIPPED" : "OK", ms: 0,
      detail: res.skipped
        ? `Etsy publish skipped: ${res.reason}`
        : `Draft listing ${res.etsy_listing_id ?? ""} created on Etsy${res.image_uploaded ? "" : " (image upload failed)"}.`,
    });
    return res;
  },

  // Steward
  getStewardReport: () => get<StewardReport>("/steward/report"),

  // Health
  health: () => get<{ status: string; service: string }>("/health"),
};

export type { PipelineResult };
