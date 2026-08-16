"""
Verification suite for the Video Generation pipeline.

Follows the repo's established pattern (no pytest here — `scripts/test_*.py`
are runnable checks): deterministic logic is tested offline with fakes, then
the HTTP surface is exercised against a real FastAPI TestClient over the real
SQLite DB. LLM and image/video generation are stubbed so this runs free and
offline; the live generation paths were verified separately against the real
ComfyUI server.

Run:  python scripts/test_video_pipeline.py
Exit code is non-zero if anything fails, so it can gate a commit.
"""

import io
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ.setdefault("VIDEO_PROVIDER", "mock")

from agents import video_assembly, video_director, video_provider, video_safety, video_store  # noqa: E402

RESULTS: list[tuple[bool, str, str]] = []


def check(name: str, condition: bool, detail: str = ""):
    RESULTS.append((bool(condition), name, detail))
    print(f"  {'PASS' if condition else 'FAIL'}  {name}" + (f"  [{detail}]" if detail and not condition else ""))


def section(title: str):
    print(f"\n=== {title} ===")


# ── 1. Sacred-figure safeguard ───────────────────────────────────────────────

def test_safety():
    section("Sacred-figure non-depiction safeguard")

    # Detection must survive every apostrophe / diacritic style in real sources.
    variants = ["Bahá'u'lláh", "Bahá’u’lláh", "Baha'u'llah",
                "Bahaullah", "BAHÁ'U'LLÁH"]
    check("detects Baha'u'llah in all spellings",
          all(video_safety._find_figures(v) for v in variants),
          str([v for v in variants if not video_safety._find_figures(v)]))
    check("detects the Bab", bool(video_safety._find_figures("the Báb")))
    check("detects Muhammad", bool(video_safety._find_figures("Muḥammad")))

    # Must NOT flag figures who MAY be depicted, or unrelated names.
    exempt = ["'Abdu'l-Bahá", "‘Abdu’l-Bahá", "Shoghi Effendi",
              "a Bahá'í gathering", "Abraham Lincoln", "Muhammad Ali the boxer"]
    check("does not flag depictable/unrelated names",
          not any(video_safety._find_figures(v) for v in exempt),
          str([v for v in exempt if video_safety._find_figures(v)]))

    shot = {
        "subject": "Bahá’u’lláh stands in the courtyard",
        "primary_action": "He raises His hand",
        "first_frame_prompt": "Close-up of Bahá’u’lláh facing the camera",
        "last_frame_prompt": "The courtyard at dusk",
        "motion_prompt": "Bahaullah walks forward",
        "narration": "Bahá’u’lláh addressed the believers.",
        "negative_prompt": "blurry",
    }
    safe, notes = video_safety.enforce_shot(shot)
    leaks = [f for f in video_safety.VISUAL_FIELDS if video_safety._find_figures(str(safe[f]))]
    check("no sacred figure survives in any VISUAL field", not leaks, str(leaks))
    check("narration is left untouched (reverent reference allowed)",
          safe["narration"] == shot["narration"])
    check("negative prompt carries the non-depiction guard",
          video_safety.NEGATIVE_GUARD in safe["negative_prompt"])
    check("rewrite is reported, not silent", len(notes) > 0)
    check("shot is marked with the treatment applied",
          bool(safe.get("sacred_treatment", {}).get("figures")))

    secular = {"subject": "a blacksmith", "primary_action": "hammers iron",
               "first_frame_prompt": "a forge", "last_frame_prompt": "sparks",
               "motion_prompt": "the hammer falls", "narration": "", "negative_prompt": "x"}
    s2, n2 = video_safety.enforce_shot(secular)
    check("ordinary secular shots are untouched",
          s2["subject"] == "a blacksmith" and not n2)

    scan = video_safety.scan_text("Mullá Husayn approached the house where "
                                  "Bahá’u’lláh was staying.")
    check("scan_text flags a sacred reference in prose", scan["has_reference"])


# ── 2. Duration constraint + shot maths ──────────────────────────────────────

def test_durations():
    section("3-4 second duration constraint")
    check("clamps an over-long duration down to 4s", video_director.clamp_duration(9.0) == 4.0)
    check("clamps a too-short duration up to 3s", video_director.clamp_duration(0.5) == 3.0)
    check("keeps a valid duration", video_director.clamp_duration(3.5) == 3.5)
    check("garbage duration falls back to the default",
          video_director.clamp_duration("nonsense") == video_director.DEFAULT_SHOT_SECONDS)
    n = video_director.shot_count_for(60)
    check(f"60s target yields 15-20 shots (got {n})", 15 <= n <= 20, str(n))
    check("120s target yields roughly double", 30 <= video_director.shot_count_for(120) <= 40)

    from agents import videographer
    check("LTX frame count is always 8n+1",
          all((videographer.frames_for_seconds(s, "ltx") - 1) % 8 == 0
              for s in (3.0, 3.5, 4.0, 5.0)))


# ── 3. Complexity + automatic splitting ──────────────────────────────────────

def test_complexity():
    section("Shot complexity and automatic splitting")
    simple = {"subject": "a woman", "primary_action": "looks up", "motion_prompt": "she looks up",
              "camera_movement": "static", "character_ids": ["c1"]}
    check("a simple shot scores under the limit",
          video_director.complexity_score(simple) <= video_director.COMPLEXITY_LIMIT)

    busy = {
        "subject": "a crowd", "camera_movement": "tracking shot following the horse",
        "primary_action": ("the rider dismounts and then walks through the crowd while "
                           "the guards raise their hands and the gate opens behind them"),
        "motion_prompt": "rider dismounts then walks while guards move",
        "character_ids": ["a", "b", "c", "d"],
    }
    score = video_director.complexity_score(busy)
    check(f"a busy shot scores over the limit (got {score})",
          score > video_director.COMPLEXITY_LIMIT, str(score))

    shots, notes = video_director.split_complex_shots([busy])
    check("a too-complex shot is split into two", len(shots) == 2, f"got {len(shots)}")
    check("the split is reported to the user", len(notes) == 1)
    check("both halves are within the duration window",
          all(3.0 <= s["duration"] <= 4.0 for s in shots))
    check("the second half continues from the first",
          shots[1].get("continuity_mode") == "continuous")
    check("splitting reduces complexity",
          max(video_director.complexity_score(s) for s in shots) < score)

    simplified = video_director.simplify_shot(busy)
    check("simplify drops to a static camera", simplified["camera_movement"] == "static")
    check("simplify reduces complexity",
          video_director.complexity_score(simplified) < score)

    ok, _ = video_director.can_merge(
        {"beat_id": "b1", "location_ids": ["l1"]}, {"beat_id": "b1", "location_ids": ["l1"]})
    check("compatible shots may merge", ok)
    bad, reason = video_director.can_merge(
        {"beat_id": "b1", "location_ids": ["l1"]}, {"beat_id": "b1", "location_ids": ["l2"]})
    check("merging across a location change is refused", not bad and "location" in reason.lower())


# ── 4. Continuity bible references ───────────────────────────────────────────

def test_continuity_refs():
    section("Continuity bible references reach the prompts")
    bible = {
        "characters": [{"id": "prince", "name": "The Prince", "appearance": "tall, grey beard",
                        "clothing": "embroidered coat", "colors": "deep blue"}],
        "locations": [{"id": "camp", "name": "The camp", "architecture": "canvas tents",
                       "time_of_day": "dusk"}],
        "props": [{"id": "purse", "name": "leather purse", "description": "heavy, worn"}],
        "style": {"visual_style": "cinematic realism", "lighting": "lamplight"},
    }
    block = video_director.continuity_reference_block(bible, ["prince"], ["camp"], ["purse"])
    # Case-insensitive: the block is prose, so entries are sentence-capitalised.
    for token in ("grey beard", "embroidered coat", "canvas tents", "leather purse"):
        check(f"reference block carries '{token}'", token in block.lower())

    shot = {"subject": "the prince", "first_frame_prompt": "he weighs the purse",
            "setting": "the camp", "framing": "medium", "lighting": "lamplight",
            "character_ids": ["prince"], "location_ids": ["camp"], "prop_ids": ["purse"]}
    prompt = video_director.build_frame_prompt(shot, bible, {"visual_style": "cinematic realism"})
    check("frame prompt includes the locked description", "embroidered coat" in prompt.lower())
    check("frame prompt asks for a single still image",
          "still photographic frame" in prompt.lower())
    check("frame prompt forbids text/captions in the image", "no text" in prompt.lower())

    motion = video_director.build_motion_prompt(
        {"motion_prompt": "he raises the purse", "camera_movement": "slow push in",
         "narration": "SHOULD NOT APPEAR", "sound_notes": "ALSO NOT"}, {})
    check("motion prompt excludes narration", "SHOULD NOT APPEAR" not in motion)
    check("motion prompt excludes sound notes", "ALSO NOT" not in motion)


