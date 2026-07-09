"""
Compositor — overlays a short quote onto AI-generated bookmark artwork.
Produces print-ready PNGs (300 dpi) in outputs/.
A 2:3 image is split left/right into two 1:3 halves: front (quote overlay) and back (clean art).
"""

import math
import uuid
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from agents import layout as layout_opts

OUTPUTS_DIR = Path(__file__).parent.parent / "outputs"
OUTPUTS_DIR.mkdir(exist_ok=True)

# The base serif stack now lives in agents/layout.py (the layout editor's
# source of truth); kept under this name so card_compositor's import of
# _FONT_PATHS from here keeps working.
_FONT_PATHS = layout_opts.SERIF_STACK

GOLD   = (212, 175, 55, 210)
WHITE  = (255, 255, 255, 255)
SHADOW = (0,   0,   0,  200)

# Print target: a bookmark face is 2"x6". The xAI image model has a fixed
# native output resolution (832x1248 as generated) with no size/quality
# parameter available — a direct API request for a larger size is rejected
# outright ("Argument not supported: size"), and no higher-resolution model
# tier exists. A bare crop of that source into front/back quarters (416x1248)
# is only ~208 real dpi at 2x6" despite being saved with 300dpi metadata — a
# false claim baked into the file. Upscaling to true 300dpi pixel dimensions
# BEFORE compositing (so the quote text and star are drawn crisp at final
# size, not blurred by a later resize) closes that gap. This is interpolation
# for correct print dimensions, not genuinely new photographic detail — the
# source artwork's real resolution is still capped by the generator.
PRINT_DPI = 300
FACE_SIZE_IN = (2, 6)
FACE_TARGET_PX = (PRINT_DPI * FACE_SIZE_IN[0], PRINT_DPI * FACE_SIZE_IN[1])  # (600, 1800)
_RESAMPLE = Image.Resampling.LANCZOS if hasattr(Image, "Resampling") else Image.LANCZOS


def _nine_pointed_star(cx: int, cy: int, r_outer: float, r_inner: float) -> list:
    """Return polygon vertices for a 9-pointed star centred at (cx, cy)."""
    pts = []
    for k in range(9):
        angle_out = math.radians(k * 40 - 90)   # 360/9 = 40°, start at top
        pts.append((cx + r_outer * math.cos(angle_out),
                    cy + r_outer * math.sin(angle_out)))
        angle_in = math.radians(k * 40 + 20 - 90)  # offset by half step
        pts.append((cx + r_inner * math.cos(angle_in),
                    cy + r_inner * math.sin(angle_in)))
    return pts


def _font(size: int, paths: list[str] | None = None) -> ImageFont.FreeTypeFont:
    for path in (paths or _FONT_PATHS):
        try:
            return ImageFont.truetype(path, size)
        except (IOError, OSError):
            continue
    return ImageFont.load_default(size=size)


