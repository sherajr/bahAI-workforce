// Typed API client for the bahAI Workforce FastAPI server.
// In dev, Vite proxies /api/* → http://localhost:8765/* (see vite.config.ts).
// Every call reports into the activity-log event bus below.

import type {
  AgentStatus, CanvaStatus, CardLanguage, Contact, EditProductPayload, EditProductResult,
  EtsyPublishResult, EtsyStatus, ImproveResult, Job, JobBase, JobStep, JobSummary, PipelineResult,
  GoogleStatus, NoteRow, PendingApproval, PendingXPost, ProductRow, RegenerateCardImageResult,
  RegenerateCardQuoteResult, RegenerateImageResult, RegenerateQuoteResult, ReminderRow,
  SecretaryChatResult, SecretaryMessage, SecretaryNotification, SecretaryStatus, SecretaryUpcoming,
  StewardReport, TaskRow, TrustReport, WhatsAppStatus, XPostApproveResult, XPostEditResult,
  XPostRegenerateImageResult, XPostStatusResult,
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
  method: "GET" | "POST" | "PATCH" | "DELETE",
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

function logJobProgress(job: JobBase) {
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
  // Generic over the job's result payload — bookmark/card/redo jobs default
  // to PipelineResult; x-post jobs pass <XPostJobResult> at the call site.
  getPipelineStatus: async <TResult = PipelineResult>(jobId: string) => {
    const job = await request<Job<TResult>>("GET", `/pipeline/status/${jobId}`, undefined, { silent: true });
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
  regenerateCardQuote: async (id: string, guidance: string) => {
    const res = await post<RegenerateCardQuoteResult>(`/products/${id}/regenerate-card-quote`, { guidance });
    pushActivity({
      ts: new Date().toLocaleTimeString(),
      method: "QUOTE", path: id, status: "OK", ms: 0,
      detail: `New quote for card ${id} (${res.old_score.toFixed(1)} → ${res.new_score.toFixed(1)}): "${res.new_quote.slice(0, 60)}..."`,
    });
    return res;
  },
  regenerateCardImage: async (id: string, guidance: string) => {
    const res = await post<RegenerateCardImageResult>(`/products/${id}/regenerate-card-image`, { guidance });
    pushActivity({
      ts: new Date().toLocaleTimeString(),
      method: "IMAGE", path: id, status: "OK", ms: 0,
      detail: `New artwork for card ${id}: ${res.old_score.toFixed(1)} → ${res.new_score.toFixed(1)}.`,
    });
    return res;
  },
  regenerateCardAll: async (id: string, guidance: string) => {
    const res = await post<{ job_id: string; status: string }>(`/products/${id}/regenerate-card-all`, {
      guidance,
    });
    pushActivity({
      ts: new Date().toLocaleTimeString(),
      method: "REDO", path: id, status: "", ms: 0,
      detail: `Started full redo of card ${id}${guidance ? `: "${guidance}"` : ""} — job ${res.job_id}`,
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
  downloadPrintSheet: async (id: string, title?: string | null) => {
    const started = performance.now();
    const ts = new Date().toLocaleTimeString();
    const path = `/products/${id}/print-sheet`;
    const res = await fetch(`${BASE}${path}`);
    if (!res.ok) {
      let detail = res.statusText;
      try {
        const data = await res.json();
        detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail ?? data);
      } catch {
        /* keep statusText */
      }
      pushActivity({ ts, method: "GET", path, status: res.status, ms: Math.round(performance.now() - started) });
      throw new Error(`${res.status}: ${detail}`);
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${(title ?? "card").trim().replace(/\s+/g, "-") || "card"}-print-sheet.pdf`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    pushActivity({
      ts, method: "PRINT", path, status: "OK", ms: Math.round(performance.now() - started),
      detail: `Downloaded printable sheet for product ${id}.`,
    });
  },
  recordFeedback: async (id: string, text: string) => {
    const res = await post<{ product_id: string; recipient_feedback: string }>(
      `/products/${id}/feedback`,
      { text }
    );
    pushActivity({
      ts: new Date().toLocaleTimeString(),
      method: "FEEDBACK", path: id, status: "OK", ms: 0,
      detail: text
        ? `Recorded recipient feedback for ${id}: "${text.slice(0, 60)}${text.length > 60 ? "..." : ""}"`
        : `Cleared recipient feedback for ${id}.`,
    });
    return res;
  },

  // Trust + agents
  getTrustReport: () => get<TrustReport>("/trust/report"),
  getAgents: () => get<AgentStatus[]>("/agents"),

  // Integrations
  getCanvaStatus: () => get<CanvaStatus>("/canva/status"),
  getEtsyStatus: () => get<EtsyStatus>("/etsy/status"),
  publishToEtsy: async (productId: string, confirm = false) => {
    const res = await post<EtsyPublishResult>("/etsy/publish", {
      product_id: productId,
      confirm,
    });
    pushActivity({
      ts: new Date().toLocaleTimeString(),
      method: "ETSY", path: productId,
      status: res.skipped ? "SKIPPED" : res.requires_confirmation ? "PARTIAL" : "OK", ms: 0,
      detail: res.requires_confirmation
        ? `Etsy publish paused for confirmation: ${res.reason}`
        : res.skipped
          ? `Etsy publish skipped: ${res.reason}`
          : `Draft listing ${res.etsy_listing_id ?? ""} created on Etsy${res.image_uploaded ? "" : " (image upload failed)"}.`,
    });
    return res;
  },

  // Secretary — content stays inside the Secretary tab; the activity log only
  // ever sees the method/path, never what was said.
  secretaryChat: (message: string) =>
    post<SecretaryChatResult>("/secretary/chat", { message }),
  getSecretaryHistory: (limit = 50) =>
    request<{ messages: SecretaryMessage[] }>(
      "GET", `/secretary/history?limit=${limit}`, undefined, { silent: true }
    ),
  getSecretaryStatus: () =>
    request<SecretaryStatus>("GET", "/secretary/status", undefined, { silent: true }),
  getSecretaryUpcoming: (days = 14) =>
    request<SecretaryUpcoming>("GET", `/secretary/upcoming?days=${days}`, undefined, { silent: true }),
  getSecretaryApprovals: () =>
    request<{ pending: PendingApproval[] }>("GET", "/secretary/approvals", undefined, { silent: true }),
  resolveSecretaryApproval: async (id: number, approve: boolean) => {
    const res = await post<{ result: string }>(`/secretary/approvals/${id}`, { approve });
    pushActivity({
      ts: new Date().toLocaleTimeString(),
      method: "SECRETARY", path: `approval #${id}`,
      status: "OK", ms: 0,
      detail: approve ? `Approved secretary action #${id}.` : `Rejected secretary action #${id}.`,
    });
    return res;
  },
  getGoogleStatus: () =>
    request<GoogleStatus>("GET", "/google/status", undefined, { silent: true }),
  getWhatsAppStatus: () =>
    request<WhatsAppStatus>("GET", "/whatsapp/status", undefined, { silent: true }),
  getContacts: () =>
    request<{ contacts: Contact[] }>("GET", "/secretary/contacts", undefined, { silent: true }),
  addContact: (name: string, phone: string, allowlisted = false) =>
    post<{ id: number }>("/secretary/contacts", { name, phone, allowlisted }),
  setContactAllowlisted: (id: number, allowlisted: boolean) =>
    post<{ result: string }>(`/secretary/contacts/${id}/allowlist`, { allowlisted }),
  removeContact: (id: number) =>
    request<{ result: string }>("DELETE", `/secretary/contacts/${id}`),
  // Personality / custom instructions
  getPersonality: () =>
    request<{ custom_instructions: string }>("GET", "/secretary/personality", undefined, { silent: true }),
  setPersonality: (custom_instructions: string) =>
    post<{ result: string }>("/secretary/personality", { custom_instructions }),
  // Notes (private/memory/*.md, manual view/edit)
  getNotes: () =>
    request<{ notes: NoteRow[] }>("GET", "/secretary/notes", undefined, { silent: true }),
  saveNote: (name: string, content: string) =>
    post<{ result: string }>("/secretary/notes", { name, content }),
  deleteNote: (name: string) =>
    request<{ result: string }>("DELETE", `/secretary/notes/${encodeURIComponent(name)}`),
  // Tasks (manual view/edit — she still only ever sees open ones)
  getTasks: () =>
    request<{ tasks: TaskRow[] }>("GET", "/secretary/tasks", undefined, { silent: true }),
  addTask: (description: string, due?: string) =>
    post<{ id: number }>("/secretary/tasks", { description, due: due || null }),
  editTask: (id: number, edits: { description?: string; due?: string | null; done?: boolean }) =>
    patch<{ result: string }>(`/secretary/tasks/${id}`, edits),
  deleteTask: (id: number) =>
    request<{ result: string }>("DELETE", `/secretary/tasks/${id}`),
  // Reminders (manual view/edit)
  getReminders: () =>
    request<{ reminders: ReminderRow[] }>("GET", "/secretary/reminders", undefined, { silent: true }),
  addReminder: (message: string, fire_at: string, recurrence?: string, wake_me = false) =>
    post<{ id: number }>("/secretary/reminders", { message, fire_at, recurrence: recurrence || null, wake_me }),
  editReminder: (
    id: number,
    edits: { message?: string; fire_at?: string; recurrence?: string | null; wake_me?: boolean }
  ) => patch<{ result: string }>(`/secretary/reminders/${id}`, edits),
  deleteReminder: (id: number) =>
    request<{ result: string }>("DELETE", `/secretary/reminders/${id}`),
  // Scheduler fires/failures -> Activity Log (titles only, hard rule 8).
  // Returns the highest notification id seen, for the next poll.
  pollSecretaryNotifications: async (afterId: number) => {
    const res = await request<{ notifications: SecretaryNotification[] }>(
      "GET", `/secretary/notifications?after_id=${afterId}`, undefined, { silent: true }
    );
    let last = afterId;
    for (const n of res.notifications) {
      last = Math.max(last, n.id);
      pushActivity({
        ts: new Date(n.created_at).toLocaleTimeString(),
        method: n.kind === "scheduler_error" ? "ERROR" : "REMIND",
        path: "secretary",
        status: n.kind === "scheduler_error" ? "ERR" : "OK",
        ms: 0,
        detail: n.title,
      });
    }
    return { notifications: res.notifications, lastId: last };
  },

  // Post to X (@peaceAntz) — giveaway outreach, never sold, never auto-posted.
  // A background job: the team's consultation (with its round-2 human pause)
  // runs the same way as the bookmark/card pipelines — poll getPipelineStatus
  // <XPostJobResult> and respondToJob for the pause.
  runXPost: async (topic: string, includeQuote: boolean) => {
    const res = await post<{ job_id: string; status: string }>("/x-post", { topic, include_quote: includeQuote });
    pushActivity({
      ts: new Date().toLocaleTimeString(),
      method: "RUN", path: "x-post", status: "", ms: 0,
      detail: `Started drafting a post for "${topic}" (${includeQuote ? "with a quote" : "no direct quote"}) — job ${res.job_id}`,
    });
    return res;
  },
  getPendingXPosts: () => get<PendingXPost[]>("/x-post/pending"),
  getDraftXPosts: () => get<PendingXPost[]>("/x-post/drafts"),
  getPostedXPosts: () => get<PendingXPost[]>("/x-post/posted"),
  saveXPostAsDraft: async (id: string) => {
    const res = await post<XPostStatusResult>(`/x-post/${id}/save-draft`);
    pushActivity({
      ts: new Date().toLocaleTimeString(),
      method: "X-POST", path: id, status: "OK", ms: 0,
      detail: `Set draft tweet ${id} aside to think about.`,
    });
    return res;
  },
  restoreXPost: async (id: string) => {
    const res = await post<XPostStatusResult>(`/x-post/${id}/restore`);
    pushActivity({
      ts: new Date().toLocaleTimeString(),
      method: "X-POST", path: id, status: "OK", ms: 0,
      detail: `Moved draft tweet ${id} back to pending approval.`,
    });
    return res;
  },
  editXPost: async (id: string, tweetText: string) => {
    const res = await patch<XPostEditResult>(`/x-post/${id}`, { tweet_text: tweetText });
    pushActivity({
      ts: new Date().toLocaleTimeString(),
      method: "X-POST", path: id, status: "OK", ms: 0,
      detail: `Hand-edited draft tweet ${id}.`,
    });
    return res;
  },
  regenerateXPostImage: async (id: string, guidance: string) => {
    const res = await post<XPostRegenerateImageResult>(`/x-post/${id}/regenerate-image`, { guidance });
    pushActivity({
      ts: new Date().toLocaleTimeString(),
      method: "X-POST", path: id, status: "OK", ms: 0,
      detail: guidance
        ? `Regenerated the image for draft ${id}: "${guidance}"`
        : `Regenerated the image for draft ${id} (no guidance).`,
    });
    return res;
  },
  approveXPost: async (id: string) => {
    const res = await post<XPostApproveResult>(`/x-post/approve/${id}`);
    pushActivity({
      ts: new Date().toLocaleTimeString(),
      method: "X-POST", path: id, status: "OK", ms: 0,
      detail: res.dry_run
        ? `Dry-run: would have posted tweet ${id} (TWITTER_DRY_RUN=true).`
        : `Posted to X: ${res.url ?? res.posted_tweet_id ?? id}`,
    });
    return res;
  },
  discardXPost: async (id: string) => {
    const res = await post<{ id: string; status: string }>(`/x-post/discard/${id}`);
    pushActivity({
      ts: new Date().toLocaleTimeString(),
      method: "X-POST", path: id, status: "OK", ms: 0,
      detail: `Discarded draft tweet ${id}.`,
    });
    return res;
  },

  // Steward
  getStewardReport: () => get<StewardReport>("/steward/report"),

  // Health
  health: () => get<{ status: string; service: string }>("/health"),
};

export type { PipelineResult };
