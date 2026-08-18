import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import type { NucleiSnapshot } from "../../lib/types";

interface Props {
  groupingId: number;
  snapshot: NucleiSnapshot;
  onClose: () => void;
  onChanged: () => void;
  onSelectActor: (id: number) => void;
}

export function GroupingDrawer({ groupingId, snapshot, onClose, onChanged, onSelectActor }: Props) {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["nuclei-grouping", groupingId],
    queryFn: () => api.getNucleiGrouping(groupingId),
  });
  const [name, setName] = useState("");
  const [kind, setKind] = useState("person");
  const [existingId, setExistingId] = useState("");
  const [confirmRemove, setConfirmRemove] = useState(false);
  const [walkFrom, setWalkFrom] = useState("");
  const [walkTo, setWalkTo] = useState("");
  const [groupLabel, setGroupLabel] = useState("");
  const [groupLink, setGroupLink] = useState("");
  useEffect(() => {
    const slug = q.data?.grouping?.kind_slug;
    if (slug === "junior_youth") setKind("household");
    else if (slug === "institution") setKind("person");
  }, [q.data?.grouping?.kind_slug]);
  const joinMe = useMutation({
    mutationFn: async () => {
      const snap = await api.getNucleiSnapshot();
      return api.addNucleiMembership(snap.owner_actor_id, groupingId);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["nuclei"] });
      onChanged();
      q.refetch();
    },
  });
  const remove = useMutation({
    mutationFn: () => api.archiveNucleiGrouping(groupingId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["nuclei"] });
      onChanged();
      onClose();
    },
  });
  const add = useMutation({
    mutationFn: () => api.createNucleiActor({
      kind, display_name: name.trim(), grouping_id: groupingId,
    }),
    onSuccess: () => {
      setName("");
      qc.invalidateQueries({ queryKey: ["nuclei"] });
      onChanged();
      q.refetch();
    },
  });
  const addExisting = useMutation({
    mutationFn: (actor_id: number) => api.addNucleiMembership(actor_id, groupingId),
    onSuccess: () => {
      setExistingId("");
      qc.invalidateQueries({ queryKey: ["nuclei"] });
      onChanged();
      q.refetch();
    },
  });
  const leave = useMutation({
    mutationFn: (membership_id: number) => api.endNucleiMembership(membership_id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["nuclei"] });
      onChanged();
      q.refetch();
    },
  });
  const addWalk = useMutation({
    mutationFn: (body: { from_actor_id: number; to_actor_id: number }) =>
      api.addNucleiTie({ kind_slug: "accompanying", grouping_id: groupingId, ...body }),
    onSuccess: () => {
      setWalkFrom("");
      setWalkTo("");
      qc.invalidateQueries({ queryKey: ["nuclei"] });
      onChanged();
      q.refetch();
    },
  });
  const endWalk = useMutation({
    mutationFn: (id: number) => api.endNucleiTie(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["nuclei"] });
      onChanged();
      q.refetch();
    },
  });
  const channel = (snapshot.channels ?? []).find(
    (c) => c.grouping_id === groupingId && c.kind === "whatsapp_group",
  );
  const saveChannel = useMutation({
    mutationFn: () => api.setNucleiChannel(groupingId, {
      label: groupLabel.trim() || undefined,
      link: groupLink.trim() || undefined,
    }),
    onSuccess: (r) => {
      qc.setQueryData(["nuclei"], r.snapshot);
      onChanged();
    },
  });
  const clearChannel = useMutation({
    mutationFn: (id: number) => api.removeNucleiChannel(id),
    onSuccess: (r) => {
      setGroupLabel("");
      setGroupLink("");
      qc.setQueryData(["nuclei"], r.snapshot);
      onChanged();
    },
  });
  useEffect(() => {
    setWalkFrom("");
    setWalkTo("");
  }, [groupingId]);
  useEffect(() => {
    setGroupLabel(channel?.label ?? "");
    setGroupLink(channel?.link ?? "");
  }, [groupingId, channel?.id, channel?.label, channel?.link]);

  const g = q.data?.grouping;
  const isInstitution = g?.kind_slug === "institution";
  const isJy = g?.kind_slug === "junior_youth";
  const memberIds = new Set((q.data?.members ?? []).map((m) => m.actor.id));
  if (!g) {
    return (
      <aside className="w-80 shrink-0 overflow-y-auto rounded-xl border border-slate-800 bg-slate-950/70 p-4">
        <p className="text-sm text-slate-500">Loading…</p>
      </aside>
    );
  }

  return (
    <aside className="w-80 shrink-0 overflow-y-auto rounded-xl border border-slate-800 bg-slate-950/70 p-4">
      <button type="button" onClick={onClose} className="float-right text-xs text-slate-500">Close</button>
      <h2 className="font-display text-lg text-slate-100">{g.name}</h2>
      <p className="mb-3 text-xs text-slate-500">
        {isInstitution ? "An institution of the Faith"
          : g.is_nucleus ? "A nucleus — a point of light, the Vision in this place"
            : g.kind_label}
      </p>
      <p className="mb-3 text-sm text-slate-400">
        {isInstitution
          ? "Friends who serve here sit close to this light. Click a name to open them. You can take someone off when they no longer serve here."
          : isJy
            ? "Families in the junior youth work. A friend who walks with someone here is serving — they do not have to be one of the families."
            : "Friends who carry this sit close to its light. Friends who only visit sit farther out. Click a name to change that, or to take them off this table or the map."}
      </p>
      <div className="space-y-1.5">
        {(q.data?.members ?? []).map((m) => (
          <div key={m.id} className="rounded-lg border border-slate-800 bg-slate-950/50">
            <div className="flex items-center justify-between gap-2 px-3 py-2">
              <button
                type="button"
                onClick={() => onSelectActor(m.actor.id)}
                className="min-w-0 flex-1 text-left text-sm text-slate-300 hover:text-amber-100"
              >
                {m.actor.display_name}
                <span className="mt-0.5 block text-[11px] text-slate-500">
                  {(m.facets ?? []).map((f) => f.label).join(" · ")
                    || (m.actor.kind === "household" ? "A family" : "—")}
                </span>
              </button>
              <button
                type="button"
                disabled={leave.isPending}
                onClick={() => leave.mutate(m.id)}
                className="shrink-0 text-[11px] text-slate-600 hover:text-rose-300"
              >
                {isInstitution ? "No longer serves here" : "Remove"}
              </button>
            </div>
            {m.actor.kind === "household" && (m.family_members ?? []).length > 0 && (
              <div className="space-y-0.5 border-t border-slate-800/80 px-3 py-2">
                {(m.family_members ?? []).map((fm) => (
                  <button
                    key={fm.id}
                    type="button"
                    onClick={() => onSelectActor(fm.person_id)}
                    className="block w-full text-left text-xs text-slate-400 hover:text-amber-100"
                  >
                    {fm.person_name}
                  </button>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>

      <div className="mt-5 border-t border-slate-800 pt-3">
        <p className="mb-1.5 text-[11px] uppercase tracking-wider text-slate-600">
          Its WhatsApp group
        </p>
        <p className="mb-2 text-[11px] text-slate-500">
          Most nuclei already talk in a group. Note it here and the Bahá'í Workforce
          can write a message for it. Nothing is ever sent to a group automatically —
          WhatsApp gives software no way to post into one, so you paste it in yourself.
        </p>
        <input
          value={groupLabel}
          onChange={(e) => setGroupLabel(e.target.value)}
          placeholder="What the group is called"
          className="mb-1.5 w-full rounded-md border border-slate-700 bg-slate-950 px-2 py-1.5
                     text-sm text-slate-100"
        />
        <input
          value={groupLink}
          onChange={(e) => setGroupLink(e.target.value)}
          placeholder="https://chat.whatsapp.com/… (optional)"
          className="w-full rounded-md border border-slate-700 bg-slate-950 px-2 py-1.5
                     text-sm text-slate-100"
        />
        <div className="mt-1.5 flex gap-2">
          <button
            type="button"
            disabled={(!groupLabel.trim() && !groupLink.trim()) || saveChannel.isPending}
            onClick={() => saveChannel.mutate()}
            className="flex-1 rounded-md border border-slate-600 bg-slate-900 px-2 py-1.5
                       text-[11px] text-slate-200 disabled:opacity-40"
          >
            {saveChannel.isPending ? "Saving…" : channel ? "Update the group" : "Save the group"}
          </button>
          {channel && (
            <button
              type="button"
              disabled={clearChannel.isPending}
              onClick={() => clearChannel.mutate(channel.id)}
              className="rounded-md border border-slate-700 px-2 py-1.5 text-[11px] text-slate-500
                         hover:text-rose-300"
            >
              Forget it
            </button>
          )}
        </div>
        {saveChannel.isError && (
          <p className="mt-1 text-[11px] text-rose-300">{(saveChannel.error as Error).message}</p>
        )}
      </div>

      <div className="mt-5 border-t border-slate-800 pt-3">
        <p className="mb-1.5 text-[11px] uppercase tracking-wider text-slate-600">
          Walking with — a way to serve
        </p>
        <div className="space-y-1.5">
          {(q.data?.accompaniments ?? []).map((t) => (
            <div key={t.id} className="flex items-start justify-between gap-2 rounded-lg
                                        border border-slate-800 bg-slate-950/50 px-3 py-2">
              <button
                type="button"
                onClick={() => onSelectActor(t.from_actor_id)}
                className="text-left text-sm text-slate-300 hover:text-amber-100"
              >
                {t.from_name || "A friend"} walks with {t.to_name || "a friend"}
              </button>
              <button
                type="button"
                disabled={endWalk.isPending}
                onClick={() => endWalk.mutate(t.id)}
                className="shrink-0 text-[11px] text-slate-600 hover:text-rose-300"
              >
                End
              </button>
            </div>
          ))}
        </div>
        <p className="mb-1.5 mt-3 text-[11px] text-slate-500">
          Who walks with whom for this work.
        </p>
        <div className="space-y-2">
          <select
            value={walkFrom}
            onChange={(e) => setWalkFrom(e.target.value)}
            className="w-full rounded-lg border border-slate-700 bg-slate-950 px-2 py-1.5 text-sm text-slate-200"
          >
            <option value="">Who walks with them…</option>
            {snapshot.actors.filter((a) => a.kind === "person").map((a) => (
              <option key={a.id} value={a.id}>{a.display_name}</option>
            ))}
          </select>
          <select
            value={walkTo}
            onChange={(e) => setWalkTo(e.target.value)}
            className="w-full rounded-lg border border-slate-700 bg-slate-950 px-2 py-1.5 text-sm text-slate-200"
          >
            <option value="">Who they walk with…</option>
            {snapshot.actors.filter((a) => a.kind === "person").map((a) => (
              <option key={a.id} value={a.id}>{a.display_name}</option>
            ))}
          </select>
          {addWalk.isError && (
            <p className="text-sm text-rose-300">{(addWalk.error as Error).message}</p>
          )}
          <button
            type="button"
            disabled={!walkFrom || !walkTo || walkFrom === walkTo || addWalk.isPending}
            onClick={() => addWalk.mutate({
              from_actor_id: Number(walkFrom),
              to_actor_id: Number(walkTo),
            })}
            className="w-full rounded-lg border border-amber-400/40 bg-amber-400/10 px-3 py-1.5 text-sm text-amber-200 disabled:opacity-40"
          >
            They walk together for this
          </button>
        </div>
      </div>

      <button
        type="button"
        onClick={() => joinMe.mutate()}
        className="mt-3 w-full rounded-lg border border-slate-700 px-3 py-2 text-sm text-slate-300 hover:border-amber-400/40"
      >
        {isInstitution ? "I serve here too" : "I sit here too"}
      </button>

      <div className="mt-5 border-t border-slate-800 pt-3">
        <p className="mb-2 text-[11px] uppercase tracking-wider text-slate-600">
          Someone already on the map
        </p>
        <div className="mb-3 flex gap-2">
          <select
            value={existingId}
            onChange={(e) => setExistingId(e.target.value)}
            className="min-w-0 flex-1 rounded-lg border border-slate-700 bg-slate-950 px-2 py-1.5 text-sm text-slate-200"
          >
            <option value="">Choose a friend…</option>
            {snapshot.actors
              .filter((a) => !memberIds.has(a.id) && a.kind !== "collective")
              .map((a) => (
                <option key={a.id} value={a.id}>
                  {a.display_name}{a.kind === "household" ? " (family)" : ""}
                </option>
              ))}
          </select>
          <button
            type="button"
            disabled={!existingId || addExisting.isPending}
            onClick={() => addExisting.mutate(Number(existingId))}
            className="rounded-lg border border-amber-400/40 bg-amber-400/10 px-2 py-1.5 text-xs text-amber-200 disabled:opacity-40"
          >
            Add
          </button>
        </div>
        <p className="mb-2 text-[11px] uppercase tracking-wider text-slate-600">
          {isInstitution ? "Someone new who serves here" : isJy ? "A new family" : "Someone new sat with us"}
        </p>
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Their name"
          className="mb-2 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100"
        />
        <select
          value={kind}
          onChange={(e) => setKind(e.target.value)}
          className="mb-2 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-300"
        >
          <option value="person">A person</option>
          <option value="household">A household</option>
          <option value="collective">A whole gathering (not yet named)</option>
        </select>
        <button
          type="button"
          disabled={!name.trim() || add.isPending}
          onClick={() => add.mutate()}
          className="w-full rounded-lg border border-amber-400/40 bg-amber-400/10 px-3 py-2 text-sm font-medium text-amber-200 disabled:opacity-40"
        >
          {isJy ? "Add this family" : "Add them here"}
        </button>
        <p className="mt-2 text-[11px] text-slate-600">
          {isInstitution
            ? "They will sit close to this institution. You can take them off later if they no longer serve here."
            : isJy
              ? "Open the family to list the people in it. A friend already on the Local Spiritual Assembly can be added there too."
              : "They start as connected. Open their light to say they gather regularly, or that they have begun to serve."}
        </p>
      </div>

      <div className="mt-8 border-t border-slate-800 pt-3">
        {!confirmRemove ? (
          <button
            type="button"
            onClick={() => setConfirmRemove(true)}
            className="text-xs text-slate-600 hover:text-rose-300"
          >
            Remove this from the map…
          </button>
        ) : (
          <div className="space-y-2">
            <p className="text-sm text-slate-400">
              Remove <span className="text-slate-200">{g.name}</span> from the map?
              The friends stay. What you already recorded stays. This is uncommon
              {isInstitution
                ? " — only if you added it by mistake or it is no longer an institution you keep here."
                : " — only if you added it by mistake or it is no longer a table you keep."}
            </p>
            <div className="flex gap-2">
              <button
                type="button"
                disabled={remove.isPending}
                onClick={() => remove.mutate()}
                className="rounded-lg border border-rose-400/40 bg-rose-400/10 px-3 py-1.5 text-sm text-rose-200"
              >
                Yes, remove it
              </button>
              <button
                type="button"
                onClick={() => setConfirmRemove(false)}
                className="rounded-lg border border-slate-700 px-3 py-1.5 text-sm text-slate-400"
              >
                Keep it
              </button>
            </div>
          </div>
        )}
      </div>
    </aside>
  );
}