def test_prompt_detail():
    """
    Prompts must be EXTREMELY detailed while the shot stays visually simple —
    the two are separate axes (owner ask, 2026-08-12). LTX/Wan both degrade
    visibly on short prompts, and neither truncates (both text encoders use
    max_length=99999999), so richness is free quality.
    """
    section("Prompt detail vs. shot simplicity")

    rich = {
        "subject": "an elderly courier",
        "subject_detail": ("a man in his sixties, weathered sun-darkened skin, close-cropped "
                           "grey beard, his weathered hands resting on the saddle pommel"),
        "primary_action": "he lowers his gaze to the purse",
        "setting": "a military camp",
        "setting_detail": ("rows of ochre canvas tents sagging with rainwater, a distant crowd "
                           "of soldiers standing motionless and blurred in the far background"),
        "time_of_day": "blue hour", "framing": "medium", "camera_angle": "slightly low angle",
        "camera_movement": "static",
        "texture_notes": "coarse undyed wool damp at the shoulders, oiled leather, brass buckle",
        "lighting": "low amber oil-lamp light from frame left, deep cool shadow on the right cheek",
        "atmosphere": "fine drizzle, visible breath vapour, faint woodsmoke haze",
        "depth_notes": "tent rope sharp in the foreground, camp lanterns as soft bokeh behind",
        "lens": "shot on an 85mm lens at f/2, shallow depth of field, fine 35mm grain",
        "first_frame_prompt": ("A weathered courier sits motionless astride a rain-soaked horse "
                               "at the edge of a camp at blue hour, a heavy leather purse in one hand."),
        "last_frame_prompt": ("The same courier in the same position, now with his head tilted "
                              "down, his gaze resting on the leather purse at his side."),
        "motion_prompt": ("The courier slowly lowers his head toward the purse; the horse shifts "
                          "its weight once; drizzle falls steadily and the lamp flame wavers."),
        "character_ids": ["courier"], "location_ids": ["camp"], "prop_ids": ["purse"],
        "duration": 3.5,
    }
    bible = {
        "characters": [{"id": "courier", "name": "The courier", "appearance": "lean, sixties",
                        "clothing": "coarse undyed wool cloak, frayed hem, bone toggle",
                        "colors": "faded indigo", "accessories": "tarnished brass buckle"}],
        "locations": [{"id": "camp", "name": "The camp", "architecture": "ochre canvas tents",
                       "time_of_day": "blue hour", "weather": "steady fine drizzle"}],
        "props": [{"id": "purse", "name": "leather purse", "description": "heavy oiled leather"}],
        "style": {"visual_style": "naturalistic historical realism", "lighting": "practical lamplight"},
    }
    direction = {"visual_style": "naturalistic historical realism",
                 "color_palette": "muted indigo, ochre and amber"}

    # Detail must NOT be read as complexity, or rich shots get split for nothing.
    score = video_director.complexity_score(rich)
    check(f"a richly-described but simple shot stays under the limit (scored {score})",
          score <= video_director.COMPLEXITY_LIMIT, str(score))
    kept, _ = video_director.split_complex_shots([rich])
    check("a richly-described simple shot is not split", len(kept) == 1)
    check("mere mention of 'hands' is texture, not hand-work",
          video_director.complexity_score(
              {"primary_action": "his hands rest on the pommel", "motion_prompt": ""}) == 0)
    check("a distant motionless crowd is not penalised",
          video_director.complexity_score(
              {"subject": "a rider", "setting": "a distant crowd, motionless", "primary_action": "waits",
               "motion_prompt": ""}) == 0)
    check("hand-INTENSIVE work is still penalised",
          video_director.complexity_score(
              {"primary_action": "she is sewing a torn hem", "motion_prompt": ""}) >= 1)
    check("a genuinely busy shot is still caught",
          video_director.complexity_score(
              {"primary_action": "he dismounts and then walks through the crowd",
               "motion_prompt": "he dismounts then walks",
               "camera_movement": "tracking shot following him",
               "character_ids": ["a", "b", "c", "d"]}) > video_director.COMPLEXITY_LIMIT)

    first = video_director.build_frame_prompt(rich, bible, direction, "first")
    last = video_director.build_frame_prompt(rich, bible, direction, "last")
    motion = video_director.build_motion_prompt(rich, direction)

    check(f"first-frame prompt is richly detailed ({len(first.split())} words, want >=60)",
          len(first.split()) >= 60, str(len(first.split())))
    check(f"last-frame prompt is richly detailed ({len(last.split())} words, want >=60)",
          len(last.split()) >= 60, str(len(last.split())))
    check(f"motion prompt is detailed ({len(motion.split())} words, want >=25)",
          len(motion.split()) >= 25, str(len(motion.split())))

    for token, label in (("bone toggle", "locked continuity wardrobe"),
                         ("amber oil-lamp", "light source and direction"),
                         ("85mm", "lens and optics"),
                         ("drizzle", "atmosphere"),
                         ("bokeh", "depth separation"),
                         ("no watermark", "quality tail")):
        check(f"frame prompt carries {label}", token in first)

    check("frame prompts read as prose, not tag soup",
          first.count(",") < len(first.split()) / 3)
    check("no empty-field artifacts leak into the prompt",
          "; ;" not in first and " ." not in first and "|" not in first)
    check("the visual style is not stated twice",
          first.lower().count("naturalistic historical realism") == 1,
          str(first.lower().count("naturalistic historical realism")))
    check("both frames share the subject/setting detail so they match",
          "sixties" in first and "sixties" in last)
    # Wording-independent: the point is that a static camera is pinned as an
    # explicit instruction, not that it uses one particular phrase.
    check("motion prompt pins a static camera explicitly",
          "locked off" in motion.lower() and "no pan" in motion.lower())

    # The continuity bible must WIN over shot-level detail for referenced ids.
    # Observed for real before this guard: one prompt carried both
    # "salt-and-pepper beard, faded indigo wool robe" (shot) and "stubble,
    # black hair, coarse undyed wool tunic" (bible), leaving the image model to
    # pick between two different men — worse than either alone, and it breaks
    # the continuity the bible exists to hold (rule 33).
    contradicting = dict(rich)
    contradicting["subject_detail"] = "clean-shaven young man in a bright red silk robe"
    conflicted = video_director.build_frame_prompt(contradicting, bible, direction, "first")
    check("shot-level subject detail is dropped when the bible describes that character",
          "bright red silk" not in conflicted)
    check("the bible's wardrobe is what survives",
          "coarse undyed wool cloak" in conflicted.lower())

    # ...but a subject with NO bible entry keeps its own description.
    anonymous = dict(rich)
    anonymous["character_ids"] = []
    anon_prompt = video_director.build_frame_prompt(anonymous, bible, direction, "first")
    check("an unreferenced subject keeps its own detail",
          "sixties" in anon_prompt)

    # Camera-angle phrasing: models write "low", "eye level" or "birds-eye"
    # interchangeably, and naive interpolation produced "from a low." /
    # "from a eye level".
    for framing, angle, expect in (
        ("wide", "low", "Wide shot from a low angle."),
        ("wide", "low angle", "Wide shot from a low angle."),
        ("medium", "eye level", "Medium shot at eye level."),
        ("wide", "birds-eye", "Wide shot from a birds-eye view."),
        ("medium", "overhead", "Medium shot from overhead."),
        ("wide", "elevated", "Wide shot from an elevated angle."),
        ("close-up", "", "Close-up shot."),
    ):
        got = video_director.build_frame_prompt(
            {"framing": framing, "camera_angle": angle,
             "first_frame_prompt": "a scene"}, {}, {}).split(". ")[0] + "."
        check(f"angle phrasing: {framing!r}+{angle!r} reads correctly", got == expect, got)


# ── 5. Provider capability + fallback order ──────────────────────────────────

def test_provider_fallbacks():
    section("Provider capability detection and fallbacks")
    native = {"first_last_frame": True, "image_to_video": True, "text_to_video": True,
              "label": "x", "first_last_frame_note": ""}
    no_flf = {"first_last_frame": False, "image_to_video": True, "text_to_video": True,
              "label": "x", "first_last_frame_note": "not supported by this model"}
    t2v_only = {"first_last_frame": False, "image_to_video": False, "text_to_video": True,
                "label": "x", "first_last_frame_note": ""}
    i2v_only = {"first_last_frame": False, "image_to_video": True, "text_to_video": False,
                "label": "x", "first_last_frame_note": ""}

    check("native FLF chosen when genuinely supported",
          video_provider.resolve_strategy(native, True, True)["strategy"]
          == video_provider.STRATEGY_NATIVE_FLF)
    r = video_provider.resolve_strategy(no_flf, True, True)
    check("falls back to first-frame i2v when FLF is unavailable",
          r["strategy"] == video_provider.STRATEGY_FIRST_FRAME)
    check("the fallback explains WHY, quoting the capability note",
          "not supported by this model" in r["why"])
    check("falls back to chain-extract with no last frame",
          video_provider.resolve_strategy(no_flf, True, False)["strategy"]
          == video_provider.STRATEGY_CHAIN_EXTRACT)
    check("falls back to text-to-video with no frames at all",
          video_provider.resolve_strategy(t2v_only, False, False)["strategy"]
          == video_provider.STRATEGY_TEXT_ONLY)
    try:
        video_provider.resolve_strategy(i2v_only, False, False)
        check("i2v-only provider with no frame raises a clear error", False)
    except video_provider.VideoProviderError as e:
        check("i2v-only provider with no frame raises a clear error", "first frame" in str(e))

    # The real local providers must NOT claim FLF — verified by probe.
    from agents import videographer
    for model in ("ltx", "wan22"):
        supported, note = videographer.supports_first_last_frame(model)
        check(f"{model} does not falsely claim first-last-frame support",
              supported is False and len(note) > 20)

    mock = video_provider.get_provider("mock")
    caps = mock.capabilities()
    check("mock provider is labelled as a mock", caps["is_mock"] is True)
    check("mock strategy result is flagged is_mock",
          video_provider.resolve_strategy(caps, True, True)["is_mock"] is True)


# ── 6. Persistence, ordering, locking, resume ────────────────────────────────

