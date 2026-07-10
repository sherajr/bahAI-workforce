"""
Verify the quote-card corpus (agents/ruhi_book1_source.py) character-exact
against the official Ruhi Book 1 PDF, and freeze a SHA256 manifest of the
verified quotes (agents/ruhi_book1_manifest.json).

Why this exists: quote cards may only ever print verbatim Ruhi Book 1 text
(hard rule 11), and the pipeline already snaps every printed quote to a
corpus entry — but the corpus itself was hand-transcribed, and a live check
against the real PDF (2026-07-10) found 8 of 67 entries diverging (mostly
extraction artifacts, but two real silent splices, since fixed). This script
makes that verification repeatable, and api._assert_ruhi_verbatim() checks
every about-to-print quote against the frozen manifest at render time.

Usage:
    python scripts/verify_ruhi_book1.py --pdf "<path to official PDF>"
    python scripts/verify_ruhi_book1.py --pdf "<path>" --write-manifest

Check mode (no --write-manifest) verifies every corpus entry against the PDF
AND cross-checks the existing manifest for drift; exit code 1 on any failure.
--write-manifest rewrites the manifest, only if all entries verify.

Matching rule: a corpus entry may elide middle sentences with ". . ." (the
book's own convention); each ellipsis-separated segment must appear verbatim
in the book, in order. Normalization only unifies representation (unicode
form, quote/dash glyphs, ligatures, whitespace, the book's per-paragraph
re-opened quotation marks, running page headers) — never words.

The PDF itself is copyrighted Ruhi Institute material: keep it OUT of the
repo (point --pdf at it wherever it lives, e.g. Downloads).

Console output is ASCII-only (Windows console is cp1252 — see AGENTS.md).
"""

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from agents.ruhi_book1_source import RUHI_BOOK1_QUOTES  # noqa: E402

MANIFEST_PATH = REPO_ROOT / "agents" / "ruhi_book1_manifest.json"
EDITION = "4.1.2.PE (May 2020)"

# Running page headers/footers that interrupt quotes crossing a page break.
_PAGE_MARKS = re.compile(
    r"(\d+\s*[-–—]\s*Reflections on the Life of the Spirit"
    r"|(?:Understanding the Bahá'í Writings|Prayer|Life and Death)"
    r"\s*[-–—]\s*\d+)"
)


def _normalize(s: str, is_book: bool = False) -> str:
    """Unify representation without ever changing words."""
    s = unicodedata.normalize("NFC", s)
    s = (s.replace("“", '"').replace("”", '"')      # curly double quotes
         .replace("‘", "'").replace("’", "'")      # curly single quotes
         .replace("ﬁ", "fi").replace("ﬂ", "fl")    # fi/fl ligatures
         .replace("…", "...")                            # ellipsis char
         .replace("–", "-").replace("—", "-"))     # en/em dashes
    s = re.sub(r"\.\s*\.\s*\.", "...", s)                     # ". . ." -> "..."
    if is_book:
        s = _PAGE_MARKS.sub(" ", s)
    # The book re-opens quotation marks at each new paragraph inside one
    # quotation; drop the marks on both sides so segments compare equal.
    s = s.replace('"', " ")
    return re.sub(r"\s+", " ", s).strip()


def _segments(normalized_quote: str) -> list[str]:
    """Split an elided quote into its verbatim segments."""
    return [seg.strip(" .") for seg in normalized_quote.split("...") if seg.strip(" .")]


def verify_against_book(book_text: str) -> list[tuple[int, str, str]]:
    """Returns [(index, 'OK'|'FAIL', detail), ...] for every corpus entry."""
    results = []
    for i, entry in enumerate(RUHI_BOOK1_QUOTES):
        segs = _segments(_normalize(entry["text"]))
        pos, ok, missing = 0, True, ""
        for seg in segs:
            found = book_text.find(seg, pos)
            if found < 0:
                # Segment order should hold, but report a segment that exists
                # earlier in the book differently from one absent entirely.
                anywhere = book_text.find(seg)
                ok = False
                missing = ("segment out of order: " if anywhere >= 0
                           else "segment not in book: ") + seg[:60]
                break
            pos = found + len(seg)
        results.append((i, "OK" if ok else "FAIL", missing))
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description="Verify Ruhi Book 1 corpus against the official PDF.")
    ap.add_argument("--pdf", required=True, help="Path to the official Ruhi Book 1 PDF")
    ap.add_argument("--write-manifest", action="store_true",
                    help="Freeze agents/ruhi_book1_manifest.json (only if all entries verify)")
    args = ap.parse_args()

    from pypdf import PdfReader  # imported here so --help works without pypdf
    reader = PdfReader(args.pdf)
    book = _normalize("\n".join(page.extract_text() or "" for page in reader.pages),
                      is_book=True)

    results = verify_against_book(book)
    failures = [r for r in results if r[1] != "OK"]
    for i, status, detail in results:
        if status != "OK":
            head = RUHI_BOOK1_QUOTES[i]["text"][:60]
            print(f"[{i:2d}] FAIL: {head!r}".encode("ascii", "replace").decode())
            print(f"      {detail}".encode("ascii", "replace").decode())
    print(f"{len(results)} corpus entries checked against the PDF: "
          f"{len(results) - len(failures)} OK, {len(failures)} FAIL")

    if failures:
        print("NOT verified -- fix the corpus (or the PDF path) before writing a manifest.")
        return 1

    manifest = {
        "edition": EDITION,
        "verified_count": len(RUHI_BOOK1_QUOTES),
        "quotes": [
            {"sha256": hashlib.sha256(e["text"].encode("utf-8")).hexdigest(),
             "section": e["section"]}
            for e in RUHI_BOOK1_QUOTES
        ],
    }

    if args.write_manifest:
        MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=True, indent=2) + "\n",
                                 encoding="utf-8")
        print(f"Manifest written: {MANIFEST_PATH} ({len(manifest['quotes'])} hashes)")
        return 0

    # Check mode: cross-check the frozen manifest for drift.
    if not MANIFEST_PATH.exists():
        print("No manifest on disk yet -- run again with --write-manifest to freeze one.")
        return 1
    frozen = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    drift = []
    if len(frozen.get("quotes", [])) != len(manifest["quotes"]):
        drift.append(f"count: manifest {len(frozen.get('quotes', []))} vs corpus {len(manifest['quotes'])}")
    else:
        for i, (a, b) in enumerate(zip(frozen["quotes"], manifest["quotes"])):
            if a.get("sha256") != b["sha256"]:
                drift.append(f"entry {i} hash differs (corpus edited since the manifest was frozen)")
    if drift:
        for d in drift:
            print(f"MANIFEST DRIFT: {d}")
        print("Re-run with --write-manifest after confirming the corpus edits are intentional.")
        return 1
    print("Manifest cross-check: OK (corpus unchanged since it was frozen).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
