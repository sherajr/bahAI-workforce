// Typed API client for the bahAI Workforce FastAPI server.
// In dev, Vite proxies /api/* → http://localhost:8765/* (see vite.config.ts).
// Every call reports into the activity-log event bus below.

import type {
  AgentStatus, CanvaStatus, CardLanguage, Contact, EditProductPayload, EditProductResult,
  EtsyPublishResult, EtsyStatus, ImproveResult, Job, JobBase, JobStep, JobSummary, PipelineResult,
  GoogleStatus, LayoutOptions, LayoutRenderResult, NoteRow, PendingApproval, PendingXPost,
  AvailableOutcomes, GatheringDetail, GatheringStage, GatheringSummary,
  HomeSummary,
  ProductLayout, ProductRow, ProductsPage, RegenerateCardImageResult,
  RegenerateCardQuoteResult, RegenerateImageResult, RegenerateQuoteResult, ReminderRow,
  SecretaryChatResult, SecretaryMessage, SecretaryNotification, SecretaryStatus, SecretaryUpcoming,
  StewardReport, TaskRow, TrustReport, WhatsAppStatus, XPostApproveResult, XPostEditResult,
  XPostRegenerateImageResult, XPostStatusResult, DeedsReport, RecentDeed,
  RuhiQuoteSuggestResult, QuoteSourceOption,
  VideoProject, VideoProjectDetail, VideoProvidersResult, VideoDefaults, VideoShot,
  VideoValidation, VideoExportResult, VideoDirection, ContinuityBible,
  VideoShotData, VideoMotionRepair, FinishedVideo,
  ColonySnapshot, ColonyAgentDetail, ColonyChatResult, AgentSettings, HandoffEdge,
  AgentRun, ColonyAction, TeamGoal, GoalLaunchResult, ModelChoices,
  WalletStatus, WalletBalances, WalletTx, CreatedWallet, WalletSendResult,
  NucleiSnapshot, NucleiActorDetail, NucleiGroupingDetail, NucleiQuietLights,
  NucleiChannel, WorkforceDraft, WorkforcePicture, WorkforceSendResult,
} from "./types";
import type {
  AnalysisResult, ConsultationCapabilities, ConsultationDetail, ConsultationMode,
  ConsultationUpdates,
  ConsultationPresence, ConsultationSession, RealtimeCredential, SpeechDecision,
  VerifiedWriting,
  ConsultationParticipant, ConsultationTurn, DiarizeResult, ReportResult, ScheduledSpeech,
  ActionStatus, ConsultationAction, ConsultationDecision, ConsultationStateMap, MapItem,
  ConceptGraph, GraphRelation,
} from "./consultationTypes";

export const BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "/api";
// There is deliberately no API_ORIGIN. Everything reaching the API -- fetches,
// <img>/<video> sources, and links opened in a new tab such as the OAuth start
// pages and /whatsapp/setup -- goes through BASE, because the proxy is where
// the owner key is attached (AGENTS.md rule 70). A URL naming the API's own
// host and port directly skips the proxy and comes back 401.

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


/**
 * Fetch a file through the API proxy and hand it to the browser to save.
 *
 * Shared, because the failure it gets right is easy to get wrong twice: an
 * error response is JSON, not a PDF, so it has to be read as an error BEFORE a
 * Blob is made — otherwise the browser cheerfully saves a file containing
 * `{"detail": "..."}` and the person opens a broken PDF instead of reading
 * what went wrong. The object URL is revoked either way.
 */
async function downloadFile(
  path: string, method: "GET" | "POST", body: unknown, filename: string,
): Promise<void> {
  const started = performance.now();
  const ts = new Date().toLocaleTimeString();
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      detail = typeof data.detail === "string"
        ? data.detail : JSON.stringify(data.detail ?? data);
    } catch {
      /* keep statusText */
    }
    pushActivity({ ts, method, path, status: res.status,
                   ms: Math.round(performance.now() - started) });
    throw new Error(detail);
  }
  // Any warning the builder wants the person to see travels in a header rather
  // than inside the file — a programme whose reading could not be found still
  // downloads, and still says so (rule 119).
  const warnings = res.headers.get("X-Program-Warnings") || "";
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  try {
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
  } finally {
    URL.revokeObjectURL(url);
  }
  pushActivity({
    ts, method: "PRINT", path, status: "OK",
    ms: Math.round(performance.now() - started),
    detail: `Downloaded ${filename}.${warnings ? " " + warnings : ""}`,
  });
  if (warnings) throw new DownloadWarning(warnings);
}

/** Not a failure: the file downloaded AND there is something to tell you. */
export class DownloadWarning extends Error {}

const get = <T>(path: string) => request<T>("GET", path);
const post = <T>(path: string, body?: unknown) => request<T>("POST", path, body);

/** A multipart POST. Separate from `request` because the Content-Type header
 *  must be left ALONE for FormData — setting it drops the multipart boundary the
 *  browser generates, and the server then cannot parse the body at all. */
async function upload<T>(path: string, form: FormData): Promise<T> {
  const started = performance.now();
  const ts = new Date().toLocaleTimeString();
  const res = await fetch(`${BASE}${path}`, { method: "POST", body: form });
  pushActivity({ ts, method: "POST", path, status: res.status,
                 ms: Math.round(performance.now() - started) });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail ?? data);
    } catch { /* keep statusText */ }
    throw new Error(`${res.status}: ${detail}`);
  }
  return (await res.json()) as T;
}
const patch = <T>(path: string, body?: unknown) => request<T>("PATCH", path, body);

// ── Pipeline job progress — turns the backend's step-by-step narration
//    (Librarian retrieving, Artist painting, Reviewer challenging, etc.)
//    into activity log entries as it actually happens. ─────────────────────────

const jobStepsSeen = new Map<string, number>();
const jobsCompletionLogged = new Set<string>();

/**
 * The backend stamps steps with `datetime.utcnow().isoformat()` — no timezone
 * suffix — so `new Date(...)` reads them as LOCAL time and the activity log
 * showed each step hours adrift from the entries around it (observed: 5:59 PM
 * next to 12:59 AM for the same event). Appending Z parses them as the UTC
 * they actually are.
 */
function asUtc(ts: string): Date {
  return new Date(/[Zz]|[+-]\d{2}:?\d{2}$/.test(ts) ? ts : `${ts}Z`);
}

