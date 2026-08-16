"""
Director — the LLM stages of the Video Generation pipeline.

Turns a scene, story, historical account or passage into: a story analysis,
a continuity bible of locked recurring elements, and a plan of MANY simple
3–4 second shots.

The governing design principle (owner spec): break complex stories into many
simple shots rather than asking a small video model for one complicated one.
An 8GB-class local model cannot resolve two simultaneous actions, a moving
camera over a moving crowd, or a change of location mid-clip. So simplicity
is enforced twice — asked for in the prompt, then measured and repaired in
code by `complexity_score` / `split_complex_shots`, because prompt compliance
is never trusted for a constraint that decides whether output is usable.

Routing note (hard rule 1): these prompts run on whatever `call_llm` gives
them, which for non-Grok task types is local Qwen with a tight context. Each
prompt is therefore kept lean and each stage is a SEPARATE call with a small
JSON schema, rather than one giant "produce the whole film" call.
"""

import json
import re

from agents.router import call_llm
from agents import video_safety

# Shot duration is the spine of the whole design. Clamped in code — a model
# that returns 8.0 gets corrected, never obeyed.
MIN_SHOT_SECONDS = 3.0
MAX_SHOT_SECONDS = 4.0
DEFAULT_SHOT_SECONDS = 3.5

# Above this score a shot is too busy for a small model and is split.
COMPLEXITY_LIMIT = 3

# --- Pacing ------------------------------------------------------------------
#
# The shot budget used to be derived from the CLOCK alone: a 60s target at 3.5s
# per shot means 17 shots, distributed across the beats whether or not the
# story has 17 distinct moments in it. Asked for three shots covering a beat
# that contains one, the planner pads by restating the same action — measured
# on a real project, the raindrop landed FOUR separate times (2026-08-13).
#
# 'cinematic' pacing derives the budget from the STORY instead and lets the
# finished video come in under the target rather than padding to it. It also
# cuts far less often, which is the larger lever: "Adam's New Day" cut every
# 4.7 seconds across 122s, and no amount of frame chaining makes that calm.
# A run of `continuous` shots is ONE unbroken take once chained (each clip
# starts on the previous clip's real final frame), so a beat playing as four
# continuous 4s shots reads as a single 16-second take, not four cuts.
PACING_MODES = ("standard", "cinematic")
DEFAULT_PACING = "standard"

# Cinematic shots sit at the top of the enforced 3-4s window. The ceiling is
# NOT raised: rule 31 exists because an 8GB-class model degrades badly past it,
# which is a hardware fact, not a preference.
CINEMATIC_SHOT_SECONDS = MAX_SHOT_SECONDS

# How far apart two shots can be and still count as the same repeated moment.
DEDUPE_WINDOW = 4


def pacing_of(direction: dict | None) -> str:
    """The pacing mode for a project, defaulting safely for older projects."""
    value = str((direction or {}).get("pacing") or DEFAULT_PACING).strip().lower()
    return value if value in PACING_MODES else DEFAULT_PACING


def shot_seconds_for(direction: dict | None) -> float:
    """
    The per-shot length a project should use. Cinematic pacing pins shots to
    the top of the allowed window; otherwise the owner's own setting wins.
    """
    direction = direction or {}
    if pacing_of(direction) == "cinematic":
        return CINEMATIC_SHOT_SECONDS
    return clamp_duration(direction.get("shot_seconds") or DEFAULT_SHOT_SECONDS)


def beat_shot_budget(beats: list[dict], total_wanted: int, pacing: str) -> list[int]:
    """
    How many shots each beat gets.

    'standard' distributes the clock's shot count across the beats by their own
    suggestion — the total always matches the target duration.

    'cinematic' treats that share as a CEILING and caps it at the number of
    distinct visual moments the beat actually contains, so a beat with one
    moment gets one shot instead of three restatements of it. The finished
    video is then as long as the story is, which is the point.
    """
    weights = [max(1, int(b.get("suggested_shots") or 1)) for b in beats]
    weight_sum = sum(weights) or 1
    shares = [max(1, round(total_wanted * w / weight_sum)) for w in weights]
    if pacing != "cinematic":
        return shares

    budget = []
    for beat, share in zip(beats, shares):
        moments = beat.get("distinct_moments")
        try:
            moments = int(moments)
        except (TypeError, ValueError):
            moments = int(beat.get("suggested_shots") or share)
        budget.append(max(1, min(share, max(1, moments))))
    return budget

_TASK = "video_direction"  # not in GROK_TASK_TYPES → local model by default

# Generous timeouts. These prompts deliberately ask for several hundred words
# of detail, and on a busy 8GB card a single beat can take minutes — the 120s
# router default produced a real mid-plan read timeout that threw away six
# already-planned beats (2026-08-12). Planning is a background job the user
# never waits on synchronously, so a long ceiling costs nothing.
ANALYSIS_TIMEOUT_S = 300
BIBLE_TIMEOUT_S = 420
SHOT_TIMEOUT_S = 420


def _extract_json(text: str) -> dict | list:
    """
    Parse a model reply that should be JSON. Tolerates ```json fences and
    leading prose, which local models still emit occasionally even in
    json_mode. Raises ValueError with the raw text when nothing parses, so a
    failure surfaces loudly instead of silently producing an empty plan.
    """
    if not text or not text.strip():
        raise ValueError("The model returned an empty response.")
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(cleaned)
    except ValueError:
        pass
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = cleaned.find(opener), cleaned.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(cleaned[start:end + 1])
            except ValueError:
                continue
    raise ValueError(f"Could not parse JSON from the model reply: {text[:400]}")


def shot_count_for(target_seconds: float, shot_seconds: float = DEFAULT_SHOT_SECONDS) -> int:
    """
    How many shots a target duration needs. The owner's own worked example —
    60s → roughly 15–20 shots — falls straight out of 3–4s shots.
    """
    shot_seconds = clamp_duration(shot_seconds)
    return max(1, round(float(target_seconds) / shot_seconds))


def clamp_duration(seconds) -> float:
    """Force any duration into the 3–4s window the whole pipeline assumes."""
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        return DEFAULT_SHOT_SECONDS
    return round(min(MAX_SHOT_SECONDS, max(MIN_SHOT_SECONDS, value)), 2)


# --- Stage 3: story analysis -------------------------------------------------

