// Secretary tab — Phase 1: private chat. Phase 2: Google Calendar with
// deterministic Bahá'í tags, verified Feast/Holy Day dates, reminders, and the
// per-event approval gate for calendars she doesn't own.
// Privacy hard rule: conversation content renders only inside this tab; the
// Activity Log receives event/reminder TITLES only (hard rule 8).

import { useCallback, useEffect, useRef, useState } from "react";
import {
  Send, StickyNote, ListChecks, CalendarDays, BellRing, ShieldQuestion, ExternalLink,
  MessageCircle, UserPlus, Trash2, Phone,
} from "lucide-react";
import { api, API_ORIGIN } from "../lib/api";
import type {
  Contact, PendingApproval, SecretaryMessage, SecretaryStatus, SecretaryUpcoming, WhatsAppStatus,
} from "../lib/types";
import { Card, CardContent, CardHeader, CardTitle, Button, BadgePill, ErrorNote } from "./ui";
import { cn } from "../lib/utils";
import { PersonalityModal } from "./PersonalityModal";
import { NotesModal } from "./NotesModal";
import { TasksModal } from "./TasksModal";
import { RemindersModal } from "./RemindersModal";

const TAG_STYLES: Record<string, string> = {
  holy_day: "border-amber-400/50 bg-amber-400/10 text-amber-300",
  feast: "border-amber-400/40 bg-amber-400/10 text-amber-200",
  core_activity: "border-emerald-500/40 bg-emerald-500/10 text-emerald-300",
  institutional: "border-sky-500/40 bg-sky-500/10 text-sky-300",
  professional: "border-violet-500/40 bg-violet-500/10 text-violet-300",
  personal: "border-slate-600 bg-slate-800/60 text-slate-400",
};

const TAG_LABELS: Record<string, string> = {
  holy_day: "Holy Day",
  feast: "Feast",
  core_activity: "Core activity",
  institutional: "Institutional",
  professional: "Professional",
  personal: "Personal",
};

function Tag({ tag }: { tag: string }) {
  return (
    <BadgePill className={cn("border text-[10px]", TAG_STYLES[tag] ?? TAG_STYLES.personal)}>
      {TAG_LABELS[tag] ?? tag}
    </BadgePill>
  );
}

