import { useState } from "react";
import { ChevronDown, ChevronRight, Loader2, Mic, Users } from "lucide-react";
import type {
  ConsultationDetail as Detail, ConsultationParticipant, ConsultationStateMap,
  ConsultationTurn, MapItem,
} from "../../lib/consultationTypes";
import { BadgePill, Button, Card, CardContent, CardHeader, CardTitle, ErrorNote } from "../ui";

/**
 * Everything the meeting produced, kept out of the way.
 *
 * The live view is deliberately four things (see ConsultationMap); this is where
 * the rest lives — the full working map, the whole transcript, and the business
 * of working out who was speaking. Nobody needs any of it while consulting, and
 * everybody wants it afterwards.
 */

const SECTIONS: { key: keyof ConsultationStateMap; label: string; hint?: string }[] = [
  { key: "agreements", label: "Where the group agreed" },
  { key: "ideas", label: "Ideas on the table" },
  { key: "tensions", label: "Pulled apart" },
  { key: "needs_and_concerns", label: "Concerns raised" },
  { key: "possible_syntheses", label: "Possible syntheses",
    hint: "Offered for the group to consider, not as the answer." },
  { key: "facts", label: "Facts" },
  { key: "assumptions", label: "Assumptions not yet established" },
  { key: "principles", label: "Principles in play" },
  { key: "unresolved_questions", label: "Open questions" },
  { key: "questions_to_investigate", label: "Needs information, not argument" },
];

const FACT_TONE: Record<string, string> = {
  confirmed: "border-emerald-500/30 bg-emerald-500/10 text-emerald-200",
  uncertain: "border-slate-700 bg-slate-800/60 text-slate-300",
  disputed: "border-amber-500/30 bg-amber-500/10 text-amber-200",
};

function speakersIn(turns: ConsultationTurn[]): string[] {
  const keys = new Set<string>();
  for (const t of turns) {
    // A diarised label is a short voice key ("A", "B"); anything longer is
    // already a person's name and is not something to be re-mapped.
    if (t.role === "human" && t.speaker_label && t.speaker_label.length <= 3) {
      keys.add(t.speaker_label);
    }
  }
  return [...keys].sort();
}

