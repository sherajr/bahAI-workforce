import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Check, Copy, ExternalLink, Loader2 } from "lucide-react";
import { api } from "../../lib/api";
import type { NucleiSnapshot } from "../../lib/types";
import { agentLabel, cn, rosterFor } from "../../lib/utils";
import { ErrorNote, RosterAvatar } from "../ui";

interface Props {
  snapshot: NucleiSnapshot;
  onClose: () => void;
  onChanged: () => void;
  onSelectActor: (id: number) => void;
  onOpenDigital: () => void;
}

type Section = "who" | "message";

export function WorkforceDrawer({
  snapshot, onClose, onChanged, onSelectActor, onOpenDigital,
}: Props) {
  const qc = useQueryClient();
  const [section, setSection] = useState<Section>("who");

  const picture = useQuery({
    queryKey: ["nuclei-workforce"],
    queryFn: api.getWorkforcePicture,
    // Matches the Colony map's own cadence while something is running.
    refetchInterval: (query) =>
      query.state.data?.running_jobs.length ? 4_000 : 20_000,
  });
  const wf = picture.data;

  const [newName, setNewName] = useState("");
  const [newRole, setNewRole] = useState("");
  const [existingId, setExistingId] = useState("");

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["nuclei"] });
    qc.invalidateQueries({ queryKey: ["nuclei-workforce"] });
    qc.invalidateQueries({ queryKey: ["colony"] });
    onChanged();
  };

  const addPerson = useMutation({
    mutationFn: (body: { display_name?: string; actor_id?: number; role?: string }) =>
      api.addWorkforcePerson(body),
    onSuccess: () => {
      setNewName("");
      setNewRole("");
      setExistingId("");
      invalidate();
    },
  });
  const removePerson = useMutation({
    mutationFn: (membershipId: number) => api.removeWorkforcePerson(membershipId),
    onSuccess: invalidate,
  });

  // People already on the map who are not on the workforce yet.
  const people = wf?.people ?? [];
  const onWorkforce = new Set(people.map((p) => p.actor_id));
  const candidates = (snapshot.actors ?? [])
    .filter((a) => a.kind === "person" && !onWorkforce.has(a.id))
    .filter((a) => a.id !== snapshot.owner_actor_id);

  return (
    <aside className="flex w-96 shrink-0 flex-col overflow-y-auto rounded-xl border
                      border-amber-500/25 bg-slate-950/70 p-4">
      <button type="button" onClick={onClose} className="float-right text-xs text-slate-500">
        Close
      </button>
      <h2 className="font-display text-lg text-amber-100">Bahá'í Workforce</h2>
      <p className="mb-3 text-xs text-slate-500">
        The agents who do the work, and the people who work alongside them.
      </p>

      <div className="mb-4 flex gap-1 rounded-lg border border-slate-800 bg-slate-950/60 p-1">
        {([["who", "Who works here"], ["message", "Send a message"]] as [Section, string][])
          .map(([id, label]) => (
            <button
              key={id}
              type="button"
              onClick={() => setSection(id)}
              className={cn(
                "flex-1 rounded-md px-2 py-1.5 text-xs font-medium",
                section === id ? "bg-amber-400/10 text-amber-200"
                  : "text-slate-400 hover:text-slate-200",
              )}
            >
              {label}
            </button>
          ))}
      </div>

      {picture.isError && (
        <ErrorNote>Could not read the workforce: {(picture.error as Error).message}</ErrorNote>
      )}
      {!wf && !picture.isError && <p className="text-sm text-slate-500">Loading…</p>}

      {wf && section === "who" && (
        <div className="space-y-5">
          {wf.running_jobs.length > 0 && (
            <section className="rounded-lg border border-amber-500/30 bg-amber-400/5 p-3">
              <h3 className="mb-1.5 flex items-center gap-1.5 text-xs font-semibold text-amber-200">
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                Working right now
              </h3>
              {wf.running_jobs.map((j) => (
                <div key={j.job_id} className="text-xs text-slate-300">
                  <span className="text-slate-500">{j.team_name ?? j.kind}</span>
                  {" · "}
                  {j.progress || "starting…"}
                  {j.started_by && j.started_by !== "sheraj" && (
                    <span className="text-slate-500"> (started by {j.started_by})</span>
                  )}
                </div>
              ))}
            </section>
          )}

          <section>
            <div className="mb-2 flex items-center justify-between">
              <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-400">
                The agents
              </h3>
              <button
                type="button"
                onClick={onOpenDigital}
                className="text-[11px] text-amber-300/80 hover:text-amber-200"
              >
                Open the Digital World →
              </button>
            </div>
            <div className="space-y-1.5">
              {wf.agents.map((a) => {
                const roster = rosterFor(a.name);
                return (
                  <div
                    key={a.name}
                    className="flex items-center gap-2.5 rounded-lg border border-slate-800
                               bg-slate-950/50 px-2.5 py-2"
                  >
                    <RosterAvatar src={roster?.avatar} name={agentLabel(a.name)}
                                  className="h-7 w-7" />
                    <div className="min-w-0 flex-1 leading-tight">
                      <div className="flex items-center gap-1.5">
                        <span className="text-sm text-slate-100">{agentLabel(a.name)}</span>
                        {a.live && (
                          <span className="h-1.5 w-1.5 rounded-full bg-emerald-400"
                                title="Working in the last few minutes" />
                        )}
                        {a.paused && (
                          <span className="text-[10px] text-amber-400/80">paused</span>
                        )}
                      </div>
                      <div className="truncate text-[11px] text-slate-500">
                        {roster?.role ?? "agent"}
                        {a.total_runs > 0 && (
                          <> · {a.clean_runs}/{a.total_runs} clean</>
                        )}
                      </div>
                    </div>
                    <span className="shrink-0 text-[10px] text-slate-500">
                      {a.total_runs > 0 ? a.trust_level_name : "not scored yet"}
                    </span>
                  </div>
                );
              })}
            </div>
            {wf.instruments.length > 0 && (
              <p className="mt-2 text-[11px] text-slate-600">
                Plus {wf.instruments.map((i) => i.name).join(" and ")} — instruments the
                work passes through, not people.
              </p>
            )}
          </section>

          <section>
            <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">
              The people
            </h3>
            {people.length === 0 && (
              <p className="mb-2 text-xs text-slate-500">
                Nobody yet. Anyone you add here also appears on the Digital World map.
              </p>
            )}
            <div className="space-y-1.5">
              {people.map((p) => (
                <div key={p.membership_id}
                     className="flex items-center gap-2 rounded-lg border border-amber-500/20
                                bg-amber-400/5 px-2.5 py-2">
                  <button
                    type="button"
                    onClick={() => onSelectActor(p.actor_id)}
                    className="min-w-0 flex-1 text-left leading-tight"
                  >
                    <div className="truncate text-sm text-amber-50">{p.display_name}</div>
                    {p.role && (
                      <div className="truncate text-[11px] text-slate-500">{p.role}</div>
                    )}
                  </button>
                  <button
                    type="button"
                    onClick={() => removePerson.mutate(p.membership_id)}
                    disabled={removePerson.isPending}
                    title="Take them off the workforce. They stay on the map."
                    className="shrink-0 text-[11px] text-slate-500 hover:text-rose-300"
                  >
                    Remove
                  </button>
                </div>
              ))}
            </div>
            {removePerson.isError && (
              <ErrorNote>{(removePerson.error as Error).message}</ErrorNote>
            )}

            <div className="mt-3 space-y-2 rounded-lg border border-slate-800 bg-slate-950/40 p-2.5">
              <label className="block text-[11px] text-slate-500">
                Add someone new
                <input
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                  placeholder="Their name"
                  className="mt-1 w-full rounded-md border border-slate-700 bg-slate-950
                             px-2 py-1.5 text-sm text-slate-100"
                />
              </label>
              <label className="block text-[11px] text-slate-500">
                What they do here (optional)
                <input
                  value={newRole}
                  onChange={(e) => setNewRole(e.target.value)}
                  placeholder="e.g. prints and delivers the cards"
                  className="mt-1 w-full rounded-md border border-slate-700 bg-slate-950
                             px-2 py-1.5 text-sm text-slate-100"
                />
              </label>
              <button
                type="button"
                disabled={!newName.trim() || addPerson.isPending}
                onClick={() => addPerson.mutate({
                  display_name: newName.trim(), role: newRole.trim() || undefined,
                })}
                className="w-full rounded-md border border-amber-400/40 bg-amber-400/10 px-2 py-1.5
                           text-xs font-medium text-amber-200 disabled:opacity-40"
              >
                {addPerson.isPending ? "Adding…" : "Add to the workforce"}
              </button>

              {candidates.length > 0 && (
                <div className="border-t border-slate-800 pt-2">
                  <label className="block text-[11px] text-slate-500">
                    …or someone already on the map
                    <select
                      value={existingId}
                      onChange={(e) => setExistingId(e.target.value)}
                      className="mt-1 w-full rounded-md border border-slate-700 bg-slate-950
                                 px-2 py-1.5 text-sm text-slate-100"
                    >
                      <option value="">Pick a friend…</option>
                      {candidates.map((a) => (
                        <option key={a.id} value={a.id}>{a.display_name}</option>
                      ))}
                    </select>
                  </label>
                  <button
                    type="button"
                    disabled={!existingId || addPerson.isPending}
                    onClick={() => addPerson.mutate({
                      actor_id: Number(existingId), role: newRole.trim() || undefined,
                    })}
                    className="mt-2 w-full rounded-md border border-slate-600 bg-slate-900 px-2 py-1.5
                               text-xs font-medium text-slate-200 disabled:opacity-40"
                  >
                    Add them too
                  </button>
                  <p className="mt-1.5 text-[10px] text-slate-600">
                    They keep the light they already have — joining the workforce
                    never draws a second one.
                  </p>
                </div>
              )}
              {addPerson.isError && <ErrorNote>{(addPerson.error as Error).message}</ErrorNote>}
            </div>
          </section>

          {wf.recent_work.length > 0 && (
            <section>
              <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">
                Finished lately
              </h3>
              <div className="space-y-1">
                {wf.recent_work.map((w) => (
                  <div key={w.id} className="truncate text-[11px] text-slate-400">
                    <span className="text-slate-600">
                      {w.kind === "quote_card" ? "card" : w.kind}
                    </span>{" "}
                    {w.title || w.id}
                  </div>
                ))}
              </div>
            </section>
          )}
        </div>
      )}

      {wf && section === "message" && (
        <MessageComposer snapshot={snapshot} hasWork={wf.recent_work.length > 0} />
      )}
    </aside>
  );
}

