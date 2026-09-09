import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Check, Plus, ShieldCheck, Trash2, X } from "lucide-react";
import { api } from "../../lib/api";
import type {
  ConsultationCapabilities, ConsultationDecision,
} from "../../lib/consultationTypes";
import { Button, Card, CardContent, CardHeader, CardTitle, ErrorNote, Modal } from "../ui";

/**
 * What happens when the meeting stops.
 *
 * Until 2026-09-03 "End session" went straight to the archive: whatever the
 * model had last written became, silently, the record of the meeting. Nobody
 * confirmed anything, nobody accepted anything, and a proposed owner read
 * exactly like an agreed one.
 *
 * This screen is the review that was missing. Three things about it are
 * load-bearing and should not be smoothed away:
 *
 *   1. **"No decision was reached" is a real answer.** It is offered as
 *      plainly as the others and the host is never blocked from choosing it.
 *      A meeting that genuinely decided nothing and a meeting whose decision
 *      was never recorded look identical otherwise (rule 98).
 *
 *   2. **A named owner is not an accepted commitment.** Accepting is a
 *      separate press, and when the host does it on somebody's behalf the
 *      record says so (rule 95).
 *
 *   3. **Deleting the transcript is irreversible and says so** before it
 *      happens, not after (rule 94).
 */
