"""
Card Compositor — renders the Quote Card product: a 3.5"×2" landscape,
devotional/reflection card (redesigned 2026-07-16, owner spec).

  FRONT — always the quote (never anywhere else): Tahoma text in dark ink on
          a calm, light background (the artwork blurred into a soft colour
          wash under a pale scrim) inside a thin gold border, with the
          citation beneath a small rule. Readability outranks decoration:
          the fit loop refuses to shrink below MIN_QUOTE_PX — a card whose
          quote would be tiny fails loudly instead of shipping.
  BACK  — never the quote: a reflection face on plain cream — a short
          reflection question, a gently-phrased call to action, ruled blank
          lines to write on, and a small code-owned share line.

Translated runs produce SEPARATE language cards (owner rule: no
English-front/translation-back splits): the variant card carries the
translated quote on ITS front (with the code-appended AI-translation
disclaimer — rule 8) and the reflection face in that language on its back.

Sibling of compositor.py (bookmarks), deliberately separate so the bookmark
render path stays untouched. Faces are composed at _SS× true 300dpi pixel
dimensions and LANCZOS-downscaled at save so type is crisp.

Script support: Tahoma is used wherever it safely covers the script (Latin,
incl. Spanish); languages with hand-verified font_paths in
translator.LANGUAGES (Chinese, Arabic) KEEP those fonts — never swapped
(rule 9). Arabic is shaped with arabic_reshaper + python-bidi per line.
"""

import uuid

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from agents import layout as layout_opts
from agents.compositor import (
    OUTPUTS_DIR, PRINT_DPI, _FONT_PATHS, _RESAMPLE, _normalise_ratio,
)
from agents.translator import LANGUAGES

CARD_SIZE_IN = (3.5, 2)  # landscape business-card size
CARD_TARGET_PX = (int(PRINT_DPI * CARD_SIZE_IN[0]), int(PRINT_DPI * CARD_SIZE_IN[1]))  # (1050, 600)

# Supersampling: compose at _SS× and downscale at save — crisp glyph edges,
# identical printed sizes (see 2026-07-11 entry in STATUS.md).
_SS = 2

# The readable floor for the quote, in 1× pixels at 300dpi (≈6.3pt). The fit
# loop never goes below it — a quote that can't fit at this size raises
# instead of rendering something tiny/crowded (owner spec, 2026-07-16).
MIN_QUOTE_PX = 26

# The attribution beneath the quote (speaker name + source) uses a FIXED size
# so it reads identically on every card regardless of the quote's length (owner
# ask, 2026-07-31) — the old size scaled with the quote (q_size * 0.40) and came
# out both too small and inconsistent card-to-card. ~2× the old typical, in 1×
# px at 300dpi (≈7.7pt). Capped at the quote size at render time so a crowded
# card never shows the attribution larger than the quote itself.
CITATION_PX = 32

# Tahoma is the card face (owner spec) — regular for body, bold for the
# reflection question. The serif stack stays as fallback only.
TAHOMA = "C:/Windows/Fonts/tahoma.ttf"
TAHOMA_BOLD = "C:/Windows/Fonts/tahomabd.ttf"

# Calm, dignified palette: dark warm ink on pale washes.
INK = (44, 44, 52, 255)             # quote / question text
INK_SOFT = (104, 96, 82, 255)       # citation, action label
RULE_GOLD = (172, 144, 84, 255)     # border + small rules (muted, not shiny)
CREAM = (250, 248, 241, 255)        # reflection-face background
WRITE_LINE = (188, 180, 164, 255)   # ruled writing lines
TINY_TEXT = (128, 122, 110, 255)    # share line / disclaimer

# Code-owned reflection defaults (used when the pipeline supplies none) and
# the share line, which is ALWAYS code-owned — tone-sensitive fixed strings,
# never LLM-written, same discipline as the disclaimers (rule 8). The
# Spanish set was human-written and render-verified; languages without a
# verified set fall back to English rather than machine-guessed text.
REFLECTION_DEFAULTS = {
    "en": {
        "question": "What is one way I can practice this today?",
        "action": "One small action I will try:",
        "share": "If this card speaks to you, pass it on to someone.",
    },
    "es": {
        "question": "¿De qué manera puedo poner esto en práctica hoy?",
        "action": "Una pequeña acción que intentaré:",
        "share": "Si esta tarjeta te habla al corazón, compártela con alguien.",
    },
}


