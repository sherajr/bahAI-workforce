"""
Compositor — overlays a short quote onto AI-generated bookmark artwork.
Produces print-ready PNGs (300 dpi) in outputs/.
A 2:3 image is split left/right into two 1:3 halves: front (quote overlay) and back (clean art).
"""

import math
import uuid
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUTPUTS_DIR = Path(__file__).parent.parent / "outputs"
OUTPUTS_DIR.mkdir(exist_ok=True)

_FONT_PATHS = [
    "C:/Windows/Fonts/palai.ttf",    # Palatino Linotype Italic — elegant default
    "C:/Windows/Fonts/pala.ttf",     # Palatino Linotype Regular
    "C:/Windows/Fonts/georgiai.ttf", # Georgia Italic
    "C:/Windows/Fonts/georgia.ttf",  # Georgia Regular
    "C:/Windows/Fonts/times.ttf",    # Times New Roman
]

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


def _font(size: int) -> ImageFont.FreeTypeFont:
    for path in _FONT_PATHS:
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


def _composite_front(img: Image.Image, quote: str) -> Image.Image:
    """Apply gradient + quote text + rule + 9-pointed star to a 1:3 image. Returns RGBA image."""
    w, h = img.size

    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    grad_top = int(h * 0.44)                       # start gradient higher up the image
    for y in range(grad_top, h):
        progress = (y - grad_top) / (h - grad_top)
        alpha = int(245 * (progress ** 0.45))      # darker and more aggressive fade
        od.rectangle([0, y, w, y + 1], fill=(0, 0, 0, alpha))

    composited = Image.alpha_composite(img, overlay)
    draw = ImageDraw.Draw(composited)

    # --- layout anchored from the bottom so star is always visible ---
    star_r  = max(12, int(w * 0.045))
    star_cx = w // 2
    star_cy = h - int(h * 0.04) - star_r          # fixed distance from bottom edge
    rule_y  = star_cy - star_r - int(h * 0.018)   # rule sits just above the star

    text_y        = grad_top + int((h - grad_top) * 0.05)
    max_text_height = rule_y - int(h * 0.018) - text_y  # space available for text

    # quote text — wider column, greedy word-wrap, auto-shrink to fit both width & height
    max_text_w = int(w * 0.82)
    q_size = max(16, int(w * 0.13))

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
        font_try = _font(size)
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
        draw.text((x, y), line, font=q_font, fill=WHITE)

    # thin gold rule
    pad_x = int(w * 0.16)
    draw.line([(pad_x, rule_y), (w - pad_x, rule_y)], fill=GOLD, width=1)

    # 9-pointed star — anchored, always inside the image
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


def render_bookmark_pair(image_path: str, quote: str) -> dict:
    """
    From a 2:3 image, extract two 1:3 bookmark faces:
      Front — center strip (W/4 → 3W/4): the focal point of the artwork, with quote overlay.
      Back  — outer quarters (left W/4 + right W/4) stitched side by side: the frame/border art.
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

    uid = uuid.uuid4().hex[:8]
    front_path = str(OUTPUTS_DIR / f"bookmark-front-{uid}.png")
    back_path  = str(OUTPUTS_DIR / f"bookmark-back-{uid}.png")

    _composite_front(front_img, quote).convert("RGB").save(front_path, "PNG", dpi=(300, 300))
    _composite_back(back_img).convert("RGB").save(back_path, "PNG", dpi=(300, 300))

    return {"front_path": front_path, "back_path": back_path}


def render_bookmark(image_path: str, quote: str, output_path: str = None) -> str:
    """Single-image fallback: treat the whole image as a 1:3 front face."""
    img = Image.open(image_path).convert("RGBA")
    img = _normalise_ratio(img, 1, 3)
    img = img.resize(FACE_TARGET_PX, _RESAMPLE)
    result = _composite_front(img, quote).convert("RGB")
    if not output_path:
        output_path = str(OUTPUTS_DIR / f"bookmark-final-{uuid.uuid4().hex[:8]}.png")
    result.save(output_path, "PNG", dpi=(300, 300))
    return output_path
