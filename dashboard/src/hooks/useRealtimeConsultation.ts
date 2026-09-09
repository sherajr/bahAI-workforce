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
  ConnectionError, MicrophoneError, TRUNCATE_FLOOR_MS, connectRealtime, events,
  requestMicrophone, type RealtimeConnection, type RealtimeEvent,
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

/**
 * Errors that mean "you were a moment late", not "something is broken".
 *
 * Cutting her off is a race we cannot win cleanly: the cancel is already on the
 * wire when her response finishes by itself, and OpenAI quite correctly says
 * there was nothing to cancel. Caught for real on 2026-08-27 — the recorded
 * message was "Cancellation failed: no active response found" while the floor
 * had already gone back to idle. Nothing went wrong, and putting a red banner
 * over the meeting for it is worse than saying nothing. They are still RECORDED
 * (rule 89) so a real pattern would still be visible in the session's events.
 */
const BENIGN_ERRORS = [
  "no active response",
  "buffer is empty",
  "already has an active response",
];

const isBenign = (message: string) =>
  BENIGN_ERRORS.some((m) => message.toLowerCase().includes(m));

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
  /** The passage she is reading, shown on screen at the same moment (rule 92). */
  const [passage, setPassage] = useState<{ text: string; source: string } | null>(null);
  const [minutesLeft, setMinutesLeft] = useState<number | null>(null);
  const [recording, setRecording] = useState(false);
  const [recordingNote, setRecordingNote] = useState("");
  /** Set when the Steward's monthly ceiling refused the session (rule 85). Held
   *  separately from `error` because it is a QUESTION for the owner, not a
   *  fault: the only thing that clears it is him deciding to go ahead. */
  const [overCeiling, setOverCeiling] = useState<string>("");

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
  // What is actually TRUE on the wire right now, as opposed to what the floor
  // state says we intend. Cancelling a response that does not exist, or
  // clearing an audio buffer that is empty, is an error from OpenAI — see
  // cutOff.
  const responseActiveRef = useRef(false);
  const audioPlayingRef = useRef(false);
  const audioStartedAtRef = useRef<number>(0);
  const appliedEagernessRef = useRef<string | null>(null);
  const pendingRef = useRef<PendingPermission | null>(null);
  const askQueuedRef = useRef<string | null>(null);
  const timers = useRef<number[]>([]);
  const attemptsRef = useRef(0);
  const revisionRef = useRef<number>(session?.state_revision ?? 0);
  const modeRef = useRef<ConsultationMode>(session?.mode ?? "facilitator");
  const turnsSinceAnalysis = useRef(0);
  const consideredRef = useRef<Set<string>>(new Set());
  const openedRef = useRef(false);
  const warnedRef = useRef<{ warn: boolean; final: boolean }>({ warn: false, final: false });
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);

  // The timings for THIS session's presence, resolved on the server (rule 87).
  // Falling back to the generic policy rather than to numbers written here, so
  // there is still only one place any of these are decided.
  const presence = session?.presence ?? "attentive";
  const policy = capabilities
    ? (capabilities.floor_policies?.[presence] ?? capabilities.floor_policy)
    : undefined;
  const policyRef = useRef(policy);
  useEffect(() => { policyRef.current = policy; }, [policy]);

  useEffect(() => {
    openedRef.current = false;
    warnedRef.current = { warn: false, final: false };
    setPassage(null);
    setMinutesLeft(null);
    setRecordingNote("");
  }, [sessionId]);

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
    // Each event is sent only when the thing it acts on actually exists.
    //
    // All three still fire on a real barge-in mid-sentence — that is the
    // guarantee (rule 76) and it is unchanged. What is gone is sending them
    // into a void: cutting off a response that has been REQUESTED but has not
    // begun (the ai_preparing window) used to emit all three regardless, and
    // OpenAI answers each one with an error. That is the error Sheraj saw
    // "just before she responds", and the cancel took her answer with it
    // (2026-08-24).
    if (responseActiveRef.current) send(events.cancelResponse());
    if (audioPlayingRef.current) {
      send(events.clearOutputAudio());
      const heard = Date.now() - audioStartedAtRef.current;
      if (responseItemRef.current && heard > TRUNCATE_FLOOR_MS) {
        send(events.truncate(responseItemRef.current, heard));
      }
    }
    responseActiveRef.current = false;
    audioPlayingRef.current = false;
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
    // A new attempt clears the last complaint: an error banner that outlives
    // the thing it described reads as "still broken" when she is answering
    // perfectly well.
    setError("");
    send(events.createResponse(instructions, modalities));
  }, [send, setFloor]);

  /**
   * Hold a question until the governor's own stated moment.
   *
   * Both governors already answer "wait, and try again in N ms" — and N was
   * being thrown away. A queued ask sat until the floor-open timer instead,
   * which produced a perverse cliff: a transcript that arrived FAST (inside the
   * 400ms invitation grace) was refused and then waited the full floor-open
   * window, so the quicker the transcription, the longer she took to answer
   * (2026-08-24). Only the grace waits carry a retry, and it shrinks each time,
   * so this cannot loop — "someone is speaking" carries none and still falls
   * through to the floor-open path.
   */
  const holdAsk = useCallback((text: string, byVoice: boolean, retryAfterMs: number | null) => {
    askQueuedRef.current = text;
    setAskQueued(text);
    setFloor("ask_queued");
    if (retryAfterMs !== null && Number.isFinite(retryAfterMs) && retryAfterMs > 0) {
      later(() => {
        if (askQueuedRef.current !== text) return;
        void askRef.current?.(text, byVoice);
      }, Math.max(50, retryAfterMs));
    }
  }, [later, setFloor]);

  /** Ask AI — by button, or because someone said "AI, ...". */
  const ask = useCallback(async (text: string, byVoice = false) => {
    if (!session) return;
    const local = localRequest(byVoice ? "invited" : "queued_ask");
    if (local && !local.allowed) {
      setLastDecision(local.reason);
      if (local.action === "wait") {
        // Queue rather than interrupt. The screen says so; it is answered at
        // the governor's own moment, or when the floor is genuinely free.
        holdAsk(text, byVoice, local.retryAfterMs);
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
      if (decision.action === "wait") holdAsk(text, byVoice, decision.retry_after_ms);
      return;
    }
    askQueuedRef.current = null;
    setAskQueued(null);
    speak(decision.instructions ?? "", decision.modalities ?? ["audio"]);
  }, [session, localRequest, speak, setFloor, holdAsk]);

  // `holdAsk` schedules a retry of `ask`, which is defined after it. The ref
  // keeps that from being a stale closure over this render's session.
  const askRef = useRef(ask);
  useEffect(() => { askRef.current = ask; }, [ask]);

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
    setError("");
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

  /**
   * Apply a mid-meeting change of presence to the live session.
   *
   * The dial's biggest lever is how readily the detector calls a turn finished,
   * and that lives in the realtime session, not in this hook's timers — so
   * without this, moving to Present changed the waiting and left the longest
   * wait exactly where it was. The session is configured once when the
   * credential is minted; this is the only thing that ever re-configures it.
   *
   * The block sent is the SERVER's, verbatim (`policy.turn_detection`). It is
   * not assembled here on purpose: `create_response: false` is what stops the
   * detector starting her by itself, and a browser that built its own object
   * could omit it — the API's default is true, and rule 75 would be gone with
   * nothing raising an error anywhere.
   */
  useEffect(() => {
    if (connection !== "live" || !policy?.turn_detection) return;
    const wanted = policy.vad_eagerness ?? null;
    if (appliedEagernessRef.current === null || appliedEagernessRef.current === wanted) return;
    if (send(events.sessionUpdate({ audio: { input: { turn_detection: policy.turn_detection } } }))) {
      appliedEagernessRef.current = wanted;
    }
  }, [connection, policy, send]);

  /** The room state both scheduled interventions are judged against. */
  const roomState = useCallback(() => ({
    floor_state: floorRef.current,
    human_speaking: floorRef.current === gov.HUMAN_SPEAKING,
    ms_since_human_speech_ended: humanSpeechEndedRef.current === null
      ? null : Date.now() - humanSpeechEndedRef.current,
    muted: mutedRef.current,
    listening_paused: pausedRef.current,
    connected: !!conn.current,
  }), []);

  /**
   * Open the meeting.
   *
   * Fires once, when the connection first goes live. The server refuses a second
   * one from its own record, so a page reload cannot make her open the meeting
   * twice — this ref only saves a round trip.
   */
  const openMeeting = useCallback(async () => {
    if (!session || openedRef.current) return;
    openedRef.current = true;
    try {
      const decision = await api.consultationOpening(session.id, roomState());
      setLastDecision(decision.reason);
      if (!decision.allowed || !decision.instructions) return;
      if (decision.passage) {
        setPassage({ text: decision.passage, source: decision.passage_source ?? "" });
      }
      speak(decision.instructions, decision.modalities ?? ["audio"]);
    } catch {
      // An opening that does not happen is a small loss; taking the meeting
      // down over it would be a large one.
      openedRef.current = false;
    }
  }, [session, roomState, speak]);

  /**
   * The clock (owner ask 2026-08-25).
   *
   * Polled rather than scheduled with one long timeout, because a laptop that
   * sleeps mid-meeting would sail straight past a `setTimeout` and never warn
   * anybody. Elapsed time is recomputed from the start on every tick, so waking
   * up late still produces the warning rather than silence.
   */
  const checkClock = useCallback(() => {
    if (!session) return;
    const total = session.duration_minutes ?? 0;
    if (!total) { setMinutesLeft(null); return; }
    const elapsedMin = (Date.now() - sessionStartRef.current) / 60000;
    const left = Math.max(0, Math.ceil(total - elapsedMin));
    setMinutesLeft(left);
    const warnAt = Math.max(0, total - (session.warn_minutes ?? 10));
    const due = elapsedMin >= total ? "final" : elapsedMin >= warnAt ? "warn" : null;
    if (!due) return;
    if (due === "warn" && warnedRef.current.warn) return;
    if (due === "final" && warnedRef.current.final) return;
    // A meeting that runs past the warning point before anyone connects should
    // not fire both at once; the final one supersedes.
    if (due === "final") warnedRef.current = { warn: true, final: true };
    else warnedRef.current.warn = true;
    void api.consultationTimeWarning(session.id, {
      ...roomState(), minutes_left: left, final: due === "final",
    }).then((decision) => {
      setLastDecision(decision.reason);
      if (decision.allowed && decision.instructions) {
        speak(decision.instructions, decision.modalities ?? ["audio"]);
      }
    }).catch(() => undefined);
  }, [session, roomState, speak]);

  useEffect(() => {
    if (connection !== "live") return;
    checkClock();
    const id = window.setInterval(checkClock, 15000);
    return () => window.clearInterval(id);
  }, [connection, checkClock]);

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
        responseActiveRef.current = true;
        break;
      }
      case "response.output_item.added": {
        responseItemRef.current = String((event.item as { id?: string })?.id ?? "");
        break;
      }
      case "output_audio_buffer.started": {
        audioPlayingRef.current = true;
        audioStartedAtRef.current = Date.now();
        setFloor("ai_speech_started");
        break;
      }
      case "response.output_audio_transcript.delta": {
        setAssistantSaying((s) => s + String(event.delta ?? ""));
        break;
      }
      case "output_audio_buffer.stopped":
      case "output_audio_buffer.cleared": {
        audioPlayingRef.current = false;
        setFloor("ai_speech_done");
        break;
      }
      case "response.done": {
        const response = (event.response ?? {}) as {
          usage?: Record<string, unknown>;
          status?: string;
          status_details?: { error?: { message?: string } };
          output?: { content?: { transcript?: string; text?: string }[] }[];
        };
        responseActiveRef.current = false;
        audioPlayingRef.current = false;
        setFloor("ai_speech_done");
        // A response can end without a word having been said. "cancelled" is
        // normal (someone spoke over her); "failed" is not, and used to be
        // silent — the transcript simply had a gap where an answer should be.
        if (response.status === "failed") {
          const detail = response.status_details?.error?.message
            ?? "The realtime model could not produce an answer.";
          setError(detail);
          if (session) {
            void api.reportConsultationClientError(session.id, {
              message: detail, event_type: "response.failed",
              floor_state: floorRef.current,
            }).catch(() => undefined);
          }
        }
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
        const err = (event.error ?? {}) as { message?: string; type?: string };
        const message = err.message ?? "The realtime service reported an error.";
        if (!isBenign(message)) setError(message);
        // Recorded, not just displayed. This whole class of bug was diagnosed
        // once from "it gives an error" and nothing else, because the banner
        // vanished with the page and the backend log was all 200s — the failing
        // exchange never touches this API (2026-08-24).
        if (session) {
          void api.reportConsultationClientError(session.id, {
            message, event_type: err.type ?? "error", floor_state: floorRef.current,
          }).catch(() => undefined);
        }
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

  // `start`'s onOpen fires the opening, which is defined above it but recreated
  // on every session change; the ref keeps the connection callback current.
  const openMeetingRef = useRef(openMeeting);
  useEffect(() => { openMeetingRef.current = openMeeting; }, [openMeeting]);

  /** Close the recording and hand it to the API. Awaited by `stop`. */
  const finishRecording = useCallback(async (sessionId: string) => {
    const rec = recorderRef.current;
    recorderRef.current = null;
    if (!rec) return;
    setRecording(false);
    const done = new Promise<void>((resolve) => {
      rec.onstop = () => resolve();
    });
    try { rec.stop(); } catch { return; }
    await done;
    const chunks = chunksRef.current;
    chunksRef.current = [];
    if (!chunks.length) return;
    const blob = new Blob(chunks, { type: chunks[0].type || "audio/webm" });
    try {
      await api.uploadConsultationAudio(sessionId, blob);
    } catch (e) {
      setRecordingNote(
        `The recording could not be saved (${(e as Error).message}). The transcript and ` +
        "the report are unaffected; only the speaker names are lost."
      );
    }
  }, []);

  const stop = useCallback(() => {
    clearTimers();
    if (sessionId) void finishRecording(sessionId);
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
    responseActiveRef.current = false;
    audioPlayingRef.current = false;
    appliedEagernessRef.current = null;
  }, [clearTimers, sessionId, finishRecording]);

  const start = useCallback(async (acceptOverCeiling = false) => {
    if (!session || !capabilities) return;
    if (startedRef.current) return;            // StrictMode remount, or a double click
    startedRef.current = true;
    setError("");
    setMicError("");
    setOverCeiling("");
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
    // Record the room, if this meeting was set up for it. The MICROPHONE
    // stream only: her own voice arrives over WebRTC and is not in it, which is
    // exactly right -- the recording exists so the humans' voices can be told
    // apart afterwards (rule 91).
    if (session.record_audio) {
      try {
        const mime = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"]
          .find((m) => MediaRecorder.isTypeSupported(m));
        const rec = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
        chunksRef.current = [];
        rec.ondataavailable = (e) => { if (e.data.size) chunksRef.current.push(e.data); };
        // A timeslice means a crash costs the last few seconds rather than the
        // whole meeting -- the chunks already delivered are still in hand.
        rec.start(5000);
        recorderRef.current = rec;
        setRecording(true);
        setRecordingNote("");
      } catch (e) {
        // Never fatal: a meeting that runs without a recording is a meeting
        // without speaker names, not a failed meeting. It says so on screen.
        setRecordingNote(
          `This meeting is not being recorded (${(e as Error).message}). Everything else works; ` +
          "the transcript just will not say who was speaking."
        );
      }
    }
    try {
      setConnection("connecting");
      const credential = await api.consultationClientSecret(session.id, acceptOverCeiling);
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
          // The credential was minted with this session's presence already in
          // it, so nothing needs sending yet — only a LATER change does.
          appliedEagernessRef.current = policyRef.current?.vad_eagerness ?? null;
          responseActiveRef.current = false;
          audioPlayingRef.current = false;
          void openMeetingRef.current?.();
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
      // 402 is the spend ceiling, and it is the one refusal with a way through.
      // It used to surface as a plain error saying "start anyway from the setup
      // screen" — from the live screen, which he had already left the setup
      // screen to reach. A dead end that names a door somewhere else is worse
      // than no door (2026-08-27).
      const message = (e as Error).message ?? "";
      if (message.startsWith("402:")) {
        setOverCeiling(message.replace(/^402:\s*/, "")
          .replace(/Start anyway from the setup screen[^.]*\./i, "").trim());
        return;
      }
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
    passage, minutesLeft, recording, recordingNote, overCeiling,
    start, stop, ask, toggleMute, togglePause, openFloor,
    runAnalysis, answerPermission, considerObservation,
    dismissPassage: () => setPassage(null),
    clearAskQueue: () => { askQueuedRef.current = null; setAskQueued(null); },
  };
}