export function SecretaryPanel() {
  const [status, setStatus] = useState<SecretaryStatus | null>(null);
  const [messages, setMessages] = useState<SecretaryMessage[]>([]);
  const [upcoming, setUpcoming] = useState<SecretaryUpcoming | null>(null);
  const [approvals, setApprovals] = useState<PendingApproval[]>([]);
  const [waStatus, setWaStatus] = useState<WhatsAppStatus | null>(null);
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [newContactName, setNewContactName] = useState("");
  const [newContactPhone, setNewContactPhone] = useState("");
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [personalityOpen, setPersonalityOpen] = useState(false);
  const [notesOpen, setNotesOpen] = useState(false);
  const [tasksOpen, setTasksOpen] = useState(false);
  const [remindersOpen, setRemindersOpen] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const lastNotifId = useRef(0);
  const firstNotifPoll = useRef(true);

  const refreshSide = useCallback(() => {
    api.getSecretaryStatus().then(setStatus).catch(() => setStatus(null));
    api.getSecretaryUpcoming(14).then(setUpcoming).catch(() => setUpcoming(null));
    api.getSecretaryApprovals().then((r) => setApprovals(r.pending)).catch(() => {});
    api.getWhatsAppStatus().then(setWaStatus).catch(() => setWaStatus(null));
    api.getContacts().then((r) => setContacts(r.contacts)).catch(() => {});
  }, []);

  const refreshChat = useCallback(() => {
    api.getSecretaryHistory(100)
      .then((r) => setMessages(r.messages))
      .catch(() => setError("Could not load the conversation. Is the backend running?"));
  }, []);

  useEffect(() => {
    refreshChat();
    refreshSide();
    // Scheduler notifications -> Activity Log; new reminders also appear in
    // the chat (the scheduler writes an assistant message), so refresh it.
    const poll = async () => {
      try {
        const { notifications, lastId } = await api.pollSecretaryNotifications(lastNotifId.current);
        // First poll returns history we've already seen — record the cursor only.
        if (!firstNotifPoll.current && notifications.length > 0) {
          refreshChat();
          refreshSide();
        }
        firstNotifPoll.current = false;
        lastNotifId.current = lastId;
      } catch {
        /* backend down — the next poll retries */
      }
    };
    poll();
    const timer = window.setInterval(poll, 20_000);
    return () => window.clearInterval(timer);
  }, [refreshChat, refreshSide]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, sending]);

  const send = async () => {
    const text = draft.trim();
    if (!text || sending) return;
    setDraft("");
    setError(null);
    setSending(true);
    const now = new Date().toLocaleTimeString();
    setMessages((m) => [...m, { role: "user", content: text, channel: "dashboard", ts: now }]);
    try {
      const res = await api.secretaryChat(text);
      setMessages((m) => [
        ...m,
        { role: "assistant", content: res.reply, channel: "dashboard", ts: new Date().toLocaleTimeString() },
      ]);
      if (res.remembered.length || res.tasks_added.length || res.actions.length) refreshSide();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Abigail didn't answer. Try again.");
    } finally {
      setSending(false);
    }
  };

  const resolveApproval = async (id: number, approve: boolean) => {
    try {
      await api.resolveSecretaryApproval(id, approve);
      refreshSide();
      refreshChat();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not resolve the approval.");
    }
  };

  const addContact = async () => {
    const name = newContactName.trim();
    const phone = newContactPhone.trim();
    if (!name || !phone) return;
    try {
      await api.addContact(name, phone, false);
      setNewContactName("");
      setNewContactPhone("");
      refreshSide();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not add that contact.");
    }
  };

  const toggleAllowlist = async (contact: Contact) => {
    try {
      await api.setContactAllowlisted(contact.id, !contact.allowlisted);
      refreshSide();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not update that contact.");
    }
  };

  const deleteContact = async (id: number) => {
    try {
      await api.removeContact(id);
      refreshSide();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not remove that contact.");
    }
  };

  return (
    <div className="flex h-full flex-col gap-4">
      <div className="flex items-center justify-between">
        <button
          onClick={() => setPersonalityOpen(true)}
          className="flex items-center gap-3 rounded-lg -m-1 p-1 text-left transition-colors hover:bg-slate-800/40"
          title="Edit her personality & instructions"
        >
          <img src="/abigail.jpg" alt="" className="h-10 w-10 rounded-full object-cover" />
          <div>
            <h1 className="font-display text-xl text-slate-100">Abigail</h1>
            <p className="text-sm text-slate-400">
              Your personal assistant. Everything here stays private, on this computer.
            </p>
          </div>
        </button>
        {status && (
          <div className="flex items-center gap-2">
            <BadgePill className={cn(
              "border",
              status.enabled
                ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-300"
                : "border-rose-500/40 bg-rose-500/10 text-rose-300"
            )}>
              {status.enabled ? "Connected" : "No API key"}
            </BadgePill>
            <button
              onClick={() => setNotesOpen(true)}
              className="inline-flex items-center rounded-full border border-slate-700 bg-slate-800/60 px-2.5 py-0.5 text-xs font-semibold tracking-wide text-slate-300 transition-colors hover:border-amber-400/50 hover:text-amber-300"
              title="View and edit her notes"
            >
              <StickyNote className="mr-1 inline h-3 w-3" />
              {status.notes} notes
            </button>
            <button
              onClick={() => setTasksOpen(true)}
              className="inline-flex items-center rounded-full border border-slate-700 bg-slate-800/60 px-2.5 py-0.5 text-xs font-semibold tracking-wide text-slate-300 transition-colors hover:border-amber-400/50 hover:text-amber-300"
              title="View and edit tasks"
            >
              <ListChecks className="mr-1 inline h-3 w-3" />
              {status.open_tasks} tasks
            </button>
            <button
              onClick={() => setRemindersOpen(true)}
              className="inline-flex items-center rounded-full border border-slate-700 bg-slate-800/60 px-2.5 py-0.5 text-xs font-semibold tracking-wide text-slate-300 transition-colors hover:border-amber-400/50 hover:text-amber-300"
              title="View and edit reminders"
            >
              <BellRing className="mr-1 inline h-3 w-3" />
              {status.pending_reminders} reminders
            </button>
          </div>
        )}
      </div>

      <PersonalityModal open={personalityOpen} onClose={() => setPersonalityOpen(false)} />
      <NotesModal
        open={notesOpen}
        onClose={() => {
          setNotesOpen(false);
          refreshSide();
        }}
      />
      <TasksModal
        open={tasksOpen}
        onClose={() => {
          setTasksOpen(false);
          refreshSide();
        }}
      />
      <RemindersModal
        open={remindersOpen}
        onClose={() => {
          setRemindersOpen(false);
          refreshSide();
        }}
      />

      <div className="flex min-h-0 flex-1 gap-4">
        {/* ── Chat ── */}
        <Card className="flex min-h-0 flex-[3] flex-col">
          <CardHeader>
            <CardTitle>Chat</CardTitle>
          </CardHeader>
          <CardContent className="flex min-h-0 flex-1 flex-col gap-3">
            <div className="flex-1 space-y-3 overflow-y-auto pr-1">
              {messages.length === 0 && !sending && (
                <p className="pt-8 text-center text-sm text-slate-500">
                  Say hello — ask about your week, set a reminder, or tell her something to remember.
                </p>
              )}
              {messages.map((m, i) => (
                <div key={i} className={cn("flex items-end gap-2", m.role === "user" ? "justify-end" : "justify-start")}>
                  {m.role !== "user" && (
                    <img src="/abigail.jpg" alt="" className="h-6 w-6 shrink-0 rounded-full object-cover" />
                  )}
                  <div className={cn(
                    "max-w-[80%] whitespace-pre-wrap rounded-2xl px-4 py-2.5 text-sm leading-relaxed",
                    m.role === "user"
                      ? "rounded-br-sm bg-amber-400/15 text-amber-100"
                      : "rounded-bl-sm bg-slate-800/80 text-slate-200"
                  )}>
                    {m.content}
                    <div className="mt-1 flex items-center gap-1 text-[10px] text-slate-500">
                      {m.channel === "whatsapp" && (
                        <Phone className="h-2.5 w-2.5" aria-label="Sent via WhatsApp" />
                      )}
                      {m.ts}
                    </div>
                  </div>
                </div>
              ))}
              {sending && (
                <div className="flex items-end justify-start gap-2">
                  <img src="/abigail.jpg" alt="" className="h-6 w-6 shrink-0 rounded-full object-cover" />
                  <div className="rounded-2xl rounded-bl-sm bg-slate-800/80 px-4 py-2.5 text-sm text-slate-400">
                    typing…
                  </div>
                </div>
              )}
              <div ref={bottomRef} />
            </div>

            {error && <ErrorNote>{error}</ErrorNote>}

            <div className="flex gap-2">
              <textarea
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    send();
                  }
                }}
                rows={2}
                placeholder="Message Abigail… (Enter to send)"
                className="flex-1 resize-none rounded-lg border border-slate-700 bg-slate-900/80 px-3 py-2 text-sm text-slate-200 placeholder:text-slate-600 focus:border-amber-400/50 focus:outline-none"
              />
              <Button onClick={send} disabled={sending || !draft.trim()}>
                <Send className="h-4 w-4" />
              </Button>
            </div>
          </CardContent>
        </Card>

        {/* ── Side column ── */}
        <div className="flex min-h-0 flex-[2] flex-col gap-4 overflow-y-auto pr-1">
          {/* Approvals — only when something is waiting */}
          {approvals.length > 0 && (
            <Card className="border-amber-400/30">
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <ShieldQuestion className="h-4 w-4 text-amber-300" />
                  Needs your approval
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                <p className="text-xs text-slate-500">
                  These touch calendars Abigail doesn't own, so nothing happens until you say so.
                </p>
                {approvals.map((a) => (
                  <div key={a.id} className="rounded-lg border border-slate-700 bg-slate-900/60 p-3">
                    <div className="text-sm text-slate-200">{a.description}</div>
                    <div className="mt-2 flex gap-2">
                      <Button onClick={() => resolveApproval(a.id, true)}>Approve</Button>
                      <Button variant="ghost" onClick={() => resolveApproval(a.id, false)}>Reject</Button>
                    </div>
                  </div>
                ))}
              </CardContent>
            </Card>
          )}

          {/* Google Workspace connection */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <CalendarDays className="h-4 w-4" />
                Google Workspace
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 text-sm">
              {status?.google_authorised ? (
                <>
                  <p className="text-emerald-300">
                    Connected — she reads your calendars and writes to her own
                    ("bahAI Secretary"), searches and reads Gmail/Drive/Docs/Sheets/Slides,
                    and can send email, create Docs/Sheets, and organize Drive files
                    (anything outside her own "bahAI Secretary" folder needs your approval).
                  </p>
                  <p className="text-xs text-slate-500">
                    Seeing permission errors on Gmail/Drive/Docs/Sheets? This connection was
                    made before those were added, so it may only cover Calendar — reconnect
                    below to grant the rest (safe to do any time; it won't lose anything).
                  </p>
                  <a
                    href={`${API_ORIGIN}/google/oauth/start`}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-1 rounded-lg border border-slate-700 px-3 py-1.5 text-xs font-medium text-slate-300 hover:border-amber-400/50 hover:text-amber-300"
                  >
                    Reconnect Google Workspace <ExternalLink className="h-3.5 w-3.5" />
                  </a>
                </>
              ) : (
                <>
                  <p className="text-slate-400">
                    Not connected yet. Connecting lets her see your schedule, remind
                    you before events, add events you ask for, and search/read/act on
                    Gmail, Drive, Docs, Sheets, and Slides.
                  </p>
                  <a
                    href={`${API_ORIGIN}/google/oauth/start`}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-1 rounded-lg bg-amber-400/15 px-3 py-1.5 font-medium text-amber-300 hover:bg-amber-400/25"
                  >
                    Connect Google Workspace <ExternalLink className="h-3.5 w-3.5" />
                  </a>
                  {!status?.google_configured && (
                    <p className="text-xs text-slate-500">
                      (The link walks you through the one-time Google setup step by step.)
                    </p>
                  )}
                </>
              )}
            </CardContent>
          </Card>

          {/* WhatsApp connection */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <MessageCircle className="h-4 w-4" />
                WhatsApp
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 text-sm">
              {waStatus?.configured ? (
                <p className="text-emerald-300">
                  Connected — message her on WhatsApp like a person. Reminders also
                  arrive there alongside the dashboard.
                </p>
              ) : (
                <>
                  <p className="text-slate-400">
                    Not connected yet. This involves a few external steps (a free Meta
                    developer account, a test phone number, and a tunnel so WhatsApp
                    can reach this computer) — the guide walks through each one.
                  </p>
                  <a
                    href={`${API_ORIGIN}/whatsapp/setup`}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-1 rounded-lg bg-amber-400/15 px-3 py-1.5 font-medium text-amber-300 hover:bg-amber-400/25"
                  >
                    Set up WhatsApp <ExternalLink className="h-3.5 w-3.5" />
                  </a>
                </>
              )}
            </CardContent>
          </Card>

          {/* Trusted contacts — the allowlist for messages she sends to people
              other than Sheraj; owner-controlled here only, never something
              she can grant herself in chat. */}
          <Card>
            <CardHeader>
              <CardTitle>Trusted contacts</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3 text-sm">
              <p className="text-xs text-slate-500">
                She can message these people on WhatsApp directly. Anyone else needs
                your approval first, every time.
              </p>
              {contacts.length > 0 && (
                <ul className="space-y-1.5">
                  {contacts.map((c) => (
                    <li key={c.id} className="flex items-center justify-between gap-2 rounded-lg border border-slate-800 bg-slate-900/40 px-2.5 py-1.5">
                      <div className="min-w-0">
                        <div className="truncate text-slate-200">{c.name}</div>
                        <div className="truncate text-xs text-slate-500">{c.phone}</div>
                      </div>
                      <div className="flex shrink-0 items-center gap-2">
                        <button
                          onClick={() => toggleAllowlist(c)}
                          className={cn(
                            "rounded-full border px-2 py-0.5 text-[10px] font-medium",
                            c.allowlisted
                              ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-300"
                              : "border-slate-700 bg-slate-800/60 text-slate-400"
                          )}
                        >
                          {c.allowlisted ? "Trusted" : "Not trusted"}
                        </button>
                        <button
                          onClick={() => deleteContact(c.id)}
                          className="text-slate-500 hover:text-rose-400"
                          title="Remove contact"
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
              <div className="flex gap-1.5">
                <input
                  value={newContactName}
                  onChange={(e) => setNewContactName(e.target.value)}
                  placeholder="Name"
                  className="w-1/3 rounded-lg border border-slate-700 bg-slate-900/80 px-2 py-1.5 text-xs text-slate-200 placeholder:text-slate-600 focus:border-amber-400/50 focus:outline-none"
                />
                <input
                  value={newContactPhone}
                  onChange={(e) => setNewContactPhone(e.target.value)}
                  placeholder="+15551234567"
                  className="flex-1 rounded-lg border border-slate-700 bg-slate-900/80 px-2 py-1.5 text-xs text-slate-200 placeholder:text-slate-600 focus:border-amber-400/50 focus:outline-none"
                />
                <Button onClick={addContact} disabled={!newContactName.trim() || !newContactPhone.trim()}>
                  <UserPlus className="h-3.5 w-3.5" />
                </Button>
              </div>
            </CardContent>
          </Card>

          {/* Today & upcoming */}
          <Card>
            <CardHeader>
              <CardTitle>Today &amp; upcoming</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4 text-sm">
              {upcoming?.badi_events && upcoming.badi_events.length > 0 && (
                <div>
                  <div className="mb-1 text-xs uppercase tracking-wide text-slate-500">
                    Bahá'í dates (verified table)
                  </div>
                  <ul className="space-y-1">
                    {upcoming.badi_events.map((b, i) => (
                      <li key={i} className="flex items-center justify-between gap-2">
                        <span className="text-slate-300">{b.name}</span>
                        <span className="shrink-0 text-xs text-slate-500">{b.date}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              <div>
                <div className="mb-1 text-xs uppercase tracking-wide text-slate-500">
                  Calendar (next 14 days)
                </div>
                {!status?.google_authorised ? (
                  <p className="text-xs text-slate-500">Connect Google Workspace to see events here.</p>
                ) : upcoming && upcoming.events.length > 0 ? (
                  <ul className="space-y-2">
                    {upcoming.events.slice(0, 12).map((ev) => (
                      <li key={`${ev.calendar_id}-${ev.id}`} className="rounded-lg border border-slate-800 bg-slate-900/40 p-2">
                        <div className="flex items-center justify-between gap-2">
                          <span className="truncate text-slate-200">{ev.summary}</span>
                          <span className="shrink-0 text-xs text-slate-500">
                            {ev.all_day ? ev.start : ev.start.replace("T", " ").slice(5, 16)}
                          </span>
                        </div>
                        <div className="mt-1 flex flex-wrap items-center gap-1">
                          {ev.tags.map((t) => <Tag key={t} tag={t} />)}
                          {ev.editable_by_secretary && (
                            <span className="text-[10px] text-slate-500">· hers</span>
                          )}
                          {ev.location && (
                            <span className="truncate text-[10px] text-slate-500">· {ev.location}</span>
                          )}
                        </div>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-xs text-slate-500">Nothing scheduled in the next two weeks.</p>
                )}
              </div>

              {upcoming?.reminders && upcoming.reminders.length > 0 && (
                <div>
                  <div className="mb-1 text-xs uppercase tracking-wide text-slate-500">Reminders</div>
                  <ul className="space-y-1">
                    {upcoming.reminders.map((r) => (
                      <li key={r.id} className="flex items-center justify-between gap-2">
                        <span className="truncate text-slate-300">{r.message}</span>
                        <span className="shrink-0 text-xs text-slate-500">
                          {r.fire_at.slice(0, 16)}
                          {r.recurrence ? ` · ${r.recurrence}` : ""}
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
