import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Info, Mic, Plus, ShieldCheck, X } from "lucide-react";
import { api } from "../../lib/api";
import type {
  ConsultationCapabilities, ConsultationMode, ConsultationPresence,
} from "../../lib/consultationTypes";
import { Button, Card, CardContent, CardHeader, CardTitle, ErrorNote, RosterAvatar } from "../ui";
import { Dictate, appendDictated } from "./DictateButton";

/**
 * Before the microphone. Nothing is requested on load: the browser is only
 * asked for a microphone after this Start, which is also where the room is told
 * plainly what will happen to what they say.
 */
export function ConsultationSetup({
  capabilities, onStarted, onCancel,
}: {
  capabilities: ConsultationCapabilities;
  onStarted: (sessionId: string) => void;
  onCancel: () => void;
}) {
  const [title, setTitle] = useState("");
  const [question, setQuestion] = useState("");
  const [context, setContext] = useState("");
  const [mode, setMode] = useState<ConsultationMode>(capabilities.default_mode);
  const [framework, setFramework] = useState(capabilities.default_framework);
  const [decisionMethod, setDecisionMethod] = useState("unspecified");
  const [presence, setPresence] = useState<ConsultationPresence>(capabilities.default_presence);
  const [people, setPeople] = useState<string[]>([]);
  const [personDraft, setPersonDraft] = useState("");
  const [duration, setDuration] = useState(0);
  const [warnMinutes, setWarnMinutes] = useState(10);
  const [record, setRecord] = useState(false);
  const [retention, setRetention] = useState(capabilities.default_retention ?? "keep");
  // The host's attestation. Nothing starts until this is ticked -- and the
  // server refuses too, because a page can be reloaded (rule 94).
  const [informed, setInformed] = useState(false);

  const addPerson = () => {
    const name = personDraft.trim();
    if (!name) return;
    setPeople((p) => (p.includes(name) ? p : [...p, name]));
    setPersonDraft("");
  };

  const create = useMutation({
    mutationFn: async () => {
      const session = await api.createConsultation({
        title: title.trim() || question.trim().slice(0, 60) || "Consultation",
        question: question.trim(),
        context: context.trim(),
        mode, framework, decision_method: decisionMethod, presence,
        participants: people,
        duration_minutes: duration,
        warn_minutes: warnMinutes,
        record_audio: record,
        retention_policy: retention,
        participants_informed: informed,
      });
      await api.startConsultation(session.id);
      return session;
    },
    onSuccess: (session) => onStarted(session.id),
  });

  const modeInfo = capabilities.modes.find((m) => m.id === mode);
  const spend = capabilities.spend;
  const name = capabilities.assistant_name;

  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <div className="flex items-center justify-between gap-4">
        <h2 className="font-display text-lg text-slate-100">New consultation</h2>
        <Button variant="ghost" onClick={onCancel}>Back</Button>
      </div>

      <Card>
        <CardHeader><CardTitle>What is being consulted on</CardTitle></CardHeader>
        <CardContent className="space-y-4">
          <Field label="Session title">
            <Dictate title="Say the title"
                     onText={(t) => setTitle((v) => appendDictated(v, t))}>
              <input
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="Neighbourhood gathering"
                className={INPUT}
              />
            </Dictate>
          </Field>
          <Field label="The question before the group">
            <Dictate title="Say the question"
                     onText={(t) => setQuestion((v) => appendDictated(v, t))}>
              <input
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                placeholder="When and how should we hold the next gathering?"
                className={INPUT}
              />
            </Dictate>
          </Field>
          <Field label="Anything the assistant should know beforehand"
                 hint="Optional. Background, constraints, what was decided last time. Dictating
                       twice adds to what is there rather than replacing it.">
            <Dictate title="Say the background"
                     onText={(t) => setContext((v) => appendDictated(v, t))}>
              <textarea
                value={context}
                onChange={(e) => setContext(e.target.value)}
                rows={3}
                className={INPUT}
              />
            </Dictate>
          </Field>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <RosterAvatar src={capabilities.assistant_avatar} name={name} className="h-6 w-6" />
            How {name} takes part
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-2 sm:grid-cols-2">
            {capabilities.modes.map((m) => (
              <button
                key={m.id}
                onClick={() => setMode(m.id)}
                className={`rounded-lg border px-4 py-3 text-left text-sm transition-colors ${
                  mode === m.id
                    ? "border-amber-400/50 bg-amber-400/10 text-amber-100"
                    : "border-slate-800 bg-slate-900/60 text-slate-300 hover:border-slate-700"
                }`}
              >
                <div className="font-medium">{m.label}</div>
                <div className="mt-1 text-xs text-slate-400">{m.blurb}</div>
              </button>
            ))}
          </div>
          {modeInfo && !modeInfo.speaks && (
            <p className="text-xs text-slate-400">
              In this mode {name} has no voice at all — not even when asked directly. She
              still listens and keeps the consultation map.
            </p>
          )}

          <Field label={`How quick ${name} is to take a turn`}
                 hint="Changeable during the meeting. It never lets her interrupt anyone.">
            <div className="grid gap-2 sm:grid-cols-3">
              {capabilities.presence_levels.map((p) => (
                <button
                  key={p.id}
                  onClick={() => setPresence(p.id)}
                  className={`rounded-lg border px-3 py-2 text-left text-xs transition-colors ${
                    presence === p.id
                      ? "border-amber-400/50 bg-amber-400/10 text-amber-100"
                      : "border-slate-800 bg-slate-900/60 text-slate-300 hover:border-slate-700"
                  }`}
                >
                  <div className="font-medium">{p.label}</div>
                  <div className="mt-0.5 text-[11px] text-slate-400">{p.blurb}</div>
                </button>
              ))}
            </div>
          </Field>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Consultation framework">
              <select value={framework} onChange={(e) => setFramework(e.target.value)}
                      className={INPUT}>
                {capabilities.frameworks.map((f) => (
                  <option key={f.id} value={f.id}>{f.label}</option>
                ))}
              </select>
            </Field>
            <Field label="How this group decides"
                   hint="The assistant never conducts the decision itself.">
              <select value={decisionMethod} onChange={(e) => setDecisionMethod(e.target.value)}
                      className={INPUT}>
                {capabilities.decision_methods.map((d) => (
                  <option key={d.id} value={d.id}>{d.label}</option>
                ))}
              </select>
            </Field>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>Who is here, and how long you have</CardTitle></CardHeader>
        <CardContent className="space-y-4">
          <Field
            label="People in the room"
            hint="Optional. Used to greet the room and to put names to voices afterwards."
          >
            <div className="flex gap-2">
              <input
                value={personDraft}
                onChange={(e) => setPersonDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") { e.preventDefault(); addPerson(); }
                }}
                placeholder="A name, then Enter"
                className={INPUT}
              />
              <Button variant="secondary" onClick={addPerson} disabled={!personDraft.trim()}>
                <Plus className="h-4 w-4" />
              </Button>
            </div>
            {people.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-2">
                {people.map((n) => (
                  <span key={n}
                        className="inline-flex items-center gap-1.5 rounded-full border border-slate-700 bg-slate-800/60 px-2.5 py-1 text-xs text-slate-300">
                    {n}
                    <button onClick={() => setPeople((p) => p.filter((x) => x !== n))}
                            className="text-slate-500 hover:text-slate-200">
                      <X className="h-3 w-3" />
                    </button>
                  </span>
                ))}
              </div>
            )}
          </Field>

          <Field
            label="How long"
            hint={duration
              ? `${name} will say something when there are ${warnMinutes} minutes left, and again at the end.`
              : "No time limit. Nobody will be interrupted about the clock."}
          >
            <div className="flex flex-wrap gap-2">
              {[0, 30, 45, 60, 90, 120].map((m) => (
                <button
                  key={m}
                  onClick={() => setDuration(m)}
                  className={`rounded-lg border px-3 py-1.5 text-sm transition ${
                    duration === m
                      ? "border-amber-400/60 bg-amber-400/10 text-amber-100"
                      : "border-slate-800 bg-slate-900 text-slate-400 hover:border-slate-700"
                  }`}
                >
                  {m === 0 ? "Untimed" : `${m} min`}
                </button>
              ))}
            </div>
            {duration > 0 && (
              <div className="mt-3 flex items-center gap-2 text-sm text-slate-400">
                <span>Warn me</span>
                <select
                  value={warnMinutes}
                  onChange={(e) => setWarnMinutes(Number(e.target.value))}
                  className="rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-sm text-slate-200"
                >
                  {[5, 10, 15, 20].filter((w) => w < duration).map((w) => (
                    <option key={w} value={w}>{w} minutes</option>
                  ))}
                </select>
                <span>before the end.</span>
              </div>
            )}
          </Field>

          <Field
            label="Record the meeting"
            hint="Only so the transcript can say who was speaking."
          >
            <label className={`flex cursor-pointer items-start gap-3 rounded-lg border p-3 transition ${
              record ? "border-amber-400/50 bg-amber-400/5" : "border-slate-800 bg-slate-900"
            } ${capabilities.recording_supported ? "" : "cursor-not-allowed opacity-50"}`}>
              <input
                type="checkbox"
                checked={record}
                disabled={!capabilities.recording_supported}
                onChange={(e) => setRecord(e.target.checked)}
                className="mt-0.5 accent-amber-400"
              />
              <span className="text-sm text-slate-300">
                Keep an audio recording of this meeting
                <span className="mt-1 block text-xs text-slate-500">
                  {capabilities.recording_supported ? (
                    <>
                      The live service cannot tell voices apart, so the only way the
                      transcript can name anyone is to record the room and go through it
                      afterwards. The audio file is kept in this application's private folder and
                      is sent to OpenAI once, when you ask it to work out who was
                      speaking. It is deleted with the session.
                      <strong className="text-amber-200/90"> Everyone present must be told
                      they are being recorded.</strong>
                    </>
                  ) : (
                    "Unavailable: this needs an OpenAI API key."
                  )}
                </span>
              </span>
            </label>
          </Field>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>How long the transcript is kept</CardTitle></CardHeader>
        <CardContent className="space-y-3">
          <Field label="Transcript"
                 hint="This only affects the words. The record you approve at the end —
                       the decisions, what people agreed to do, the passages — is kept
                       either way.">
            <select value={retention} onChange={(e) => setRetention(e.target.value)}
                    className={INPUT}>
              {(capabilities.retention_policies ?? []).map((r) => (
                <option key={r.id} value={r.id}>{r.label}</option>
              ))}
            </select>
          </Field>
          <p className="text-xs text-slate-500">
            {(capabilities.retention_policies ?? []).find((r) => r.id === retention)?.blurb}
          </p>
          <p className="text-xs text-slate-500">
            A timed deletion happens the next time this application is running after the
            day arrives — if the computer is off, it happens when it comes back on, not
            on the day itself.
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <ShieldCheck className="h-4 w-4 text-amber-300" />
            Before you press start
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-2 text-sm text-slate-300">
          <p>
            The microphone in this room will be sent to OpenAI's realtime service, which
            is what turns speech into text and gives {name} her voice in here. That is a
            paid cloud service, billed by the minute of audio. (Her chat and WhatsApp are
            unchanged — those still run on Claude.)
          </p>
          <p>
            <strong className="text-slate-100">{name} knows nothing about your private
            world in this room.</strong> No notes, no tasks, no calendar, no messages —
            and she can't do anything from in here either. There are other people
            present, so she is here only to help you all consult.
          </p>
          <p>
            The transcript, the consultation map and everything the assistant notices are
            stored on this machine, in the application&rsquo;s private folder. Nothing is
            published, and you can delete a whole session in one press.{" "}
            <strong className="text-slate-100">Stored on this machine is not the same as
            encrypted:</strong> the file is not scrambled, so anyone who can use this
            computer can read it.
          </p>
          <p className="text-xs text-slate-400">
            OpenAI&rsquo;s own terms apply to the audio while they are processing it. This
            application does not control what they do with it.
          </p>
          <p className={record ? "text-amber-200/90" : undefined}>
            {record
              ? "This meeting WILL be recorded to audio, kept on this machine, and sent to OpenAI once afterwards to work out who was speaking. Tell the room."
              : "No audio recording is kept. Only the text of what was said is stored."}
          </p>
          {/* Rule 94. This used to be a sentence asking the host to tell the room;
              now it is a gate, and the server enforces it too. It is deliberately
              worded as an attestation -- the host's word that they did it -- because
              this application cannot know whether anyone actually consented, and
              claiming that it does would be worse than claiming nothing. */}
          <label className={`flex cursor-pointer gap-3 rounded-lg border px-4 py-3 ${
            informed ? "border-amber-400/50 bg-amber-400/5" : "border-amber-400/30 bg-slate-900"
          }`}>
            <input type="checkbox" checked={informed}
                   onChange={(e) => setInformed(e.target.checked)}
                   className="mt-0.5 h-4 w-4 accent-amber-400" />
            <span className="text-sm text-amber-200/90">
              I have told everyone here that {name} is listening and transcribing
              {record ? ", and that the meeting is being recorded" : ""}.
              <span className="mt-1 block text-xs text-slate-400">
                That is theirs to know, not yours to assume. This records that you say you
                told them — it is not a record of anyone agreeing.
              </span>
            </span>
          </label>
          {spend.known && (
            <p className="text-xs text-slate-400">
              Metered API spend this month: ${spend.month_total} of a ${spend.monthly_ceiling}{" "}
              ceiling.{spend.over_ceiling ? " You are over it — starting will ask you to confirm." : ""}
            </p>
          )}
        </CardContent>
      </Card>

      {create.error && <ErrorNote>{(create.error as Error).message}</ErrorNote>}

      <div className="flex items-center justify-end gap-3 pb-8">
        <span className="mr-auto inline-flex items-center gap-2 text-xs text-slate-500">
          <Info className="h-3.5 w-3.5" />
          The microphone is only asked for when you press this.
        </span>
        <Button variant="secondary" onClick={onCancel}>Cancel</Button>
        <Button onClick={() => create.mutate()} loading={create.isPending}
                disabled={!informed}
                title={informed ? undefined
                                : "First confirm that you have told the room."}>
          <Mic className="h-4 w-4" />
          Start listening
        </Button>
      </div>
    </div>
  );
}

const INPUT =
  "w-full rounded-lg border border-slate-800 bg-slate-950 px-3 py-2 text-sm text-slate-100 " +
  "placeholder:text-slate-600 focus:border-amber-400/50 focus:outline-none";

function Field({ label, hint, children }: {
  label: string; hint?: string; children: React.ReactNode;
}) {
  return (
    <label className="block space-y-1.5">
      <span className="text-xs font-medium uppercase tracking-wide text-slate-400">{label}</span>
      {children}
      {hint && <span className="block text-xs text-slate-500">{hint}</span>}
    </label>
  );
}
