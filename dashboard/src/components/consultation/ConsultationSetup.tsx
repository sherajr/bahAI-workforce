import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Info, Mic, ShieldCheck } from "lucide-react";
import { api } from "../../lib/api";
import type {
  ConsultationCapabilities, ConsultationMode, ConsultationPresence,
} from "../../lib/consultationTypes";
import { Button, Card, CardContent, CardHeader, CardTitle, ErrorNote, RosterAvatar } from "../ui";

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

  const create = useMutation({
    mutationFn: async () => {
      const session = await api.createConsultation({
        title: title.trim() || question.trim().slice(0, 60) || "Consultation",
        question: question.trim(),
        context: context.trim(),
        mode, framework, decision_method: decisionMethod, presence,
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
            <input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Neighbourhood gathering"
              className={INPUT}
            />
          </Field>
          <Field label="The question before the group">
            <input
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              placeholder="When and how should we hold the next gathering?"
              className={INPUT}
            />
          </Field>
          <Field label="Anything the assistant should know beforehand"
                 hint="Optional. Background, constraints, what was decided last time.">
            <textarea
              value={context}
              onChange={(e) => setContext(e.target.value)}
              rows={3}
              className={INPUT}
            />
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
          <Field label="Recording">
            <div className="rounded-lg border border-slate-800 bg-slate-900/60 px-4 py-3 text-sm text-slate-400">
              Raw audio is never saved. Only the text of what was said is kept, on this
              machine. There is no recorder in this version, so there is nothing to
              switch on.
            </div>
          </Field>
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
            stored on this machine, in the application's private folder. Nothing is
            published, and you can delete a whole session in one press.
          </p>
          <p className="text-amber-200/90">
            Tell the people in the room that {name} is listening and transcribing. That
            is theirs to know, not yours to assume.
          </p>
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
        <Button onClick={() => create.mutate()} loading={create.isPending}>
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