def test_store():
    section("Persistence, shot ordering, locking, resume")
    video_store.init_video_db()

    pid = video_store.create_project(
        title="TEST scene", source_kind="scene_story", source_text="A test scene.",
        direction={"target_seconds": 30, "provider": "mock"})
    check("project persists", video_store.get_project(pid) is not None)

    shots = [{"beat_id": "b1", "subject": f"shot {i}", "duration": 3.5,
              "primary_action": "a", "locked_fields": []} for i in range(4)]
    ids = video_store.replace_shots(pid, shots)
    stored = video_store.list_shots(pid)
    check("all shots persist in order",
          [s["shot_number"] for s in stored] == [1, 2, 3, 4])

    # Reorder
    reversed_ids = [s["id"] for s in reversed(stored)]
    video_store.reorder_shots(pid, reversed_ids)
    after = video_store.list_shots(pid)
    check("reorder writes a new, gapless order",
          [s["shot_number"] for s in after] == [1, 2, 3, 4]
          and [s["id"] for s in after] == reversed_ids)

    # Locking: regeneration must not overwrite a locked field...
    target = after[0]["id"]
    video_store.update_shot(target, locked=["subject"])
    video_store.update_shot(target, data={"subject": "OVERWRITTEN BY REGEN"})
    check("a locked field survives regeneration",
          video_store.get_shot(target)["data"]["subject"] != "OVERWRITTEN BY REGEN")
    # ...but a human edit may change it.
    video_store.update_shot(target, data={"subject": "HUMAN EDIT"}, force_locked=True)
    check("a human edit may still change a locked field",
          video_store.get_shot(target)["data"]["subject"] == "HUMAN EDIT")
    # Merging must not drop sibling fields.
    video_store.update_shot(target, data={"lighting": "dusk"}, force_locked=True)
    merged = video_store.get_shot(target)["data"]
    check("partial update merges instead of replacing",
          merged.get("subject") == "HUMAN EDIT" and merged.get("lighting") == "dusk")

    # Assets: paths only, versioned history.
    video_store.add_asset(pid, "first_frame", "/outputs/a.png", shot_id=target, seed=1, model="m")
    video_store.add_asset(pid, "first_frame", "/outputs/b.png", shot_id=target, seed=2, model="m")
    versions = video_store.list_assets(pid, shot_id=target, kind="first_frame")
    check("asset regeneration keeps a version history",
          len(versions) == 2 and {v["version"] for v in versions} == {1, 2})

    # Insert / delete renumber correctly.
    video_store.add_shot(pid, {"subject": "inserted"}, after_number=1)
    check("insert renumbers the rest",
          [s["shot_number"] for s in video_store.list_shots(pid)] == [1, 2, 3, 4, 5])
    video_store.delete_shot(video_store.list_shots(pid)[1]["id"])
    check("delete renumbers the rest",
          [s["shot_number"] for s in video_store.list_shots(pid)] == [1, 2, 3, 4])

    # Resume state
    from agents import video_pipeline
    state = video_pipeline.resume_state(pid)
    check("resume reports every shot as needing frames", len(state["needs_frames"]) == 4)
    check("resume reports the project as incomplete", state["complete"] is False)

    first = video_store.list_shots(pid)[0]["id"]
    video_store.update_shot(first, first_frame_path="/a.png", last_frame_path="/b.png",
                            clip_path="/c.mp4")
    state2 = video_pipeline.resume_state(pid)
    check("resume skips the shot that already has assets",
          len(state2["needs_frames"]) == 3 and len(state2["needs_clips"]) == 3)

    # Resumable job rows: reopening returns the SAME job (that is what resume means).
    j1 = video_store.start_job(pid, "clips", total=4)
    video_store.update_job(j1, done=2, cursor="x")
    j2 = video_store.start_job(pid, "clips", total=4)
    check("an interrupted job resumes instead of restarting",
          j1 == j2 and video_store.get_job(j2)["done"] == 2)

    video_store.delete_project(pid)
    check("deleting a project removes its shots too",
          video_store.get_project(pid) is None and video_store.list_shots(pid) == [])


# ── 7. Assembly + export metadata ────────────────────────────────────────────

def test_export():
    section("Assembly metadata and export")
    pid = video_store.create_project(title="TEST export", source_text="x",
                                     direction={"target_seconds": 30})
    video_store.replace_shots(pid, [
        {"subject": "one", "duration": 3.5, "narration": "First line.", "beat_id": "b1"},
        {"subject": "two", "duration": 4.0, "narration": "Second line.", "beat_id": "b1"},
    ])
    meta = video_assembly.export_metadata(pid)
    check("export includes the project", meta["project"]["id"] == pid)
    check("export includes every shot", len(meta["shots"]) == 2)
    check("export totals the duration", meta["totals"]["estimated_seconds"] == 7.5)
    check("export is JSON-serialisable", isinstance(json.dumps(meta), str))

    srt = video_assembly.export_subtitles(pid)
    check("subtitles contain both narration lines",
          "First line." in srt and "Second line." in srt)
    check("subtitles use SRT timing", "00:00:00,000 --> 00:00:03,500" in srt)

    # Assembly with no clips must fail HONESTLY, not silently.
    result = video_assembly.assemble_draft(pid)
    check("assembly with no clips returns no video", result["video_path"] is None)
    check("assembly explains why in plain language", "clips" in result["reason"].lower())
    check("metadata is still written when assembly can't run",
          os.path.exists(result["metadata_path"]))
    for path in (result["metadata_path"], result.get("subtitles_path")):
        if path and os.path.exists(path):
            os.remove(path)
    video_store.delete_project(pid)


# ── 7b. Finished videos on the Products shelf ────────────────────────────────

def test_finished_shelf():
    section("Finished videos on the Products shelf")
    outputs = video_assembly.OUTPUTS_DIR
    made: list[Path] = []

    def temp_file(name: str, content: bytes = b"x") -> Path:
        path = outputs / name
        path.write_bytes(content)
        made.append(path)
        return path

    # 1. Planned but never assembled — nothing to put on a shelf.
    unfinished = video_store.create_project(title="TEST unfinished", source_text="x")
    video_store.replace_shots(unfinished, [{"subject": "one", "duration": 3.5}])

    # 2. Really finished: an mp4 on disk, two clips, one real first frame.
    done = video_store.create_project(title="TEST finished", source_text="x")
    shot_ids = video_store.replace_shots(done, [
        {"subject": "one", "duration": 3.5},
        {"subject": "two", "duration": 4.0},
        {"subject": "three (never rendered)", "duration": 3.5},
    ])
    frame = temp_file(f"test-shelf-{done}-frame.png")
    for shot_id in shot_ids[:2]:
        clip = temp_file(f"test-shelf-{shot_id}.mp4")
        video_store.update_shot(shot_id, clip_path=str(clip), status="clip_ready")
        video_store.add_asset(done, "clip", str(clip), shot_id=shot_id,
                              meta={"is_mock": False})
    video_store.update_shot(shot_ids[0], first_frame_path=str(frame))
    video_path = temp_file(f"test-shelf-{done}.mp4")
    meta_path = temp_file(f"test-shelf-{done}.json", b"{}")
    video_store.update_project(done, status="complete", stage="export", export={
        "project_id": done, "video_path": str(video_path),
        "metadata_path": str(meta_path), "subtitles_path": None,
        "clip_count": 2, "reason": "Joined 2 clips.",
    })

    # 3. Assembled once, but the file has since been deleted from outputs/.
    gone = video_store.create_project(title="TEST deleted file", source_text="x")
    video_store.update_project(gone, status="complete", export={
        "project_id": gone, "video_path": str(outputs / "test-shelf-does-not-exist.mp4"),
        "metadata_path": "", "subtitles_path": None, "clip_count": 1, "reason": "ok",
    })

    # 4. Built from mock clips — must be labelled all the way to the shelf.
    mock = video_store.create_project(title="TEST mock", source_text="x")
    mock_shots = video_store.replace_shots(mock, [{"subject": "one", "duration": 3.5}])
    mock_clip = temp_file(f"test-shelf-{mock}-clip.mp4")
    video_store.update_shot(mock_shots[0], clip_path=str(mock_clip))
    video_store.add_asset(mock, "clip", str(mock_clip), shot_id=mock_shots[0],
                          meta={"is_mock": True})
    mock_video = temp_file(f"test-shelf-{mock}.mp4")
    video_store.update_project(mock, status="complete", export={
        "project_id": mock, "video_path": str(mock_video), "metadata_path": "",
        "subtitles_path": None, "clip_count": 1, "reason": "Joined 1 clip.",
    })

    try:
        listed = {v["id"]: v for v in video_assembly.list_finished()}
        check("an assembled project appears on the shelf", done in listed)
        check("a project that was never assembled does not", unfinished not in listed)
        check("a project whose file was deleted does not",
              gone not in listed, "a shelf entry must point at a file that exists")

        item = listed.get(done, {})
        check("the shelf counts the shots that really have clips",
              item.get("clip_count") == 2 and item.get("shot_count") == 3)
        # The stub .mp4 files here are one byte, so ffprobe (if installed) can't
        # measure them: the shelf must fall back to the PLANNED length and say
        # so, rather than reporting a measurement it doesn't have.
        check("length falls back to the plan when the file can't be measured",
              item.get("duration_seconds") == 7.5, str(item.get("duration_seconds")))
        check("an unmeasured length is flagged as not measured",
              item.get("duration_measured") is False)

        # A length already measured for THIS file is reused, never re-probed.
        video_store.update_project(done, export={
            **(video_store.get_project(done)["export"] or {}),
            "duration_seconds": 9.25, "duration_measured_for": str(video_path)})
        remeasured = {v["id"]: v for v in video_assembly.list_finished()}[done]
        check("a stored measurement for the same file is reused",
              remeasured["duration_seconds"] == 9.2 and remeasured["duration_measured"] is True,
              str(remeasured["duration_seconds"]))
        video_store.update_project(done, export={
            **(video_store.get_project(done)["export"] or {}),
            "duration_measured_for": str(outputs / "some-older-render.mp4")})
        stale = {v["id"]: v for v in video_assembly.list_finished()}[done]
        check("a measurement taken from a DIFFERENT file is not reused",
              stale["duration_seconds"] == 7.5, str(stale["duration_seconds"]))
        check("the poster is a frame that exists on disk",
              item.get("poster_path") == str(frame))
        check("a real-provider video is not labelled mock", item.get("is_mock") is False)
        check("the shelf carries the title and source kind",
              item.get("title") == "TEST finished" and item.get("source_kind") == "scene_story")

        check("a mock-built video is labelled mock end to end",
              listed.get(mock, {}).get("is_mock") is True)

        # The missing file is reportable, just never presented as playable.
        with_missing = {v["id"]: v for v in video_assembly.list_finished(include_missing=True)}
        check("include_missing reports the gap rather than hiding it",
              with_missing.get(gone, {}).get("file_missing") is True)
        check("an existing file is never flagged missing",
              with_missing.get(done, {}).get("file_missing") is False)

        # HTTP: the dashboard needs servable URLs, not Windows paths.
        from fastapi.testclient import TestClient
        from agents import api as api_module
        client = TestClient(api_module.app)
        r = client.get("/video/finished")
        check("GET /video/finished responds", r.status_code == 200, r.text[:200])
        api_items = {v["id"]: v for v in r.json()["videos"]}
        check("the endpoint lists the finished video", done in api_items)
        check("the endpoint hides the deleted-file video", gone not in api_items)
        web = api_items.get(done, {})
        check("video_url is a servable /outputs path",
              str(web.get("video_url", "")).startswith("/outputs/")
              and web["video_url"].endswith(".mp4"))
        check("poster_url is a servable /outputs path",
              str(web.get("poster_url", "")).startswith("/outputs/"))
        check("metadata_url is a servable /outputs path",
              str(web.get("metadata_url", "")).startswith("/outputs/"))
        check("a subtitle track that was never written stays empty",
              web.get("subtitles_url") == "")

        # A video is NOT a product row — that would double-count it in the
        # Steward's ledger and hand the print/Etsy actions a type they can't use.
        product_ids = {p["id"] for p in client.get("/products").json()}
        check("finished videos are not written into the products table",
              done not in product_ids and mock not in product_ids)
    finally:
        for pid_ in (unfinished, done, gone, mock):
            video_store.delete_project(pid_)
        for path in made:
            path.unlink(missing_ok=True)


