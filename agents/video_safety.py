"""
Sacred-figure non-depiction safeguard for the video pipeline.

Same class as `scribe._sanitize_claims` and the code-appended disclaimers
(hard rules 4 and 8): honesty/reverence-critical behaviour is enforced
DETERMINISTICALLY in code, never by trusting an LLM to have complied. The
Director's prompts also ask for reverent treatment, but this module is what
actually guarantees it — every shot passes through `enforce_shot()` before
any frame or clip is generated, whatever the model wrote.

The rule (Bahá'í practice): the Manifestations of God are not portrayed
visually or vocally. They MAY be referenced with reverence — named in
narration, present in the story, spoken ABOUT, their words quoted, their
effect on others shown. So the safeguard is deliberately asymmetric:

  VISUAL fields  (subject, framing, first/last-frame + motion prompts)
      → a Manifestation may never be the depicted subject. Detected
        references are rewritten into an indirect treatment.
  NARRATION field
      → left alone. Reverent reference in narration is the intended
        outcome, not a violation.

This is a religious-treatment rule specific to the sacred figures listed
below; it is NOT a general content filter and must not stop the pipeline
from handling ordinary historical or secular subjects.
"""

import re
import unicodedata

# Every apostrophe-like character that shows up in Bahá'í transliteration:
# ASCII ', the typographic ’ (U+2019) this repo actually uses everywhere, the
# ʻayn/hamza letters (U+02BB/U+02BC), and assorted look-alikes. Matching text
# is normalised (diacritics stripped, these removed) BEFORE the patterns run,
# so `Bahá'u'lláh`, `Bahá’u’lláh`, `Baha'u'llah` and `Bahaullah` all reduce to
# the same `bahaullah` and hit the same pattern.
#
# This normalisation is load-bearing, not tidiness: the first version matched
# on a raw ASCII apostrophe and therefore MISSED `Bahá’u’lláh` — the exact
# spelling used throughout this repo and in any real Bahá'í source text —
# while its leak-check, sharing the same matcher, reported all-clear.
_APOSTROPHES = "'‘’ʻʼ՚＇`´′"


def _normalize(text: str) -> str:
    """Lowercase, strip diacritics, drop apostrophe-likes: `Bahá’u’lláh` → `bahaullah`."""
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFD", str(text))
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    for ch in _APOSTROPHES:
        stripped = stripped.replace(ch, "")
    return stripped.lower()


# Manifestations of God. Bahá'í usage recognises the Founders of the great
# religions as Manifestations, so the safeguard covers all of Them, not only
# the Twin Manifestations of the Bahá'í Faith. Patterns are written against
# NORMALISED text (see _normalize) — no diacritics, no apostrophes.
_FIGURE_PATTERNS: list[tuple[str, str]] = [
    # (regex over normalised text, canonical display name)
    (r"\bbahaullah\b|\bbahaullahs\b", "Bahá'u'lláh"),
    # `the Báb` only with its article, so `Abdul-Baha` and `Babi` never match.
    (r"\bthe\s+bab\b|\bsiyyid\s+ali\s+muhammad\b", "the Báb"),
    (r"\bmuhammad\b(?!\s*-?\s*(?:ali|hasan|husayn|rida|taqi|baqir|shah))", "Muḥammad"),
    (r"\bjesus\b|\bchrist\b(?!ian)|\bthe\s+messiah\b", "Christ"),
    (r"\bmoses\b|\bmusa\b", "Moses"),
    (r"\bbuddha\b|\bgautama\b|\bsiddhartha\b", "the Buddha"),
    (r"\bkrishna\b", "Krishna"),
    (r"\bzoroaster\b|\bzarathustra\b", "Zoroaster"),
    (r"\babraham\b(?!\s+lincoln)", "Abraham"),
    (r"\bnoah\b", "Noah"),
    (r"\bmanifestation\s+of\s+god\b", "the Manifestation of God"),
]

