import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft, BookOpen, Download, FileText, Plus, Trash2,
} from "lucide-react";
import { api } from "../lib/api";
import type {
  AvailableOutcomes, GatheringCommitment, GatheringDetail, GatheringSummary,
  ProgramItem,
} from "../lib/types";
import {
  BadgePill, Button, Card, CardContent, ErrorNote, Modal,
} from "./ui";

/**
 * A gathering, from preparing to reflecting (rules 117-119).
 *
 * The sequence this screen is built around is the one the work actually
 * follows: name the purpose → consult → approve the record → confirm who
 * accepted what → prepare the materials → print them → record what happened and
 * what was learned.
 *
 * Three things it is careful to be honest about, because each is a place a tool
 * like this usually lies:
 *   * A commitment is only shown as accepted when a person recorded that
 *     (rule 101). A name mentioned in a meeting is a proposal.
 *   * An outcome can only be carried forward from a consultation whose record
 *     somebody APPROVED, and when it cannot, the reason is on screen rather
 *     than the section simply looking empty (rule 118).
 *   * Nothing here generates anything or spends anything. Cards come from the
 *     shelf; making new ones is a separate, explicit act in the Pipeline tab
 *     with its own review and its own metering.
 */

const PROGRAM_KIND_LABELS: Record<string, string> = {
  welcome: "Welcome",
  reading: "Reading",
  prayer: "Prayer",
  reflection: "Reflection",
  music: "Music",
  refreshments: "Refreshments",
  note: "Note",
};

function acceptance(c: GatheringCommitment): { label: string; tone: string } {
  if (c.owner_accepted === true) return { label: "Accepted", tone: "text-emerald-300" };
  if (c.owner_accepted === false) return { label: "Did not accept", tone: "text-rose-300" };
  // The honest third state. It is not a failure and it is not agreement.
  return { label: "Nobody has answered yet", tone: "text-slate-400" };
}

// ── The list ──────────────────────────────────────────────────────────────────

function GatheringList({ onOpen }: { onOpen: (id: string) => void }) {
  const qc = useQueryClient();
  const [title, setTitle] = useState("");
  const [purpose, setPurpose] = useState("");
  const [when, setWhen] = useState("");

  const list = useQuery({ queryKey: ["gatherings"], queryFn: api.listGatherings });
  const create = useMutation({
    mutationFn: () => api.createGathering({
      title, purpose,
      gathering_at: when || null,
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    }),
    onSuccess: (detail) => {
      void qc.invalidateQueries({ queryKey: ["gatherings"] });
      setTitle(""); setPurpose(""); setWhen("");
      onOpen(detail.project.id);
    },
  });

  return (
    <div className="mx-auto max-w-3xl space-y-5">
      <header className="space-y-1">
        <h1 className="font-display text-2xl text-slate-100">Gatherings</h1>
        <p className="text-sm text-slate-400">
          A gathering, a study circle, a visit, a service project — anything the group
          prepares for, consults about, does, and then reflects on.
        </p>
      </header>

      <Card>
        <CardContent className="space-y-3 pt-5">
          <div className="text-sm font-medium text-slate-200">Prepare a gathering</div>
          <label className="block text-xs text-slate-400">
            What is it?
            <input
              value={title} onChange={(e) => setTitle(e.target.value)}
              placeholder="Friday devotional at Nasrin's"
              className="mt-1 w-full rounded border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100"
            />
          </label>
          <label className="block text-xs text-slate-400">
            Why — the purpose, in your own words
            <textarea
              value={purpose} onChange={(e) => setPurpose(e.target.value)}
              rows={2}
              placeholder="To pray together and invite the neighbours we have been visiting."
              className="mt-1 w-full rounded border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100"
            />
          </label>
          <label className="block text-xs text-slate-400">
            When (optional — you can decide later)
            <input
              type="datetime-local" value={when} onChange={(e) => setWhen(e.target.value)}
              className="mt-1 w-full rounded border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100"
            />
          </label>
          {create.error && <ErrorNote>{(create.error as Error).message}</ErrorNote>}
          <Button onClick={() => create.mutate()} loading={create.isPending}
                  disabled={!title.trim()}>
            <Plus className="h-4 w-4" /> Start preparing
          </Button>
        </CardContent>
      </Card>

      {list.isError && (
        <ErrorNote>Could not load gatherings: {(list.error as Error).message}</ErrorNote>
      )}
      {list.data?.projects.length === 0 && (
        <Card>
          <CardContent className="pt-5 text-sm text-slate-400">
            Nothing here yet. The box above is the start of one.
          </CardContent>
        </Card>
      )}
      <div className="space-y-2">
        {(list.data?.projects ?? []).map((p: GatheringSummary) => (
          <button
            key={p.id} type="button" onClick={() => onOpen(p.id)}
            className="flex w-full flex-wrap items-center gap-3 rounded-lg border border-slate-800 bg-slate-900/60 px-4 py-3 text-left hover:border-amber-400/40"
          >
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm text-slate-100">{p.title}</div>
              <div className="mt-0.5 text-xs text-slate-500">
                {p.stage_info?.label ?? p.stage}
                {p.gathering_at ? ` · ${p.gathering_at}` : ""}
                {p.session_count ? ` · ${p.session_count} consultation(s)` : ""}
                {p.item_count ? ` · ${p.item_count} item(s)` : ""}
              </div>
            </div>
            {/* Counted, never scored (rule 61). */}
            {p.commitments_total > 0 && (
              <BadgePill className="border-slate-700 text-slate-300">
                {p.commitments_open} of {p.commitments_total} still open
              </BadgePill>
            )}
          </button>
        ))}
      </div>
    </div>
  );
}

