// Quality-gate settings, persisted in localStorage and sent on each pipeline run.

export interface Settings {
  targetScore: number; // 6–10, step 0.5
  maxAttempts: number; // 1–5
}

const KEY = "bahai.workforce.settings";
export const DEFAULT_SETTINGS: Settings = { targetScore: 9.0, maxAttempts: 3 };

export function getSettings(): Settings {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return DEFAULT_SETTINGS;
    const parsed = JSON.parse(raw) as Partial<Settings>;
    return {
      targetScore: clamp(Number(parsed.targetScore ?? 9.0), 6, 10),
      maxAttempts: clamp(Math.round(Number(parsed.maxAttempts ?? 3)), 1, 5),
    };
  } catch {
    return DEFAULT_SETTINGS;
  }
}

export function saveSettings(s: Settings) {
  localStorage.setItem(KEY, JSON.stringify(s));
}

const JOB_KEY = "bahai.workforce.activeJob";
export function getActiveJobId(): string | null {
  return localStorage.getItem(JOB_KEY);
}
export function setActiveJobId(id: string | null) {
  if (id) localStorage.setItem(JOB_KEY, id);
  else localStorage.removeItem(JOB_KEY);
}

// Separate slot for the Post to X tab's active job — distinct from the
// Pipeline tab's above so starting one doesn't clobber the other's reattach.
const X_POST_JOB_KEY = "bahai.workforce.activeXPostJob";
export function getActiveXPostJobId(): string | null {
  return localStorage.getItem(X_POST_JOB_KEY);
}
export function setActiveXPostJobId(id: string | null) {
  if (id) localStorage.setItem(X_POST_JOB_KEY, id);
  else localStorage.removeItem(X_POST_JOB_KEY);
}

// Video tab UI state. The Video pipeline is long-running and multi-stage, so
// losing your place when you switch tabs (React unmounts the panel) or close
// the window is genuinely disruptive — this keeps the open project, the
// sub-tab and any running job across both.
export interface VideoUiState {
  projectId: string | null;
  tab: string | null;
  jobId: string | null;
}

const VIDEO_KEY = "bahai.workforce.videoUi";
const EMPTY_VIDEO_UI: VideoUiState = { projectId: null, tab: null, jobId: null };

export function getVideoUi(): VideoUiState {
  try {
    const raw = localStorage.getItem(VIDEO_KEY);
    if (!raw) return EMPTY_VIDEO_UI;
    const p = JSON.parse(raw) as Partial<VideoUiState>;
    return {
      projectId: typeof p.projectId === "string" ? p.projectId : null,
      tab: typeof p.tab === "string" ? p.tab : null,
      jobId: typeof p.jobId === "string" ? p.jobId : null,
    };
  } catch {
    return EMPTY_VIDEO_UI;
  }
}

export function patchVideoUi(patch: Partial<VideoUiState>) {
  const next = { ...getVideoUi(), ...patch };
  localStorage.setItem(VIDEO_KEY, JSON.stringify(next));
}

// Products tab filters. The shelf holds bookmarks, quote cards AND finished
// videos now, so "which kind am I looking at" and "how is it sorted" are
// standing preferences worth keeping across tab switches.
//
// The SEARCH TEXT is deliberately NOT persisted: a typed query is momentary,
// and coming back to a nearly-empty shelf because of a search you forgot you
// typed is the one failure this whole bar exists to avoid.
export interface ProductsUiState {
  kind: string;   // "all" | "bookmark" | "quote_card" | "video"
  badge: string;  // "all" | EXCEPTIONAL | APPROVED | BORDERLINE | REJECTED | BEST EFFORT
  sort: string;   // "newest" | "oldest" | "score" | "score_low" | "title"
}

const PRODUCTS_KEY = "bahai.workforce.productsUi";
export const DEFAULT_PRODUCTS_UI: ProductsUiState = {
  kind: "all", badge: "all", sort: "newest",
};