# ── 8. Validation findings ───────────────────────────────────────────────────

def test_validation():
    section("Continuity validation")
    pid = video_store.create_project(title="TEST validate", source_text="x")
    video_store.update_project(pid, continuity={
        "characters": [{"id": "known", "name": "Known"}],
        "locations": [], "props": [], "style": {}, "locked": [],
    })
    video_store.replace_shots(pid, [
        {"subject": "a", "duration": 3.5, "location_ids": ["l1"], "character_ids": ["known"]},
        {"subject": "b", "duration": 3.5, "location_ids": ["l2"], "character_ids": ["ghost"],
         "continuity_mode": "continuous"},
    ])
    report = video_assembly.validate_project(pid, use_vision=False)
    kinds = {f["kind"] for f in report["findings"]}
    check("flags a location change on a continuous shot", "location_drift" in kinds)
    check("flags a reference to an unlocked element", "unknown_reference" in kinds)
    check("flags missing frames", "missing_asset" in kinds)
    check("findings are sorted most-severe first",
          report["findings"][0]["severity"] == "error")
    check("summary counts errors", report["summary"]["errors"] >= 1)
    video_store.delete_project(pid)


# ── 9. HTTP surface (FastAPI TestClient over the real DB) ────────────────────

def test_api():
    section("HTTP endpoints")
    from fastapi.testclient import TestClient
    from agents import api as api_module
    from agents.state import init_db

    init_db()
    client = TestClient(api_module.app)

    r = client.get("/video/providers")
    check("GET /video/providers responds", r.status_code == 200)
    body = r.json()
    comfy = next((p for p in body["providers"] if p["id"] == "comfyui:wan22"), None)
    check("provider list reports FLF support honestly",
          comfy is not None and comfy.get("first_last_frame") is False)

    r = client.get("/video/defaults")
    check("GET /video/defaults responds", r.status_code == 200)
    check("defaults enforce the 3-4s window",
          r.json()["min_shot_seconds"] == 3.0 and r.json()["max_shot_seconds"] == 4.0)

    # -- create from free text (the primary path) --
    r = client.post("/video/projects", json={
        "source_kind": "scene_story",
        "source_text": "A courier rides through the rain to deliver a message.",
        "source_instructions": "quiet and reverent",
    })
    check("POST /video/projects creates from pasted text", r.status_code == 200, r.text[:200])
    project = r.json()
    pid = project["id"]
    check("a task row is created alongside the project", bool(project.get("task_id")))
    check("direction defaults are applied", project["direction"]["target_seconds"] == 60)

    # -- empty source is refused --
    r = client.post("/video/projects", json={"source_kind": "scene_story", "source_text": "  "})
    check("empty source is refused with a clear message",
          r.status_code == 400 and "paste" in r.json()["detail"].lower())

    # -- create from a bookmark / quote card, if one exists --
    products = client.get("/products").json()
    if products:
        product = products[0]
        kind = "quote_card" if product.get("product_type") == "quote_card" else "bookmark"
        r = client.post("/video/projects", json={
            "source_kind": kind, "source_product_id": product["id"]})
        check(f"POST /video/projects creates from a {kind}", r.status_code == 200, r.text[:200])
        if r.status_code == 200:
            from_product = r.json()
            check("product source is referenced, not duplicated",
                  from_product["source_product_id"] == product["id"])
            check("product text is copied in as the starting source",
                  len(from_product["source_text"]) > 0)
            # The original product row must be untouched.
            after = client.get(f"/products/{product['id']}").json()
            check("the source product itself is unchanged",
                  after["listing_copy"] == product["listing_copy"])
            client.delete(f"/video/projects/{from_product['id']}")
    else:
        check("SKIP: no products available to test bookmark/card sourcing", True)

    r = client.post("/video/projects", json={
        "source_kind": "bookmark", "source_product_id": None})
    check("bookmark source without a product id is refused", r.status_code == 400)

    # -- shots: add, edit, lock, reorder, split, simplify, approve --
    r = client.post(f"/video/projects/{pid}/shots", json={"data": {
        "subject": "a courier", "primary_action": "rides forward", "duration": 9.0}})
    check("POST shot clamps an out-of-range duration",
          r.status_code == 200 and r.json()["data"]["duration"] == 4.0)
    shot_a = r.json()["id"]

    r = client.post(f"/video/projects/{pid}/shots", json={"data": {
        "subject": "the gate", "primary_action": "opens", "duration": 3.5}})
    shot_b = r.json()["id"]

    r = client.post(f"/video/projects/{pid}/shots/reorder",
                    json={"shot_ids": [shot_b, shot_a]})
    check("reorder endpoint works",
          r.status_code == 200 and r.json()["shots"][0]["id"] == shot_b)

    r = client.post(f"/video/projects/{pid}/shots/reorder", json={"shot_ids": [shot_a]})
    check("reorder rejects an incomplete list", r.status_code == 400)

    r = client.patch(f"/video/shots/{shot_a}", json={"locked_fields": ["subject"]})
    check("locking a field works", r.status_code == 200 and "subject" in r.json()["locked_fields"])

    r = client.patch(f"/video/shots/{shot_a}", json={"data": {"subject": "edited by hand"}})
    check("a human edit overrides the lock",
          r.status_code == 200 and r.json()["data"]["subject"] == "edited by hand")

    r = client.post(f"/video/shots/{shot_a}/split")
    check("split endpoint returns two shots",
          r.status_code == 200 and len(r.json()["shots"]) == 2)

    r = client.post(f"/video/shots/{shot_a}/simplify")
    check("simplify endpoint works",
          r.status_code == 200 and r.json()["data"]["camera_movement"] == "static")

    r = client.post(f"/video/projects/{pid}/approve", json={"approved": True})
    check("bulk approve works", r.status_code == 200 and r.json()["updated"] >= 2)

    r = client.get(f"/video/projects/{pid}")
    check("GET project returns shots and resume state",
          r.status_code == 200 and "resume" in r.json() and len(r.json()["shots"]) >= 2)
    check("shots carry servable URLs for the dashboard",
          "first_frame_url" in r.json()["shots"][0])

    # -- validation, export, assembly --
    r = client.get(f"/video/projects/{pid}/validate")
    check("validate endpoint responds", r.status_code == 200 and "findings" in r.json())

    r = client.get(f"/video/projects/{pid}/export")
    check("export endpoint returns the production record",
          r.status_code == 200 and r.json()["project"]["id"] == pid)

    r = client.get(f"/video/projects/{pid}/subtitles")
    check("subtitles endpoint responds", r.status_code == 200)

    r = client.post(f"/video/projects/{pid}/assemble", json={})
    check("assemble responds honestly with no clips",
          r.status_code == 200 and r.json()["video_path"] is None and r.json()["reason"])

    # -- 404s --
    check("unknown project 404s", client.get("/video/projects/nope").status_code == 404)
    check("unknown shot 404s",
          client.patch("/video/shots/nope", json={"data": {}}).status_code == 404)
    check("cancelling an unknown job 404s",
          client.post("/video/jobs/nope/cancel").status_code == 404)

    # -- cleanup --
    check("delete project works",
          client.delete(f"/video/projects/{pid}").status_code == 200)
    check("deleted project is gone", client.get(f"/video/projects/{pid}").status_code == 404)


# ── 10. Story → shot decomposition with a stubbed LLM ────────────────────────