// ── One gathering ─────────────────────────────────────────────────────────────

function OutcomesSection({ id, detail, refresh }: {
  id: string; detail: GatheringDetail; refresh: () => void;
}) {
  const available = useQuery<AvailableOutcomes>({
    queryKey: ["gathering-outcomes", id],
    queryFn: () => api.gatheringAvailableOutcomes(id),
  });
  const add = useMutation({
    mutationFn: (o: { kind: string; text: string; session_id: string; ref_id: string }) =>
      api.addGatheringOutcome(id, o),
    onSuccess: refresh,
  });
  const drop = useMutation({
    mutationFn: (oid: string) => api.removeGatheringOutcome(id, oid),
    onSuccess: refresh,
  });

  const chosen = new Set(detail.outcomes.map((o) => `${o.kind}:${o.text}`));

  return (
    <Card>
      <CardContent className="space-y-3 pt-5">
        <div className="text-sm font-medium text-slate-200">
          What this gathering is carrying forward
        </div>
        {detail.outcomes.length === 0 && (
          <div className="text-xs text-slate-500">
            Nothing chosen yet. Only decisions and questions from a consultation whose
            record has been approved can be carried forward.
          </div>
        )}
        {detail.outcomes.map((o) => (
          <div key={o.id}
               className="flex items-start gap-3 rounded border border-slate-800 bg-slate-950/50 px-3 py-2">
            <BadgePill className="border-slate-700 text-slate-400">{o.kind}</BadgePill>
            <div className="min-w-0 flex-1 text-sm text-slate-200">{o.text}</div>
            <button onClick={() => drop.mutate(o.id)} aria-label="Remove this outcome"
                    className="text-slate-500 hover:text-rose-300">
              <Trash2 className="h-4 w-4" />
            </button>
          </div>
        ))}

        {/* Not eligible YET, with the reason. "Nothing here" and "you have not
            approved it" are different things to be told (rule 118). */}
        {(available.data?.waiting ?? []).map((w) => (
          <div key={w.session_id}
               className="rounded border border-amber-400/30 bg-amber-400/5 px-3 py-2 text-xs text-amber-200">
            <span className="font-medium">{w.title}:</span> {w.reason}
          </div>
        ))}

        {(available.data?.offered ?? []).length > 0 && (
          <div className="space-y-2 pt-1">
            <div className="text-xs uppercase tracking-wider text-slate-500">
              Available to carry forward
            </div>
            {available.data!.offered.map((o, i) => (
              <div key={`${o.ref_id}-${i}`}
                   className="flex items-start gap-3 rounded border border-slate-800 px-3 py-2">
                <BadgePill className="border-slate-700 text-slate-400">{o.kind}</BadgePill>
                <div className="min-w-0 flex-1 text-sm text-slate-300">
                  {o.text}
                  {(o.retained_concerns ?? []).length > 0 && (
                    <div className="mt-1 text-xs text-amber-200">
                      Concerns still standing: {(o.retained_concerns ?? []).join("; ")}
                    </div>
                  )}
                </div>
                <Button
                  variant="secondary"
                  disabled={chosen.has(`${o.kind}:${o.text}`)}
                  onClick={() => add.mutate({
                    kind: o.kind, text: o.text,
                    session_id: o.session_id, ref_id: o.ref_id,
                  })}
                >
                  {chosen.has(`${o.kind}:${o.text}`) ? "Added" : "Carry forward"}
                </Button>
              </div>
            ))}
          </div>
        )}
        {add.error && <ErrorNote>{(add.error as Error).message}</ErrorNote>}
      </CardContent>
    </Card>
  );
}

