"""
Video Generation pipeline orchestration — the stages that actually produce
assets, sitting between the API routes and the individual agents.

Stages here:
  build_plan      — source → analysis → continuity bible → shot plan
  generate_frames — first/last frame per shot, honouring the continuity mode
  generate_clips  — the video clip per shot, via the provider adapter

Everything is RESUMABLE and IDEMPOTENT at shot granularity: each shot's
assets are written to video_store the moment they exist, and a re-run skips
shots that already have what they need unless `force` names them. Killing the
app mid-run therefore costs at most the shot in flight, never the project.

Frame conditioning (owner spec):
  continuous     — reuse the PREVIOUS shot's last frame as this shot's first
                   frame. No generation, perfect continuity, zero cost.
  editorial_cut  — generate a new first frame from the shot prompt plus the
                   locked continuity descriptions.
"""

import os
import uuid
from pathlib import Path

from agents import video_director, video_provider, video_safety, video_store
from agents.state import log_run

OUTPUTS_DIR = Path(__file__).parent.parent / "outputs"
OUTPUTS_DIR.mkdir(exist_ok=True)


class VideoPipelineError(RuntimeError):
    pass


# --- Stage: plan -------------------------------------------------------------

def build_plan(project_id: str, progress=None) -> dict:
    """
    Source → analysis → continuity bible → shots. Each step persists as soon
    as it completes, so a failure in shot planning never loses the analysis
    the user already paid for.
    """
    def note(msg):
        if progress:
            progress(msg)

    project = video_store.get_project(project_id)
    if not project:
        raise VideoPipelineError(f"Video project {project_id} not found.")

    source_text = (project.get("source_text") or "").strip()
    if not source_text:
        raise VideoPipelineError(
            "This project has no source text yet — paste a scene or story, or pick a "
            "bookmark or quote card, before planning shots."
        )
    direction = project.get("direction") or {}
    task_id = project.get("task_id") or ""

    note("Director is analysing the story (beats, characters, locations)...")
    analysis = video_director.analyze_story(
        source_text, direction, project.get("source_instructions") or "")
    video_store.update_project(project_id, analysis=analysis, stage="analysis")
    if task_id:
        log_run(task_id, "director", "analyze", source_text[:200],
                f"{len(analysis.get('beats', []))} beats, "
                f"{len(analysis.get('characters', []))} characters")

    safety = {"source_scan": analysis.get("sacred_flags", {}), "notes": []}
    if analysis.get("sacred_flags", {}).get("has_reference"):
        note("Sacred figures referenced — shots will use indirect treatment.")

    note("Director is writing the continuity bible...")
    continuity = video_director.build_continuity_bible(analysis, direction)
    video_store.update_project(project_id, continuity=continuity, stage="continuity")
    if task_id:
        log_run(task_id, "director", "continuity", "",
                f"{len(continuity.get('characters', []))} characters locked")

    beats = analysis.get("beats") or [{"id": "beat_1", "title": "Scene",
                                       "summary": source_text[:400], "suggested_shots": 4}]
    target = float(direction.get("target_seconds") or 60)
    pacing = video_director.pacing_of(direction)
    shot_seconds = video_director.shot_seconds_for(direction)
    total_wanted = video_director.shot_count_for(target, shot_seconds)

    all_shots: list[dict] = []
    notes: list[str] = []
    failed_beats: list[dict] = []

    # Cinematic pacing treats the clock's share as a CEILING and caps it at the
    # distinct moments a beat really contains, so the video comes in as long as
    # the story is rather than padded to the target with restatements.
    per_beat = video_director.beat_shot_budget(beats, total_wanted, pacing)
    if pacing == "cinematic" and sum(per_beat) < total_wanted:
        notes.append(
            f"Cinematic pacing: planning {sum(per_beat)} shots instead of padding to "
            f"{total_wanted} — the story has that many distinct moments, so the video "
            f"will run shorter than the {target:.0f}s target rather than repeating itself."
        )

    for i, (beat, want) in enumerate(zip(beats, per_beat), start=1):
        note(f"Director is planning beat {i}/{len(beats)}: {beat.get('title', '')[:40]}...")
        try:
            shots = video_director.plan_beat_shots(beat, analysis, continuity, direction, want)
        except Exception as e:
            # One failed beat must NOT discard the whole plan. This happened for
            # real: beat 2 of 7 hit an Ollama read timeout and six beats of
            # already-completed work were thrown away (2026-08-12). Instead the
            # beat becomes a clearly-marked placeholder the user can regenerate
            # or edit, and planning carries on.
            message = str(e)[:300]
            failed_beats.append({"beat": i, "title": beat.get("title", ""), "error": message})
            notes.append(
                f"Beat {i} ('{beat.get('title', '')}') could not be planned: {message}. "
                f"A placeholder shot was inserted — use Regenerate on it, or edit it by hand."
            )
            all_shots.append(_placeholder_shot(beat, shot_seconds, message))
            note(f"Beat {i} failed ({message[:60]}) — continuing with a placeholder.")
            continue
        all_shots += shots

    # Every shot's duration is forced into the 3-4s window regardless of what
    # the model returned (the constraint the whole design rests on).
    for shot in all_shots:
        shot["duration"] = video_director.clamp_duration(shot.get("duration", shot_seconds))

    # Deduplicate BEFORE splitting: the comparison should see the planner's own
    # actions, not the halves a complexity split has already carved them into.
    if pacing == "cinematic":
        note("Removing shots that film the same moment twice...")
        all_shots, dedupe_notes = video_director.dedupe_shots(all_shots)
        notes += dedupe_notes

    note("Splitting any shot too complex for a small model...")
    all_shots, split_notes = video_director.split_complex_shots(all_shots)
    notes += split_notes

    # After splitting, so the halves are covered too. Sets continuity_mode,
    # which repair_motion then reads when matching a continuous shot's camera.
    if pacing == "cinematic":
        note("Cutting only where the story actually changes...")
        all_shots, cut_notes = video_director.enforce_cut_policy(all_shots)
        notes += cut_notes

    # Runs AFTER splitting so the halves are checked too, and after durations
    # are clamped so the pace sentence matches the real clip length.
    note("Checking that every shot describes a real movement of its own...")
    all_shots, motion_notes = video_director.repair_motion(all_shots)
    notes += motion_notes

    # Standard pacing only WARNS about repeats — removing a shot the owner may
    # have meant is a story decision, and cinematic pacing is where it is made.
    if pacing != "cinematic":
        notes += video_director.repeated_action_warnings(all_shots)

    note("Applying the sacred-figure safeguard...")
    all_shots, safety_notes = video_safety.enforce_shots(all_shots)
    notes += safety_notes
    safety["notes"] = safety_notes

    video_store.replace_shots(project_id, all_shots)
    video_store.update_project(project_id, stage="shots",
                               status="planned" if not failed_beats else "planned_with_gaps",
                               safety=safety)
    if task_id:
        log_run(task_id, "director", "shot_plan", f"{len(beats)} beats",
                f"{len(all_shots)} shots planned"
                + (f", {len(failed_beats)} beat(s) failed" if failed_beats else ""))

    cuts = sum(1 for s in all_shots if s.get("continuity_mode") != "continuous")
    estimated = round(sum(s.get("duration", 3.5) for s in all_shots), 1)
    return {
        "project_id": project_id,
        "analysis": analysis,
        "continuity": continuity,
        "shot_count": len(all_shots),
        "notes": notes,
        "safety": safety,
        "failed_beats": failed_beats,
        "estimated_seconds": estimated,
        "pacing": pacing,
        # What the viewer actually feels: chained `continuous` shots play as one
        # unbroken take, so cut COUNT — not shot count — is the pacing the user
        # perceives. Reported so the difference between the modes is visible.
        "cut_count": cuts,
        "seconds_per_cut": round(estimated / cuts, 1) if cuts else estimated,
    }


