/**
 * The live consultation session, as one hook.
 *
 * It owns the realtime connection, the floor state machine, the transcript in
 * flight, and the timers. What it does NOT own is the decision to speak: every
 * path to `response.create` goes through the local governor and then through
 * the server's, and both have to say yes.
 *
 * The shape of the thing:
 *
 *   VAD says speech started  -> a human owns the floor. If the assistant was
 *                               talking, it is cut off in the same tick.
 *   VAD says speech stopped  -> a reflective pause. Nothing is allowed yet.
 *   transcript completed     -> saved; checked for a direct invitation, and for
 *                               a yes/no if a request for the floor stands.
 *   every few seconds        -> a debounced analysis pass (server decides
 *                               whether it is worth paying for).
 *   an observation arrives   -> considered, and almost always refused.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../lib/api";
import * as gov from "../lib/consultationGovernor";
import {
  ConnectionError, MicrophoneError, connectRealtime, events, requestMicrophone,
  type RealtimeConnection, type RealtimeEvent,
} from "../lib/consultationRealtime";
import type {
  ConsultationCapabilities, ConsultationDetail, ConsultationMode, ConsultationObservation,
} from "../lib/consultationTypes";

export type ConnectionState =
  | "idle" | "starting" | "connecting" | "live" | "reconnecting" | "closed" | "error";

export interface PendingPermission {
  observationId: string;
  sentence: string;
  askedAt: number;
}

interface Options {
  session: ConsultationDetail["session"] | undefined;
  capabilities: ConsultationCapabilities | undefined;
  /** Called whenever something happened that the stored record now reflects. */
  onRecordChanged: () => void;
}

const RECONNECT_ATTEMPTS = 2;