export function ConsultationCloseout({
  sessionId, capabilities, onDone, onSkip,
}: {
  sessionId: string;
  capabilities: ConsultationCapabilities;
  onDone: () => void;
  onSkip: () => void;
}) {
  const qc = useQueryClient();
  const [outcome, setOutcome] = useState<string>("");
  const [note, setNote] = useState("");
  const [reflection, setReflection] = useState("");
  const [retention, setRetention] = useState<string>("");
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [newAction, setNewAction] = useState("");
  const [retainDraft, setRetainDraft] = useState<Record<string, string>>({});

  const { data, error } = useQuery({
    queryKey: ["consultation", sessionId],
    queryFn: () => api.getConsultation(sessionId),
  });

  const refresh = () => void qc.invalidateQueries({ queryKey: ["consultation", sessionId] });
  const refreshAll = () => {
    refresh();
    void qc.invalidateQueries({ queryKey: ["consultations"] });
  };

  const confirmDecision = useMutation({
    mutationFn: ({ id, retained }: { id: string; retained: string[] }) =>
      api.confirmConsultationDecision(sessionId, id, retained),
    onSuccess: refreshAll,
  });
  const rejectDecision = useMutation({
    mutationFn: (id: string) => api.rejectConsultationDecision(sessionId, id),
    onSuccess: refreshAll,
  });
  const accept = useMutation({
    mutationFn: ({ id, accepted }: { id: string; accepted: boolean | null }) =>
      api.acceptConsultationAction(sessionId, id, accepted, accepted ? "the host" : ""),
    onSuccess: refresh,
  });
  const addAction = useMutation({
    mutationFn: (action: string) => api.createConsultationAction(sessionId, { action }),
    onSuccess: () => { setNewAction(""); refresh(); },
  });
  const removeAction = useMutation({
    mutationFn: (id: string) => api.deleteConsultationAction(sessionId, id),
    onSuccess: refresh,
  });
  const finish = useMutation({
    mutationFn: () => api.closeoutConsultation(sessionId, {
      outcome,
      note: note.trim(),
      reflection_at: reflection.trim() || null,
      retention_policy: retention || undefined,
    }),
    onSuccess: () => { refreshAll(); onDone(); },
  });
  const dropTranscript = useMutation({
    mutationFn: () => api.deleteConsultationTranscript(sessionId),
    onSuccess: () => { setConfirmingDelete(false); refreshAll(); },
  });

  if (error) return <ErrorNote>{(error as Error).message}</ErrorNote>;
  if (!data) return <p className="text-sm text-slate-400">Loading…</p>;

  const { session, state, decisions, action_items, turns } = data;
  const candidates = decisions.filter((d) => d.status === "candidate");
  const confirmed = decisions.filter((d) => d.status === "confirmed");
  const agreements = (state.agreements ?? []).filter((i) => i.text);
  const stillOpen = [...(state.tensions ?? []), ...(state.unresolved_questions ?? [])]
    .filter((i) => i.text && (i.lifecycle ?? "open") === "open");

  const retentionLabel = (capabilities.retention_policies ?? [])
    .find((r) => r.id === (retention || session.retention_policy))?.label ?? "";

  return (
    <div className="mx-auto max-w-3xl space-y-4 pb-10">
      <div className="flex items-center justify-between gap-4">
        <div>
          <h2 className="font-display text-lg text-slate-100">Closing the consultation</h2>
          <p className="text-sm text-slate-400">
            {session.title} · listening has stopped. Nothing below is recorded as the
            group&rsquo;s until you say so.
          </p>
        </div>
        <Button variant="ghost" onClick={onSkip}>Finish later</Button>
      </div>

      {/* 1 — what the assistant made of it, clearly labelled as that */}
      <Card>
        <CardHeader><CardTitle>What {capabilities.assistant_name} understood</CardTitle></CardHeader>
        <CardContent className="space-y-3 text-sm text-slate-300">
          {(state.summary ?? "").trim()
            ? <p>{state.summary}</p>
            : <p className="text-slate-500">She did not have enough to summarise.</p>}
          {agreements.length > 0 && (
            <div>
              <p className="text-xs uppercase tracking-wide text-slate-500">Apparent agreement</p>
              <ul className="mt-1 list-disc space-y-1 pl-5">
                {agreements.map((i) => <li key={i.id}>{i.text}</li>)}
              </ul>
            </div>
          )}
          {stillOpen.length > 0 && (
            <div>
              <p className="text-xs uppercase tracking-wide text-slate-500">
                Still standing
              </p>
              <ul className="mt-1 list-disc space-y-1 pl-5 text-amber-200/80">
                {stillOpen.map((i) => <li key={i.id}>{i.text}</li>)}
              </ul>
              <p className="mt-1 text-xs text-slate-500">
                These stay in the record whatever you decide. They are not deleted.
              </p>
            </div>
          )}
        </CardContent>
      </Card>

      {/* 2 — decisions. Only a human gets here (rule 81). */}
      <Card>
        <CardHeader><CardTitle>Was anything decided?</CardTitle></CardHeader>
        <CardContent className="space-y-3">
          {confirmed.map((d) => (
            <ConfirmedRow key={d.id} decision={d} />
          ))}
          {candidates.length === 0 && confirmed.length === 0 && (
            <p className="text-sm text-slate-500">
              Nothing was noticed that looked like a decision. That is a perfectly
              ordinary way for a consultation to go.
            </p>
          )}
          {candidates.map((d) => (
            <div key={d.id} className="rounded-lg border border-slate-800 bg-slate-900/50 p-3">
              <p className="text-sm text-slate-200">{d.text}</p>
              {d.rationale && (
                <p className="mt-1 text-xs text-slate-500">Why: {d.rationale}</p>
              )}
              {d.concerns.length > 0 && (
                <ul className="mt-2 list-disc space-y-0.5 pl-5 text-xs text-amber-200/80">
                  {d.concerns.map((c, n) => <li key={n}>{c}</li>)}
                </ul>
              )}
              <input
                value={retainDraft[d.id] ?? ""}
                onChange={(e) => setRetainDraft((s) => ({ ...s, [d.id]: e.target.value }))}
                placeholder="A concern to carry forward with this decision (optional)"
                className="mt-2 w-full rounded border border-slate-800 bg-slate-950 px-2 py-1.5
                           text-xs text-slate-100 placeholder:text-slate-600
                           focus:border-amber-400/50 focus:outline-none"
              />
              <div className="mt-2 flex gap-2">
                <Button
                  onClick={() => confirmDecision.mutate({
                    id: d.id,
                    retained: (retainDraft[d.id] ?? "").trim()
                      ? [(retainDraft[d.id] ?? "").trim()] : [],
                  })}
                  loading={confirmDecision.isPending}>
                  <Check className="h-4 w-4" />
                  The group confirmed this
                </Button>
                <Button variant="secondary" onClick={() => rejectDecision.mutate(d.id)}>
                  <X className="h-4 w-4" />
                  Not a decision
                </Button>
              </div>
            </div>
          ))}
          <div className="space-y-1.5 pt-1">
            <p className="text-xs uppercase tracking-wide text-slate-500">
              How did this meeting end?
            </p>
            {(capabilities.closeout_outcomes ?? []).map((o) => (
              <label key={o.id}
                     className={`flex cursor-pointer items-center gap-2 rounded border px-3 py-2
                                 text-sm ${outcome === o.id
                                   ? "border-amber-400/50 bg-amber-400/5 text-slate-100"
                                   : "border-slate-800 text-slate-300"}`}>
                <input type="radio" name="outcome" value={o.id}
                       checked={outcome === o.id}
                       onChange={() => setOutcome(o.id)}
                       className="accent-amber-400" />
                {o.label}
              </label>
            ))}
            <textarea
              value={note} onChange={(e) => setNote(e.target.value)} rows={2}
              placeholder="Anything to add about how it ended (optional)"
              className="mt-1 w-full rounded-lg border border-slate-800 bg-slate-950 px-3 py-2
                         text-sm text-slate-100 placeholder:text-slate-600
                         focus:border-amber-400/50 focus:outline-none"
            />
          </div>
        </CardContent>
      </Card>

      {/* 3 — commitments. A name is not an agreement (rule 95). */}
      <Card>
        <CardHeader><CardTitle>Who is doing what</CardTitle></CardHeader>
        <CardContent className="space-y-2">
          {action_items.filter((a) => a.status !== "dropped").length === 0 && (
            <p className="text-sm text-slate-500">Nothing was recorded to do.</p>
          )}
          {action_items.filter((a) => a.status !== "dropped").map((a) => (
            <div key={a.id}
                 className="rounded-lg border border-slate-800 bg-slate-900/50 p-3">
              <p className="text-sm text-slate-200">{a.action}</p>
              <p className="mt-0.5 text-xs text-slate-500">
                {a.owner ?? "Owner not assigned"}
                {a.due ? ` · due ${a.due}` : ""}
              </p>
              <div className="mt-2 flex flex-wrap items-center gap-2">
                {a.owner_accepted === true ? (
                  <span className="text-xs text-emerald-300">
                    Accepted{a.accepted_by ? ` (recorded by ${a.accepted_by})` : ""}
                  </span>
                ) : a.owner_accepted === false ? (
                  <span className="text-xs text-slate-400">Did not accept</span>
                ) : (
                  <span className="text-xs text-amber-200/80">Not yet accepted</span>
                )}
                <Button variant="secondary"
                        onClick={() => accept.mutate({ id: a.id, accepted: true })}>
                  Accepted
                </Button>
                <Button variant="ghost"
                        onClick={() => accept.mutate({ id: a.id, accepted: false })}>
                  Did not
                </Button>
                <Button variant="ghost" onClick={() => removeAction.mutate(a.id)}>
                  <Trash2 className="h-3.5 w-3.5" />
                </Button>
              </div>
            </div>
          ))}
          <div className="flex gap-2 pt-1">
            <input
              value={newAction} onChange={(e) => setNewAction(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && newAction.trim()) addAction.mutate(newAction.trim());
              }}
              placeholder="Add something she missed"
              className="flex-1 rounded-lg border border-slate-800 bg-slate-950 px-3 py-2
                         text-sm text-slate-100 placeholder:text-slate-600
                         focus:border-amber-400/50 focus:outline-none"
            />
            <Button variant="secondary" disabled={!newAction.trim()}
                    onClick={() => addAction.mutate(newAction.trim())}>
              <Plus className="h-4 w-4" />
            </Button>
          </div>
          <p className="text-xs text-slate-500">
            Recording that somebody accepted is your word for them unless they pressed it
            themselves. The record says which it was.
          </p>
        </CardContent>
      </Card>

      {/* 4 — the words, and what happens to them */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <ShieldCheck className="h-4 w-4 text-amber-300" />
            The transcript
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm text-slate-300">
          {session.transcript_deleted_at ? (
            <p className="text-slate-400">
              The transcript was deleted on {session.transcript_deleted_at}. The record
              below is what was kept.
            </p>
          ) : (
            <>
              <p>
                {turns.length} line{turns.length === 1 ? "" : "s"} were written down. They
                are stored on this machine, unencrypted.
              </p>
              <select value={retention || session.retention_policy}
                      onChange={(e) => setRetention(e.target.value)}
                      className="w-full rounded-lg border border-slate-800 bg-slate-950 px-3
                                 py-2 text-sm text-slate-100 focus:border-amber-400/50
                                 focus:outline-none">
                {(capabilities.retention_policies ?? []).map((r) => (
                  <option key={r.id} value={r.id}>{r.label}</option>
                ))}
              </select>
              <Button variant="danger" onClick={() => setConfirmingDelete(true)}>
                <Trash2 className="h-4 w-4" />
                Delete the transcript now, keep the record
              </Button>
            </>
          )}
        </CardContent>
      </Card>

      {/* 5 — coming back to it */}
      <Card>
        <CardHeader><CardTitle>Looking back at this later</CardTitle></CardHeader>
        <CardContent className="space-y-2">
          <input
            type="date" value={reflection} onChange={(e) => setReflection(e.target.value)}
            className="w-full rounded-lg border border-slate-800 bg-slate-950 px-3 py-2
                       text-sm text-slate-100 focus:border-amber-400/50 focus:outline-none"
          />
          <p className="text-xs text-slate-500">
            Optional. This records a date to come back to what was decided; nothing
            reminds you yet, so treat it as a note in the record rather than an alarm.
          </p>
        </CardContent>
      </Card>

      {finish.error && <ErrorNote>{(finish.error as Error).message}</ErrorNote>}
      {dropTranscript.error && <ErrorNote>{(dropTranscript.error as Error).message}</ErrorNote>}

      <div className="flex items-center justify-end gap-3">
        <span className="mr-auto text-xs text-slate-500">
          {retentionLabel}
        </span>
        <Button variant="secondary" onClick={onSkip}>Finish later</Button>
        <Button onClick={() => finish.mutate()} loading={finish.isPending}
                disabled={!outcome}
                title={outcome ? undefined : "Say how the meeting ended first."}>
          Save the record
        </Button>
      </div>

      <Modal open={confirmingDelete} onClose={() => setConfirmingDelete(false)}
             title="Delete the transcript?">
        <div className="space-y-3 text-sm text-slate-300">
          <p className="flex items-start gap-2 text-amber-200/90">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            This cannot be undone. Every line of what was said is deleted, along with any
            recording.
          </p>
          <p>
            What is kept: the report, the decisions, what people agreed to do, the
            concerns that were left standing, and the verified passages.
          </p>
          <p className="text-slate-400">
            After this there is no full export of the meeting — only the record.
          </p>
          <div className="flex justify-end gap-2 pt-1">
            <Button variant="secondary" onClick={() => setConfirmingDelete(false)}>
              Keep it
            </Button>
            <Button variant="danger" onClick={() => dropTranscript.mutate()}
                    loading={dropTranscript.isPending}>
              Delete the transcript
            </Button>
          </div>
        </div>
      </Modal>
    </div>
  );
}

function ConfirmedRow({ decision }: { decision: ConsultationDecision }) {
  return (
    <div className="rounded-lg border border-emerald-400/30 bg-emerald-400/5 p-3">
      <p className="text-xs uppercase tracking-wide text-emerald-300">Confirmed</p>
      <p className="mt-1 text-sm text-slate-100">{decision.text}</p>
      {decision.retained_concerns?.length > 0 && (
        <>
          <p className="mt-2 text-xs text-amber-200/80">
            Carried forward with it:
          </p>
          <ul className="list-disc space-y-0.5 pl-5 text-xs text-amber-200/80">
            {decision.retained_concerns.map((c, n) => <li key={n}>{c}</li>)}
          </ul>
        </>
      )}
    </div>
  );
}