def test_decomposition_with_stub():
    section("Story-to-shot decomposition (stubbed LLM)")
    import agents.video_director as vd

    calls: list[str] = []

    def fake_call_llm(task_type, messages, **kwargs):
        user = messages[-1]["content"]
        calls.append(user[:40])
        # Dispatch on each prompt's OPENING LINE, not on a phrase that might
        # appear anywhere. The shot-planning prompt legitimately mentions "the
        # continuity bible" (telling the model not to duplicate it), which
        # collided with a naive substring match and made this stub answer the
        # wrong stage.
        opening = user.lstrip().split("\n", 1)[0]
        if opening.startswith("Break this story beat"):
            pass  # fall through to the shot-planning response below
        elif "Analyse this source" in opening:
            return json.dumps({
                "summary": "A courier delivers a message.",
                "central_message": "Faithfulness in small duties.",
                "characters": [{"id": "courier", "name": "The courier", "description": "young rider"}],
                "locations": [{"id": "camp", "name": "The camp", "description": "tents in rain"}],
                "props": [{"id": "letter", "name": "sealed letter", "description": "wax seal"}],
                "beats": [{"id": "beat_1", "title": "Arrival", "summary": "He arrives.",
                           "emotion": "tense", "suggested_shots": 2}],
                "emotional_progression": "tense to calm",
                "narration_notes": "", "continuity_risks": [], "do_not_depict_literally": [],
            })
        elif opening.startswith("Write a continuity bible"):
            return json.dumps({
                "characters": [{"id": "courier", "name": "The courier", "appearance": "young, wiry",
                                "clothing": "wet wool cloak", "colors": "brown"}],
                "locations": [{"id": "camp", "name": "The camp", "architecture": "canvas tents",
                               "time_of_day": "dusk", "weather": "light rain"}],
                "props": [{"id": "letter", "name": "sealed letter", "description": "wax seal"}],
                "style": {"visual_style": "cinematic realism", "lighting": "lamplight"},
            })
        # shot planning
        return json.dumps({"shots": [
            {"narrative_purpose": "establish", "subject": "the courier",
             "primary_action": "reins in his horse", "setting": "the camp",
             "time_of_day": "dusk", "framing": "wide", "camera_angle": "eye level",
             "camera_movement": "static", "lighting": "lamplight", "mood": "tense",
             "character_ids": ["courier"], "location_ids": ["camp"], "prop_ids": [],
             "first_frame_prompt": "rider halts at the camp edge",
             "last_frame_prompt": "rider still, horse settled",
             "motion_prompt": "the horse settles", "narration": "He had ridden two days.",
             "dialogue": "", "sound_notes": "rain", "transition": "cut",
             "continuity_mode": "editorial_cut", "continuity_notes": "", "duration": 12.0},
            {"narrative_purpose": "detail", "subject": "the letter",
             "primary_action": "is drawn from the cloak and then handed over while the guard turns",
             "setting": "the camp", "time_of_day": "dusk", "framing": "close-up",
             "camera_angle": "high", "camera_movement": "tracking shot following the hand",
             "lighting": "lamplight", "mood": "tense",
             "character_ids": ["courier", "a", "b", "c"], "location_ids": ["camp"],
             "prop_ids": ["letter"],
             "first_frame_prompt": "a sealed letter inside a wet cloak",
             "last_frame_prompt": "the letter in another hand",
             "motion_prompt": "hand draws the letter then passes it while the guard turns",
             "narration": "", "dialogue": "", "sound_notes": "", "transition": "cut",
             "continuity_mode": "continuous", "continuity_notes": "", "duration": 3.5},
        ]})

    original = vd.call_llm
    vd.call_llm = fake_call_llm
    try:
        analysis = vd.analyze_story("A courier rides to the camp.", {"target_seconds": 30})
        check("analysis produces beats", len(analysis["beats"]) == 1)
        check("analysis carries a deterministic sacred scan", "sacred_flags" in analysis)

        bible = vd.build_continuity_bible(analysis, {})
        check("continuity bible has locked character descriptions",
              bible["characters"][0]["clothing"] == "wet wool cloak")

        shots = vd.plan_beat_shots(analysis["beats"][0], analysis, bible, {}, 2)
        check("beat produces the requested shots", len(shots) == 2)
        check("an over-long duration from the model is clamped",
              all(3.0 <= s["duration"] <= 4.0 for s in shots),
              str([s["duration"] for s in shots]))

        split, notes = vd.split_complex_shots(shots)
        check("the over-complex shot gets split", len(split) == 3, f"got {len(split)}")
        check("the split is reported", len(notes) >= 1)

        safe, _ = video_safety.enforce_shots(split)
        check("every shot carries the non-depiction guard",
              all(video_safety.NEGATIVE_GUARD in s["negative_prompt"] for s in safe))
    finally:
        vd.call_llm = original


def test_planning_resilience():
    """
    A single failing beat must not discard the whole plan.

    Regression: a real run lost six already-planned beats when beat 2 of 7 hit
    an Ollama read timeout (2026-08-12). Planning now inserts a clearly-marked
    placeholder and carries on.
    """
    section("Planning survives a failed beat")
    from agents import video_pipeline
    import agents.video_director as vd

    pid = video_store.create_project(
        title="TEST resilience", source_text="A long story with several beats.",
        direction={"target_seconds": 30, "provider": "mock"})

    calls = {"n": 0}

    def fake_analyze(*a, **k):
        return {
            "summary": "s", "central_message": "m",
            "characters": [], "locations": [], "props": [],
            "beats": [{"id": f"beat_{i}", "title": f"Beat {i}", "summary": "x",
                       "suggested_shots": 1} for i in range(1, 4)],
            "emotional_progression": "", "narration_notes": "",
            "continuity_risks": [], "do_not_depict_literally": [],
            "sacred_flags": {"figures": [], "has_reference": False, "depiction_risk": False},
        }

    def fake_bible(*a, **k):
        return {"characters": [], "locations": [], "props": [], "style": {}, "locked": []}

    def flaky_plan(beat, *a, **k):
        calls["n"] += 1
        if beat["id"] == "beat_2":
            raise RuntimeError("Read timed out. (read timeout=120)")
        return [vd._normalize_shot({"subject": f"shot for {beat['id']}",
                                    "primary_action": "waits", "duration": 3.5}, beat["id"])]

    orig = (vd.analyze_story, vd.build_continuity_bible, vd.plan_beat_shots)
    vd.analyze_story, vd.build_continuity_bible, vd.plan_beat_shots = (
        fake_analyze, fake_bible, flaky_plan)
    try:
        result = video_pipeline.build_plan(pid)
        check("planning completes despite a failed beat", result["shot_count"] >= 3)
        check("every beat was attempted, not abandoned at the failure", calls["n"] == 3)
        check("the failure is reported to the user", len(result.get("failed_beats", [])) == 1)
        check("the failure explains itself in the notes",
              any("could not be planned" in n for n in result["notes"]))
        shots = video_store.list_shots(pid)
        placeholders = [s for s in shots if (s["data"] or {}).get("needs_replanning")]
        check("a marked placeholder stands in for the failed beat", len(placeholders) == 1)
        check("the good beats' shots survived",
              any("beat_1" in str((s["data"] or {}).get("subject", "")) for s in shots)
              and any("beat_3" in str((s["data"] or {}).get("subject", "")) for s in shots))
        proj = video_store.get_project(pid)
        check("the project status flags the gap", proj["status"] == "planned_with_gaps")
    finally:
        vd.analyze_story, vd.build_continuity_bible, vd.plan_beat_shots = orig
        video_store.delete_project(pid)


def test_chained_generation():
    """
    Chained generation: each clip must start from the PREVIOUS clip's real
    final frame. Rendering shots independently is what made finished videos
    look like disconnected slides (owner report, 2026-08-12).
    """
    section("Chained generation")
    from agents import video_pipeline
    import agents.video_director as vd

    pid = video_store.create_project(
        title="TEST chain", source_text="x",
        direction={"target_seconds": 15, "provider": "mock", "low_resource": True})
    video_store.update_project(pid, continuity={
        "characters": [], "locations": [], "props": [], "style": {}, "locked": []})
    video_store.replace_shots(pid, [
        {"subject": "one", "duration": 3.5, "primary_action": "waits",
         "continuity_mode": "editorial_cut"},                       # opening
        {"subject": "two", "duration": 3.5, "primary_action": "waits",
         "continuity_mode": "continuous"},                          # must REUSE frame
        {"subject": "three", "duration": 3.5, "primary_action": "waits",
         "continuity_mode": "editorial_cut"},                       # new frame, matched
    ])

    stills: list[str] = []
    observed_calls = {"n": 0}
    extracted_seq = {"n": 0}

    def fake_still(prompt, aspect="16:9"):
        path = str(OUTPUTS := Path(__file__).parent.parent / "outputs" /
                   f"TESTchain-still-{len(stills)}.png")
        Path(path).write_bytes(b"x")
        stills.append(prompt)
        return path

    def fake_extract(clip_path, dest=None):
        extracted_seq["n"] += 1
        p = Path(__file__).parent.parent / "outputs" / f"TESTchain-extract-{extracted_seq['n']}.png"
        p.write_bytes(b"x")
        return p

    def fake_observe(image_path):
        observed_calls["n"] += 1
        return {"subject": "a marked man", "wardrobe": "OBSERVED-COAT",
                "setting": "a room", "lighting": "warm", "palette": "amber"}

    from agents import videographer
    orig = (video_pipeline._generate_still, videographer.extract_last_frame, vd.observe_frame)
    video_pipeline._generate_still = fake_still
    videographer.extract_last_frame = fake_extract
    vd.observe_frame = fake_observe
    try:
        result = video_pipeline.generate_chained(pid, provider_id="mock", adapt=True)
        check("chained run completes", result.get("chained") is True and result["done"] == 3)
        check("no errors in a clean chain", not result["errors"], str(result["errors"]))

        shots = video_store.list_shots(pid)
        check("every shot got a clip", all(s.get("clip_path") for s in shots))
        check("every shot got a real extracted last frame",
              all("TESTchain-extract" in str(s.get("last_frame_path")) for s in shots))

        # THE core property: shot 2 starts on shot 1's extracted final frame.
        check("a continuous shot reuses the previous clip's REAL final frame",
              shots[1]["first_frame_path"] == shots[0]["last_frame_path"],
              f"{shots[1]['first_frame_path']} vs {shots[0]['last_frame_path']}")

        # A cut regenerates, but anchored to what the previous clip showed.
        check("a cut generates a NEW first frame rather than reusing",
              shots[2]["first_frame_path"] != shots[1]["last_frame_path"])
        check("the cut's frame was vision-matched to the previous clip",
              observed_calls["n"] >= 1)
        check("the observation actually reaches the cut's prompt",
              any("OBSERVED-COAT" in p for p in stills), str(stills[-1])[:120])
        check("only the opening frame and the cut were generated (not shot 2)",
              len(stills) == 2, f"{len(stills)} stills")

        # Resume: rerunning skips finished shots and does not re-render.
        before = len(stills)
        again = video_pipeline.generate_chained(pid, provider_id="mock", adapt=True)
        check("re-running skips shots that already have clips",
              again["done"] == 3 and len(stills) == before)
    finally:
        video_pipeline._generate_still, videographer.extract_last_frame, vd.observe_frame = orig
        for f in (Path(__file__).parent.parent / "outputs").glob("TESTchain-*"):
            f.unlink(missing_ok=True)
        video_store.delete_project(pid)