def repair_project_motion(project_id: str, recut: bool = False) -> dict:
    """
    Run the deterministic repairs over a project that was ALREADY planned.

    `build_plan` applies these to new plans, but a project planned before the
    repairs existed keeps its defects on disk, and re-planning would throw away
    every hand edit (and cost another 10-15 minutes of local LLM time). This is
    the same pass applied in place: no LLM call, no cost, every change reported.

    `recut` additionally applies the cinematic CUT POLICY — cuts only at beat
    boundaries and real changes of place or time. That is where the pacing a
    viewer actually feels comes from: measured on a real 35-shot project it
    turned 26 cuts into 10, i.e. a cut every 11.2s instead of every 4.7s.

    Both passes are NON-DESTRUCTIVE: no shot is ever deleted. Duplicated
    moments stay as warnings, because deleting a shot that may already have a
    rendered clip is the owner's call, not this function's. Locked fields are
    respected — a description the owner locked is theirs (rule 33).
    """
    project = video_store.get_project(project_id)
    if not project:
        raise VideoPipelineError(f"Video project {project_id} not found.")

    shots = video_store.list_shots(project_id)
    if not shots:
        raise VideoPipelineError("This project has no shots to repair yet.")

    before = [dict(s["data"] or {}, continuity_mode=s.get("continuity_mode")) for s in shots]

    notes: list[str] = []
    working = [dict(s) for s in before]
    if recut:
        working, cut_notes = video_director.enforce_cut_policy(working)
        notes += cut_notes
    # Runs after the cut policy so a shot it made `continuous` also gets its
    # camera matched to the frame it now reuses.
    repaired, motion_notes = video_director.repair_motion(working)
    notes += motion_notes

    changed = 0
    recut_count = 0
    for shot, new_data, old_data in zip(shots, repaired, before):
        diff = {k: v for k, v in new_data.items()
                if k in ("motion_prompt", "framing", "camera_angle") and v != old_data.get(k)}
        mode = new_data.get("continuity_mode")
        mode_changed = recut and mode and mode != old_data.get("continuity_mode")
        if not diff and not mode_changed:
            continue
        if diff:
            video_store.update_shot(shot["id"], data=diff)
        if mode_changed:
            video_store.update_shot(shot["id"], continuity_mode=mode)
            recut_count += 1
        changed += 1

    cuts = sum(1 for s in repaired if s.get("continuity_mode") != "continuous")
    seconds = sum(float(s.get("duration") or 3.5) for s in repaired)
    warnings = video_director.repeated_action_warnings(repaired)
    if project.get("task_id"):
        # Mechanical, not a judged outcome (rule 14).
        log_run(project["task_id"], "director", "repair_motion", "",
                f"{changed} shot(s) repaired, {recut_count} recut, "
                f"{len(warnings)} repeat warning(s)")
    return {"project_id": project_id, "shots_changed": changed,
            "notes": notes, "warnings": warnings, "total_shots": len(shots),
            "recut": bool(recut), "shots_recut": recut_count,
            "cut_count": cuts,
            "seconds_per_cut": round(seconds / cuts, 1) if cuts else round(seconds, 1)}