export function getProductsUi(): ProductsUiState {
  try {
    const raw = localStorage.getItem(PRODUCTS_KEY);
    if (!raw) return DEFAULT_PRODUCTS_UI;
    const p = JSON.parse(raw) as Partial<ProductsUiState>;
    return {
      kind: typeof p.kind === "string" ? p.kind : "all",
      badge: typeof p.badge === "string" ? p.badge : "all",
      sort: typeof p.sort === "string" ? p.sort : "newest",
    };
  } catch {
    return DEFAULT_PRODUCTS_UI;
  }
}

export function patchProductsUi(patch: Partial<ProductsUiState>) {
  const next = { ...getProductsUi(), ...patch };
  localStorage.setItem(PRODUCTS_KEY, JSON.stringify(next));
}

// Colony tab UI state — same reasoning as the Video tab above: switching tabs
// unmounts the panel, and losing the selected agent, the open view and a
// running team consultation every time you look elsewhere makes it unusable.
export interface ColonyUiState {
  view: string | null;      // "map" | "performance" | "handoffs" | "goals"
  agent: string | null;     // selected agent id
  team: string | null;      // selected team id
  consultJobId: string | null;
  world: string | null;     // "digital" | "real"
  rwScale: number | null;   // Material World camera
  rwPanX: number | null;
  rwPanY: number | null;
}

const COLONY_KEY = "bahai.workforce.colonyUi";
const EMPTY_COLONY_UI: ColonyUiState = {
  view: null, agent: null, team: null, consultJobId: null, world: null,
  rwScale: null, rwPanX: null, rwPanY: null,
};

export function getColonyUi(): ColonyUiState {
  try {
    const raw = localStorage.getItem(COLONY_KEY);
    if (!raw) return EMPTY_COLONY_UI;
    const p = JSON.parse(raw) as Partial<ColonyUiState>;
    return {
      view: typeof p.view === "string" ? p.view : null,
      agent: typeof p.agent === "string" ? p.agent : null,
      team: typeof p.team === "string" ? p.team : null,
      consultJobId: typeof p.consultJobId === "string" ? p.consultJobId : null,
      world: p.world === "real" || p.world === "digital" ? p.world : null,
      rwScale: typeof p.rwScale === "number" && Number.isFinite(p.rwScale) ? p.rwScale : null,
      rwPanX: typeof p.rwPanX === "number" && Number.isFinite(p.rwPanX) ? p.rwPanX : null,
      rwPanY: typeof p.rwPanY === "number" && Number.isFinite(p.rwPanY) ? p.rwPanY : null,
    };
  } catch {
    return EMPTY_COLONY_UI;
  }
}

export function patchColonyUi(patch: Partial<ColonyUiState>) {
  const next = { ...getColonyUi(), ...patch };
  localStorage.setItem(COLONY_KEY, JSON.stringify(next));
}

function clamp(n: number, lo: number, hi: number): number {
  return isNaN(n) ? lo : Math.min(hi, Math.max(lo, n));
}

// Live Consultation tab state. A consultation is long and the panel unmounts
// when Sheraj looks at another tab; losing the open session and the view would
// be the same disruption the Video tab's persistence exists to prevent
// (AGENTS.md rule 33c). The live session itself is deliberately NOT resumed
// from here — a realtime connection needs a fresh credential and a fresh
// microphone permission, both of which are the user's to give.
export interface ConsultationUiState {
  view: string | null;      // "archive" | "setup" | "live" | "summary"
  sessionId: string | null;
}

const CONSULTATION_KEY = "bahai.workforce.consultationUi";
const EMPTY_CONSULTATION_UI: ConsultationUiState = { view: null, sessionId: null };

export function getConsultationUi(): ConsultationUiState {
  try {
    const raw = localStorage.getItem(CONSULTATION_KEY);
    if (!raw) return EMPTY_CONSULTATION_UI;
    const p = JSON.parse(raw) as Partial<ConsultationUiState>;
    return {
      view: typeof p.view === "string" ? p.view : null,
      sessionId: typeof p.sessionId === "string" ? p.sessionId : null,
    };
  } catch {
    return EMPTY_CONSULTATION_UI;
  }
}

export function patchConsultationUi(patch: Partial<ConsultationUiState>) {
  const next = { ...getConsultationUi(), ...patch };
  localStorage.setItem(CONSULTATION_KEY, JSON.stringify(next));
}