def analyze_story(source_text: str, direction: dict | None = None,
                  instructions: str = "") -> dict:
    """
    Divide the source into BEATS first, then note how many simple shots each
    beat needs. Returns the analysis dict; `sacred_flags` is filled in
    deterministically by video_safety, never by the model.
    """
    direction = direction or {}
    brief_bits = [
        f"Style: {direction.get('visual_style')}" if direction.get("visual_style") else "",
        f"Period: {direction.get('historical_period')}" if direction.get("historical_period") else "",
        f"Setting: {direction.get('setting')}" if direction.get("setting") else "",
        f"Mood: {direction.get('mood')}" if direction.get("mood") else "",
        f"Audience: {direction.get('audience')}" if direction.get("audience") else "",
    ]
    brief = "\n".join(b for b in brief_bits if b)
    target = float(direction.get("target_seconds") or 60)
    shots_needed = shot_count_for(target, direction.get("shot_seconds") or DEFAULT_SHOT_SECONDS)

    system = (
        "You are a film director planning a short video. You break stories into "
        "beats and then into MANY simple shots. You reply with JSON only."
    )
    user = (
        f"Analyse this source for a {target:.0f}-second video of about {shots_needed} "
        f"shots (each 3-4 seconds).\n\n"
        f"{brief}\n"
        f"{('Extra instructions: ' + instructions) if instructions else ''}\n\n"
        f"SOURCE:\n{source_text[:6000]}\n\n"
        f"{video_safety.director_guidance()}\n\n"
        "Return JSON with exactly these keys:\n"
        '{"summary": "2-3 sentences",\n'
        ' "central_message": "one sentence",\n'
        ' "characters": [{"id":"char_slug","name":"","description":"","role":""}],\n'
        ' "locations": [{"id":"loc_slug","name":"","description":""}],\n'
        ' "props": [{"id":"prop_slug","name":"","description":""}],\n'
        ' "beats": [{"id":"beat_1","title":"","summary":"","emotion":"",'
        '"suggested_shots":3,"distinct_moments":2}],\n'
        ' "emotional_progression": "one sentence",\n'
        ' "narration_notes": "",\n'
        ' "continuity_risks": ["..."],\n'
        ' "do_not_depict_literally": ["..."]}\n\n'
        f"Plan enough beats that their suggested_shots sum to roughly {shots_needed}. "
        "distinct_moments is how many genuinely DIFFERENT things a viewer would "
        "SEE happen in that beat — count separate visible events, not restatements "
        "of one event. A beat where a drop falls and lands has 2, not 4. Be honest "
        "and small here; it is used to stop the same moment being filmed repeatedly. "
        "Beats must not overlap: each event belongs to exactly ONE beat. "
        "Ids must be lowercase slugs. No prose outside the JSON."
    )
    raw = call_llm(_TASK, [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ], temperature=0.6, max_tokens=2200, json_mode=True, timeout=ANALYSIS_TIMEOUT_S, agent="director")
    data = _extract_json(raw)
    if not isinstance(data, dict):
        raise ValueError("Story analysis did not return a JSON object.")

    for key, default in (("characters", []), ("locations", []), ("props", []),
                         ("beats", []), ("continuity_risks", []),
                         ("do_not_depict_literally", [])):
        if not isinstance(data.get(key), list):
            data[key] = default
    for key in ("summary", "central_message", "emotional_progression", "narration_notes"):
        data.setdefault(key, "")

    # Deterministic safeguard scan (never the model's judgement): flag sacred
    # figures in the SOURCE so the user sees it before any shot is built.
    scan = video_safety.scan_text(source_text)
    data["sacred_flags"] = scan
    if scan["has_reference"]:
        names = ", ".join(scan["figures"])
        note = (f"This source references {names}. Manifestations of God are never "
                "portrayed visually — shots will show them indirectly (reactions, "
                "environment, objects, an empty threshold) and narration may name "
                "them with reverence.")
        if note not in data["do_not_depict_literally"]:
            data["do_not_depict_literally"].insert(0, note)
    return data


# --- Stage 4: continuity bible ----------------------------------------------

def build_continuity_bible(analysis: dict, direction: dict | None = None) -> dict:
    """
    Lock the look of every recurring element so shots can REFERENCE ids rather
    than reinventing descriptions (which is how a small model drifts).
    """
    direction = direction or {}
    system = ("You are a film continuity supervisor. You write vivid, concrete, "
              "specific visual descriptions that a text-to-image model can follow "
              "exactly and reproduce identically across many shots. "
              "You reply with JSON only.")
    user = (
        "Write a continuity bible for this video.\n\n"
        f"Summary: {analysis.get('summary', '')}\n"
        f"Style: {direction.get('visual_style', 'cinematic realism')}\n"
        f"Period: {direction.get('historical_period', 'unspecified')}\n"
        f"Palette: {direction.get('color_palette', 'natural')}\n"
        f"Characters: {json.dumps(analysis.get('characters', [])[:8])}\n"
        f"Locations: {json.dumps(analysis.get('locations', [])[:6])}\n"
        f"Props: {json.dumps(analysis.get('props', [])[:8])}\n\n"
        f"{video_safety.director_guidance()}\n\n"
        "Return JSON:\n"
        '{"characters":[{"id":"","name":"","appearance":"","age":"","hair":"",'
        '"clothing":"","colors":"","accessories":"","relationships":""}],\n'
        ' "locations":[{"id":"","name":"","architecture":"","geography":"",'
        '"time_of_day":"","weather":""}],\n'
        ' "props":[{"id":"","name":"","description":""}],\n'
        ' "style":{"visual_style":"","lighting":"","color_treatment":"",'
        '"historical_details":"","content_restrictions":""}}\n\n'
        "These descriptions are pasted into EVERY shot that features the element, so "
        "they are what keeps a character or place from drifting between shots. Make "
        "each field 15-35 words of specific, concrete visual detail — name exact "
        "fabrics, materials, colours, wear and distinguishing features (e.g. "
        "'coarse undyed wool cloak, damp at the shoulders, frayed hem, bone toggle "
        "at the throat' rather than 'simple clothing'). Never use metaphor or "
        "abstraction, and never describe personality — only what a camera sees. "
        "Reuse the ids you were given. No prose outside the JSON."
    )
    # Higher ceiling than the other stages: richer per-element descriptions are
    # the point of this call, and a truncated bible loses continuity anchors.
    raw = call_llm(_TASK, [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ], temperature=0.5, max_tokens=3200, json_mode=True, timeout=BIBLE_TIMEOUT_S, agent="director")
    data = _extract_json(raw)
    if not isinstance(data, dict):
        raise ValueError("Continuity bible did not return a JSON object.")
    for key in ("characters", "locations", "props"):
        if not isinstance(data.get(key), list):
            data[key] = []
    if not isinstance(data.get("style"), dict):
        data["style"] = {}
    # `locked` starts empty; the user locks elements from the UI and locking is
    # then honoured by update_shot / regeneration.
    data.setdefault("locked", [])
    return data


def _decap(text: str) -> str:
    """
    Lowercase a fragment's leading capital so it reads correctly mid-sentence.
    Models return sentence-cased field values ("Coarse undyed wool tunic"),
    which produce "wearing Coarse undyed wool tunic" once joined. Left alone
    when the word is an acronym or fully capitalised.
    """
    s = str(text or "").strip()
    if not s:
        return ""
    first = s.split()[0]
    if first.isupper() or (len(first) > 1 and first[1:].lower() != first[1:]):
        return s  # acronym or CamelCase — leave it
    return s[0].lower() + s[1:]


def _clause(*parts) -> str:
    """Join only the non-empty parts with commas — no empty `; ;` gaps."""
    return ", ".join(_decap(str(p).strip().strip(",;")) for p in parts
                     if p and str(p).strip().strip(",;"))


def continuity_reference_block(continuity: dict, character_ids: list[str],
                               location_ids: list[str], prop_ids: list[str]) -> str:
    """
    Render the locked descriptions for the ids a shot references. This string
    is what actually travels into the image prompt — the mechanism that keeps a
    character looking the same across shots.

    Written as PROSE sentences, not delimited fields. An earlier version
    interpolated every field unconditionally and joined with pipes, which
    produced `The courier: lean, sixties; ; ; wearing wool cloak bone toggle
    faded indigo tarnished buckle | The camp: ...` — empty gaps, run-together
    phrases and tag-soup separators, for ~a third of the whole prompt. These
    models are trained on natural-language captions, so that shape actively
    costs quality on the exact block that matters most for continuity.
    """
    if not continuity:
        return ""
    sentences: list[str] = []

    for entry in continuity.get("characters", []):
        if entry.get("id") not in character_ids:
            continue
        name = entry.get("name") or entry.get("id")
        looks = _clause(entry.get("appearance"), entry.get("age"), entry.get("hair"))
        worn = _clause(entry.get("clothing"), entry.get("colors"), entry.get("accessories"))
        bits = [f"{name} is {looks}" if looks else name]
        if worn:
            bits.append(f"wearing {worn}")
        sentences.append(_sentence(", ".join(bits)))

    for entry in continuity.get("locations", []):
        if entry.get("id") not in location_ids:
            continue
        name = entry.get("name") or entry.get("id")
        detail = _clause(entry.get("architecture"), entry.get("geography"),
                         entry.get("time_of_day"), entry.get("weather"))
        sentences.append(_sentence(f"{name}: {detail}" if detail else name))

    for entry in continuity.get("props", []):
        if entry.get("id") not in prop_ids:
            continue
        name = entry.get("name") or entry.get("id")
        desc = str(entry.get("description") or "").strip()
        sentences.append(_sentence(f"{name}, {desc}" if desc else name))

    style = continuity.get("style") or {}
    style_text = _clause(style.get("visual_style"),
                         (f"{style.get('lighting')} lighting" if style.get("lighting") else ""),
                         style.get("color_treatment"), style.get("historical_details"))
    if style_text:
        sentences.append(_sentence(style_text))

    return " ".join(s for s in sentences if s)