function logJobProgress(job: JobBase) {
  const seen = jobStepsSeen.get(job.job_id) ?? 0;
  const newSteps: JobStep[] = job.steps.slice(seen);
  for (const step of newSteps) {
    pushActivity({
      ts: asUtc(step.ts).toLocaleTimeString(),
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
  runCardPipeline: async (theme: string, language: string | null, targetScore = 9.0, maxAttempts = 3, pinnedQuote = "", sources: string[] | null = null, pinnedCitation = "") => {
    const res = await post<{ job_id: string; status: string }>("/pipeline/run-card", {
      theme,
      language,
      target_score: targetScore,
      max_attempts: maxAttempts,
      pinned_quote: pinnedQuote,
      sources,
      pinned_citation: pinnedCitation,
    });
    pushActivity({
      ts: new Date().toLocaleTimeString(),
      method: "RUN", path: "card-pipeline", status: "", ms: 0,
      detail: `Started quote card "${theme}"${language ? ` with ${language} translation` : " (English only)"}${pinnedQuote ? ` (pinned quote: "${pinnedQuote.slice(0, 30)}...")` : ""} — job ${res.job_id}`,
    });
    return res;
  },
  runCardBatch: async (theme: string, language: string | null, quotes: string[], targetScore = 9.0, maxAttempts = 3, sources: string[] | null = null, quoteCitations: string[] | null = null) => {
    const res = await post<{ job_id: string; status: string; total: number }>("/pipeline/run-card-batch", {
      theme,
      language,
      quotes,
      target_score: targetScore,
      max_attempts: maxAttempts,
      sources,
      quote_citations: quoteCitations,
    });
    pushActivity({
      ts: new Date().toLocaleTimeString(),
      method: "RUN", path: "card-batch", status: "", ms: 0,
      detail: `Started a hands-free batch of ${quotes.length} quote cards ("${theme}"${language ? `, with ${language} translation` : ""}) — job ${res.job_id}`,
    });
    return res;
  },
  getCardLanguages: () => get<CardLanguage[]>("/card/languages"),
  // Selectable quote sources for the card form (Ruhi + ingested library texts).
  getQuoteSources: () => get<{ sources: QuoteSourceOption[] }>("/quote-sources"),
  // Librarian quote suggestions for the card form — searches the SELECTED
  // sources (default Ruhi Book 1). Local-tier results are pre-verified
  // server-side; web-tier results come back flagged verified=false.
  suggestRuhiQuotes: async (topic: string, count: number, sources: string[] | null = null) => {
    const qs =
      `topic=${encodeURIComponent(topic)}&count=${count}` +
      (sources && sources.length ? `&sources=${encodeURIComponent(sources.join(","))}` : "");
    const res = await get<RuhiQuoteSuggestResult>(`/ruhi-quotes?${qs}`);
    const unverified = res.items.filter((i) => !i.verified).length;
    pushActivity({
      ts: new Date().toLocaleTimeString(),
      method: "GET", path: "ruhi-quotes", status: "OK", ms: 0,
      detail: `The Librarian found ${res.items.length} passage${res.items.length === 1 ? "" : "s"} for "${topic}"` +
        (unverified ? ` (${unverified} from the web — wording not verified)` : ""),
    });
    return res;
  },
  // Generic over the job's result payload — bookmark/card/redo jobs default
  // to PipelineResult; x-post jobs pass <XPostJobResult> at the call site.
  getPipelineStatus: async <TResult = PipelineResult>(jobId: string) => {
    const job = await request<Job<TResult>>("GET", `/pipeline/status/${jobId}`, undefined, { silent: true });
    logJobProgress(job);
    return job;
  },
  getJobs: () => get<JobSummary[]>("/pipeline/jobs"),
  // Stop a running job. The server flags it and the worker stops at its next
  // step boundary, so the panel keeps polling until the status really is
  // "cancelled" rather than assuming this call ended it.
  cancelJob: async (jobId: string) => {
    const res = await post<{ status: string; message: string; already_finished?: boolean }>(
      `/pipeline/status/${jobId}/cancel`, {});
    pushActivity({
      ts: new Date().toLocaleTimeString(),
      method: "CANCEL", path: jobId, status: "OK", ms: 0,
      detail: res.message,
    });
    return res;
  },
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
  /** One bounded page of the shelf, with search, filters, sort and counts
   *  applied to the WHOLE shelf rather than to the page (rule 116). */
  getProductsPage: (params: {
    limit: number; offset: number; kind: string; search: string;
    sort: string; badge?: string;
  }) => {
    const q = new URLSearchParams({
      limit: String(params.limit), offset: String(params.offset),
      kind: params.kind, search: params.search, sort: params.sort,
    });
    if (params.badge && params.badge !== "all") q.set("badge", params.badge);
    return get<ProductsPage>(`/products/summary?${q.toString()}`);
  },
  getProduct: (id: string) => get<ProductRow>(`/products/${id}`),

  // ── Gatherings (rules 117-119) and Home (rule 120) ──────────────────────
  getHome: () => get<HomeSummary>("/home/summary"),
  listGatherings: () =>
    get<{ projects: GatheringSummary[]; stages: Record<string, GatheringStage> }>(
      "/gatherings"),
  createGathering: (body: {
    title: string; purpose?: string; gathering_at?: string | null; timezone?: string;
  }) => post<GatheringDetail>("/gatherings", body),
  getGathering: (id: string) => get<GatheringDetail>(`/gatherings/${id}`),
  patchGathering: (id: string, body: Record<string, unknown>) =>
    request<GatheringDetail>("PATCH", `/gatherings/${id}`, body),
  deleteGathering: (id: string) =>
    request<{ deleted: boolean; note: string }>("DELETE", `/gatherings/${id}`),
  linkGatheringSession: (id: string, sessionId: string) =>
    post<GatheringDetail>(`/gatherings/${id}/sessions`, { session_id: sessionId }),
  unlinkGatheringSession: (id: string, sessionId: string) =>
    request<GatheringDetail>("DELETE", `/gatherings/${id}/sessions/${sessionId}`),
  gatheringAvailableOutcomes: (id: string) =>
    get<AvailableOutcomes>(`/gatherings/${id}/available-outcomes`),
  addGatheringOutcome: (id: string, body: {
    kind: string; text: string; session_id?: string; ref_id?: string; note?: string;
  }) => post<GatheringDetail>(`/gatherings/${id}/outcomes`, body),
  removeGatheringOutcome: (id: string, outcomeId: string) =>
    request<GatheringDetail>("DELETE", `/gatherings/${id}/outcomes/${outcomeId}`),
  addGatheringItem: (id: string, productId: string) =>
    post<GatheringDetail>(`/gatherings/${id}/items`, { product_id: productId }),
  removeGatheringItem: (id: string, productId: string) =>
    request<GatheringDetail>("DELETE", `/gatherings/${id}/items/${productId}`),
  addProgramItem: (id: string, body: {
    kind: string; title?: string; body?: string; minutes?: number | null;
    writing_id?: string;
  }) => post<GatheringDetail>(`/gatherings/${id}/program`, body),
  patchProgramItem: (id: string, itemId: string, body: Record<string, unknown>) =>
    request<GatheringDetail>("PATCH", `/gatherings/${id}/program/${itemId}`, body),
  removeProgramItem: (id: string, itemId: string) =>
    request<GatheringDetail>("DELETE", `/gatherings/${id}/program/${itemId}`),
  reorderProgram: (id: string, itemIds: string[]) =>
    post<GatheringDetail>(`/gatherings/${id}/program/reorder`, { item_ids: itemIds }),
  /**
   * The programme and the cards are two SEPARATE downloads, on purpose: the card
   * sheet is a duplex grid whose page 2 has to line up with page 1 through the
   * printer's flip, and a programme page inside it shifts every back face by a
   * page (rule 119).
   */
  downloadProgram: (id: string, name = "programme") =>
    downloadFile(`/gatherings/${id}/program.pdf`, "GET", undefined,
                 `${name} - programme.pdf`),
  downloadGatheringCards: (id: string, duplex: boolean, name = "gathering") =>
    downloadFile(`/gatherings/${id}/cards.pdf`, "POST", { duplex },
                 `${name} - cards.pdf`),

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
  // Visual layout editor — presentation only, never text. previewLayout is a
  // silent, high-frequency call (fires as sliders move); saveLayout is logged.
  getLayout: (id: string) =>
    request<LayoutOptions>("GET", `/products/${id}/layout`, undefined, { silent: true }),
  previewLayout: (id: string, layout: ProductLayout) =>
    request<LayoutRenderResult>("POST", `/products/${id}/layout/preview`, { layout }, { silent: true }),
  saveLayout: async (id: string, layout: ProductLayout) => {
    const res = await post<LayoutRenderResult>(`/products/${id}/layout`, { layout });
    pushActivity({
      ts: new Date().toLocaleTimeString(),
      method: "LAYOUT", path: id, status: "OK", ms: 0,
      detail: `Saved layout for product ${id} (font ${layout.font}, text ${layout.text_color}).`,
    });
    return res;
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

  // ── The Colony ────────────────────────────────────────────────────────────
  // Polled views are `silent` so a graph refreshing every few seconds doesn't
  // drown the Activity Log; anything that CHANGES something stays loud.
  getColony: () => request<ColonySnapshot>("GET", "/colony", undefined, { silent: true }),
  getColonyAgent: (agent: string) =>
    request<ColonyAgentDetail>("GET", `/colony/agents/${agent}`, undefined, { silent: true }),
  colonyChat: (agent: string, message: string) =>
    post<ColonyChatResult>(`/colony/agents/${agent}/chat`, { message }),
  clearColonyChat: (agent: string) =>
    request<{ result: string }>("DELETE", `/colony/agents/${agent}/chat`),
  getAgentModels: (agent: string) =>
    request<ModelChoices>("GET", `/colony/models?agent=${agent}`, undefined, { silent: true }),
  setAgentSettings: async (
    agent: string,
    patch: { custom_instructions?: string; paused?: boolean; model?: string },
  ) => {
    const res = await post<AgentSettings>(`/colony/agents/${agent}/settings`, patch);
    pushActivity({
      ts: new Date().toLocaleTimeString(),
      method: "COLONY", path: agent, status: "OK", ms: 0,
      detail: patch.paused !== undefined
        ? `${agent} ${patch.paused ? "paused" : "un-paused"}.`
        : patch.model !== undefined
          ? (patch.model
              ? `${agent} now runs on ${patch.model}.`
              : `${agent} reset to the default model.`)
          : `Saved standing instructions for ${agent}.`,
    });
    return res;
  },
  getColonyHandoffs: (days = 30) =>
    request<{ days: number; edges: HandoffEdge[]; recent_runs: AgentRun[] }>(
      "GET", `/colony/handoffs?days=${days}`, undefined, { silent: true },
    ),

  getColonyActions: (status = "pending") =>
    request<{ actions: ColonyAction[] }>(
      "GET", `/colony/actions?status=${status}`, undefined, { silent: true },
    ),
  resolveColonyAction: async (id: number, approve: boolean) => {
    const res = await post<{ result: string; outcome?: string; action: ColonyAction }>(
      `/colony/actions/${id}?approve=${approve}`, {},
    );
    pushActivity({
      ts: new Date().toLocaleTimeString(),
      method: "COLONY", path: `action #${id}`,
      status: res.result === "done" ? "OK" : "SKIPPED", ms: 0,
      detail: res.result === "done"
        ? `Approved and ran action #${id}: ${res.outcome ?? ""}`
        : `Declined action #${id}.`,
    });
    return res;
  },

  getGoals: (team?: string, status?: string) => {
    const q = new URLSearchParams();
    if (team) q.set("team", team);
    if (status) q.set("status", status);
    const qs = q.toString();
    return request<{ goals: TeamGoal[] }>(
      "GET", `/colony/goals${qs ? `?${qs}` : ""}`, undefined, { silent: true },
    );
  },
  createGoal: (body: { team: string; goal: string; detail?: string; target_count?: number | null }) =>
    post<TeamGoal>("/colony/goals", body),
  updateGoal: (id: number, patch: Partial<Pick<TeamGoal, "goal" | "detail" | "target_count" | "status">>) =>
    request<TeamGoal>("PATCH", `/colony/goals/${id}`, patch),
  deleteGoal: (id: number) => request<{ result: string }>("DELETE", `/colony/goals/${id}`),
  launchGoal: async (id: number, body: { kind?: string; theme?: string; language?: string | null }) => {
    const res = await post<GoalLaunchResult>(`/colony/goals/${id}/launch`, body);
    pushActivity({
      ts: new Date().toLocaleTimeString(),
      method: "COLONY", path: `goal #${id}`, status: "OK", ms: 0,
      detail: res.result === "project_created"
        ? `Goal #${id} created video project ${res.video_project_id}.`
        : `Goal #${id} started a real ${res.kind} run (job ${res.job_id}).`,
    });
    return res;
  },
  // ── The project wallet ────────────────────────────────────────────────────
  // Money moves are LOUD in the activity log by design — an irreversible
  // action must never be something you have to go looking for.
  getWalletStatus: () => get<WalletStatus>("/wallet/status"),
  getWalletBalances: () =>
    request<WalletBalances>("GET", "/wallet/balances", undefined, { silent: true }),
  getWalletHistory: (limit = 50) =>
    request<{ transactions: WalletTx[] }>(
      "GET", `/wallet/history?limit=${limit}`, undefined, { silent: true }),
  createWallet: async () => {
    const res = await post<CreatedWallet>("/wallet/create", {});
    pushActivity({
      ts: new Date().toLocaleTimeString(),
      method: "WALLET", path: "create", status: "OK", ms: 0,
      // The address is public; the key is never logged anywhere.
      detail: `Project wallet created: ${res.address}`,
    });
    return res;
  },
  addAllowlist: async (label: string, address: string, note = "") => {
    const res = await post<{ label: string; address: string }>(
      "/wallet/allowlist", { label, address, note });
    pushActivity({
      ts: new Date().toLocaleTimeString(),
      method: "WALLET", path: "approve", status: "OK", ms: 0,
      detail: `Approved "${label}" (${address}) to receive funds.`,
    });
    return res;
  },
  removeAllowlist: async (id: number) => {
    const res = await request<{ result: string }>("DELETE", `/wallet/allowlist/${id}`);
    pushActivity({
      ts: new Date().toLocaleTimeString(),
      method: "WALLET", path: "approve", status: "OK", ms: 0,
      detail: `Removed an approved address (#${id}).`,
    });
    return res;
  },
  addTreasury: (label: string, address: string) =>
    post<{ label: string; address: string }>("/wallet/treasury", { label, address }),
  removeTreasury: (id: number) =>
    request<{ result: string }>("DELETE", `/wallet/treasury/${id}`),
  walletSend: async (body: { to: string; amount: string; chain?: string; note?: string }) => {
    const res = await post<WalletSendResult>("/wallet/send", body);
    pushActivity({
      ts: new Date().toLocaleTimeString(),
      method: "WALLET", path: "send", status: "OK", ms: 0,
      detail: `Sent ${res.amount} USDC to ${res.to_label} — ${res.tx_hash}`,
    });
    return res;
  },

  consultTeam: (team: string, question: string) =>
    post<{ job_id: string; status: string; team: string }>(
      `/colony/teams/${team}/consult`, { question },
    ),

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

  // Deeds
  getDeeds: () => get<DeedsReport>("/deeds"),
  recordDeed: async (payload: { kind: "gift" | "gathering" | "digital"; count?: number; product_id?: string; note?: string }) => {
    const res = await post<RecentDeed>("/deeds", payload);
    pushActivity({
      ts: new Date().toLocaleTimeString(),
      method: "DEED", path: "", status: "OK", ms: 0,
      detail: `Recorded ${payload.kind} deed: ${payload.count ?? 1}x` + (payload.note ? ` ("${payload.note}")` : "") + (payload.product_id ? ` for product ${payload.product_id}` : "") + ".",
    });
    return res;
  },
  downloadGatheringSheet: async (productIds: string[], duplex?: boolean) => {
    const started = performance.now();
    const ts = new Date().toLocaleTimeString();
    const path = "/print-sheet";
    const res = await fetch(`${BASE}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ product_ids: productIds, duplex }),
    });
    if (!res.ok) {
      let detail = res.statusText;
      try {
        const data = await res.json();
        detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail ?? data);
      } catch {
        /* keep statusText */
      }
      pushActivity({ ts, method: "POST", path, status: res.status, ms: Math.round(performance.now() - started) });
      throw new Error(detail);
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `gathering-sheet.pdf`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    pushActivity({
      ts, method: "PRINT", path, status: "OK", ms: Math.round(performance.now() - started),
      detail: `Downloaded gathering print sheet for ${productIds.length} products.`,
    });
  },

  // ── Video Generation ───────────────────────────────────────────────────────
  // A scene/story becomes many simple 3-4s shots. Long stages (plan, frames,
  // clips) are background jobs — poll getPipelineStatus like the other pipelines.
  getVideoProviders: () => get<VideoProvidersResult>("/video/providers"),
  getVideoDefaults: () => get<VideoDefaults>("/video/defaults"),
  getVideoProjects: () => get<{ projects: VideoProject[] }>("/video/projects"),
  // Finished videos for the Products shelf. Derived server-side from the video
  // tables, so it always agrees with the Video tab.
  getFinishedVideos: () => get<{ videos: FinishedVideo[] }>("/video/finished"),
  getVideoProject: (id: string) =>
    request<VideoProjectDetail>("GET", `/video/projects/${id}`, undefined, { silent: true }),
  createVideoProject: async (payload: {
    title?: string;
    source_kind?: string;
    source_text?: string;
    source_brief?: string;
    source_instructions?: string;
    source_product_id?: string | null;
    direction?: Partial<VideoDirection>;
  }) => {
    const res = await post<VideoProject>("/video/projects", payload);
    pushActivity({
      ts: new Date().toLocaleTimeString(),
      method: "VIDEO", path: "new", status: "OK", ms: 0,
      detail: `Created video project "${res.title}" from ${payload.source_kind ?? "scene_story"}.`,
    });
    return res;
  },
  updateVideoProject: (id: string, payload: {
    title?: string; source_text?: string; source_brief?: string;
    source_instructions?: string; direction?: Partial<VideoDirection>;
    continuity?: ContinuityBible;
  }) => patch<VideoProject>(`/video/projects/${id}`, payload),
  deleteVideoProject: (id: string) =>
    request<{ result: string }>("DELETE", `/video/projects/${id}`),
  planVideo: async (id: string) => {
    const res = await post<{ job_id: string; status: string }>(`/video/projects/${id}/plan`);
    pushActivity({
      ts: new Date().toLocaleTimeString(),
      method: "VIDEO", path: id, status: "", ms: 0,
      detail: `Planning shots for video ${id} — job ${res.job_id}`,
    });
    return res;
  },
  generateVideoFrames: async (id: string, shotIds: string[] | null = null, force = false) => {
    const res = await post<{ job_id: string; status: string }>(
      `/video/projects/${id}/frames`, { shot_ids: shotIds, force });
    pushActivity({
      ts: new Date().toLocaleTimeString(),
      method: "VIDEO", path: id, status: "", ms: 0,
      detail: `Generating ${shotIds ? `${shotIds.length} shot's` : "all"} frames — job ${res.job_id}`,
    });
    return res;
  },
  generateVideoClips: async (id: string, shotIds: string[] | null = null, force = false,
                             provider?: string) => {
    const res = await post<{ job_id: string; status: string }>(
      `/video/projects/${id}/clips`, { shot_ids: shotIds, force, provider });
    pushActivity({
      ts: new Date().toLocaleTimeString(),
      method: "VIDEO", path: id, status: "", ms: 0,
      detail: `Generating ${shotIds ? `${shotIds.length} shot's` : "all"} clips — job ${res.job_id}`,
    });
    return res;
  },
  // Chained generation: each clip is rendered from the PREVIOUS clip's real
  // final frame, so the finished video reads as one continuous scene instead
  // of independently-generated shots.
  chainVideo: async (id: string, adapt = true, force = false, provider?: string) => {
    const res = await post<{ job_id: string; status: string }>(
      `/video/projects/${id}/chain`, { adapt, force, provider });
    pushActivity({
      ts: new Date().toLocaleTimeString(),
      method: "VIDEO", path: id, status: "", ms: 0,
      detail: `Chained generation started (each clip continues from the last)${adapt ? ", matching across cuts" : ""} — job ${res.job_id}`,
    });
    return res;
  },
  // Deterministic, free and synchronous — no job to poll.
  repairVideoMotion: async (id: string, recut = false) => {
    const res = await post<VideoMotionRepair>(
      `/video/projects/${id}/repair-motion`, { recut });
    pushActivity({
      ts: new Date().toLocaleTimeString(),
      method: "VIDEO", path: id, status: "", ms: 0,
      detail: `Movement check: ${res.shots_changed} of ${res.total_shots} shot(s) fixed`
        + (res.shots_recut ? `, ${res.shots_recut} recut (now a cut every ${res.seconds_per_cut}s)` : "")
        + (res.warnings.length ? `, ${res.warnings.length} repeated-action warning(s)` : ""),
    });
    return res;
  },
  cancelVideoJob: async (jobId: string) => {
    const res = await post<{ result: string }>(`/video/jobs/${jobId}/cancel`);
    pushActivity({
      ts: new Date().toLocaleTimeString(),
      method: "VIDEO", path: jobId, status: "OK", ms: 0,
      detail: `Cancelling video job ${jobId} after the current shot.`,
    });
    return res;
  },
  // Storyboard editing
  editVideoShot: (shotId: string, payload: {
    data?: Partial<VideoShotData>; locked_fields?: string[];
    approved?: boolean; continuity_mode?: string;
  }) => patch<VideoShot>(`/video/shots/${shotId}`, payload),
  addVideoShot: (projectId: string, afterNumber: number | null, data?: Partial<VideoShotData>) =>
    post<VideoShot>(`/video/projects/${projectId}/shots`, { after_number: afterNumber, data }),
  duplicateVideoShot: (shotId: string) => post<VideoShot>(`/video/shots/${shotId}/duplicate`),
  deleteVideoShot: (shotId: string) =>
    request<{ result: string }>("DELETE", `/video/shots/${shotId}`),
  reorderVideoShots: (projectId: string, shotIds: string[]) =>
    post<{ shots: VideoShot[] }>(`/video/projects/${projectId}/shots/reorder`, { shot_ids: shotIds }),
  splitVideoShot: (shotId: string) => post<{ shots: VideoShot[] }>(`/video/shots/${shotId}/split`),
  simplifyVideoShot: (shotId: string) => post<VideoShot>(`/video/shots/${shotId}/simplify`),
  mergeVideoShots: (shotId: string, otherShotId: string) =>
    post<VideoShot>(`/video/shots/${shotId}/merge`, { other_shot_id: otherShotId }),
  approveVideoShots: (projectId: string, shotIds: string[] | null, approved: boolean) =>
    post<{ updated: number; approved: boolean }>(
      `/video/projects/${projectId}/approve`, { shot_ids: shotIds, approved }),
  // Review + export
  validateVideo: (projectId: string, vision = false) =>
    get<VideoValidation>(`/video/projects/${projectId}/validate?vision=${vision}`),
  assembleVideo: async (projectId: string, onlyApproved = false, crossfade = false) => {
    const res = await post<VideoExportResult>(
      `/video/projects/${projectId}/assemble`, { only_approved: onlyApproved, crossfade });
    pushActivity({
      ts: new Date().toLocaleTimeString(),
      method: "VIDEO", path: projectId,
      status: res.video_path ? "OK" : "PARTIAL", ms: 0,
      detail: res.video_path
        ? `Assembled draft video from ${res.clip_count} clips.`
        : `Assembly incomplete: ${res.reason}`,
    });
    return res;
  },
  exportVideoMetadata: (projectId: string) =>
    get<Record<string, unknown>>(`/video/projects/${projectId}/export`),

  // Material World (nuclei) — private/nuclei.db only (rules 15 / 59)
  getNucleiSnapshot: () =>
    request<NucleiSnapshot>("GET", "/nuclei/snapshot", undefined, { silent: true }),
  createNucleiGrouping: (kind_slug: string, name: string) =>
    post<NucleiSnapshot["groupings"][number]>("/nuclei/groupings", { kind_slug, name }),
  getNucleiGrouping: (id: number) => get<NucleiGroupingDetail>(`/nuclei/groupings/${id}`),
  renameNucleiGrouping: (id: number, name: string) =>
    patch<NucleiSnapshot["groupings"][number]>(`/nuclei/groupings/${id}`, { name }),
  archiveNucleiGrouping: (id: number) =>
    post<NucleiSnapshot["groupings"][number]>(`/nuclei/groupings/${id}/archive`),
  setNucleiPosition: (id: number, x: number, y: number) =>
    patch<NucleiSnapshot>(`/nuclei/groupings/${id}/position`, { x, y }),
  optimizeNucleiLayout: () =>
    post<NucleiSnapshot>("/nuclei/layout/optimize"),
  createNucleiActor: (body: {
    kind?: string; display_name: string; how_we_met?: string;
    grouping_id?: number; introduced_as?: string; role_slug?: string;
  }) => post<NucleiActorDetail>("/nuclei/actors", body),
  patchNucleiActor: (id: number, body: { display_name?: string; how_we_met?: string }) =>
    request<NucleiActorDetail>("PATCH", `/nuclei/actors/${id}`, body),
  getNucleiActor: (id: number) => get<NucleiActorDetail>(`/nuclei/actors/${id}`),
  archiveNucleiActor: (id: number) =>
    post<NucleiSnapshot["actors"][number]>(`/nuclei/actors/${id}/archive`),
  addNucleiMembership: (actor_id: number, grouping_id: number, role_slug?: string) =>
    post<Record<string, unknown>>("/nuclei/memberships", {
      actor_id, grouping_id, ...(role_slug ? { role_slug } : {}),
    }),
  endNucleiMembership: (id: number) =>
    post<{ result: string }>(`/nuclei/memberships/${id}/end`),
  addHouseholdMember: (householdId: number, body: { person_id?: number; display_name?: string }) =>
    post<Record<string, unknown>>(`/nuclei/households/${householdId}/members`, body),
  endHouseholdMember: (id: number) =>
    post<{ result: string }>(`/nuclei/household-members/${id}/end`),
  addNucleiTie: (body: {
    kind_slug?: string; from_actor_id: number; to_actor_id: number; grouping_id?: number;
  }) => post<Record<string, unknown>>("/nuclei/ties", body),
  endNucleiTie: (id: number) =>
    post<{ result: string }>(`/nuclei/ties/${id}/end`),
  addNucleiFacet: (membership_id: number, slug: string) =>
    post<Record<string, unknown>>("/nuclei/facets", { membership_id, slug }),
  endNucleiFacet: (id: number) =>
    post<{ result: string }>(`/nuclei/facets/${id}/end`),
  satTogether: (actor_id: number) =>
    post<Record<string, unknown>>("/nuclei/activities/sat-together", { actor_id }),
  recordNucleiActivity: (body: {
    kind_slug?: string; grouping_id?: number; participant_ids: number[]; title?: string;
  }) => post<Record<string, unknown>>("/nuclei/activities", body),
  getQuietLights: () => get<NucleiQuietLights>("/nuclei/quiet-lights"),

  // The Bahá'í Workforce as a place on the Material World map (rules 65-68).
  getWorkforcePicture: () => get<WorkforcePicture>("/nuclei/workforce"),
  addWorkforcePerson: (body: { display_name?: string; actor_id?: number; role?: string }) =>
    post<{ actor_id: number; membership_id: number; snapshot: NucleiSnapshot }>(
      "/nuclei/workforce/people", body),
  removeWorkforcePerson: (membershipId: number) =>
    post<{ result: string; snapshot: NucleiSnapshot }>(
      `/nuclei/workforce/people/${membershipId}/end`),
  setNucleiChannel: (groupingId: number, body: { label?: string; link?: string;
                                                 kind?: string }) =>
    post<{ channel: NucleiChannel; snapshot: NucleiSnapshot }>(
      `/nuclei/groupings/${groupingId}/channel`, body),
  removeNucleiChannel: (channelId: number) =>
    request<{ result: string; snapshot: NucleiSnapshot }>(
      "DELETE", `/nuclei/channels/${channelId}`),
  draftWorkforceMessage: (body: {
    about: string; to_kind: "contact" | "group";
    contact_id?: number; channel_id?: number; include_recent_work?: boolean;
  }) => post<WorkforceDraft>("/nuclei/workforce/message/draft", body),
  sendWorkforceMessage: (body: { contact_id: number; message: string }) =>
    post<WorkforceSendResult>("/nuclei/workforce/message/send", body),

  // ── Live Consultation (rules 73-86) ───────────────────────────────────────
  // A separate subsystem from the product pipeline's consultation: these calls
  // reach private/consultation.db and nothing else.
  consultationCapabilities: () =>
    get<ConsultationCapabilities>("/live-consultation/capabilities"),
  listConsultations: () =>
    get<{ sessions: ConsultationSession[] }>("/live-consultation/sessions"),
  createConsultation: (body: {
    title: string; question?: string; context?: string; framework?: string;
    mode?: ConsultationMode; decision_method?: string; presence?: ConsultationPresence;
    participants?: string[]; duration_minutes?: number; warn_minutes?: number;
    record_audio?: boolean; retention_policy?: string; participants_informed?: boolean;
  }) => post<ConsultationSession>("/live-consultation/sessions", body),
  getConsultation: (id: string) =>
    get<ConsultationDetail>(`/live-consultation/sessions/${id}`),
  /** What has CHANGED since the cursors we hold (rule 115). An unchanged poll
   *  answers with the cursors and nothing else, so the cost of listening stops
   *  growing with the length of the meeting. */
  consultationUpdates: (
    id: string,
    cursors: { turns_rev: number; state_revision: number; record_revision: number },
  ) =>
    get<ConsultationUpdates>(
      `/live-consultation/sessions/${id}/updates` +
      `?turns_rev=${cursors.turns_rev}` +
      `&state_revision=${cursors.state_revision}` +
      `&record_revision=${cursors.record_revision}`),
  patchConsultation: (id: string, body: Record<string, unknown>) =>
    request<ConsultationDetail>("PATCH", `/live-consultation/sessions/${id}`, body),
  deleteConsultation: (id: string) =>
    request<{ deleted: boolean; turns: number; observations: number; audio_files: number }>(
      "DELETE", `/live-consultation/sessions/${id}`),
  startConsultation: (id: string) =>
    post<ConsultationDetail>(`/live-consultation/sessions/${id}/start`),
  endConsultation: (id: string) =>
    post<ConsultationDetail>(`/live-consultation/sessions/${id}/end`, {}),
  // Silent: transcript turns arrive every few seconds and would drown the
  // Activity Log, which exists to show Sheraj what the workforce is doing.
  addConsultationTurn: (id: string, body: {
    text: string; realtime_item_id?: string; role?: string; is_final?: boolean;
    speaker_label?: string | null;
  }) => request<{ turn: unknown }>("POST", `/live-consultation/sessions/${id}/turns`, body,
                                   { silent: true }),
  labelConsultationTurn: (id: string, turnId: number, speaker_label: string | null) =>
    post<{ turn: unknown }>(`/live-consultation/sessions/${id}/turns/${turnId}/label`,
                            { speaker_label }),
  analyzeConsultation: (id: string, force = false) =>
    request<AnalysisResult>("POST", `/live-consultation/sessions/${id}/analyze`, { force },
                            { silent: !force }),
  // The server's half of the floor gate. The client governor has already said
  // yes; nothing speaks until this one does too.
  consultationSpeechPermission: (id: string, body: Record<string, unknown>) =>
    request<SpeechDecision>("POST", `/live-consultation/sessions/${id}/speech-permission`,
                            body, { silent: true }),
  askConsultation: (id: string, body: Record<string, unknown>) =>
    post<SpeechDecision>(`/live-consultation/sessions/${id}/ask`, body),
  answerConsultationPermission: (id: string, observationId: string,
                                 body: { granted: boolean; ignored?: boolean }) =>
    post<{ granted: boolean; instructions?: string; reason?: string }>(
      `/live-consultation/sessions/${id}/observations/${observationId}/answer`, body),
  dismissConsultationObservation: (id: string, observationId: string) =>
    post<{ observation: unknown }>(
      `/live-consultation/sessions/${id}/observations/${observationId}/dismiss`),
  setConsultationObservationStatus: (id: string, observationId: string, status: string) =>
    request<{ observation: unknown }>(
      "POST", `/live-consultation/sessions/${id}/observations/${observationId}/status`,
      { status }, { silent: true }),
  confirmConsultationDecision: (id: string, decisionId: string,
                                retained_concerns: string[] = []) =>
    post<Record<string, unknown>>(
      `/live-consultation/sessions/${id}/decisions/${decisionId}/confirm`,
      { retained_concerns }),
  rejectConsultationDecision: (id: string, decisionId: string) =>
    post<Record<string, unknown>>(
      `/live-consultation/sessions/${id}/decisions/${decisionId}/reject`),
  setConsultationActionStatus: (id: string, actionId: string, status: ActionStatus) =>
    post<Record<string, unknown>>(
      `/live-consultation/sessions/${id}/actions/${actionId}/status`, { status }),

  // ── Human authority over the map (rules 95-98) ─────────────────────────
  // Until 2026-09-03 none of this existed: the map was whatever the model last
  // said, and a mishearing stayed on screen for the rest of the meeting.
  confirmConsultationInformed: (id: string) =>
    post<ConsultationDetail>(`/live-consultation/sessions/${id}/inform`),
  editConsultationMapItem: (id: string, list: string, itemId: string,
                            body: Record<string, unknown>) =>
    request<{ item: MapItem; state: ConsultationStateMap }>(
      "PATCH", `/live-consultation/sessions/${id}/map/${list}/${itemId}`, body),
  deleteConsultationMapItem: (id: string, list: string, itemId: string) =>
    request<{ deleted: boolean; state: ConsultationStateMap }>(
      "DELETE", `/live-consultation/sessions/${id}/map/${list}/${itemId}`),
  reviewConsultationMapItem: (id: string, list: string, itemId: string, reviewed = true) =>
    post<{ item: MapItem; state: ConsultationStateMap }>(
      `/live-consultation/sessions/${id}/map/${list}/${itemId}/review`, { reviewed }),
  correctConsultationTurn: (id: string, turnId: number, text: string) =>
    post<{ turn: ConsultationTurn; note: string }>(
      `/live-consultation/sessions/${id}/turns/${turnId}/text`, { text }),

  // ── Commitments ────────────────────────────────────────────────────────
  createConsultationAction: (id: string, body: {
    action: string; owner?: string | null; due?: string | null;
  }) => post<{ action_item: ConsultationAction }>(
    `/live-consultation/sessions/${id}/actions`, body),
  editConsultationAction: (id: string, actionId: string, body: Record<string, unknown>) =>
    request<{ action_item: ConsultationAction }>(
      "PATCH", `/live-consultation/sessions/${id}/actions/${actionId}`, body),
  deleteConsultationAction: (id: string, actionId: string) =>
    request<{ deleted: boolean }>(
      "DELETE", `/live-consultation/sessions/${id}/actions/${actionId}`),
  /** Record that the owner accepted — or explicitly did not. A named owner is
   *  only ever a proposal until a human calls this (rule 95). */
  acceptConsultationAction: (id: string, actionId: string,
                             accepted: boolean | null, accepted_by = "") =>
    post<{ action_item: ConsultationAction }>(
      `/live-consultation/sessions/${id}/actions/${actionId}/accept`,
      { accepted, accepted_by }),
  editConsultationDecision: (id: string, decisionId: string,
                             body: Record<string, unknown>) =>
    request<{ decision: ConsultationDecision }>(
      "PATCH", `/live-consultation/sessions/${id}/decisions/${decisionId}`, body),

  // ── Closeout and retention (rules 94, 98) ──────────────────────────────
  closeoutConsultation: (id: string, body: {
    outcome: string; note?: string; reflection_at?: string | null;
    retention_policy?: string;
  }) => post<ConsultationDetail>(`/live-consultation/sessions/${id}/closeout`, body),
  /** Irreversible. The dashboard says so before it calls this. */
  deleteConsultationTranscript: (id: string) =>
    request<ConsultationDetail>(
      "DELETE", `/live-consultation/sessions/${id}/transcript`),
  findConsultationWritings: (id: string, theme: string) =>
    post<{ theme: string; available: boolean; note: string; passages: VerifiedWriting[] }>(
      `/live-consultation/sessions/${id}/writings`, { theme }),
  consultationClientSecret: (session_id: string, accept_over_ceiling = false) =>
    post<RealtimeCredential>("/live-consultation/realtime/client-secret",
                             { session_id, accept_over_ceiling }),
  recordConsultationUsage: (id: string, usage: Record<string, unknown>, model: string) =>
    request<{ recorded: boolean; cost: number | null; reason?: string }>(
      "POST", `/live-consultation/sessions/${id}/usage`, { usage, model }, { silent: true }),
  // ── Participants, the clock, the recording and the report ───────────────
  addConsultationParticipant: (id: string, name: string) =>
    post<ConsultationParticipant>(`/live-consultation/sessions/${id}/participants`, { name }),
  removeConsultationParticipant: (id: string, participantId: string) =>
    request<{ removed: boolean }>(
      "DELETE", `/live-consultation/sessions/${id}/participants/${participantId}`),
  mapConsultationSpeaker: (id: string, participantId: string, speaker_key: string | null) =>
    post<{ participant: ConsultationParticipant; turns_labelled: number;
           final_turns: ConsultationTurn[] }>(
      `/live-consultation/sessions/${id}/participants/${participantId}/speaker`,
      { speaker_key }),
  consultationOpening: (id: string, body: Record<string, unknown>) =>
    post<ScheduledSpeech>(`/live-consultation/sessions/${id}/opening`, body),
  consultationTimeWarning: (id: string, body: Record<string, unknown>) =>
    post<ScheduledSpeech>(`/live-consultation/sessions/${id}/time-warning`, body),
  uploadConsultationAudio: (id: string, blob: Blob, filename = "meeting.webm") => {
    const form = new FormData();
    form.append("file", blob, filename);
    return upload<{ saved: boolean; bytes: number }>(
      `/live-consultation/sessions/${id}/audio`, form);
  },
  /** One piece of a recording, saved while the meeting is still running.
   *  Order is the container (rule 113): these are not interchangeable files. */
  uploadConsultationAudioChunk: (
    id: string, recordingId: string, seq: number, blob: Blob,
  ) => {
    const form = new FormData();
    form.append("file", blob, `chunk-${seq}.webm`);
    return upload<{ saved: boolean; duplicate: boolean; next_seq: number; bytes: number }>(
      `/live-consultation/sessions/${id}/audio/chunk` +
      `?recording_id=${encodeURIComponent(recordingId)}&seq=${seq}`, form);
  },
  finalizeConsultationAudio: (id: string, recordingId: string) =>
    post<{ saved: boolean; bytes: number }>(
      `/live-consultation/sessions/${id}/audio/finalize` +
      `?recording_id=${encodeURIComponent(recordingId)}`, {}),
  consultationAudioProgress: (id: string, recordingId: string) =>
    get<{ next_seq: number; bytes: number; finalized: boolean }>(
      `/live-consultation/sessions/${id}/audio/progress` +
      `?recording_id=${encodeURIComponent(recordingId)}`),
  /** Speech to text for a form field. Session-less: it is used on the setup
   *  screen, before a consultation exists. */
  dictate: (blob: Blob, filename = "note.webm") => {
    const form = new FormData();
    form.append("file", blob, filename);
    return upload<{ text: string; cost: number | null }>(
      "/live-consultation/dictate", form);
  },
  diarizeConsultation: (id: string) =>
    post<DiarizeResult>(`/live-consultation/sessions/${id}/diarize`, {}),
  makeConsultationReport: (id: string) =>
    post<ReportResult>(`/live-consultation/sessions/${id}/report`, {}),

  /** Silent on purpose: this reports a fault, and a failure to report one must
   *  not raise a second banner on top of the first. */
  reportConsultationClientError: (
    id: string, body: { message: string; event_type?: string; floor_state?: string },
  ) =>
    request<{ recorded: boolean }>(
      "POST", `/live-consultation/sessions/${id}/client-error`, body, { silent: true }),
  consultationExportUrl: (id: string) => `${BASE}/live-consultation/sessions/${id}/export`,

  // ── The concept map ────────────────────────────────────────────────────
  // Derived on every read, never a second copy (agents/live_consultation_graph.py).
  getConsultationGraph: (id: string) =>
    get<{ graph: ConceptGraph }>(`/live-consultation/sessions/${id}/graph`),
  setGraphNodeView: (id: string, nodeId: string,
                    body: { x?: number; y?: number; pinned?: boolean; collapsed?: boolean }) =>
    request<{ view: unknown; session: ConsultationSession }>(
      "PATCH", `/live-consultation/sessions/${id}/graph/nodes/${nodeId}/view`, body,
      { silent: true }),
  arrangeConsultationGraph: (id: string) =>
    post<{ graph: ConceptGraph }>(`/live-consultation/sessions/${id}/graph/arrange`, {}),
  addGraphEdge: (id: string, body: { from_id: string; to_id: string; relation: GraphRelation;
                                     label?: string }) =>
    post<{ edge: unknown; graph: ConceptGraph }>(
      `/live-consultation/sessions/${id}/graph/edges`, body),
  editGraphEdge: (id: string, edgeId: string,
                 body: { relation?: GraphRelation; label?: string }) =>
    request<{ edge: unknown; graph: ConceptGraph }>(
      "PATCH", `/live-consultation/sessions/${id}/graph/edges/${edgeId}`, body),
  rejectGraphEdge: (id: string, edgeId: string) =>
    request<{ deleted: boolean; graph: ConceptGraph }>(
      "DELETE", `/live-consultation/sessions/${id}/graph/edges/${edgeId}`),
  mergeGraphNodes: (id: string, body: { list_name: string; keep_id: string; remove_id: string;
                                        text?: string }) =>
    post<{ state: ConsultationStateMap; graph: ConceptGraph }>(
      `/live-consultation/sessions/${id}/graph/merge`, body),
  downloadGraphSvg: (id: string, title: string) =>
    downloadFile(`/live-consultation/sessions/${id}/graph/export.svg`, "GET", undefined,
                `${(title || "concept-map").replace(/[^\w \-]+/g, "").trim() || "concept-map"}.svg`),
  downloadGraphPng: (id: string, title: string) =>
    downloadFile(`/live-consultation/sessions/${id}/graph/export.png`, "GET", undefined,
                `${(title || "concept-map").replace(/[^\w \-]+/g, "").trim() || "concept-map"}.png`),
  downloadGraphHtml: (id: string, title: string) =>
    downloadFile(`/live-consultation/sessions/${id}/graph/export.html`, "GET", undefined,
                `${(title || "consultation-report").replace(/[^\w \-]+/g, "").trim()
                  || "consultation-report"}.html`),

  // Health
  health: () => get<{ status: string; service: string }>("/health"),
};

export type { PipelineResult };
