/**
 * The WebRTC transport for a live consultation.
 *
 * Native browser WebRTC and explicit event handling, deliberately, with no SDK:
 * the whole feature turns on knowing exactly which events arrive and being able
 * to refuse to create a response. An abstraction that helpfully answers for you
 * is the one thing this must not have.
 *
 * The flow (OpenAI Realtime GA, checked 2026-08-21):
 *   1. the app's own API mints a short-lived client secret (the master key
 *      never comes near this file)
 *   2. an offer goes to /v1/realtime/calls with that secret
 *   3. events flow both ways over the "oai-events" data channel
 *
 * Everything here is mechanism. No decision about whether the assistant may
 * speak is taken in this file — see consultationGovernor.ts.
 */

export interface RealtimeEvent {
  type: string;
  [key: string]: unknown;
}

export interface RealtimeConnection {
  pc: RTCPeerConnection;
  dc: RTCDataChannel;
  audio: HTMLAudioElement;
  stream: MediaStream;
  send: (event: RealtimeEvent) => boolean;
  close: () => void;
}

export class MicrophoneError extends Error {}
export class ConnectionError extends Error {}

/** Ask for the microphone. Only ever called from a Start the user pressed. */
export async function requestMicrophone(): Promise<MediaStream> {
  if (!navigator.mediaDevices?.getUserMedia) {
    throw new MicrophoneError(
      "This browser cannot reach a microphone. Chrome or Edge over http://localhost works."
    );
  }
  try {
    return await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });
  } catch (err) {
    const name = (err as DOMException)?.name ?? "";
    if (name === "NotAllowedError" || name === "SecurityError") {
      throw new MicrophoneError(
        "Microphone access was denied. Allow the microphone for this page in your " +
        "browser, then press Retry."
      );
    }
    if (name === "NotFoundError" || name === "OverconstrainedError") {
      throw new MicrophoneError(
        "No microphone was found. Plug one in or choose one in your system settings, " +
        "then press Retry."
      );
    }
    throw new MicrophoneError(`The microphone could not be opened (${name || "unknown error"}).`);
  }
}

export async function connectRealtime(opts: {
  clientSecret: string;
  callsUrl: string;
  model: string;
  stream: MediaStream;
  onEvent: (event: RealtimeEvent) => void;
  onOpen: () => void;
  onClose: (reason: string) => void;
}): Promise<RealtimeConnection> {
  const pc = new RTCPeerConnection();

  // The assistant's voice. Created here rather than rendered, so nothing about
  // the audio depends on a component staying mounted.
  const audio = document.createElement("audio");
  audio.autoplay = true;
  pc.ontrack = (e) => {
    audio.srcObject = e.streams[0];
    void audio.play().catch(() => {
      /* autoplay policy — the user has already interacted by pressing Start */
    });
  };

  for (const track of opts.stream.getTracks()) pc.addTrack(track, opts.stream);

  const dc = pc.createDataChannel("oai-events");
  dc.addEventListener("message", (e) => {
    try {
      opts.onEvent(JSON.parse(e.data as string) as RealtimeEvent);
    } catch {
      /* a malformed frame is not worth taking the meeting down for */
    }
  });
  dc.addEventListener("open", () => opts.onOpen());

  let closedReported = false;
  const reportClosed = (reason: string) => {
    if (closedReported) return;
    closedReported = true;
    opts.onClose(reason);
  };
  pc.onconnectionstatechange = () => {
    if (pc.connectionState === "failed") reportClosed("the connection failed");
    else if (pc.connectionState === "disconnected") reportClosed("the connection dropped");
    else if (pc.connectionState === "closed") reportClosed("the connection closed");
  };

  const offer = await pc.createOffer();
  await pc.setLocalDescription(offer);

  const url = `${opts.callsUrl}?model=${encodeURIComponent(opts.model)}`;
  let answer: string;
  try {
    const res = await fetch(url, {
      method: "POST",
      body: offer.sdp,
      headers: {
        Authorization: `Bearer ${opts.clientSecret}`,
        "Content-Type": "application/sdp",
      },
    });
    if (!res.ok) {
      throw new ConnectionError(
        `OpenAI refused the connection (${res.status}). ${(await res.text()).slice(0, 200)}`
      );
    }
    answer = await res.text();
  } catch (err) {
    pc.close();
    if (err instanceof ConnectionError) throw err;
    throw new ConnectionError(
      `Could not reach OpenAI to start the session (${(err as Error).message}).`
    );
  }
  await pc.setRemoteDescription({ type: "answer", sdp: answer });

  const send = (event: RealtimeEvent): boolean => {
    if (dc.readyState !== "open") return false;
    dc.send(JSON.stringify(event));
    return true;
  };

  const close = () => {
    try { dc.close(); } catch { /* already gone */ }
    try { pc.getSenders().forEach((s) => s.track?.stop()); } catch { /* already gone */ }
    try { opts.stream.getTracks().forEach((t) => t.stop()); } catch { /* already gone */ }
    try { pc.close(); } catch { /* already gone */ }
    audio.srcObject = null;
    reportClosed("the session ended");
  };

  return { pc, dc, audio, stream: opts.stream, send, close };
}

// ── The events this app sends ───────────────────────────────────────────────
//
// Written as builders rather than inline objects so every place that stops the
// assistant stops it the same way. Barge-in in particular is three events, and
// sending two of the three leaves audio playing.

export const events = {
  /** Configure the live session. `create_response: false` is the whole point. */
  sessionUpdate(session: Record<string, unknown>): RealtimeEvent {
    return { type: "session.update", session };
  },

  /** The only way a response is ever created. Never called from a VAD event. */
  createResponse(instructions: string, modalities: string[] = ["audio"]): RealtimeEvent {
    return {
      type: "response.create",
      response: { instructions, output_modalities: modalities },
    };
  },

  /** Say exactly this and stop — used for a request for the floor, where the
   *  substance must NOT be smuggled in. */
  createExactResponse(sentence: string, modalities: string[] = ["audio"]): RealtimeEvent {
    return {
      type: "response.create",
      response: {
        instructions:
          `Say exactly this, and nothing else: "${sentence}" Then stop and wait.`,
        output_modalities: modalities,
      },
    };
  },

  cancelResponse(): RealtimeEvent {
    return { type: "response.cancel" };
  },

  /** WebRTC-specific: drop audio already handed to the peer connection but not
   *  yet heard. Without this the assistant keeps talking after the cancel. */
  clearOutputAudio(): RealtimeEvent {
    return { type: "output_audio_buffer.clear" };
  },

  /** Remove the unheard tail from the conversation, so the model's own record
   *  matches what the room actually heard. */
  truncate(itemId: string, audioEndMs: number): RealtimeEvent {
    return {
      type: "conversation.item.truncate",
      item_id: itemId,
      content_index: 0,
      audio_end_ms: Math.max(0, Math.round(audioEndMs)),
    };
  },
};