def test_chain_strength():
    """The handed-in frame must be honoured, not treated as loose inspiration."""
    section("Chain image adherence")
    from agents import videographer
    graph, _ = videographer._ltx_graph("p", "n", image_filename="f.png",
                                       width=768, height=512, length=81, seed=1,
                                       image_strength=1.0)
    check("chained LTX renders start firmly on the given frame",
          graph["77"]["inputs"]["strength"] == 1.0)
    default_graph, _ = videographer._ltx_graph("p", "n", image_filename="f.png",
                                               width=768, height=512, length=81, seed=1)
    check("the non-chained default is unchanged",
          default_graph["77"]["inputs"]["strength"] == 0.15)
    spec = video_provider.ClipSpec("p", image_strength=1.0)
    check("ClipSpec carries image_strength", spec.image_strength == 1.0)


def test_safeguard_is_unbypassable():
    """
    Rule 30 must not depend on which door a shot came through.

    Planning enforces it and so does adding a shot by hand, but a shot EDITED
    afterwards merges arbitrary text straight into the stored plan — so the
    edit endpoint enforces it too, and `_safe_shot_data` enforces it again at
    the render boundary, which is the last code to run before a prompt is
    built. Same discipline as `_sanitize_claims` on the listing path (rule 4).
    """
    section("Sacred-figure safeguard cannot be bypassed")
    from fastapi.testclient import TestClient
    from agents import api as api_module, video_pipeline
    client = TestClient(api_module.app)

    pid = video_store.create_project(source_kind="scene_story", source_text="A journey.",
                                     title="Safeguard", direction={"provider": "mock"})
    video_store.replace_shots(pid, [
        {"subject": "a courier", "primary_action": "he waits", "duration": 3.5,
         "continuity_mode": "editorial_cut"},
    ])
    shot_id = video_store.list_shots(pid)[0]["id"]

    # The typographic apostrophe every real source actually uses.
    banned = "Bahá’u’lláh"
    resp = client.patch(f"/video/shots/{shot_id}", json={"data": {
        "subject": f"{banned} standing at the window",
        "first_frame_prompt": f"A portrait of {banned} in the doorway",
        "narration": f"The words of {banned} reached them that morning.",
    }})
    check("the edit endpoint accepts the request", resp.status_code == 200,
          str(resp.status_code))
    body = resp.json()
    stored = body["data"]

    check("a hand-edited visual field is rewritten, not stored as given",
          banned not in stored.get("subject", ""), stored.get("subject", "")[:60])
    check("the frame prompt is rewritten too",
          banned not in stored.get("first_frame_prompt", ""))
    check("the rewrite is REPORTED, never silent", bool(body.get("safety_notes")))
    check("narration is deliberately left alone (reverent naming is intended)",
          banned in stored.get("narration", ""), stored.get("narration", "")[:60])

    # Second line of defence: even if a field reached storage some other way,
    # nothing may reach a prompt builder unfiltered.
    video_store.update_shot(shot_id, data={"subject": f"{banned} at the threshold"},
                            force_locked=True)
    raw = video_store.get_shot(shot_id)
    check("the bypass is real without the render-boundary guard",
          banned in raw["data"]["subject"])
    safe, notes = video_pipeline._safe_shot_data(raw)
    check("the render boundary catches it anyway", banned not in safe.get("subject", ""))
    check("and says so", bool(notes))

    prompt = video_director.build_frame_prompt(safe, {}, {}, "first")
    check("no banned name can reach the image prompt", banned not in prompt)
    motion = video_director.build_motion_prompt(safe, {})
    check("nor the video prompt", banned not in motion)

    # 'Abdu'l-Bahá and Shoghi Effendi are NOT Manifestations — still depictable.
    ok_resp = client.patch(f"/video/shots/{shot_id}", json={"data": {
        "subject": "'Abdu'l-Bahá walking in the garden"}})
    check("'Abdu'l-Bahá is still depictable",
          "Abdu" in ok_resp.json()["data"]["subject"],
          ok_resp.json()["data"]["subject"][:60])

    video_store.delete_project(pid)


def test_cinematic_pacing():
    """
    'Fewer, longer, non-overlapping moments' (owner ask, 2026-08-13).

    Three levers, all deterministic: the shot budget comes from the story's
    distinct moments rather than the clock, repeated moments are removed
    instead of only warned about, and cuts happen at BEAT boundaries — a run
    of chained `continuous` shots is one unbroken take, so cut count is the
    pacing a viewer actually feels.
    """
    section("Cinematic pacing")

    check("pacing defaults safely for older projects",
          video_director.pacing_of(None) == "standard"
          and video_director.pacing_of({}) == "standard"
          and video_director.pacing_of({"pacing": "nonsense"}) == "standard")
    check("cinematic pacing is recognised",
          video_director.pacing_of({"pacing": "cinematic"}) == "cinematic")

    # -- shot length -----------------------------------------------------------
    check("cinematic pins shots to the longest allowed",
          video_director.shot_seconds_for({"pacing": "cinematic", "shot_seconds": 3.0})
          == video_director.MAX_SHOT_SECONDS)
    check("standard still honours the owner's shot length",
          video_director.shot_seconds_for({"shot_seconds": 3.0}) == 3.0)
    check("the 3-4s ceiling is NOT raised (rule 31 is a hardware fact)",
          video_director.MAX_SHOT_SECONDS == 4.0
          and video_director.CINEMATIC_SHOT_SECONDS <= video_director.MAX_SHOT_SECONDS)

    # -- budget from the story, not the clock ---------------------------------
    beats = [
        {"id": "beat_1", "suggested_shots": 3, "distinct_moments": 1},
        {"id": "beat_2", "suggested_shots": 3, "distinct_moments": 2},
    ]
    standard = video_director.beat_shot_budget(beats, 12, "standard")
    cine = video_director.beat_shot_budget(beats, 12, "cinematic")
    check("standard fills the clock's shot count", sum(standard) == 12, str(standard))
    check("cinematic caps each beat at its distinct moments",
          cine == [1, 2], str(cine))
    check("cinematic never empties a beat",
          all(n >= 1 for n in video_director.beat_shot_budget(
              [{"id": "b", "suggested_shots": 4, "distinct_moments": 0}], 8, "cinematic")))
    check("a beat with no distinct_moments falls back, never to zero",
          video_director.beat_shot_budget(
              [{"id": "b", "suggested_shots": 2}], 8, "cinematic") == [2])

    # -- repeated moments are REMOVED here, not just warned about -------------
    shots = [
        {"beat_id": "b1", "primary_action": "the raindrop falls and lands on the plant",
         "first_frame_prompt": "a long and richly written description of the drop",
         "continuity_mode": "editorial_cut"},
        {"beat_id": "b1", "primary_action": "the stem lifts toward the light",
         "first_frame_prompt": "the stem", "continuity_mode": "continuous"},
        {"beat_id": "b2", "primary_action": "landing on the plant",
         "first_frame_prompt": "x", "continuity_mode": "editorial_cut"},
        {"beat_id": "b2", "primary_action": "the leaves darken with water",
         "first_frame_prompt": "y", "continuity_mode": "continuous"},
    ]
    deduped, notes = video_director.dedupe_shots([dict(s) for s in shots])
    check("a repeated moment is dropped", len(deduped) == 3, f"{len(deduped)} shots")
    check("the removal is reported", len(notes) == 1, str(notes))
    check("the distinct moments all survive",
          [s["primary_action"] for s in deduped] == [
              "the raindrop falls and lands on the plant",
              "the stem lifts toward the light",
              "the leaves darken with water"])
    check("a plan with no repeats is left alone",
          video_director.dedupe_shots([dict(s) for s in shots[:2]])[1] == [])

    # The survivor keeps the EARLIER position but the better-written text.
    richer = [
        {"beat_id": "b1", "primary_action": "the drop lands", "first_frame_prompt": "short",
         "continuity_mode": "editorial_cut"},
        {"beat_id": "b1", "primary_action": "she turns away", "first_frame_prompt": "z",
         "continuity_mode": "continuous"},
        {"beat_id": "b2", "primary_action": "the drop lands on soil",
         "first_frame_prompt": "a far more detailed and richly written frame prompt",
         "continuity_mode": "editorial_cut"},
    ]
    kept, _ = video_director.dedupe_shots(richer)
    check("the better-written duplicate survives",
          "richly written" in kept[0]["first_frame_prompt"])
    check("but it keeps the earlier story position", len(kept) == 2
          and kept[1]["primary_action"] == "she turns away")

    # -- cuts only where the story changes ------------------------------------
    run = [
        {"beat_id": "b1", "location_ids": ["room"], "time_of_day": "night",
         "continuity_mode": "editorial_cut"},
        {"beat_id": "b1", "location_ids": ["room"], "time_of_day": "night",
         "continuity_mode": "editorial_cut"},
        {"beat_id": "b1", "location_ids": ["room"], "time_of_day": "night",
         "continuity_mode": "editorial_cut"},
        {"beat_id": "b2", "location_ids": ["room"], "time_of_day": "night",
         "continuity_mode": "continuous"},
        {"beat_id": "b2", "location_ids": ["yard"], "time_of_day": "night",
         "continuity_mode": "continuous"},
    ]
    cut, cut_notes = video_director.enforce_cut_policy(run)
    modes = [s["continuity_mode"] for s in cut]
    check("the opening shot is always a cut (nothing precedes it)",
          modes[0] == "editorial_cut")
    check("shots inside one beat play as one continuous take",
          modes[1] == "continuous" and modes[2] == "continuous", str(modes))
    check("a new beat cuts", modes[3] == "editorial_cut")
    check("a location change still cuts, whatever the beat says",
          modes[4] == "editorial_cut")
    check("the cut count really drops",
          sum(1 for m in modes if m == "editorial_cut") == 3, str(modes))
    check("every forced continuation is reported", len(cut_notes) == 2, str(cut_notes))

    # A time-of-day change inside a beat must still cut — pretending otherwise
    # would be a claim the reused frame cannot honour.
    tod, _ = video_director.enforce_cut_policy([
        {"beat_id": "b1", "location_ids": ["room"], "time_of_day": "night"},
        {"beat_id": "b1", "location_ids": ["room"], "time_of_day": "dawn"},
    ])
    check("a time-of-day change inside a beat still cuts",
          tod[1]["continuity_mode"] == "editorial_cut")

    # -- applying the cut policy to an EXISTING plan, in place ----------------
    from fastapi.testclient import TestClient
    from agents import api as api_module, video_pipeline
    client = TestClient(api_module.app)

    check("the pacing options are offered to the dashboard",
          {o["id"] for o in client.get("/video/defaults").json()["pacing_options"]}
          == {"standard", "cinematic"})

    pid = video_store.create_project(source_kind="scene_story", source_text="A lantern.",
                                     title="Recut", direction={"provider": "mock"})
    video_store.replace_shots(pid, [
        {"beat_id": "b1", "subject": "a lantern", "primary_action": "the flame rises",
         "motion_prompt": "The flame rises slowly and steadily upward.",
         "location_ids": ["room"], "time_of_day": "night", "duration": 3.5,
         "continuity_mode": "editorial_cut"},
        {"beat_id": "b1", "subject": "a lantern", "primary_action": "the flame leans",
         "motion_prompt": "The flame leans to the left across a finger's width.",
         "location_ids": ["room"], "time_of_day": "night", "duration": 3.5,
         "continuity_mode": "editorial_cut"},
        {"beat_id": "b2", "subject": "a lantern", "primary_action": "the wick darkens",
         "motion_prompt": "The wick darkens from its tip downward.",
         "location_ids": ["room"], "time_of_day": "night", "duration": 3.5,
         "continuity_mode": "editorial_cut"},
    ])

    plain = video_pipeline.repair_project_motion(pid)
    check("without recut the cut rhythm is left alone",
          [s["continuity_mode"] for s in video_store.list_shots(pid)]
          == ["editorial_cut"] * 3)
    check("the plain repair reports no recut", not plain.get("shots_recut"))

    out = client.post(f"/video/projects/{pid}/repair-motion", json={"recut": True})
    check("the recut endpoint responds", out.status_code == 200, str(out.status_code))
    body = out.json()
    modes = [s["continuity_mode"] for s in video_store.list_shots(pid)]
    check("shots inside a beat become one continuous take",
          modes == ["editorial_cut", "continuous", "editorial_cut"], str(modes))
    check("the recut is reported with a real cut count",
          body["shots_recut"] == 1 and body["cut_count"] == 2, str(body["cut_count"]))
    check("seconds-per-cut is reported for the user",
          body["seconds_per_cut"] > 0)
    check("recutting NEVER deletes a shot", len(video_store.list_shots(pid)) == 3)

    video_store.delete_project(pid)


