"""
Layout parameters for the visual editor — the single source of truth for every
adjustable knob Sheraj can turn on a bookmark or quote-card face, plus the
sanitiser that turns whatever the dashboard sends into safe, clamped values
before it ever reaches a compositor.

Design rule (why this module is small and strict): the visual editor may only
ever change *how* a face looks — font, size, position, colour, gradient — never
*what it says*. The printed quote, the citation, the translation, and the
AI-artwork / translation disclaimers are all supplied to the compositors from
the database at render time and are NOT layout parameters, so no value that
passes through here can rewrite honesty-critical text (CLAUDE.md rules 2, 8, 9,
11, 12). `sanitize()` is the boundary: it keeps only known keys, clamps every
number to a safe range, and drops anything it doesn't recognise.

This module deliberately imports nothing from the compositors (they import it),
so it owns the base serif stack the rest of the render path shares.
"""

# The Windows serif fallback stack, tried in order. Owned here so both
# compositors and the font registry below agree on one list. Every path was
# verified present on this machine; the loaders fall back down the list (and
# finally to PIL's default) if any single file is missing, so a missing font
# degrades gracefully rather than rendering tofu.
SERIF_STACK = [
    "C:/Windows/Fonts/palai.ttf",     # Palatino Linotype Italic — elegant default
    "C:/Windows/Fonts/pala.ttf",      # Palatino Linotype Regular
    "C:/Windows/Fonts/georgiai.ttf",  # Georgia Italic
    "C:/Windows/Fonts/georgia.ttf",   # Georgia Regular
    "C:/Windows/Fonts/times.ttf",     # Times New Roman
]

# Selectable fonts for the English/Latin text on a face. Each maps to a
# preferred file followed by the shared fallback stack, so an unavailable
# choice quietly falls back instead of failing. "palatino" resolves to the
# stack unchanged, so it reproduces the pre-editor default exactly.
FONTS: dict[str, dict] = {
    "palatino":         {"label": "Palatino (italic)",  "paths": list(SERIF_STACK)},
    "palatino_regular": {"label": "Palatino (regular)",  "paths": ["C:/Windows/Fonts/pala.ttf", *SERIF_STACK]},
    "georgia":          {"label": "Georgia (italic)",    "paths": ["C:/Windows/Fonts/georgiai.ttf", *SERIF_STACK]},
    "georgia_regular":  {"label": "Georgia (regular)",   "paths": ["C:/Windows/Fonts/georgia.ttf", *SERIF_STACK]},
    "times":            {"label": "Times New Roman",     "paths": ["C:/Windows/Fonts/times.ttf", *SERIF_STACK]},
    "constantia":       {"label": "Constantia",          "paths": ["C:/Windows/Fonts/constan.ttf", *SERIF_STACK]},
    "cambria":          {"label": "Cambria",             "paths": ["C:/Windows/Fonts/cambria.ttc", *SERIF_STACK]},
}
_DEFAULT_FONT = "palatino"

# Text colour presets → opaque RGBA. White is the pre-editor default; cream and
# gold are the two other legible-on-dark options.
COLORS: dict[str, tuple] = {
    "white": (255, 255, 255, 255),
    "cream": (245, 240, 222, 255),
    "gold":  (232, 205, 120, 255),
}
_DEFAULT_COLOR = "white"

# Defaults chosen so that a face rendered with these values is byte-identical
# to the pre-editor output — the editor is purely additive.
BOOKMARK_DEFAULTS = {
    "font": _DEFAULT_FONT,
    "text_scale": 1.0,     # multiplier on the auto-fit starting size
    "text_offset": 0.0,    # vertical nudge of the quote block, fraction of face height
    "text_color": _DEFAULT_COLOR,
    "gradient": 1.0,       # multiplier on the darkening scrim behind the text
    "show_star": True,     # the gold nine-pointed star
    "show_rule": True,     # the thin gold rule above the star
}

CARD_DEFAULTS = {
    "font": _DEFAULT_FONT,   # English quote + citation only; translation font stays script-verified
    "text_scale": 1.0,
    "text_color": _DEFAULT_COLOR,
    "vignette": 1.0,         # multiplier on the radial scrim strength
}


def _clamp(value, lo: float, hi: float, default: float) -> float:
    try:
        return max(lo, min(hi, float(value)))
    except (TypeError, ValueError):
        return default


def _as_bool(value, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return default


def sanitize(product_type: str, raw: dict | None) -> dict:
    """
    Coerce an untrusted layout dict from the dashboard into a safe, complete
    one. Unknown keys are dropped; every value is clamped to its allowed range
    or reset to its default. Always returns a full dict (defaults filled in),
    so a compositor can rely on every key being present. No text ever flows
    through here — only presentation knobs.
    """
    raw = raw or {}
    is_card = (product_type or "bookmark") == "quote_card"
    d = dict(CARD_DEFAULTS if is_card else BOOKMARK_DEFAULTS)

    font = str(raw.get("font") or "").strip()
    d["font"] = font if font in FONTS else _DEFAULT_FONT

    color = str(raw.get("text_color") or "").strip()
    d["text_color"] = color if color in COLORS else _DEFAULT_COLOR

    if is_card:
        d["text_scale"] = round(_clamp(raw.get("text_scale"), 0.6, 1.3, 1.0), 3)
        d["vignette"] = round(_clamp(raw.get("vignette"), 0.3, 1.6, 1.0), 3)
    else:
        d["text_scale"] = round(_clamp(raw.get("text_scale"), 0.6, 1.4, 1.0), 3)
        d["text_offset"] = round(_clamp(raw.get("text_offset"), -0.22, 0.22, 0.0), 3)
        d["gradient"] = round(_clamp(raw.get("gradient"), 0.2, 1.6, 1.0), 3)
        d["show_star"] = _as_bool(raw.get("show_star"), True)
        d["show_rule"] = _as_bool(raw.get("show_rule"), True)
    return d


def font_paths(key: str) -> list[str]:
    """The ordered font-file list for a font key (falls back to the default)."""
    return list(FONTS.get(key, FONTS[_DEFAULT_FONT])["paths"])


def color_rgba(key: str) -> tuple:
    """The opaque RGBA tuple for a colour key (falls back to white)."""
    return COLORS.get(key, COLORS[_DEFAULT_COLOR])


def options(product_type: str) -> dict:
    """
    Everything the dashboard needs to render the editor controls for a product
    type: the current defaults, the font/colour choices, and the numeric
    slider ranges. Kept here so the UI and the sanitiser can never disagree
    about what's adjustable.
    """
    is_card = (product_type or "bookmark") == "quote_card"
    fonts = [{"key": k, "label": v["label"]} for k, v in FONTS.items()]
    colors = [{"key": k, "label": k.capitalize()} for k in COLORS]
    if is_card:
        return {
            "product_type": "quote_card",
            "defaults": dict(CARD_DEFAULTS),
            "fonts": fonts,
            "colors": colors,
            "ranges": {
                "text_scale": {"min": 0.6, "max": 1.3, "step": 0.05},
                "vignette":   {"min": 0.3, "max": 1.6, "step": 0.05},
            },
        }
    return {
        "product_type": "bookmark",
        "defaults": dict(BOOKMARK_DEFAULTS),
        "fonts": fonts,
        "colors": colors,
        "ranges": {
            "text_scale":  {"min": 0.6, "max": 1.4, "step": 0.05},
            "text_offset": {"min": -0.22, "max": 0.22, "step": 0.02},
            "gradient":    {"min": 0.2, "max": 1.6, "step": 0.05},
        },
    }