def _placeholder_shot(beat: dict, shot_seconds: float, error: str) -> dict:
    """
    Stand-in for a beat the Director couldn't plan. Deliberately honest: it is
    marked `needs_replanning` and describes the beat in plain terms rather than
    pretending to be a finished shot, so it shows up in the storyboard as
    something to fix instead of silently rendering as a generic image.
    """
    summary = str(beat.get("summary") or beat.get("title") or "this moment").strip()
    return {
        "beat_id": beat.get("id", ""),
        "duration": video_director.clamp_duration(shot_seconds),
        "narrative_purpose": f"PLACEHOLDER for beat '{beat.get('title', '')}'",
        "subject": summary[:120],
        "primary_action": "holds still",
        "setting": "", "time_of_day": "", "framing": "medium",
        "camera_angle": "eye level", "camera_movement": "static",
        "lighting": "", "mood": beat.get("emotion", ""),
        "character_ids": [], "location_ids": [], "prop_ids": [],
        "first_frame_prompt": summary[:300],
        "last_frame_prompt": summary[:300],
        "motion_prompt": "a slow, steady camera push; minimal motion",
        "negative_prompt": video_director.DEFAULT_NEGATIVE,
        "narration": "", "dialogue": "", "sound_notes": "",
        "transition": "cut", "continuity_mode": "editorial_cut",
        "continuity_notes": f"Auto-placeholder: planning failed ({error[:160]})",
        "needs_replanning": True,
        "complexity_score": 0,
    }


# --- Stage: frames -----------------------------------------------------------

def _safe_shot_data(shot: dict) -> tuple[dict, list[str]]:
    """
    The sacred-figure safeguard applied at the RENDER boundary (rule 30).

    Planning runs `video_safety.enforce_shots`, and adding a shot by hand runs
    `enforce_shot` — but neither covers a shot EDITED afterwards through
    `PATCH /video/shots/{id}`, which merges arbitrary text straight into the
    stored plan. Enforcing here instead of at each entrance means no edit path,
    present or future, can put a Manifestation into a visual prompt: this is
    the last code that runs before the prompt is built, exactly as
    `_sanitize_claims` sits on the listing path (rule 4) rather than trusting
    every caller to remember.

    Narration is untouched, as always — naming Them with reverence in narration
    is the intended outcome, not a violation.
    """
    data = shot.get("data") if isinstance(shot.get("data"), dict) else shot
    return video_safety.enforce_shot(data or {})


