"""
Review, continuity validation, assembly and export for the Video pipeline.

Two halves:

1. VALIDATION — flags continuity problems before the user spends time on a
   draft. Structural checks (missing assets, complexity budget, mismatched
   frame pairs, location/screen-direction drift) are DETERMINISTIC and always
   run. An optional vision pass reuses the repo's existing image-analysis
   infrastructure (`router.call_grok_vision`, the Reviewer's own path) to
   compare consecutive frames — it is opt-in because it is a paid call, and
   its absence never blocks the pipeline.

2. ASSEMBLY — joins the approved clips into a draft video with ffmpeg when
   ffmpeg is present, and always writes the production metadata (shot plan,
   prompts, seeds, continuity bible, narration/subtitles) regardless. This is
   deliberately NOT a nonlinear editor: order, join, optional crossfade,
   title/attribution cards, captions, export. Nothing more.
"""

import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path

from agents import video_director, video_store

OUTPUTS_DIR = Path(__file__).parent.parent / "outputs"
OUTPUTS_DIR.mkdir(exist_ok=True)


def ffmpeg_path() -> str | None:
    """Absolute path to ffmpeg, or None. Export degrades honestly without it."""
    return shutil.which("ffmpeg")


def has_ffmpeg() -> bool:
    return ffmpeg_path() is not None


# --- Continuity validation ---------------------------------------------------

SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