# Names that CONTAIN a figure name but are different people — checked first
# and their spans removed before figure matching, so `'Abdu'l-Bahá` (who may
# be depicted) can never be read as `Bahá'u'lláh`.
_EXEMPT_SPANS = re.compile(r"\babdulbaha\b|\babdul-baha\b|\bshoghi\s+effendi\b|\bbahai\b|\bbabi\b",
                           re.IGNORECASE)

# `'Abdu'l-Bahá` and Shoghi Effendi are NOT Manifestations and MAY be
# depicted (photographs of them exist and are published by the Faith). They
# are listed here only so the Director is told so explicitly — otherwise a
# cautious model tends to over-refuse and blur out anyone significant.
DEPICTABLE_FIGURES = ("'Abdu'l-Bahá", "Shoghi Effendi")

_COMPILED = [(re.compile(pat), name) for pat, name in _FIGURE_PATTERNS]  # normalised = lowercase

# Visual verbs that turn a mention into a DEPICTION. "Bahá'u'lláh's words
# appear on screen" is fine; "Bahá'u'lláh walks toward the camera" is not.
# Used only for explanatory flag text — enforcement does not depend on
# catching the verb, since the figure must not be the visual subject at all.
_DEPICTION_HINTS = re.compile(
    r"\b(stands?|sits?|walks?|looks?|gazes?|turns?|smiles?|speaks?|raises?|holds?|"
    r"appears?|enters?|faces?|portrait|close-?up|face|figure|silhouette|reveal)\b",
    re.IGNORECASE,
)

# The indirect treatments the safeguard substitutes in. Each keeps the shot
# usable — an actual filmable image — rather than deleting the beat.
INDIRECT_TREATMENTS = [
    ("reaction", "the faces of those listening, lit with awe and recognition"),
    ("environment", "the room and its lamplight where the words were spoken, no figure present"),
    ("object", "the writing desk, reed pen and paper resting where the Tablet was revealed"),
    ("threshold", "an open doorway with light beyond it, the space left reverently empty"),
    ("path", "the road and footsteps leading away, the traveller beyond the frame"),
    ("gathering", "the assembled believers turned toward an unseen point off-camera"),
]

FLAG_SACRED_DEPICTION = "sacred_figure_depiction"


def _find_figures(text: str) -> list[str]:
    """
    Canonical names of every Manifestation referenced in `text`. Matching runs
    over the NORMALISED form, so spelling and apostrophe style don't matter.
    """
    if not text:
        return []
    haystack = _EXEMPT_SPANS.sub(" ", _normalize(text))
    found: list[str] = []
    for rx, name in _COMPILED:
        if rx.search(haystack) and name not in found:
            found.append(name)
    return found


def scan_text(text: str) -> dict:
    """
    Report (without modifying) any sacred-figure reference in a block of prose
    — used during story analysis so the problem surfaces to the user BEFORE
    shots are built. `depiction_risk` is True when the mention also carries
    visual/action language, i.e. the source would push toward portraying them.
    """
    figures = _find_figures(text)
    return {
        "figures": figures,
        "has_reference": bool(figures),
        "depiction_risk": bool(figures) and bool(_DEPICTION_HINTS.search(text or "")),
    }


def _strip_figure_clauses(text: str, figures: list[str]) -> str:
    """
    Remove the clause(s) naming a Manifestation from a VISUAL prompt. Splits
    on sentence/clause boundaries and drops any span that names one, so the
    rest of the composition (setting, lighting, camera) survives intact.
    """
    if not text:
        return text
    parts = re.split(r"(?<=[.;])\s+|,\s+(?=(?:and\s+)?[A-Z])", text)
    kept = [p for p in parts if not _find_figures(p)]
    out = " ".join(s.strip() for s in kept if s and s.strip())
    return out.strip()


def _indirect_for(shot_index: int) -> str:
    """Pick a varied indirect treatment so consecutive rewrites don't repeat."""
    return INDIRECT_TREATMENTS[shot_index % len(INDIRECT_TREATMENTS)][1]


# Appended in code to every visual prompt the pipeline sends to an image or
# video model when the shot touches sacred history — belt and braces next to
# the rewrite above. Code-owned string, never LLM-written (rule 8's class).
NEGATIVE_GUARD = (
    "depiction of a Manifestation of God, face of a prophet, portrait of a holy founder, "
    "religious figure as subject"
)