/**
 * Draft a WhatsApp message, then either send it (one contact) or copy it
 * (a nucleus's group).
 *
 * The split is not a design choice: the WhatsApp Cloud API this whole
 * Secretary runs on has no group-messaging endpoint at all, so a group can
 * only ever be reached by Sheraj pasting the message in himself. Saying so on
 * screen is the point — a Send button that silently did nothing for groups is
 * exactly the failure mode Canva autofill was.
 */
function MessageComposer({ snapshot, hasWork }: { snapshot: NucleiSnapshot; hasWork: boolean }) {
  const [target, setTarget] = useState("");
  const [about, setAbout] = useState("");
  const [mentionWork, setMentionWork] = useState(false);
  const [message, setMessage] = useState("");
  const [warnings, setWarnings] = useState<string[]>([]);
  const [copied, setCopied] = useState(false);

  const contacts = useQuery({ queryKey: ["contacts"], queryFn: api.getContacts });
  const channels = snapshot.channels ?? [];
  const groupingsById = useMemo(() => {
    const m: Record<number, string> = {};
    for (const g of snapshot.groupings) m[g.id] = g.name;
    return m;
  }, [snapshot.groupings]);

  const [kind, id] = target ? target.split(":") : ["", ""];
  const isGroup = kind === "group";
  const channel = isGroup ? channels.find((c) => c.id === Number(id)) : undefined;
  const contact = !isGroup && id
    ? (contacts.data?.contacts ?? []).find((c) => c.id === Number(id))
    : undefined;

  const draft = useMutation({
    mutationFn: () => api.draftWorkforceMessage({
      about: about.trim(),
      to_kind: isGroup ? "group" : "contact",
      contact_id: contact?.id,
      channel_id: channel?.id,
      include_recent_work: mentionWork,
    }),
    onSuccess: (d) => { setMessage(d.message); setWarnings(d.warnings ?? []); },
  });
  const send = useMutation({
    mutationFn: () => api.sendWorkforceMessage({
      contact_id: Number(id), message: message.trim(),
    }),
  });

  useEffect(() => { send.reset(); setCopied(false); }, [target, message]);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(message);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };

  return (
    <div className="space-y-3">
      <label className="block text-[11px] text-slate-500">
        Who is it for
        <select
          value={target}
          onChange={(e) => setTarget(e.target.value)}
          className="mt-1 w-full rounded-md border border-slate-700 bg-slate-950 px-2 py-1.5
                     text-sm text-slate-100"
        >
          <option value="">Pick someone or a group…</option>
          {(contacts.data?.contacts ?? []).length > 0 && (
            <optgroup label="One person">
              {(contacts.data?.contacts ?? []).map((c) => (
                <option key={`c-${c.id}`} value={`contact:${c.id}`}>
                  {c.name}{c.allowlisted ? "" : " (needs your approval)"}
                </option>
              ))}
            </optgroup>
          )}
          {channels.length > 0 && (
            <optgroup label="A WhatsApp group">
              {channels.map((ch) => (
                <option key={`g-${ch.id}`} value={`group:${ch.id}`}>
                  {ch.label || groupingsById[ch.grouping_id] || "group"}
                </option>
              ))}
            </optgroup>
          )}
        </select>
      </label>
      {(contacts.data?.contacts ?? []).length === 0 && channels.length === 0 && (
        <p className="text-[11px] text-slate-500">
          Nothing to write to yet. Add trusted contacts in the Secretary tab, or open a
          nucleus and note the WhatsApp group it already talks in.
        </p>
      )}

      <label className="block text-[11px] text-slate-500">
        What should it say
        <textarea
          value={about}
          onChange={(e) => setAbout(e.target.value)}
          rows={3}
          placeholder="e.g. invite them to the devotional on Friday and say the cards are ready"
          className="mt-1 w-full rounded-md border border-slate-700 bg-slate-950 px-2 py-1.5
                     text-sm text-slate-100"
        />
      </label>

      <label className={cn("flex items-start gap-2 text-[11px]",
        hasWork ? "text-slate-400" : "text-slate-600")}>
        <input
          type="checkbox"
          checked={mentionWork}
          disabled={!hasWork}
          onChange={(e) => setMentionWork(e.target.checked)}
          className="mt-0.5"
        />
        <span>
          Mention what the workforce finished lately
          {!hasWork && " — nothing finished yet"}
        </span>
      </label>

      <button
        type="button"
        disabled={!target || !about.trim() || draft.isPending}
        onClick={() => draft.mutate()}
        className="w-full rounded-md border border-amber-400/40 bg-amber-400/10 px-2 py-2
                   text-xs font-medium text-amber-200 disabled:opacity-40"
      >
        {draft.isPending ? "Clara is writing…" : message ? "Write it again" : "Draft the message"}
      </button>
      {draft.isError && <ErrorNote>{(draft.error as Error).message}</ErrorNote>}
      <p className="text-[10px] text-slate-600">
        Clara writes this on the model running on this machine, never a cloud one —
        the draft names one of your friends.
      </p>

      {message && (
        <>
          <label className="block text-[11px] text-slate-500">
            The message — edit it freely before it goes anywhere
            <textarea
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              rows={7}
              className="mt-1 w-full rounded-md border border-slate-700 bg-slate-950 px-2 py-1.5
                         text-sm text-slate-100"
            />
          </label>
          <div className="text-right text-[10px] text-slate-600">{message.length}/900</div>

          {/* A small local model supplies a plausible time when it is not given
              one. It cannot be mechanically removed from free prose, so it is
              pointed at instead — the box above is editable. */}
          {warnings.length > 0 && (
            <div className="rounded-lg border border-amber-500/40 bg-amber-400/5 p-2.5">
              <p className="text-[11px] font-medium text-amber-200">
                Check this before you send it
              </p>
              <ul className="mt-1 space-y-0.5">
                {warnings.map((w) => (
                  <li key={w} className="text-[11px] text-amber-100/80">· {w}</li>
                ))}
              </ul>
            </div>
          )}

          {isGroup ? (
            <div className="space-y-2 rounded-lg border border-slate-800 bg-slate-950/40 p-2.5">
              <p className="text-[11px] text-slate-400">
                WhatsApp gives no way to post into a group from software — not from
                here and not from Abigail. Copy this and paste it into the group
                yourself.
              </p>
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={copy}
                  className="flex flex-1 items-center justify-center gap-1.5 rounded-md
                             border border-amber-400/40 bg-amber-400/10 px-2 py-1.5 text-xs
                             font-medium text-amber-200"
                >
                  {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
                  {copied ? "Copied" : "Copy the message"}
                </button>
                {channel?.link && (
                  <a
                    href={channel.link}
                    target="_blank"
                    rel="noreferrer"
                    className="flex items-center justify-center gap-1.5 rounded-md border
                               border-slate-600 bg-slate-900 px-2.5 py-1.5 text-xs text-slate-200"
                  >
                    <ExternalLink className="h-3.5 w-3.5" />
                    Open the group
                  </a>
                )}
              </div>
            </div>
          ) : (
            <div className="space-y-2">
              <button
                type="button"
                disabled={!message.trim() || send.isPending}
                onClick={() => send.mutate()}
                className="w-full rounded-md border border-emerald-400/40 bg-emerald-400/10
                           px-2 py-2 text-xs font-medium text-emerald-200 disabled:opacity-40"
              >
                {send.isPending ? "Sending…"
                  : contact?.allowlisted ? `Send to ${contact.name}`
                    : "Queue it for your approval"}
              </button>
              <button
                type="button"
                onClick={copy}
                className="w-full rounded-md border border-slate-700 bg-slate-900 px-2 py-1.5
                           text-[11px] text-slate-300"
              >
                {copied ? "Copied" : "Or copy it and send it yourself"}
              </button>
              {send.isError && <ErrorNote>{(send.error as Error).message}</ErrorNote>}
              {send.data && (
                <p className={cn("text-[11px]",
                  send.data.status === "sent" ? "text-emerald-300" : "text-amber-300")}>
                  {send.data.note}
                </p>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