def validate_project(project_id: str, use_vision: bool = False) -> dict:
    """
    Structural continuity checks over the whole project. Returns findings
    sorted most-severe first. `use_vision` adds a paid frame-comparison pass.
    """
    project = video_store.get_project(project_id)
    if not project:
        raise ValueError(f"Video project {project_id} not found.")
    shots = video_store.list_shots(project_id)
    continuity = project.get("continuity") or {}
    findings: list[dict] = []

    known_chars = {c.get("id") for c in continuity.get("characters", []) if c.get("id")}
    known_locs = {l.get("id") for l in continuity.get("locations", []) if l.get("id")}
    known_props = {p.get("id") for p in continuity.get("props", []) if p.get("id")}

    prev = None
    for shot in shots:
        data = shot.get("data") or {}
        number = shot["shot_number"]

        # -- assets present --
        if not shot.get("first_frame_path"):
            findings.append(_f(number, shot["id"], "missing_asset", "error",
                               "No first frame generated yet."))
        if not shot.get("last_frame_path"):
            findings.append(_f(number, shot["id"], "missing_asset", "warning",
                               "No last frame generated yet."))
        if not shot.get("clip_path"):
            findings.append(_f(number, shot["id"], "missing_asset", "info",
                               "No clip generated yet."))
        if shot.get("error"):
            findings.append(_f(number, shot["id"], "generation_error", "error",
                               f"Last generation failed: {shot['error'][:180]}"))

        # -- complexity budget --
        score = video_director.complexity_score(data)
        if score > video_director.COMPLEXITY_LIMIT:
            findings.append(_f(number, shot["id"], "complexity", "warning",
                               f"Shot complexity {score} exceeds the budget of "
                               f"{video_director.COMPLEXITY_LIMIT} — a small model may "
                               f"struggle. Use Simplify or Split."))

        # -- duration constraint --
        duration = data.get("duration")
        if duration is not None and not (
                video_director.MIN_SHOT_SECONDS - 0.01 <= float(duration)
                <= video_director.MAX_SHOT_SECONDS + 0.01):
            findings.append(_f(number, shot["id"], "duration", "warning",
                               f"Duration {duration}s is outside the 3-4s window."))

        # -- dangling continuity ids (the drift mechanism) --
        for key, known, label in (("character_ids", known_chars, "character"),
                                  ("location_ids", known_locs, "location"),
                                  ("prop_ids", known_props, "prop")):
            for ref in data.get(key) or []:
                if known and ref not in known:
                    findings.append(_f(number, shot["id"], "unknown_reference", "warning",
                                       f"References unknown {label} id '{ref}' — its look "
                                       f"is not locked, so it will drift between shots."))

        if prev is not None:
            pdata = prev.get("data") or {}
            # -- location drift on a 'continuous' shot --
            if (shot.get("continuity_mode") == "continuous"
                    and set(pdata.get("location_ids") or []) != set(data.get("location_ids") or [])):
                findings.append(_f(number, shot["id"], "location_drift", "error",
                                   "Marked as continuous movement but the location changed "
                                   "from the previous shot — should be an editorial cut."))
            # -- continuous shot not actually chained --
            if (shot.get("continuity_mode") == "continuous"
                    and prev.get("last_frame_path")
                    and shot.get("first_frame_path")
                    and shot["first_frame_path"] != prev["last_frame_path"]):
                findings.append(_f(number, shot["id"], "broken_chain", "warning",
                                   "Continuous shot does not start from the previous shot's "
                                   "last frame — continuity will visibly jump."))
            # -- screen direction --
            pd, cd = _direction_of(pdata), _direction_of(data)
            if pd and cd and pd != cd and shot.get("continuity_mode") == "continuous":
                findings.append(_f(number, shot["id"], "screen_direction", "warning",
                                   f"Screen direction flips ({pd} → {cd}) inside a continuous "
                                   f"run — the subject will appear to reverse."))
            # -- time-of-day / lighting jumps --
            if (pdata.get("time_of_day") and data.get("time_of_day")
                    and pdata["time_of_day"] != data["time_of_day"]
                    and pdata.get("beat_id") == data.get("beat_id")):
                findings.append(_f(number, shot["id"], "lighting", "info",
                                   f"Time of day changes within one beat "
                                   f"({pdata['time_of_day']} → {data['time_of_day']})."))
        # -- motion description defects (the "trippy" causes, measured 2026-08-13) --
        motion = str(data.get("motion_prompt") or "")
        cleaned, negated = video_director._strip_motion_negation(motion)
        if negated:
            findings.append(_f(number, shot["id"], "motion", "warning",
                               "The movement description tells the model not to move, which "
                               "contradicts this shot's own action — the clip will drift and "
                               "morph instead of moving. Use 'Check the movement descriptions'."))
        if prev is not None:
            pmotion = str((prev.get("data") or {}).get("motion_prompt") or "")
            if cleaned and video_director._norm_motion(cleaned) == \
                    video_director._norm_motion(pmotion):
                findings.append(_f(number, shot["id"], "motion", "warning",
                                   f"Has the same movement description as shot {number - 1}, so "
                                   f"the video repeats itself instead of moving on."))
            if (shot.get("continuity_mode") == "continuous"
                    and (prev.get("data") or {}).get("framing")
                    and data.get("framing")
                    and data["framing"] != (prev.get("data") or {})["framing"]):
                findings.append(_f(number, shot["id"], "camera_mismatch", "warning",
                                   f"Continues from shot {number - 1} (reusing its final frame) "
                                   f"but declares different framing — the model will warp trying "
                                   f"to satisfy both."))
        prev = shot

    # Story-level repetition. Reported, never auto-fixed: deciding an action was
    # meant to happen once is a story judgement, not a mechanical one.
    for message in video_director.repeated_action_warnings(
            [dict((s.get("data") or {})) for s in shots]):
        findings.append(_f(0, "", "repeated_action", "warning", message))

    if use_vision:
        findings += _vision_findings(shots)

    findings.sort(key=lambda f: (SEVERITY_ORDER.get(f["severity"], 9), f["shot_number"]))
    summary = {
        "errors": sum(1 for f in findings if f["severity"] == "error"),
        "warnings": sum(1 for f in findings if f["severity"] == "warning"),
        "info": sum(1 for f in findings if f["severity"] == "info"),
    }
    return {"findings": findings, "summary": summary, "shot_count": len(shots),
            "vision_used": bool(use_vision)}


def _f(number: int, shot_id: str, kind: str, severity: str, message: str) -> dict:
    return {"shot_number": number, "shot_id": shot_id, "kind": kind,
            "severity": severity, "message": message}


_LEFT = ("left", "leftward", "to the left", "screen left")
_RIGHT = ("right", "rightward", "to the right", "screen right")


def _direction_of(data: dict) -> str | None:
    text = f"{data.get('primary_action','')} {data.get('camera_movement','')}".lower()
    has_left = any(w in text for w in _LEFT)
    has_right = any(w in text for w in _RIGHT)
    if has_left and not has_right:
        return "left"
    if has_right and not has_left:
        return "right"
    return None


