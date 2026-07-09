"""
Card Compositor — renders the Quote Card product: a 3.5"×2" landscape,
business-card-sized giveaway (front: vignetted artwork + centered quote,
optional translation, citation, translation disclaimer; back: clean artwork).

Sibling of compositor.py (bookmarks), deliberately separate so the bookmark
render path stays untouched. Shares the brand constants and the
true-print-resolution discipline: faces are upscaled to real 300dpi pixel
dimensions BEFORE any text is drawn, so type is crisp at final size (see the
PRINT_DPI comment in compositor.py for why DPI metadata alone is a lie).

Script support: Latin renders with the bookmark's serif stack; Mandarin and
Arabic use fonts from translator.LANGUAGES (verified to contain real glyphs,
not tofu). Arabic is shaped with arabic_reshaper and reordered with
python-bidi PER LINE at draw time — PIL alone would render disjointed,
left-to-right letterforms. Wrapping happens on the logical (unshaped) text;
shaping per line is safe because Arabic letters never join across a space.
"""

import math
import uuid

from PIL import Image, ImageDraw, ImageFont

from agents import layout as layout_opts
from agents.compositor import (
    GOLD, WHITE, SHADOW, OUTPUTS_DIR, PRINT_DPI, _FONT_PATHS, _RESAMPLE,
    _normalise_ratio,
)
from agents.translator import LANGUAGES

CARD_SIZE_IN = (3.5, 2)  # landscape business-card size
CARD_TARGET_PX = (int(PRINT_DPI * CARD_SIZE_IN[0]), int(PRINT_DPI * CARD_SIZE_IN[1]))  # (1050, 600)


def _font_stack(lang_code: str | None) -> list[str]:
    """Font paths to try for a language (None/Latin → bookmark serif stack)."""
    if lang_code:
        paths = (LANGUAGES.get(lang_code) or {}).get("font_paths")
        if paths:
            return list(paths) + _FONT_PATHS
    return list(_FONT_PATHS)


def _load_font(lang_code: str | None, size: int,
               override_paths: list[str] | None = None) -> ImageFont.FreeTypeFont:
    # override_paths (the layout editor's Latin font choice) is only ever
    # passed for Latin text (English quote + citation). Translation and
    # disclaimer keep their script-verified fonts from LANGUAGES (rule 9), so
    # the editor can never swap a CJK/Arabic face for one lacking its glyphs.
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
    """
    Split text into wrap units and the joiner used to reassemble a line.
    CJK has no spaces — wrap per character; everything else wraps per word.
    """
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
                   fill=WHITE, shadow_px: int = 2) -> int:
    """Draw shaped lines centered horizontally starting at y. Returns next y."""
    for line in lines:
        shaped = _shape(line, lang_code)
        lw = draw.textlength(shaped, font=font)
        x = int((face_w - lw) // 2)
        if shadow_px:
            for dx, dy in [(-shadow_px, -shadow_px), (shadow_px, -shadow_px),
                           (-shadow_px, shadow_px), (shadow_px, shadow_px)]:
                draw.text((x + dx, y + dy), shaped, font=font, fill=SHADOW)
        draw.text((x, y), shaped, font=font, fill=fill)
        y += line_h
    return y


def _vignette(img: Image.Image, center_alpha: int, edge_alpha: int) -> Image.Image:
    """
    Soft radial scrim: `center_alpha` black in the middle easing to
    `edge_alpha` at the corners. Built at 64×64 and upscaled — smooth and cheap.
    """
    w, h = img.size
    small = 64
    mask = Image.new("L", (small, small))
    px = mask.load()
    for y in range(small):
        for x in range(small):
            dx = (x - small / 2) / (small / 2)
            dy = (y - small / 2) / (small / 2)
            d = min(1.0, math.hypot(dx, dy) / 1.35)
            px[x, y] = int(center_alpha + (edge_alpha - center_alpha) * (d ** 2))
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 255))
    overlay.putalpha(mask.resize((w, h), _RESAMPLE))
    return Image.alpha_composite(img, overlay)


