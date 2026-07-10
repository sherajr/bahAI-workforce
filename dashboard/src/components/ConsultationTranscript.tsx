import type { ConsultationTurn } from "../lib/types";
import { imageUrl } from "../lib/api";
import { AGENT_COLORS, cn, rosterFor } from "../lib/utils";
import { Card, CardContent, CardHeader, CardTitle, RosterAvatar } from "./ui";

interface ParsedVerification {
  verdict: string;
  quote: string;
  source: string;
  reasoning: string;
}

function parseVerification(message: string): ParsedVerification | null {
  if (!message) return null;
  const lines = message.split(/\r?\n/);
  const hasVerdict = lines.some((line) => /^\s*verdict\s*:/i.test(line));
  if (!hasVerdict) return null;

  let verdict = "";
  const quoteLines: string[] = [];
  let source = "";
  let reasoning = "";
  let inQuote = false;

  for (const line of lines) {
    const verdictMatch = line.match(/^\s*verdict\s*:\s*(.*)/i);
    if (verdictMatch) {
      verdict = verdictMatch[1].trim();
      inQuote = false;
      continue;
    }

    const quoteMatch = line.match(/^\s*verified\s+quote\s*:\s*(.*)/i);
    if (quoteMatch) {
      const firstLine = quoteMatch[1].trim();
      if (firstLine) {
        quoteLines.push(firstLine);
      }
      inQuote = true;
      continue;
    }

    const sourceMatch = line.match(/^\s*source\s*:\s*(.*)/i);
    if (sourceMatch) {
      source = sourceMatch[1].trim();
      inQuote = false;
      continue;
    }

    const reasoningMatch = line.match(/^\s*reasoning\s*:\s*(.*)/i);
    if (reasoningMatch) {
      reasoning = reasoningMatch[1].trim();
      inQuote = false;
      continue;
    }

    if (inQuote) {
      const trimmed = line.trim();
      if (trimmed) {
        quoteLines.push(trimmed);
      }
    }
  }

  let quote = quoteLines.join("\n").trim();
  if (quote.startsWith('"') && quote.endsWith('"')) {
    quote = quote.slice(1, -1).trim();
  } else if (quote.startsWith("'") && quote.endsWith("'")) {
    quote = quote.slice(1, -1).trim();
  }

  return { verdict, quote, source, reasoning };
}

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
          const r = rosterFor(t.agent);
          // Named agents (Ruth/Theo/…) show their name with the role as context;
          // non-persona turns (Sheraj, System) keep their plain label.
          const displayName = r ? r.name : t.agent;
          const roleText = r ? `${r.role} · ${t.role}` : t.role;
          const parsed = parseVerification(t.message);

          return (
            <div
              key={i}
              className={cn(
                "rounded-lg border border-slate-800 border-l-4 bg-slate-950/60 px-4 py-3",
                borderColor
              )}
            >
              <div className="mb-1 flex items-center gap-2">
                {r && <RosterAvatar src={r.avatar} name={r.name} className="h-6 w-6" />}
                <span className={cn("text-sm font-semibold", textColor)}>{displayName}</span>
                <span className="text-xs text-slate-500">{roleText}</span>
              </div>
              {t.image && (
                <img
                  src={imageUrl(t.image)}
                  alt="Front face preview"
                  className="mb-2 mt-1 max-h-72 rounded-lg border border-slate-700 object-contain shadow-md"
                />
              )}
              {parsed ? (
                <Card className="mt-2 bg-slate-900/50 border-slate-800/80">
                  <CardContent className="pt-3 pb-3 px-4 space-y-3">
                    <div className="flex items-center gap-2">
                      <span
                        className={cn(
                          "rounded-full border px-2.5 py-0.5 text-[10px] uppercase tracking-wide font-semibold",
                          parsed.verdict.toLowerCase().includes("grounded")
                            ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-300"
                            : "border-amber-500/40 bg-amber-500/10 text-amber-300"
                        )}
                      >
                        {parsed.verdict.toLowerCase().includes("grounded")
                          ? "Verified against sources"
                          : "Original composition — adapted from sources"}
                      </span>
                    </div>
                    {parsed.quote && (
                      <div className="border-l-2 border-slate-700 pl-4 py-0.5 space-y-1">
                        <blockquote className="font-serif italic text-base text-slate-100 whitespace-pre-wrap">
                          “{parsed.quote}”
                        </blockquote>
                        {parsed.source && (
                          <cite className="not-italic text-xs text-slate-400 block mt-1">
                            — {parsed.source}
                          </cite>
                        )}
                      </div>
                    )}
                    {parsed.reasoning && (
                      <p className="text-sm text-slate-300 leading-relaxed">
                        {parsed.reasoning}
                      </p>
                    )}
                  </CardContent>
                </Card>
              ) : (
                <p className="whitespace-pre-wrap text-sm leading-relaxed text-slate-300">
                  {t.message}
                </p>
              )}
            </div>
          );
        })}
      </CardContent>
    </Card>
  );
}

