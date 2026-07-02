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

function clamp(n: number, lo: number, hi: number): number {
  return isNaN(n) ? lo : Math.min(hi, Math.max(lo, n));
}
