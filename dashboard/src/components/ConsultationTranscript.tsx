import type { ConsultationTurn } from "../lib/types";
import { AGENT_COLORS, cn } from "../lib/utils";
import { Card, CardContent, CardHeader, CardTitle } from "./ui";

// The four-turn agent consultation, rendered as a meeting record.
export function ConsultationTranscript({ turns }: { turns: ConsultationTurn[] }) {
  if (!turns || turns.length === 0) return null;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Agent consultation</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {turns.map((t, i) => {
          const colors = AGENT_COLORS[t.agent] ?? "border-l-slate-500 text-slate-300";
          const [borderColor, textColor] = colors.split(" ");
          return (
            <div
              key={i}
              className={cn(
                "rounded-lg border border-slate-800 border-l-4 bg-slate-950/60 px-4 py-3",
                borderColor
              )}
            >
              <div className="mb-1 flex items-baseline gap-2">
                <span className={cn("text-sm font-semibold", textColor)}>{t.agent}</span>
                <span className="text-xs text-slate-500">{t.role}</span>
              </div>
              <p className="whitespace-pre-wrap text-sm leading-relaxed text-slate-300">
                {t.message}
              </p>
            </div>
          );
        })}
      </CardContent>
    </Card>
  );
}
