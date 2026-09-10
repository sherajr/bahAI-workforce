import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ChevronDown, ChevronRight, Circle, Clock, Hand, Loader2, MessageCircleQuestion, Mic,
  MicOff, Play, RefreshCw, Square, Volume2, VolumeX, X,
} from "lucide-react";
import { api } from "../../lib/api";
import * as gov from "../../lib/consultationGovernor";
import { registerLeaveGuard } from "../../lib/navGuard";
import { useRealtimeConsultation } from "../../hooks/useRealtimeConsultation";
import { useConsultationUpdates } from "../../hooks/useConsultationUpdates";
import type {
  ConsultationCapabilities, ConsultationMode, ConsultationObservation,
  ConsultationPresence,
} from "../../lib/consultationTypes";
import { BadgePill, Button, Card, CardContent, ErrorNote, Modal, RosterAvatar } from "../ui";
import { ConsultationGlance } from "./ConsultationGlance";
import { ConsultationMap } from "./ConsultationMap";
import { ConsultationObservations } from "./ConsultationObservations";
import { LiveTranscript } from "./LiveTranscript";

/** The room's screen while a consultation is running. */
export function LiveConsultationSession({
  sessionId, capabilities, autoStart, onEnded, onBack,
}: {
  sessionId: string;
  capabilities: ConsultationCapabilities;
  autoStart: boolean;
  onEnded: () => void;
  onBack: () => void;
}) {
  const qc = useQueryClient();
  const [askText, setAskText] = useState("");
  // The passage is shown in full while she reads it, so the room can follow,
  // and collapses to one line the moment she finishes. Left expanded it is
  // ~1000 characters sitting above the transcript, which on a laptop pushed the
  // transcript clean off the bottom of the screen -- "all I see is the initial
  // announcement" (2026-08-27).
  const [passageOpen, setPassageOpen] = useState(true);
  // Which transcript lines a map item came from, when someone asks. Provenance
  // proves only what was SAID in this meeting -- never that it is true -- and
  // the panel says so (rule 95).
  const [sourceTurns, setSourceTurns] = useState<string[] | null>(null);
  const wasReadingRef = useRef(false);
  const [now, setNow] = useState(Date.now());
  const autoStarted = useRef(false);

  // Read the whole session ONCE, then take deltas (rule 115). The four-second
  // full refetch that used to be here re-sent the entire transcript -- twice --
  // on every tick, so an hour-long meeting paid more per poll than a five-minute
  // one for exactly the same information.
  const { data, error } = useQuery({
    queryKey: ["consultation", sessionId],
    queryFn: () => api.getConsultation(sessionId),
  });
  useConsultationUpdates(sessionId, true);

  const refresh = useCallback(() => {
    void qc.invalidateQueries({ queryKey: ["consultation", sessionId] });
  }, [qc, sessionId]);

  const live = useRealtimeConsultation({
    session: data?.session,
    capabilities,
    onRecordChanged: refresh,
  });

  // Only ever started by a press: `autoStart` is true exactly because the user
  // just pressed Start listening on the setup screen.
  useEffect(() => {
    if (!autoStart || autoStarted.current || !data?.session) return;
    autoStarted.current = true;
    void live.start();
  }, [autoStart, data?.session, live]);

  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, []);

  useEffect(() => {
    if (!live.passage) { setPassageOpen(true); wasReadingRef.current = false; return; }
    if (live.floorState === gov.AI_SPEAKING || live.floorState === gov.AI_PREPARING) {
      wasReadingRef.current = true;
    } else if (wasReadingRef.current) {
      setPassageOpen(false);
    }
  }, [live.passage, live.floorState]);

  const endSession = useMutation({
    mutationFn: async () => {
      // AWAITED. `stop()` finishes and finalises the recording, and ending the
      // meeting used to race it -- the screen moved on to closeout while the
      // last seconds of audio were still being saved, and a failure surfaced
      // after the record had already been called complete (rule 113).
      await live.stop();
      return api.endConsultation(sessionId);
    },
    onSuccess: () => { refresh(); onEnded(); },
  });

  /**
   * Leaving this screen while the microphone is open (rule 114).
   *
   * The choice is deliberately only two things, and neither of them is "end the
   * meeting": ending a consultation is a separate, deliberate act that leads to
   * the closeout, and a mis-click on the sidebar must never be able to perform
   * it. Stopping listening is honest about what it does -- the session stays
   * open and resumable, and says so.
   */
  useEffect(() => {
    const listening = live.connection === "live" || live.connection === "connecting"
      || live.connection === "starting" || live.connection === "reconnecting";
    if (!listening) return;
    return registerLeaveGuard(async () => {
      const leave = window.confirm(
        "This consultation is still listening." + "\n\n" +
        "OK: stop listening and leave. The meeting is NOT ended -- it stays open "
        + "and you can resume listening when you come back." + "\n" +
        "Cancel: stay on this screen."
      );
      if (!leave) return "stay";
      // Await the recording's finalisation before the panel is unmounted, so
      // the last chunks are saved rather than racing the navigation.
      await live.stop();
      return "leave";
    });
  }, [live.connection, live]);

  const setMode = useMutation({
    mutationFn: (mode: ConsultationMode) => api.patchConsultation(sessionId, { mode }),
    onSuccess: refresh,
  });

  // How quick she is. Changeable mid-meeting on purpose — the moment you notice
  // she is too slow is while you are sitting there waiting for her.
  const setPresence = useMutation({
    mutationFn: (presence: ConsultationPresence) =>
      api.patchConsultation(sessionId, { presence }),
    onSuccess: refresh,
  });

  const elapsed = useMemo(() => {
    const started = data?.session.started_at;
    if (!started) return "";
    const ms = now - new Date(started.replace(" ", "T")).getTime();
    if (ms < 0) return "";
    const mins = Math.floor(ms / 60000);
    const secs = Math.floor((ms % 60000) / 1000);
    return `${mins}:${String(secs).padStart(2, "0")}`;
  }, [data?.session.started_at, now]);

  const askObservation = (observation: ConsultationObservation) => {
    void live.ask(`You noticed: ${observation.summary}. Explain it to the group, briefly.`);
  };

  if (error) return <ErrorNote>{(error as Error).message}</ErrorNote>;
  if (!data) {
    return (
      <div className="flex items-center gap-2 text-sm text-slate-400">
        <Loader2 className="h-4 w-4 animate-spin" /> Opening the session…
      </div>
    );
  }

  const { session, state, turns, observations, decisions, action_items, writings } = data;
  const connecting = live.connection === "starting" || live.connection === "connecting";
  const isLive = live.connection === "live";
  const modeInfo = capabilities.modes.find((m) => m.id === session.mode);

  return (
    <div className="flex h-full min-h-0 flex-col gap-4">
      {/* Header */}
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex items-center gap-3">
            <h2 className="truncate font-display text-lg text-slate-100">{session.title}</h2>
            {elapsed && <span className="font-mono text-xs text-slate-500">{elapsed}</span>}
            {/* The clock, when the group set one. It turns amber inside the
                warning window, so the screen says the same thing she is about
                to say rather than the warning arriving out of nowhere. */}
            {live.minutesLeft !== null && (
              <span className={`inline-flex items-center gap-1 font-mono text-xs ${
                live.minutesLeft <= (session.warn_minutes ?? 10)
                  ? "text-amber-300" : "text-slate-500"
              }`}>
                <Clock className="h-3.5 w-3.5" />
                {live.minutesLeft > 0 ? `${live.minutesLeft} min left` : "time is up"}
              </span>
            )}
            {live.recording && (
              <span className="inline-flex items-center gap-1 text-xs text-rose-300"
                    title="This meeting is being recorded so the transcript can name who spoke.">
                <Circle className="h-2.5 w-2.5 fill-rose-400 text-rose-400" />
                recording
              </span>
            )}
          </div>
          {session.question && (
            <p className="mt-0.5 text-sm text-slate-400">{session.question}</p>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <select
            value={session.mode}
            onChange={(e) => setMode.mutate(e.target.value as ConsultationMode)}
            className="rounded-lg border border-slate-800 bg-slate-950 px-3 py-2 text-xs text-slate-200 focus:outline-none"
          >
            {capabilities.modes.map((m) => (
              <option key={m.id} value={m.id}>{m.label}</option>
            ))}
          </select>
          <select
            value={session.presence}
            onChange={(e) => setPresence.mutate(e.target.value as ConsultationPresence)}
            title="How quick she is to take a turn"
            className="rounded-lg border border-slate-800 bg-slate-950 px-3 py-2 text-xs text-slate-200 focus:outline-none"
          >
            {capabilities.presence_levels.map((p) => (
              <option key={p.id} value={p.id}>{p.label}</option>
            ))}
          </select>
          <Button variant="secondary" onClick={onBack}>Back</Button>
          <Button variant="danger" onClick={() => endSession.mutate()}
                  loading={endSession.isPending}>
            <Square className="h-4 w-4" />
            End session
          </Button>
        </div>
      </div>

      {/* State indicator — the room has to be able to see that the silence is
          deliberate rather than a crash. */}
      <Card className={
        live.floorState === gov.HUMAN_REFLECTIVE_PAUSE ? "border-sky-500/40"
        : live.pendingPermission ? "border-amber-400/50"
        : live.connection === "error" ? "border-rose-500/40" : ""
      }>
        <CardContent className="flex flex-wrap items-center justify-between gap-4 py-4">
          <div className="flex items-center gap-3">
            <div className="relative">
              <RosterAvatar src={capabilities.assistant_avatar} name={live.name}
                            className="h-9 w-9" />
              <span className="absolute -bottom-0.5 -right-0.5 rounded-full bg-slate-950 p-0.5">
                <StatusDot state={live.floorState} connection={live.connection}
                           muted={live.muted} />
              </span>
            </div>
            <div>
              <div className="text-sm font-medium text-slate-100">{live.stateLabel}</div>
              <div className="text-xs text-slate-500">
                {live.pendingPermission
                  ? `${live.name} asked whether it would help. If nobody answers, she drops it.`
                  : live.lastDecision || modeInfo?.blurb}
              </div>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {!isLive && !connecting && (
              <Button onClick={() => void live.start()}>
                <Play className="h-4 w-4" />
                {/* "Resume" once this meeting has already been listening, so
                    coming back to a session that was left is not mistaken for
                    starting a new one -- and so it is clear the meeting was
                    never ended (rule 114). Resuming is always a press: nothing
                    reopens a microphone because a panel mounted. */}
                {live.connection === "error"
                  ? "Reconnect"
                  : data?.session.started_at ? "Resume listening" : "Start listening"}
              </Button>
            )}
            {connecting && (
              <span className="inline-flex items-center gap-2 text-xs text-slate-400">
                <Loader2 className="h-4 w-4 animate-spin" /> Connecting…
              </span>
            )}
            {isLive && (
              <>
                <Button variant="secondary" onClick={live.toggleMute}>
                  {live.muted ? <VolumeX className="h-4 w-4" /> : <Volume2 className="h-4 w-4" />}
                  {live.muted ? `Unmute ${live.name}` : `Mute ${live.name}`}
                </Button>
                <Button variant="secondary" onClick={live.togglePause}>
                  {live.listeningPaused ? <Mic className="h-4 w-4" /> : <MicOff className="h-4 w-4" />}
                  {live.listeningPaused ? "Resume listening" : "Pause listening"}
                </Button>
                <Button variant="secondary" onClick={live.openFloor}>
                  <Hand className="h-4 w-4" />
                  I'm finished
                </Button>
              </>
            )}
          </div>
        </CardContent>
      </Card>

      {live.micError && (
        <ErrorNote>
          {live.micError}{" "}
          <button className="underline" onClick={() => void live.start()}>Retry</button>
        </ErrorNote>
      )}
      {live.error && <ErrorNote>{live.error}</ErrorNote>}
      {/* What the SERVER holds, not what the browser emitted. A recording light
          that reflects only the second is not a claim about safety (rule 113). */}
      {live.saveFailed && <ErrorNote>{live.saveFailed}</ErrorNote>}
      {live.recording && !live.saveFailed && (
        <div className="rounded-lg border border-slate-800 bg-slate-900/60 px-4 py-2 text-xs text-slate-400">
          Recording saved as it is made — {(live.savedBytes / (1024 * 1024)).toFixed(1)} MB
          stored so far
          {live.savePending > 0 && `, ${live.savePending} piece(s) still to send`}.
          {!data?.session.started_at && " "}
        </div>
      )}
      {!isLive && data?.session.started_at && data.session.status !== "ended" && (
        <div className="rounded-lg border border-slate-800 bg-slate-900/60 px-4 py-2 text-xs text-slate-400">
          This consultation is open but not listening. The meeting has not been ended —
          press Resume listening to carry on, or End the consultation when the group is
          finished.
        </div>
      )}
      {live.analysisNote && (
        <div className="rounded-lg border border-slate-800 bg-slate-900/60 px-4 py-2 text-xs text-slate-400">
          {live.analysisNote}
        </div>
      )}

      {live.pendingPermission && (
        <Card className="border-amber-400/50">
          <CardContent className="flex flex-wrap items-center justify-between gap-3 py-4">
            <p className="text-sm text-amber-100">"{live.pendingPermission.sentence}"</p>
            <div className="flex gap-2">
              <Button onClick={() => void live.answerPermission(true)}>Yes, go ahead</Button>
              <Button variant="secondary" onClick={() => void live.answerPermission(false)}>
                Not now
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {live.askQueued && (
        <div className="flex items-center justify-between gap-3 rounded-lg border border-slate-800 bg-slate-900/60 px-4 py-2 text-xs text-slate-300">
          <span>{live.name} will answer when the floor is free — she will not interrupt.</span>
          <button className="text-slate-400 underline" onClick={live.clearAskQueue}>
            Cancel
          </button>
        </div>
      )}

      {/* Ask */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-[16rem] flex-1">
          <MessageCircleQuestion className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-600" />
          <input
            value={askText}
            onChange={(e) => setAskText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && askText.trim()) {
                void live.ask(askText.trim());
                setAskText("");
              }
            }}
            placeholder={`Ask ${live.name} something — or just say "${live.name}, …" out loud`}
            className="w-full rounded-lg border border-slate-800 bg-slate-950 py-2 pl-9 pr-3 text-sm text-slate-100 placeholder:text-slate-600 focus:border-amber-400/50 focus:outline-none"
          />
        </div>
        <Button
          onClick={() => { void live.ask(askText.trim() || "Summarise where we have got to."); setAskText(""); }}
          disabled={!isLive || !modeInfo?.speaks}
        >
          Ask {live.name}
        </Button>
        <Button variant="secondary" onClick={() => void live.runAnalysis(true)}>
          <RefreshCw className="h-4 w-4" />
          Update the map
        </Button>
      </div>

      {/* The passage she is reading, on screen at the same moment she reads it.
          The room sees the exact words rather than only hearing them (rule 92). */}
      {live.passage && (
        <Card className="border-amber-400/40">
          <CardContent className="py-3">
            <div className="flex items-center justify-between gap-3">
              <button
                onClick={() => setPassageOpen((v) => !v)}
                className="flex min-w-0 items-center gap-2 text-left"
              >
                {passageOpen
                  ? <ChevronDown className="h-4 w-4 shrink-0 text-amber-200/70" />
                  : <ChevronRight className="h-4 w-4 shrink-0 text-amber-200/70" />}
                <span className="truncate text-xs uppercase tracking-wide text-amber-200/70">
                  Read at the opening
                </span>
                {!passageOpen && (
                  <span className="truncate text-xs text-slate-500">
                    &mdash; {live.passage.source}
                  </span>
                )}
              </button>
              <button onClick={live.dismissPassage} title="Hide this"
                      className="shrink-0 text-slate-500 hover:text-slate-300">
                <X className="h-4 w-4" />
              </button>
            </div>
            {passageOpen && (
              // Capped and scrollable even when open: the transcript below it
              // must never be pushed off the screen by a long passage.
              <div className="mt-3 max-h-44 space-y-3 overflow-y-auto pr-1">
                {live.passage.text.split("\n\n").map((para, i) => (
                  <p key={i} className="text-sm italic leading-relaxed text-slate-200">{para}</p>
                ))}
                {live.passage.source && (
                  <p className="text-xs text-slate-500">{live.passage.source}</p>
                )}
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {live.recordingNote && <ErrorNote>{live.recordingNote}</ErrorNote>}

      {/* The spend ceiling, with the decision it is actually asking for. */}
      {live.overCeiling && (
        <Card className="border-amber-400/40">
          <CardContent className="flex flex-wrap items-center justify-between gap-4 py-4">
            <div className="min-w-0">
              <p className="text-sm text-slate-200">{live.overCeiling}</p>
              <p className="mt-1 text-xs text-slate-500">
                The ceiling is a reminder, not a hard limit — it is there so a paid
                service cannot run up a bill without you seeing it.
              </p>
            </div>
            <Button onClick={() => void live.start(true)}>
              <Play className="h-4 w-4" />
              Start anyway
            </Button>
          </CardContent>
        </Card>
      )}

      {/* Body */}
      <div className="grid min-h-0 flex-1 gap-4 lg:grid-cols-[1.1fr_1fr]">
        <LiveTranscript
          sessionId={sessionId}
          turns={turns}
          partials={live.partials}
          assistantSaying={live.assistantSaying}
          assistantName={live.name}
          onLabelled={refresh}
          onCorrected={refresh}
        />
        <div className="min-h-0 space-y-3 overflow-y-auto pr-1">
          <ConsultationGlance
            state={state}
            threads={data.open_threads}
            decided={!!decisions.find((d) => d.status === "confirmed")}
          />
          <ConsultationObservations
            observations={observations}
            assistantName={live.name}
            onAsk={askObservation}
            onDismiss={(id) => {
              void api.dismissConsultationObservation(sessionId, id).then(refresh);
            }}
            disabled={!isLive || !modeInfo?.speaks}
          />
          <ConsultationMap
            state={state}
            decisions={decisions}
            actions={action_items}
            writings={writings}
            onConfirmDecision={(id) => {
              void api.confirmConsultationDecision(sessionId, id).then(refresh);
            }}
            onRejectDecision={(id) => {
              void api.rejectConsultationDecision(sessionId, id).then(refresh);
            }}
            onToggleAction={(id, status) => {
              void api.setConsultationActionStatus(sessionId, id, status).then(refresh);
            }}
            /* Human authority over the map (rule 95). A correction made here
               is protected from the next analysis pass by the server. */
            onEditItem={(list, id, text) => {
              void api.editConsultationMapItem(sessionId, list, id, { text }).then(refresh);
            }}
            onDeleteItem={(list, id) => {
              void api.deleteConsultationMapItem(sessionId, list, id).then(refresh);
            }}
            onShowSource={setSourceTurns}
          />
        </div>
      </div>

      {sourceTurns && (
        <Modal open onClose={() => setSourceTurns(null)} title="What was actually said">
          <div className="space-y-3">
            <p className="text-xs text-slate-500">
              The lines this came from. It shows what was said in the meeting — not
              that what was said is correct.
            </p>
            {turns.filter((t) => sourceTurns.includes(String(t.id))).map((t) => (
              <div key={t.id} className="rounded border border-slate-800 bg-slate-900/60 p-2">
                <p className="text-xs font-semibold text-slate-400">
                  {t.role === "assistant" ? live.name : t.speaker_label ?? "Participant"}
                </p>
                <p className="mt-0.5 text-sm text-slate-200">{t.text}</p>
              </div>
            ))}
            {!turns.some((t) => sourceTurns.includes(String(t.id))) && (
              <p className="text-sm text-slate-500">
                Those lines are no longer in the transcript.
              </p>
            )}
          </div>
        </Modal>
      )}
    </div>
  );
}

function StatusDot({ state, connection, muted }: {
  state: string; connection: string; muted: boolean;
}) {
  const tone =
    connection === "error" ? "bg-rose-400"
    : connection !== "live" ? "bg-slate-600"
    : muted ? "bg-slate-500"
    : state === gov.HUMAN_SPEAKING ? "bg-sky-400"
    : state === gov.HUMAN_REFLECTIVE_PAUSE ? "bg-sky-500/60"
    : state === gov.AI_SPEAKING ? "bg-amber-400"
    : state === gov.AI_PERMISSION_PENDING ? "bg-amber-300"
    : "bg-emerald-400";
  const pulse = connection === "live" && state === gov.AI_SPEAKING ? "animate-pulse" : "";
  return (
    <span className="relative flex h-3 w-3 items-center justify-center">
      <span className={`h-3 w-3 rounded-full ${tone} ${pulse}`} />
    </span>
  );
}

export function ModeBadge({ label }: { label: string }) {
  return (
    <BadgePill className="border-slate-700 bg-slate-800/80 text-slate-300">{label}</BadgePill>
  );
}
