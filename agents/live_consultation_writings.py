"""
Verified writings for a live consultation (rule 84).

A generative model may never produce an authoritative Bahá'í quotation from
memory. Anything shown or spoken as a QUOTATION comes out of the local verified
corpus through the Librarian's index, or it does not exist — the same class of
guarantee as `_sanitize_claims` (rule 4) and the code-appended disclosures
(rule 8), and the same reasoning as rule 11's tiers: what is printed must be a
real span of an indexed text.

What this module deliberately does NOT do:
  * It does not touch the product pipelines' scripture rules. The bookmark and
    quote-card paths keep their own restrictions (rules 6, 11) untouched; this
    one reads the broader 7-text library because a live consultation is not a
    product.
  * It does not ask a model to choose the words. A model supplies a THEME; the
    index supplies the text.
  * It never falls back to "close enough". An empty result is reported as an
    empty result, and the assistant says it found nothing.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Optional

MAX_RESULTS = 3

# Passages longer than this are shown in full on screen but are never a good
# spoken intervention; the caller decides, this is only a hint.
LONG_PASSAGE_CHARS = 600

# `Consultation: A Compilation` (bahai.org), ingested by
# `scripts/ingest_consultation.py` into a collection of its OWN.
#
# Not in `bahai_texts` on purpose: that index is what a quote card may print
# from via `lib:<slug>` (rule 11), and widening the printable sources as a side
# effect of improving the consultation tab is exactly the silent widening that
# rule forbids. Live Consultation is allowed the broader library because it is
# not making a product (rule 84); the product pipelines cannot see this
# collection at all.
#
# It is searched FIRST because it is the on-topic corpus: 46 passages that are
# specifically about consultation, against 6,342 general chunks. A general
# search for "detachment from one's own opinion" will find Gleanings before it
# finds the compilation, which is not what a room consulting together needs.
COMPILATION_COLLECTION = "consultation_compilation"


def _hits(collection: str, theme: str, n_results: int) -> list[dict]:
    from agents.librarian import retrieve
    return retrieve(theme, n_results=n_results, collection_name=collection) or []


def _normalize(text: str) -> str:
    """Compare on shape, not on typography: curly and straight apostrophes,
    NBSPs and line wrapping all differ between a chunk and anything that has
    been through a model or a UI."""
    text = unicodedata.normalize("NFKC", text or "")
    text = text.replace("’", "'").replace("‘", "'")
    text = text.replace("“", '"').replace("”", '"')
    text = text.replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", text).strip().lower()


def search(theme: str, n_results: int = MAX_RESULTS,
           framework: str = "bahai") -> dict:
    """
    Find passages on a theme in the verified corpus.

    Searches the consultation compilation first, then the general 7-text index,
    and returns whatever it actually found — never a paraphrase, never a
    "close enough" match.

    `framework` is not cosmetic. Much of the compilation is addressed to
    elected Bahá'í institutions, and handing an Assembly's guidance to a school
    board or a family as though it applied to them would be exactly the
    over-generalisation this feature must not commit. In a `general`
    consultation those passages are left out; in a Bahá'í one they are kept and
    labelled.

    Returns {"available", "passages", "note"}. Never raises: a live meeting must
    not fall over because the index is missing or Ollama is not running — it is
    told plainly that no verified passage is available.
    """
    theme = (theme or "").strip()
    if not theme:
        return {"available": False, "passages": [], "note": "No theme was given to look up."}
    want = max(1, min(int(n_results or 1), MAX_RESULTS))
    hits: list[dict] = []
    reached = False
    try:
        hits = _hits(COMPILATION_COLLECTION, theme, want)
        reached = True
    except Exception:
        # A missing compilation collection is not a failure: this installation
        # may simply never have run the ingest. Fall through to the general
        # index rather than reporting the whole library as unreachable.
        hits = []
    for hit in hits:
        hit["_from_compilation"] = True

    if len(hits) < want:
        try:
            general = _hits("bahai_texts", theme, want - len(hits))
            reached = True
            hits.extend(general)
        except Exception as e:
            if not reached:
                return {"available": False, "passages": [],
                        "note": f"The verified writings index could not be reached "
                                f"({type(e).__name__}). No quotation is shown rather "
                                "than an unverified one."}

    passages = []
    skipped_institutional = 0
    for hit in hits:
        text = (hit.get("text") or "").strip()
        if not text:
            continue
        institutional = bool(hit.get("institutional"))
        if institutional and framework != "bahai":
            # Guidance written for a Spiritual Assembly is not guidance for
            # every gathering. Dropped rather than shown with a caveat, because
            # a caveat under a passage is not read.
            skipped_institutional += 1
            continue
        passages.append({
            "text": text,
            "source": hit.get("source", ""),
            "section": hit.get("section", ""),
            "link": hit.get("link", ""),
            "score": hit.get("score", 0.0),
            "verified": True,
            "long": len(text) > LONG_PASSAGE_CHARS,
            "institutional": institutional,
            "from_compilation": bool(hit.get("_from_compilation")),
        })
    if not passages:
        note = "No verified passage in the library matched that closely enough."
        if skipped_institutional:
            note = ("The passages that matched are addressed to Bahá'í institutions, "
                    "so they were not shown in a general consultation.")
        return {"available": True, "passages": [], "note": note}
    return {"available": True, "passages": passages, "note": ""}


def verify_quotation(text: str, theme: str = "") -> dict:
    """
    Is this exact text a real span of an indexed passage?

    Used on anything that arrived as a quotation from a model rather than from
    `search()`. A near miss is a FAILURE, not a correction: paraphrase that
    reads as scripture is the specific harm this exists to prevent.
    """
    candidate = _normalize(text)
    if not candidate:
        return {"verified": False, "reason": "empty"}
    if len(candidate) < 20:
        return {"verified": False, "reason": "too short to verify"}
    try:
        from agents.librarian import retrieve
        hits = retrieve(theme or text, n_results=MAX_RESULTS)
    except Exception as e:
        return {"verified": False, "reason": f"index unreachable ({type(e).__name__})"}
    for hit in hits:
        if candidate in _normalize(hit.get("text", "")):
            return {
                "verified": True,
                "source": hit.get("source", ""),
                "section": hit.get("section", ""),
                "link": hit.get("link", ""),
            }
    return {"verified": False,
            "reason": "not found verbatim in the verified library"}


def available() -> bool:
    """Whether the verified library can be searched at all. Cheap and
    non-fatal — used by the capabilities endpoint so the UI can say that the
    writings lookup is unavailable instead of silently offering nothing."""
    try:
        from agents.librarian import _get_collection
        return _get_collection("bahai_texts") is not None
    except Exception:
        return False


def spoken_line(passage: Optional[dict]) -> str:
    """
    What the assistant is allowed to SAY about a verified passage.

    It does not read it aloud. A realtime voice model handed a quotation to
    recite is a generative model reconstructing scripture, and a spoken
    paraphrase that sounds like a quotation is exactly the thing that must not
    happen. The verified text goes on screen; the voice points at it.
    """
    if not passage:
        return "I could not find a verified passage on that, so I would rather not offer one."
    source = (passage.get("source") or "").strip()
    where = f" from {source}" if source else ""
    return (f"I found a relevant passage{where} and put the verified text on screen.")
