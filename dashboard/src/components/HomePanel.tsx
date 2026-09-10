import { useQuery } from "@tanstack/react-query";
import {
  AlertTriangle, ArrowRight, CalendarClock, Images, MessageCircleHeart, Play,
  Sparkles, Users,
} from "lucide-react";
import { api } from "../lib/api";
import { homeOk } from "../lib/types";
import type { HomeEntry, HomeNextAction, HomeReflection } from "../lib/types";
import type { Tab } from "./Nav";
import { Button, ErrorNote } from "./ui";

/**
 * Home: what is happening, and what needs you (rule 120).
 *
 * The dashboard used to open on the Pipeline form — a theme box and a target
 * score — which answers "make me a bookmark" and not "what was I doing?".
 * Sheraj is non-technical and the deliverable is dashboard-visible behaviour
 * (AGENTS.md), so this page is written in ordinary language: no pipeline, no
 * provider, no agent graph, no trust score, and every row is a link to the
 * place the thing actually lives.
 *
 * It is deliberately CHEAP. One request, no panels behind it, nothing paid, no
 * microphone, no model. Opening the app must not start anything.
 */

function Section({ title, blurb, children }: {
  title: string; blurb?: string; children: React.ReactNode;
}) {
  return (
    <section className="space-y-2" aria-label={title}>
      <div>
        <h2 className="text-sm font-semibold uppercase tracking-widest text-slate-400">
          {title}
        </h2>
        {blurb && <p className="text-xs text-slate-500">{blurb}</p>}
      </div>
      {children}
    </section>
  );
}

function Row({ title, detail, badge, onGo, tone = "plain" }: {
  title: string; detail?: string; badge?: string; onGo?: () => void;
  tone?: "plain" | "attention";
}) {
  const border = tone === "attention"
    ? "border-amber-400/40 hover:border-amber-400/70"
    : "border-slate-800 hover:border-slate-700";
  return (
    <button
      type="button"
      onClick={onGo}
      disabled={!onGo}
      className={`flex w-full items-center gap-3 rounded-lg border ${border} bg-slate-900/60 px-4 py-3 text-left transition-colors disabled:cursor-default`}
    >
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm text-slate-100">{title}</div>
        {detail && <div className="mt-0.5 text-xs text-slate-400">{detail}</div>}
      </div>
      {badge && (
        <span className="shrink-0 rounded border border-slate-700 px-2 py-0.5 text-[10px] uppercase tracking-wider text-slate-400">
          {badge}
        </span>
      )}
      {onGo && <ArrowRight className="h-4 w-4 shrink-0 text-slate-500" aria-hidden />}
    </button>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900/30 px-4 py-3 text-xs text-slate-500">
      {children}
    </div>
  );
}

/** A section the backend could not read says so, and the page carries on. */
function SectionError({ what }: { what: string }) {
  return (
    <Empty>
      {what} could not be read just now. Everything else on this page is current.
    </Empty>
  );
}

function acceptanceNote(a: HomeNextAction): string {
  const parts: string[] = [];
  if (a.owner) parts.push(a.owner);
  // Tri-state, and shown as such. "Nobody has answered" is a different and more
  // honest fact than "declined" (rule 101).
  parts.push(a.owner_accepted === true ? "accepted"
    : a.owner_accepted === false ? "did not accept"
      : "not asked yet");
  if (a.due) parts.push(`due ${a.due}`);
  return parts.join(" · ");
}