def _generate_still(prompt: str, aspect_ratio: str = "16:9") -> str:
    """
    One still frame via the existing Artist image path (xAI) — the repo's
    established image provider, metered at its own chokepoint. Kept in one
    place so swapping in a local image model later touches only this function.
    """
    from agents.artist import generate_image
    result = generate_image(prompt, aspect_ratio)
    return result.get("image_url", "")


def _aspect_for(direction: dict) -> str:
    value = str((direction or {}).get("aspect_ratio") or "16:9")
    return {"16:9": "16:9", "9:16": "9:16", "1:1": "1:1", "4:5": "4:5"}.get(value, "16:9")


def generate_frames(project_id: str, shot_ids: list[str] | None = None,
                    force: bool = False, progress=None, should_cancel=None) -> dict:
    """
    Generate first/last frames for the project's shots.

    Resumable: a shot that already has its frames is skipped unless `force`.
    `shot_ids` limits the run to a subset (partial regeneration) — the rest of
    the project is untouched.
    """
    project = video_store.get_project(project_id)
    if not project:
        raise VideoPipelineError(f"Video project {project_id} not found.")
    direction = project.get("direction") or {}
    continuity = project.get("continuity") or {}
    aspect = _aspect_for(direction)
    task_id = project.get("task_id") or ""

    shots = video_store.list_shots(project_id)
    if shot_ids:
        wanted = set(shot_ids)
        targets = [s for s in shots if s["id"] in wanted]
    else:
        targets = shots
    if not targets:
        raise VideoPipelineError("No shots to generate frames for — plan the shots first.")

    job_id = video_store.start_job(project_id, "frames", total=len(targets))
    done = 0
    errors: list[dict] = []

    for shot in targets:
        if should_cancel and should_cancel():
            video_store.update_job(job_id, status="interrupted",
                                   cursor=shot["id"], done=done)
            return {"job_id": job_id, "cancelled": True, "done": done,
                    "total": len(targets), "errors": errors}

        # Rule 30 at the render boundary — covers shots edited by hand after
        # planning, which no earlier enforcement point sees.
        data, safety_notes = _safe_shot_data(shot)
        if safety_notes and progress:
            for note_text in safety_notes:
                progress(f"Shot {shot['shot_number']}: {note_text}")
        number = shot["shot_number"]
        have_first = bool(shot.get("first_frame_path"))
        have_last = bool(shot.get("last_frame_path"))
        if have_first and have_last and not force:
            done += 1
            video_store.update_job(job_id, cursor=shot["id"], done=done)
            continue

        if progress:
            progress(f"Shot {number}/{len(shots)}: generating frames...")
        video_store.update_job(job_id, cursor=shot["id"])

        try:
            # --- first frame ---
            first_path = shot.get("first_frame_path")
            mode = shot.get("continuity_mode") or data.get("continuity_mode") or "editorial_cut"
            reused = False
            if force or not first_path:
                prev = _previous_shot(shots, number)
                if mode == "continuous" and prev and prev.get("last_frame_path"):
                    # Continuous movement: literally the same image, so subject
                    # position, costume, lighting and screen direction cannot drift.
                    first_path = prev["last_frame_path"]
                    reused = True
                else:
                    prompt = video_director.build_frame_prompt(data, continuity, direction, "first")
                    first_path = _generate_still(prompt, aspect)
                    video_store.add_asset(project_id, "first_frame", first_path,
                                          shot_id=shot["id"], prompt=prompt, model="xai",
                                          meta={"mode": mode})

            # --- last frame ---
            last_path = shot.get("last_frame_path")
            if force or not last_path:
                prompt = video_director.build_frame_prompt(data, continuity, direction, "last")
                last_path = _generate_still(prompt, aspect)
                video_store.add_asset(project_id, "last_frame", last_path,
                                      shot_id=shot["id"], prompt=prompt, model="xai")

            video_store.update_shot(
                shot["id"], first_frame_path=first_path, last_frame_path=last_path,
                status="frames_ready", error=None,
            )
            if reused and progress:
                progress(f"Shot {number}: reused shot {number - 1}'s last frame (continuous).")
        except Exception as e:
            message = str(e)[:400]
            video_store.update_shot(shot["id"], status="error", error=message)
            errors.append({"shot_id": shot["id"], "shot_number": number, "error": message})
            if progress:
                progress(f"Shot {number}: frame generation FAILED — {message}")
        done += 1
        video_store.update_job(job_id, done=done)

    video_store.update_job(job_id, status="done" if not errors else "partial",
                           done=done, error=(f"{len(errors)} shot(s) failed" if errors else None))
    video_store.update_project(project_id, stage="frames")
    if task_id:
        # Mechanical outcome — rule 14: no passed_review, this is not a judgement.
        log_run(task_id, "videographer", "frames", f"{len(targets)} shots",
                f"{done - len(errors)} ok, {len(errors)} failed")
    return {"job_id": job_id, "done": done, "total": len(targets), "errors": errors,
            "cancelled": False}


