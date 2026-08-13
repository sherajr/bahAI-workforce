import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import {
  ArrowDown, ArrowUp, CheckCircle2, ChevronDown, ChevronRight, Copy, Lock, Merge,
  Play, RefreshCw, Scissors, ShieldCheck, Sparkles, Trash2, Unlock, Wand2,
} from "lucide-react";
import { api, BASE } from "../../lib/api";
import type { ContinuityBible, VideoShot, VideoShotData } from "../../lib/types";
import { BadgePill, Button, Card, CardContent, CardHeader, CardTitle, ErrorNote } from "../ui";

/**
 * Storyboard editor — the main working surface for the shot plan.
 *
 * Shots are edited as FIELDS, never as raw JSON (the spec is explicit about
 * this); the full record is still exportable from the Review tab.
 */
export function Storyboard({
  projectId, shots, continuity, onChanged, onGenerateFrames, onGenerateClips, busy,
}: {
  projectId: string;
  shots: VideoShot[];
  continuity: ContinuityBible | null;
  onChanged: () => void;
  onGenerateFrames: (ids: string[] | null, force: boolean) => void;
  onGenerateClips: (ids: string[] | null, force: boolean) => void;
  busy: boolean;
}) {
  const [openId, setOpenId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const run = <T,>(p: Promise<T>) =>
    p.then(() => { setError(null); onChanged(); })
     .catch((e: Error) => setError(e.message));

  const addShot = useMutation({
    mutationFn: (after: number | null) => api.addVideoShot(projectId, after, {}),
    onSuccess: onChanged, onError: (e: Error) => setError(e.message),
  });
  const approveAll = useMutation({
    mutationFn: (approved: boolean) => api.approveVideoShots(projectId, null, approved),
    onSuccess: onChanged, onError: (e: Error) => setError(e.message),
  });

  const move = (index: number, delta: number) => {
    const next = [...shots];
    const target = index + delta;
    if (target < 0 || target >= next.length) return;
    [next[index], next[target]] = [next[target], next[index]];
    run(api.reorderVideoShots(projectId, next.map((s) => s.id)));
  };

  if (shots.length === 0) {
    return (
      <Card>
        <CardContent className="py-10 text-center text-sm text-slate-500">
          No shots planned yet. Use “Analyse story &amp; plan shots” on the Direction tab.
        </CardContent>
      </Card>
    );
  }

  const totalSeconds = shots.reduce((sum, s) => sum + (s.data.duration ?? 3.5), 0);
  const complex = shots.filter((s) => (s.data.complexity_score ?? 0) > 3).length;

  return (
    <div className="space-y-4">
      {error && <ErrorNote>{error}</ErrorNote>}

      <Card>
        <CardHeader>
          <CardTitle className="flex flex-wrap items-center justify-between gap-3">
            <span>{shots.length} shots · {totalSeconds.toFixed(1)}s total</span>
            <div className="flex flex-wrap gap-2">
              <Button variant="secondary" className="px-3 py-1 text-xs" disabled={busy}
                      onClick={() => onGenerateFrames(null, false)}>
                <Sparkles className="h-3.5 w-3.5" /> Generate all frames
              </Button>
              <Button variant="secondary" className="px-3 py-1 text-xs" disabled={busy}
                      onClick={() => onGenerateClips(null, false)}>
                <Play className="h-3.5 w-3.5" /> Generate all clips
              </Button>
              <Button variant="ghost" className="px-3 py-1 text-xs"
                      onClick={() => approveAll.mutate(true)}>
                <CheckCircle2 className="h-3.5 w-3.5" /> Approve all
              </Button>
            </div>
          </CardTitle>
        </CardHeader>
        {complex > 0 && (
          <CardContent className="pt-0">
            <div className="rounded-lg border border-amber-500/25 bg-amber-500/5 px-3 py-2 text-xs text-amber-200">
              {complex} shot{complex > 1 ? "s are" : " is"} above the complexity budget — a small
              local model may struggle. Use <em>Simplify</em> or <em>Split</em> on those.
            </div>
          </CardContent>
        )}
      </Card>

      {shots.map((shot, i) => (
        <ShotCard
          key={shot.id}
          shot={shot}
          index={i}
          total={shots.length}
          continuity={continuity}
          open={openId === shot.id}
          onToggle={() => setOpenId(openId === shot.id ? null : shot.id)}
          onMove={(d) => move(i, d)}
          onChanged={onChanged}
          onError={setError}
          onGenerateFrames={onGenerateFrames}
          onGenerateClips={onGenerateClips}
          nextShotId={shots[i + 1]?.id}
          busy={busy}
        />
      ))}

      <Button variant="secondary" onClick={() => addShot.mutate(shots.length)}
              loading={addShot.isPending}>
        + Add a shot at the end
      </Button>
    </div>
  );
}

function ShotCard({
  shot, index, total, continuity, open, onToggle, onMove, onChanged, onError,
  onGenerateFrames, onGenerateClips, nextShotId, busy,
}: {
  shot: VideoShot;
  index: number;
  total: number;
  continuity: ContinuityBible | null;
  open: boolean;
  onToggle: () => void;
  onMove: (delta: number) => void;
  onChanged: () => void;
  onError: (msg: string) => void;
  onGenerateFrames: (ids: string[] | null, force: boolean) => void;
  onGenerateClips: (ids: string[] | null, force: boolean) => void;
  nextShotId?: string;
  busy: boolean;
}) {
  const [draft, setDraft] = useState<VideoShotData>(shot.data);
  const [dirty, setDirty] = useState(false);

  const act = (p: Promise<unknown>) =>
    p.then(() => onChanged()).catch((e: Error) => onError(e.message));

  const save = useMutation({
    mutationFn: () => api.editVideoShot(shot.id, { data: draft }),
    onSuccess: () => { setDirty(false); onChanged(); },
    onError: (e: Error) => onError(e.message),
  });

  const set = <K extends keyof VideoShotData>(k: K, v: VideoShotData[K]) => {
    setDraft((p) => ({ ...p, [k]: v }));
    setDirty(true);
  };

  const locked = new Set(shot.locked_fields);
  const toggleLock = (field: string) => {
    const next = new Set(locked);
    next.has(field) ? next.delete(field) : next.add(field);
    act(api.editVideoShot(shot.id, { locked_fields: [...next] }));
  };

  const complexity = shot.data.complexity_score ?? 0;
  const sacred = shot.data.sacred_treatment;

  return (
    <Card className={shot.error ? "border-rose-500/30" : undefined}>
      <CardHeader className="pb-2">
        <div className="flex items-start gap-3">
          <button onClick={onToggle} className="mt-0.5 text-slate-400 hover:text-slate-200">
            {open ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
          </button>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-sm font-semibold text-slate-200">Shot {shot.shot_number}</span>
              <BadgePill className="border-slate-700 bg-slate-800/60 text-slate-400">
                {shot.data.duration ?? 3.5}s
              </BadgePill>
              <BadgePill className={
                shot.continuity_mode === "continuous"
                  ? "border-sky-500/40 bg-sky-500/10 text-sky-300"
                  : "border-slate-700 bg-slate-800/60 text-slate-400"
              }>
                {shot.continuity_mode === "continuous" ? "continuous" : "cut"}
              </BadgePill>
              {complexity > 3 && (
                <BadgePill className="border-amber-500/40 bg-amber-500/10 text-amber-300">
                  complexity {complexity}
                </BadgePill>
              )}
              {shot.data.needs_replanning && (
                <BadgePill className="border-rose-500/40 bg-rose-500/10 text-rose-300"
                           title="The Director could not plan this beat — edit it or re-plan.">
                  needs re-planning
                </BadgePill>
              )}
              {sacred && (
                <BadgePill className="border-sky-500/40 bg-sky-500/10 text-sky-300"
                           title={sacred.rule}>
                  <ShieldCheck className="mr-1 h-3 w-3" /> indirect
                </BadgePill>
              )}
              {shot.approved && <CheckCircle2 className="h-4 w-4 text-emerald-400" />}
            </div>
            <div className="mt-1 truncate text-sm text-slate-400">
              {shot.data.subject || shot.data.narrative_purpose || "—"}
            </div>
          </div>
          <div className="flex shrink-0 gap-1">
            <IconBtn title="Move up" disabled={index === 0} onClick={() => onMove(-1)}>
              <ArrowUp className="h-3.5 w-3.5" />
            </IconBtn>
            <IconBtn title="Move down" disabled={index === total - 1} onClick={() => onMove(1)}>
              <ArrowDown className="h-3.5 w-3.5" />
            </IconBtn>
          </div>
        </div>
      </CardHeader>

      {/* Frame pair — the continuity relationship, always visible. */}
      <CardContent className="pt-0">
        <div className="flex flex-wrap items-center gap-3">
          <FrameBox label="First frame" url={shot.first_frame_url} />
          <span className="text-slate-600">→</span>
          <FrameBox label="Last frame" url={shot.last_frame_url} />
          {shot.clip_url && (
            <video src={`${BASE}${shot.clip_url}`} controls
                   className="h-24 rounded-lg border border-slate-800" />
          )}
          <div className="flex flex-1 flex-wrap justify-end gap-2">
            <Button variant="secondary" className="px-2.5 py-1 text-xs" disabled={busy}
                    onClick={() => onGenerateFrames([shot.id], true)}>
              <RefreshCw className="h-3.5 w-3.5" /> Frames
            </Button>
            <Button variant="secondary" className="px-2.5 py-1 text-xs" disabled={busy}
                    onClick={() => onGenerateClips([shot.id], true)}>
              <Play className="h-3.5 w-3.5" /> Clip
            </Button>
            <Button
              variant={shot.approved ? "ghost" : "secondary"}
              className="px-2.5 py-1 text-xs"
              onClick={() => act(api.editVideoShot(shot.id, { approved: !shot.approved }))}
            >
              <CheckCircle2 className="h-3.5 w-3.5" /> {shot.approved ? "Approved" : "Approve"}
            </Button>
          </div>
        </div>
        {shot.error && (
          <p className="mt-2 text-xs text-rose-300">Last generation failed: {shot.error}</p>
        )}
        {sacred && (
          <p className="mt-2 text-xs text-sky-300/80">{sacred.rule}</p>
        )}
      </CardContent>

      {open && (
        <CardContent className="space-y-3 border-t border-slate-800 pt-3">
          <div className="grid gap-3 sm:grid-cols-2">
            <ShotField label="Subject" field="subject" value={draft.subject ?? ""}
                       locked={locked} onLock={toggleLock} onChange={(v) => set("subject", v)} />
            <ShotField label="Primary action (one only)" field="primary_action"
                       value={draft.primary_action ?? ""} locked={locked} onLock={toggleLock}
                       onChange={(v) => set("primary_action", v)} />
            <ShotField label="Setting" field="setting" value={draft.setting ?? ""}
                       locked={locked} onLock={toggleLock} onChange={(v) => set("setting", v)} />
            <ShotField label="Time of day" field="time_of_day" value={draft.time_of_day ?? ""}
                       locked={locked} onLock={toggleLock} onChange={(v) => set("time_of_day", v)} />
            <ShotField label="Framing" field="framing" value={draft.framing ?? ""}
                       locked={locked} onLock={toggleLock} onChange={(v) => set("framing", v)} />
            <ShotField label="Camera movement" field="camera_movement"
                       value={draft.camera_movement ?? ""} locked={locked} onLock={toggleLock}
                       onChange={(v) => set("camera_movement", v)} />
            <ShotField label="Lighting" field="lighting" value={draft.lighting ?? ""}
                       locked={locked} onLock={toggleLock} onChange={(v) => set("lighting", v)} />
            <label className="block space-y-1">
              <span className="text-xs text-slate-400">Duration (3–4s)</span>
              <input type="number" min={3} max={4} step={0.5} value={draft.duration ?? 3.5}
                     onChange={(e) => set("duration", Number(e.target.value))}
                     className="w-full rounded-lg border border-slate-700 bg-slate-900/60 px-3 py-2 text-sm text-slate-200 focus:border-amber-400/50 focus:outline-none" />
            </label>
          </div>

          <ShotArea label="First frame prompt (a still image)" field="first_frame_prompt"
                    value={draft.first_frame_prompt ?? ""} locked={locked} onLock={toggleLock}
                    onChange={(v) => set("first_frame_prompt", v)} />
          <ShotArea label="Last frame prompt (the same scene, after the action)"
                    field="last_frame_prompt" value={draft.last_frame_prompt ?? ""}
                    locked={locked} onLock={toggleLock} onChange={(v) => set("last_frame_prompt", v)} />
          <ShotArea label="Motion (what moves between the two frames)" field="motion_prompt"
                    value={draft.motion_prompt ?? ""} locked={locked} onLock={toggleLock}
                    onChange={(v) => set("motion_prompt", v)} />
          <ShotArea label="Narration (spoken — never enters the picture prompt)"
                    field="narration" value={draft.narration ?? ""} locked={locked}
                    onLock={toggleLock} onChange={(v) => set("narration", v)} />
          <div className="grid gap-3 sm:grid-cols-2">
            <ShotField label="Sound / ambience" field="sound_notes" value={draft.sound_notes ?? ""}
                       locked={locked} onLock={toggleLock} onChange={(v) => set("sound_notes", v)} />
            <ShotField label="Continuity notes" field="continuity_notes"
                       value={draft.continuity_notes ?? ""} locked={locked} onLock={toggleLock}
                       onChange={(v) => set("continuity_notes", v)} />
          </div>

          {/* Continuity references */}
          <div className="text-xs text-slate-500">
            References:{" "}
            {[...(draft.character_ids ?? []), ...(draft.location_ids ?? []), ...(draft.prop_ids ?? [])]
              .join(", ") || "none"}
            {continuity && (
              <span className="ml-1 text-slate-600">
                (locked descriptions from the continuity bible travel into every frame prompt)
              </span>
            )}
          </div>

          <div className="flex flex-wrap gap-2 border-t border-slate-800 pt-3">
            <Button onClick={() => save.mutate()} loading={save.isPending} disabled={!dirty}
                    className="px-3 py-1 text-xs">
              Save changes
            </Button>
            <Button variant="secondary" className="px-3 py-1 text-xs"
                    onClick={() => act(api.simplifyVideoShot(shot.id))}>
              <Wand2 className="h-3.5 w-3.5" /> Simplify
            </Button>
            <Button variant="secondary" className="px-3 py-1 text-xs"
                    onClick={() => act(api.splitVideoShot(shot.id))}>
              <Scissors className="h-3.5 w-3.5" /> Split
            </Button>
            {nextShotId && (
              <Button variant="secondary" className="px-3 py-1 text-xs"
                      onClick={() => act(api.mergeVideoShots(shot.id, nextShotId))}>
                <Merge className="h-3.5 w-3.5" /> Merge with next
              </Button>
            )}
            <Button variant="secondary" className="px-3 py-1 text-xs"
                    onClick={() => act(api.duplicateVideoShot(shot.id))}>
              <Copy className="h-3.5 w-3.5" /> Duplicate
            </Button>
            <Button variant="secondary" className="px-3 py-1 text-xs"
                    onClick={() => act(api.editVideoShot(shot.id, {
                      continuity_mode: shot.continuity_mode === "continuous"
                        ? "editorial_cut" : "continuous",
                    }))}>
              Make {shot.continuity_mode === "continuous" ? "a cut" : "continuous"}
            </Button>
            <Button variant="danger" className="px-3 py-1 text-xs"
                    onClick={() => { if (confirm(`Delete shot ${shot.shot_number}?`)) act(api.deleteVideoShot(shot.id)); }}>
              <Trash2 className="h-3.5 w-3.5" /> Delete
            </Button>
          </div>
        </CardContent>
      )}
    </Card>
  );
}

function FrameBox({ label, url }: { label: string; url: string }) {
  return (
    <div className="space-y-1">
      <div className="text-[10px] uppercase tracking-wide text-slate-500">{label}</div>
      {url ? (
        <img src={`${BASE}${url}`} alt={label}
             className="h-24 w-40 rounded-lg border border-slate-800 object-cover" />
      ) : (
        <div className="flex h-24 w-40 items-center justify-center rounded-lg border border-dashed border-slate-800 text-[11px] text-slate-600">
          not generated
        </div>
      )}
    </div>
  );
}

function IconBtn({ children, title, onClick, disabled }: {
  children: React.ReactNode; title: string; onClick: () => void; disabled?: boolean;
}) {
  return (
    <button title={title} onClick={onClick} disabled={disabled}
            className="rounded p-1.5 text-slate-500 hover:bg-slate-800 hover:text-slate-300 disabled:opacity-30">
      {children}
    </button>
  );
}

function LockBtn({ field, locked, onLock }: {
  field: string; locked: Set<string>; onLock: (f: string) => void;
}) {
  const isLocked = locked.has(field);
  return (
    <button
      onClick={() => onLock(field)}
      title={isLocked ? "Locked — regeneration won't change this" : "Lock this field"}
      className={"rounded p-0.5 " + (isLocked ? "text-amber-300" : "text-slate-600 hover:text-slate-400")}
    >
      {isLocked ? <Lock className="h-3 w-3" /> : <Unlock className="h-3 w-3" />}
    </button>
  );
}

function ShotField({ label, field, value, onChange, locked, onLock }: {
  label: string; field: string; value: string; onChange: (v: string) => void;
  locked: Set<string>; onLock: (f: string) => void;
}) {
  return (
    <label className="block space-y-1">
      <span className="flex items-center gap-1.5 text-xs text-slate-400">
        {label} <LockBtn field={field} locked={locked} onLock={onLock} />
      </span>
      <input value={value} onChange={(e) => onChange(e.target.value)}
             className="w-full rounded-lg border border-slate-700 bg-slate-900/60 px-3 py-2 text-sm text-slate-200 focus:border-amber-400/50 focus:outline-none" />
    </label>
  );
}

function ShotArea({ label, field, value, onChange, locked, onLock }: {
  label: string; field: string; value: string; onChange: (v: string) => void;
  locked: Set<string>; onLock: (f: string) => void;
}) {
  return (
    <label className="block space-y-1">
      <span className="flex items-center gap-1.5 text-xs text-slate-400">
        {label} <LockBtn field={field} locked={locked} onLock={onLock} />
      </span>
      <textarea value={value} onChange={(e) => onChange(e.target.value)} rows={2}
                className="w-full rounded-lg border border-slate-700 bg-slate-900/60 px-3 py-2 text-sm text-slate-200 focus:border-amber-400/50 focus:outline-none" />
    </label>
  );
}