# --- Stage 5: shot planning --------------------------------------------------

SHOT_FIELDS = (
    "beat_id", "duration", "narrative_purpose", "subject", "primary_action",
    "setting", "time_of_day", "framing", "camera_angle", "camera_movement",
    "lighting", "mood", "character_ids", "location_ids", "prop_ids",
    "first_frame_prompt", "last_frame_prompt", "motion_prompt", "negative_prompt",
    "narration", "dialogue", "sound_notes", "transition", "continuity_notes",
    "continuity_mode",
    # Descriptive-detail fields. These raise render quality without adding any
    # narrative or camera complexity — see DETAIL_GUIDANCE for why the two are
    # kept strictly apart.
    "subject_detail", "setting_detail", "texture_notes", "atmosphere",
    "depth_notes", "lens",
)

DEFAULT_NEGATIVE = (
    "blurry, low quality, low resolution, distorted, deformed, disfigured, extra limbs, "
    "extra fingers, fused fingers, bad anatomy, warped face, text, caption, subtitles, "
    "watermark, signature, logo, fast motion, motion smear, jitter, flicker, "
    "busy crowd, complex choreography, multiple simultaneous actions, "
    "oversaturated, washed out, plastic skin, uncanny, "
    # Aimed squarely at the observed chained-generation failure: given a real
    # first frame and too little movement to render, these models hold the
    # composition and slowly dissolve the texture instead. That is what reads
    # as "trippy", and it is a different defect from ordinary blur.
    "morphing, melting, dissolving, shape-shifting, warping geometry, rippling "
    "distortion, drifting texture, swimming detail, psychedelic, kaleidoscopic, "
    "hallucinated detail, objects turning into other objects, unstable identity, "
    "flickering shapes, boiling edges"
)

# The single most important lever on output quality for these models.
#
# LTX-Video's own documentation is blunt about it ("this model needs long
# descriptive prompts, if the prompt is too short the quality will suffer
# greatly"), and Wan behaves the same way. But "detailed" must never be
# confused with "complicated": richness belongs in the DESCRIPTION (materials,
# light, texture, atmosphere, optics), while the ACTION and CAMERA stay
# stubbornly simple. Those are two different axes and this block says so
# explicitly, because a model told only "be detailed" will happily add a
# second action and a camera move too.
DETAIL_GUIDANCE = (
    "DESCRIPTIVE DETAIL — be EXHAUSTIVE here (this is what makes the render "
    "high quality; short prompts produce visibly worse video):\n"
    "- Subject: age, build, posture, expression, skin and hair detail, how the "
    "light falls on the face\n"
    "- Materials and texture: the exact fabric, weave, wear, dust, damp, metal, "
    "wood grain, stone, leather — name them\n"
    "- Colour: specific hues, not 'colourful' (e.g. 'faded indigo', 'oxidised "
    "brass', 'ochre dust')\n"
    "- Light: its SOURCE, direction, hardness and colour temperature (e.g. 'low "
    "amber oil-lamp light from frame left, soft falloff, deep shadow on the "
    "right cheek')\n"
    "- Atmosphere: haze, dust motes, rain, breath vapour, smoke, humidity\n"
    "- Depth: what is in the foreground, midground and background, and what is "
    "sharp versus softly out of focus\n"
    "- Optics and medium: focal length feel, depth of field, grain, and the "
    "look (e.g. 'shot on 50mm, shallow depth of field, fine 35mm grain, "
    "naturalistic colour')\n\n"
    "VISUAL SIMPLICITY — stay strict (this is a SMALL model on modest hardware):\n"
    "- Still ONE subject, ONE action, ONE simple camera behaviour\n"
    "- Detail describes what things LOOK like; it must never add a second thing "
    "HAPPENING\n"
    "- Rich, still, specific beats busy every time"
)


def _normalize_shot(shot: dict, beat_id: str = "") -> dict:
    """Force one raw model shot into the full record shape with safe defaults."""
    out = {field: shot.get(field, "") for field in SHOT_FIELDS}
    out["beat_id"] = shot.get("beat_id") or beat_id
    out["duration"] = clamp_duration(shot.get("duration", DEFAULT_SHOT_SECONDS))
    for key in ("character_ids", "location_ids", "prop_ids"):
        value = shot.get(key)
        out[key] = [str(v) for v in value] if isinstance(value, list) else []
    out["negative_prompt"] = shot.get("negative_prompt") or DEFAULT_NEGATIVE
    out["camera_movement"] = shot.get("camera_movement") or "static"
    out["transition"] = shot.get("transition") or "cut"
    out["continuity_mode"] = (shot.get("continuity_mode")
                              if shot.get("continuity_mode") in ("continuous", "editorial_cut")
                              else "editorial_cut")
    out["complexity_score"] = complexity_score(out)
    return out


