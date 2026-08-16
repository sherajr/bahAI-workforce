import { useEffect, useRef, useState } from "react";
import { Send, Trash2 } from "lucide-react";
import { api } from "../../lib/api";
import type { ColonyAgentMessage } from "../../lib/types";
import { agentLabel, cn, rosterFor } from "../../lib/utils";
import { Button, ErrorNote, RosterAvatar } from "../ui";

/**
 * One-to-one chat with a workforce agent.
 *
 * These agents answer on THEIR OWN models — Grok for Theo and Amos (paid,
 * metered), the free local model for everyone else. That is hard rule 16:
 * Claude belongs to Abigail alone, which is also why she isn't chatted with
 * here at all. The cost note under the composer says which is which, because
 * "is this message going to cost me?" is a fair question to be able to answer
 * before pressing send.
 */

const PAID_AGENTS = new Set(["artist", "reviewer"]);

export function AgentChat({
  agent, initialMessages, onActed,
}: {
  agent: string;
  initialMessages: ColonyAgentMessage[];
  onActed: () => void;
}) {
  const [messages, setMessages] = useState<ColonyAgentMessage[]>(initialMessages);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => setMessages(initialMessages), [initialMessages]);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); },
    [messages.length, sending]);

  const send = async () => {
    const text = draft.trim();
    if (!text || sending) return;
    setError(null);
    setSending(true);
    // Show his own message immediately; the reply lands when it lands.
    setMessages((m) => [...m, {
      id: -Date.now(), agent, role: "user", content: text, ts: new Date().toISOString(),
    }]);
    setDraft("");
    try {
      const res = await api.colonyChat(agent, text);
      setMessages((m) => [...m, {
        id: -Date.now() - 1, agent, role: "assistant", content: res.reply,
        ts: new Date().toISOString(),
      }]);
      // A queued action is a new row in the approval queue — refresh so it
      // appears without waiting for the next poll.
      if (res.queued.length > 0) onActed();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not reach that agent.");
    } finally {
      setSending(false);
    }
  };

  const clear = async () => {
    await api.clearColonyChat(agent);
    setMessages([]);
  };

  const roster = rosterFor(agent);
  const paid = PAID_AGENTS.has(agent);

  return (
    <div className="flex h-full flex-col">
      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto pr-1">
        {messages.length === 0 && (
          <p className="py-6 text-center text-sm text-slate-500">
            Ask {agentLabel(agent)} about their work, or ask them to do some of it.
          </p>
        )}
        {messages.map((m) => (
          <div key={m.id} className={cn("flex gap-2.5",
            m.role === "user" ? "justify-end" : "justify-start")}>
            {m.role === "assistant" && (
              <RosterAvatar src={roster?.avatar} name={agentLabel(agent)}
                            className="mt-0.5 h-7 w-7 shrink-0" />
            )}
            <div className={cn(
              "max-w-[85%] whitespace-pre-wrap rounded-xl px-3 py-2 text-sm leading-relaxed",
              m.role === "user"
                ? "bg-amber-400/10 text-amber-100"
                : "bg-slate-900 text-slate-200",
            )}>
              {m.content}
            </div>
          </div>
        ))}
        {sending && (
          <div className="flex gap-2.5">
            <RosterAvatar src={roster?.avatar} name={agentLabel(agent)}
                          className="mt-0.5 h-7 w-7 shrink-0" />
            <div className="rounded-xl bg-slate-900 px-3 py-2 text-sm text-slate-500">
              {agentLabel(agent)} is thinking…
            </div>
          </div>
        )}
        <div ref={endRef} />
      </div>

      {error && <div className="mt-2"><ErrorNote>{error}</ErrorNote></div>}

      <div className="mt-3 space-y-2">
        <div className="flex gap-2">
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
            }}
            rows={2}
            placeholder={`Message ${agentLabel(agent)}…`}
            className="flex-1 resize-none rounded-lg border border-slate-700 bg-slate-900/80
                       px-3 py-2 text-sm text-slate-200 placeholder:text-slate-600
                       focus:border-amber-400/50 focus:outline-none"
          />
          <div className="flex flex-col gap-2">
            <Button onClick={send} loading={sending} disabled={sending || !draft.trim()}>
              <Send className="h-4 w-4" />
            </Button>
            {messages.length > 0 && (
              <button
                onClick={clear}
                title="Clear this conversation"
                className="rounded-lg border border-slate-700 p-2 text-slate-500
                           hover:border-slate-600 hover:text-slate-300"
              >
                <Trash2 className="h-4 w-4" />
              </button>
            )}
          </div>
        </div>
        <p className="text-[11px] leading-relaxed text-slate-500">
          {paid
            ? `${agentLabel(agent)} runs on the paid Grok API, so each message costs a little — it shows in the Steward's report.`
            : `${agentLabel(agent)} runs on the free local model.`}
          {" "}Anything that spends money or changes a saved product waits for your approval.
        </p>
      </div>
    </div>
  );
}