def guard_negative_prompt(negative: str) -> str:
    """Append the non-depiction guard to a shot's negative prompt, once."""
    negative = (negative or "").strip()
    if NEGATIVE_GUARD in negative:
        return negative
    return f"{negative}, {NEGATIVE_GUARD}".strip(", ").strip()


# Fields carrying VISUAL instruction — these get enforced. `narration` is
# deliberately absent: reverent verbal reference is allowed and wanted.
VISUAL_FIELDS = ("subject", "primary_action", "first_frame_prompt",
                 "last_frame_prompt", "motion_prompt")


def enforce_shot(shot: dict, shot_index: int = 0) -> tuple[dict, list[str]]:
    """
    Make one shot safe to generate. Returns (shot, notes) where `shot` is a
    NEW dict (never mutated in place) and `notes` describes every change made,
    for display in the UI — a silent rewrite would be worse than the problem.

    Narration is not touched. If the visual fields named a Manifestation, they
    are rewritten to an indirect treatment and the shot is marked
    `sacred_treatment` so the storyboard can badge it.
    """
    out = dict(shot)
    notes: list[str] = []

    offending: list[str] = []
    for field in VISUAL_FIELDS:
        offending += [f for f in _find_figures(str(out.get(field) or "")) if f not in offending]

    if offending:
        indirect = _indirect_for(shot_index)
        for field in VISUAL_FIELDS:
            value = str(out.get(field) or "")
            if not _find_figures(value):
                continue
            stripped = _strip_figure_clauses(value, offending)
            if field in ("subject",):
                out[field] = indirect
            elif field in ("first_frame_prompt", "last_frame_prompt"):
                out[field] = (f"{stripped} {indirect}".strip()
                              if stripped else indirect)
            elif field == "primary_action":
                out[field] = stripped or "the listeners react quietly"
            else:  # motion_prompt
                out[field] = stripped or "a slow, steady camera push; minimal motion"
        out["sacred_treatment"] = {
            "figures": offending,
            "treatment": indirect,
            "rule": ("Manifestations of God are never portrayed. This shot references "
                     f"{', '.join(offending)} indirectly instead."),
        }
        notes.append(
            f"Shot rewritten to avoid portraying {', '.join(offending)} — "
            f"now shows {indirect}. Narration may still name them with reverence."
        )

    # The negative guard rides along on any shot in a project that touches
    # sacred history, not only rewritten ones: an unrelated shot in the same
    # story can still drift toward a prophet-like figure.
    out["negative_prompt"] = guard_negative_prompt(out.get("negative_prompt", ""))
    return out, notes


def enforce_shots(shots: list[dict]) -> tuple[list[dict], list[str]]:
    """Run `enforce_shot` across a whole plan, collecting all notes."""
    safe: list[dict] = []
    notes: list[str] = []
    for i, shot in enumerate(shots):
        s, n = enforce_shot(shot, i)
        safe.append(s)
        notes += n
    return safe, notes


def director_guidance() -> str:
    """
    The instruction block appended to every Director prompt. Kept here (not
    inline in video_director) so the wording lives beside the enforcement it
    describes and the two can't drift apart.
    """
    return (
        "REVERENCE RULE (mandatory): never write a shot that visually portrays a "
        "Manifestation of God — Bahá'u'lláh, the Báb, Muḥammad, Christ, Moses, the "
        "Buddha, Krishna, Zoroaster, or Abraham. Do not describe Their face, figure, "
        "silhouette, hands, or likeness, and do not make Them the subject of a shot. "
        "You MAY name Them with reverence in the `narration` field, quote Their words, "
        "and show the story around Them: the faces and reactions of others, the room, "
        "the light, objects, the road, an empty threshold, a gathering turned toward "
        "someone outside the frame. "
        f"({', '.join(DEPICTABLE_FIGURES)} are NOT Manifestations and may be depicted "
        "normally.) This applies only to those sacred figures — ordinary historical, "
        "secular, and fictional subjects are shot normally."
    )