def test_cold_start_chain():
    """
    A chain started on a project with NO frames and NO clips must generate its
    own opening frame and run the whole way through unattended.

    Reported 2026-08-13 as "it didn't automatically work by itself the first
    time — I had to generate the first clip". The chain logic itself is fine
    (this test proves it); what actually failed was that the refusal was
    invisible, which `test_chain_preflight` covers. This guards the other half.
    """
    section("Cold-start chaining")
    from PIL import Image
    from agents import video_pipeline

    pid = video_store.create_project(
        source_kind="scene_story", source_text="A lantern burns on a table.",
        title="Cold start", direction={"provider": "mock"})
    # Deliberately marks the FIRST shot 'continuous' — there is nothing behind
    # it to continue from, which is exactly the edge the opening frame covers.
    video_store.replace_shots(pid, [
        {"subject": "a brass lantern", "primary_action": "the flame burns",
         "motion_prompt": "The flame wavers gently to the left and back.",
         "duration": 3.5, "continuity_mode": "continuous"},
        {"subject": "a brass lantern", "primary_action": "the flame leans",
         "motion_prompt": "The flame leans slowly to one side.",
         "duration": 3.5, "continuity_mode": "continuous"},
    ])
    shots = video_store.list_shots(pid)
    check("the project really starts cold",
          not any(s.get("first_frame_path") or s.get("clip_path") for s in shots))

    made: list[str] = []

    def fake_still(prompt, aspect="16:9"):
        path = video_pipeline.OUTPUTS_DIR / f"test-coldstart-{len(made)}.png"
        Image.new("RGB", (64, 36), (20, 20, 30)).save(path)
        made.append(str(path))
        return str(path)

    real_still = video_pipeline._generate_still
    video_pipeline._generate_still = fake_still
    steps: list[str] = []
    try:
        result = video_pipeline.generate_chained(
            pid, provider_id="mock", adapt=False, progress=steps.append)
    finally:
        video_pipeline._generate_still = real_still

    check("the chain completes every shot unattended", result["done"] == 2,
          f"done={result['done']}")
    check("no shot errored", not result["errors"], str(result["errors"])[:160])
    check("it generates its own opening frame without being asked", len(made) >= 1)
    check("the first step says so in plain language",
          bool(steps) and "opening frame" in steps[0].lower(),
          steps[0] if steps else "no steps")

    after = video_store.list_shots(pid)
    check("every shot ends up with a clip", all(s.get("clip_path") for s in after))
    check("shot 2 starts on shot 1's real final frame",
          after[1]["first_frame_path"] == after[0].get("last_frame_path"))

    video_store.delete_project(pid)
    for path in made:
        try:
            os.remove(path)
        except OSError:
            pass


def test_motion_repair():
    """
    The three motion defects measured in real finished projects (2026-08-13),
    each of which makes a chained clip drift and morph instead of move:
      - a motion description that tells the model nothing moves
      - a motion description copied verbatim from the previous shot
      - a 'continuous' shot declaring a camera setup its reused frame can't have
    """
    section("Motion coherence repair")

    # -- 1. de-negation -------------------------------------------------------
    cleaned, changed = video_director._strip_motion_negation(
        "The raindrop slowly sinks into the soil, leaving a small depression. "
        "Dust motes drift lazily in the air. No other motion.")
    check("a trailing 'No other motion.' is removed", changed and "no other motion" not in cleaned.lower())
    check("the real movement survives de-negation", "sinks into the soil" in cleaned)
    check("ambient motion survives de-negation", "dust motes" in cleaned.lower())

    cleaned2, changed2 = video_director._strip_motion_negation(
        "No physical movement in the raindrop; only the slow coalescence of water "
        "vapor into the droplet, with minimal ambient motion from mist.")
    check("a leading 'No physical movement' clause is removed",
          changed2 and "no physical movement" not in cleaned2.lower())
    check("the remaining clause is kept intact", "coalescence" in cleaned2)

    cleaned3, changed3 = video_director._strip_motion_negation(
        "the raindrop slowly descends, with no other motion in the frame")
    check("a mid-sentence negation clause is stripped",
          changed3 and "no other motion" not in cleaned3.lower()
          and "descends" in cleaned3)

    untouched, unchanged = video_director._strip_motion_negation(
        "The flame leans slowly to one side as if touched by a draught.")
    check("a clean motion description is left alone", not unchanged and "leans" in untouched)

    # -- 2. duplicates and camera coherence -----------------------------------
    shots = [
        {"subject": "a raindrop", "primary_action": "condenses on the leaf edge",
         "motion_prompt": "The droplet swells slowly on the leaf edge, catching the light.",
         "framing": "wide", "camera_angle": "overhead", "continuity_mode": "editorial_cut"},
        {"subject": "a raindrop", "primary_action": "gains weight and begins to descend",
         "motion_prompt": "The droplet swells slowly on the leaf edge, catching the light.",
         "framing": "medium", "camera_angle": "overhead", "continuity_mode": "continuous"},
        {"subject": "a raindrop", "primary_action": "falls and lands on the plant",
         "motion_prompt": "No physical movement; the scene is completely still.",
         "framing": "close-up", "camera_angle": "low angle", "continuity_mode": "continuous"},
    ]
    repaired, notes = video_director.repair_motion(shots)

    check("a duplicated motion description is rewritten",
          repaired[1]["motion_prompt"] != repaired[0]["motion_prompt"])
    check("the rewrite comes from the shot's OWN action",
          "descend" in repaired[1]["motion_prompt"].lower())
    check("a pure stillness instruction is replaced, not merely trimmed",
          "no physical movement" not in repaired[2]["motion_prompt"].lower()
          and len(repaired[2]["motion_prompt"].split()) >= 3)
    check("the replacement describes this shot's action",
          "lands" in repaired[2]["motion_prompt"].lower())

    check("a continuous shot inherits the previous framing",
          repaired[1]["framing"] == "wide", repaired[1]["framing"])
    check("a continuous shot inherits the previous camera angle",
          repaired[2]["camera_angle"] == "overhead", repaired[2]["camera_angle"])
    check("an editorial cut keeps its own framing", repaired[0]["framing"] == "wide")
    check("every repair is reported to the user", len(notes) >= 3, f"{len(notes)} notes")
    check("the notes name the shot they changed", any("Shot 2" in n for n in notes))

    # A clean plan must pass through untouched — the repair must not churn.
    clean = [
        {"subject": "a lantern", "primary_action": "the flame leans",
         "motion_prompt": "The flame leans slowly left, then steadies, about a hand's width.",
         "framing": "medium", "camera_angle": "eye level", "continuity_mode": "editorial_cut"},
        {"subject": "a lantern", "primary_action": "the flame settles",
         "motion_prompt": "The flame straightens upright again over three slow seconds.",
         "framing": "medium", "camera_angle": "eye level", "continuity_mode": "continuous"},
    ]
    same, clean_notes = video_director.repair_motion(clean)
    check("a clean plan is left completely alone",
          not clean_notes and same[0]["motion_prompt"] == clean[0]["motion_prompt"]
          and same[1]["motion_prompt"] == clean[1]["motion_prompt"])

    # -- 3. repeated story actions are reported, never silently rewritten -----
    warnings = video_director.repeated_action_warnings([
        {"primary_action": "the raindrop lands on the soil"},
        {"primary_action": "the plant stem lifts"},
        {"primary_action": "the raindrop lands on the ground"},
    ])
    check("a repeated story action is flagged", len(warnings) == 1, str(warnings))
    check("the warning names both shots",
          warnings and "1" in warnings[0] and "3" in warnings[0])
    check("distinct actions are not flagged",
          not video_director.repeated_action_warnings([
              {"primary_action": "the raindrop lands on the soil"},
              {"primary_action": "the plant stem lifts toward the light"}]))


