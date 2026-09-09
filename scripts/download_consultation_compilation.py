"""
Download `Consultation: A Compilation` from the Bahá'í Reference Library.

Saves `texts/consultation-compilation.json` in the same passage-level shape as
the seven core texts (`scripts/download_texts.py`), so `ingest_consultation.py`
can chunk and embed it exactly the way `ingest_texts.py` does.

Run once — it skips the file if it already exists.

WHY THIS IS A SEPARATE FILE FROM `download_texts.py`
The seven core works are single-author books: one title, one author, and a
section heading per passage. A compilation is not. Every passage in this one
carries its OWN provenance — a different Tablet, a different letter, a
different year — printed in the paragraph immediately after it. Folding it into
the seven-text downloader would have meant either losing that per-passage
citation or bending that script out of shape. Rule 84 turns on a passage being
citable to what it actually came from, so the citation is the thing this file
exists to preserve.

WHY IT GOES IN ITS OWN COLLECTION, NOT `bahai_texts`
`bahai_texts` is what the quote-card pipeline draws from via `lib:<slug>`
(rule 11). Adding a work to it silently widens what can be PRINTED ON A
PRODUCT, and rule 11 says never widen silently. This corpus is for the live
consultation only, so it lives in `consultation_compilation` and the product
pipelines cannot see it at all.

Source: https://www.bahai.org/library/authoritative-texts/compilations/consultation/
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "texts" / "consultation-compilation.json"

URL = ("https://www.bahai.org/library/authoritative-texts/compilations/"
       "consultation/consultation.xhtml")
PAGE = ("https://www.bahai.org/library/authoritative-texts/compilations/"
        "consultation/")
HEADERS = {"User-Agent": "bahAI-Workforce/1.0 (personal spiritual research tool)"}

TITLE = "Consultation: A Compilation"
SLUG = "consultation-compilation"

# The markup, as surveyed on 2026-09-03:
#   p.c.lb   a section heading naming whose writings follow
#   p / p.zd the passage itself
#   p.mb     the citation for the passage immediately above it
HEADING_CLASS = {"c", "lb"}
CITATION_CLASS = "mb"

# Passages addressed specifically to elected Bahá'í institutions. Flagged, not
# dropped: the compilation genuinely contains guidance for Assemblies, and it
# would be wrong both to hide it from a Bahá'í consultation and to hand it to a
# school board as though it applied to them (the prompt's own warning about
# universalising institution-specific direction).
#
# This is a HEURISTIC and is labelled as one everywhere it surfaces. It errs
# toward marking a passage institutional, because showing an Assembly passage
# to a general meeting is the worse mistake.
_INSTITUTIONAL = re.compile(
    r"\b(spiritual assembl\w+|local assembl\w+|national assembl\w+|"
    r"house of justice|assembly|assemblies|institution\w*|"
    r"board member\w*|counsell?or\w*)\b",
    re.IGNORECASE,
)


def _clean(text: str) -> str:
    """Collapse the page's wrapping; never touch the words themselves."""
    text = text.replace(" ", " ")
    text = re.sub(r"\s*\n\s*", " ", text)
    return re.sub(r" {2,}", " ", text).strip()


def _strip_footnote(text: str) -> str:
    """Drop a trailing footnote marker.

    The page prints these two ways: bracketed on citations (`[7]`) and as a
    bare trailing digit on some headings and passages, where the `<sup>` has
    been flattened by `get_text()`. Both are page furniture, not words of the
    text, and leaving them in would put a stray "4" inside a citation a person
    is meant to be able to check.
    """
    text = re.sub(r"\s*\[\d+\]\s*$", "", text).strip()
    # The bare-digit form is stripped ONLY after sentence-ending punctuation.
    # This is scripture: a rule loose enough to also take the "2" off a passage
    # that genuinely ended in a numeral would be silently altering the text,
    # which is the exact class of error rule 84 exists to prevent.
    return re.sub(r"(?<=[.!?”’\"'…])\s+\d{1,2}$", "", text).strip()


def _is_heading(tag) -> bool:
    return HEADING_CLASS.issubset(set(tag.get("class") or []))


def _is_citation(tag) -> bool:
    return CITATION_CLASS in (tag.get("class") or [])


def fetch() -> str:
    resp = requests.get(URL, headers=HEADERS, timeout=45)
    resp.raise_for_status()
    # The page is served as UTF-8; be explicit so the diacritics survive.
    resp.encoding = resp.encoding or "utf-8"
    return resp.text


def parse(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    passages: list[dict] = []
    section = ""
    pending: str | None = None

    for tag in soup.find_all("p"):
        text = _clean(tag.get_text(" ", strip=True))
        if not text:
            continue
        if _is_heading(tag):
            # A heading is page furniture, so a trailing marker there can be
            # taken off unconditionally -- unlike a passage.
            section = re.sub(r"\s+\d{1,2}$", "", text).strip()
            pending = None
            continue
        if _is_citation(tag):
            # A citation with no passage before it is front matter, not a source.
            if pending:
                citation = _strip_footnote(text).strip("()").strip()
                passages.append({
                    "text": pending,
                    # `section` is whose writings these are; `citation` is the
                    # actual work. Both are needed: neither alone is a citation
                    # a person could check.
                    "section": section,
                    "citation": citation,
                    "link": PAGE,
                    "institutional": bool(_INSTITUTIONAL.search(pending)),
                })
                pending = None
            continue
        # Anything else that is long enough to be a passage rather than a
        # date line or an editorial note.
        if len(text) >= 60:
            pending = _strip_footnote(text)

    return passages


def main() -> int:
    if OUTPUT.exists():
        print(f"{OUTPUT.name} already exists - nothing to do.")
        return 0
    print(f"Fetching {URL} ...")
    try:
        html = fetch()
    except Exception as e:
        print(f"ERROR: could not fetch the compilation ({type(e).__name__}: {e})")
        return 1

    passages = parse(html)
    if len(passages) < 30:
        # A structural change on bahai.org would show up as a near-empty parse,
        # and writing that out would quietly shrink the verified corpus.
        print(f"ERROR: only {len(passages)} passages parsed - the page structure has "
              "probably changed. Nothing was written; check the markup before "
              "trusting this.")
        return 1

    institutional = sum(1 for p in passages if p["institutional"])
    payload = {
        "title": TITLE,
        "author": "Bahá'u'lláh, 'Abdu'l-Bahá, Shoghi Effendi and the Universal House of Justice",
        "slug": SLUG,
        "source_url": PAGE,
        "note": ("Prepared by the Research Department of the Universal House of "
                 "Justice. Each passage keeps the citation printed beneath it on "
                 "the source page, so anything quoted can be checked."),
        "passages": passages,
    }
    OUTPUT.parent.mkdir(exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {OUTPUT} - {len(passages)} passages "
          f"({institutional} flagged as addressed to institutions).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
