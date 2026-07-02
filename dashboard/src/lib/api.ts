// Typed API client for the bahAI Workforce FastAPI server.
// In dev, Vite proxies /api/* → http://localhost:8765/* (see vite.config.ts).
// Every call reports into the activity-log event bus below.

import type {
  AgentStatus, CanvaStatus, EtsyPublishResult, EtsyStatus, ImproveResult,
  Job, JobSummary, PipelineResult, ProductRow, StewardReport, TrustReport,
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
  status: number | "ERR";
  ms: number;
}

const MAX_LOG = 20;
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

async function request<T>(method: "GET" | "POST", path: string, body?: unknown): Promise<T> {
  const started = performance.now();
  const ts = new Date().toLocaleTimeString();
  try {
    const res = await fetch(`${BASE}${path}`, {
      method,
      headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
    pushActivity({ ts, method, path, status: res.status, ms: Math.round(performance.now() - started) });
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
  runPipeline: (theme: string, targetScore = 9.0, maxAttempts = 3) =>
    post<{ job_id: string; status: string }>("/pipeline/run", {
      theme,
      target_score: targetScore,
      max_attempts: maxAttempts,
    }),
  writeApproveAsync: (payload: Record<string, unknown>) =>
    post<{ job_id: string; status: string }>("/pipeline/write-approve/async", payload),
  getPipelineStatus: (jobId: string) => get<Job>(`/pipeline/status/${jobId}`),
  getJobs: () => get<JobSummary[]>("/pipeline/jobs"),

  // Products
  getProducts: () => get<ProductRow[]>("/products"),
  getProduct: (id: string) => get<ProductRow>(`/products/${id}`),
  improveProduct: (id: string, humanNotes = "") =>
    post<ImproveResult>(`/products/${id}/improve`, {
      human_notes: humanNotes,
      target_score: 9.0,
      max_attempts: 2,
    }),
  recordRevenue: (id: string, amount: number) =>
    post<{ product_id: string; revenue: number }>(`/products/${id}/revenue`, { amount }),

  // Trust + agents
  getTrustReport: () => get<TrustReport>("/trust/report"),
  getAgents: () => get<AgentStatus[]>("/agents"),

  // Integrations
  getCanvaStatus: () => get<CanvaStatus>("/canva/status"),
  getEtsyStatus: () => get<EtsyStatus>("/etsy/status"),
  publishToEtsy: (productId: string) =>
    post<EtsyPublishResult>("/etsy/publish", { product_id: productId }),

  // Steward
  getStewardReport: () => get<StewardReport>("/steward/report"),

  // Health
  health: () => get<{ status: string; service: string }>("/health"),
};

export type { PipelineResult };