function ProgramSection({ id, detail, refresh }: {
  id: string; detail: GatheringDetail; refresh: () => void;
}) {
  const [kind, setKind] = useState("reflection");
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [minutes, setMinutes] = useState("");
  const [writingId, setWritingId] = useState("");

  const add = useMutation({
    mutationFn: () => api.addProgramItem(id, {
      kind, title, body,
      minutes: minutes ? Number(minutes) : null,
      writing_id: kind === "reading" ? writingId : "",
    }),
    onSuccess: () => { refresh(); setTitle(""); setBody(""); setMinutes(""); },
  });
  const drop = useMutation({
    mutationFn: (itemId: string) => api.removeProgramItem(id, itemId),
    onSuccess: refresh,
  });
  const move = useMutation({
    mutationFn: (ids: string[]) => api.reorderProgram(id, ids),
    onSuccess: refresh,
  });

  const reorder = (from: number, to: number) => {
    const ids = detail.program.map((p) => p.id);
    if (to < 0 || to >= ids.length) return;
    const [moved] = ids.splice(from, 1);
    ids.splice(to, 0, moved);
    move.mutate(ids);
  };

  const byId = useMemo(
    () => Object.fromEntries(detail.writings.map((w) => [String(w.id), w])),
    [detail.writings]);

  return (
    <Card>
      <CardContent className="space-y-3 pt-5">
        <div className="flex items-baseline justify-between gap-3">
          <div className="text-sm font-medium text-slate-200">The programme</div>
          <div className="text-xs text-slate-500">
            {/* null means nobody put times against the items -- not zero, and
                not a guess (rule 117). */}
            {detail.program_minutes === null
              ? "No timings given"
              : `about ${detail.program_minutes} minutes`}
          </div>
        </div>

        {detail.program.map((item: ProgramItem, i: number) => (
          <div key={item.id}
               className="flex items-start gap-3 rounded border border-slate-800 bg-slate-950/50 px-3 py-2">
            <div className="flex flex-col gap-0.5 pt-0.5">
              <button onClick={() => reorder(i, i - 1)} disabled={i === 0}
                      aria-label="Move earlier"
                      className="text-xs text-slate-500 hover:text-slate-200 disabled:opacity-30">▲</button>
              <button onClick={() => reorder(i, i + 1)}
                      disabled={i === detail.program.length - 1}
                      aria-label="Move later"
                      className="text-xs text-slate-500 hover:text-slate-200 disabled:opacity-30">▼</button>
            </div>
            <BadgePill className="border-slate-700 text-slate-400">
              {PROGRAM_KIND_LABELS[item.kind] ?? item.kind}
            </BadgePill>
            <div className="min-w-0 flex-1">
              <div className="text-sm text-slate-100">
                {item.title || "(untitled)"}
                {item.minutes ? (
                  <span className="ml-2 text-xs text-slate-500">{item.minutes} min</span>
                ) : null}
              </div>
              {item.writing_id && byId[item.writing_id] && (
                <div className="mt-1 rounded bg-slate-900/70 px-2 py-1 text-xs italic text-slate-300">
                  “{byId[item.writing_id].text}”
                  <div className="mt-0.5 not-italic text-slate-500">
                    {byId[item.writing_id].source}
                  </div>
                </div>
              )}
              {item.writing_id && !byId[item.writing_id] && (
                <div className="mt-1 text-xs text-amber-200">
                  The verified passage for this reading is no longer in the record. Nothing
                  has been substituted for it.
                </div>
              )}
              {item.body && (
                <div className="mt-1 whitespace-pre-wrap text-xs text-slate-400">
                  {item.body}
                </div>
              )}
            </div>
            <button onClick={() => drop.mutate(item.id)} aria-label="Remove this item"
                    className="text-slate-500 hover:text-rose-300">
              <Trash2 className="h-4 w-4" />
            </button>
          </div>
        ))}

        <div className="space-y-2 rounded border border-slate-800 p-3">
          <div className="flex flex-wrap gap-2">
            <label className="text-xs text-slate-400">
              What kind
              <select value={kind} onChange={(e) => setKind(e.target.value)}
                      className="ml-2 rounded border border-slate-700 bg-slate-950 px-2 py-1 text-sm text-slate-100">
                {Object.entries(PROGRAM_KIND_LABELS).map(([k, label]) => (
                  <option key={k} value={k}>{label}</option>
                ))}
              </select>
            </label>
            <label className="text-xs text-slate-400">
              Minutes
              <input value={minutes} onChange={(e) => setMinutes(e.target.value)}
                     inputMode="numeric" placeholder="optional"
                     className="ml-2 w-24 rounded border border-slate-700 bg-slate-950 px-2 py-1 text-sm text-slate-100" />
            </label>
          </div>
          <input value={title} onChange={(e) => setTitle(e.target.value)}
                 placeholder="Title, e.g. Opening prayer"
                 className="w-full rounded border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100" />
          {kind === "reading" ? (
            <label className="block text-xs text-slate-400">
              Which verified passage
              <select value={writingId} onChange={(e) => setWritingId(e.target.value)}
                      className="mt-1 w-full rounded border border-slate-700 bg-slate-950 px-2 py-2 text-sm text-slate-100">
                <option value="">Choose a passage…</option>
                {detail.writings.map((w) => (
                  <option key={w.id} value={String(w.id)}>
                    {w.text.slice(0, 70)}… — {w.source}
                  </option>
                ))}
              </select>
              <span className="mt-1 block text-slate-500">
                Only passages that came out of the library can be read here. Nothing is
                written for you, and nothing is quoted from memory.
              </span>
            </label>
          ) : (
            <textarea value={body} onChange={(e) => setBody(e.target.value)} rows={2}
                      placeholder="A note, or the question for the group"
                      className="w-full rounded border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100" />
          )}
          {add.error && <ErrorNote>{(add.error as Error).message}</ErrorNote>}
          <Button variant="secondary" onClick={() => add.mutate()} loading={add.isPending}>
            <Plus className="h-4 w-4" /> Add to the programme
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function KitSection({ id, detail }: { id: string; detail: GatheringDetail }) {
  const [busy, setBusy] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState("");

  const run = async (what: "program" | "cards") => {
    setBusy(what); setError(""); setNote("");
    try {
      if (what === "program") {
        await api.downloadProgram(id, detail.project.title);
      } else {
        await api.downloadGatheringCards(id, true, detail.project.title);
      }
      setNote(what === "program" ? "Programme downloaded." : "Card sheet downloaded.");
    } catch (e) {
      const message = (e as Error).message;
      // A warning means the file DID download and there is something to say
      // about it -- a reading whose passage went missing, for instance.
      if ((e as Error).name === "DownloadWarning") setNote(`Downloaded. ${message}`);
      else setError(message);
    } finally {
      setBusy("");
    }
  };

  return (
    <Card>
      <CardContent className="space-y-3 pt-5">
        <div className="text-sm font-medium text-slate-200">The kit</div>
        <div className="text-xs text-slate-500">
          {detail.kit.cards} card(s) ready to print · {detail.kit.program_items} programme
          item(s).
        </div>
        {detail.kit.problems.map((p) => (
          <div key={p}
               className="rounded border border-amber-400/30 bg-amber-400/5 px-3 py-2 text-xs text-amber-200">
            {p}
          </div>
        ))}
        {/* Two downloads, and the reason is stated: a programme page inside the
            duplex card sheet shifts every back face onto the wrong side. */}
        <div className="flex flex-wrap gap-2">
          <Button variant="secondary" disabled={!detail.kit.can_print_program}
                  loading={busy === "program"} onClick={() => void run("program")}>
            <FileText className="h-4 w-4" /> Programme (PDF)
          </Button>
          <Button variant="secondary" disabled={!detail.kit.can_print_cards}
                  loading={busy === "cards"} onClick={() => void run("cards")}>
            <Download className="h-4 w-4" /> Card print sheet (PDF)
          </Button>
        </div>
        <p className="text-xs text-slate-500">
          Two separate files on purpose. The card sheet is a double-sided grid whose second
          page has to line up with the first through the printer's flip; a programme page
          inside it would put every card back on the wrong side.
        </p>
        {note && <div className="text-xs text-emerald-300">{note}</div>}
        {error && <ErrorNote>{error}</ErrorNote>}
        <div className="pt-1 text-xs text-slate-500">
          Cards come from what has already been made. To make new ones, go to the Pipeline
          tab — that is a separate, deliberate step with its own review.
        </div>
      </CardContent>
    </Card>
  );
}

function GatheringDetailView({ id, onBack }: { id: string; onBack: () => void }) {
  const qc = useQueryClient();
  const [confirmDelete, setConfirmDelete] = useState(false);
  const detail = useQuery({
    queryKey: ["gathering", id],
    queryFn: () => api.getGathering(id),
  });
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["gathering", id] });
    void qc.invalidateQueries({ queryKey: ["gathering-outcomes", id] });
    void qc.invalidateQueries({ queryKey: ["gatherings"] });
    void qc.invalidateQueries({ queryKey: ["home"] });
  };

  const patch = useMutation({
    mutationFn: (body: Record<string, unknown>) => api.patchGathering(id, body),
    onSuccess: refresh,
  });
  const remove = useMutation({
    mutationFn: () => api.deleteGathering(id),
    onSuccess: () => { refresh(); onBack(); },
  });

  const sessions = useQuery({
    queryKey: ["consultations"],
    queryFn: api.listConsultations,
  });
  const link = useMutation({
    mutationFn: (sid: string) => api.linkGatheringSession(id, sid),
    onSuccess: refresh,
  });

  const d = detail.data;
  const [reflection, setReflection] = useState<string | null>(null);
  const [notes, setNotes] = useState<string | null>(null);

  if (detail.isLoading) {
    return <div className="p-6 text-sm text-slate-400" role="status">Opening…</div>;
  }
  if (detail.isError || !d) {
    return (
      <div className="mx-auto max-w-3xl space-y-3 p-6">
        <ErrorNote>
          This gathering could not be opened: {(detail.error as Error)?.message}
        </ErrorNote>
        <Button variant="secondary" onClick={onBack}>
          <ArrowLeft className="h-4 w-4" /> Back
        </Button>
      </div>
    );
  }

  const linkable = (sessions.data?.sessions ?? [])
    .filter((s) => !d.sessions.some((x) => x.id === s.id));

  return (
    <div className="mx-auto max-w-3xl space-y-5">
      <div className="flex flex-wrap items-center gap-3">
        <Button variant="secondary" onClick={onBack}>
          <ArrowLeft className="h-4 w-4" /> All gatherings
        </Button>
        <div className="min-w-0 flex-1">
          <h1 className="truncate font-display text-xl text-slate-100">{d.project.title}</h1>
          <div className="text-xs text-slate-500">
            {d.stage_info?.blurb ?? ""}
          </div>
        </div>
        <label className="text-xs text-slate-400">
          Stage
          <select
            value={d.project.stage}
            onChange={(e) => patch.mutate({ stage: e.target.value })}
            className="ml-2 rounded border border-slate-700 bg-slate-950 px-2 py-1 text-sm text-slate-100"
          >
            {["preparing", "consulting", "ready", "held", "reflecting", "closed"].map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        </label>
      </div>

      {d.project.purpose && (
        <Card>
          <CardContent className="pt-5 text-sm text-slate-300">{d.project.purpose}</CardContent>
        </Card>
      )}

      {/* Preparation notes */}
      <Card>
        <CardContent className="space-y-2 pt-5">
          <div className="text-sm font-medium text-slate-200">Preparation notes</div>
          <textarea
            value={notes ?? d.project.notes} rows={3}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="What needs doing, who to ask, what to bring."
            className="w-full rounded border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100"
          />
          <Button variant="secondary" loading={patch.isPending}
                  disabled={notes === null}
                  onClick={() => { patch.mutate({ notes: notes ?? "" }); setNotes(null); }}>
            Save notes
          </Button>
        </CardContent>
      </Card>

      {/* Consultations */}
      <Card>
        <CardContent className="space-y-3 pt-5">
          <div className="text-sm font-medium text-slate-200">Consultations</div>
          {d.sessions.length === 0 && (
            <div className="text-xs text-slate-500">
              None attached yet. Hold one in the Consultation tab, or attach one you have
              already held.
            </div>
          )}
          {d.sessions.map((s) => (
            <div key={s.id}
                 className="flex flex-wrap items-center gap-3 rounded border border-slate-800 bg-slate-950/50 px-3 py-2">
              <div className="min-w-0 flex-1 text-sm text-slate-200">{s.title}</div>
              <BadgePill className={s.approved_at
                ? "border-emerald-400/40 text-emerald-300"
                : "border-slate-700 text-slate-400"}>
                {s.approved_at ? "Record approved" : s.status === "ended"
                  ? "Not approved yet" : s.status}
              </BadgePill>
            </div>
          ))}
          {linkable.length > 0 && (
            <label className="block text-xs text-slate-400">
              Attach a consultation
              <select
                defaultValue=""
                onChange={(e) => { if (e.target.value) link.mutate(e.target.value); }}
                className="mt-1 w-full rounded border border-slate-700 bg-slate-950 px-2 py-2 text-sm text-slate-100"
              >
                <option value="">Choose one…</option>
                {linkable.map((s) => (
                  <option key={s.id} value={s.id}>{s.title}</option>
                ))}
              </select>
            </label>
          )}
          {link.error && <ErrorNote>{(link.error as Error).message}</ErrorNote>}
        </CardContent>
      </Card>

      <OutcomesSection id={id} detail={d} refresh={refresh} />

      {/* Commitments -- the consultation's own action items, read through here */}
      <Card>
        <CardContent className="space-y-3 pt-5">
          <div className="text-sm font-medium text-slate-200">Who has taken something on</div>
          {d.commitments.length === 0 && (
            <div className="text-xs text-slate-500">
              Nothing yet. Commitments come from the consultations attached above — they are
              the same records, not a copy, so accepting one here and accepting it in the
              Consultation tab are the same act.
            </div>
          )}
          {d.commitments.map((c) => {
            const state = acceptance(c);
            return (
              <div key={c.id}
                   className="space-y-1 rounded border border-slate-800 bg-slate-950/50 px-3 py-2">
                <div className="text-sm text-slate-100">{c.action}</div>
                <div className="text-xs">
                  <span className="text-slate-400">{c.owner || "Owner not assigned"}</span>
                  <span className="mx-2 text-slate-700">·</span>
                  <span className={state.tone}>{state.label}</span>
                  <span className="mx-2 text-slate-700">·</span>
                  <span className="text-slate-500">{c.status}</span>
                  {c.due && <span className="ml-2 text-slate-500">due {c.due}</span>}
                </div>
                {c.blocker && (
                  <div className="text-xs text-amber-200">Blocked — {c.blocker}</div>
                )}
              </div>
            );
          })}
          <div className="text-xs text-slate-500">
            Acceptance is recorded in the consultation itself, where the words were said.
          </div>
        </CardContent>
      </Card>

      {/* Materials */}
      <Card>
        <CardContent className="space-y-3 pt-5">
          <div className="text-sm font-medium text-slate-200">Materials</div>
          {d.items.length === 0 && (
            <div className="text-xs text-slate-500">
              Nothing chosen. Pick cards from the Products tab and add them by id, or add
              them from a card's drawer.
            </div>
          )}
          <div className="grid gap-2 sm:grid-cols-2">
            {d.items.map((item) => (
              <div key={item.id}
                   className="flex items-center gap-3 rounded border border-slate-800 bg-slate-950/50 px-3 py-2">
                <BookOpen className="h-4 w-4 shrink-0 text-slate-500" aria-hidden />
                <div className="min-w-0 flex-1 truncate text-sm text-slate-200">
                  {item.title || item.theme || item.id}
                </div>
                {!(item.front_image && item.back_image) && (
                  <BadgePill className="border-amber-400/40 text-amber-300">
                    not rendered
                  </BadgePill>
                )}
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      <ProgramSection id={id} detail={d} refresh={refresh} />
      <KitSection id={id} detail={d} />

      {/* Reflection */}
      <Card>
        <CardContent className="space-y-3 pt-5">
          <div className="text-sm font-medium text-slate-200">Reflection</div>
          <div className="flex flex-wrap items-center gap-3">
            <label className="text-xs text-slate-400">
              When to look back
              <input
                type="date" value={d.project.reflection_at ?? ""}
                onChange={(e) => patch.mutate({ reflection_at: e.target.value })}
                className="ml-2 rounded border border-slate-700 bg-slate-950 px-2 py-1 text-sm text-slate-100"
              />
            </label>
            <label className="flex items-center gap-2 text-xs text-slate-400">
              <input
                type="checkbox" checked={d.project.reflection_skipped === 1}
                onChange={(e) => patch.mutate({ reflection_skipped: e.target.checked })}
                className="h-4 w-4 accent-amber-400"
              />
              We are deliberately not setting a date
            </label>
          </div>
          <textarea
            value={reflection ?? d.project.reflection_notes} rows={4}
            onChange={(e) => setReflection(e.target.value)}
            placeholder="What helped? What got in the way? What changed our minds? What do we want to try next?"
            className="w-full rounded border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100"
          />
          <Button variant="secondary" disabled={reflection === null} loading={patch.isPending}
                  onClick={() => {
                    patch.mutate({ reflection_notes: reflection ?? "" });
                    setReflection(null);
                  }}>
            Save what was learned
          </Button>
          <p className="text-xs text-slate-500">
            For learning, not for marking. Nothing here is scored, ranked or counted as a
            streak.
          </p>
        </CardContent>
      </Card>

      <div className="pt-2">
        <button onClick={() => setConfirmDelete(true)}
                className="text-xs text-slate-500 hover:text-rose-300">
          Delete this gathering
        </button>
      </div>

      <Modal open={confirmDelete} onClose={() => setConfirmDelete(false)}
             title="Delete this gathering?">
        <div className="space-y-4 text-sm text-slate-300">
          <p>
            This removes the gathering, its programme, and the outcomes it was carrying
            forward. The consultations it linked are <strong>not</strong> deleted — a
            meeting is a thing that happened, and tidying away a plan must not destroy the
            record of the conversation that produced it.
          </p>
          {remove.error && <ErrorNote>{(remove.error as Error).message}</ErrorNote>}
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setConfirmDelete(false)}>Keep it</Button>
            <Button variant="danger" loading={remove.isPending}
                    onClick={() => remove.mutate()}>
              Delete the gathering
            </Button>
          </div>
        </div>
      </Modal>
    </div>
  );
}

export function GatheringsPanel() {
  const [openId, setOpenId] = useState<string | null>(null);
  return openId
    ? <GatheringDetailView id={openId} onBack={() => setOpenId(null)} />
    : <GatheringList onOpen={setOpenId} />;
}
