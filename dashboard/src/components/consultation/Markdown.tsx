import type { ReactNode } from "react";

/**
 * A very small Markdown renderer for the consultation report.
 *
 * Deliberately hand-written and deliberately tiny. The report contains model-
 * written prose and text spoken in a private meeting, and this feature's rules
 * forbid putting either through `dangerouslySetInnerHTML` — so nothing here
 * produces an HTML string at any point. Every branch returns React elements,
 * and any syntax it does not recognise falls through as plain text rather than
 * being interpreted. There is no link syntax on purpose: a report is read, not
 * navigated, and an anchor is the one element worth not synthesising from text
 * a model wrote.
 *
 * It handles what `live_consultation_report.build_report` actually emits:
 * headings, bullets, blockquotes, bold, italic, and horizontal rules.
 */

/** `**bold**` and `*italic*`, without regex-replacing into markup. */
function inline(text: string, keyPrefix: string): ReactNode[] {
  const out: ReactNode[] = [];
  const pattern = /(\*\*[^*]+\*\*|\*[^*]+\*)/g;
  let last = 0;
  let match: RegExpExecArray | null;
  let n = 0;
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > last) out.push(text.slice(last, match.index));
    const token = match[0];
    if (token.startsWith("**")) {
      out.push(
        <strong key={`${keyPrefix}-b${n}`} className="font-semibold text-slate-100">
          {token.slice(2, -2)}
        </strong>
      );
    } else {
      out.push(
        <em key={`${keyPrefix}-i${n}`} className="text-slate-400">{token.slice(1, -1)}</em>
      );
    }
    last = match.index + token.length;
    n += 1;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

export function Markdown({ text }: { text: string }) {
  const lines = (text ?? "").split("\n");
  const blocks: ReactNode[] = [];
  let bullets: string[] = [];
  let quote: string[] = [];

  const flushBullets = () => {
    if (!bullets.length) return;
    const items = bullets;
    bullets = [];
    blocks.push(
      <ul key={`ul-${blocks.length}`} className="ml-1 space-y-1.5">
        {items.map((b, i) => (
          <li key={i} className="flex gap-2 text-sm leading-relaxed text-slate-300">
            <span className="text-slate-600">·</span>
            <span>{inline(b, `ul${blocks.length}-${i}`)}</span>
          </li>
        ))}
      </ul>
    );
  };

  const flushQuote = () => {
    if (!quote.length) return;
    const body = quote.join(" ");
    quote = [];
    blocks.push(
      <blockquote key={`q-${blocks.length}`}
                  className="border-l-2 border-amber-400/40 pl-3 text-sm italic leading-relaxed text-slate-200">
        {inline(body, `q${blocks.length}`)}
      </blockquote>
    );
  };

  const flushAll = () => { flushBullets(); flushQuote(); };

  lines.forEach((raw, i) => {
    const line = raw.trimEnd();
    if (!line.trim()) { flushAll(); return; }

    if (line.startsWith("- ")) { flushQuote(); bullets.push(line.slice(2)); return; }
    if (line.startsWith("> ")) { flushBullets(); quote.push(line.slice(2)); return; }
    flushAll();

    if (line.startsWith("### ")) {
      blocks.push(<h4 key={i} className="pt-2 font-display text-sm text-slate-200">{line.slice(4)}</h4>);
    } else if (line.startsWith("## ")) {
      blocks.push(
        <h3 key={i} className="border-b border-slate-800 pb-1 pt-3 font-display text-base text-slate-100">
          {line.slice(3)}
        </h3>
      );
    } else if (line.startsWith("# ")) {
      blocks.push(<h2 key={i} className="font-display text-lg text-slate-100">{line.slice(2)}</h2>);
    } else if (line.startsWith("---")) {
      blocks.push(<hr key={i} className="border-slate-800" />);
    } else {
      blocks.push(
        <p key={i} className="text-sm leading-relaxed text-slate-300">{inline(line, `p${i}`)}</p>
      );
    }
  });
  flushAll();

  return <div className="space-y-2">{blocks}</div>;
}