def _previous_shot(shots: list[dict], number: int) -> dict | None:
    for s in shots:
        if s["shot_number"] == number - 1:
            return s
    return None


# --- Stage: clips ------------------------------------------------------------

def generate_clips(project_id: str, shot_ids: list[str] | None = None, force: bool = False,
                   provider_id: str | None = None, progress=None, should_cancel=None) -> dict:
    """
    Generate the video clip for each shot through the provider adapter.

    Sequential by design: on an 8GB card two clips in flight is an
    out-of-memory error, not throughput. Resumable and idempotent per shot.
    The chosen fallback strategy is recorded on every asset so the UI can show
    exactly how each clip was produced.
    """
    project = video_store.get_project(project_id)
    if not project:
        raise VideoPipelineError(f"Video project {project_id} not found.")
    direction = project.get("direction") or {}
    task_id = project.get("task_id") or ""
    low_resource = bool(direction.get("low_resource", True))

    provider = video_provider.get_provider(provider_id or direction.get("provider"))
    caps = provider.capabilities()
    if not caps.get("available"):
        raise VideoPipelineError(caps.get("unavailable_reason") or "Video provider unavailable.")

    shots = video_store.list_shots(project_id)
    targets = [s for s in shots if s["id"] in set(shot_ids)] if shot_ids else shots
    if not targets:
        raise VideoPipelineError("No shots to generate clips for.")

    job_id = video_store.start_job(project_id, "clips", total=len(targets))
    done = 0
    errors: list[dict] = []
    strategies: list[str] = []

    for shot in targets:
        if should_cancel and should_cancel():
            video_store.update_job(job_id, status="interrupted", cursor=shot["id"], done=done)
            return {"job_id": job_id, "cancelled": True, "done": done,
                    "total": len(targets), "errors": errors, "provider": caps}

        data, safety_notes = _safe_shot_data(shot)   # rule 30, render boundary
        if safety_notes and progress:
            for note_text in safety_notes:
                progress(f"Shot {shot['shot_number']}: {note_text}")
        number = shot["shot_number"]
        if shot.get("clip_path") and not force:
            done += 1
            video_store.update_job(job_id, cursor=shot["id"], done=done)
            continue

        first_frame = shot.get("first_frame_path")
        last_frame = shot.get("last_frame_path")
        try:
            plan = video_provider.resolve_strategy(caps, bool(first_frame), bool(last_frame))
        except video_provider.VideoProviderError as e:
            message = str(e)[:400]
            video_store.update_shot(shot["id"], status="error", error=message)
            errors.append({"shot_id": shot["id"], "shot_number": number, "error": message})
            done += 1
            continue

        strategies.append(plan["strategy"])
        if progress:
            progress(f"Shot {number}/{len(shots)}: generating clip "
                     f"({plan['label']}, ~{caps.get('typical_seconds_per_clip', 0)}s)...")
        video_store.update_job(job_id, cursor=shot["id"])

        spec = video_provider.ClipSpec(
            prompt=video_director.build_motion_prompt(data, direction),
            negative_prompt=video_safety.guard_negative_prompt(data.get("negative_prompt", "")),
            first_frame=first_frame,
            last_frame=last_frame if plan["strategy"] == video_provider.STRATEGY_NATIVE_FLF else None,
            seconds=video_director.clamp_duration(data.get("duration", 3.5)),
            low_resource=low_resource,
        )
        try:
            result = provider.generate_clip(spec, progress=None,
                                            should_cancel=should_cancel)
        except video_provider.VideoProviderError as e:
            message = str(e)[:400]
            video_store.update_shot(shot["id"], status="error", error=message)
            errors.append({"shot_id": shot["id"], "shot_number": number, "error": message})
            done += 1
            video_store.update_job(job_id, done=done)
            continue
        except Exception as e:
            if type(e).__name__ == "VideoCancelled":
                video_store.update_job(job_id, status="interrupted", cursor=shot["id"], done=done)
                return {"job_id": job_id, "cancelled": True, "done": done,
                        "total": len(targets), "errors": errors, "provider": caps}
            raise

        clip_path = str(result["video_path"])
        video_store.add_asset(
            project_id, "clip", clip_path, shot_id=shot["id"], prompt=spec.prompt,
            seed=result.get("seed"), model=str(result.get("model", "")),
            meta={"strategy": plan["strategy"], "strategy_label": plan["label"],
                  "why": plan["why"], "is_mock": bool(result.get("is_mock")),
                  "last_frame_used": bool(result.get("last_frame_used"))},
        )
        update: dict = {"clip_path": clip_path, "status": "clip_ready", "error": None}

        # chain_extract: carry this clip's REAL final frame into the next shot,
        # rather than assuming it matched the planned last frame.
        if plan["strategy"] == video_provider.STRATEGY_CHAIN_EXTRACT:
            from agents import videographer
            extracted = videographer.extract_last_frame(clip_path)
            if extracted:
                update["last_frame_path"] = str(extracted)
                video_store.add_asset(project_id, "last_frame", str(extracted),
                                      shot_id=shot["id"], model="extracted",
                                      meta={"source": "clip_final_frame"})
        video_store.update_shot(shot["id"], **update)
        done += 1
        video_store.update_job(job_id, done=done)

    video_store.update_job(job_id, status="done" if not errors else "partial", done=done,
                           error=(f"{len(errors)} shot(s) failed" if errors else None))
    video_store.update_project(project_id, stage="clips")
    if task_id:
        # Mechanical (rule 14): "the render returned a file" is not a judgement.
        log_run(task_id, "videographer", "clips", f"{len(targets)} shots",
                f"{done - len(errors)} ok, {len(errors)} failed")
    return {"job_id": job_id, "done": done, "total": len(targets), "errors": errors,
            "provider": caps, "strategies": sorted(set(strategies)), "cancelled": False}