def _reflection_for(lang_code: str | None, supplied: dict | None) -> dict:
    """Merge supplied reflection text over the code defaults for a language.
    `supplied` uses the English keys; its optional `native` sub-dict applies
    to the translated card. The share line stays code-owned unless the
    pipeline explicitly passes one (it shouldn't — rule 8 discipline)."""
    base = dict(REFLECTION_DEFAULTS.get(lang_code or "en") or REFLECTION_DEFAULTS["en"])
    if supplied:
        src = supplied.get("native") if (lang_code and lang_code != "en") else supplied
        for k in ("question", "action"):
            v = ((src or {}).get(k) or "").strip()
            if v:
                base[k] = v
    return base


def _font_stack(lang_code: str | None) -> list[str]:
    """Font paths for a language. Languages with hand-verified font_paths
    (zh, ar) keep them (rule 9); Latin / unlisted scripts get Tahoma first
    (owner spec), then the old serif stack purely as fallback."""
    if lang_code:
        paths = (LANGUAGES.get(lang_code) or {}).get("font_paths")
        if paths:
            return list(paths) + [TAHOMA] + _FONT_PATHS
    return [TAHOMA] + _FONT_PATHS


def _load_font(lang_code: str | None, size: int,
               override_paths: list[str] | None = None) -> ImageFont.FreeTypeFont:
    # override_paths (the layout editor's Latin font choice) is only ever
    # passed for Latin text. Verified non-Latin fonts always win (rule 9).
    for path in ((override_paths or []) + _font_stack(lang_code)):
        try:
            return ImageFont.truetype(path, size)
        except (IOError, OSError):
            continue
    return ImageFont.load_default(size=size)


def _shape(text: str, lang_code: str | None) -> str:
    """Reshape + bidi-reorder RTL text for PIL. No-op for LTR scripts."""
    if lang_code and (LANGUAGES.get(lang_code) or {}).get("rtl"):
        import arabic_reshaper
        from bidi.algorithm import get_display
        return get_display(arabic_reshaper.reshape(text))
    return text


def _tokens(text: str, lang_code: str | None) -> tuple[list[str], str]:
    """CJK has no spaces — wrap per character; everything else per word."""
    if lang_code == "zh":
        return [ch for ch in text.replace("\n", " ").strip() if not ch.isspace()], ""
    return text.split(), " "


def _wrap(draw: ImageDraw.ImageDraw, text: str, lang_code: str | None,
          font: ImageFont.FreeTypeFont, max_w: int) -> list[str]:
    """Greedy wrap measured on the SHAPED text (what will actually be drawn)."""
    tokens, joiner = _tokens(text, lang_code)
    lines, current = [], ""
    for tok in tokens:
        test = (current + joiner + tok) if current else tok
        if draw.textlength(_shape(test, lang_code), font=font) <= max_w:
            current = test
        else:
            if current:
                lines.append(current)
            current = tok
    if current:
        lines.append(current)
    return lines