def plan_beat_shots(beat: dict, analysis: dict, continuity: dict,
                    direction: dict, shots_wanted: int) -> list[dict]:
    """
    Plan the shots for ONE beat. Planning beat-by-beat (rather than the whole
    film in a single call) is what keeps each prompt inside the local model's
    context and each shot genuinely simple — see hard rule 1.
    """
    direction = direction or {}
    known_chars = [c.get("id") for c in analysis.get("characters", []) if c.get("id")]
    known_locs = [l.get("id") for l in analysis.get("locations", []) if l.get("id")]
    known_props = [p.get("id") for p in analysis.get("props", []) if p.get("id")]

    system = ("You are a shot designer and cinematographer working with a SMALL local "
              "video model. You write EXHAUSTIVELY detailed descriptions of visually "
              "SIMPLE shots. You reply with JSON only.")
    user = (
        f"Break this story beat into exactly {shots_wanted} shot(s) of 3-4 seconds each.\n\n"
        f"BEAT: {beat.get('title','')} — {beat.get('summary','')}\n"
        f"Emotion: {beat.get('emotion','')}\n"
        f"Style: {direction.get('visual_style','cinematic realism')}; "
        f"period {direction.get('historical_period','unspecified')}; "
        f"mood {direction.get('mood','')}; "
        f"palette {direction.get('color_palette','natural')}\n"
        f"Known character ids: {known_chars}\n"
        f"Known location ids: {known_locs}\n"
        f"Known prop ids: {known_props}\n\n"
        "HARD SHOT RULES — a shot that breaks these is unusable:\n"
        "- ONE primary subject (or a small, still, clearly arranged group)\n"
        "- ONE primary action; never two actions in sequence in one shot\n"
        "- ONE simple camera behaviour: static, or a single slow push/pan\n"
        "- No location change inside a shot, no crowds unless distant and still\n"
        "- No complex hand actions, no visible dialogue or lip-sync\n"
        "- Minimal background motion; clear silhouette; readable composition\n"
        "- Concrete visual language only — never poetic abstraction\n"
        "- Narration/sound go in their own fields, NEVER in the visual prompts\n\n"
        f"{DETAIL_GUIDANCE}\n\n"
        f"{video_safety.director_guidance()}\n\n"
        'Return JSON: {"shots":[{'
        '"narrative_purpose":"","subject":"","subject_detail":"","primary_action":"",'
        '"setting":"","setting_detail":"","time_of_day":"",'
        '"framing":"wide|medium|close-up","camera_angle":"",'
        '"camera_movement":"static|slow push in|slow pan left","lighting":"",'
        '"atmosphere":"","texture_notes":"","depth_notes":"","lens":"","mood":"",'
        '"character_ids":[],"location_ids":[],"prop_ids":[],'
        '"first_frame_prompt":"","last_frame_prompt":"","motion_prompt":"",'
        '"narration":"","dialogue":"","sound_notes":"","transition":"cut",'
        '"continuity_mode":"continuous|editorial_cut","continuity_notes":"",'
        '"duration":3.5}]}\n\n'
        "LENGTH REQUIREMENTS (these matter — short prompts render badly):\n"
        "- first_frame_prompt and last_frame_prompt: 50-90 words EACH, written as "
        "flowing descriptive prose, not comma-separated tags. Each describes one "
        "STILL photograph: the same scene at the start and at the end of the action, "
        "differing ONLY by the movement that happened between them. Repeat the subject "
        "and setting detail in both so they match.\n"
        "- motion_prompt: 30-60 words describing the ONE movement precisely — name "
        "what moves, which DIRECTION it travels, HOW FAR across the frame it gets in "
        "3-4 seconds, and how slowly. Add at most one small ambient motion (cloth, "
        "dust, flame, breath, hair). NEVER write that nothing moves, that the subject "
        "is still, or 'no other motion' — every shot must contain one real, visible "
        "movement, and stillness of the background is added automatically. Each shot's "
        "movement must be DIFFERENT from the shot before it and must carry the story "
        "forward, never repeat it.\n"
        "- primary_action: ONE short clause. Keep this one brief; the detail belongs "
        "in the frame prompts.\n"
        "- subject_detail, setting_detail, texture_notes, atmosphere, depth_notes, "
        "lens: one specific phrase each, concrete nouns and materials.\n"
        "- IMPORTANT: when the shot uses a known character or location id, do NOT "
        "re-describe that character's fixed appearance or wardrobe in subject_detail, "
        "or that location's fixed features in setting_detail — those are already "
        "locked in the continuity bible and will be added automatically. Use those "
        "fields only for what is true in THIS MOMENT: posture, expression, where the "
        "light falls, what is wet or dusty right now.\n\n"
        "Use continuity_mode 'continuous' when this shot carries straight on from the "
        "previous camera setup, otherwise 'editorial_cut'. No prose outside the JSON."
    )

    def _attempt(prompt: str, max_tokens: int) -> list[dict]:
        raw = call_llm(_TASK, [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ], temperature=0.7, max_tokens=max_tokens, json_mode=True, timeout=SHOT_TIMEOUT_S, agent="director")
        data = _extract_json(raw)
        raw_shots = data.get("shots") if isinstance(data, dict) else data
        if not isinstance(raw_shots, list) or not raw_shots:
            raise ValueError(f"Shot planning returned no shots for beat {beat.get('id')}.")
        return [_normalize_shot(s, beat.get("id", "")) for s in raw_shots
                if isinstance(s, dict)]

    try:
        return _attempt(user, 4000)
    except Exception as first_error:
        # One leaner retry. A timeout or a truncated/unparseable reply usually
        # means the request was simply too big for the machine right now, so
        # asking for the same thing again would fail the same way — this asks
        # for shorter prompts and a smaller budget instead. Still detailed, just
        # less of it, which beats losing the beat entirely.
        lean = (user
                .replace("50-90 words EACH", "35-50 words EACH")
                .replace("20-40 words describing", "15-25 words describing")
                + "\n\nKeep the reply compact: prioritise finishing all shots over "
                  "maximum length in any one field.")
        try:
            return _attempt(lean, 2600)
        except Exception:
            raise first_error


# --- Complexity: measured in code, not asked of the model --------------------

_MULTI_ACTION = re.compile(
    r"\b(then|after that|while|as she|as he|as they|simultaneously|meanwhile|"
    r"and then|before turning|followed by)\b", re.IGNORECASE)
# A crowd only counts against a shot when it is ACTIVE and near. "a distant
# crowd, motionless" is explicitly allowed by the shot rules, so the qualifier
# exempts it — otherwise richer background description would be punished.
_CROWD = re.compile(r"\b(crowd|throng|multitude|army|battle|mob|dozens|hundreds)\b",
                    re.IGNORECASE)
_CROWD_OK = re.compile(
    r"\b(distant|far|faraway|background|blurred|out of focus|still|static|"
    r"motionless|silhouetted|unmoving)\b", re.IGNORECASE)
# Hand-INTENSIVE manipulation, not any mention of hands. Detailed prompts
# legitimately say "his weathered hands rest on the pommel"; that is texture,
# not fine motor work a small model will mangle.
_HANDS = re.compile(
    r"\b(writing|sewing|knitting|typing|juggl\w*|threading|tying|untying|carving|"
    r"counting (?:coins|money)|fastening|unfastening|braiding|weaving|"
    r"(?:hands?|fingers?)\s+(?:manipulat\w*|assembl\w*|work\w*|fumbl\w*))\b",
    re.IGNORECASE)
_COMPLEX_CAMERA = re.compile(
    r"\b(tracking|dolly|crane|handheld|orbit|whip|zoom out and|pan and tilt|"
    r"follows the|circles?)\b", re.IGNORECASE)

# Backstop for a genuinely rambling ACTION. Deliberately generous: descriptive
# richness now lives in the frame prompts, and the action field itself should
# still be one clear beat. Only counted on primary_action, never on the
# (intentionally verbose) motion or frame prompts.
_ACTION_WORD_CAP = 45


def complexity_score(shot: dict) -> int:
    """
    0–6ish. Each point is one thing a SMALL local model handles badly.
    Deterministic, so the same shot always scores the same.

    Measures NARRATIVE and CAMERA complexity only — never descriptive richness.
    Detailed prompts are the goal (they raise generation quality); what breaks a
    small model is two actions, a moving camera over a moving subject, or fine
    hand work. Keep those separate or detailed shots get split for no reason.
    """
    action = f"{shot.get('primary_action', '')} {shot.get('motion_prompt', '')}"
    score = 0
    if _MULTI_ACTION.search(action):
        score += 2                                   # sequential actions: worst offender
    crowd_field = f"{shot.get('subject', '')} {shot.get('setting', '')} {action}"
    if _CROWD.search(crowd_field) and not _CROWD_OK.search(crowd_field):
        score += 1
    if _HANDS.search(action):
        score += 1
    if _COMPLEX_CAMERA.search(str(shot.get("camera_movement", "")) + " " + action):
        score += 1
    if len(shot.get("character_ids") or []) > 2:
        score += 1
    if len(str(shot.get("primary_action", "")).split()) > _ACTION_WORD_CAP:
        score += 1
    return score


def split_complex_shots(shots: list[dict]) -> tuple[list[dict], list[str]]:
    """
    Split any shot over the complexity limit into two simpler halves, in code.
    A model asked to "keep it simple" regularly does not; this is what makes
    the guarantee real. Returns (shots, notes) — notes surface in the UI so the
    split is visible rather than mysterious.
    """
    out: list[dict] = []
    notes: list[str] = []
    for shot in shots:
        score = complexity_score(shot)
        if score <= COMPLEXITY_LIMIT:
            shot = dict(shot)
            shot["complexity_score"] = score
            out.append(shot)
            continue
        first, second = _split_shot(shot)
        out.append(first)
        out.append(second)
        # ASCII arrow on purpose: these notes are printed by scripts, and the
        # Windows console is cp1252 (see AGENTS.md gotchas).
        notes.append(
            f"Split a shot that was too complex for a small model "
            f"(score {score} > {COMPLEXITY_LIMIT}): "
            f"\"{str(shot.get('primary_action',''))[:60]}\" -> two simpler shots."
        )
    return out, notes


