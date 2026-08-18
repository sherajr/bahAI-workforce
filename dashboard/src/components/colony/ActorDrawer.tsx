import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import type { NucleiSnapshot } from "../../lib/types";
import { cn } from "../../lib/utils";
import { RenameField } from "./RenameField";

interface Props {
  actorId: number;
  snapshot: NucleiSnapshot;
  onClose: () => void;
  onChanged: () => void;
  onSelectActor?: (id: number) => void;
}

export function ActorDrawer({ actorId, snapshot, onClose, onChanged, onSelectActor }: Props) {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["nuclei-actor", actorId],
    queryFn: () => api.getNucleiActor(actorId),
  });
  const detail = q.data;
  const actor = detail?.actor;
  const isYou = actorId === snapshot.owner_actor_id;
  const facets = snapshot.kinds.facet_kinds;
  const participation = facets.filter((f) => f.axis_slug === "participation");
  const service = facets.filter((f) =>
    f.axis_slug === "service" && f.slug !== "being_accompanied" && f.slug !== "accompanying");
  const groupRoles = facets.filter((f) => f.axis_slug === "group_role");

  const sat = useMutation({
    mutationFn: () => api.satTogether(actorId),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["nuclei"] }); onChanged(); q.refetch(); },
  });
  const addFacet = useMutation({
    mutationFn: ({ membership_id, slug }: { membership_id: number; slug: string }) =>
      api.addNucleiFacet(membership_id, slug),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["nuclei"] }); onChanged(); q.refetch(); },
  });
  const endFacet = useMutation({
    mutationFn: (id: number) => api.endNucleiFacet(id),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["nuclei"] }); onChanged(); q.refetch(); },
  });
  const join = useMutation({
    mutationFn: (grouping_id: number) => api.addNucleiMembership(actorId, grouping_id),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["nuclei"] }); onChanged(); q.refetch(); },
  });
  const leave = useMutation({
    mutationFn: (membership_id: number) => api.endNucleiMembership(membership_id),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["nuclei"] }); onChanged(); q.refetch(); },
  });
  // Renaming a person changes the label on their light, the name in every
  // table they sit at, and -- if they are on the workforce -- the name in the
  // Digital World too, so all three caches have to be told.
  const rename = useMutation({
    mutationFn: (display_name: string) => api.patchNucleiActor(actorId, { display_name }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["nuclei"] });
      qc.invalidateQueries({ queryKey: ["nuclei-workforce"] });
      qc.invalidateQueries({ queryKey: ["colony"] });
      onChanged();
      q.refetch();
    },
  });
  const remove = useMutation({
    mutationFn: () => api.archiveNucleiActor(actorId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["nuclei"] });
      onChanged();
      onClose();
    },
  });
  const [confirmRemove, setConfirmRemove] = useState(false);
  const [familyName, setFamilyName] = useState("");
  const [existingPerson, setExistingPerson] = useState("");
  const [walkFrom, setWalkFrom] = useState("");
  const [walkTo, setWalkTo] = useState("");
  const [walkFor, setWalkFor] = useState("");
  useEffect(() => {
    setConfirmRemove(false);
    setFamilyName("");
    setExistingPerson("");
    setWalkFrom("");
    setWalkTo("");
    setWalkFor("");
  }, [actorId]);
  const addFamily = useMutation({
    mutationFn: (body: { person_id?: number; display_name?: string }) =>
      api.addHouseholdMember(actorId, body),
    onSuccess: () => {
      setFamilyName("");
      setExistingPerson("");
      qc.invalidateQueries({ queryKey: ["nuclei"] });
      onChanged();
      q.refetch();
    },
  });
  const leaveFamily = useMutation({
    mutationFn: (id: number) => api.endHouseholdMember(id),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["nuclei"] }); onChanged(); q.refetch(); },
  });
  const addWalk = useMutation({
    mutationFn: (body: { from_actor_id: number; to_actor_id: number; grouping_id: number }) =>
      api.addNucleiTie({ kind_slug: "accompanying", ...body }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["nuclei"] });
      onChanged();
      q.refetch();
    },
  });
  const endWalk = useMutation({
    mutationFn: (id: number) => api.endNucleiTie(id),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["nuclei"] }); onChanged(); q.refetch(); },
  });

  if (!actor) {
    return (
      <aside className="w-80 shrink-0 overflow-y-auto rounded-xl border border-slate-800 bg-slate-950/70 p-4">
        <p className="text-sm text-slate-500">Loading…</p>
      </aside>
    );
  }

  const joined = new Set((detail?.memberships ?? []).map((m) => m.grouping_id));

  return (
    <aside className="w-80 shrink-0 overflow-y-auto rounded-xl border border-slate-800 bg-slate-950/70 p-4">
      <button type="button" onClick={onClose} className="float-right text-xs text-slate-500">Close</button>
      <RenameField
        name={actor.display_name}
        label={actor.kind === "household" ? "Rename this family" : "Rename"}
        subtitle={actor.kind === "household" ? "A household"
          : actor.kind === "collective" ? "A gathering of many — not yet named"
            : isYou ? "This light is you" : "A friend"}
        onSave={(name) => rename.mutateAsync(name)}
      />
      {actor.how_we_met && <p className="mb-3 text-sm text-slate-400">{actor.how_we_met}</p>}

      {(detail?.memberships ?? []).map((m) => {
        const live = m.facets ?? [];
        const partSlug = live.find((f) => f.axis_slug === "participation")?.slug;
        return (
          <div key={m.id} className="mb-4 border-t border-slate-800 pt-3">
            <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-500">
              {m.grouping_name}
            </div>
            {m.grouping_kind === "junior_youth" && actor.kind === "person" && (
              <>
                <p className="mb-1.5 text-[11px] uppercase tracking-wider text-slate-600">
                  Their part in this group
                </p>
                <div className="mb-2 flex flex-wrap gap-1.5">
                  {groupRoles.map((f) => (
                    <Chip
                      key={f.slug}
                      label={f.label}
                      on={live.some((x) => x.slug === f.slug)}
                      onClick={() => addFacet.mutate({ membership_id: m.id, slug: f.slug })}
                    />
                  ))}
                </div>
              </>
            )}
            <p className="mb-1.5 text-[11px] uppercase tracking-wider text-slate-600">How they gather</p>
            <div className="mb-2 flex flex-wrap gap-1.5">
              {participation.map((f) => (
                <Chip
                  key={f.slug}
                  label={f.label}
                  on={partSlug === f.slug}
                  onClick={() => addFacet.mutate({ membership_id: m.id, slug: f.slug })}
                />
              ))}
            </div>
            <p className="mb-1.5 text-[11px] uppercase tracking-wider text-slate-600">How they serve</p>
            <div className="flex flex-wrap gap-1.5">
              {service.map((f) => {
                const hit = live.find((x) => x.slug === f.slug);
                return (
                  <Chip
                    key={f.slug}
                    label={f.label}
                    on={!!hit}
                    core={!!f.is_core}
                    onClick={() => hit
                      ? endFacet.mutate(hit.id)
                      : addFacet.mutate({ membership_id: m.id, slug: f.slug })}
                  />
                );
              })}
            </div>
            <p className="mt-2 text-[11px] text-slate-600">
              Closer when they carry it — tutoring, hosting, animating, teaching, or walking with someone.
            </p>
            <button
              type="button"
              disabled={leave.isPending}
              onClick={() => leave.mutate(m.id)}
              className="mt-2 text-[11px] text-slate-600 hover:text-rose-300"
            >
              {m.grouping_kind === "institution"
                ? (isYou ? "I no longer serve here" : "They no longer serve here")
                : (isYou ? "I no longer sit here" : "They no longer sit here")}
            </button>
          </div>
        );
      })}

      {actor.kind === "person" && (
        <div className="mb-4 border-t border-slate-800 pt-3">
          <p className="mb-1.5 text-[11px] uppercase tracking-wider text-slate-600">
            Walking with — a way to serve
          </p>
          <div className="space-y-1.5">
            {(detail?.ties ?? []).filter((t) => t.slug === "accompanying").map((t) => {
              const from = snapshot.actors.find((a) => a.id === t.from_actor_id);
              const to = snapshot.actors.find((a) => a.id === t.to_actor_id);
              const where = t.grouping_name || "this work";
              const line = t.from_actor_id === actorId
                ? `${isYou ? "You walk" : "They walk"} with ${to?.display_name ?? "a friend"} for ${where}`
                : `${from?.display_name ?? "A friend"} walks with ${isYou ? "you" : "them"} for ${where}`;
              return (
                <div key={t.id} className="flex items-start justify-between gap-2 rounded-lg
                                            border border-slate-800 bg-slate-950/50 px-3 py-2">
                  <span className="text-sm text-slate-300">{line}</span>
                  <button
                    type="button"
                    disabled={endWalk.isPending}
                    onClick={() => endWalk.mutate(t.id)}
                    className="shrink-0 text-[11px] text-slate-600 hover:text-rose-300"
                  >
                    End
                  </button>
                </div>
              );
            })}
          </div>
          <p className="mb-1.5 mt-3 text-[11px] text-slate-500">
            Who walks with whom, and for which work.
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
            <select
              value={walkFor}
              onChange={(e) => setWalkFor(e.target.value)}
              className="w-full rounded-lg border border-slate-700 bg-slate-950 px-2 py-1.5 text-sm text-slate-200"
            >
              <option value="">For which work…</option>
              {snapshot.groupings.map((g) => (
                <option key={g.id} value={g.id}>{g.name}</option>
              ))}
            </select>
            {addWalk.isError && (
              <p className="text-sm text-rose-300">{(addWalk.error as Error).message}</p>
            )}
            <button
              type="button"
              disabled={!walkFrom || !walkTo || !walkFor || walkFrom === walkTo || addWalk.isPending}
              onClick={() => {
                addWalk.mutate({
                  from_actor_id: Number(walkFrom),
                  to_actor_id: Number(walkTo),
                  grouping_id: Number(walkFor),
                });
              }}
              className="w-full rounded-lg border border-amber-400/40 bg-amber-400/10 px-3 py-1.5 text-sm text-amber-200 disabled:opacity-40"
            >
              They walk together for this
            </button>
          </div>
        </div>
      )}

      {actor.kind === "household" && (
        <div className="mb-4 border-t border-slate-800 pt-3">
          <p className="mb-1.5 text-[11px] uppercase tracking-wider text-slate-600">People in this family</p>
          <div className="space-y-1.5">
            {(detail?.family_members ?? []).map((fm) => (
              <div key={fm.id} className="flex items-center justify-between gap-2 rounded-lg
                                          border border-slate-800 bg-slate-950/50 px-3 py-2">
                <button
                  type="button"
                  onClick={() => onSelectActor?.(fm.person_id)}
                  className="text-left text-sm text-slate-300 hover:text-amber-100"
                >
                  {fm.person_name}
                </button>
                <button
                  type="button"
                  disabled={leaveFamily.isPending}
                  onClick={() => leaveFamily.mutate(fm.id)}
                  className="text-[11px] text-slate-600 hover:text-rose-300"
                >
                  Not in this family
                </button>
              </div>
            ))}
            {(detail?.family_members ?? []).length === 0 && (
              <p className="text-xs text-slate-600">No one listed yet.</p>
            )}
          </div>
          <p className="mb-1.5 mt-3 text-[11px] uppercase tracking-wider text-slate-600">
            Someone already on the map
          </p>
          <div className="mb-2 flex gap-2">
            <select
              value={existingPerson}
              onChange={(e) => setExistingPerson(e.target.value)}
              className="min-w-0 flex-1 rounded-lg border border-slate-700 bg-slate-950 px-2 py-1.5 text-sm text-slate-200"
            >
              <option value="">Choose a friend…</option>
              {snapshot.actors
                .filter((a) => a.kind === "person"
                  && !(detail?.family_members ?? []).some((fm) => fm.person_id === a.id))
                .map((a) => (
                  <option key={a.id} value={a.id}>{a.display_name}</option>
                ))}
            </select>
            <button
              type="button"
              disabled={!existingPerson || addFamily.isPending}
              onClick={() => addFamily.mutate({ person_id: Number(existingPerson) })}
              className="rounded-lg border border-amber-400/40 bg-amber-400/10 px-2 py-1.5 text-xs text-amber-200 disabled:opacity-40"
            >
              Add
            </button>
          </div>
          <p className="mb-1.5 text-[11px] uppercase tracking-wider text-slate-600">Someone new</p>
          <div className="flex gap-2">
            <input
              value={familyName}
              onChange={(e) => setFamilyName(e.target.value)}
              placeholder="Their name"
              className="min-w-0 flex-1 rounded-lg border border-slate-700 bg-slate-950 px-2 py-1.5 text-sm text-slate-100"
            />
            <button
              type="button"
              disabled={!familyName.trim() || addFamily.isPending}
              onClick={() => addFamily.mutate({ display_name: familyName.trim() })}
              className="rounded-lg border border-amber-400/40 bg-amber-400/10 px-2 py-1.5 text-xs text-amber-200 disabled:opacity-40"
            >
              Add
            </button>
          </div>
          <p className="mt-2 text-[11px] text-slate-600">
            A friend already on the Local Spiritual Assembly can be in this family too — they stay one light.
          </p>
        </div>
      )}

      {actor.kind === "person" && detail?.family && (
        <div className="mb-4 border-t border-slate-800 pt-3">
          <p className="text-sm text-slate-400">
            Part of <span className="text-slate-200">{detail.family.household_name}</span>
          </p>
          <button
            type="button"
            disabled={leaveFamily.isPending}
            onClick={() => leaveFamily.mutate(detail.family!.id)}
            className="mt-1 text-[11px] text-slate-600 hover:text-rose-300"
          >
            They are no longer in this family
          </button>
        </div>
      )}

      <div className="border-t border-slate-800 pt-3">
        <p className="mb-1.5 text-[11px] uppercase tracking-wider text-slate-600">Also sit at</p>
        <div className="flex flex-wrap gap-1.5">
          {snapshot.groupings.filter((g) => !joined.has(g.id)).map((g) => (
            <button
              key={g.id}
              type="button"
              onClick={() => join.mutate(g.id)}
              className="rounded-full border border-slate-700 px-2 py-0.5 text-[11px] text-slate-400 hover:border-amber-400/40 hover:text-amber-200"
            >
              + {g.name}
            </button>
          ))}
          {snapshot.groupings.every((g) => joined.has(g.id)) && (
            <span className="text-xs text-slate-600">Already in every grouping.</span>
          )}
        </div>
      </div>

      {!isYou && actor.kind === "person" && (
        <div className="mt-4 border-t border-slate-800 pt-3">
          <p className="mb-2 text-sm text-slate-400">
            {detail?.sat_sentence ?? "You have not recorded sitting together yet."}
          </p>
          <button
            type="button"
            onClick={() => sat.mutate()}
            className="w-full rounded-lg border border-amber-400/40 bg-amber-400/10 px-3 py-2 text-sm font-medium text-amber-200"
          >
            We sat together today
          </button>
          <p className="mt-2 text-[11px] text-slate-600">This notes a friendship. It is not a ticket.</p>
        </div>
      )}

      {!isYou && (
        <div className="mt-8 border-t border-slate-800 pt-3">
          {remove.isError && (
            <p className="mb-2 text-sm text-rose-300">{(remove.error as Error).message}</p>
          )}
          {!confirmRemove ? (
            <button
              type="button"
              onClick={() => setConfirmRemove(true)}
              className="text-xs text-slate-600 hover:text-rose-300"
            >
              Remove from the map…
            </button>
          ) : (
            <div className="space-y-2">
              <p className="text-sm text-slate-400">
                Remove <span className="text-slate-200">{actor.display_name}</span> from the map?
                What you already recorded stays. This is uncommon — only if you added them
                by mistake or they are no longer someone you keep here.
              </p>
              <div className="flex gap-2">
                <button
                  type="button"
                  disabled={remove.isPending}
                  onClick={() => remove.mutate()}
                  className="rounded-lg border border-rose-400/40 bg-rose-400/10 px-3 py-1.5 text-sm text-rose-200"
                >
                  Yes, remove them
                </button>
                <button
                  type="button"
                  onClick={() => setConfirmRemove(false)}
                  className="rounded-lg border border-slate-700 px-3 py-1.5 text-sm text-slate-400"
                >
                  Keep them
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </aside>
  );
}

function Chip({ label, on, core, onClick }: {
  label: string; on: boolean; core?: boolean; onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "rounded-full border px-2 py-0.5 text-[11px]",
        on && core && "border-amber-400 bg-amber-400/15 text-amber-200",
        on && !core && "border-sky-400/50 bg-sky-400/10 text-sky-200",
        !on && "border-slate-700 text-slate-500 hover:border-slate-500",
      )}
    >
      {label}{core && on ? " · core" : ""}
    </button>
  );
}