def _composite_card_front(img: Image.Image, quote_en: str, citation: str,
                          translation: dict | None, layout: dict | None = None) -> Image.Image:
    """
    Centered text stack over a soft vignette:
      English quote → (translation) → gold rule → citation → (disclaimer, pinned bottom)
    Auto-shrinks until the stack fits; raises if even the minimum size
    overflows — an illegible card must fail loudly, never ship silently.

    `layout` (agents.layout.sanitize output) tunes the ENGLISH quote + citation
    font, the English text size and colour, and the vignette strength. The
    translation and the two disclaimers are untouched by it — their fonts stay
    script-verified and their text is always supplied by the caller from stored
    data, never editable here (CLAUDE.md rules 8, 9). Default layout reproduces
    the pre-editor render.
    """
    layout = layout or layout_opts.CARD_DEFAULTS
    en_paths = layout_opts.font_paths(layout.get("font", "palatino"))
    en_fill = layout_opts.color_rgba(layout.get("text_color", "white"))
    scale = float(layout.get("text_scale", 1.0))
    vig = float(layout.get("vignette", 1.0))

    composited = _vignette(img, center_alpha=max(0, min(255, int(118 * vig))),
                           edge_alpha=max(0, min(255, int(200 * vig))))
    draw = ImageDraw.Draw(composited)
    w, h = composited.size

    lang = (translation or {}).get("code")
    tr_text = (translation or {}).get("text", "").strip()
    disclaimer = (translation or {}).get("disclaimer_native", "").strip()

    max_text_w = int(w * 0.84)
    margin_y = int(h * 0.075)
    # Reserve a strip at the bottom for the disclaimer when there is one.
    disclaimer_size = 17
    bottom_reserved = (disclaimer_size + int(h * 0.045)) if disclaimer else 0
    avail_h = h - 2 * margin_y - bottom_reserved

    quote_flat = " ".join(quote_en.split())
    citation = citation.strip()

    fitted = None
    en_size_top = max(18, int(52 * scale))
    for en_size in range(en_size_top, 15, -2):
        en_font = _load_font(None, en_size, override_paths=en_paths)
        en_lines = _wrap(draw, quote_flat, None, en_font, max_text_w)
        en_lh = int(en_size * 1.32)
        total = len(en_lines) * en_lh

        tr_font = tr_lines = None
        tr_lh = 0
        if tr_text:
            tr_size = max(18, int(en_size * 0.82))
            tr_font = _load_font(lang, tr_size)
            tr_lines = _wrap(draw, tr_text, lang, tr_font, max_text_w)
            # CJK/Arabic need more leading than Latin at the same size
            tr_lh = int(tr_size * 1.45)
            total += int(en_size * 0.5) + len(tr_lines) * tr_lh

        cit_font = cit_lines = None
        cit_lh = 0
        if citation:
            cit_size = max(16, int(en_size * 0.44))
            cit_font = _load_font(None, cit_size, override_paths=en_paths)
            cit_lines = _wrap(draw, citation, None, cit_font, max_text_w)
            cit_lh = int(cit_size * 1.3)
            total += int(en_size * 0.55) + int(h * 0.035) + len(cit_lines) * cit_lh

        if total <= avail_h and all(
            draw.textlength(_shape(l, lc), font=f) <= max_text_w
            for lc, f, ls in ((None, en_font, en_lines), (lang, tr_font, tr_lines or []),
                              (None, cit_font, cit_lines or []))
            if f is not None
            for l in ls
        ):
            fitted = (en_font, en_lines, en_lh, en_size,
                      tr_font, tr_lines, tr_lh, cit_font, cit_lines, cit_lh, total)
            break

    if fitted is None:
        raise ValueError(
            f"Quote card text does not fit legibly on a {CARD_SIZE_IN[0]}x{CARD_SIZE_IN[1]}in face "
            f"(quote {len(quote_flat)} chars, translation {len(tr_text)} chars) — "
            "use a shorter quote."
        )

    (en_font, en_lines, en_lh, en_size,
     tr_font, tr_lines, tr_lh, cit_font, cit_lines, cit_lh, total) = fitted

    y = margin_y + (avail_h - total) // 2
    y = _draw_centered(draw, en_lines, None, en_font, y, en_lh, w, fill=en_fill)
    if tr_lines:
        y += int(en_size * 0.5)
        y = _draw_centered(draw, tr_lines, lang, tr_font, y, tr_lh, w)
    if cit_lines:
        y += int(en_size * 0.55)
        rule_half = int(w * 0.10)
        draw.line([(w // 2 - rule_half, y), (w // 2 + rule_half, y)], fill=GOLD, width=1)
        y += int(h * 0.035)
        y = _draw_centered(draw, cit_lines, None, cit_font, y, cit_lh, w,
                           fill=(255, 244, 214, 235), shadow_px=1)

    if disclaimer:
        disc_font = _load_font(lang, disclaimer_size)
        disc_y = h - margin_y - disclaimer_size
        _draw_centered(draw, [disclaimer], lang, disc_font, disc_y, disclaimer_size, w,
                       fill=(255, 255, 255, 210), shadow_px=1)

    return composited


def render_quote_card(image_path: str, quote_en: str, citation: str = "",
                      translation: dict | None = None, layout: dict | None = None,
                      dest_stem: str | None = None) -> dict:
    """
    From one (portrait 2:3) artwork, produce the two 3.5"×2" card faces at a
    true 1050×600px / 300dpi:
      Back  — the artwork's central band: the focal point, clean, no text.
      Front — a higher band of the same artwork (calmer region, and visibly
              different from the back) under the vignette + quote stack.
    translation, when given, is translator.translate_quote()'s dict.

    `layout` (agents.layout.sanitize output) controls presentation only — see
    _composite_card_front; None reproduces the pre-editor render. `dest_stem`,
    when given, writes to `<stem>-front.png`/`<stem>-back.png` (overwriting) so
    the live layout preview reuses one file pair per product instead of
    accumulating; default is fresh uuid-named files.
    Returns {front_path, back_path}.
    """
    img = Image.open(image_path).convert("RGBA")
    img = _normalise_ratio(img, 2, 3)
    w, h = img.size

    band_h = int(w * CARD_SIZE_IN[1] / CARD_SIZE_IN[0])  # 7:4 band from full width
    back_top = (h - band_h) // 2
    front_top = min(int(h * 0.12), max(0, back_top - band_h // 3))

    back_img = img.crop((0, back_top, w, back_top + band_h)).resize(CARD_TARGET_PX, _RESAMPLE)
    front_img = img.crop((0, front_top, w, front_top + band_h)).resize(CARD_TARGET_PX, _RESAMPLE)

    front = _composite_card_front(front_img, quote_en, citation, translation, layout)
    back = _vignette(back_img, center_alpha=0, edge_alpha=60)

    stem = dest_stem or f"card-{uuid.uuid4().hex[:8]}"
    front_path = str(OUTPUTS_DIR / f"{stem}-front.png")
    back_path = str(OUTPUTS_DIR / f"{stem}-back.png")
    front.convert("RGB").save(front_path, "PNG", dpi=(PRINT_DPI, PRINT_DPI))
    back.convert("RGB").save(back_path, "PNG", dpi=(PRINT_DPI, PRINT_DPI))
    return {"front_path": front_path, "back_path": back_path}