def _vision_findings(shots: list[dict]) -> list[dict]:
    """
    Optional paid pass: ask the existing vision model to compare each
    consecutive frame pair for character/costume/prop/lighting drift. Failures
    are reported as findings, never raised — a validation extra must not break
    the pipeline.
    """
    from agents.router import call_grok_vision
    out: list[dict] = []
    pairs = []
    for i in range(1, len(shots)):
        a, b = shots[i - 1].get("last_frame_path"), shots[i].get("first_frame_path")
        if a and b and os.path.exists(a) and os.path.exists(b):
            pairs.append((shots[i], a, b))
    for shot, a, b in pairs[:8]:  # cap the spend; the worst drift shows up early
        try:
            reply = call_grok_vision(
                [a, b],
                "The FIRST image ends one shot; the SECOND begins the next. List only real "
                "continuity breaks between them: character appearance, costume, props, "
                "lighting, or location. Reply JSON: "
                '{"issues":[{"kind":"costume|character|prop|lighting|location|style",'
                '"detail":"","severity":"warning|info"}]}. Empty list if consistent.',
                max_tokens=400, json_mode=True,
            )
            data = json.loads(reply[reply.find("{"):reply.rfind("}") + 1])
            for issue in (data.get("issues") or [])[:4]:
                out.append(_f(shot["shot_number"], shot["id"],
                              f"vision_{issue.get('kind', 'drift')}",
                              issue.get("severity", "warning"),
                              f"Vision check: {issue.get('detail', '')}"[:300]))
        except Exception as e:
            out.append(_f(shot["shot_number"], shot["id"], "vision_unavailable", "info",
                          f"Vision continuity check could not run: {str(e)[:150]}"))
            break  # one failure means the rest will fail too — don't spend on them
    return out


# --- Export ------------------------------------------------------------------

def export_metadata(project_id: str) -> dict:
    """
    The full production record: shot plan, prompts, seeds, continuity bible,
    narration, provider settings. Always available, ffmpeg or not — this is
    the JSON export the spec asks for.
    """
    project = video_store.get_project(project_id)
    if not project:
        raise ValueError(f"Video project {project_id} not found.")
    shots = video_store.list_shots(project_id)
    assets = video_store.list_assets(project_id)

    by_shot: dict[str, list[dict]] = {}
    for asset in assets:
        by_shot.setdefault(asset.get("shot_id") or "", []).append({
            "kind": asset["kind"], "path": asset["path"], "prompt": asset.get("prompt"),
            "seed": asset.get("seed"), "model": asset.get("model"),
            "version": asset.get("version"), "meta": asset.get("meta"),
            "created_at": asset.get("created_at"),
        })

    return {
        "project": {
            "id": project["id"], "title": project.get("title"),
            "status": project.get("status"), "stage": project.get("stage"),
            "source_kind": project.get("source_kind"),
            "source_product_id": project.get("source_product_id"),
            "source_text": project.get("source_text"),
            "source_brief": project.get("source_brief"),
            "instructions": project.get("source_instructions"),
            "created_at": project.get("created_at"),
        },
        "direction": project.get("direction") or {},
        "analysis": project.get("analysis") or {},
        "continuity": project.get("continuity") or {},
        "safety": project.get("safety") or {},
        "shots": [{
            "shot_number": s["shot_number"], "id": s["id"], "beat_id": s.get("beat_id"),
            "status": s.get("status"), "approved": s.get("approved"),
            "continuity_mode": s.get("continuity_mode"),
            "locked_fields": s.get("locked_fields"),
            "first_frame": s.get("first_frame_path"), "last_frame": s.get("last_frame_path"),
            "clip": s.get("clip_path"), "error": s.get("error"),
            **(s.get("data") or {}),
            "asset_history": by_shot.get(s["id"], []),
        } for s in shots],
        "totals": {
            "shots": len(shots),
            "approved": sum(1 for s in shots if s.get("approved")),
            "with_clips": sum(1 for s in shots if s.get("clip_path")),
            "estimated_seconds": round(
                sum(float((s.get("data") or {}).get("duration") or 0) for s in shots), 1),
        },
    }


def export_subtitles(project_id: str) -> str:
    """Narration as an SRT track, timed from each shot's duration."""
    shots = video_store.list_shots(project_id)
    lines: list[str] = []
    t = 0.0
    index = 1
    for shot in shots:
        data = shot.get("data") or {}
        duration = float(data.get("duration") or video_director.DEFAULT_SHOT_SECONDS)
        text = (data.get("narration") or "").strip()
        if text:
            lines.append(f"{index}\n{_ts(t)} --> {_ts(t + duration)}\n{text}\n")
            index += 1
        t += duration
    return "\n".join(lines)


def _ts(seconds: float) -> str:
    ms = int(round((seconds - int(seconds)) * 1000))
    s = int(seconds)
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d},{ms:03d}"