def _normalise_ratio(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """Center-crop img to the target W:H ratio (within 2% tolerance)."""
    w, h = img.size
    target = target_w / target_h
    actual = w / h
    if actual > target * 1.02:
        new_w = int(h * target)
        x1 = (w - new_w) // 2
        img = img.crop((x1, 0, x1 + new_w, h))
    elif actual < target * 0.98:
        new_h = int(w / target)
        y1 = (h - new_h) // 2
        img = img.crop((0, y1, w, y1 + new_h))
    return img


def _composite_front(img: Image.Image, quote: str, layout: dict | None = None) -> Image.Image:
    """
    Apply gradient + quote text + rule + 9-pointed star to a 1:3 image.
    Returns RGBA image.

    `layout` (sanitised via agents.layout.sanitize) tunes presentation only —
    font, text size, vertical position, colour, gradient strength, and whether
    the rule/star are drawn. The `quote` text itself is never sourced from
    here. With the default layout the output is identical to the pre-editor
    render.
    """
    layout = layout or layout_opts.BOOKMARK_DEFAULTS
    font_paths = layout_opts.font_paths(layout.get("font", "palatino"))
    text_fill = layout_opts.color_rgba(layout.get("text_color", "white"))
    grad_mult = float(layout.get("gradient", 1.0))
    scale = float(layout.get("text_scale", 1.0))
    offset = float(layout.get("text_offset", 0.0))
    w, h = img.size

    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    grad_top = int(h * 0.44)                       # start gradient higher up the image
    alpha_cap = max(0, min(255, int(245 * grad_mult)))
    for y in range(grad_top, h):
        progress = (y - grad_top) / (h - grad_top)
        alpha = int(alpha_cap * (progress ** 0.45))  # darker and more aggressive fade
        od.rectangle([0, y, w, y + 1], fill=(0, 0, 0, alpha))

    composited = Image.alpha_composite(img, overlay)
    draw = ImageDraw.Draw(composited)

    # --- layout anchored from the bottom so star is always visible ---
    star_r  = max(12, int(w * 0.045))
    star_cx = w // 2
    star_cy = h - int(h * 0.04) - star_r          # fixed distance from bottom edge
    rule_y  = star_cy - star_r - int(h * 0.018)   # rule sits just above the star

    text_y = grad_top + int((h - grad_top) * 0.05)
    # Optional vertical nudge, clamped so the quote always stays between the
    # gradient top and the rule regardless of the requested offset.
    text_y = max(int(h * 0.06), min(text_y + int(offset * h), rule_y - int(h * 0.12)))
    max_text_height = rule_y - int(h * 0.018) - text_y  # space available for text

    # quote text — wider column, greedy word-wrap, auto-shrink to fit both width & height
    max_text_w = int(w * 0.82)
    q_size = max(16, int(w * 0.13 * scale))

    def _line_w(text, font):
        bb = draw.textbbox((0, 0), text, font=font)
        return bb[2] - bb[0]

    def _wrap_greedy(words, font):
        lines_out, current = [], ""
        for word in words:
            test = (current + " " + word).strip()
            if _line_w(test, font) <= max_text_w:
                current = test
            else:
                if current:
                    lines_out.append(current)
                current = word
        if current:
            lines_out.append(current)
        return lines_out

    words = quote.strip().split()
    lines, q_font = [], None
    for size in range(q_size, 8, -1):
        font_try = _font(size, font_paths)
        wrapped  = _wrap_greedy(words, font_try)
        line_h   = int(size * 1.65)
        if (wrapped
                and all(_line_w(l, font_try) <= max_text_w for l in wrapped)
                and len(wrapped) * line_h <= max_text_height):
            lines, q_font, q_size = wrapped, font_try, size
            break

    line_h = int(q_size * 1.65)

    for i, line in enumerate(lines):
        lw = _line_w(line, q_font)
        x  = (w - lw) // 2
        y  = text_y + i * line_h
        for dx, dy in [(-2, -2), (2, -2), (-2, 2), (2, 2), (0, 3), (3, 0)]:
            draw.text((x + dx, y + dy), line, font=q_font, fill=SHADOW)
        draw.text((x, y), line, font=q_font, fill=text_fill)

    # thin gold rule
    if layout.get("show_rule", True):
        pad_x = int(w * 0.16)
        draw.line([(pad_x, rule_y), (w - pad_x, rule_y)], fill=GOLD, width=1)

    # 9-pointed star — anchored, always inside the image
    if layout.get("show_star", True):
        pts = _nine_pointed_star(star_cx, star_cy, star_r, star_r * 0.42)
        draw.polygon(pts, fill=GOLD)
        draw.polygon(pts, outline=(160, 130, 30, 180))

    return composited


def _composite_back(img: Image.Image) -> Image.Image:
    """Apply minimal vignette + small star watermark to the back half. Returns RGBA image."""
    w, h = img.size

    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    grad_top = int(h * 0.88)
    for y in range(grad_top, h):
        progress = (y - grad_top) / (h - grad_top)
        alpha = int(100 * (progress ** 0.5))
        od.rectangle([0, y, w, y + 1], fill=(0, 0, 0, alpha))

    return Image.alpha_composite(img, overlay)


def render_bookmark_pair(image_path: str, quote: str, layout: dict | None = None,
                         dest_stem: str | None = None) -> dict:
    """
    From a 2:3 image, extract two 1:3 bookmark faces:
      Front — center strip (W/4 → 3W/4): the focal point of the artwork, with quote overlay.
      Back  — outer quarters (left W/4 + right W/4) stitched side by side: the frame/border art.

    `layout` (agents.layout.sanitize output) controls presentation only — see
    _composite_front; None reproduces the pre-editor render exactly. `dest_stem`,
    when given, writes to `<stem>-front.png`/`<stem>-back.png` (overwriting) so
    the live layout preview reuses one pair of files per product instead of
    accumulating; the default is fresh uuid-named files.
    Returns: {front_path, back_path}
    """
    img = Image.open(image_path).convert("RGBA")
    img = _normalise_ratio(img, 2, 3)
    w, h = img.size

    q = w // 4  # one quarter width

    # Front: center half of the image
    front_img = img.crop((q, 0, w - q, h))

    # Back: left quarter + right quarter joined side by side
    left_q  = img.crop((0, 0, q, h))
    right_q = img.crop((w - q, 0, w, h))
    back_img = Image.new("RGBA", (q * 2, h), (0, 0, 0, 0))
    back_img.paste(left_q,  (0, 0))
    back_img.paste(right_q, (q, 0))

    # Upscale both faces to true 300dpi print dimensions before compositing
    # text/star — see PRINT_DPI comment above.
    front_img = front_img.resize(FACE_TARGET_PX, _RESAMPLE)
    back_img  = back_img.resize(FACE_TARGET_PX, _RESAMPLE)

    stem = dest_stem or f"bookmark-{uuid.uuid4().hex[:8]}"
    front_path = str(OUTPUTS_DIR / f"{stem}-front.png")
    back_path  = str(OUTPUTS_DIR / f"{stem}-back.png")

    _composite_front(front_img, quote, layout).convert("RGB").save(front_path, "PNG", dpi=(300, 300))
    _composite_back(back_img).convert("RGB").save(back_path, "PNG", dpi=(300, 300))

    return {"front_path": front_path, "back_path": back_path}


def render_bookmark(image_path: str, quote: str, output_path: str = None,
                    layout: dict | None = None) -> str:
    """Single-image fallback: treat the whole image as a 1:3 front face."""
    img = Image.open(image_path).convert("RGBA")
    img = _normalise_ratio(img, 1, 3)
    img = img.resize(FACE_TARGET_PX, _RESAMPLE)
    result = _composite_front(img, quote, layout).convert("RGB")
    if not output_path:
        output_path = str(OUTPUTS_DIR / f"bookmark-final-{uuid.uuid4().hex[:8]}.png")
    result.save(output_path, "PNG", dpi=(300, 300))
    return output_path
