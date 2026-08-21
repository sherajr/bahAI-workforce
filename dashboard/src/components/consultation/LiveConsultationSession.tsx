import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Hand, Loader2, MessageCircleQuestion, Mic, MicOff, Play, RefreshCw, Square, Volume2, VolumeX,
} from "lucide-react";
import { api } from "../../lib/api";
import * as gov from "../../lib/consultationGovernor";
import { useRealtimeConsultation } from "../../hooks/useRealtimeConsultation";
import type {
  ConsultationCapabilities, ConsultationMode, ConsultationObservation,
  ConsultationPresence,
} from "../../lib/consultationTypes";
import { BadgePill, Button, Card, CardContent, ErrorNote, RosterAvatar } from "../ui";
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
  const [now, setNow] = useState(Date.now());
  const autoStarted = useRef(false);

  const { data, error } = useQuery({
    queryKey: ["consultation", sessionId],
    queryFn: () => api.getConsultation(sessionId),
    refetchInterval: 4000,
  });

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

  const endSession = useMutation({
    mutationFn: async () => {
      live.stop();
      return api.endConsultation(sessionId);
    },
    onSuccess: () => { refresh(); onEnded(); },
  });

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
                {live.connection === "error" ? "Reconnect" : "Start listening"}
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

      {/* Body */}
      <div className="grid min-h-0 flex-1 gap-4 lg:grid-cols-[1.1fr_1fr]">
        <LiveTranscript
          sessionId={sessionId}
          turns={turns}
          partials={live.partials}
          assistantSaying={live.assistantSaying}
          assistantName={live.name}
          onLabelled={refresh}
        />
        <div className="min-h-0 space-y-3 overflow-y-auto pr-1">
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
          />
        </div>
      </div>
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