def assemble_draft(project_id: str, only_approved: bool = False,
                   crossfade: bool = False, progress=None) -> dict:
    """
    Join the project's clips into one draft mp4 with ffmpeg.

    `crossfade` uses xfade between shots; otherwise hard cuts (the default —
    hard cuts are what the shot plan is designed around). Returns a result
    that always includes the metadata path, and `video_path: None` with a
    clear reason when assembly could not run — never a silent failure.
    """
    project = video_store.get_project(project_id)
    if not project:
        raise ValueError(f"Video project {project_id} not found.")
    shots = video_store.list_shots(project_id)
    usable = [s for s in shots
              if s.get("clip_path") and os.path.exists(s["clip_path"])
              and str(s["clip_path"]).lower().endswith(".mp4")
              and (s.get("approved") or not only_approved)]

    stem = f"video-{project_id}-{uuid.uuid4().hex[:6]}"
    meta = export_metadata(project_id)
    meta_path = OUTPUTS_DIR / f"{stem}.json"
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    srt = export_subtitles(project_id)
    srt_path = None
    if srt.strip():
        srt_path = OUTPUTS_DIR / f"{stem}.srt"
        srt_path.write_text(srt, encoding="utf-8")

    result = {
        "project_id": project_id,
        "metadata_path": str(meta_path),
        "subtitles_path": str(srt_path) if srt_path else None,
        "clip_count": len(usable),
        "video_path": None,
        "reason": "",
    }

    if not usable:
        result["reason"] = (
            "No finished .mp4 clips to assemble yet."
            + (" (Only approved shots were requested.)" if only_approved else "")
            + " Generate clips first — the metadata and subtitles were still exported."
        )
        video_store.update_project(project_id, export=result)
        return result

    ffmpeg = ffmpeg_path()
    if not ffmpeg:
        result["reason"] = (
            "ffmpeg is not installed or not on PATH, so the clips could not be joined. "
            "The shot plan, prompts, metadata and subtitles were exported, and every clip "
            "is available individually."
        )
        video_store.update_project(project_id, export=result)
        return result

    out_path = OUTPUTS_DIR / f"{stem}.mp4"
    if progress:
        progress(f"Joining {len(usable)} clips with ffmpeg...")

    try:
        if crossfade and len(usable) > 1:
            cmd = _xfade_command(ffmpeg, [s["clip_path"] for s in usable], out_path)
        else:
            list_file = OUTPUTS_DIR / f"{stem}-concat.txt"
            list_file.write_text(
                "\n".join(f"file '{Path(s['clip_path']).as_posix()}'" for s in usable),
                encoding="utf-8",
            )
            cmd = [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
                   "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "24", str(out_path)]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        if proc.returncode != 0 or not out_path.exists():
            result["reason"] = f"ffmpeg failed: {(proc.stderr or '')[-600:]}"
            video_store.update_project(project_id, export=result)
            return result
    except subprocess.TimeoutExpired:
        result["reason"] = "ffmpeg timed out while joining the clips."
        video_store.update_project(project_id, export=result)
        return result
    finally:
        for leftover in OUTPUTS_DIR.glob(f"{stem}-concat.txt"):
            leftover.unlink(missing_ok=True)

    result["video_path"] = str(out_path)
    result["reason"] = f"Joined {len(usable)} clips" + (" with crossfades." if crossfade else ".")
    video_store.add_asset(project_id, "draft_video", str(out_path),
                          meta={"clips": len(usable), "crossfade": crossfade})
    video_store.update_project(project_id, export=result, stage="export", status="complete")
    return result


def _xfade_command(ffmpeg: str, clips: list[str], out_path: Path,
                   fade: float = 0.4) -> list[str]:
    """
    Build an ffmpeg xfade chain. Offsets accumulate per clip minus the fade
    overlap; durations are probed so the chain lines up with the real files
    rather than the planned durations.
    """
    inputs: list[str] = []
    for clip in clips:
        inputs += ["-i", clip]
    durations = [_probe_duration(clip) for clip in clips]

    filters: list[str] = []
    label = "0:v"
    offset = max(0.0, durations[0] - fade)
    for i in range(1, len(clips)):
        out_label = f"v{i}"
        filters.append(
            f"[{label}][{i}:v]xfade=transition=fade:duration={fade}:offset={offset:.3f}[{out_label}]"
        )
        label = out_label
        offset += max(0.0, durations[i] - fade)
    return [ffmpeg, "-y", *inputs, "-filter_complex", ";".join(filters),
            "-map", f"[{label}]", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-r", "24", str(out_path)]


def _probe_duration(path: str, default: float = 3.5) -> float:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return default
    try:
        proc = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", path],
            capture_output=True, text=True, timeout=30,
        )
        return float((proc.stdout or "").strip() or default)
    except Exception:
        return default