# --- Stage: chained generation (frames and clips interleaved) ----------------

def chain_preflight(project_id: str, provider_id: str | None = None) -> tuple[bool, str]:
    """
    Everything that must be true BEFORE a chained run starts, as (ok, reason).

    Split out of `generate_chained` so the API can check it synchronously and
    answer the button press with a real error, instead of returning a job id
    for a run that dies immediately in a worker thread.
    """
    project = video_store.get_project(project_id)
    if not project:
        return False, f"Video project {project_id} not found."

    if not video_store.list_shots(project_id):
        return False, ("There are no shots to generate yet — plan the shots first on the "
                       "Direction tab.")

    # Chaining IS frame extraction. Without PyAV every link would silently fall
    # back to an independently-generated shot, i.e. exactly the disconnected
    # result chaining exists to fix — so refuse rather than fake it.
    try:
        import av  # noqa: F401
    except ImportError:
        return False, ("Chained generation needs the 'av' package to read each clip's "
                       "final frame (pip install av). Without it every shot would be "
                       "generated independently, which is the disconnected look "
                       "chaining exists to remove.")

    direction = project.get("direction") or {}
    try:
        provider = video_provider.get_provider(provider_id or direction.get("provider"))
    except Exception as e:
        return False, str(e)
    caps = provider.capabilities()
    if not caps.get("available"):
        return False, (caps.get("unavailable_reason")
                       or "The video provider is not available right now.")
    if not caps.get("image_to_video"):
        return False, (f"{caps.get('label', 'This provider')} cannot do image-to-video, so "
                       "it cannot chain shots together. Pick a provider that supports "
                       "image-to-video.")
    return True, ""