def _draw_centered(draw: ImageDraw.ImageDraw, lines: list[str], lang_code: str | None,
                   font: ImageFont.FreeTypeFont, y: int, line_h: int, face_w: int,
                   fill=INK) -> int:
    """Draw shaped lines centered horizontally starting at y. Returns next y.
    Dark ink on light backgrounds needs no drop shadow — none is drawn."""
    for line in lines:
        shaped = _shape(line, lang_code)
        lw = draw.textlength(shaped, font=font)
        draw.text((int((face_w - lw) // 2), y), shaped, font=font, fill=fill)
        y += line_h
    return y


def _soft_background(band: Image.Image, wash: float) -> Image.Image:
    """The front's calm background: the artwork blurred into a colour wash
    under a pale scrim. `wash` (the layout 'vignette' knob, 0.3–1.6) sets how
    flat it is — higher = paler/flatter, lower = more of the art's colour."""
    w, h = band.size
    bg = band.filter(ImageFilter.GaussianBlur(radius=max(8, w // 40)))
    alpha = max(120, min(238, int(140 + 58 * wash)))
    scrim = Image.new("RGBA", (w, h), (252, 250, 244, alpha))
    return Image.alpha_composite(bg, scrim)


def _draw_border(draw: ImageDraw.ImageDraw, w: int, h: int):
    """Thin double gold border — quiet edge treatment, not ornament."""
    inset = int(h * 0.040)
    draw.rectangle([inset, inset, w - inset, h - inset],
                   outline=RULE_GOLD, width=2 * _SS)
    inner = inset + 5 * _SS
    draw.rectangle([inner, inner, w - inner, h - inner],
                   outline=(*RULE_GOLD[:3], 120), width=_SS)


def _fit_quote_block(draw: ImageDraw.ImageDraw, text_flat: str, lang_code: str | None,
                     citation: str, override: list[str] | None, scale: float,
                     h: int, avail_h: int, max_text_w: int):
    """
    The front face's fit loop, shared by the real render and quote_fits_card
    (the pre-flight check the quote-suggestion endpoint uses). Walks the quote
    size down from the layout's top size to the MIN_QUOTE_PX floor and returns
    the first (q_font, q_lines, q_lh, q_size, cit_font, cit_lines, cit_lh,
    total) that fits, or None if nothing does — the caller decides whether
    None means raise (render) or False (fit check).
    """
    cjk_leading = 1.5 if lang_code == "zh" else 1.36
    size_top = max(MIN_QUOTE_PX * _SS, int(50 * _SS * scale))
    for q_size in range(size_top, MIN_QUOTE_PX * _SS - 1, -2 * _SS):
        q_font = _load_font(lang_code, q_size, override_paths=override)
        q_lines = _wrap(draw, text_flat, lang_code, q_font, max_text_w)
        q_lh = int(q_size * cjk_leading)
        total = len(q_lines) * q_lh

        cit_font = cit_lines = None
        cit_lh = 0
        if citation:
            # Fixed size (owner ask, 2026-07-31) so the attribution is the same
            # on every card; capped at the quote size so it never outsizes the
            # quote on a crowded card (and eases fit pressure as the quote shrinks).
            cit_size = min(CITATION_PX * _SS, q_size)
            cit_font = _load_font(None, cit_size, override_paths=override)
            cit_lines = _wrap(draw, citation, None, cit_font, max_text_w)
            cit_lh = int(cit_size * 1.3)
            total += int(q_size * 0.55) + int(h * 0.030) + len(cit_lines) * cit_lh

        if total <= avail_h and all(
            draw.textlength(_shape(l, lc), font=f) <= max_text_w
            for lc, f, ls in ((lang_code, q_font, q_lines), (None, cit_font, cit_lines or []))
            if f is not None
            for l in ls
        ):
            return (q_font, q_lines, q_lh, q_size, cit_font, cit_lines, cit_lh, total)
    return None


def quote_fits_card(text: str, citation: str = "", lang_code: str | None = None,
                    layout: dict | None = None) -> bool:
    """
    Pre-flight readability check: would this quote + citation render on the
    card FRONT at >= MIN_QUOTE_PX (the English/no-disclaimer case)? Runs the
    REAL fit loop (_fit_quote_block) against the same geometry as
    _compose_quote_face — keep the constants below in sync with it — but
    without composing any artwork, so it's cheap enough to call per candidate
    while suggesting quotes.
    """
    layout = layout or layout_opts.CARD_DEFAULTS
    latin = lang_code is None or not (LANGUAGES.get(lang_code) or {}).get("font_paths")
    override = layout_opts.font_paths(layout.get("font", "tahoma")) if latin else None
    scale = float(layout.get("text_scale", 1.0))
    w, h = CARD_TARGET_PX[0] * _SS, CARD_TARGET_PX[1] * _SS
    draw = ImageDraw.Draw(Image.new("RGB", (8, 8)))  # textlength ignores canvas size
    max_text_w = int(w * 0.84)
    margin_y = int(h * 0.085)
    avail_h = h - 2 * margin_y  # no disclaimer on the English card
    return _fit_quote_block(draw, " ".join(text.split()), lang_code, citation.strip(),
                            override, scale, h, avail_h, max_text_w) is not None


def _compose_quote_face(band: Image.Image, text: str, lang_code: str | None,
                        citation: str, disclaimer: str,
                        layout: dict | None) -> Image.Image:
    """
    The FRONT: the quote (in `lang_code`'s script — None means English),
    citation under a small rule, optional code-appended translation
    disclaimer pinned at the bottom. Fit loop floor is MIN_QUOTE_PX — below
    it the card refuses to render (readability rule, 2026-07-16).
    """
    layout = layout or layout_opts.CARD_DEFAULTS
    latin = lang_code is None or not (LANGUAGES.get(lang_code) or {}).get("font_paths")
    override = layout_opts.font_paths(layout.get("font", "tahoma")) if latin else None
    scale = float(layout.get("text_scale", 1.0))
    wash = float(layout.get("vignette", 1.0))

    face = _soft_background(band, wash)
    draw = ImageDraw.Draw(face)
    w, h = face.size

    max_text_w = int(w * 0.84)
    margin_y = int(h * 0.085)
    disclaimer_size = 16 * _SS
    bottom_reserved = (disclaimer_size + int(h * 0.040)) if disclaimer else 0
    avail_h = h - 2 * margin_y - bottom_reserved

    text_flat = " ".join(text.split())
    citation = citation.strip()

    fitted = _fit_quote_block(draw, text_flat, lang_code, citation, override,
                              scale, h, avail_h, max_text_w)

    if fitted is None:
        raise ValueError(
            f"Quote does not fit at the readable minimum ({MIN_QUOTE_PX}px/300dpi) on a "
            f"{CARD_SIZE_IN[0]}x{CARD_SIZE_IN[1]}in card face "
            f"({len(text_flat)} chars, language {lang_code or 'en'}) — use a shorter quote."
        )

    q_font, q_lines, q_lh, q_size, cit_font, cit_lines, cit_lh, total = fitted

    y = margin_y + (avail_h - total) // 2
    y = _draw_centered(draw, q_lines, lang_code, q_font, y, q_lh, w, fill=INK)
    if cit_lines:
        y += int(q_size * 0.55)
        rule_half = int(w * 0.085)
        draw.line([(w // 2 - rule_half, y), (w // 2 + rule_half, y)],
                  fill=RULE_GOLD, width=_SS)
        y += int(h * 0.030)
        _draw_centered(draw, cit_lines, None, cit_font, y, cit_lh, w, fill=INK_SOFT)

    if disclaimer:
        disc_font = _load_font(lang_code, disclaimer_size)
        disc_y = h - margin_y - disclaimer_size
        _draw_centered(draw, [disclaimer], lang_code, disc_font, disc_y,
                       disclaimer_size, w, fill=TINY_TEXT)

    _draw_border(draw, w, h)
    return face


def _compose_reflection_face(lang_code: str | None, reflection: dict | None,
                             band: Image.Image | None = None) -> Image.Image:
    """
    The BACK: never the quote. A reflection question, a gently-phrased call
    to action, ruled blank lines to write on, and the small code-owned share
    line. When the artwork band is given (owner ask 2026-07-16), the picture
    SURROUNDS the writing section: it stays visible around the edges under a
    light veil, melting into a solid cream panel in the middle so pen ink
    still reads cleanly — the writing area itself is never over the art.
    """
    r = _reflection_for(lang_code, reflection)
    w, h = CARD_TARGET_PX[0] * _SS, CARD_TARGET_PX[1] * _SS
    if band is not None:
        base = band.resize((w, h), _RESAMPLE).filter(
            ImageFilter.GaussianBlur(radius=max(2, w // 400)))
        # Cream overlay: the art shows THROUGH the whole back (owner ask
        # 2026-07-16) — barely veiled at the edges, mostly (not fully) opaque
        # over the writing panel so pen ink reads while the picture still
        # breathes through, with a feathered transition between the zones.
        panel_inset_x, panel_inset_y = int(w * 0.075), int(h * 0.115)
        mask = Image.new("L", (w, h), 28)   # edges: art nearly unveiled
        ImageDraw.Draw(mask).rounded_rectangle(
            [panel_inset_x, panel_inset_y, w - panel_inset_x, h - panel_inset_y],
            radius=int(h * 0.05), fill=214)  # panel: opaque enough for pen ink
        # Wide feather so the opaque-to-clear shift reads as one long gradient
        # rather than a visible panel edge (owner tuning, 2026-07-16).
        mask = mask.filter(ImageFilter.GaussianBlur(radius=int(h * 0.075)))
        overlay = Image.new("RGBA", (w, h), CREAM)
        overlay.putalpha(mask)
        face = Image.alpha_composite(base, overlay)
    else:
        face = Image.new("RGBA", (w, h), CREAM)
    draw = ImageDraw.Draw(face)

    latin = lang_code is None or not (LANGUAGES.get(lang_code) or {}).get("font_paths")
    q_paths = [TAHOMA_BOLD, TAHOMA] if latin else None
    max_text_w = int(w * 0.80)
    margin_y = int(h * 0.105)
    share_size = 15 * _SS

    # Question — shrink modestly if it wraps long; it must stay prominent.
    q_font = q_lines = None
    for q_size in range(26 * _SS, 19 * _SS, -2 * _SS):
        q_font = _load_font(lang_code, q_size, override_paths=q_paths)
        q_lines = _wrap(draw, r["question"], lang_code, q_font, max_text_w)
        if len(q_lines) <= 2:
            break
    q_lh = int(q_font.size * 1.4)
    y = margin_y
    y = _draw_centered(draw, q_lines, lang_code, q_font, y, q_lh, w, fill=INK)

    # Action label, left-aligned over the writing area.
    a_size = 19 * _SS
    a_font = _load_font(lang_code, a_size)
    left_x = int(w * 0.10)
    y += int(h * 0.055)
    draw.text((left_x, y), _shape(r["action"], lang_code), font=a_font, fill=INK_SOFT)
    y += int(a_size * 1.5)

    # Three ruled writing lines (owner ask 2026-07-16), spread evenly between
    # the action label and the share line — roomier per-line pen space.
    share_y = h - int(h * 0.085) - share_size
    right_x = w - left_x
    write_top = y + int(h * 0.015)
    write_bottom = share_y - int(h * 0.06)
    n_lines = 3
    step = max(1, (write_bottom - write_top) // n_lines)
    for i in range(1, n_lines + 1):
        line_y = write_top + i * step
        draw.line([(left_x, line_y), (right_x, line_y)], fill=WRITE_LINE, width=_SS)

    share_font = _load_font(lang_code, share_size)
    _draw_centered(draw, [r["share"]], lang_code, share_font, share_y,
                   share_size, w, fill=TINY_TEXT)

    _draw_border(draw, w, h)
    return face


def render_quote_card(image_path: str, quote_en: str, citation: str = "",
                      translation: dict | None = None, layout: dict | None = None,
                      dest_stem: str | None = None, reflection: dict | None = None) -> dict:
    """
    Render the quote-card product (redesign 2026-07-16). Always produces the
    ENGLISH card pair:
      front — the quote over a soft wash of the artwork; back — reflection face.
    When `translation` (translator.translate_quote dict) carries text, ALSO
    produces a separate card pair in that language: its front is the
    translated quote + the code-appended native disclaimer (rule 8), its back
    the reflection face in that language. The quote never appears on any back.

    `reflection`: optional {"question","action", "native": {...}} from the
    pipeline; code-owned defaults fill any gap. `layout` is
    agents.layout.sanitize output (font/scale/wash — the text_color knob is
    ignored by cards since the redesign: ink on a light wash is fixed).

    Returns {"front_path", "back_path", "variants": {code: {front_path, back_path}}}
    — existing callers that only read front/back keep working.
    """
    img = Image.open(image_path).convert("RGBA")
    img = _normalise_ratio(img, 2, 3)
    w, h = img.size

    band_h = int(w * CARD_SIZE_IN[1] / CARD_SIZE_IN[0])
    band_top = (h - band_h) // 2
    ss_target = (CARD_TARGET_PX[0] * _SS, CARD_TARGET_PX[1] * _SS)
    band = img.crop((0, band_top, w, band_top + band_h)).resize(ss_target, _RESAMPLE)

    def _save(face: Image.Image, path: str):
        face.resize(CARD_TARGET_PX, _RESAMPLE).convert("RGB").save(
            path, "PNG", dpi=(PRINT_DPI, PRINT_DPI))

    stem = dest_stem or f"card-{uuid.uuid4().hex[:8]}"
    front_path = str(OUTPUTS_DIR / f"{stem}-front.png")
    back_path = str(OUTPUTS_DIR / f"{stem}-back.png")

    _save(_compose_quote_face(band, quote_en, None, citation, "", layout), front_path)
    _save(_compose_reflection_face(None, reflection, band=band), back_path)
    result = {"front_path": front_path, "back_path": back_path, "variants": {}}

    tr_text = (translation or {}).get("text", "").strip()
    if tr_text:
        code = (translation or {}).get("code") or "xx"
        disclaimer = (translation or {}).get("disclaimer_native", "").strip()
        v_front = str(OUTPUTS_DIR / f"{stem}-{code}-front.png")
        v_back = str(OUTPUTS_DIR / f"{stem}-{code}-back.png")
        _save(_compose_quote_face(band, tr_text, code, citation, disclaimer, layout), v_front)
        _save(_compose_reflection_face(code, reflection, band=band), v_back)
        result["variants"][code] = {"front_path": v_front, "back_path": v_back}
    return result