export function HomePanel({ onNavigate }: { onNavigate?: (tab: Tab) => void } = {}) {
  const home = useQuery({
    queryKey: ["home"],
    queryFn: api.getHome,
    // Slow on purpose. Home is a summary, not a monitor: a fast timer here
    // would make simply having the app open more expensive (rule 120).
    refetchInterval: 60_000,
  });

  const go = (tab: string) => () => onNavigate?.(tab as Tab);

  const cont = home.data ? homeOk<HomeEntry[]>(home.data.continue) : null;
  const running = home.data ? homeOk<HomeEntry[]>(home.data.running) : null;
  const decisions = home.data
    ? homeOk<{ items: HomeEntry[]; total: number }>(home.data.decisions) : null;
  const next = home.data ? homeOk<{
    accepted: HomeNextAction[]; accepted_total: number;
    blocked: HomeNextAction[]; blocked_total: number;
    reflections: HomeReflection[];
  }>(home.data.next_actions) : null;
  const made = home.data
    ? homeOk<{ products: number; quote_cards: number; bookmarks: number }>(home.data.made)
    : null;

  return (
    <div className="mx-auto max-w-3xl space-y-7">
      <header className="space-y-1">
        <h1 className="font-display text-2xl text-slate-100">Where things stand</h1>
        <p className="text-sm text-slate-400">
          What is under way, what is waiting for you, and what to do next.
        </p>
      </header>

      {home.isError && (
        <ErrorNote>
          Could not read this page: {(home.error as Error).message}. Is the API running
          on port 8765?
        </ErrorNote>
      )}
      {home.isLoading && <Empty>Reading…</Empty>}

      <Section title="Continue"
               blurb="Work that is already started.">
        {!cont && home.data && <SectionError what="Work in progress" />}
        {running && running.length > 0 && running.map((r) => (
          <Row key={r.id} title={r.title} detail={r.detail}
               badge={r.started_by ? `running · ${r.started_by}` : "running"}
               onGo={go(r.tab)} tone="attention" />
        ))}
        {cont && cont.map((c) => (
          <Row key={`${c.kind}-${c.id}`} title={c.title} detail={c.detail}
               badge={c.when || c.stage} onGo={go(c.tab)} />
        ))}
        {cont && cont.length === 0 && (!running || running.length === 0) && (
          <Empty>Nothing is part-finished. Start something below.</Empty>
        )}
      </Section>

      <Section title="Needs your decision"
               blurb="Nothing here has happened yet. It is waiting for you.">
        {!decisions && home.data && <SectionError what="The approval queues" />}
        {decisions && decisions.items.map((d) => (
          <Row key={`${d.kind}-${d.id}`} title={d.title} detail={d.detail}
               onGo={go(d.tab)} tone="attention" />
        ))}
        {decisions && decisions.items.length === 0 && (
          <Empty>Nothing is waiting on you.</Empty>
        )}
        {decisions && decisions.total > decisions.items.length && (
          <div className="text-xs text-slate-500">
            …and {decisions.total - decisions.items.length} more.
          </div>
        )}
      </Section>

      <Section title="Next actions"
               blurb="What people took on, and what is stuck.">
        {!next && home.data && <SectionError what="Commitments" />}
        {next?.blocked.map((a) => (
          <Row key={`b-${a.id}`}
               title={a.action}
               detail={`Blocked — ${a.blocker || "no reason recorded"}`}
               badge="blocked" onGo={go(a.tab)} tone="attention" />
        ))}
        {next?.accepted.map((a) => (
          <Row key={`a-${a.id}`} title={a.action} detail={acceptanceNote(a)}
               onGo={go(a.tab)} />
        ))}
        {next?.reflections.map((r) => (
          <Row key={`r-${r.id}`}
               title={`Reflect on “${r.title}”`}
               detail={r.overdue ? `Was due ${r.when}` : `Due ${r.when}`}
               badge={r.overdue ? "overdue" : "coming up"}
               onGo={go(r.tab)} tone={r.overdue ? "attention" : "plain"} />
        ))}
        {next && !next.blocked.length && !next.accepted.length
          && !next.reflections.length && (
          <Empty>
            Nothing is recorded as accepted yet. A commitment appears here once somebody
            has actually agreed to it — a name mentioned in a meeting is not a promise.
          </Empty>
        )}
      </Section>

      <Section title="Create">
        <div className="grid gap-2 sm:grid-cols-2">
          <Button variant="secondary" onClick={go("gatherings")}>
            <Users className="h-4 w-4" /> Prepare a gathering
          </Button>
          <Button variant="secondary" onClick={go("consultation")}>
            <Play className="h-4 w-4" /> Start a consultation
          </Button>
          <Button variant="secondary" onClick={go("pipeline")}>
            <Sparkles className="h-4 w-4" /> Create cards
          </Button>
          <Button variant="secondary" onClick={go("secretary")}>
            <MessageCircleHeart className="h-4 w-4" /> Talk to Abigail
          </Button>
        </div>
      </Section>

      {made && (
        <Section title="What has been made"
                 blurb="Deeds, counted. Never a score of anybody.">
          <button type="button" onClick={go("products")}
                  className="flex w-full items-center gap-4 rounded-lg border border-slate-800 bg-slate-900/60 px-4 py-3 text-left hover:border-slate-700">
            <Images className="h-5 w-5 text-slate-500" aria-hidden />
            <div className="text-sm text-slate-300">
              <span className="font-medium text-slate-100">{made.products}</span> pieces —{" "}
              {made.quote_cards} quote cards, {made.bookmarks} bookmarks
            </div>
            <ArrowRight className="ml-auto h-4 w-4 text-slate-500" aria-hidden />
          </button>
        </Section>
      )}

      <footer className="flex items-center gap-2 pt-2 text-[11px] text-slate-600">
        <CalendarClock className="h-3 w-3" aria-hidden />
        Read at {home.data?.generated_at ?? "—"}
        {home.isError && <AlertTriangle className="h-3 w-3 text-amber-400" aria-hidden />}
      </footer>
    </div>
  );
}
