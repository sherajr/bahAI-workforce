"""
The gathering programme, as a page somebody can print and hold (rule 119).

A separate PDF from the card print sheet, and separate ON PURPOSE. The card
sheet is a duplex grid whose page 2 must line up with page 1 through a printer's
long-edge flip (`print_sheet.build_print_sheet`); adding a programme page to
that file shifts every back face by one page and every card prints on the wrong
side. So there are two downloads and the UI says which is which.

Built with PIL at the same 300 dpi as the print sheet, rather than by adding a
PDF library to `requirements.txt` for one page. It is a programme, not
typesetting.

WHAT IT MAY PRINT
-----------------
A verified passage reaches this page by ID — it is fetched from the session's
`writings` rows, which came out of the verified corpus (rule 84), and printed
verbatim with its source. The programme never carries scripture text of its
own, so there is nothing here that could be edited into something the library
does not contain, and nothing a model wrote can be printed as a quotation.

A reading is never TRUNCATED to make the layout work either: it flows onto
another page. Quietly shortening a passage to fit a box is the same failure as
paraphrasing one.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

DPI = 300
# Sizes here are POINTS, converted at render time. Getting this wrong is silent
# and total: a "28" that means 28 pixels at 300 dpi is 6.7 point on paper --
# text nobody can read, on a page that looks fine on screen because the preview
# is scaled. `pt()` exists so a size in this file always means what it says.
PT = DPI / 72.0


def pt(points: float) -> int:
    return max(1, round(points * PT))


PAGE_W_IN, PAGE_H_IN = 8.5, 11.0
PAGE_W, PAGE_H = round(PAGE_W_IN * DPI), round(PAGE_H_IN * DPI)
MARGIN = round(0.75 * DPI)

OUTPUTS_DIR = Path(__file__).parent.parent / "outputs"

INK = (28, 28, 30)
SOFT = (110, 110, 118)
RULE = (200, 196, 186)
GOLD = (176, 141, 62)
PAPER = (255, 253, 249)

_FONT_STACK = [
    "C:/Windows/Fonts/tahoma.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]
_BOLD_STACK = [
    "C:/Windows/Fonts/tahomabd.ttf",
    "C:/Windows/Fonts/segoeuib.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    for path in (_BOLD_STACK if bold else _FONT_STACK):
        try:
            return ImageFont.truetype(path, size)
        except (IOError, OSError):
            continue
    return ImageFont.load_default(size=size)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont,
          width: int) -> list[str]:
    """Word wrap. A word longer than the line is left to overflow rather than
    being cut: a broken word is a corrupted word, and this page may carry
    names."""
    lines: list[str] = []
    for paragraph in (text or "").split("\n"):
        if not paragraph.strip():
            lines.append("")
            continue
        words, current = paragraph.split(), ""
        for word in words:
            trial = f"{current} {word}".strip()
            if draw.textlength(trial, font=font) <= width or not current:
                current = trial
            else:
                lines.append(current)
                current = word
        if current:
            lines.append(current)
    return lines


class _Sheet:
    """Pages that grow as they are written to, so nothing has to be trimmed."""

    def __init__(self) -> None:
        self.pages: list[Image.Image] = []
        self._new_page()

    def _new_page(self) -> None:
        page = Image.new("RGB", (PAGE_W, PAGE_H), PAPER)
        self.pages.append(page)
        self.draw = ImageDraw.Draw(page)
        self.y = MARGIN

    def space(self, needed: int) -> None:
        if self.y + needed > PAGE_H - MARGIN:
            self._new_page()

    def text(self, body: str, font: ImageFont.FreeTypeFont, fill=INK,
             indent: int = 0, leading: float = 1.45, gap_after: int = 0) -> None:
        width = PAGE_W - 2 * MARGIN - indent
        line_h = round(font.size * leading)
        for line in _wrap(self.draw, body, font, width):
            self.space(line_h)
            self.draw.text((MARGIN + indent, self.y), line, font=font, fill=fill)
            self.y += line_h
        self.y += gap_after

    def rule(self, colour=RULE, gap_before: int = 12, gap_after: int = 18) -> None:
        self.y += gap_before
        self.space(4)
        self.draw.line([(MARGIN, self.y), (PAGE_W - MARGIN, self.y)], fill=colour, width=2)
        self.y += gap_after


def build_program_sheet(project: dict, items: list[dict],
                        writings: Optional[dict] = None,
                        out_pdf_path: str | None = None) -> dict:
    """
    Render the programme. Returns {path, pages, warnings}.

    `writings` maps a writing id to its stored row, so a `reading` item prints
    the verified passage and its source. An item whose passage cannot be found
    is REPORTED and printed as a placeholder rather than silently dropped —
    a programme that quietly loses a reading is worse than one that says a
    reading is missing.
    """
    writings = writings or {}
    warnings: list[str] = []

    title_f = _font(pt(24), bold=True)
    sub_f = _font(pt(11))
    item_f = _font(pt(13), bold=True)
    body_f = _font(pt(11.5))
    quote_f = _font(pt(12.5))
    small_f = _font(pt(9.5))

    sheet = _Sheet()
    sheet.text(str(project.get("title") or "Gathering"), title_f, gap_after=8)

    meta = []
    if project.get("gathering_at"):
        meta.append(str(project["gathering_at"]))
    if project.get("timezone"):
        meta.append(str(project["timezone"]))
    total = sum(int(i.get("minutes") or 0) for i in items)
    if total:
        meta.append(f"about {total} minutes")
    if meta:
        sheet.text(" · ".join(meta), sub_f, fill=SOFT, gap_after=6)
    if (project.get("purpose") or "").strip():
        sheet.text(str(project["purpose"]).strip(), sub_f, fill=SOFT, gap_after=4)
    sheet.rule(GOLD)

    if not items:
        sheet.text("This programme has no activities in it yet.", body_f, fill=SOFT)
        warnings.append("The programme is empty.")

    for n, item in enumerate(items, start=1):
        heading = str(item.get("title") or "").strip()
        minutes = item.get("minutes")
        label = f"{n}. {heading}" if heading else f"{n}."
        if minutes:
            label += f"   ({int(minutes)} min)"
        sheet.space(round(item_f.size * 2.2))
        sheet.text(label, item_f, gap_after=6)

        if item.get("kind") == "reading":
            wid = str(item.get("writing_id") or "")
            passage = writings.get(wid)
            if passage:
                # Verbatim, with its source, and never shortened to fit. If it
                # runs past the bottom of the page it continues on the next one.
                sheet.text(f"\u201c{passage.get('text', '').strip()}\u201d",
                           quote_f, indent=round(0.35 * DPI), gap_after=6)
                source = " — ".join(x for x in [passage.get("source"),
                                                passage.get("section")] if x)
                if source:
                    sheet.text(source, small_f, fill=SOFT,
                               indent=round(0.35 * DPI), gap_after=6)
            else:
                sheet.text("[A verified passage was chosen for this reading but could "
                           "not be found. Nothing has been substituted for it.]",
                           body_f, fill=SOFT, indent=round(0.35 * DPI), gap_after=6)
                warnings.append(
                    f"Item {n} ({heading or 'reading'}) points at a passage that is no "
                    "longer in the record, so it printed as a placeholder.")

        if (item.get("body") or "").strip():
            sheet.text(str(item["body"]).strip(), body_f,
                       indent=round(0.35 * DPI), gap_after=8)
        sheet.rule(RULE, gap_before=6, gap_after=14)

    if out_pdf_path is None:
        OUTPUTS_DIR.mkdir(exist_ok=True)
        out_pdf_path = str(OUTPUTS_DIR / f"program-{uuid.uuid4().hex[:8]}.pdf")
    Path(out_pdf_path).parent.mkdir(parents=True, exist_ok=True)
    sheet.pages[0].save(out_pdf_path, "PDF", resolution=float(DPI),
                        save_all=True, append_images=sheet.pages[1:])
    return {"path": out_pdf_path, "pages": len(sheet.pages), "warnings": warnings}