export function ConsultationDetailPanel({
  detail, onMapSpeaker, onDiarize, diarizing, error,
}: {
  detail: Detail;
  onMapSpeaker: (participantId: string, speakerKey: string | null) => void;
  onDiarize: () => void;
  diarizing?: boolean;
  error?: string;
}) {
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const { session, state, participants, final_turns: turns, has_diarized } = detail;
  const unmapped = speakersIn(turns);
  const recorded = !!session.record_audio;

  return (
    <div className="space-y-3">
      {/* ── Who was here ─────────────────────────────────────────────────── */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Users className="h-4 w-4 text-slate-400" /> Who was here
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {participants.length === 0 && (
            <p className="text-sm text-slate-500">
              Nobody was named for this meeting.
            </p>
          )}
          {participants.length > 0 && !has_diarized && (
            <p className="text-xs text-slate-500">
              {recorded
                ? "Names can be matched to voices once the recording has been gone through."
                : "This meeting was not recorded, so the transcript cannot say who was speaking."}
            </p>
          )}
          {participants.map((p: ConsultationParticipant) => (
            <div key={p.id} className="flex items-center justify-between gap-3">
              <span className="text-sm text-slate-300">{p.name}</span>
              {has_diarized ? (
                <select
                  value={p.speaker_key ?? ""}
                  onChange={(e) => onMapSpeaker(p.id, e.target.value || null)}
                  className="rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-xs text-slate-200"
                >
                  <option value="">— which voice? —</option>
                  {[...new Set([...unmapped, ...(p.speaker_key ? [p.speaker_key] : [])])]
                    .sort()
                    .map((k) => (
                      <option key={k} value={k}>Voice {k}</option>
                    ))}
                </select>
              ) : (
                <BadgePill className="border-slate-700 bg-slate-800/60 text-slate-400">
                  not matched
                </BadgePill>
              )}
            </div>
          ))}

          {recorded && !has_diarized && session.status === "ended" && (
            <div className="border-t border-slate-800 pt-3">
              <Button onClick={onDiarize} disabled={diarizing}>
                {diarizing ? (
                  <><Loader2 className="mr-2 h-4 w-4 animate-spin" /> Going through the recording…</>
                ) : (
                  <><Mic className="mr-2 h-4 w-4" /> Work out who said what</>
                )}
              </Button>
              <p className="mt-2 text-xs text-slate-500">
                Sends this meeting's recording to OpenAI once, to separate the voices. It
                labels them A, B, C — it does not know who anyone is, so you match the
                names yourself afterwards. This costs a small amount.
              </p>
            </div>
          )}
          {session.audio_status === "failed" && session.audio_note && (
            <ErrorNote>{session.audio_note}</ErrorNote>
          )}
          {error && <ErrorNote>{error}</ErrorNote>}
        </CardContent>
      </Card>

      {/* ── The full working map ─────────────────────────────────────────── */}
      <Card>
        <CardHeader>
          <CardTitle>The full working map</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          <p className="px-4 pb-3 pt-1 text-xs text-slate-500">
            Everything the assistant tracked while listening. This is her working
            structure, not a summary — the report is the readable version.
          </p>
          {SECTIONS.map((section) => {
            const items = (state[section.key] as MapItem[] | undefined) ?? [];
            const count = items.length;
            const isOpen = open[section.key as string] ?? false;
            return (
              <div key={section.key as string}
                   className="border-b border-slate-800/70 last:border-0">
                <button
                  onClick={() => setOpen((o) => ({ ...o, [section.key]: !isOpen }))}
                  disabled={!count}
                  className="flex w-full items-center justify-between px-4 py-2 text-left text-sm text-slate-300 hover:bg-slate-800/40 disabled:opacity-40"
                >
                  <span className="font-medium">{section.label}</span>
                  <span className="flex items-center gap-2 text-xs text-slate-500">
                    {count}
                    {count > 0 && (isOpen
                      ? <ChevronDown className="h-4 w-4" />
                      : <ChevronRight className="h-4 w-4" />)}
                  </span>
                </button>
                {isOpen && count > 0 && (
                  <ul className="space-y-1.5 px-4 pb-3">
                    {section.hint && (
                      <li className="text-xs text-slate-500">{section.hint}</li>
                    )}
                    {items.map((item) => (
                      <li key={item.id} className="text-sm leading-relaxed text-slate-300">
                        <span className="mr-2 text-slate-600">·</span>{item.text}
                        {section.key === "facts" && item.status && (
                          <BadgePill className={`ml-2 ${FACT_TONE[item.status] ?? FACT_TONE.uncertain}`}>
                            {item.status}
                          </BadgePill>
                        )}
                        {item.note && (
                          <span className="block pl-4 text-xs text-slate-500">{item.note}</span>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            );
          })}
        </CardContent>
      </Card>

      {/* ── The transcript ───────────────────────────────────────────────── */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center justify-between gap-2">
            <span>Transcript</span>
            <BadgePill className={has_diarized
              ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-200"
              : "border-slate-700 bg-slate-800/60 text-slate-400"}>
              {has_diarized ? "voices separated" : "as heard live"}
            </BadgePill>
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          {turns.length === 0 && (
            <p className="text-sm text-slate-500">Nothing was transcribed.</p>
          )}
          {turns.map((t) => (
            <div key={t.id} className="text-sm leading-relaxed">
              <span className={t.role === "assistant" ? "text-amber-300/80" : "text-slate-500"}>
                {t.role === "assistant"
                  ? "Abigail"
                  : t.speaker_label
                    ? (t.speaker_label.length <= 3 ? `Voice ${t.speaker_label}` : t.speaker_label)
                    : "Participant"}
              </span>
              <span className="mx-2 text-slate-700">·</span>
              <span className="text-slate-300">{t.text}</span>
            </div>
          ))}
        </CardContent>
      </Card>
    </div>
  );
}
