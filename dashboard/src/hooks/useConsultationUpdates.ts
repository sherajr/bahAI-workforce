import { useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { ConsultationDetail, ConsultationTurn } from "../lib/consultationTypes";

/**
 * Poll a live consultation for what CHANGED, not for all of it (rule 115).
 *
 * The panel used to refetch the entire session every four seconds -- and, since
 * the diarised transcript landed, two copies of the transcript inside it, since
 * `turns` and `final_turns` are the same rows until a speaker pass exists.
 * Measured on synthetic meetings, that was 68 KB at 60 turns and 679 KB at 600:
 * the cost of sitting in a meeting grew with how long the meeting had been
 * going on, which is precisely backwards.
 *
 * The cursor is a per-turn revision rather than the greatest turn id, because
 * the interesting changes happen to turns that ALREADY EXIST -- a line that
 * finalises late, and a line a person corrects. An id cursor would never send
 * either of them again.
 *
 * Everything is merged into the SAME react-query cache entry the full read
 * populates, so every component reading `["consultation", id]` is unchanged and
 * none of them needs to know this exists.
 */
export function useConsultationUpdates(sessionId: string | null, active: boolean,
                                       intervalMs = 4000) {
  const qc = useQueryClient();
  // A poll in flight when the session changes must not write its answer into
  // the new session's cache entry.
  const genRef = useRef(0);
  const busyRef = useRef(false);

  useEffect(() => {
    genRef.current += 1;
    if (!sessionId || !active) return;
    const myGen = genRef.current;
    const key = ["consultation", sessionId];

    const tick = async () => {
      if (busyRef.current) return;             // never overlap polls
      const current = qc.getQueryData<ConsultationDetail>(key);
      if (!current) return;                    // the full read has not landed yet
      busyRef.current = true;
      // A page of changes at the cap means there are more; take the next one
      // now rather than waiting out the interval. This used to be a plain
      // `void tick()` called from INSIDE the block below, while `busyRef`
      // was still true — the recursive call's very first line then saw
      // `busyRef.current` set and returned immediately, doing nothing, so
      // the "take the next page now" behaviour never actually ran; the
      // remaining pages waited out the full interval like anything else.
      // Recording the intent here and firing it after `finally` resets the
      // flag is what makes the immediate follow-up real.
      let more = false;
      try {
        const delta = await api.consultationUpdates(sessionId, {
          turns_rev: current.turns_rev ?? 0,
          state_revision: current.state.state_revision ?? 0,
          record_revision: current.record_revision ?? 0,
        });
        if (genRef.current !== myGen) return;   // a different session now

        if (delta.resync) {
          // The transcript was deleted, or our cursor describes rows that no
          // longer exist. Start again rather than quietly showing deleted
          // words while reporting "nothing changed" (rule 100).
          void qc.invalidateQueries({ queryKey: key });
          return;
        }
        if (!delta.changed) return;             // the cheap, common answer
        more = delta.more;

        qc.setQueryData<ConsultationDetail>(key, (prev) => {
          if (!prev) return prev;
          const next: ConsultationDetail = { ...prev };

          if (delta.turns.length) {
            // Upsert by id, then order by sequence. Upsert rather than append
            // because a changed turn arrives with the id it already had.
            const byId = new Map<number, ConsultationTurn>();
            for (const t of prev.turns) byId.set(t.id, t);
            for (const t of delta.turns) byId.set(t.id, t);
            const merged = [...byId.values()].sort((a, b) => a.sequence - b.sequence);
            next.turns = merged;
            // `final_turns` is the same record until a speaker pass exists.
            if (!delta.has_diarized) next.final_turns = merged;
          }
          next.turns_rev = delta.turns_rev;
          next.record_revision = delta.record_revision;
          next.has_diarized = delta.has_diarized;
          next.transcript_deleted = delta.transcript_deleted;
          if (delta.state) next.state = delta.state;
          if (delta.open_threads) next.open_threads = delta.open_threads;
          if (delta.decisions) next.decisions = delta.decisions;
          if (delta.confirmed_decisions) {
            next.confirmed_decisions = delta.confirmed_decisions;
            next.confirmed_decision = delta.confirmed_decisions[0] ?? null;
          }
          if (delta.action_items) next.action_items = delta.action_items;
          if (delta.participants) next.participants = delta.participants;
          if (delta.writings) next.writings = delta.writings;
          if (delta.record) next.record = delta.record;
          return next;
        });
      } catch {
        // A failed poll is not an error worth showing: the next one is four
        // seconds away, and the full read is still the source of truth. A
        // persistent failure surfaces through the connection state instead.
      } finally {
        busyRef.current = false;
      }
      if (more) void tick();
    };

    const timer = window.setInterval(() => { void tick(); }, intervalMs);
    return () => window.clearInterval(timer);
  }, [sessionId, active, intervalMs, qc]);
}