def generate_chained(project_id: str, provider_id: str | None = None, adapt: bool = True,
                     force: bool = False, progress=None, should_cancel=None) -> dict:
    """
    Generate the whole video as ONE CONTINUOUS CHAIN instead of independently.

    The independent path (generate_frames then generate_clips) renders every
    shot from its own text prompt, so each shot invents its own version of the
    character and place — which is why a finished video reads as a series of
    disconnected slides rather than a scene. This path instead threads the real
    output forward:

        shot 1: first frame from text  ->  clip  ->  EXTRACT its real last frame
        shot 2: that extracted frame IS the first frame  ->  clip  ->  extract
        shot 3: ...

    So each clip is built on the actual pixels the previous clip ended on, not
    on a fresh guess at what the plan meant.

    At an EDITORIAL CUT the frame cannot simply be reused (the angle or place
    changes on purpose), so a new first frame is generated — but with `adapt`
    it is anchored by a vision reading of what the previous clip really showed,
    so identity, wardrobe, setting and light carry across the cut. That reading
    is a paid call; `adapt=False` skips it and the chain still works, just with
    weaker carry-over across cuts.

    Sequential and resumable: a shot that already has a clip is skipped (unless
    `force`), and the chain picks up from that shot's real final frame.
    """
    project = video_store.get_project(project_id)
    if not project:
        raise VideoPipelineError(f"Video project {project_id} not found.")
    direction = project.get("direction") or {}
    continuity = project.get("continuity") or {}
    task_id = project.get("task_id") or ""
    aspect = _aspect_for(direction)

    ok, reason = chain_preflight(project_id, provider_id)
    if not ok:
        raise VideoPipelineError(reason)

    provider = video_provider.get_provider(provider_id or direction.get("provider"))
    caps = provider.capabilities()
    shots = video_store.list_shots(project_id)

    job_id = video_store.start_job(project_id, "chain", total=len(shots))
    done = 0
    errors: list[dict] = []
    notes: list[str] = []
    carry_frame: str | None = None      # the real final frame of the previous clip
    observed: dict = {}                 # what that frame actually showed

    from agents import videographer

    for shot in shots:
        if should_cancel and should_cancel():
            video_store.update_job(job_id, status="interrupted", cursor=shot["id"], done=done)
            return {"job_id": job_id, "cancelled": True, "done": done, "total": len(shots),
                    "errors": errors, "notes": notes, "provider": caps, "chained": True}

        data, safety_notes = _safe_shot_data(shot)   # rule 30, render boundary
        for note_text in safety_notes:
            notes.append(f"Shot {shot['shot_number']}: {note_text}")
        number = shot["shot_number"]
        mode = shot.get("continuity_mode") or "editorial_cut"

        # Already rendered: keep it, but pick the chain back up from its real
        # final frame so the following shot still continues from reality.
        if shot.get("clip_path") and not force:
            existing = shot.get("last_frame_path")
            if not existing or not os.path.exists(str(existing)):
                existing = videographer.extract_last_frame(shot["clip_path"])
                existing = str(existing) if existing else None
            carry_frame = existing or carry_frame
            observed = {}
            done += 1
            video_store.update_job(job_id, cursor=shot["id"], done=done)
            continue

        video_store.update_job(job_id, cursor=shot["id"])
        try:
            # ---- 1. decide this shot's first frame ----
            # True only when this clip literally resumes the previous one with
            # no cut — it decides whether the motion prompt says "carry this on"
            # or "render this shot", which are very different instructions.
            continues = False
            if carry_frame and mode == "continuous":
                first_frame = carry_frame
                continues = True
                how = "continued from the previous clip's final frame"
            elif carry_frame:
                if progress:
                    progress(f"Shot {number}/{len(shots)}: new angle — matching it to the "
                             f"previous clip...")
                if adapt and not observed:
                    observed = video_director.observe_frame(carry_frame)
                prompt = video_director.build_continuation_prompt(
                    data, continuity, direction, observed)
                first_frame = _generate_still(prompt, aspect)
                video_store.add_asset(project_id, "first_frame", first_frame,
                                      shot_id=shot["id"], prompt=prompt, model="xai",
                                      meta={"chained": True, "mode": "editorial_cut",
                                            "anchored_to_previous": bool(observed)})
                how = ("new frame for the cut, matched to what the previous clip showed"
                       if observed else "new frame for the cut")
            else:
                # First shot of the chain (or resuming with nothing behind it).
                first_frame = shot.get("first_frame_path")
                if force or not first_frame or not os.path.exists(str(first_frame)):
                    if progress:
                        progress(f"Shot {number}/{len(shots)}: generating the opening frame...")
                    prompt = video_director.build_frame_prompt(
                        data, continuity, direction, "first")
                    first_frame = _generate_still(prompt, aspect)
                    video_store.add_asset(project_id, "first_frame", first_frame,
                                          shot_id=shot["id"], prompt=prompt, model="xai",
                                          meta={"chained": True, "mode": "opening"})
                how = "opening frame"

            video_store.update_shot(shot["id"], first_frame_path=str(first_frame),
                                    status="frames_ready", error=None)

            # ---- 2. render the clip from that frame ----
            if progress:
                progress(f"Shot {number}/{len(shots)}: rendering clip ({how}, "
                         f"~{caps.get('typical_seconds_per_clip', 0)}s)...")
            spec = video_provider.ClipSpec(
                prompt=video_director.build_motion_prompt(
                    data, direction, continues=continues, observed=observed or None),
                negative_prompt=video_safety.guard_negative_prompt(data.get("negative_prompt", "")),
                first_frame=str(first_frame),
                last_frame=None,          # chaining never relies on end-frame conditioning
                seconds=video_director.clamp_duration(data.get("duration", 3.5)),
                low_resource=bool(direction.get("low_resource", True)),
                # Maximum adherence to the handed-in frame: the whole point of
                # chaining is that the clip STARTS on the previous clip's last
                # frame. Providers that ignore this simply keep their default.
                image_strength=1.0,
            )
            result = provider.generate_clip(spec, should_cancel=should_cancel)
            clip_path = str(result["video_path"])
            video_store.add_asset(
                project_id, "clip", clip_path, shot_id=shot["id"], prompt=spec.prompt,
                seed=result.get("seed"), model=str(result.get("model", "")),
                meta={"strategy": "chained", "how": how,
                      "is_mock": bool(result.get("is_mock"))},
            )

            # ---- 3. extract the REAL final frame and carry it forward ----
            extracted = videographer.extract_last_frame(clip_path)
            update = {"clip_path": clip_path, "status": "clip_ready", "error": None}
            if extracted:
                update["last_frame_path"] = str(extracted)
                video_store.add_asset(project_id, "last_frame", str(extracted),
                                      shot_id=shot["id"], model="extracted",
                                      meta={"source": "clip_final_frame", "chained": True})
                carry_frame = str(extracted)
                observed = {}     # stale now; re-read only if the next shot is a cut
            else:
                # No PyAV: the chain can't continue from real pixels. Say so
                # rather than silently falling back to disconnected shots.
                carry_frame = None
                notes.append(
                    f"Shot {number}: could not read the clip's final frame (install the "
                    f"'av' package to enable chaining), so the next shot starts fresh."
                )
            video_store.update_shot(shot["id"], **update)

        except Exception as e:
            if type(e).__name__ == "VideoCancelled":
                video_store.update_job(job_id, status="interrupted", cursor=shot["id"], done=done)
                return {"job_id": job_id, "cancelled": True, "done": done, "total": len(shots),
                        "errors": errors, "notes": notes, "provider": caps, "chained": True}
            message = str(e)[:400]
            video_store.update_shot(shot["id"], status="error", error=message)
            errors.append({"shot_id": shot["id"], "shot_number": number, "error": message})
            if progress:
                progress(f"Shot {number}: FAILED — {message[:80]}")
            # Break the chain rather than continuing from a stale frame: the
            # following shot would appear to continue from a shot that doesn't
            # exist, which is worse than an honest restart.
            carry_frame = None
            observed = {}
        done += 1
        video_store.update_job(job_id, done=done)

    video_store.update_job(job_id, status="done" if not errors else "partial", done=done,
                           error=(f"{len(errors)} shot(s) failed" if errors else None))
    video_store.update_project(project_id, stage="clips")
    if task_id:
        # Mechanical (rule 14) — a render returning a file is not a judgement.
        log_run(task_id, "videographer", "chain", f"{len(shots)} shots",
                f"{done - len(errors)} ok, {len(errors)} failed")
    return {"job_id": job_id, "done": done, "total": len(shots), "errors": errors,
            "notes": notes, "provider": caps, "chained": True, "cancelled": False}


def resume_state(project_id: str) -> dict:
    """
    What's left to do — powers the UI's Resume affordance after a crash or a
    deliberate close. Derived from the shots themselves, so it is accurate
    even if a job row was lost.
    """
    shots = video_store.list_shots(project_id)
    need_frames = [s["id"] for s in shots if not (s.get("first_frame_path") and s.get("last_frame_path"))]
    need_clips = [s["id"] for s in shots if not s.get("clip_path")]
    failed = [s["id"] for s in shots if s.get("status") == "error"]
    return {
        "shots": len(shots),
        "needs_frames": need_frames,
        "needs_clips": need_clips,
        "failed": failed,
        "frames_job": video_store.latest_job(project_id, "frames"),
        "clips_job": video_store.latest_job(project_id, "clips"),
        "complete": bool(shots) and not need_frames and not need_clips,
    }
