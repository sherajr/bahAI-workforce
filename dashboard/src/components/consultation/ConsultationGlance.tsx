import { CircleHelp, CircleDot, ListChecks, UserRound } from "lucide-react";
import type { ConsultationStateMap, MapItem, OpenThreads } from "../../lib/consultationTypes";
import { Card, CardContent } from "../ui";

/**
 * The consultation at a glance (owner ask 2026-08-27: "a visual aid for what
 * we've talked about and potential items or questions to address").
 *
 * Two halves, and both are meant to be read in about a second while you are
 * listening to someone:
 *
 *   1. A BAR showing the shape of the meeting — how much is settled against how
 *      much is still moving — plus the subjects touched, as chips.
 *   2. A short list of what is actually outstanding, phrased as things to
 *      address rather than as data.
 *
 * The outstanding list comes from the SAME `_open_threads` the spoken time
 * check reads, so the screen and her voice can never disagree about what is
 * still open.
 *
 * It deliberately does not score anything. The bar shows counts, which are a
 * fact about the record; it is not a completeness percentage or a progress
 * meter, because "how far through consultation are we" is not a quantity and a
 * number would invite the group to chase it.
 */

const TONE = {
  agreed: { bar: "bg-emerald-500/70", dot: "text-emerald-300", label: "agreed" },
  open: { bar: "bg-amber-500/70", dot: "text-amber-300", label: "unresolved" },
  question: { bar: "bg-sky-500/60", dot: "text-sky-300", label: "questions" },
} as const;

function Bar({ agreed, open, questions }: { agreed: number; open: number; questions: number }) {
  const total = agreed + open + questions;
  if (!total) {
    return (
      <div className="h-2 w-full overflow-hidden rounded-full bg-slate-800">
        <div className="h-full w-full bg-slate-800" />
      </div>
    );
  }
  const pct = (n: number) => `${(n / total) * 100}%`;
  return (
    <div className="flex h-2 w-full overflow-hidden rounded-full bg-slate-800">
      {agreed > 0 && <div className={TONE.agreed.bar} style={{ width: pct(agreed) }} />}
      {open > 0 && <div className={TONE.open.bar} style={{ width: pct(open) }} />}
      {questions > 0 && <div className={TONE.question.bar} style={{ width: pct(questions) }} />}
    </div>
  );
}

export function ConsultationGlance({
  state, threads, decided,
}: {
  state: ConsultationStateMap;
  threads?: OpenThreads;
  decided: boolean;
}) {
  const themes = (state.themes ?? []) as MapItem[];
  const agreed = ((state.agreements ?? []) as MapItem[]).length;
  const open = ((state.tensions ?? []) as MapItem[]).length;
  const questions = ((state.unresolved_questions ?? []) as MapItem[]).length;

  const toAddress: { icon: JSX.Element; text: string; note: string }[] = [];
  for (const q of threads?.questions ?? []) {
    toAddress.push({
      icon: <CircleHelp className="h-3.5 w-3.5 text-sky-300" />, text: q, note: "unanswered",
    });
  }
  for (const d of threads?.unconfirmed ?? []) {
    toAddress.push({
      icon: <ListChecks className="h-3.5 w-3.5 text-amber-300" />, text: d,
      note: "sounded decided, not confirmed",
    });
  }
  for (const t of threads?.unresolved ?? []) {
    toAddress.push({
      icon: <CircleDot className="h-3.5 w-3.5 text-amber-300" />, text: t, note: "unresolved",
    });
  }
  for (const a of threads?.unowned ?? []) {
    toAddress.push({
      icon: <UserRound className="h-3.5 w-3.5 text-slate-400" />, text: a, note: "nobody assigned",
    });
  }

  return (
    <Card>
      <CardContent className="space-y-4 py-4">
        <div className="space-y-2">
          <div className="flex items-baseline justify-between gap-3">
            <span className="text-sm font-medium text-slate-200">Where we are</span>
            {decided && (
              <span className="text-xs text-emerald-300">a decision is confirmed</span>
            )}
          </div>
          <Bar agreed={agreed} open={open} questions={questions} />
          <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-400">
            {([["agreed", agreed], ["open", open], ["question", questions]] as const).map(
              ([key, n]) => (
                <span key={key} className="inline-flex items-center gap-1.5">
                  <span className={`inline-block h-2 w-2 rounded-full ${TONE[key].bar}`} />
                  {n} {TONE[key].label}
                </span>
              )
            )}
          </div>
        </div>

        <div className="space-y-1.5">
          <span className="text-xs uppercase tracking-wide text-slate-500">
            What we have talked about
          </span>
          {themes.length === 0 ? (
            <p className="text-sm text-slate-500">
              Nothing yet — this fills in as the consultation goes on.
            </p>
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {themes.map((t) => (
                <span
                  key={t.id}
                  className="rounded-full border border-slate-700 bg-slate-800/60 px-2.5 py-1 text-xs text-slate-300"
                >
                  {t.text}
                </span>
              ))}
            </div>
          )}
        </div>

        <div className="space-y-1.5">
          <span className="text-xs uppercase tracking-wide text-slate-500">
            Still to address
          </span>
          {toAddress.length === 0 ? (
            <p className="text-sm text-slate-500">Nothing outstanding in the record.</p>
          ) : (
            <ul className="space-y-1.5">
              {toAddress.slice(0, 8).map((item, i) => (
                <li key={i} className="flex items-start gap-2 text-sm leading-relaxed text-slate-300">
                  <span className="mt-0.5 shrink-0">{item.icon}</span>
                  <span>
                    {item.text}
                    <span className="ml-2 text-xs text-slate-500">{item.note}</span>
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
