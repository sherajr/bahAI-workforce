import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Lock, Unlock } from "lucide-react";
import { api } from "../../lib/api";
import type { ContinuityBible, VideoProject } from "../../lib/types";
import { Button, Card, CardContent, CardHeader, CardTitle, ErrorNote } from "../ui";

/**
 * Continuity bible editor.
 *
 * These locked descriptions are what actually keep a character looking the
 * same from shot to shot — every frame prompt is assembled from the shot plus
 * the descriptions of the ids it references. Locking an element means
 * regeneration must preserve it.
 */
export function ContinuityEditor({
  project, onSaved,
}: {
  project: VideoProject;
  onSaved: () => void;
}) {
  const [bible, setBible] = useState<ContinuityBible | null>(project.continuity);
  const [dirty, setDirty] = useState(false);

  const save = useMutation({
    mutationFn: () => api.updateVideoProject(project.id, { continuity: bible! }),
    onSuccess: () => { setDirty(false); onSaved(); },
  });

  if (!bible) {
    return (
      <Card>
        <CardContent className="py-8 text-center text-sm text-slate-500">
          No continuity bible yet — it is written during “Analyse story &amp; plan shots”.
        </CardContent>
      </Card>
    );
  }

  const lockedSet = new Set(bible.locked ?? []);
  const toggleLock = (id: string) => {
    const next = new Set(lockedSet);
    next.has(id) ? next.delete(id) : next.add(id);
    setBible({ ...bible, locked: [...next] });
    setDirty(true);
  };

  const edit = (
    group: "characters" | "locations" | "props",
    index: number,
    field: string,
    value: string
  ) => {
    const list = [...(bible[group] as unknown as Record<string, string>[])];
    list[index] = { ...list[index], [field]: value };
    setBible({ ...bible, [group]: list } as unknown as ContinuityBible);
    setDirty(true);
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center justify-between gap-3">
          <span>Continuity bible</span>
          <Button onClick={() => save.mutate()} loading={save.isPending} disabled={!dirty}
                  className="px-3 py-1 text-xs">
            Save continuity
          </Button>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-5">
        <p className="text-xs text-slate-500">
          These descriptions travel into every frame prompt that references them — that is what
          stops a character drifting between shots. Lock an element to keep regeneration from
          changing it.
        </p>
        {save.isError && <ErrorNote>{(save.error as Error).message}</ErrorNote>}

        <Group title="Characters">
          {(bible.characters ?? []).map((c, i) => (
            <Entry key={c.id ?? i} id={c.id} name={c.name}
                   locked={lockedSet.has(c.id)} onLock={() => toggleLock(c.id)}>
              <Row label="Appearance" value={c.appearance ?? ""} onChange={(v) => edit("characters", i, "appearance", v)} />
              <Row label="Age" value={c.age ?? ""} onChange={(v) => edit("characters", i, "age", v)} />
              <Row label="Hair / face" value={c.hair ?? ""} onChange={(v) => edit("characters", i, "hair", v)} />
              <Row label="Clothing" value={c.clothing ?? ""} onChange={(v) => edit("characters", i, "clothing", v)} />
              <Row label="Colours" value={c.colors ?? ""} onChange={(v) => edit("characters", i, "colors", v)} />
              <Row label="Accessories" value={c.accessories ?? ""} onChange={(v) => edit("characters", i, "accessories", v)} />
            </Entry>
          ))}
        </Group>

        <Group title="Locations">
          {(bible.locations ?? []).map((l, i) => (
            <Entry key={l.id ?? i} id={l.id} name={l.name}
                   locked={lockedSet.has(l.id)} onLock={() => toggleLock(l.id)}>
              <Row label="Architecture" value={l.architecture ?? ""} onChange={(v) => edit("locations", i, "architecture", v)} />
              <Row label="Geography" value={l.geography ?? ""} onChange={(v) => edit("locations", i, "geography", v)} />
              <Row label="Time of day" value={l.time_of_day ?? ""} onChange={(v) => edit("locations", i, "time_of_day", v)} />
              <Row label="Weather" value={l.weather ?? ""} onChange={(v) => edit("locations", i, "weather", v)} />
            </Entry>
          ))}
        </Group>

        <Group title="Props">
          {(bible.props ?? []).map((p, i) => (
            <Entry key={p.id ?? i} id={p.id} name={p.name}
                   locked={lockedSet.has(p.id)} onLock={() => toggleLock(p.id)}>
              <Row label="Description" value={p.description ?? ""} onChange={(v) => edit("props", i, "description", v)} />
            </Entry>
          ))}
        </Group>

        {!!Object.keys(bible.style ?? {}).length && (
          <Group title="Style">
            <div className="space-y-2 rounded-lg border border-slate-800 bg-slate-900/40 p-3">
              {Object.entries(bible.style).map(([k, v]) => (
                <Row key={k} label={k.replace(/_/g, " ")} value={v}
                     onChange={(nv) => {
                       setBible({ ...bible, style: { ...bible.style, [k]: nv } });
                       setDirty(true);
                     }} />
              ))}
            </div>
          </Group>
        )}
      </CardContent>
    </Card>
  );
}

function Group({ title, children }: { title: string; children: React.ReactNode }) {
  const empty = Array.isArray(children) && children.length === 0;
  return (
    <div className="space-y-2">
      <div className="text-xs uppercase tracking-wide text-slate-500">{title}</div>
      {empty ? <div className="text-xs text-slate-600">none</div> : children}
    </div>
  );
}

function Entry({
  id, name, locked, onLock, children,
}: {
  id: string; name: string; locked: boolean; onLock: () => void; children: React.ReactNode;
}) {
  return (
    <div className={
      "space-y-2 rounded-lg border p-3 " +
      (locked ? "border-amber-400/30 bg-amber-400/5" : "border-slate-800 bg-slate-900/40")
    }>
      <div className="flex items-center gap-2">
        <span className="text-sm font-medium text-slate-200">{name || id}</span>
        <code className="rounded bg-slate-800/70 px-1 text-[10px] text-slate-400">{id}</code>
        <button onClick={onLock}
                title={locked ? "Locked — regeneration preserves this" : "Lock this element"}
                className={"ml-auto rounded p-1 " + (locked ? "text-amber-300" : "text-slate-600 hover:text-slate-400")}>
          {locked ? <Lock className="h-3.5 w-3.5" /> : <Unlock className="h-3.5 w-3.5" />}
        </button>
      </div>
      <div className="grid gap-2 sm:grid-cols-2">{children}</div>
    </div>
  );
}

function Row({ label, value, onChange }: {
  label: string; value: string; onChange: (v: string) => void;
}) {
  return (
    <label className="block space-y-0.5">
      <span className="text-[11px] capitalize text-slate-500">{label}</span>
      <input value={value} onChange={(e) => onChange(e.target.value)}
             className="w-full rounded border border-slate-700 bg-slate-900/60 px-2 py-1 text-xs text-slate-200 focus:border-amber-400/50 focus:outline-none" />
    </label>
  );
}