export function useRealtimeConsultation({ session, capabilities, onRecordChanged }: Options) {
  const [connection, setConnection] = useState<ConnectionState>("idle");
  const [floorState, setFloorState] = useState<gov.FloorState>(gov.DISCONNECTED);
  const [muted, setMuted] = useState(false);
  const [listeningPaused, setListeningPaused] = useState(false);
  const [error, setError] = useState<string>("");
  const [micError, setMicError] = useState<string>("");
  const [partials, setPartials] = useState<Record<string, string>>({});
  const [assistantSaying, setAssistantSaying] = useState("");
  const [pendingPermission, setPendingPermission] = useState<PendingPermission | null>(null);
  const [askQueued, setAskQueued] = useState<string | null>(null);
  const [lastDecision, setLastDecision] = useState<string>("");
  const [analysisNote, setAnalysisNote] = useState<string>("");
  const [analyzing, setAnalyzing] = useState(false);

  const sessionId = session?.id ?? "";
  const conn = useRef<RealtimeConnection | null>(null);
  const startedRef = useRef(false);          // React 18 double-mount guard
  const floorRef = useRef<gov.FloorState>(gov.DISCONNECTED);
  const mutedRef = useRef(false);
  const pausedRef = useRef(false);
  const sessionStartRef = useRef<number>(Date.now());
  const humanSpeechEndedRef = useRef<number | null>(null);
  const responseIdRef = useRef<string | null>(null);
  const responseItemRef = useRef<string | null>(null);
  const responseStartRef = useRef<number>(0);
  const pendingRef = useRef<PendingPermission | null>(null);
  const askQueuedRef = useRef<string | null>(null);
  const timers = useRef<number[]>([]);
  const attemptsRef = useRef(0);
  const revisionRef = useRef<number>(session?.state_revision ?? 0);
  const modeRef = useRef<ConsultationMode>(session?.mode ?? "facilitator");
  const turnsSinceAnalysis = useRef(0);
  const consideredRef = useRef<Set<string>>(new Set());

  // The timings for THIS session's presence, resolved on the server (rule 87).
  // Falling back to the generic policy rather than to numbers written here, so
  // there is still only one place any of these are decided.
  const presence = session?.presence ?? "attentive";
  const policy = capabilities
    ? (capabilities.floor_policies?.[presence] ?? capabilities.floor_policy)
    : undefined;
  const policyRef = useRef(policy);
  useEffect(() => { policyRef.current = policy; }, [policy]);

  useEffect(() => { modeRef.current = session?.mode ?? "facilitator"; }, [session?.mode]);
  useEffect(() => { revisionRef.current = session?.state_revision ?? revisionRef.current; },
            [session?.state_revision]);

  const setFloor = useCallback((event: gov.FloorEvent) => {
    const next = gov.advance(floorRef.current, event);
    floorRef.current = next;
    setFloorState(next);
    return next;
  }, []);

  const clearTimers = useCallback(() => {
    timers.current.forEach((t) => window.clearTimeout(t));
    timers.current = [];
  }, []);

  const later = useCallback((fn: () => void, ms: number) => {
    const id = window.setTimeout(fn, ms);
    timers.current.push(id);
    return id;
  }, []);

  const send = useCallback((event: RealtimeEvent) => conn.current?.send(event) ?? false, []);

  /**
   * Stop the assistant, now. Three events, because two of them leave audio
   * playing: cancel the response, drop the audio already queued in the peer
   * connection, and truncate the unheard tail so the model's record matches
   * what the room actually heard.
   */
  const cutOff = useCallback(() => {
    if (floorRef.current !== gov.AI_SPEAKING && floorRef.current !== gov.AI_PREPARING) return;
    send(events.cancelResponse());
    send(events.clearOutputAudio());
    if (responseItemRef.current) {
      send(events.truncate(responseItemRef.current, Date.now() - responseStartRef.current));
    }
    responseIdRef.current = null;
    responseItemRef.current = null;
    setAssistantSaying("");
  }, [send]);

  // ── Speaking ──────────────────────────────────────────────────────────────

  const localRequest = useCallback((kind: gov.SpeechKind,
                                    observation?: ConsultationObservation | null) => {
    if (!capabilities || !policyRef.current) return null;
    return gov.evaluate({
      kind,
      mode: modeRef.current,
      modes: capabilities.modes,
      policy: policyRef.current,
      floorState: floorRef.current,
      muted: mutedRef.current,
      listeningPaused: pausedRef.current,
      connected: !!conn.current,
      msSinceHumanSpeechEnded: humanSpeechEndedRef.current === null
        ? null : Date.now() - humanSpeechEndedRef.current,
      msSinceSessionStart: Date.now() - sessionStartRef.current,
      permissionPending: !!pendingRef.current,
      observation: observation
        ? {
            importance: observation.importance,
            shouldRequestFloor: !!observation.should_request_floor,
            status: observation.status,
            stateRevision: observation.state_revision,
          }
        : null,
      currentRevision: revisionRef.current,
    });
  }, [capabilities]);

  const speak = useCallback((instructions: string, modalities: string[] = ["audio"]) => {
    setFloor("ai_preparing");
    responseStartRef.current = Date.now();
    send(events.createResponse(instructions, modalities));
  }, [send, setFloor]);

  /** Ask AI — by button, or because someone said "AI, ...". */
  const ask = useCallback(async (text: string, byVoice = false) => {
    if (!session) return;
    const local = localRequest(byVoice ? "invited" : "queued_ask");
    if (local && !local.allowed) {
      setLastDecision(local.reason);
      if (local.action === "wait") {
        // Queue rather than interrupt. The screen says so; it is answered when
        // the floor is genuinely free.
        askQueuedRef.current = text;
        setAskQueued(text);
        setFloor("ask_queued");
        return;
      }
      return;
    }
    const decision = await api.askConsultation(session.id, {
      text,
      invited_by_voice: byVoice,
      floor_state: floorRef.current,
      human_speaking: floorRef.current === gov.HUMAN_SPEAKING,
      ms_since_human_speech_ended: humanSpeechEndedRef.current === null
        ? null : Date.now() - humanSpeechEndedRef.current,
      muted: mutedRef.current,
      listening_paused: pausedRef.current,
      connected: !!conn.current,
    });
    setLastDecision(decision.reason);
    if (!decision.allowed) {
      if (decision.action === "wait") {
        askQueuedRef.current = text;
        setAskQueued(text);
        setFloor("ask_queued");
      }
      return;
    }
    askQueuedRef.current = null;
    setAskQueued(null);
    speak(decision.instructions ?? "", decision.modalities ?? ["audio"]);
  }, [session, localRequest, speak, setFloor]);

  /**
   * Consider an observation. Almost always refused, and that is the design:
   * the assistant may at most ask ONE short question about it, and only after
   * both governors agree.
   */
  const considerObservation = useCallback(async (observation: ConsultationObservation) => {
    if (!session || !observation.should_request_floor) return;
    if (consideredRef.current.has(observation.id)) return;
    const local = localRequest("unsolicited", observation);
    if (!local || !local.allowed) {
      if (local) setLastDecision(local.reason);
      return;
    }
    consideredRef.current.add(observation.id);
    const decision = await api.consultationSpeechPermission(session.id, {
      kind: "unsolicited",
      observation_id: observation.id,
      floor_state: floorRef.current,
      human_speaking: floorRef.current === gov.HUMAN_SPEAKING,
      ms_since_human_speech_ended: humanSpeechEndedRef.current === null
        ? null : Date.now() - humanSpeechEndedRef.current,
      muted: mutedRef.current,
      listening_paused: pausedRef.current,
      connected: !!conn.current,
    });
    setLastDecision(decision.reason);
    if (!decision.allowed || decision.action !== "request_permission" || !decision.say) {
      // Refused by the server: let it be reconsidered later if the situation
      // genuinely changes (a cooldown elapsing, say).
      consideredRef.current.delete(observation.id);
      return;
    }
    const pending: PendingPermission = {
      observationId: observation.id, sentence: decision.say, askedAt: Date.now(),
    };
    pendingRef.current = pending;
    setPendingPermission(pending);
    setFloor("permission_requested");
    responseStartRef.current = Date.now();
    send(events.createExactResponse(decision.say));
    onRecordChanged();

    // Nobody has to answer. An unanswered request expires and is never
    // repeated — silence here is a no.
    const timeout = policyRef.current?.permission_timeout_ms ?? 15000;
    later(() => {
      if (pendingRef.current?.observationId !== observation.id) return;
      void answerPermission(false, true);
    }, timeout);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session, localRequest, send, setFloor, later, capabilities, onRecordChanged]);

  const answerPermission = useCallback(async (granted: boolean, ignored = false) => {
    const pending = pendingRef.current;
    if (!pending || !session) return;
    pendingRef.current = null;
    setPendingPermission(null);
    setFloor(granted ? "permission_granted" : ignored ? "permission_expired" : "permission_denied");
    const result = await api.answerConsultationPermission(session.id, pending.observationId,
                                                          { granted, ignored });
    onRecordChanged();
    if (granted && result.instructions) speak(result.instructions);
  }, [session, setFloor, speak, onRecordChanged]);

  // ── Analysis ──────────────────────────────────────────────────────────────

  const runAnalysis = useCallback(async (force = false) => {
    if (!session) return;
    setAnalyzing(true);
    try {
      const result = await api.analyzeConsultation(session.id, force);
      if (result.state?.state_revision) revisionRef.current = result.state.state_revision;
      setAnalysisNote(result.ran === false ? "" : (result.note ?? ""));
      if (result.ran) {
        turnsSinceAnalysis.current = 0;
        onRecordChanged();
        for (const observation of result.observations ?? []) {
          void considerObservation(observation);
        }
      }
    } catch (e) {
      setAnalysisNote(`The consultation map could not be updated (${(e as Error).message}).`);
    } finally {
      setAnalyzing(false);
    }
  }, [session, onRecordChanged, considerObservation]);

  // ── Realtime events ───────────────────────────────────────────────────────

  const handleEvent = useCallback((event: RealtimeEvent) => {
    switch (event.type) {
      case "input_audio_buffer.speech_started": {
        // A human owns the floor, immediately, from any state.
        cutOff();
        clearTimers();
        setFloor("human_speech_started");
        humanSpeechEndedRef.current = null;
        break;
      }
      case "input_audio_buffer.speech_stopped": {
        humanSpeechEndedRef.current = Date.now();
        setFloor("human_speech_stopped");
        const pol = policyRef.current;
        later(() => setFloor("reflective_elapsed"), pol?.reflective_pause_ms ?? 1200);
        later(() => {
          setFloor("floor_open_elapsed");
          // A queued question is answered only once the floor is really free.
          if (askQueuedRef.current) void ask(askQueuedRef.current);
        }, pol?.floor_open_ms ?? 3000);
        break;
      }
      case "conversation.item.input_audio_transcription.delta": {
        const id = String(event.item_id ?? "");
        const delta = String(event.delta ?? "");
        if (id) setPartials((p) => ({ ...p, [id]: (p[id] ?? "") + delta }));
        break;
      }
      case "conversation.item.input_audio_transcription.completed": {
        const id = String(event.item_id ?? "");
        const text = String(event.transcript ?? "").trim();
        setPartials((p) => {
          const next = { ...p };
          delete next[id];
          return next;
        });
        if (!text || !session) break;
        turnsSinceAnalysis.current += 1;
        void api.addConsultationTurn(session.id, {
          text, realtime_item_id: id, role: "human", is_final: true,
        }).then(onRecordChanged).catch(() => undefined);

        // A standing request for the floor is answered by what was just said.
        if (pendingRef.current) {
          const answer = gov.permissionAnswer(text);
          if (answer === true) void answerPermission(true);
          else if (answer === false) void answerPermission(false);
        } else if (gov.isDirectAddress(text)) {
          void ask(text, true);
        }
        break;
      }
      case "response.created": {
        responseIdRef.current = String((event.response as { id?: string })?.id ?? "");
        break;
      }
      case "response.output_item.added": {
        responseItemRef.current = String((event.item as { id?: string })?.id ?? "");
        break;
      }
      case "output_audio_buffer.started": {
        responseStartRef.current = Date.now();
        setFloor("ai_speech_started");
        break;
      }
      case "response.output_audio_transcript.delta": {
        setAssistantSaying((s) => s + String(event.delta ?? ""));
        break;
      }
      case "output_audio_buffer.stopped":
      case "output_audio_buffer.cleared": {
        setFloor("ai_speech_done");
        break;
      }
      case "response.done": {
        const response = (event.response ?? {}) as {
          usage?: Record<string, unknown>;
          status?: string;
          output?: { content?: { transcript?: string; text?: string }[] }[];
        };
        setFloor("ai_speech_done");
        const spoken = (response.output ?? [])
          .flatMap((item) => item.content ?? [])
          .map((c) => c.transcript ?? c.text ?? "")
          .join(" ")
          .trim();
        if (spoken && session) {
          void api.addConsultationTurn(session.id, {
            text: spoken, realtime_item_id: responseIdRef.current ?? undefined,
            role: "assistant", is_final: true,
          }).then(onRecordChanged).catch(() => undefined);
        }
        setAssistantSaying("");
        if (response.usage && session) {
          void api.recordConsultationUsage(session.id, response.usage,
                                           session.realtime_model ?? "").catch(() => undefined);
        }
        break;
      }
      case "error": {
        const message = ((event.error ?? {}) as { message?: string }).message
          ?? "The realtime service reported an error.";
        setError(message);
        break;
      }
      default:
        break;
    }
  }, [cutOff, clearTimers, setFloor, capabilities, later, ask, session, onRecordChanged,
      answerPermission]);

  // ── Connect / disconnect ──────────────────────────────────────────────────

  // The data channel captures its handler ONCE, when the connection is made.
  // Handing it the callback directly would freeze this turn's closures for the
  // life of the meeting; the ref keeps it current.
  const handleEventRef = useRef(handleEvent);
  useEffect(() => { handleEventRef.current = handleEvent; }, [handleEvent]);

  const stop = useCallback(() => {
    clearTimers();
    // Null the ref BEFORE closing: close() reports a close, and onClose treats
    // "there is still a connection" as a drop worth reconnecting from. A
    // deliberate stop must not reconnect itself.
    const open = conn.current;
    conn.current = null;
    open?.close();
    startedRef.current = false;
    floorRef.current = gov.DISCONNECTED;
    setFloorState(gov.DISCONNECTED);
    setConnection("closed");
    setPartials({});
    setAssistantSaying("");
    pendingRef.current = null;
    setPendingPermission(null);
  }, [clearTimers]);

  const start = useCallback(async () => {
    if (!session || !capabilities) return;
    if (startedRef.current) return;            // StrictMode remount, or a double click
    startedRef.current = true;
    setError("");
    setMicError("");
    setConnection("starting");
    let stream: MediaStream;
    try {
      stream = await requestMicrophone();
    } catch (e) {
      startedRef.current = false;
      setMicError((e as MicrophoneError).message);
      setConnection("error");
      return;
    }
    try {
      setConnection("connecting");
      const credential = await api.consultationClientSecret(session.id);
      const connected = await connectRealtime({
        clientSecret: credential.client_secret,
        callsUrl: credential.calls_url,
        model: credential.model,
        stream,
        onEvent: (e) => handleEventRef.current(e),
        onOpen: () => {
          setConnection("live");
          sessionStartRef.current = session.started_at
            ? new Date(session.started_at.replace(" ", "T")).getTime()
            : Date.now();
          setFloor("connected");
          attemptsRef.current = 0;
        },
        onClose: (reason) => {
          if (!conn.current) return;           // a deliberate stop
          conn.current = null;
          startedRef.current = false;
          setFloor("disconnected");
          if (attemptsRef.current < RECONNECT_ATTEMPTS) {
            attemptsRef.current += 1;
            setConnection("reconnecting");
            setFloor("reconnecting");
            later(() => { void start(); }, 1500 * attemptsRef.current);
          } else {
            setConnection("error");
            setError(
              `Connection lost (${reason}). Your transcript up to this point is saved ` +
              "locally. Press Reconnect to carry on."
            );
          }
        },
      });
      conn.current = connected;
    } catch (e) {
      startedRef.current = false;
      stream.getTracks().forEach((t) => t.stop());
      setConnection("error");
      setError(e instanceof ConnectionError
        ? e.message
        : `The consultation could not be started (${(e as Error).message}).`);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session, capabilities, setFloor, later]);

  // Mute keeps listening; pause stops the microphone reaching OpenAI at all.
  const toggleMute = useCallback(() => {
    setMuted((m) => {
      mutedRef.current = !m;
      if (!m) cutOff();
      return !m;
    });
  }, [cutOff]);

  const togglePause = useCallback(() => {
    setListeningPaused((p) => {
      const next = !p;
      pausedRef.current = next;
      conn.current?.stream.getAudioTracks().forEach((t) => { t.enabled = !next; });
      setFloor(next ? "listening_paused" : "listening_resumed");
      return next;
    });
  }, [setFloor]);

  /** "I'm finished" — an explicit floor release, which is much safer than
   *  guessing one from silence. */
  const openFloor = useCallback(() => {
    humanSpeechEndedRef.current = Date.now() - (policyRef.current?.floor_open_ms ?? 3000);
    setFloor("floor_open_elapsed");
    if (askQueuedRef.current) void ask(askQueuedRef.current);
  }, [capabilities, setFloor, ask]);

  // The analysis loop. The server decides whether a pass is worth paying for;
  // this only offers it the chance at a sane interval.
  const runAnalysisRef = useRef(runAnalysis);
  useEffect(() => { runAnalysisRef.current = runAnalysis; }, [runAnalysis]);

  const analysisEveryMs = Math.max(10, capabilities?.analysis_policy.min_interval_s ?? 25) * 1000;
  useEffect(() => {
    if (connection !== "live" || !sessionId) return;
    // Keyed on the session ID and the interval, never on the session OBJECT:
    // that changes identity on every four-second poll, and an interval rebuilt
    // that often would never reach its own deadline.
    const interval = window.setInterval(() => {
      if (turnsSinceAnalysis.current > 0) void runAnalysisRef.current(false);
    }, analysisEveryMs);
    return () => window.clearInterval(interval);
  }, [connection, sessionId, analysisEveryMs]);

  // Nothing survives unmount: no zombie peer connection, no live microphone.
  useEffect(() => () => {
    clearTimers();
    const open = conn.current;
    conn.current = null;
    open?.close();
    startedRef.current = false;
  }, [clearTimers]);

  const name = capabilities?.assistant_name ?? "Abigail";
  const stateLabel = useMemo(() => {
    if (connection === "error") return "Not connected";
    if (connection === "reconnecting") return "Reconnecting";
    if (muted) return `${name} is muted — still listening`;
    if (listeningPaused) return "Listening paused";
    if (askQueued) return "Will answer when the floor is free";
    // "Thinking" never displaces a conversational state: the room needs to see
    // that the assistant is holding back, not that it is busy.
    if (analyzing && (floorState === gov.LISTENING_IDLE || floorState === gov.FLOOR_OPEN)) {
      return "Updating the consultation map";
    }
    return capabilities?.state_labels[floorState] ?? gov.STATE_LABELS[floorState] ?? "Listening";
  }, [connection, muted, listeningPaused, askQueued, capabilities, floorState, analyzing, name]);

  return {
    connection, floorState, stateLabel, muted, listeningPaused, name, presence,
    error, micError, partials, assistantSaying, pendingPermission, askQueued,
    lastDecision, analysisNote, analyzing,
    start, stop, ask, toggleMute, togglePause, openFloor,
    runAnalysis, answerPermission, considerObservation,
    clearAskQueue: () => { askQueuedRef.current = null; setAskQueued(null); },
  };
}
