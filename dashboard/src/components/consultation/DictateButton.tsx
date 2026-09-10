import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2, Mic, Square } from "lucide-react";
import { api } from "../../lib/api";

/**
 * Say it instead of typing it.
 *
 * Press to record, press again to stop; the text comes back and is appended to
 * whatever is already in the box, so dictating twice adds rather than replaces
 * and nothing anyone typed is ever destroyed by pressing this.
 *
 * The audio goes to the same OpenAI account the rest of this tab uses and is
 * never written to disk at either end — it exists in memory for one request.
 * Deliberately NOT the browser's built-in speech recognition, which would send
 * this to a third party (Google, in Chrome) that nothing else here talks to,
 * and which is markedly worse at names.
 *
 * A tap too short to be speech is discarded without being sent, because a
 * transcription model handed silence invents a word rather than returning
 * nothing (see MIN_DICTATION_MS).
 *
 * The microphone is released the moment recording stops. A page that quietly
 * holds the mic open leaves the browser's recording indicator lit, which reads
 * as "this thing is still listening to me" — and here of all places that would
 * be an alarming thing to get wrong.
 */
/** Below this, a press is a mis-tap rather than dictation. */
const MIN_DICTATION_MS = 600;

export function Dictate({
  onText, children, title = "Dictate",
}: {
  onText: (text: string) => void;
  children: React.ReactNode;
  title?: string;
}) {
  const [state, setState] = useState<"idle" | "recording" | "working">("idle");
  const [error, setError] = useState("");
  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const startedAtRef = useRef(0);

  // Bumped on unmount. `getUserMedia` is asynchronous, so a permission prompt
  // answered after this component has gone resolves into a stream nobody owns
  // -- and the old cleanup could not release it, because it ran BEFORE the
  // stream existed (rule 114). The same race as the consultation hook's, in the
  // place it is easiest to hit: a mic button on a form.
  const aliveRef = useRef(0);

  const release = useCallback(() => {
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    recorderRef.current = null;
  }, []);

  // A component that unmounts mid-recording must not leave the mic open -- and
  // must not send a transcription request either. Detaching `onstop` first is
  // what stops the recorder's own stop event calling `send()` into a component
  // that is no longer on screen, which would be a paid call nobody asked for
  // and whose text has nowhere to go.
  useEffect(() => () => {
    aliveRef.current += 1;
    const rec = recorderRef.current;
    if (rec) {
      rec.onstop = null;
      rec.ondataavailable = null;
      if (rec.state !== "inactive") {
        try { rec.stop(); } catch { /* already stopping */ }
      }
    }
    chunksRef.current = [];
    release();
  }, [release]);

  const start = async () => {
    setError("");
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      setError("This browser cannot record. Type it in instead.");
      return;
    }
    const myGen = aliveRef.current;
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (e) {
      if (aliveRef.current !== myGen) return;
      const name = (e as DOMException)?.name ?? "";
      setError(name === "NotAllowedError"
        ? "The microphone was blocked. Allow it for this page, or type it in."
        : "No microphone was found. Type it in instead.");
      return;
    }
    if (aliveRef.current !== myGen) {
      // Answered after this button went away. Release it here, because the
      // cleanup that ran on unmount had nothing to release yet.
      stream.getTracks().forEach((t) => t.stop());
      return;
    }
    streamRef.current = stream;
    try {
      const mime = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"]
        .find((m) => MediaRecorder.isTypeSupported(m));
      const rec = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
      chunksRef.current = [];
      rec.ondataavailable = (e) => { if (e.data.size) chunksRef.current.push(e.data); };
      rec.onstop = () => { void send(); };
      rec.start();
      recorderRef.current = rec;
      startedAtRef.current = Date.now();
      setState("recording");
    } catch (e) {
      release();
      setError(`Recording could not start (${(e as Error).message}). Type it in instead.`);
    }
  };

  const send = async () => {
    const chunks = chunksRef.current;
    const heldFor = Date.now() - startedAtRef.current;
    chunksRef.current = [];
    release();
    if (!chunks.length) { setState("idle"); return; }
    // An accidental double-tap is not dictation. Worth catching specifically:
    // handed near-silence, the transcription model does not return nothing — it
    // returns a plausible short word (a real reply to one second of silence was
    // "Sijainti."), and that would appear in the box as if it had been said.
    if (heldFor < MIN_DICTATION_MS) {
      setState("idle");
      setError("That was too short to hear. Hold the button while you speak.");
      return;
    }
    setState("working");
    try {
      const blob = new Blob(chunks, { type: chunks[0].type || "audio/webm" });
      const { text } = await api.dictate(blob);
      onText(text);
    } catch (e) {
      setError(`${(e as Error).message}`.replace(/^\d{3}:\s*/, ""));
    } finally {
      setState("idle");
    }
  };

  const stop = () => {
    const rec = recorderRef.current;
    if (!rec) { setState("idle"); return; }
    try { rec.stop(); } catch { release(); setState("idle"); }
  };

  return (
    <div className="space-y-1">
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">{children}</div>
        <button
          type="button"
          onClick={() => (state === "recording" ? stop() : void start())}
          disabled={state === "working"}
          title={state === "recording" ? "Stop and add what you said" : title}
          aria-label={state === "recording" ? "Stop dictating" : title}
          className={`mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border transition ${
            state === "recording"
              ? "border-rose-400/60 bg-rose-500/15 text-rose-300"
              : "border-slate-800 bg-slate-900 text-slate-400 hover:border-slate-700 hover:text-slate-200"
          } disabled:opacity-50`}
        >
          {state === "working"
            ? <Loader2 className="h-4 w-4 animate-spin" />
            : state === "recording"
              ? <Square className="h-3.5 w-3.5 fill-current" />
              : <Mic className="h-4 w-4" />}
        </button>
      </div>
      {state === "recording" && (
        <p className="text-xs text-rose-300">Listening — press again when you have finished.</p>
      )}
      {error && <p className="text-xs text-amber-300">{error}</p>}
    </div>
  );
}

/** Append dictated text to whatever is already there, tidily. */
export function appendDictated(existing: string, spoken: string): string {
  const a = (existing ?? "").trim();
  const b = (spoken ?? "").trim();
  if (!b) return existing ?? "";
  if (!a) return b;
  return `${a} ${b}`;
}