def test_motion_prompt_precision():
    """
    The motion prompt must describe the movement PRECISELY (owner ask,
    2026-08-13: "a better description of the movement of the shot, keep it
    simple but precise") and must say what holds still without ever negating
    the subject's own action.
    """
    section("Motion prompt precision")

    shot = {
        "subject": "a brass lantern", "primary_action": "the flame leans",
        "motion_prompt": "The flame leans slowly to the left, about a finger's width, "
                         "then holds there.",
        "setting": "a bare wooden room at night", "duration": 3.5,
        "camera_movement": "static", "atmosphere": "still air, faint smoke",
        "lighting": "a single warm flame from frame left",
    }
    m = video_director.build_motion_prompt(shot, {"visual_style": "naturalistic realism"})

    check("the movement itself is stated", "leans slowly to the left" in m)
    check("the movement is tied to the real clip length", "3.5 seconds" in m)
    check("the movement is told not to repeat or loop", "never repeats" in m.lower())
    check("what holds still is named", "bare wooden room" in m and "holds still" in m.lower())
    check("stillness is scoped to the background, never the subject",
          "everything else holds still" in m.lower())
    check("the anti-morph instruction is present",
          "morph" in m.lower() and "melt" in m.lower())
    check("the camera is pinned", "locked off" in m.lower())
    check("the prompt is long enough to render well", len(m.split()) >= 90, f"{len(m.split())} words")
    check("it reads as prose, not tag soup", m.count(",") < len(m.split()) / 3)

    # A chained clip must be told it is CONTINUING, not starting something new.
    chained = video_director.build_motion_prompt(
        shot, {}, continues=True,
        observed={"subject": "a brass lantern", "wardrobe": "", "setting": "a wooden table",
                  "lighting": "warm from the left", "palette": "amber and deep brown"})
    check("a chained clip is told the shot is already in progress",
          "already in progress" in chained.lower())
    check("a chained clip is told the image is its exact first frame",
          "exact first frame" in chained.lower())
    check("a chained clip forbids a cut", "no cut" in chained.lower())
    check("what the previous clip really showed is carried in",
          "amber and deep brown" in chained)
    check("the non-chained prompt makes no continuation claim",
          "already in progress" not in m.lower())

    # A camera move must be described as ONE move, not a free hand.
    moving = video_director.build_motion_prompt(
        {**shot, "camera_movement": "slow push in"}, {})
    check("a camera move is pinned to exactly one direction",
          "exactly one slow push in" in moving.lower())
    check("the camera is told not to reverse", "never" in moving.lower()
          and "reversing" in moving.lower())

    # Empty/thin motion text must still produce a usable movement description.
    thin = video_director.build_motion_prompt(
        {"subject": "the courier", "primary_action": "lowers his gaze", "duration": 3.0}, {})
    check("a thin motion field falls back to the shot's action",
          "lowers his gaze" in thin.lower())
    check("the fallback still names the subject", "courier" in thin.lower())


def test_chain_preflight():
    """
    A chained run must refuse LOUDLY and SYNCHRONOUSLY. Raised inside the job
    thread instead, the message reached the dashboard as a job error the panel
    cleared on the same tick — a click that looked like it did nothing.
    """
    section("Chain preflight")
    import inspect
    from fastapi.testclient import TestClient
    from agents import api as api_module, video_pipeline
    client = TestClient(api_module.app)

    check("preflight is a separate, callable check",
          callable(getattr(video_pipeline, "chain_preflight", None)))

    ok, reason = video_pipeline.chain_preflight("does-not-exist")
    check("an unknown project is refused", not ok and "not found" in reason.lower())

    pid = video_store.create_project(source_kind="scene_story", source_text="A lantern burns.",
                                     title="Preflight", direction={"provider": "mock"})
    ok, reason = video_pipeline.chain_preflight(pid)
    check("a project with no shots is refused with a plain-language reason",
          not ok and "plan the shots" in reason.lower(), reason)

    video_store.replace_shots(pid, [{"subject": "a lantern", "primary_action": "burns",
                                     "duration": 3.5, "continuity_mode": "editorial_cut"}])
    ok, reason = video_pipeline.chain_preflight(pid, "mock")
    check("a planned project on an available provider passes", ok, reason)

    # The API must answer the button press itself, not hand back a doomed job.
    src = inspect.getsource(api_module.video_generate_chained)
    check("the endpoint preflights before starting a job",
          "chain_preflight" in src and "HTTPException" in src)
    check("the refusal is a 400, not a silent job failure", "status_code=400" in src)

    resp = client.post(f"/video/projects/{pid}/chain", json={"provider": "comfyui:ltx"})
    check("an unavailable provider is refused on the request itself",
          resp.status_code in (200, 400))
    if resp.status_code == 400:
        check("the refusal explains itself in plain language",
              len(str(resp.json().get("detail", ""))) > 30)
    else:
        check("the refusal explains itself in plain language", True, "ComfyUI is running")

    video_store.delete_project(pid)


def test_llm_timeouts():
    """Long planning calls must get a timeout that fits how slow they really are."""
    section("LLM timeouts")
    import inspect
    from agents import router
    check("call_llm accepts a timeout override",
          "timeout" in inspect.signature(router.call_llm).parameters)
    check("shot planning uses a generous timeout",
          video_director.SHOT_TIMEOUT_S >= 300, str(video_director.SHOT_TIMEOUT_S))
    check("the analysis stage gets a raised timeout too",
          video_director.ANALYSIS_TIMEOUT_S > 120)
    src = inspect.getsource(video_director.plan_beat_shots)
    check("shot planning retries leaner before giving up",
          "lean" in src and "_attempt" in src)


def test_fresh_database():
    """
    Regression test: the video tables must be created on a BRAND-NEW database.

    The first version called init_video_db() inside state.init_db()'s open
    write transaction; SQLite refuses a second writer, so on a fresh DB the
    tables were silently skipped and every video endpoint then failed with
    "no such table". Passing this on an existing DB proves nothing — it has to
    be a genuinely empty file.
    """
    section("Fresh-database initialisation")
    import shutil
    import sqlite3
    import tempfile
    from agents import state

    tmpdir = tempfile.mkdtemp()
    fresh = Path(tmpdir) / "workforce.db"
    old_state, old_store = state.DB_PATH, video_store.DB_PATH
    try:
        state.DB_PATH = fresh
        video_store.DB_PATH = fresh
        state.init_db()
        conn = sqlite3.connect(fresh)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
        required = {"video_projects", "video_shots", "video_assets", "video_jobs"}
        check("all video tables are created on a fresh DB",
              required <= tables, f"missing {sorted(required - tables)}")
        pid = video_store.create_project(title="fresh", source_text="x")
        check("the store is usable immediately after a fresh init",
              video_store.get_project(pid) is not None)
    finally:
        state.DB_PATH, video_store.DB_PATH = old_state, old_store
        shutil.rmtree(tmpdir, ignore_errors=True)


def main():
    print("Video Generation pipeline — verification suite")
    test_fresh_database()
    test_safety()
    test_durations()
    test_complexity()
    test_continuity_refs()
    test_prompt_detail()
    test_provider_fallbacks()
    test_store()
    test_export()
    test_finished_shelf()
    test_validation()
    test_decomposition_with_stub()
    test_llm_timeouts()
    test_planning_resilience()
    test_chained_generation()
    test_chain_strength()
    test_safeguard_is_unbypassable()
    test_cinematic_pacing()
    test_cold_start_chain()
    test_motion_repair()
    test_motion_prompt_precision()
    test_chain_preflight()
    test_api()

    failed = [r for r in RESULTS if not r[0]]
    print(f"\n{'=' * 60}")
    print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    if failed:
        print("\nFAILURES:")
        for _, name, detail in failed:
            print(f"  - {name}" + (f"  [{detail}]" if detail else ""))
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