def _split_shot(shot: dict) -> tuple[dict, dict]:
    """
    Divide one busy shot into two. The action text is cut at its first
    sequential connective ("then", "while", ...) when there is one, so the
    halves follow the story rather than being arbitrary.
    """
    action = str(shot.get("primary_action", "")).strip()
    match = _MULTI_ACTION.search(action)
    if match:
        head, tail = action[:match.start()].strip(" ,."), action[match.end():].strip(" ,.")
    else:
        words = action.split()
        mid = max(1, len(words) // 2)
        head, tail = " ".join(words[:mid]), " ".join(words[mid:])
    head = head or action or "the subject holds still"
    tail = tail or "the moment settles"

    first = dict(shot)
    second = dict(shot)

    first["primary_action"] = head
    first["last_frame_prompt"] = shot.get("first_frame_prompt", "") or shot.get("last_frame_prompt", "")
    first["motion_prompt"] = f"{head}; single slow movement, minimal background motion"
    first["camera_movement"] = "static"
    first["narration"] = shot.get("narration", "")
    first["continuity_notes"] = (str(shot.get("continuity_notes", "")) +
                                 " (auto-split: first half)").strip()

    second["primary_action"] = tail
    second["first_frame_prompt"] = first["last_frame_prompt"]
    second["motion_prompt"] = f"{tail}; single slow movement, minimal background motion"
    second["camera_movement"] = "static"
    second["continuity_mode"] = "continuous"   # the halves are one continuous moment
    second["narration"] = ""                    # narration stays on the first half
    second["continuity_notes"] = (str(shot.get("continuity_notes", "")) +
                                  " (auto-split: second half)").strip()

    for part in (first, second):
        part["duration"] = clamp_duration(shot.get("duration", DEFAULT_SHOT_SECONDS))
        part["complexity_score"] = complexity_score(part)
    return first, second


def simplify_shot(shot: dict) -> dict:
    """
    Mechanically reduce one shot's complexity (the storyboard's "Simplify"
    button): drop to a static camera, keep only the first action, and trim the
    subject to one. No LLM call — instant, predictable, and free.
    """
    out = dict(shot)
    action = str(out.get("primary_action", ""))
    match = _MULTI_ACTION.search(action)
    if match:
        out["primary_action"] = action[:match.start()].strip(" ,.") or action
    out["camera_movement"] = "static"
    motion = str(out.get("motion_prompt", ""))
    m2 = _MULTI_ACTION.search(motion)
    if m2:
        motion = motion[:m2.start()].strip(" ,.")
    out["motion_prompt"] = (motion or out["primary_action"]) + \
        "; single slow movement, minimal background motion"
    if len(out.get("character_ids") or []) > 2:
        out["character_ids"] = out["character_ids"][:1]
    out["duration"] = clamp_duration(out.get("duration", DEFAULT_SHOT_SECONDS))
    out["complexity_score"] = complexity_score(out)
    return out


def merge_shots(first: dict, second: dict) -> dict:
    """
    Merge two compatible shots (storyboard "Merge"). Compatible means same
    beat and same location — merging across a location change would create
    exactly the shot the pipeline exists to avoid.
    """
    merged = dict(first)
    merged["primary_action"] = (f"{first.get('primary_action','')}, then "
                                f"{second.get('primary_action','')}").strip(", ")
    merged["last_frame_prompt"] = second.get("last_frame_prompt") or first.get("last_frame_prompt")
    merged["narration"] = " ".join(x for x in (first.get("narration"), second.get("narration")) if x)
    merged["sound_notes"] = " ".join(x for x in (first.get("sound_notes"),
                                                 second.get("sound_notes")) if x)
    merged["duration"] = clamp_duration(
        float(first.get("duration", DEFAULT_SHOT_SECONDS)) +
        float(second.get("duration", DEFAULT_SHOT_SECONDS))
    )
    merged["complexity_score"] = complexity_score(merged)
    return merged


def can_merge(first: dict, second: dict) -> tuple[bool, str]:
    if first.get("beat_id") != second.get("beat_id"):
        return False, "Shots are in different story beats."
    if set(first.get("location_ids") or []) != set(second.get("location_ids") or []):
        return False, "Shots are in different locations — merging would change location mid-clip."
    return True, ""


# --- Motion coherence (deterministic repair of what the planner returned) ----
#
# Three defects were measured in real finished projects (2026-08-13) and all
# three produce the same symptom: a clip that drifts and morphs instead of
# moving. Like `split_complex_shots`, these are fixed in CODE rather than by
# asking the planner more nicely, because the planner demonstrably does not
# comply — in one 17-shot project 8 shots told the model not to move, 4 shots
# repeated the previous shot's motion verbatim, and 6 "continuous" shots
# declared a camera setup that contradicted the frame they actually reuse.

# Clauses that instruct the video model to hold still. Harmless-looking, and
# ruinous: handed a real first frame with `image_strength=1.0` and told nothing
# moves, the model has no motion to render and fills the time by wandering the
# texture — which is exactly the "trippy" look. Background stillness is still
# requested, but by the code-owned tail below, never by negating the subject's
# own action.
_MOTION_NEGATION = re.compile(
    r"(?:^|(?<=[.;,]))\s*(?:and\s+|but\s+|with\s+|while\s+)?"
    r"(?:there\s+is\s+|there's\s+)?"
    r"(?:absolutely\s+|completely\s+|entirely\s+)?"
    r"no\s+(?:other\s+|further\s+|additional\s+|physical\s+|visible\s+|significant\s+)*"
    r"(?:movement|motion|action|change)\b[^.;]*",
    re.IGNORECASE,
)
_STILLNESS_ONLY = re.compile(
    r"^\W*(?:the\s+\w+\s+)?(?:is\s+|remains?\s+|stays?\s+|holds?\s+)?"
    r"(?:completely\s+|perfectly\s+|entirely\s+)?"
    r"(?:still|motionless|frozen|unmoving|static)\W*$",
    re.IGNORECASE,
)
_NOTHING_MOVES = re.compile(r"\bnothing\s+(?:else\s+)?(?:moves|is\s+moving|changes)\b[^.;]*",
                            re.IGNORECASE)

# A motion prompt this short cannot describe a movement precisely enough to be
# worth keeping over one rebuilt from the shot's own action.
_MIN_MOTION_WORDS = 6


def _strip_motion_negation(text: str) -> tuple[str, bool]:
    """
    Remove clauses that tell the model not to move, keeping everything else.
    Returns (cleaned, changed). Clause-level, not word-level: dropping the word
    "no" would invert the meaning rather than remove it.
    """
    original = str(text or "")
    if not original.strip():
        return "", False

    kept: list[str] = []
    # Split into clauses on sentence and semicolon boundaries, keeping order.
    for clause in re.split(r"(?<=[.;])\s+|;\s*", original):
        c = clause.strip()
        if not c:
            continue
        if _STILLNESS_ONLY.match(c):
            continue                      # "No movement." / "Motionless."
        c = _MOTION_NEGATION.sub("", c)
        c = _NOTHING_MOVES.sub("", c)
        c = re.sub(r"\s{2,}", " ", c).strip(" ,;")
        # Stripping the negation can leave nothing but punctuation behind
        # ("No other motion." -> "."), which must not become a clause.
        if not re.search(r"[A-Za-z0-9]", c):
            continue
        if _STILLNESS_ONLY.match(c):
            continue
        kept.append(c)

    # Rejoin as SENTENCES, not with semicolons: the clauses came from sentence
    # boundaries as often as not, and a blind "; " join produced "…in the
    # background.; The mist rises…".
    sentences = []
    for c in kept:
        c = c.rstrip(" ,;")
        sentences.append(c if c.endswith((".", "!", "?")) else c + ".")
    cleaned = " ".join(sentences)

    # Compare on content, not punctuation, so re-terminating a sentence is not
    # reported to the user as a change.
    def _content(s: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", s.lower())
    return cleaned, _content(cleaned) != _content(original)


def _motion_from_action(shot: dict) -> str:
    """
    Rebuild a motion description from the shot's OWN action. Used when the
    planner's motion_prompt was a duplicate of the previous shot's, or was
    nothing but a stillness instruction. Deterministic and free.
    """
    action = str(shot.get("primary_action") or "").strip(" .,;")
    subject = str(shot.get("subject") or "").strip(" .,;")
    if not action:
        return ""
    # "the raindrop gains weight" already names its subject; "gains weight"
    # does not, so give it one. Naming it twice ("raindrop raindrop gains
    # weight") is worse than either, so check the action's own words first.
    if subject and not re.match(r"^(the|a|an|his|her|their|its)\b", action, re.IGNORECASE):
        subject_words = {w for w in re.findall(r"[a-z]+", subject.lower()) if len(w) >= 3}
        action_words = set(re.findall(r"[a-z]+", action.lower()))
        if not (subject_words & action_words):
            return f"{_decap(subject)} {_decap(action)}"
    return _decap(action)


def _keep_ambient(rebuilt: str, original: str, shot: dict) -> str:
    """
    When a duplicated motion description is replaced, salvage its AMBIENT
    detail ("a faint mist rises from the ground") instead of throwing away
    good texture along with the wrong primary movement.

    A trailing sentence counts as ambient only if it does not mention the
    shot's own subject — one that does is describing the primary movement,
    which is precisely the part being replaced.
    """
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", str(original or "")) if s.strip()]
    if len(sentences) < 2:
        return rebuilt
    subject_words = {w for w in re.findall(r"[a-z]+", str(shot.get("subject") or "").lower())
                     if len(w) >= 4}
    ambient = [s for s in sentences[1:]
               if not (subject_words & set(re.findall(r"[a-z]+", s.lower())))]
    if not ambient:
        return rebuilt
    head = rebuilt.rstrip(" .,;")
    return f"{head}. " + " ".join(ambient)


def _norm_motion(text: str) -> str:
    """Comparison key for spotting a motion prompt copied from the shot before."""
    return re.sub(r"[^a-z0-9 ]+", "", str(text or "").lower()).strip()


def repair_motion(shots: list[dict]) -> tuple[list[dict], list[str]]:
    """
    Make each shot's motion description actually describe THIS shot's movement.

    Three mechanical repairs, each reported so the change is visible rather
    than mysterious (same contract as `split_complex_shots`):

      1. Strip clauses that tell the model not to move.
      2. Replace a motion prompt duplicated from the previous shot with one
         rebuilt from this shot's own action.
      3. Make a `continuous` shot's declared framing and camera angle match the
         shot it continues from — a continuous shot REUSES the previous clip's
         final frame, so a different declared framing is a claim the pixels
         cannot honour, and the model resolves the contradiction by warping.

    Returns (shots, notes).
    """
    out: list[dict] = []
    notes: list[str] = []
    previous: dict | None = None

    for index, original in enumerate(shots, start=1):
        shot = dict(original)

        # --- 1. de-negate -----------------------------------------------------
        cleaned, changed = _strip_motion_negation(shot.get("motion_prompt", ""))
        if changed:
            notes.append(
                f"Shot {index}: removed a 'no movement' instruction from the motion "
                f"description — it contradicted the shot's own action and makes the "
                f"clip drift instead of move."
            )
        shot["motion_prompt"] = cleaned

        # --- 2. de-duplicate --------------------------------------------------
        if previous is not None:
            same = _norm_motion(cleaned) and _norm_motion(cleaned) == _norm_motion(
                previous.get("motion_prompt", ""))
            if same:
                rebuilt = _motion_from_action(shot)
                if rebuilt and _norm_motion(rebuilt) != _norm_motion(cleaned):
                    rebuilt = _keep_ambient(rebuilt, cleaned, shot)
                    shot["motion_prompt"] = rebuilt
                    notes.append(
                        f"Shot {index}: had the same movement description as shot "
                        f"{index - 1}; rewrote it from this shot's own action so the "
                        f"video moves on instead of repeating."
                    )

        # --- 3. thin or empty -------------------------------------------------
        if len(str(shot.get("motion_prompt", "")).split()) < _MIN_MOTION_WORDS:
            rebuilt = _motion_from_action(shot)
            if rebuilt:
                shot["motion_prompt"] = rebuilt

        # --- 4. continuous shots cannot change the camera setup ---------------
        if previous is not None and (shot.get("continuity_mode") or "") == "continuous":
            fixes = []
            for field, label in (("framing", "framing"), ("camera_angle", "camera angle")):
                mine, theirs = shot.get(field), previous.get(field)
                if theirs and mine and str(mine).strip().lower() != str(theirs).strip().lower():
                    shot[field] = theirs
                    fixes.append(f"{label} ({mine} -> {theirs})")
            if fixes:
                notes.append(
                    f"Shot {index}: continues straight on from shot {index - 1}, so it "
                    f"reuses that shot's final frame — matched its " + " and ".join(fixes)
                    + ". Change it to a cut in the storyboard if you wanted a new angle."
                )

        out.append(shot)
        previous = shot
    return out, notes


_ACTION_STOPWORDS = frozenset((
    "the", "and", "but", "for", "with", "into", "onto", "from", "toward", "towards",
    "his", "her", "its", "their", "our", "this", "that", "these", "those",
    "begins", "begin", "starts", "start", "then", "while", "over", "under", "across",
    "slowly", "gently", "softly", "again", "still", "very", "just", "one", "single",
))


def _stem(word: str) -> str:
    """
    Crude suffix stripper — enough to make "lands", "landing" and "land" the
    same token. A real stemmer would be a dependency for no extra accuracy at
    this scale (a plan is tens of shots, not a corpus).
    """
    for suffix in ("ing", "ies", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            base = word[: -len(suffix)]
            return base + "y" if suffix == "ies" else base
    return word


def _action_keys(text: str) -> set[str]:
    """The meaningful stems of an action, for comparing two shots' intent."""
    return {_stem(w) for w in re.findall(r"[a-z]+", str(text or "").lower())
            if len(w) >= 3 and w not in _ACTION_STOPWORDS}


# Two actions count as the same story moment at this much overlap. Tuned
# against real plans: it catches "raindrop falls and lands on the plant" vs
# "landing on the plant", while leaving "the flame leans" and "the flame
# settles" alone as the genuinely different moments they are.
_REPEAT_OVERLAP = 0.5
_REPEAT_MIN_SHARED = 2


def _detail_weight(shot: dict) -> int:
    """How richly a shot is written — used to pick the survivor of a duplicate."""
    return sum(len(str(shot.get(field) or "")) for field in
               ("first_frame_prompt", "last_frame_prompt", "motion_prompt",
                "subject_detail", "setting_detail"))


def dedupe_shots(shots: list[dict], window: int = DEDUPE_WINDOW) -> tuple[list[dict], list[str]]:
    """
    Drop shots that re-film a moment an earlier nearby shot already covers.

    Only used by 'cinematic' pacing — the default mode still merely WARNS
    (`repeated_action_warnings`), because silently deleting a shot the owner
    may have meant is a story decision. In cinematic pacing that decision has
    been made explicitly: fewer, non-overlapping moments.

    The SURVIVOR is the more richly written of the pair, but it keeps the
    EARLIER position so the story order is preserved. A beat is never emptied.
    """
    keys = [_action_keys(s.get("primary_action", "")) for s in shots]
    dropped: set[int] = set()
    notes: list[str] = []

    for i in range(len(shots)):
        if i in dropped or len(keys[i]) < _REPEAT_MIN_SHARED:
            continue
        for j in range(i + 1, min(i + 1 + window, len(shots))):
            if j in dropped or len(keys[j]) < _REPEAT_MIN_SHARED:
                continue
            shared, union = keys[i] & keys[j], keys[i] | keys[j]
            if not (len(shared) >= _REPEAT_MIN_SHARED
                    and union and len(shared) / len(union) >= _REPEAT_OVERLAP):
                continue
            # The only floor is that the plan keeps at least one shot. A beat
            # left empty because its single shot merely restated an earlier one
            # was a redundant BEAT — removing it is the point of this pass, and
            # nothing is lost: the survivor below inherits the better text.
            if len(shots) - len(dropped) <= 1:
                continue
            if _detail_weight(shots[j]) > _detail_weight(shots[i]):
                # Keep the better-written text, but at the earlier position.
                shots[i] = dict(shots[j], beat_id=shots[i].get("beat_id"),
                                continuity_mode=shots[i].get("continuity_mode"))
                keys[i] = keys[j]
            dropped.add(j)
            notes.append(
                f"Removed a shot that filmed the same moment again "
                f"(\"{str(shots[j].get('primary_action', ''))[:50]}\") — the story "
                f"moves on instead of repeating it."
            )
    return [s for n, s in enumerate(shots) if n not in dropped], notes


def enforce_cut_policy(shots: list[dict]) -> tuple[list[dict], list[str]]:
    """
    Cut only at BEAT boundaries (cinematic pacing).

    A run of `continuous` shots is one unbroken take once chained — each clip
    starts on the previous clip's real final frame — so four continuous 4s
    shots read as a single 16-second take rather than four cuts. Measured
    before this existed: 26 cuts across 122 seconds, a cut every 4.7s, which
    is what makes a finished video feel restless no matter how clean the joins.

    A genuine change of place or time still cuts: pretending those continue
    would be a lie the pixels can't tell.
    """
    out: list[dict] = []
    notes: list[str] = []
    previous: dict | None = None

    for shot in shots:
        shot = dict(shot)
        if previous is not None:
            same_beat = shot.get("beat_id") == previous.get("beat_id")
            same_place = (set(shot.get("location_ids") or [])
                          == set(previous.get("location_ids") or []))
            same_time = (str(shot.get("time_of_day") or "")
                         == str(previous.get("time_of_day") or ""))
            if same_beat and same_place and same_time:
                if shot.get("continuity_mode") == "editorial_cut":
                    notes.append(
                        f"Shot {len(out) + 1} now plays straight on from the shot before "
                        f"it instead of cutting — same moment, same place, so the beat "
                        f"reads as one continuous take."
                    )
                shot["continuity_mode"] = "continuous"
            else:
                shot["continuity_mode"] = "editorial_cut"
        else:
            shot["continuity_mode"] = "editorial_cut"   # nothing to continue from
        out.append(shot)
        previous = shot
    return out, notes


def repeated_action_warnings(shots: list[dict]) -> list[str]:
    """
    Flag shots that plan the SAME story action more than once.

    Measured on a real finished project: a 17-shot plan had the raindrop land
    four separate times, which no amount of frame chaining can make coherent —
    the joins were seamless and the story still looped.

    Deliberately NOT repaired automatically: deciding that "the drop lands" was
    meant to happen once is a story judgement, not a mechanical one. Surfaced
    as a review warning so the owner can merge or re-plan.
    """
    keys = [_action_keys(s.get("primary_action", "")) for s in shots]
    grouped: set[int] = set()
    warnings: list[str] = []

    for i, key_i in enumerate(keys):
        if i in grouped or len(key_i) < _REPEAT_MIN_SHARED:
            continue
        group = [i]
        for j in range(i + 1, len(keys)):
            if j in grouped or len(keys[j]) < _REPEAT_MIN_SHARED:
                continue
            shared = key_i & keys[j]
            union = key_i | keys[j]
            if (len(shared) >= _REPEAT_MIN_SHARED
                    and union and len(shared) / len(union) >= _REPEAT_OVERLAP):
                group.append(j)
        if len(group) > 1:
            grouped.update(group)
            warnings.append(
                "Shots " + ", ".join(str(n + 1) for n in group) +
                " all plan the same action (\"" +
                str(shots[group[0]].get("primary_action", ""))[:60] +
                "\"). The finished video will show it happening several times — "
                "consider merging them or re-planning that beat."
            )
    return warnings


# --- Frame + motion prompt assembly (what actually reaches the models) -------

def _sentence(text) -> str:
    """Trim a fragment into a capitalised, terminated sentence so prompts read as prose."""
    s = str(text or "").strip().strip(",;")
    if not s:
        return ""
    s = s[0].upper() + s[1:]
    return s if s.endswith((".", "!", "?")) else s + "."


# Quality tail appended to every still prompt. Code-owned and constant so a
# regenerated frame can never drift to a different rendering intent than the
# frame it has to match.
_STILL_QUALITY_TAIL = (
    "Single still photographic frame, sharp focus on the subject, natural "
    "proportions, high detail, no text, no caption, no watermark, no border."
)


def build_frame_prompt(shot: dict, continuity: dict, direction: dict,
                       which: str = "first") -> str:
    """
    Compose the final still-image prompt as flowing descriptive PROSE.

    Two deliberate choices. First, prose rather than comma-separated tags:
    LTX-Video and Wan are both trained on natural-language captions and their
    own docs warn that short prompts visibly hurt quality — tag soup reads as a
    short prompt no matter how many tags it has. Second, the composition is
    assembled in CODE from the shot plus the LOCKED continuity descriptions for
    the ids it references, so a regenerated frame can never quietly lose the
    continuity block or the quality tail (which is how a re-rendered shot ends
    up not matching its neighbours).

    Detail here is unbounded on purpose; narrative and camera complexity are
    constrained elsewhere (complexity_score). See DETAIL_GUIDANCE.
    """
    direction = direction or {}
    base = shot.get(f"{which}_frame_prompt") or shot.get("subject", "")
    refs = continuity_reference_block(
        continuity or {},
        shot.get("character_ids") or [],
        shot.get("location_ids") or [],
        shot.get("prop_ids") or [],
    )

    framing = str(shot.get("framing") or "").strip()
    opening_bits = [framing, "shot"] if framing else []
    angle_phrase = _angle_phrase(shot.get("camera_angle"))
    if angle_phrase:
        opening_bits.append(angle_phrase)
    opening = _sentence(" ".join(opening_bits)) if opening_bits else ""

    # When the shot references a character/location that the continuity bible
    # already describes, the BIBLE WINS and the shot's own detail field is
    # dropped. Otherwise both land in the same prompt and contradict each other
    # — observed for real: one shot described "salt-and-pepper beard, faded
    # indigo wool robe" while the bible said "stubble, black hair, coarse
    # undyed wool tunic, brown trousers", leaving the image model to pick.
    # Contradiction is worse than either description alone, and the bible is
    # what holds a character steady across shots (hard rule 33).
    has_char_ref = _has_entry(continuity, "characters", shot.get("character_ids"))
    has_loc_ref = _has_entry(continuity, "locations", shot.get("location_ids"))

    parts = [
        opening,
        _sentence(base),
        "" if has_char_ref else _sentence(shot.get("subject_detail")),
        _sentence(shot.get("setting") if has_loc_ref
                  else (shot.get("setting_detail") or shot.get("setting"))),
        _sentence(shot.get("time_of_day")),
        _sentence(shot.get("texture_notes")),
        _sentence(shot.get("lighting")),
        _sentence(shot.get("atmosphere")),
        _sentence(shot.get("depth_notes")),
        # Continuity descriptions ride in as their own sentences so they read as
        # description rather than being lost in a tag list.
        _sentence(refs),
        # The continuity bible carries its own style line, so only add the
        # project's visual_style when the bible hasn't already said it —
        # repeating it verbatim wastes prompt budget and over-weights the phrase.
        "" if _style_already_stated(refs, direction.get("visual_style"))
           else _sentence(direction.get("visual_style")),
        _sentence(direction.get("color_palette")
                  and f"Colour palette: {direction.get('color_palette')}"),
        _sentence(shot.get("lens") or "Shot on a 50mm lens with a shallow depth of field"),
        _STILL_QUALITY_TAIL,
    ]
    return " ".join(p for p in parts if p)


def _angle_phrase(angle) -> str:
    """
    Turn whatever the model wrote for camera_angle into grammatical English.
    It returns anything from "low" to "eye level" to "birds-eye", and naive
    interpolation produced "from a low." / "from a eye level".
    """
    a = _decap(str(angle or "").strip().strip(".,;"))
    if not a:
        return ""
    if re.search(r"\blevel\b", a, re.IGNORECASE):
        return f"at {a}"                                  # "at eye level"
    if re.search(r"\b(overhead|top-?down)\b", a, re.IGNORECASE):
        return f"from {a}"                                # "from overhead"
    if re.search(r"\b(birds?|worms?)[- ]eye\b", a, re.IGNORECASE) and "view" not in a.lower():
        a = f"{a} view"
    elif not re.search(r"\b(angle|view|shot|perspective)\b", a, re.IGNORECASE):
        a = f"{a} angle"                                  # "low" -> "low angle"
    article = "an" if a[:1].lower() in "aeiou" else "a"
    return f"from {article} {a}"


def _has_entry(continuity: dict, group: str, ids) -> bool:
    """True when the bible actually describes at least one of the referenced ids."""
    if not continuity or not ids:
        return False
    known = {e.get("id") for e in (continuity.get(group) or []) if e.get("id")}
    return bool(known & set(ids))


def _style_already_stated(refs: str, style) -> bool:
    """True when the continuity block already contains the project's style phrase."""
    style = str(style or "").strip().lower()
    if not style:
        return True
    head = style.split(",")[0].strip()
    return bool(head) and head in (refs or "").lower()


def observe_frame(image_path: str) -> dict:
    """
    Look at a frame that was ACTUALLY generated and describe what is really in
    it — subject appearance, wardrobe, position in frame, lighting, setting.

    This closes the loop that makes chained generation work. The shot plan
    describes what was *intended*; the model renders something adjacent to that.
    If the next shot is prompted from the plan alone it drifts back toward the
    intention and the join is visible. Prompting it from what the previous clip
    really produced is what makes consecutive shots look like one scene.

    Uses the repo's existing vision path (paid, metered at its own chokepoint).
    Returns {} on any failure — a missing observation degrades continuity
    slightly and must never break a generation run.
    """
    from agents.router import call_grok_vision
    try:
        reply = call_grok_vision(
            image_path,
            "Describe ONLY what is literally visible in this frame, for use as a "
            "continuity reference when generating the next shot of the same scene. "
            "Reply as JSON:\n"
            '{"subject":"who/what is present, their build, face, hair",'
            '"wardrobe":"exact garments, colours, materials, condition",'
            '"position":"where in frame, facing which way, posture",'
            '"setting":"location, notable objects, background",'
            '"lighting":"direction, colour, hardness of the light",'
            '"palette":"dominant colours"}\n'
            "Be concrete and specific. No interpretation, no mood, no story.",
            max_tokens=500, json_mode=True,
        )
        data = _extract_json(reply)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def observation_block(observed: dict) -> str:
    """Render an observation into a prose continuity clause for the next prompt."""
    if not observed:
        return ""
    parts = [observed.get("subject"), observed.get("wardrobe"), observed.get("setting"),
             observed.get("lighting"), observed.get("palette")]
    text = _clause(*[p for p in parts if p])
    return _sentence(f"Continuing the same scene: {text}") if text else ""


def build_continuation_prompt(shot: dict, continuity: dict, direction: dict,
                              observed: dict) -> str:
    """
    First-frame prompt for a shot that follows an EDITORIAL CUT during chained
    generation. The camera setup changes, so the frame must be regenerated
    rather than reused — but it is anchored to what the previous clip actually
    showed, so the character, wardrobe, place and light carry across the cut
    instead of being re-invented.
    """
    base = build_frame_prompt(shot, continuity, direction, "first")
    obs = observation_block(observed)
    if not obs:
        return base
    # The observation goes BEFORE the quality tail so it reads as description,
    # and after the shot's own framing so the new angle still leads.
    tail = _STILL_QUALITY_TAIL
    body = base[: -len(tail)].rstrip() if base.endswith(tail) else base
    return f"{body} {obs} {tail}"


# Code-owned tail on every motion prompt, constant for the same reason
# `_STILL_QUALITY_TAIL` is: two clips joined end to end must be asking for the
# same rendering intent, or the join shows. The anti-morph wording is aimed at
# a specific observed failure — handed a real first frame, these models will
# hold the composition and slowly melt the texture rather than move anything.
_MOTION_QUALITY_TAIL = (
    "One single continuous movement, smooth and natural, at a calm even pace from "
    "the first frame to the last. Shapes, faces, clothing and objects keep their "
    "form throughout and never melt, morph, stretch or swap into something else. "
    "The lighting, colour grade and composition stay constant. No cut, no scene "
    "change, no jump, no camera shake, no speed change, no text or caption."
)


def _motion_pace_sentence(seconds: float) -> str:
    """
    Tie the movement to the clip's real length. Without this the model is free
    to complete the action in half a second and then invent something to fill
    the rest, which is a large part of what reads as restlessness.
    """
    return (
        f"The movement begins as the shot begins and runs continuously for the whole "
        f"{seconds:g} seconds — it is a small, slow, unhurried movement that is still "
        f"finishing as the shot ends. It happens once and never repeats or loops."
    )


def _holds_still_sentence(shot: dict) -> str:
    """
    Name what must NOT move. This is the positive counterpart to the stillness
    instructions `repair_motion` strips out: stillness is scoped to the
    background here, so it can never contradict the subject's own action.
    """
    where = _decap(str(shot.get("setting") or "").strip(" .,;"))
    if where:
        return (f"Everything else holds still: {where} stays exactly as it is, fixed and "
                f"unchanging behind the movement.")
    return "Everything else in the frame holds still and stays exactly as it is."


def build_motion_prompt(shot: dict, direction: dict, *, continues: bool = False,
                        observed: dict | None = None) -> str:
    """
    The video model's prompt: precisely what MOVES, and precisely what does not.

    Structured deliberately, because a vague movement description is the single
    biggest cause of the drifting, morphing output these small models produce:

        [continuation clause, chained runs only]
        [what actually moves — one movement, named]
        [how long it takes — tied to the real clip duration]
        [what holds still]
        [what the camera does]
        [look and atmosphere]
        [code-owned quality tail]

    `continues=True` reframes the whole prompt as the middle of a shot already
    in progress rather than a new shot that happens to start on this image —
    the difference between "carry this on" and "make something like this",
    which is what a chained run actually needs.

    Narration and sound are deliberately excluded — they are separate fields
    and putting them here makes the model try to render speech and captions.
    """
    direction = direction or {}
    shot = shot or {}
    seconds = clamp_duration(shot.get("duration", DEFAULT_SHOT_SECONDS))

    movement = str(shot.get("camera_movement") or "static").strip()
    if movement.lower() in ("static", "none", "locked", ""):
        camera = ("The camera is locked off and completely still for the entire shot — "
                  "no pan, no tilt, no zoom, no drift.")
    else:
        camera = (f"The camera makes exactly one {_decap(movement)}: slow, smooth and "
                  f"even, continuing in the same direction for the whole shot, never "
                  f"speeding up, stopping or reversing.")

    # The movement itself. `repair_motion` has already guaranteed this describes
    # THIS shot and does not negate itself, so it can be trusted here.
    movement_text = str(shot.get("motion_prompt") or "").strip()
    if len(movement_text.split()) < _MIN_MOTION_WORDS:
        movement_text = _motion_from_action(shot) or movement_text

    parts: list[str] = []
    if continues:
        parts.append(
            "This is the continuation of a shot already in progress. The supplied image "
            "is the exact first frame: the same subject, the same place, the same camera "
            "position and the same light carry straight on from it with no cut and no "
            "jump, as if the recording never stopped."
        )
    # Independent of `continues`: after a cut the frame was regenerated to match
    # the previous clip, and restating what it holds keeps identity stable
    # across the 3-4 seconds rather than only at the first frame.
    obs = observation_block(observed)
    if obs:
        parts.append(obs)

    parts += [
        _sentence(movement_text),
        _motion_pace_sentence(seconds),
        _holds_still_sentence(shot),
        camera,
        _sentence(shot.get("atmosphere")),
        _sentence(shot.get("lighting")),
        _sentence(direction.get("visual_style")),
        _MOTION_QUALITY_TAIL,
    ]
    return " ".join(p for p in parts if p)
