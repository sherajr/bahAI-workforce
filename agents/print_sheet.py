"""
Print Sheet -- arranges an existing front/back card image pair (bookmark or
quote-card faces, or any future product) into a cut-tolerant, multi-up
Letter-page layout for home printing.

Design notes:
- Card physical size is derived from the front image's OWN pixel dimensions
  at DPI (matching the true-300dpi convention already established in
  compositor.py) -- this module has no opinion on which product made the
  PNGs or their aspect ratio. A 1050x600 quote-card face becomes a 3.5x2in
  card; a 600x1800 bookmark face becomes a 2x6in card; automatically.
- Grid size (cols x rows) is computed to fill a US Letter page for whatever
  that card size turns out to be.
- The gaps between cards, AND the outer margin, are filled with one
  continuous, non-directional textured pattern instead of white space.
  Because the pattern looks the same everywhere, a cut that wanders a
  little in either direction still leaves every card looking intentionally
  framed -- no precision cutting required. A double keyline sits just
  inside each card's true edge as a second fallback frame.
- Output is a single 2-page PDF: page 1 = fronts grid, page 2 = backs grid,
  each card at the same grid position on both pages, so pairing them up
  after cutting (or gluing front-to-back) is just "match the position."
"""

import math
import random
import uuid
from pathlib import Path

from PIL import Image, ImageDraw

DPI = 300
PAGE_W_IN, PAGE_H_IN = 8.5, 11.0
PAGE_W_PX, PAGE_H_PX = round(PAGE_W_IN * DPI), round(PAGE_H_IN * DPI)
SEAM_IN = 0.25  # gap between cards AND outer margin -- the "cut anywhere in here" zone

OUTPUTS_DIR = Path(__file__).parent.parent / "outputs"

_RESAMPLE = Image.Resampling.LANCZOS if hasattr(Image, "Resampling") else Image.LANCZOS

# Antique-gold palette matched to the quote card's dawn-garden artwork.
# Pass a different palette to build_print_sheet() for other product lines.
GOLD_PALETTE = {
    "base":          (205, 181, 138),
    "noise_lo":      (196, 170, 124),
    "noise_hi":      (216, 194, 154),
    "star_deep":     (158, 128, 82),
    "star_pale":     (236, 222, 192),
    "keyline_dark":  (128, 100, 60),
    "keyline_light": (245, 235, 214),
}


def _nine_pointed_star(draw, cx, cy, r, color, rot=0.0):
    """Same 9-pointed motif compositor.py already draws on card faces."""
    pts = []
    inner = r * 0.45
    for i in range(18):
        ang = rot + math.pi * i / 9.0 - math.pi / 2
        rad = r if i % 2 == 0 else inner
        pts.append((cx + rad * math.cos(ang), cy + rad * math.sin(ang)))
    draw.polygon(pts, fill=color)


