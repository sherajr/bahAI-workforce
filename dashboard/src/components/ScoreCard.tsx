import type { Review } from "../lib/types";
import { badgeClasses, badgeFor, principleLabel, scoreBarColor } from "../lib/utils";
import { BadgePill, Card, CardContent, CardHeader, CardTitle, ProgressBar } from "./ui";

// Seven principle bars + overall badge.
export function ScoreCard({ review, badge }: { review: Review; badge?: string }) {
  const overall = review.overall ?? 0;
  const label = badge ?? badgeFor(overall);
  const entries = Object.entries(review.scores ?? {}).sort(([a], [b]) => a.localeCompare(b));

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle>Constitution scores</CardTitle>
        <div className="flex items-center gap-3">
          <span className="font-mono text-lg text-slate-100">{overall.toFixed(1)}/10</span>
          <BadgePill className={badgeClasses(label)}>{label}</BadgePill>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        {entries.length === 0 && (
          <p className="text-sm text-slate-500">No principle scores recorded.</p>
        )}
        {entries.map(([key, ps]) => (
          <div key={key}>
            <div className="mb-1 flex items-baseline justify-between gap-4">
              <span className="text-sm text-slate-300">{principleLabel(key)}</span>
              <span className="font-mono text-xs text-slate-400">{ps.score}/10</span>
            </div>
            <ProgressBar value={ps.score * 10} colorClass={scoreBarColor(ps.score)} />
            {ps.note && <p className="mt-1 text-xs leading-relaxed text-slate-500">{ps.note}</p>}
          </div>
        ))}
        {review.recommendation && (
          <p className="border-t border-slate-800 pt-3 text-sm text-slate-400">
            <span className="font-semibold text-slate-300">Reviewer: </span>
            {review.recommendation}
          </p>
        )}
      </CardContent>
    </Card>
  );
}
