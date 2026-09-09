"""
Export the Bahá'í writings corpus as portable files that can be ATTACHED to an
AI chat (Claude, ChatGPT, NotebookLM, a Project/knowledge base).

Deliberately exports the TEXT, not the vectors. `vector_store/` holds 768-dim
nomic-embed-text embeddings, which are meaningless to any other model: an
embedding is only readable by the same embedding model plus a retrieval layer.
What travels between systems is the passage text and its citation.

Sources: texts/*.json (7 works, ingested from reference.bahai.org) and
agents/ruhi_book1_source.py (the 67 hand-transcribed Ruhi Book 1 quotes).

Writes to outputs/scripture/:
  bahai-writings-complete.md   every work in one file
  by-book/<slug>.md            one file per work (each fits a normal context)
  ruhi-book1-quotes.md         the curated Ruhi Book 1 pool
  bahai-writings.jsonl         one passage per line, for rebuilding an index

Run: python scripts/export_scripture.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEXTS = ROOT / "texts"
OUT = ROOT / "outputs" / "scripture"

PROVENANCE = (
    "These are the authorized English translations as published on the Bahá'í "
    "Reference Library (reference.bahai.org). Each passage keeps its section "
    "heading and a link back to the source page, so anything quoted from this "
    "file can be cited and checked. Passages were exported verbatim; only the "
    "source pages' hard line-wrapping was removed so each passage reads as one "
    "paragraph."
)


def unwrap(text: str) -> str:
    """Undo the source pages' hard line wrapping; keep the words untouched."""
    text = re.sub(r"\s*\n\s*", " ", text)
    return re.sub(r" {2,}", " ", text).strip()


def load_books() -> list[dict]:
    books = []
    for path in sorted(TEXTS.glob("*.json")):
        book = json.loads(path.read_text(encoding="utf-8"))
        for p in book["passages"]:
            p["text"] = unwrap(p["text"])
        books.append(book)
    return books


def book_markdown(book: dict, heading: str = "#") -> str:
    lines = [
        f"{heading} {book['title']}",
        "",
        f"**Author:** {book['author']}  ",
        f"**Source:** {book['source_url']}  ",
        f"**Passages:** {len(book['passages'])}",
        "",
    ]
    last_section = None
    for i, p in enumerate(book["passages"], 1):
        section = p.get("section") or ""
        if section and section != last_section:
            lines += [f"{heading}## {section}", ""]
            last_section = section
        lines += [p["text"], ""]
        link = p.get("link")
        if link:
            lines += [f"<sub>{book['title']} — {section or f'passage {i}'} — {link}</sub>", ""]
    return "\n".join(lines)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "by-book").mkdir(exist_ok=True)
    books = load_books()

    # 1. Everything in one file.
    whole = [
        "# The Bahá'í Writings — reference corpus",
        "",
        PROVENANCE,
        "",
        "## Contents",
        "",
    ]
    for b in books:
        whole.append(f"- {b['title']} — {b['author']} ({len(b['passages'])} passages)")
    whole.append("")
    for b in books:
        whole += ["---", "", book_markdown(b, heading="#"), ""]
    complete = OUT / "bahai-writings-complete.md"
    complete.write_text("\n".join(whole), encoding="utf-8")

    # 2. One file per work.
    for b in books:
        text = "\n".join([book_markdown(b, heading="#"), "", "---", "", PROVENANCE, ""])
        (OUT / "by-book" / f"{b['slug']}.md").write_text(text, encoding="utf-8")

    # 3. The curated Ruhi Book 1 pool.
    sys.path.insert(0, str(ROOT))
    from agents.ruhi_book1_source import RUHI_BOOK1_QUOTES

    ruhi = [
        "# Ruhi Book 1 — quotable passages",
        "",
        "Every passage block-quoted for reading and reflection in Ruhi Institute "
        "Book 1, *Reflections on the Life of the Spirit* (edition 4.1.2.PE, May "
        "2020), transcribed verbatim and cross-checked against the book's own "
        "References list. The locator line names where the book cites it from.",
        "",
    ]
    for q in RUHI_BOOK1_QUOTES:
        ruhi += [f"> {q['text']}", "", f"— **{q['source']}**  ", f"<sub>{q['section']}</sub>", ""]
    (OUT / "ruhi-book1-quotes.md").write_text("\n".join(ruhi), encoding="utf-8")

    # 4. Machine-readable, for rebuilding an index elsewhere.
    with (OUT / "bahai-writings.jsonl").open("w", encoding="utf-8") as fh:
        for b in books:
            for i, p in enumerate(b["passages"], 1):
                fh.write(json.dumps({
                    "id": f"{b['slug']}-{i}",
                    "text": p["text"],
                    "work": b["title"],
                    "author": b["author"],
                    "section": p.get("section", ""),
                    "link": p.get("link", ""),
                }, ensure_ascii=False) + "\n")

    total = sum(len(b["passages"]) for b in books)
    words = sum(len(p["text"].split()) for b in books for p in b["passages"])
    print(f"-> {OUT}")
    print(f"   {total} passages, {words:,} words, {len(RUHI_BOOK1_QUOTES)} Ruhi quotes")
    for f in sorted(OUT.rglob("*")):
        if f.is_file():
            print(f"   {f.relative_to(OUT).as_posix():34} {f.stat().st_size/1_048_576:6.2f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
