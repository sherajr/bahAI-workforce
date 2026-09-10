/**
 * A guard on leaving a screen that is doing something irreversible (rule 114).
 *
 * The one real case is a live consultation: the microphone is open, a paid
 * realtime session is running, and audio is being recorded. Switching tabs
 * unmounts that panel — App has always done this, deliberately — so a stray
 * click on "Products" used to end a meeting's listening with no warning, no
 * choice and no record of what happened to it.
 *
 * Deliberately tiny, and deliberately NOT a router. This dashboard has one
 * piece of state for which screen is showing; introducing a routing library to
 * get a confirmation dialog would be a much larger change than the problem
 * warrants, and every other tab is safe to leave at any moment.
 *
 * What it is not: a promise about closing the browser tab. `beforeunload` can
 * show the browser's own generic prompt and nothing more — no custom dialog, no
 * reliable request — so nothing here pretends otherwise. That case is handled
 * by the recording being saved as it is made (rule 113), not by a dialog.
 */

export type LeaveDecision = "stay" | "leave";

/** Asked when something wants to navigate away. Returns what to do. */
export type LeaveGuard = () => Promise<LeaveDecision>;

let guard: LeaveGuard | null = null;

/** Register the current screen's guard. Returns the unregister function. */
export function registerLeaveGuard(fn: LeaveGuard): () => void {
  guard = fn;
  return () => {
    if (guard === fn) guard = null;
  };
}

export function hasLeaveGuard(): boolean {
  return guard !== null;
}

/**
 * Ask whoever is guarding whether we may leave. Always resolves; a guard that
 * throws is treated as "yes", because a broken confirmation dialog must never
 * trap somebody on a screen they are trying to leave.
 */
export async function confirmLeave(): Promise<boolean> {
  if (!guard) return true;
  try {
    return (await guard()) === "leave";
  } catch {
    return true;
  }
}
