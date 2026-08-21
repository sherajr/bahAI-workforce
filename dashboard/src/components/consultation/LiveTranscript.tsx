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
  sessionId, turns, partials, assistantSaying, assistantName, onLabelled,
}: {
  sessionId: string;
  turns: ConsultationTurn[];
  partials: Record<string, string>;
  assistantSaying: string;
  assistantName: string;
  onLabelled: () => void;
}) {
  const boxRef = useRef<HTMLDivElement>(null);
  const [stickToBottom, setStickToBottom] = useState(true);
  const [editing, setEditing] = useState<number | null>(null);
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
              <p className={`mt-0.5 text-sm leading-relaxed ${
                turn.role === "assistant" ? "text-amber-100/90" : "text-slate-200"
              }`}>
                {turn.text}
              </p>
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