def _make_pattern(w: int, h: int, palette: dict, seed: int = 7) -> Image.Image:
    """Uniform, non-directional textured fill -- looks identical no matter
    where within it a card gets cut, so an imprecise cut still looks
    deliberate rather than like a mistake."""
    rng = random.Random(seed)
    img = Image.new("RGB", (w, h), palette["base"])
    d = ImageDraw.Draw(img)
    for _ in range(w * h // 55):
        x, y = rng.randrange(w), rng.randrange(h)
        d.point((x, y), fill=palette["noise_lo"] if rng.random() < 0.5 else palette["noise_hi"])
    for _ in range(int(w * h / 5200)):
        x, y = rng.uniform(0, w), rng.uniform(0, h)
        r = rng.uniform(7, 13)
        col = palette["star_deep"] if rng.random() < 0.55 else palette["star_pale"]
        _nine_pointed_star(d, x, y, r, col, rot=rng.uniform(0, math.pi))
    return img


def _load_flat(path: str) -> Image.Image:
    """Open an image and flatten any transparency onto white."""
    img = Image.open(path)
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        return bg
    return img.convert("RGB")


def _fit_crop(img: Image.Image, target_ratio: float) -> Image.Image:
    """Center-crop img to target_ratio (w/h) without distorting it."""
    w, h = img.size
    src_ratio = w / h
    if abs(src_ratio - target_ratio) > 0.001:
        if src_ratio > target_ratio:
            new_w = int(h * target_ratio)
            off = (w - new_w) // 2
            img = img.crop((off, 0, off + new_w, h))
        else:
            new_h = int(w / target_ratio)
            off = (h - new_h) // 2
            img = img.crop((0, off, w, off + new_h))
    return img


def _auto_grid(card_w_in: float, card_h_in: float, seam_in: float = SEAM_IN) -> tuple:
    """Largest (cols, rows) of card_w_in x card_h_in cards that fit on a
    Letter page, with a seam_in gap between cards AND around the outside.
    Assumes card_w_in/card_h_in individually fit within the page -- true
    for any realistic card size this codebase produces."""
    def max_count(card_dim, page_dim):
        n = 1
        while (n + 1) * card_dim + n * seam_in + 2 * seam_in <= page_dim:
            n += 1
        return n
    return max_count(card_w_in, PAGE_W_IN), max_count(card_h_in, PAGE_H_IN)


def _sheet_page(card_img: Image.Image, card_w_px: int, card_h_px: int,
                 cols: int, rows: int, seam_px: int, palette: dict) -> Image.Image:
    block_w = cols * card_w_px + (cols - 1) * seam_px + 2 * seam_px
    block_h = rows * card_h_px + (rows - 1) * seam_px + 2 * seam_px
    bx = (PAGE_W_PX - block_w) // 2
    by = (PAGE_H_PX - block_h) // 2

    page = Image.new("RGB", (PAGE_W_PX, PAGE_H_PX), (255, 255, 255))
    page.paste(_make_pattern(block_w, block_h, palette), (bx, by))
    d = ImageDraw.Draw(page)

    for r in range(rows):
        for c in range(cols):
            x = bx + seam_px + c * (card_w_px + seam_px)
            y = by + seam_px + r * (card_h_px + seam_px)
            page.paste(card_img, (x, y))
            d.rectangle([x - 1, y - 1, x + card_w_px, y + card_h_px],
                        outline=palette["keyline_light"], width=2)
            d.rectangle([x - 4, y - 4, x + card_w_px + 3, y + card_h_px + 3],
                        outline=palette["keyline_dark"], width=2)

    tick = (150, 150, 150)
    def v_tick(x):
        d.line([x, by - 40, x, by - 10], fill=tick, width=3)
        d.line([x, by + block_h + 10, x, by + block_h + 40], fill=tick, width=3)
    def h_tick(y):
        d.line([bx - 40, y, bx - 10, y], fill=tick, width=3)
        d.line([bx + block_w + 10, y, bx + block_w + 40, y], fill=tick, width=3)

    for c in range(1, cols):
        v_tick(bx + seam_px + c * card_w_px + (c - 1) * seam_px + seam_px // 2)
    for r in range(1, rows):
        h_tick(by + seam_px + r * card_h_px + (r - 1) * seam_px + seam_px // 2)
    for x in (bx, bx + block_w):
        v_tick(x)
    for y in (by, by + block_h):
        h_tick(y)

    return page


def build_print_sheet(front_path: str, back_path: str, out_pdf_path: str = None,
                       palette: dict = None) -> str:
    """
    Build a 2-page Letter PDF from an existing front/back image pair:
    page 1 is a grid of the front face, page 2 the same grid of the back
    face, both at the card's true printed size (derived from the front
    image's own pixel dimensions at 300dpi). Gaps and outer margin are
    filled with a continuous textured pattern plus a keyline on every
    card, so cuts don't need to be precise. Returns the output path.

    out_pdf_path defaults to outputs/print-sheet-<random>.pdf if not given
    -- pass an explicit path (e.g. keyed by product_id) when you want
    repeat calls to overwrite the same file rather than accumulate.
    """
    palette = palette or GOLD_PALETTE

    front_raw = _load_flat(front_path)
    back_raw = _load_flat(back_path)

    card_w_px, card_h_px = front_raw.size
    card_w_in, card_h_in = card_w_px / DPI, card_h_px / DPI
    ratio = card_w_px / card_h_px

    back_card = _fit_crop(back_raw, ratio).resize((card_w_px, card_h_px), _RESAMPLE)

    cols, rows = _auto_grid(card_w_in, card_h_in)
    seam_px = round(SEAM_IN * DPI)

    front_page = _sheet_page(front_raw, card_w_px, card_h_px, cols, rows, seam_px, palette)
    back_page = _sheet_page(back_card, card_w_px, card_h_px, cols, rows, seam_px, palette)

    if out_pdf_path is None:
        OUTPUTS_DIR.mkdir(exist_ok=True)
        out_pdf_path = str(OUTPUTS_DIR / f"print-sheet-{uuid.uuid4().hex[:8]}.pdf")

    front_page.save(out_pdf_path, "PDF", resolution=float(DPI),
                     save_all=True, append_images=[back_page])
    return out_pdf_path
