import { useEffect, useRef, useState } from "react";
import { Pencil } from "lucide-react";
import { api } from "../../lib/api";
import type { ConsultationTurn } from "../../lib/consultationTypes";
import { Card, CardContent, CardHeader, CardTitle } from "../ui";

/**
 * The transcript as it happens.
 *
 * No speaker is ever guessed. Live transcription gives us text and an item id,
 * not a person, so a turn reads "Participant" until someone types in a name by
 * hand — inventing one and showing it as fact would be worse than saying
 * nothing (rule 80).
 */
export function LiveTranscript({
  sessionId, turns, partials, assistantSaying, assistantName, onLabelled, onCorrected,
}: {
  sessionId: string;
  turns: ConsultationTurn[];
  partials: Record<string, string>;
  assistantSaying: string;
  assistantName: string;
  onLabelled: () => void;
  /** Correct a misheard line. Optional: the read-only Detail view passes none,
   *  and the pencil simply does not appear. */
  onCorrected?: () => void;
}) {
  const boxRef = useRef<HTMLDivElement>(null);
  const [stickToBottom, setStickToBottom] = useState(true);
  const [editing, setEditing] = useState<number | null>(null);
  const [fixing, setFixing] = useState<number | null>(null);
  const [textDraft, setTextDraft] = useState("");
  const [draft, setDraft] = useState("");

  const partialList = Object.entries(partials).filter(([, text]) => text.trim());

  useEffect(() => {
    if (!stickToBottom) return;
    const box = boxRef.current;
    if (box) box.scrollTop = box.scrollHeight;
  }, [turns.length, partialList.length, assistantSaying, stickToBottom]);

  const onScroll = () => {
    const box = boxRef.current;
    if (!box) return;
    // Once someone scrolls up to read, stop yanking them back down.
    setStickToBottom(box.scrollHeight - box.scrollTop - box.clientHeight < 60);
  };

  const saveLabel = async (turnId: number) => {
    await api.labelConsultationTurn(sessionId, turnId, draft.trim() || null);
    setEditing(null);
    setDraft("");
    onLabelled();
  };

  const saveText = async (turnId: number) => {
    const text = textDraft.trim();
    setFixing(null);
    const original = turns.find((t) => t.id === turnId)?.text ?? "";
    // An empty correction is a no-op, not a way to blank a line: the server
    // refuses it too, and deleting the meeting is the deliberate way to remove
    // what was said.
    if (!text || text === original) return;
    await api.correctConsultationTurn(sessionId, turnId, text);
    onCorrected?.();
  };

  return (
    <Card className="flex min-h-0 flex-1 flex-col">
      <CardHeader className="flex items-center justify-between gap-3">
        <CardTitle>Transcript</CardTitle>
        {!stickToBottom && (
          <button
            onClick={() => setStickToBottom(true)}
            className="text-xs text-amber-300 hover:text-amber-200"
          >
            Jump to latest
          </button>
        )}
      </CardHeader>
      <CardContent className="min-h-0 flex-1 overflow-hidden p-0">
        <div
          ref={boxRef}
          onScroll={onScroll}
          className="h-full space-y-3 overflow-y-auto px-5 pb-5"
        >
          {turns.length === 0 && partialList.length === 0 && (
            <p className="pt-4 text-sm text-slate-500">
              Nothing yet. What is said in the room will appear here.
            </p>
          )}

          {turns.map((turn) => (
            <div key={turn.id} className="group">
              <div className="flex items-center gap-2 text-xs">
                {editing === turn.id ? (
                  <input
                    autoFocus
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    onBlur={() => void saveLabel(turn.id)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") void saveLabel(turn.id);
                      if (e.key === "Escape") setEditing(null);
                    }}
                    placeholder="Who was speaking?"
                    className="rounded border border-slate-700 bg-slate-950 px-2 py-0.5 text-xs text-slate-100 focus:outline-none"
                  />
                ) : (
                  <button
                    onClick={() => {
                      if (turn.role === "assistant") return;
                      setEditing(turn.id);
                      setDraft(turn.speaker_label ?? "");
                    }}
                    className={`inline-flex items-center gap-1.5 font-semibold ${
                      turn.role === "assistant" ? "text-amber-300" : "text-slate-300"
                    }`}
                  >
                    {turn.role === "assistant"
                      ? assistantName
                      : turn.speaker_label ?? "Participant"}
                    {turn.role !== "assistant" && (
                      <Pencil className="h-3 w-3 opacity-0 transition-opacity group-hover:opacity-60" />
                    )}
                  </button>
                )}
                <span className="text-slate-600">{(turn.created_at ?? "").slice(11, 16)}</span>
              </div>
              {/* A misheard line can be fixed (rule 95). The original is not kept:
                  a person corrects a line precisely because the machine wrote
                  down something that was not said -- most painfully, a name. */}
              {fixing === turn.id ? (
                <textarea
                  autoFocus value={textDraft} rows={2}
                  onChange={(e) => setTextDraft(e.target.value)}
                  onBlur={() => void saveText(turn.id)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void saveText(turn.id); }
                    if (e.key === "Escape") setFixing(null);
                  }}
                  className="mt-0.5 w-full rounded border border-slate-700 bg-slate-950 px-2
                             py-1 text-sm text-slate-100 focus:border-amber-400/50
                             focus:outline-none"
                />
              ) : (
                <p className={`mt-0.5 text-sm leading-relaxed ${
                  turn.role === "assistant" ? "text-amber-100/90" : "text-slate-200"
                }`}>
                  {turn.text}
                  {turn.corrected_at && (
                    <span className="ml-1.5 text-xs text-emerald-400/70">(corrected)</span>
                  )}
                  {turn.role !== "assistant" && onCorrected && (
                    <button
                      onClick={() => { setFixing(turn.id); setTextDraft(turn.text); }}
                      title="She misheard this"
                      className="ml-1.5 align-middle text-slate-600 opacity-0
                                 transition-opacity hover:text-slate-300 group-hover:opacity-100">
                      <Pencil className="inline h-3 w-3" />
                    </button>
                  )}
                </p>
              )}
            </div>
          ))}

          {/* Partial text is visibly unfinished, and is never saved. */}
          {partialList.map(([id, text]) => (
            <div key={id}>
              <div className="text-xs font-semibold text-slate-500">Participant</div>
              <p className="mt-0.5 text-sm italic leading-relaxed text-slate-500">{text}</p>
            </div>
          ))}
          {assistantSaying && (
            <div>
              <div className="text-xs font-semibold text-amber-300/70">{assistantName}</div>
              <p className="mt-0.5 text-sm italic leading-relaxed text-amber-100/60">
                {assistantSaying}
              </p>
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
