"""
The quote-card pipeline's HTTP routes (rules 1-14 by extension, 11, 29),
extracted from api.py: the write/consult/render/score/revise cycle, the full
theme-to-card run and hands-free batch, quote-source discovery, the
Librarian's quote finder, and targeted regeneration
(quote/image/everything) for an existing card.

`RegenerateAllRequest` is imported from agents.pipeline_api rather than
redefined here -- `_redo_card`'s guidance-only request shape is identical to
the bookmark's, and the original api.py already reused one class for both.

`_run_card_pipeline` and `launch_team_pipeline` (agents/colony_api.py) are
the two entry points that start a card run; nothing outside this file
should call the write/consult/render helpers directly.

No prefix on this router -- see wallet_api.py's docstring for why.
"""

import hashlib
import json
import re
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agents.librarian import retrieve, retrieve_ruhi_book1
from agents.pipeline_api import RegenerateAllRequest
from agents.ruhi_book1_source import RUHI_BOOK1_QUOTES
from agents.state import create_product, log_run, update_product, update_task_status
from agents.jobs import (
    _badge, _load_product_or_404, _require_card, _start_job, _web_image_path,
    create_task,
)

router = APIRouter()


# --- Pipeline: Quote Cards (giveaway product line — parallel to bookmarks) ---
#
# A quote card is NOT sold: no listing, no Etsy, no pricing. The deliverable
# is a verified quote + welcoming artwork + optional AI-labeled translation,
# rendered as a 3.5"x2" front/back PNG pair. See docs/fable5-briefing-quote-cards.md.

class CardPipelineRequest(BaseModel):
    theme: str
    language: Optional[str] = None   # LANGUAGES code ("es"/"zh"/"ar") or None for English-only
    target_score: float = 9.0
    max_attempts: int = 3
    # Redo-everything steer (regenerate-card-all only; empty for a normal new
    # card run). Folded into the retrieval query and image brief ONLY — the
    # stored theme/title stay the clean original so repeated redos don't
    # accumulate "NEW DIRECTION" text into permanent storage.
    guidance: str = ""
    # Owner-supplied exact quote. Verified against the selected sources
    # before any image generation or paid call; locks the printed quote for
    # the whole run (revision loop cannot swap it).
    pinned_quote: str = ""
    # Owner-selected quote sources (rule 11 update, 2026-08-04). None/empty =
    # Ruhi Book 1 only — the unchanged default. Ids: "ruhi_book1",
    # "lib:<slug>" (verified library text), "web:<http(s) url>" (RISKY:
    # prints machine-unverified wording, flagged on the product).
    sources: Optional[list[str]] = None
    # Citation label used ONLY when the pinned quote resolves to the risky
    # web tier — verified local tiers always print corpus metadata and can
    # never be overridden by this field.
    pinned_citation: str = ""


# Honesty disclosure for the card's ARTWORK (principle 3) — a fixed string
# stored in the card's metadata and shown on the dashboard, same discipline as
# the translation disclaimers. Whether it also gets printed on the card back
# is a design decision for Sheraj (hard rule 9: any card-face change ships
# only after a human-viewed render), so it is metadata-only for now.
CARD_ART_DISCLOSURE = (
    "Artwork created with AI image-generation tools, art-directed and curated by Sheraj. "
    "The quote is a verbatim excerpt from Ruhi Institute Book 1."
)


@router.get("/card/languages")
def card_languages():
    """Translation languages the card pipeline offers (config in translator.py)."""
    from agents.translator import LANGUAGES
    return [
        {"code": code, "name": cfg["name"], "native_name": cfg["native_name"]}
        for code, cfg in LANGUAGES.items()
    ]


_SENTENCE_END_RE = re.compile(r'[.!?](?=\s|$)')


# Elision suffix forms accepted on shortened card quotes. The book's own
# style (" . . .") is what we emit; "..." and "…" are tolerated on input.
_ELISION_SUFFIXES = (" . . .", "...", "\u2026")


def _trim_card_quote(text: str, limit: int = 150) -> str:
    """
    Trim a passage to a card-appropriate excerpt at a SENTENCE boundary —
    always a complete sentence, never a mid-sentence hard cut. A card that
    trails off with no closing punctuation ("...and verities will come to")
    reads as a broken render, not a deliberate excerpt — worse than a longer
    but complete quote. Takes as many whole sentences as fit within `limit`;
    if even the first sentence alone exceeds it, takes that one sentence in
    full anyway and lets the Card Compositor's own auto-shrink (or its
    raise-if-it-still-doesn't-fit guard) handle the length, rather than
    truncating it into a broken fragment here.

    When (and only when) the return is shorter than the full stripped text,
    appends the book's own elision marks " . . ." so a shortened quote is
    visibly honest about the cut.
    """
    text = text.strip()
    if len(text) <= limit:
        return text
    ends = [m.end() for m in _SENTENCE_END_RE.finditer(text)]
    if not ends:
        return text  # no sentence punctuation at all — return whole passage rather than guess a cut
    cut = next((e for e in ends if e > limit), None)
    if cut is None:
        cut = ends[-1]
    else:
        fits = [e for e in ends if e <= limit]
        if fits:
            cut = fits[-1]
    trimmed = text[:cut].strip()
    if trimmed == text:
        return text
    return trimmed + " . . ."


# Cache for agents/ruhi_book1_manifest.json — the SHA256 manifest frozen by
# scripts/verify_ruhi_book1.py after every corpus entry was machine-verified
# character-exact against the official Ruhi Book 1 PDF (edition 4.1.2.PE).
_RUHI_MANIFEST_CACHE: Optional[dict] = None


def _load_ruhi_manifest() -> dict:
    global _RUHI_MANIFEST_CACHE
    if _RUHI_MANIFEST_CACHE is not None:
        return _RUHI_MANIFEST_CACHE
    path = Path(__file__).parent / "ruhi_book1_manifest.json"
    if not path.exists():
        raise RuntimeError(
            "The Ruhi Book 1 verification manifest is missing. Run "
            "python scripts/verify_ruhi_book1.py --pdf <official pdf> --write-manifest "
            "before rendering quote cards."
        )
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"The Ruhi Book 1 manifest could not be read: {exc}") from exc
    quotes = manifest.get("quotes")
    if not isinstance(quotes, list) or len(quotes) != len(RUHI_BOOK1_QUOTES):
        raise RuntimeError(
            "The Ruhi Book 1 manifest does not match the current corpus size. Re-run "
            "python scripts/verify_ruhi_book1.py --pdf <official pdf> --write-manifest."
        )
    _RUHI_MANIFEST_CACHE = manifest
    return manifest


def _assert_ruhi_verbatim(quote: str) -> None:
    """
    Fail loudly unless the about-to-print card quote is either:
      (a) an exact character match of a manifest-verified Ruhi Book 1 corpus
          entry's text.strip() (covers full entries, including those that
          natively end with ". . ."), or
      (b) a sentence-boundary prefix of such an entry, carrying honest
          elision marks (" . . .", also tolerating "..." or "…" as the
          suffix) — i.e. after stripping the suffix, the remainder equals
          text[:e].strip() for some sentence-end e from _SENTENCE_END_RE.
    Mid-sentence cuts and unmarked character prefixes are rejected. This
    catches stale ChromaDB text and unverified corpus edits BEFORE a card
    can render wrong words — same discipline as _sanitize_claims.
    """
    quote = (quote or "").strip()
    if not quote:
        raise RuntimeError("Empty card quote reached the verbatim gate — refusing to render.")
    manifest_quotes = _load_ruhi_manifest()["quotes"]

    # Detect a trailing elision suffix (book style first, then common variants).
    elided_body = None
    for suffix in _ELISION_SUFFIXES:
        if quote.endswith(suffix):
            elided_body = quote[: -len(suffix)].strip()
            break

    for idx, entry in enumerate(RUHI_BOOK1_QUOTES):
        text = str(entry["text"])
        text_stripped = text.strip()
        if manifest_quotes[idx].get("sha256") != hashlib.sha256(text.encode("utf-8")).hexdigest():
            continue
        # (a) exact full-entry match — must run before (b) so a corpus entry
        # that natively ends with ". . ." is accepted as-is, not re-parsed
        # as an elision of a shorter body.
        if quote == text_stripped:
            return
        # (b) sentence-boundary prefix + honest elision marks
        if elided_body:
            ends = [m.end() for m in _SENTENCE_END_RE.finditer(text_stripped)]
            for e in ends:
                if text_stripped[:e].strip() == elided_body:
                    return
    raise RuntimeError(
        "Card quote is not verbatim from the verified Ruhi Book 1 corpus, so the render "
        f"was stopped. Offending quote starts: {quote[:80]!r}. Shortened quotes must end "
        "at a sentence boundary and carry \" . . .\" elision marks. Likely causes: a mid-"
        "sentence cut, a missing elision mark, a stale ChromaDB index (re-run "
        "scripts/ingest_ruhi_book1.py), or an unverified corpus edit (re-run "
        "python scripts/verify_ruhi_book1.py --pdf <official pdf> --write-manifest)."
    )


def _quote_lenient_key(s: str) -> str:
    """
    Lenient key for matching an owner-supplied pinned quote against the
    corpus: lowercase; curly quotes/apostrophes → straight; "…" → ". . .";
    strip leading/trailing quotation marks; collapse whitespace runs.
    """
    s = (s or "")
    s = (s.replace("\u2018", "'").replace("\u2019", "'")
          .replace("\u201c", '"').replace("\u201d", '"')
          .replace("\u2026", ". . ."))
    s = s.strip().lower().strip("\"'")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _resolve_pinned_quote(supplied: str) -> dict:
    """
    Resolve an owner-supplied exact quote against the Ruhi Book 1 corpus.

    Returns {"quote": <exact text to print>, "source": <entry source>,
    "sha256": <hash of the full entry text>}. The resolved quote is always
    the corpus entry's exact characters (or an exact sentence-boundary
    prefix plus " . . ."). Raises RuntimeError before any paid work if the
    supply cannot be verified.
    """
    supplied = (supplied or "").strip()
    err = (
        "The supplied quote could not be verified against the Ruhi Book 1 corpus "
        "(67 verified passages). Card quotes must match an authorized passage exactly "
        "(ellipses allowed only to shorten at a sentence boundary). Nothing was generated."
    )
    if not supplied:
        raise RuntimeError(err)

    full_key = _quote_lenient_key(supplied)

    # Elision may be present on a shortened supply — strip from the body used
    # for prefix matching, but keep full_key (above) for whole-entry match so
    # corpus entries that natively end with ". . ." still resolve via (i).
    body = supplied
    for suffix in _ELISION_SUFFIXES:
        if body.endswith(suffix):
            body = body[: -len(suffix)].strip()
            break
    body_key = _quote_lenient_key(body)

    manifest_quotes = _load_ruhi_manifest()["quotes"]
    for idx, entry in enumerate(RUHI_BOOK1_QUOTES):
        text = str(entry["text"])
        text_stripped = text.strip()
        sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if manifest_quotes[idx].get("sha256") != sha:
            continue
        source = str(entry.get("source") or "").strip()
        entry_key = _quote_lenient_key(text_stripped)

        # (i) full entry (lenient) → print the EXACT corpus text
        if full_key == entry_key or body_key == entry_key:
            return {"quote": text_stripped, "source": source, "sha256": sha}

        # (ii) sentence-boundary prefix → EXACT prefix + " . . ."
        ends = [m.end() for m in _SENTENCE_END_RE.finditer(text_stripped)]
        for e in ends:
            prefix = text_stripped[:e].strip()
            if not prefix or prefix == text_stripped:
                continue
            prefix_key = _quote_lenient_key(prefix)
            if body_key == prefix_key or full_key == prefix_key:
                return {"quote": prefix + " . . .", "source": source, "sha256": sha}

    raise RuntimeError(err)


# ── Card quote sources (owner expansion of rule 11, 2026-08-04) ──────────────
# Quote cards DEFAULT to Ruhi Book 1 only — exactly the pre-expansion
# behavior — but Sheraj can now explicitly widen a single run's quote pool:
#   "ruhi_book1" — the manifest-verified Ruhi corpus (character-exact tier)
#   "lib:<slug>" — one ingested library text (verbatim-chunk tier: the printed
#                  text must be a boundary-honest verbatim span of an indexed
#                  chunk — the same index the bookmark product already prints
#                  from, so this adds no new trust in any LLM)
#   "web:<url>"  — RISKY and explicitly opt-in: the quote prints as supplied/
#                  fetched and is flagged quote_verified=false everywhere; it
#                  is never grounded and never silently trusted.
# Selection is per-run and explicit. Nothing ever falls back to a wider pool
# than what was selected — an empty result within the selection is still a
# hard stop.

RUHI_SOURCE_ID = "ruhi_book1"


def _parse_card_sources(sources: Optional[list]) -> tuple[bool, list[str], list[str]]:
    """
    Normalize a request's quote-source selection into
    (use_ruhi, lib_slugs, web_urls). None/empty → Ruhi only (the default and
    the pre-expansion behavior). Raises ValueError on unknown ids so
    endpoints can 422 before any job starts.
    """
    if not sources:
        return True, [], []
    from agents.librarian import list_library_sources
    known_slugs = {s["slug"] for s in list_library_sources()}
    use_ruhi, lib_slugs, web_urls = False, [], []
    for raw in sources:
        sid = str(raw or "").strip()
        if not sid:
            continue
        if sid == RUHI_SOURCE_ID:
            use_ruhi = True
        elif sid.startswith("lib:"):
            slug = sid[4:].strip()
            if slug not in known_slugs:
                raise ValueError(
                    f"Unknown library text '{slug}' — available: {sorted(known_slugs)}")
            if slug not in lib_slugs:
                lib_slugs.append(slug)
        elif sid.startswith("web:"):
            url = sid[4:].strip()
            if not url.lower().startswith(("http://", "https://")):
                raise ValueError(f"A web source must be an http(s) URL, got: {url[:80]!r}")
            if url not in web_urls:
                web_urls.append(url)
        else:
            raise ValueError(f"Unknown quote source id: {sid[:80]!r}")
    if not (use_ruhi or lib_slugs or web_urls):
        return True, [], []
    return use_ruhi, lib_slugs, web_urls


def _origin_label(origin: str) -> str:
    """Human name for a quote origin id (transcript/dashboard text only)."""
    if origin == RUHI_SOURCE_ID:
        return "Ruhi Book 1 (Reflections on the Life of the Spirit)"
    if origin.startswith("lib:"):
        from agents.librarian import list_library_sources
        slug = origin[4:]
        for s in list_library_sources():
            if s["slug"] == slug:
                return s["name"]
        return slug
    if origin.startswith("web:"):
        return re.sub(r"^https?://(www\.)?", "", origin[4:]).split("/")[0]
    return origin


def _card_retrieve(query: str, use_ruhi: bool, lib_slugs: list[str],
                   n_results: int = 3) -> list[dict]:
    """
    Citations for a card run, drawn ONLY from the selected verified local
    sources (web never contributes citations — it has no verified index).
    Each passage is tagged with `origin` ("ruhi_book1" or "lib:<slug>") so
    every downstream verbatim gate knows which discipline applies. Callers
    must treat an empty result exactly like the old empty-Ruhi result: a
    hard stop, never a silent fallback to a wider pool (rule 11).
    """
    out = []
    if use_ruhi:
        for p in (retrieve_ruhi_book1(query, n_results=n_results) or []):
            p = dict(p)
            p["origin"] = RUHI_SOURCE_ID
            out.append(p)
    if lib_slugs:
        for p in (retrieve(query, n_results=n_results, slugs=lib_slugs) or []):
            p = dict(p)
            p["origin"] = f"lib:{p.get('slug') or ''}"
            out.append(p)
    out.sort(key=lambda p: p.get("score") or 0.0, reverse=True)
    return out[:n_results]


def _find_verbatim_span(supplied_body: str, chunk_text: str) -> Optional[str]:
    """
    Locate supplied_body inside chunk_text as a contiguous run of whole
    words, comparing per-word under the same lenient normalization as
    _quote_lenient_key, and return the CHUNK's exact characters for that
    span — never the supplied characters (same "print the corpus text"
    discipline as _resolve_pinned_quote). None if not present.
    """
    sup = [t for t in (_quote_lenient_key(w) for w in supplied_body.split()) if t]
    if not sup:
        return None
    toks = [(m.group(0), m.start(), m.end()) for m in re.finditer(r"\S+", chunk_text)]
    ch = [_quote_lenient_key(t[0]) for t in toks]
    m = len(sup)
    for i in range(len(ch) - m + 1):
        if ch[i:i + m] == sup:
            return chunk_text[toks[i][1]:toks[i + m - 1][2]]
    return None


def _span_boundary_ok(chunk_text: str, span: str, elided: bool) -> bool:
    """
    Boundary honesty for a library-chunk span (mirrors the Ruhi tier's
    whole-entry / sentence-prefix rule): the span must START at a sentence
    start and END with sentence punctuation — elision marks excuse stopping
    before the chunk's end, never a mid-sentence cut. A span at the chunk's
    own start counts only if the chunk itself opens sentence-clean: the
    SentenceChunker's 100-char overlap can open a chunk mid-sentence, and a
    fragment ("immensity of the heavens, until...") must never print as if
    it were a complete passage (caught in offline testing, 2026-08-04).
    """
    start = chunk_text.find(span)
    if start < 0:
        return False
    prefix = chunk_text[:start].rstrip()
    if prefix:
        tail = prefix.rstrip("\"'”’)")
        if not tail or tail[-1] not in ".!?":
            return False
    else:
        # Lowercase first letter = almost certainly an overlap fragment. The
        # rare genuinely-lowercase opening loses a candidate (its complete
        # neighbor chunk usually resolves instead) — safe direction.
        first_alpha = next((ch for ch in span if ch.isalpha()), "")
        if first_alpha and first_alpha.islower():
            return False
    body = span.rstrip().rstrip("\"'”’)")
    if not body or body[-1] not in ".!?":
        # A chunk-final span may legitimately end with the source's own
        # elision (entries like "… appear great . . ."), which strips to
        # nothing above — accept only exact chunk-tail in that case.
        return elided is False and chunk_text.rstrip().endswith(span.rstrip()) and body != ""
    return True


def _lib_excerpt(chunk_text: str) -> Optional[str]:
    """
    Card-safe excerpt of a library chunk: drop a mid-sentence opening
    fragment (overlap chunking can open mid-sentence), then trim to card
    length at sentence boundaries (_trim_card_quote). None when the chunk
    holds no sentence-clean text to print.
    """
    text = (chunk_text or "").strip()
    first_alpha = next((ch for ch in text if ch.isalpha()), "")
    if first_alpha and first_alpha.islower():
        m = _SENTENCE_END_RE.search(text)
        if not m:
            return None
        text = text[m.end():].strip()
        first_alpha = next((ch for ch in text if ch.isalpha()), "")
        if not text or (first_alpha and first_alpha.islower()):
            return None
    # Drop a leading "N. " list marker (Paris Talks enumerates points) — the
    # card shouldn't print "1. To show compassion...". Verification still
    # holds: the remaining span starts right after the marker's ".", which
    # _span_boundary_ok reads as a sentence end.
    text = re.sub(r"^\d{1,3}\.\s+", "", text)
    return _trim_card_quote(text) if text else None


def _resolve_library_quote(supplied: str, lib_slugs: list[str]) -> Optional[dict]:
    """
    Tier-2 resolver: verify an owner-supplied quote as a boundary-honest
    verbatim span of a chunk in the SELECTED library texts (the same
    ChromaDB index the bookmark product already prints from). Returns
    {"quote", "source", "origin": "lib:<slug>", "link"} — the quote is the
    chunk's exact characters (+ " . . ." when the supply elided early) — or
    None when no selected text contains it. The verbatim check itself is
    pure string matching against stored chunk text; the embedding search
    only decides which chunks get checked.
    """
    supplied = (supplied or "").strip()
    if not supplied:
        return None
    body, elided = supplied, False
    for suffix in _ELISION_SUFFIXES:
        if body.endswith(suffix):
            body, elided = body[: -len(suffix)].strip(), True
            break
    try:
        candidates = retrieve(body[:400], n_results=8, slugs=lib_slugs) or []
    except Exception:
        return None
    for p in candidates:
        chunk = str(p.get("text") or "")
        span = _find_verbatim_span(body, chunk)
        if not span or not _span_boundary_ok(chunk, span, elided):
            continue
        return {"quote": span + (" . . ." if elided else ""),
                "source": str(p.get("source") or "").strip(),
                "origin": f"lib:{p.get('slug') or ''}",
                "link": p.get("link", "")}
    return None


def _resolve_pinned_quote_multi(supplied: str, use_ruhi: bool, lib_slugs: list[str],
                                web_urls: list[str], citation_hint: str = "") -> dict:
    """
    Tiered pinned-quote resolution across the selected sources. Verified
    local tiers always run first (Ruhi character-exact, then library
    verbatim-chunk); the web tier — only when explicitly selected — accepts
    the text as supplied, flagged verified=False (RISKY: the wording is the
    owner's responsibility, machine-unverified by definition). Raises
    RuntimeError naming the selected sources when nothing matches and web
    was not selected. Returns {"quote","source","origin","verified"}.
    """
    supplied = (supplied or "").strip()
    if use_ruhi:
        try:
            r = _resolve_pinned_quote(supplied)
            return {"quote": r["quote"], "source": r["source"],
                    "origin": RUHI_SOURCE_ID, "verified": True}
        except RuntimeError:
            if not (lib_slugs or web_urls):
                raise
    if lib_slugs:
        r = _resolve_library_quote(supplied, lib_slugs)
        if r:
            return {"quote": r["quote"], "source": r["source"],
                    "origin": r["origin"], "verified": True}
    if web_urls:
        if not supplied:
            raise RuntimeError("Empty card quote cannot be pinned.")
        return {"quote": supplied,
                "source": (citation_hint or "").strip() or _origin_label(f"web:{web_urls[0]}"),
                "origin": f"web:{web_urls[0]}", "verified": False}
    selected = ([RUHI_SOURCE_ID] if use_ruhi else []) + [f"lib:{s}" for s in lib_slugs]
    raise RuntimeError(
        "The quote could not be verified against the selected sources "
        f"({', '.join(selected) or 'none'}). Card quotes must match a passage in a "
        "selected verified source exactly (ellipses allowed only to shorten at a "
        "sentence boundary) — or enable the risky web source to print unverified "
        "wording deliberately. Nothing was generated."
    )


def _assert_excerpt_of(quote: str, chunk_text: str) -> None:
    """
    Render gate for a library-chunk pick (the counterpart of
    _assert_ruhi_verbatim): the about-to-print quote must be a verbatim,
    boundary-honest span of the retrieved chunk — sentence-clean start,
    sentence-punctuation end, elision marks covering any early stop —
    exactly what _lib_excerpt produces. Fails loudly, same contract as the
    Ruhi gate.
    """
    quote = (quote or "").strip()
    chunk = (chunk_text or "").strip()
    body, elided = quote, False
    for suffix in _ELISION_SUFFIXES:
        if body.endswith(suffix):
            body, elided = body[: -len(suffix)].strip(), True
            break
    if quote and body:
        span = _find_verbatim_span(body, chunk)
        if (span is not None and _span_boundary_ok(chunk, span, elided)
                and span + (" . . ." if elided else "") == quote):
            return
    raise RuntimeError(
        "Card quote is not a verbatim excerpt of the retrieved library passage, so the "
        f"render was stopped. Offending quote starts: {quote[:80]!r}."
    )


def _assert_card_quote_verbatim(quote: str, origin: str) -> None:
    """
    Origin-aware render/save gate. Ruhi → the manifest gate; library → the
    quote must independently re-verify as a verbatim span of its selected
    text; web → no verified source of truth exists (that is the point of the
    tier) — the quote_verified=false flag, not this gate, is the safeguard.
    """
    if origin.startswith("lib:"):
        r = _resolve_library_quote(quote, [origin[4:]])
        if not r or r["quote"] != (quote or "").strip():
            raise RuntimeError(
                "Card quote no longer re-verifies verbatim against the selected library "
                f"text ({_origin_label(origin)}) — render stopped. Starts: {quote[:80]!r}. "
                "Likely cause: a stale ChromaDB index (re-run scripts/ingest_texts.py)."
            )
    elif not origin.startswith("web:"):
        _assert_ruhi_verbatim(quote)


# ── Risky web tier: fetch a page, extract candidate passages ────────────────

_WEB_FETCH_MAX_BYTES = 2_000_000


def _fetch_web_page_text(url: str) -> tuple[str, list[str]]:
    """
    (page title, text blocks) from a web page, stdlib-only parsing. This
    feeds the RISKY quote tier: extraction is best-effort (sites differ,
    JS-rendered pages come back empty) and wording is NEVER verified — the
    caller flags everything from here as unverified.
    """
    import requests as _rq
    from html.parser import HTMLParser

    resp = _rq.get(url, timeout=20, stream=True,
                   headers={"User-Agent": "Mozilla/5.0 (bahAI-workforce quote finder)"})
    resp.raise_for_status()
    raw = resp.raw.read(_WEB_FETCH_MAX_BYTES + 1, decode_content=True)
    if len(raw) > _WEB_FETCH_MAX_BYTES:
        raise RuntimeError("page is larger than 2 MB — link a specific passage page instead")
    html = raw.decode(resp.encoding or "utf-8", errors="replace")

    _BLOCK_TAGS = ("p", "div", "blockquote", "li", "section", "article",
                   "h1", "h2", "h3", "br", "td")
    _SKIP_TAGS = ("script", "style", "noscript", "nav", "header", "footer")

    class _Extract(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.blocks: list[str] = []
            self._buf: list[str] = []
            self._skip = 0
            self._in_title = False
            self.title = ""

        def handle_starttag(self, tag, attrs):
            if tag in _SKIP_TAGS:
                self._skip += 1
            elif tag == "title":
                self._in_title = True
            elif tag in _BLOCK_TAGS:
                self._flush()

        def handle_endtag(self, tag):
            if tag in _SKIP_TAGS:
                self._skip = max(0, self._skip - 1)
            elif tag == "title":
                self._in_title = False
            elif tag in _BLOCK_TAGS:
                self._flush()

        def handle_data(self, data):
            if self._in_title:
                self.title += data
            elif not self._skip:
                self._buf.append(data)

        def _flush(self):
            text = " ".join("".join(self._buf).split())
            self._buf = []
            if len(text) >= 60:
                self.blocks.append(text)

    parser = _Extract()
    parser.feed(html)
    parser._flush()
    return " ".join(parser.title.split()), parser.blocks[:200]


# Obvious site chrome/boilerplate — never quote material. Best-effort filter
# for the risky tier (the owner still reviews everything it returns).
_WEB_BOILERPLATE_RE = re.compile(
    r"copyright|all rights reserved|terms of use|privacy|cookie|"
    r"download|available in the following formats|javascript|sign in|newsletter|"
    r"new version of|old version|can be accessed at|contact us|site map",
    re.IGNORECASE)

# "¶1:" / "§2." / "“3:" paragraph markers common on reference-library pages
# (reference.bahai.org renders its pilcrows as curly quotes) — noise on a
# card. Web-tier text is unverified by definition, so this cosmetic strip
# needs no verbatim gate.
_WEB_PARA_MARKER_RE = re.compile(r"^[¶§“”\"']?\s*\d+\s*[:.]\s*")


def _rank_web_blocks(topic: str, blocks: list[str], top_n: int) -> list[tuple[float, str]]:
    """
    Rank page blocks against the topic — local Ollama embeddings (the same
    model the index uses; free) with a plain token-overlap fallback. This is
    the risky tier: ranking is best-effort, the owner reviews every result.
    """
    import math as _math
    blocks = blocks[:120]
    try:
        from agents.librarian import _embed
        tvec = _embed(topic)

        def _cos(a, b):
            dot = sum(x * y for x, y in zip(a, b))
            na = _math.sqrt(sum(x * x for x in a))
            nb = _math.sqrt(sum(y * y for y in b))
            return dot / (na * nb) if na and nb else 0.0

        scored = [(round(_cos(tvec, _embed(b[:1000])), 4), b) for b in blocks]
    except Exception:
        twords = set(re.findall(r"[a-z']+", topic.lower()))
        scored = [
            (round(len(twords & set(re.findall(r"[a-z']+", b.lower()))) / max(1, len(twords)), 4), b)
            for b in blocks
        ]
    scored.sort(key=lambda t: t[0], reverse=True)
    return scored[:top_n]


def _best_matching_citation(quote: str, citations: list[dict]) -> dict:
    """
    Bag-of-words overlap: which of the (up to 3) retrieved Ruhi Book 1
    passages does the consultation's proposed quote most closely track?
    Used to replace the LLM's own wording with that passage's VERBATIM text —
    see the comment at its call site in _run_card_pipeline for why this
    exists: live testing caught the Librarian blending two different
    retrieved passages into one composite quote and crediting the whole
    thing to only one of their sources, with its own round-2 verdict
    overriding round-1's correct 'ORIGINAL COMPOSITION' self-assessment.
    Never trust that self-report for a claim this consequential — verify
    deterministically instead, the same discipline as _sanitize_claims.
    """
    def norm(s: str) -> set:
        return set(re.sub(r"[^a-z0-9 ]", " ", s.lower()).split())

    quote_words = norm(quote)
    best, best_score = citations[0], -1
    for c in citations:
        score = len(quote_words & norm(c.get("text", "")))
        if score > best_score:
            best, best_score = c, score
    return best


def _librarian_source_from(transcript: list, citations: list) -> str:
    """
    The citation line printed on the card: the Librarian's own SOURCE:
    attribution from the latest consultation turn, falling back to the top
    retrieved passage's source metadata.
    """
    for turn in reversed(transcript or []):
        if turn.get("agent") == "Librarian":
            m = re.search(r"SOURCE:\s*(.+)", turn.get("message", ""))
            if m:
                return m.group(1).strip()
    if citations:
        return str(citations[0].get("source") or "").strip()
    return ""


def _cap_at_word(text: str, max_len: int) -> str:
    """Hard-cap a string at max_len, preferring a word boundary so a mid-word
    cut never reaches the render."""
    text = (text or "").strip()
    if len(text) <= max_len:
        return text
    cut = text[:max_len].rstrip()
    sp = cut.rfind(" ")
    if sp > max_len // 3:
        cut = cut[:sp].rstrip()
    return cut


def _card_reflection(quote: str, task_id: str, language: str | None) -> dict:
    """
    Ask the local Scribe for a short reflection question + gentle action
    prompt inspired by the card's quote. Returns a dict suitable for
    card_compositor.render_quote_card's reflection= kwarg:
      {"question", "action", optional "native": {"question", "action"}}
    On ANY failure returns {} so the compositor's code defaults take over.
    Mechanical step — passed_review=None (rule 14). Never writes the share
    line or disclaimers (rule 8 — those stay code-owned in the compositor).
    """
    from agents.router import call_llm

    prompt = (
        "You write the back of a small giveaway reflection card.\n\n"
        f'Quote:\n"{(quote or "")[:400]}"\n\n'
        'Return ONLY this JSON:\n'
        '{"question": "...", "action": "..."}\n\n'
        "Rules:\n"
        "- question: short personal reflection (<=90 chars), first person, ends with ?\n"
        "- action: gently-phrased self-chosen action prompt (<=60 chars), ends with :\n"
        "- Inspired by the quote's spirit; calm, constructive, never commanding or salesy\n"
        "- No emojis, no scripture paraphrase, no quotation marks inside the strings\n"
    )
    raw = ""
    try:
        raw = call_llm(
            "scribe",
            [{"role": "user", "content": prompt}],
            agent="scribe",
            json_mode=True,
            max_tokens=200,
            temperature=0.6,
        ).strip()
    except Exception:
        log_run(task_id, "scribe", "card_reflection", (quote or "")[:200],
                "fallback: defaults", passed_review=None)
        return {}

    data = None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        m = re.search(r"\{[\s\S]*?\}", raw or "")
        if m:
            try:
                data = json.loads(m.group(0))
            except (json.JSONDecodeError, TypeError, ValueError):
                data = None

    if not isinstance(data, dict):
        log_run(task_id, "scribe", "card_reflection", (quote or "")[:200],
                "fallback: defaults", passed_review=None)
        return {}

    question = _cap_at_word(str(data.get("question") or ""), 110)
    action = _cap_at_word(str(data.get("action") or ""), 70)
    if not question and not action:
        log_run(task_id, "scribe", "card_reflection", (quote or "")[:200],
                "fallback: defaults", passed_review=None)
        return {}

    result: dict = {}
    if question:
        result["question"] = question
    if action:
        result["action"] = action

    # Optional native pair for translated runs — never fail the card over this.
    if language and result:
        try:
            from agents.translator import translate_quote
            native: dict = {}
            if question:
                nq = (translate_quote(question, language).get("text") or "").strip()
                if nq:
                    native["question"] = _cap_at_word(nq, 110)
            if action:
                na = (translate_quote(action, language).get("text") or "").strip()
                if na:
                    native["action"] = _cap_at_word(na, 70)
            if native:
                result["native"] = native
        except Exception:
            pass

    log_run(task_id, "scribe", "card_reflection", (quote or "")[:200],
            (result.get("question") or "ok")[:200], passed_review=None)
    return result


def _reflection_from_card_copy(card_copy: dict) -> Optional[dict]:
    """Rebuild the reflection= dict from fields stored on a saved card.
    Returns None when nothing was stored (compositor defaults apply)."""
    if not card_copy:
        return None
    q = (card_copy.get("reflection_question") or "").strip()
    a = (card_copy.get("reflection_action") or "").strip()
    native = card_copy.get("reflection_native")
    if not q and not a and not native:
        return None
    out: dict = {}
    if q:
        out["question"] = q
    if a:
        out["action"] = a
    if isinstance(native, dict):
        nq = (native.get("question") or "").strip()
        na = (native.get("action") or "").strip()
        if nq or na:
            out["native"] = {k: v for k, v in (("question", nq), ("action", na)) if v}
    return out or None


def _variant_faces_from_rendered(rendered: dict) -> dict:
    """Store-shape for per-language card pairs: {code: {front, back}}."""
    faces = {}
    for code, pair in (rendered.get("variants") or {}).items():
        if not isinstance(pair, dict):
            continue
        faces[code] = {
            "front": pair.get("front_path") or pair.get("front") or "",
            "back": pair.get("back_path") or pair.get("back") or "",
        }
    return faces


def _run_card_pipeline(req: CardPipelineRequest, progress, on_turn=None,
                       request_human_input=None, existing_product_id: str = None) -> dict:
    """
    The whole quote-card pipeline in one background job:
    task → Librarian → Artist (card brief + generate) → consultation (card
    framing, includes the post-round-2 pause for Sheraj) → optional
    translation → Card Compositor → Reviewer (card rubric, sees the rendered
    front) → simple revision loop (re-pick quote or repaint artwork — there is
    no listing text to edit) → save product.

    existing_product_id: set only by the "redo everything" redirect action —
    overwrites that product's row in place (same in-place-redo contract as
    bookmarks' _redo_product) instead of creating a new one.
    """
    from agents.artist import build_card_image_prompt, generate_image
    from agents.card_compositor import render_quote_card
    from agents.consultation import (
        run_consultation, run_card_revision_consultation,
        CARD_PIN_LABEL_LIBRARY, CARD_PIN_LABEL_WEB,
        CARD_SOURCE_SCOPE_EXPANDED, CARD_QUOTE_SOURCING_NOTE_EXPANDED,
    )
    from agents.reviewer import score_quote_card
    from agents.translator import translate_quote, LANGUAGES

    lang_name = LANGUAGES[req.language]["name"] if req.language else None
    # Redo-everything guidance steers retrieval/artwork only — req.theme
    # itself (used for title, stored theme, and every requote step below)
    # stays clean so a redo never bakes "NEW DIRECTION from Sheraj" text
    # into permanent storage the way appending it to req.theme would.
    retrieval_query = f"{req.theme}\n\n{req.guidance}" if req.guidance.strip() else req.theme

    progress("Creating task...")
    task_id = create_task(req.theme, "card_design", assigned_to="pipeline")

    # Owner-selected quote sources (rule 11 update, 2026-08-04): default is
    # Ruhi Book 1 only — the pre-expansion behavior, unchanged. Endpoints
    # validate ids before the job starts; this re-parse just converts.
    try:
        use_ruhi, lib_slugs, web_urls = _parse_card_sources(req.sources)
    except ValueError as e:
        raise RuntimeError(str(e))
    expanded = bool(lib_slugs or web_urls)

    # Owner-pinned quote: verify against the SELECTED sources BEFORE any
    # retrieval, image generation, or paid call. Fold the verified text into
    # the retrieval query (so citations + artist brief track the quote) but
    # never into req.theme (stored title stays clean).
    pinned = None
    pinned_turn = None
    if (req.pinned_quote or "").strip():
        pinned = _resolve_pinned_quote_multi(req.pinned_quote, use_ruhi, lib_slugs,
                                             web_urls, citation_hint=req.pinned_citation)
        retrieval_query = f"{retrieval_query}\n\n{pinned['quote']}"
        pinned_turn = {
            "agent": "System",
            "role": "owner-pinned quote",
            "message": (
                ('Sheraj pinned this exact quote (verified verbatim against '
                 f'{_origin_label(pinned["origin"])}): "{pinned["quote"]}" — '
                 'the team may shape artwork and framing only.')
                if pinned["verified"] else
                ('Sheraj pinned this quote from a WEB source '
                 f'({_origin_label(pinned["origin"])}) — its wording is NOT machine-'
                 'verified and the card is flagged accordingly: '
                 f'"{pinned["quote"]}" — the team may shape artwork and framing only.')
            ),
        }
        if on_turn:
            on_turn(pinned_turn)

    # Retrieval is restricted to the SELECTED verified sources (default: the
    # Ruhi Book 1 index alone — never the full 7-text corpus unless Sheraj
    # explicitly selected texts from it). Unlike the bookmark path (which
    # tolerates empty retrieval by letting the consultation's Librarian draw
    # on "well-known writings" generally), an empty result here must fail the
    # job outright: falling through to open-ended sourcing would silently
    # break the restriction the moment retrieval hiccups. The one exception:
    # a web-pinned run with NO local sources selected has no index to search
    # — the quote is already fixed, so the consultation proceeds citation-free.
    progress("Librarian is searching the selected sources for passages...")
    citations = _card_retrieve(retrieval_query, use_ruhi, lib_slugs, n_results=3)
    log_run(task_id, "librarian", "retrieve", retrieval_query[:200],
            f"{len(citations)} passages retrieved")
    if not citations:
        if not (pinned and not use_ruhi and not lib_slugs):
            raise RuntimeError(
                "No passage found in the selected source index(es) for this theme, or "
                "the index isn't built yet — run scripts/ingest_ruhi_book1.py (Ruhi) or "
                "scripts/ingest_texts.py (library). Quote cards only ever draw from the "
                "sources selected for the run, so this can't fall back to a wider pool."
            )

    progress("Artist is composing the card image brief (local Qwen3)...")
    image_prompt = build_card_image_prompt(retrieval_query, citations)
    log_run(task_id, "artist", "card_brief", req.theme[:200], image_prompt[:200])

    progress("Artist is generating the artwork (xAI)...")
    gen = generate_image(image_prompt, "2:3")
    image_path = gen.get("image_url", "")
    log_run(task_id, "artist", "generate", image_prompt[:200], image_path[:200])

    # ── Consultation (card framing) ──────────────────────────────────────────
    def _preview_front(quote: str, transcript: list) -> dict:
        """LLM-free render of BOTH faces for the pause (owner ask 2026-07-16 —
        the back now carries the artwork, so Sheraj steers from front AND
        back). Translation doesn't exist yet at this point in the pipeline,
        so the preview is English-only, and the reflection question isn't
        generated yet — the back previews with the code-default wording.
        When a quote is pinned, the pause preview shows the pinned text
        (what will actually print), not the consultation's interim pick."""
        print_quote = pinned["quote"] if pinned else quote
        preview = render_quote_card(
            image_path, print_quote,
            _librarian_source_from(transcript, citations)
            if not pinned else pinned["source"],
            reflection=None,
        )
        return {"front": _web_image_path(preview["front_path"]),
                "back": _web_image_path(preview["back_path"])}

    consultation = {"transcript": [], "context": "", "brief": {}}
    try:
        consultation = run_consultation(
            image_path, req.theme, image_prompt, citations,
            progress=progress, on_turn=on_turn,
            request_human_input=request_human_input, product="quote_card",
            render_preview=_preview_front,
            preview_note=(
                "The image above is the card's front face as it would print right now."
                + (f" The {lang_name} translation isn't added yet — it goes on right "
                   "after this step, with its AI-assisted label." if lang_name else "")
            ),
            fixed_quote=(pinned["quote"] if pinned else ""),
            # Code-owned provenance wording (rule 11 update, 2026-08-04):
            # empty strings preserve the original Ruhi wording exactly.
            fixed_quote_label=(
                "" if not pinned or pinned["origin"] == RUHI_SOURCE_ID else
                CARD_PIN_LABEL_WEB.format(name=_origin_label(pinned["origin"]))
                if pinned["origin"].startswith("web:") else
                CARD_PIN_LABEL_LIBRARY.format(name=_origin_label(pinned["origin"]))
            ),
            source_scope_override=(CARD_SOURCE_SCOPE_EXPANDED if expanded else ""),
        )
        vq = (consultation.get("verified_quote") or "").strip()
        # passed_review=None — consultation process, not a quality verdict.
        log_run(task_id, "consultation", "consult", req.theme[:200],
                f"{len(consultation['transcript'])} turns completed"
                + (f"; quote len={len(vq)}" if vq else ""))
    except Exception as e:
        consultation["transcript"] = [{"agent": "System", "role": "error",
                                       "message": f"Consultation skipped: {e}"}]
        log_run(task_id, "consultation", "consult", req.theme[:200],
                f"failed: {e}"[:400])

    # Surface the pin on the stored transcript (on_turn already emitted it
    # live; prepend so the dashboard shows it before the consultation turns).
    if pinned_turn:
        consultation["transcript"] = [pinned_turn] + list(consultation.get("transcript") or [])

    editing_log = []

    # Honour the consultation's image decision (same contract as bookmarks:
    # regenerate ONCE so the shipped card reflects what the team agreed).
    brief = consultation.get("brief") or {}
    adjustment = (brief.get("image_adjustment") or "").strip()
    if adjustment and image_path:
        progress(f"Artist is repainting per the consultation: {adjustment[:120]}...")
        try:
            revised_prompt = (f"{image_prompt}\n\n"
                              f"IMPORTANT adjustment agreed in team consultation: {adjustment}")
            regen = generate_image(revised_prompt, "2:3")
            new_path = regen.get("image_url", "")
            if new_path and Path(new_path).exists():
                image_path, image_prompt = new_path, revised_prompt
                editing_log.append(
                    {"agent": "Artist", "role": "image revision (consultation)",
                     "message": f"Repainted the artwork per the team's agreed adjustment:\n{adjustment}"})
                # passed_review stays None: generating a file is mechanical
                # success, not a quality verdict — trust only moves on judged
                # outcomes (principle 8).
                log_run(task_id, "artist", "regenerate",
                        adjustment[:200], new_path[:200])
        except Exception as e:
            editing_log.append(
                {"agent": "Artist", "role": "image revision (consultation)",
                 "message": f"Regeneration failed ({e}) — continuing with the original artwork."})
            log_run(task_id, "artist", "regenerate", adjustment[:200],
                    f"failed: {e}"[:400])

    # ── The quote (and its honesty flags) ────────────────────────────────────
    # When Sheraj pinned an exact quote, use it as-is (already corpus-verified)
    # and skip the consultation-matching / trim path entirely. Otherwise the
    # consultation's own wording is NEVER printed as-is: whatever quote (or
    # fragment of one) it proposed is used only to pick WHICH of the retrieved
    # Ruhi Book 1 passages the team meant — the printed text and citation are
    # always that passage's own verbatim (trimmed) text and true source
    # metadata. This is deterministic by construction (every passage in
    # `citations` came from retrieve_ruhi_book1, so this can never surface a
    # quote from outside the book), and it closes a real failure mode caught
    # live: a Librarian round can blend two different retrieved passages into
    # one composite line and credit the whole thing to just one of them —
    # round 2 once reversed round 1's own correct "ORIGINAL COMPOSITION"
    # verdict to "GROUNDED" for exactly this kind of blend.
    if pinned:
        quote = pinned["quote"]
        citation_src = pinned["source"]
        quote_origin = pinned["origin"]
        _assert_card_quote_verbatim(quote, quote_origin)  # no-op only for the flagged web tier
        quote_grounded = pinned["verified"]  # web tier is NEVER grounded
    else:
        proposed = (consultation.get("verified_quote") or "").strip()
        if proposed and citations:
            matched = _best_matching_citation(proposed, citations)
        elif citations:
            matched = citations[0]
        else:
            raise RuntimeError(
                "No verified quote available: consultation produced none and no passages "
                "were retrieved. Build the selected source index(es) or retry."
            )
        # Take the matched passage's card-safe excerpt; a library chunk that
        # is only a mid-sentence overlap fragment yields none — fall through
        # to the next-best citation rather than print a fragment.
        ordered = [matched] + [c for c in citations if c is not matched]
        quote = None
        for cand in ordered:
            cand_origin = cand.get("origin") or RUHI_SOURCE_ID
            q = (_trim_card_quote(cand["text"]) if cand_origin == RUHI_SOURCE_ID
                 else _lib_excerpt(cand["text"]))
            if q:
                quote, matched = q, cand
                break
        if quote is None:
            raise RuntimeError(
                "Every retrieved passage was a mid-sentence fragment — retry, or "
                "rephrase the theme so retrieval surfaces complete passages."
            )
        quote_origin = matched.get("origin") or RUHI_SOURCE_ID
        # Character-exact against the passage's own verified index, or the
        # job fails loudly — the Ruhi manifest gate for Ruhi picks, the
        # excerpt-of-chunk gate for selected library picks.
        if quote_origin == RUHI_SOURCE_ID:
            _assert_ruhi_verbatim(quote)
        else:
            _assert_excerpt_of(quote, matched["text"])
        quote_grounded = True  # always true here — a verbatim excerpt of an indexed passage
        citation_src = str(matched.get("source") or "").strip()

    # Quote lock: a pinned supply OR any pause guidance from Sheraj means the
    # revision loop may not swap the quote (production bug: mid-run "use the
    # full quote please" was later overridden by a requote action).
    quote_locked = bool(pinned) or bool((consultation.get("human_note") or "").strip())

    # Reflection face copy (back of the card) — LLM-written like listing copy;
    # share line + disclaimers stay code-owned inside the compositor (rule 8).
    # Empty dict on failure → compositor defaults. Before translation so a
    # translated run still gets native reflection via translate_quote inside.
    progress("Scribe is drafting the reflection question for the back...")
    reflection = _card_reflection(quote, task_id, req.language)

    # ── Optional translation (Grok; labeled AI-assisted by code, not the LLM) ─
    def _translate(q: str) -> Optional[dict]:
        if not req.language:
            return None
        progress(f"Translating the quote into {lang_name} (xAI Grok)...")
        try:
            tr = translate_quote(q, req.language)
        except Exception as first_err:
            progress(f"Translation attempt failed ({first_err}) — retrying once...")
            tr = translate_quote(q, req.language)  # second failure raises → job errors honestly
        # Translator script check is a deterministic judged outcome (rule 14).
        log_run(task_id, "translator", "translate", q[:200],
                tr["text"][:200], passed_review=True)
        return tr

    try:
        translation = _translate(quote)
    except Exception as e:
        log_run(task_id, "translator", "translate", quote[:200],
                f"failed: {e}"[:400], passed_review=False)
        raise RuntimeError(f"Translation into {lang_name} failed twice: {e}") from e

    # ── Render → Score → Revise loop ─────────────────────────────────────────
    # No listing text exists, so revision levers are re-picking the quote
    # ("requote") or regenerating the artwork ("repaint") — chosen by the
    # Reviewer's machine-readable `action`, never inferred from prose.
    def _render(q: str, tr: Optional[dict]) -> dict:
        progress("Card Compositor is rendering the front and back faces...")
        # reflection is closed-over; new quote → new reflection before re-render.
        r = render_quote_card(image_path, q, citation_src, translation=tr,
                              reflection=reflection)
        log_run(task_id, "compositor", "render_card", image_path[:200],
                r["front_path"][:200])
        return r

    def _score(q: str, tr: Optional[dict], rendered: dict, prev=None, note=None) -> dict:
        progress("Reviewer is scoring the card (seeing the rendered front face)...")
        return score_quote_card(
            req.theme, q, citation_src, quote_grounded,
            front_image_path=rendered["front_path"], translation=tr,
            consultation_transcript=consultation.get("transcript"),
            consultation_decision=brief or None,
            previous_review=prev, revision_note=note,
            quote_pinned=bool(pinned),
            back_image_path=rendered["back_path"],
            sourcing_note=(CARD_QUOTE_SOURCING_NOTE_EXPANDED if expanded else None),
            quote_web_unverified=str(quote_origin).startswith("web:"),
        )

    latest_citations = citations  # updated after each requote so the team always sees current candidates
    revision_history = []  # [{attempt, action, guidance, overall, prev_overall}, ...] this run

    def _team_decide(rendered: dict, review: dict, attempt: int) -> dict:
        """
        The whole team weighs in on the Reviewer's scored concerns before the
        pipeline commits to a revision — previously the Reviewer's own
        action/action_guidance drove requote/repaint unilaterally ("the last
        part just has the reviewer saying stuff" — owner feedback, 2026-07).
        Skipped when the card already meets target: no need to convene the
        team just to say "ship".
        """
        if review.get("overall", 0) >= req.target_score:
            return {"action": "ship", "action_guidance": ""}
        decision = run_card_revision_consultation(
            req.theme, quote, citation_src, rendered["front_path"], latest_citations,
            review, progress=progress, on_turn=on_turn, attempt=attempt,
            history=revision_history, quote_pinned=quote_locked,
            back_image_path=rendered["back_path"],
            sourcing_note=(CARD_QUOTE_SOURCING_NOTE_EXPANDED if expanded else ""),
        )
        editing_log.extend(decision["transcript"])
        # passed_review=None — holding a consultation is process, not a judged
        # outcome; trust only moves on quality verdicts (principle 8).
        log_run(task_id, "consultation", f"card_revision_consult_{attempt}",
                req.theme[:200], decision["action"])
        return decision

    rendered = _render(quote, translation)
    review = _score(quote, translation, rendered)
    log_run(task_id, "reviewer", "card_score_1", req.theme[:200],
            json.dumps({"overall": review.get("overall")})[:200],
            passed_review=review.get("passed"))
    editing_log.append(
        {"agent": "Reviewer", "role": "card score — attempt 1 (editing)",
         "message": f"Overall {review.get('overall', 0)}/10.\n"
                    f"Recommendation: {review.get('recommendation', '')}"})

    best = {"quote": quote, "grounded": quote_grounded, "citation": citation_src,
            "origin": quote_origin,
            "translation": translation, "reflection": reflection,
            "rendered": rendered, "review": review,
            "image_path": image_path, "image_prompt": image_prompt}
    cur_review = review
    attempt = 1
    stalled = 0

    decision = (_team_decide(rendered, review, attempt=1)
                if attempt < req.max_attempts else {"action": "ship", "action_guidance": ""})
    cur_action, cur_guidance = decision["action"], decision["action_guidance"]

    while best["review"].get("overall", 0) < req.target_score and attempt < req.max_attempts:
        action = cur_action
        guidance = cur_guidance
        if action not in ("requote", "repaint"):
            editing_log.append({"agent": "System", "role": "editing stopped",
                                "message": "The team's consultation reached no further revision "
                                           f"action — keeping the best card ({best['review'].get('overall', 0)}/10)."})
            break
        # Code-enforce the quote lock regardless of what the consult returned
        # (consultation also coerces requote→ship when quote_pinned, but this
        # is the hard gate that makes the production bug unrecoverable).
        if action == "requote" and quote_locked:
            editing_log.append({
                "agent": "System", "role": "editing stopped",
                "message": "The team proposed swapping the quote, but Sheraj's choice "
                           "locks it for this run — keeping the quote.",
            })
            break
        attempt += 1

        if action == "requote":
            progress(f"Re-picking the quote per the Reviewer: {guidance[:100] or req.theme}...")
            try:
                passages = _card_retrieve(guidance.strip() or req.theme,
                                          use_ruhi, lib_slugs, n_results=3)
            except Exception:
                passages = []
            if passages:
                latest_citations = passages  # keep the team's view of "other candidates" current
            # First passage whose card-safe excerpt is usable AND different —
            # library fragments (no sentence-clean text) are skipped, never printed.
            pick = new_q = None
            for p in passages:
                p_origin = p.get("origin") or RUHI_SOURCE_ID
                q = (_trim_card_quote(p["text"]) if p_origin == RUHI_SOURCE_ID
                     else _lib_excerpt(p["text"]))
                if q and q != quote:
                    pick, new_q = p, q
                    break
            if not pick:
                editing_log.append({"agent": "System", "role": "editing stopped",
                                    "message": "No different passage found for the Reviewer's "
                                               "steer — keeping the best card."})
                break
            quote = new_q
            quote_origin = pick.get("origin") or RUHI_SOURCE_ID
            # Same gates as the initial pick — requotes get no exemption.
            if quote_origin == RUHI_SOURCE_ID:
                _assert_ruhi_verbatim(quote)
            else:
                _assert_excerpt_of(quote, pick["text"])
            quote_grounded = True
            citation_src = str(pick.get("source") or "").strip() or citation_src
            # passed_review=None — finding a different passage is mechanical;
            # whether it HELPED is judged by the re-score that follows.
            log_run(task_id, "librarian", f"requote_{attempt}", guidance[:200], quote[:200])
            try:
                translation = _translate(quote)
            except Exception as e:
                # Script-check / translate failure is a judged outcome (rule 14).
                log_run(task_id, "translator", "translate", quote[:200],
                        f"failed: {e}"[:400], passed_review=False)
                editing_log.append({"agent": "System", "role": "editing stopped",
                                    "message": f"Translation of the re-picked quote failed ({e}) — "
                                               "keeping the best card."})
                break
            # New quote → new reflection question/action (same path as initial).
            reflection = _card_reflection(quote, task_id, req.language)
            revision_note = f'Quote re-picked per your steer: now "{quote[:120]}" ({citation_src})'
        else:  # repaint
            progress(f"Artist is repainting per the Reviewer: {guidance[:100]}...")
            try:
                new_prompt = (f"{image_prompt}\n\nIMPORTANT change requested in review: "
                              f"{guidance or 'better express the theme'}")
                regen = generate_image(new_prompt, "2:3")
                new_path = regen.get("image_url", "")
                if not (new_path and Path(new_path).exists()):
                    raise RuntimeError("no image returned")
                image_path, image_prompt = new_path, new_prompt
                # passed_review=None — same reasoning as requote above.
                log_run(task_id, "artist", f"repaint_{attempt}", guidance[:200],
                        new_path[:200])
            except Exception as e:
                log_run(task_id, "artist", f"repaint_{attempt}", guidance[:200],
                        f"failed: {e}"[:400])
                editing_log.append({"agent": "System", "role": "editing stopped",
                                    "message": f"Repaint failed ({e}) — keeping the best card."})
                break
            revision_note = f"Artwork regenerated per your steer: {guidance[:150]}"

        rendered = _render(quote, translation)
        new_review = _score(quote, translation, rendered, prev=cur_review, note=revision_note)
        log_run(task_id, "reviewer", f"card_score_{attempt}", req.theme[:200],
                json.dumps({"overall": new_review.get("overall")})[:200],
                passed_review=new_review.get("passed"))
        prev_overall = cur_review.get("overall", 0)
        new_overall = new_review.get("overall", 0)
        editing_log.append(
            {"agent": "Reviewer", "role": f"card score — attempt {attempt} (editing)",
             "message": f"Overall {new_overall}/10 (was {prev_overall}/10 — "
                        f"{'improved' if new_overall > prev_overall else 'did not improve'}).\n"
                        f"Applied: {revision_note}\n"
                        f"Recommendation: {new_review.get('recommendation', '')}"})

        revision_history.append({"attempt": attempt, "action": action, "guidance": guidance,
                                  "overall": new_overall, "prev_overall": prev_overall})
        cur_review = new_review
        decision = (_team_decide(rendered, new_review, attempt=attempt)
                    if attempt < req.max_attempts else {"action": "ship", "action_guidance": ""})
        cur_action, cur_guidance = decision["action"], decision["action_guidance"]
        best_overall = best["review"].get("overall", 0)
        if new_overall >= best_overall:
            # Ties adopt the newer card — it incorporated more feedback
            # (same invariant as the listing loop). Only strict regressions
            # count toward the 2-strike stall.
            best = {"quote": quote, "grounded": quote_grounded, "citation": citation_src,
                    "origin": quote_origin,
                    "translation": translation, "reflection": reflection,
                    "rendered": rendered, "review": new_review,
                    "image_path": image_path, "image_prompt": image_prompt}
            if new_overall > best_overall:
                stalled = 0
        else:
            stalled += 1
        if stalled >= 2:
            editing_log.append({"agent": "System", "role": "editing stopped",
                                "message": f"Two revisions in a row scored worse than the best — "
                                           f"keeping the best card ({best['review'].get('overall', 0)}/10)."})
            break

    # ── Save ─────────────────────────────────────────────────────────────────
    progress("Saving the quote card...")
    tr = best["translation"]
    # Final gate: a pinned quote must still be byte-identical at save time.
    if pinned:
        best_hash = hashlib.sha256(best["quote"].encode("utf-8")).hexdigest()
        pinned_hash = hashlib.sha256(pinned["quote"].encode("utf-8")).hexdigest()
        if best_hash != pinned_hash:
            raise RuntimeError(
                "Pinned quote was altered before save — refusing to write the product. "
                f"Pinned starts: {pinned['quote'][:80]!r}; "
                f"best starts: {best['quote'][:80]!r}."
            )
    refl = best.get("reflection") or {}
    card_copy = {
        "product_kind": "quote_card",
        "quote": best["quote"],
        "quote_grounded": best["grounded"],
        "citation": best["citation"],
        "language": req.language,
        "language_name": (tr or {}).get("name"),
        "translation_text": (tr or {}).get("text"),
        "translation_disclaimer_native": (tr or {}).get("disclaimer_native"),
        "translation_disclaimer_en": (tr or {}).get("disclaimer_en"),
        # Fixed string, never LLM-written — same honesty class as the
        # translation disclaimers above.
        "artwork_disclosure": CARD_ART_DISCLOSURE,
        "quote_pinned": bool(pinned),
        # Provenance (rule 11 update, 2026-08-04). quote_verified is False
        # ONLY for the risky web tier — the dashboard surfaces that plainly.
        "quote_verified": not str(best.get("origin") or "").startswith("web:"),
        "quote_provenance": best.get("origin") or RUHI_SOURCE_ID,
        "quote_sources": req.sources or [RUHI_SOURCE_ID],
        # Reflection face copy actually used (empty string when compositor
        # defaults filled in). Share line stays code-owned, never stored.
        "reflection_question": refl.get("question") or "",
        "reflection_action": refl.get("action") or "",
        "reflection_native": refl.get("native") or None,
        # Per-language card pair paths from the compositor (empty when EN-only).
        "variant_faces": _variant_faces_from_rendered(best["rendered"]),
    }
    title = f"Quote card — {req.theme[:70]}" + (f" ({lang_name})" if lang_name else "")
    if existing_product_id:
        product_id = existing_product_id
        update_product(
            product_id, title=title, image_url=best["image_path"],
            listing_copy=json.dumps(card_copy), image_prompt=best["image_prompt"],
            theme=req.theme, product_type="quote_card",
        )
    else:
        product_id = create_product(
            task_id=task_id, title=title, image_url=best["image_path"],
            listing_copy=json.dumps(card_copy), image_prompt=best["image_prompt"],
            theme=req.theme, product_type="quote_card",
        )
    full_transcript = consultation.get("transcript", []) + editing_log
    overall = best["review"].get("overall", 0)
    update_product(product_id,
                   reviewer_scores=json.dumps(best["review"]),
                   consultation=json.dumps(full_transcript),
                   front_image=best["rendered"]["front_path"],
                   back_image=best["rendered"]["back_path"],
                   target_reached=1 if overall >= req.target_score else 0,
                   attempts=attempt)
    update_task_status(task_id, "completed")
    return {
        "task_id": task_id,
        "product_id": product_id,
        "product_type": "quote_card",
        "theme": req.theme,
        "language": req.language,
        "language_name": lang_name,
        "quote": best["quote"],
        "quote_grounded": best["grounded"],
        "quote_verified": not str(best.get("origin") or "").startswith("web:"),
        "quote_provenance": best.get("origin") or RUHI_SOURCE_ID,
        "citation": best["citation"],
        "translation": tr,
        "image_prompt": best["image_prompt"],
        "image_path": best["image_path"],
        "image_web": _web_image_path(best["image_path"]),
        "front_image_path": best["rendered"]["front_path"],
        "front_image_web": _web_image_path(best["rendered"]["front_path"]),
        "back_image_path": best["rendered"]["back_path"],
        "back_image_web": _web_image_path(best["rendered"]["back_path"]),
        # Per-language card pairs, passed explicitly so the dashboard's
        # results panel never has to fish them out of a stale product cache
        # (real bug: a fresh run's Spanish pair rendered fine but the panel
        # couldn't find the just-created product — 2026-07-16).
        "variant_faces": _variant_faces_from_rendered(best["rendered"]),
        "compositor_error": None,
        "review": best["review"],
        "attempts": attempt,
        "target_reached": overall >= req.target_score,
        "badge": _badge(overall),
        "consultation": full_transcript,
    }


@router.post("/pipeline/run-card")
def pipeline_run_card(req: CardPipelineRequest):
    """
    Dashboard entry point for the Quote Cards pipeline (parallel to
    /pipeline/run, which stays bookmark-only). Returns {job_id} immediately;
    poll GET /pipeline/status/{job_id}.
    """
    from agents.translator import LANGUAGES
    if not req.theme.strip():
        raise HTTPException(status_code=422, detail="theme is required")
    if req.language and req.language not in LANGUAGES:
        raise HTTPException(status_code=422,
                            detail=f"Unknown language '{req.language}' — offered: {sorted(LANGUAGES)}")
    try:
        use_ruhi, lib_slugs, web_urls = _parse_card_sources(req.sources)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    # A web-only run has no verified index for the team to pick a quote from
    # — the quote must be fixed up front (the finder can fetch candidates).
    if web_urls and not (use_ruhi or lib_slugs) and not (req.pinned_quote or "").strip():
        raise HTTPException(
            status_code=422,
            detail="A web-only source run needs an exact quote up front — use "
                   '"Find quotes" to fetch candidates from the page, or add a '
                   "verified source to the selection.")
    job_id = _start_job(
        "card-pipeline",
        lambda progress, on_turn, ask: _run_card_pipeline(req, progress, on_turn, ask),
    )
    return {"job_id": job_id, "status": "running"}


class CardBatchRequest(BaseModel):
    theme: str
    language: Optional[str] = None   # shared by every card in the batch
    target_score: float = 9.0
    max_attempts: int = 3
    # One owner-supplied exact quote per card. Each is resolved against the
    # SELECTED sources in the ENDPOINT — a bad paste is a 422 before the job
    # even starts, so nothing paid ever runs on an unverifiable quote (web
    # tier excepted by design: it is explicitly unverified and flagged).
    quotes: list[str] = []
    # Owner-selected quote sources shared by the whole batch (rule 11 update,
    # 2026-08-04). None/empty = Ruhi Book 1 only, the unchanged default.
    sources: Optional[list[str]] = None
    # Optional per-quote citation labels, aligned by index with `quotes`.
    # Used ONLY for quotes that resolve to the risky web tier (verified
    # tiers always print corpus metadata). The dashboard fills these from
    # the finder's results; missing/empty entries fall back to the web host.
    quote_citations: Optional[list[str]] = None


# Spend guard: every card in a batch is a full paid pipeline run (xAI image
# + Grok consultation/review), so a single request can't queue unbounded work.
_CARD_BATCH_MAX = 19


def _run_card_batch(req: CardBatchRequest, progress, on_turn=None) -> dict:
    """
    Run several quote cards as ONE background job, strictly one card at a
    time (same sequential GPU/API discipline as everything else). Batch runs
    are hands-free by design (owner ask, 2026-07-17): request_human_input is
    never passed down, so run_consultation's round-2 pause simply doesn't
    happen and the job never enters 'waiting_for_input'.

    req.quotes arrive already resolved to exact corpus text by the endpoint;
    _run_card_pipeline re-verifies each via its own pinned-quote gate (the
    authoritative check stays where it always was). After the up-front
    verification, one card's mid-run failure (e.g. a transient xAI error) is
    recorded on its item and announced as a turn — it never kills the rest
    of the batch.
    """
    total = len(req.quotes)
    items = []
    completed = 0
    for i, quote in enumerate(req.quotes):
        label = f"Card {i + 1}/{total}"
        if on_turn:
            on_turn({"agent": "System", "role": "batch",
                     "message": f'{label} begins: "{quote[:90]}{"..." if len(quote) > 90 else ""}" '
                                "— hands-free run, the team proceeds without the mid-run check-in."})
        citations_list = req.quote_citations or []
        item_req = CardPipelineRequest(
            theme=req.theme, language=req.language,
            target_score=req.target_score, max_attempts=req.max_attempts,
            pinned_quote=quote, sources=req.sources,
            pinned_citation=(citations_list[i] if i < len(citations_list) else ""),
        )
        try:
            r = _run_card_pipeline(
                item_req,
                lambda m, label=label: progress(f"{label}: {m}"),
                on_turn=on_turn, request_human_input=None,
            )
            completed += 1
            items.append({
                "index": i + 1, "status": "done",
                "quote": r["quote"], "citation": r.get("citation"),
                "product_id": r["product_id"], "task_id": r["task_id"],
                "front_image_web": r["front_image_web"],
                "back_image_web": r["back_image_web"],
                "variant_faces": r.get("variant_faces") or {},
                "overall": r["review"].get("overall"), "badge": r["badge"],
                "attempts": r["attempts"], "target_reached": r["target_reached"],
            })
        except Exception as e:
            items.append({"index": i + 1, "status": "error",
                          "quote": quote, "error": str(e)})
            if on_turn:
                on_turn({"agent": "System", "role": "error",
                         "message": f"{label} failed and was skipped ({e}) — moving on to the next card."})
    from agents.translator import LANGUAGES
    return {
        "batch": True,
        "product_type": "quote_card_batch",
        "theme": req.theme,
        "language": req.language,
        "language_name": LANGUAGES[req.language]["name"] if req.language else None,
        "total": total,
        "completed": completed,
        "failed": total - completed,
        "items": items,
    }


@router.post("/pipeline/run-card-batch")
def pipeline_run_card_batch(req: CardBatchRequest, started_by: str = "sheraj"):
    """
    Queue several quote cards in one hands-free job (the consultation's
    mid-run pause is skipped for every card — batch runs never wait on
    Sheraj). Every quote is verified against the SELECTED sources HERE,
    before the job starts: an unverifiable paste is a 422 and nothing is
    generated. (A quote that resolves to the explicitly-selected risky web
    tier is accepted by design — unverified and flagged, per rule 11's
    2026-08-04 owner update.) Poll the returned job_id like any other
    pipeline job; the result carries batch=true and a per-card items list.
    """
    from agents.translator import LANGUAGES
    if not req.theme.strip():
        raise HTTPException(status_code=422, detail="theme is required")
    if req.language and req.language not in LANGUAGES:
        raise HTTPException(status_code=422,
                            detail=f"Unknown language '{req.language}' — offered: {sorted(LANGUAGES)}")
    try:
        use_ruhi, lib_slugs, web_urls = _parse_card_sources(req.sources)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    # Keep citations aligned while dropping empty quote boxes.
    raw_citations = req.quote_citations or []
    pairs = [(q.strip(), (raw_citations[i] if i < len(raw_citations) else "").strip())
             for i, q in enumerate(req.quotes or []) if (q or "").strip()]
    if not pairs:
        raise HTTPException(status_code=422, detail="at least one quote is required")
    if len(pairs) > _CARD_BATCH_MAX:
        raise HTTPException(
            status_code=422,
            detail=f"A batch is capped at {_CARD_BATCH_MAX} cards per run (each card is a "
                   "full paid pipeline run) — split the rest into a second batch.")
    resolved, resolved_citations = [], []
    for i, (q, hint) in enumerate(pairs):
        try:
            r = _resolve_pinned_quote_multi(q, use_ruhi, lib_slugs, web_urls,
                                            citation_hint=hint)
        except RuntimeError as e:
            raise HTTPException(
                status_code=422,
                detail=f'Quote {i + 1} of {len(pairs)} (starts: "{q[:60]}") '
                       f"could not be verified: {e}")
        resolved.append(r["quote"])
        # Web-tier quotes carry their citation label through the job; verified
        # tiers re-derive their citation from corpus metadata in the pipeline.
        resolved_citations.append(r["source"] if not r["verified"] else "")
    req.quotes = resolved
    req.quote_citations = resolved_citations
    job_id = _start_job(
        "card-batch",
        lambda progress, on_turn, ask: _run_card_batch(req, progress, on_turn),
        started_by=started_by,
    )
    return {"job_id": job_id, "status": "running", "total": len(resolved)}


def _fit_or_shorten(quote: str, source: str) -> Optional[tuple[str, bool]]:
    """
    (fitting quote, was_shortened) for a finder suggestion: the full text if
    it renders at the card's readable minimum (rule 29), else the longest
    sentence-boundary prefix + " . . ." that does; None when nothing fits.
    """
    from agents.card_compositor import quote_fits_card
    if quote_fits_card(quote, source):
        return quote, False
    best_prefix = None
    for m in _SENTENCE_END_RE.finditer(quote):
        prefix = quote[: m.end()].strip()
        if prefix and prefix != quote:
            cand = prefix + " . . ."
            if quote_fits_card(cand, source):
                best_prefix = cand  # keep the longest fitting prefix
    return (best_prefix, True) if best_prefix else None


@router.get("/quote-sources")
def quote_card_sources():
    """
    The quote-source options the card form can offer (rule 11 update,
    2026-08-04): Ruhi Book 1 (always first, the default) plus every ingested
    library text. The risky web option is a client-side row (it needs a URL
    typed in), so it isn't listed here.
    """
    from agents.librarian import list_library_sources
    return {"sources": (
        [{"id": RUHI_SOURCE_ID,
          "name": "Ruhi Book 1 — Reflections on the Life of the Spirit",
          "kind": "verified", "default": True}]
        + [{"id": f"lib:{s['slug']}", "name": s["name"], "kind": "verified",
            "default": False} for s in list_library_sources()]
    )}


@router.get("/ruhi-quotes")
def suggest_ruhi_quotes(topic: str = "", count: int = 4, sources: str = ""):
    """
    Librarian quote suggestions for the card form: semantic search of the
    SELECTED sources (default: the Ruhi Book 1 corpus alone — the rule 11
    default; `sources` is a comma-separated id list, same ids as the
    pipeline). Local sources are free (ChromaDB + a local Ollama embedding
    — no paid LLM call, so no spend metering); a "web:<url>" source
    additionally fetches that page (RISKY tier: wording NOT verified,
    items flagged verified=false).

    Every local-tier quote is canonicalized through the same resolvers the
    batch endpoint verifies with, so pasting a suggestion straight into a
    run verifies by construction; anything too long for the card's readable
    minimum (rule 29) is shortened at a sentence boundary or skipped.
    Verified local results always rank before risky web results.
    """
    topic = (topic or "").strip()
    if not topic:
        raise HTTPException(status_code=422, detail="Give the Librarian a topic to search for.")
    if not 1 <= count <= _CARD_BATCH_MAX:
        raise HTTPException(
            status_code=422,
            detail=f"count must be between 1 and {_CARD_BATCH_MAX} (the batch cap).")
    try:
        use_ruhi, lib_slugs, web_urls = _parse_card_sources(
            [s for s in (sources or "").split(",") if s.strip()])
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    items, skipped_too_long, seen = [], 0, set()
    web_note = None

    if use_ruhi or lib_slugs:
        # Retrieve a few extra so a skipped-as-unfittable passage can be
        # backfilled and the owner still gets the number asked for.
        try:
            passages = _card_retrieve(topic, use_ruhi, lib_slugs,
                                      n_results=min(count + 5, 24))
        except Exception as e:
            raise HTTPException(
                status_code=503,
                detail=f"The Librarian's local search isn't reachable right now ({e}) — "
                       "is Ollama running?")
        if not passages and not web_urls:
            raise HTTPException(
                status_code=503,
                detail="The selected source index(es) haven't been built on this machine — "
                       "run scripts/ingest_ruhi_book1.py (Ruhi) or scripts/ingest_texts.py "
                       "(library). Quote suggestions never fall back to unselected sources.")
        for p in passages:
            if len(items) >= count:
                break
            origin = p.get("origin") or RUHI_SOURCE_ID
            if origin == RUHI_SOURCE_ID:
                try:
                    resolved = _resolve_pinned_quote(p.get("text") or "")
                except RuntimeError:
                    # Index text no longer matches the corpus/manifest (stale
                    # index) — never suggest text the batch gate would reject.
                    continue
                quote, source = resolved["quote"], resolved["source"]
            else:
                chunk = (p.get("text") or "").strip()
                quote = _lib_excerpt(chunk)  # sentence-clean, card-length excerpt
                if not quote:
                    continue  # mid-sentence overlap fragment — never suggest it
                source = str(p.get("source") or "").strip()
                try:
                    _assert_excerpt_of(quote, chunk)  # same gate the pipeline applies
                except RuntimeError:
                    continue
            fit = _fit_or_shorten(quote, source)
            if fit is None:
                skipped_too_long += 1
                continue
            quote, shortened = fit
            if shortened:
                # Canonicalize/re-verify the shortened form through the same
                # gates the batch endpoint uses — what we hand back must be
                # guaranteed to verify verbatim on submit.
                if origin == RUHI_SOURCE_ID:
                    quote = _resolve_pinned_quote(quote)["quote"]
                else:
                    try:
                        _assert_excerpt_of(quote, chunk)
                    except RuntimeError:
                        continue
            key = _quote_lenient_key(quote)
            if key in seen:
                continue  # Ruhi and the library share passages — suggest each once
            seen.add(key)
            items.append({"quote": quote, "source": source, "score": p.get("score"),
                          "shortened": shortened, "origin": origin, "verified": True})

    # RISKY web tier — only when explicitly selected; always after verified
    # results, every item flagged verified=false.
    for url in web_urls:
        if len(items) >= count:
            break
        try:
            title, blocks = _fetch_web_page_text(url)
        except Exception as e:
            web_note = f"Could not fetch {url}: {e}"
            continue
        blocks = [b for b in blocks if not _WEB_BOILERPLATE_RE.search(b)]
        if not blocks:
            web_note = (f"No readable passages found at {url} — the page may need "
                        "JavaScript; try a page that shows the text directly.")
            continue
        # Citation label: the page title up to a separator, else the host.
        src_label = re.split(r"\s+[|–—-]\s+", title)[0].strip() if title else ""
        src_label = src_label or _origin_label(f"web:{url}")
        for score, block in _rank_web_blocks(topic, blocks, top_n=count * 2):
            if len(items) >= count:
                break
            block = _WEB_PARA_MARKER_RE.sub("", block).strip()
            if len(block) < 40:
                continue  # a bare address line ("O SON OF SPIRIT!") isn't a quote
            fit = _fit_or_shorten(_trim_card_quote(block), src_label)
            if fit is None:
                skipped_too_long += 1
                continue
            quote, shortened = fit
            if len(quote) < 40:
                continue  # trimmed down to just an address/heading — skip
            key = _quote_lenient_key(quote)
            if key in seen:
                continue
            seen.add(key)
            items.append({"quote": quote, "source": src_label, "score": score,
                          "shortened": shortened, "origin": f"web:{url}",
                          "verified": False})

    if not items:
        raise HTTPException(
            status_code=422,
            detail=f'The Librarian searched the selected sources for "{topic}" but found '
                   "nothing that fits on a card at readable size"
                   + (f". {web_note}" if web_note else " — try a different topic."))
    return {"topic": topic, "requested": count, "items": items,
            "skipped_too_long": skipped_too_long, "web_note": web_note}
# --- Quote card "redirect the team" — same three levers as bookmarks above,
# adapted for a product with no listing: requote (Ruhi Book 1 only, hard
# rule 11), repaint, or redo everything. ---


def _card_translation_dict(card_copy: dict) -> Optional[dict]:
    """Reconstructs translate_quote()'s dict shape from what's stored on the
    product, for re-rendering with an UNCHANGED translation (regenerate-card-
    image). Must include "code" — render_quote_card's font/RTL shaping keys
    off it, not off language_name."""
    if not card_copy.get("language"):
        return None
    return {
        "code": card_copy.get("language"),
        "name": card_copy.get("language_name"),
        "text": card_copy.get("translation_text"),
        "disclaimer_native": card_copy.get("translation_disclaimer_native"),
        "disclaimer_en": card_copy.get("translation_disclaimer_en"),
    }


class RegenerateCardQuoteRequest(BaseModel):
    guidance: str = ""   # e.g. "something about detachment instead of unity"

@router.post("/products/{product_id}/regenerate-card-quote")
def regenerate_card_quote(product_id: str, req: RegenerateCardQuoteRequest):
    """
    Replace ONLY the printed quote — same "redirect" contract as bookmarks'
    regenerate-quote, but sourced exclusively from Ruhi Book 1 (hard rule 11:
    retrieve_ruhi_book1, never the general library) and always verbatim, so
    quote_grounded stays True by construction. Re-renders on the SAME
    artwork, re-translates if the card has a translation, and re-scores with
    the card rubric. Always saves — a deliberate creative decision, not a
    quality-gated auto-improve.
    """
    from agents.card_compositor import render_quote_card
    from agents.reviewer import score_quote_card
    from agents.translator import translate_quote

    product = _load_product_or_404(product_id)
    _require_card(product)
    card_copy = json.loads(product.get("listing_copy") or "{}")
    theme = product.get("theme") or ""
    image_path = product.get("image_url", "")
    old_quote = card_copy.get("quote", "")
    language = card_copy.get("language")

    # Honor the sources the card was CREATED with (rule 11 update 2026-08-04)
    # — never widen. Web sources are excluded here by construction: there is
    # no verified index to re-pick from, so a web-only card can't requote.
    try:
        use_ruhi, lib_slugs, _web_urls = _parse_card_sources(card_copy.get("quote_sources"))
    except ValueError:
        use_ruhi, lib_slugs = True, []  # stored ids no longer resolvable → safe default

    query = req.guidance.strip() or theme
    passages = _card_retrieve(query, use_ruhi, lib_slugs, n_results=3)
    if not passages:
        raise HTTPException(
            status_code=422,
            detail="No matching passage found in this card's source index(es) for that "
                   "guidance. Try different wording, or rebuild the index "
                   "(scripts/ingest_ruhi_book1.py / scripts/ingest_texts.py). A card whose "
                   "only source was a web page has no verified pool to re-pick from — "
                   "run a new card instead.",
        )
    # First passage with a usable card-safe excerpt, preferring a different
    # quote — library overlap fragments (no sentence-clean text) are skipped.
    candidates = []
    for p in passages:
        p_origin = p.get("origin") or RUHI_SOURCE_ID
        q = (_trim_card_quote(p["text"]) if p_origin == RUHI_SOURCE_ID
             else _lib_excerpt(p["text"]))
        if q:
            candidates.append((p, q))
    if not candidates:
        raise HTTPException(
            status_code=422,
            detail="Every matching passage was a mid-sentence fragment — try different wording.")
    pick, new_quote = next(((p, q) for p, q in candidates if q != old_quote), candidates[0])
    pick_origin = pick.get("origin") or RUHI_SOURCE_ID
    # Same gates as the pipeline — manual requotes get no exemption.
    if pick_origin == RUHI_SOURCE_ID:
        _assert_ruhi_verbatim(new_quote)
    else:
        _assert_excerpt_of(new_quote, pick["text"])
    citation_src = str(pick.get("source") or "").strip()

    translation = None
    if language:
        try:
            translation = translate_quote(new_quote, language)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Translation failed: {e}")

    # New quote → new reflection face (same helper as the pipeline).
    reflection = _card_reflection(new_quote, product_id, language)

    try:
        rendered = render_quote_card(
            image_path, new_quote, citation_src,
            translation=translation, reflection=reflection,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not re-render the card: {e}")

    old_review = json.loads(product.get("reviewer_scores") or "{}")
    try:
        consult_transcript = json.loads(product.get("consultation") or "[]")
    except (json.JSONDecodeError, TypeError):
        consult_transcript = []
    review = score_quote_card(
        theme, new_quote, citation_src, True,
        front_image_path=rendered["front_path"], translation=translation,
        consultation_transcript=consult_transcript, previous_review=old_review or None,
        back_image_path=rendered["back_path"],
    )

    card_copy["quote"] = new_quote
    card_copy["quote_grounded"] = True
    card_copy["quote_verified"] = True          # re-picks always come from a verified index
    card_copy["quote_provenance"] = pick_origin
    card_copy["citation"] = citation_src
    if language:
        card_copy["language_name"] = translation.get("name")
        card_copy["translation_text"] = translation.get("text")
        card_copy["translation_disclaimer_native"] = translation.get("disclaimer_native")
        card_copy["translation_disclaimer_en"] = translation.get("disclaimer_en")
    card_copy["reflection_question"] = (reflection or {}).get("question") or ""
    card_copy["reflection_action"] = (reflection or {}).get("action") or ""
    card_copy["reflection_native"] = (reflection or {}).get("native") or None
    card_copy["variant_faces"] = _variant_faces_from_rendered(rendered)

    update_product(
        product_id, listing_copy=json.dumps(card_copy),
        reviewer_scores=json.dumps(review),
        front_image=rendered["front_path"], back_image=rendered["back_path"],
    )
    log_run(product_id, "librarian", "regenerate_card_quote", query[:200], new_quote[:200])

    return {
        "product_id": product_id,
        "old_quote": old_quote, "new_quote": new_quote, "citation": citation_src,
        "old_score": old_review.get("overall", 0), "new_score": review.get("overall", 0),
        "review": review,
        "front_image_web": _web_image_path(rendered["front_path"]),
        "back_image_web": _web_image_path(rendered["back_path"]),
    }


class RegenerateCardImageRequest(BaseModel):
    guidance: str   # required — e.g. "more vibrant colors, remove the lotus, add mountains"

@router.post("/products/{product_id}/regenerate-card-image")
def regenerate_card_image(product_id: str, req: RegenerateCardImageRequest):
    """
    Replace ONLY the artwork. Repaints from the original image prompt plus
    fresh guidance, keeps the existing (locked) quote/citation/translation,
    re-renders, and re-scores with the card rubric. Always saves.
    """
    from agents.artist import generate_image
    from agents.card_compositor import render_quote_card
    from agents.reviewer import score_quote_card

    if not req.guidance.strip():
        raise HTTPException(status_code=422,
                            detail="guidance is required — describe what should change about the artwork")

    product = _load_product_or_404(product_id)
    _require_card(product)
    card_copy = json.loads(product.get("listing_copy") or "{}")
    theme = product.get("theme") or ""
    old_image_prompt = product.get("image_prompt", "")
    quote = card_copy.get("quote", "")
    citation_src = card_copy.get("citation", "")
    quote_grounded = card_copy.get("quote_grounded", True)
    translation = _card_translation_dict(card_copy)
    # Quote unchanged → reuse stored reflection fields as-is.
    reflection = _reflection_from_card_copy(card_copy)

    new_prompt = f"{old_image_prompt}\n\nIMPORTANT new direction from Sheraj: {req.guidance}"
    try:
        gen = generate_image(new_prompt, "2:3")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Image generation error: {e}")
    new_image_path = gen.get("image_url", "")

    try:
        rendered = render_quote_card(
            new_image_path, quote, citation_src,
            translation=translation, reflection=reflection,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not render the new artwork: {e}")

    old_review = json.loads(product.get("reviewer_scores") or "{}")
    try:
        consult_transcript = json.loads(product.get("consultation") or "[]")
    except (json.JSONDecodeError, TypeError):
        consult_transcript = []
    review = score_quote_card(
        theme, quote, citation_src, quote_grounded,
        front_image_path=rendered["front_path"], translation=translation,
        consultation_transcript=consult_transcript, previous_review=old_review or None,
        back_image_path=rendered["back_path"],
    )

    card_copy["variant_faces"] = _variant_faces_from_rendered(rendered)
    update_product(
        product_id, image_url=new_image_path, image_prompt=new_prompt,
        listing_copy=json.dumps(card_copy),
        reviewer_scores=json.dumps(review),
        front_image=rendered["front_path"], back_image=rendered["back_path"],
    )
    log_run(product_id, "artist", "regenerate_card_image", req.guidance[:200], new_image_path[:200])

    return {
        "product_id": product_id,
        "old_score": old_review.get("overall", 0), "new_score": review.get("overall", 0),
        "review": review,
        "image_web": _web_image_path(new_image_path),
        "front_image_web": _web_image_path(rendered["front_path"]),
        "back_image_web": _web_image_path(rendered["back_path"]),
    }


def _redo_card(product_id: str, req: RegenerateAllRequest, progress,
              on_turn=None, request_human_input=None) -> dict:
    """
    Full redo of a quote card: re-run the ENTIRE card pipeline (Librarian,
    Artist, consultation, translation, Card Compositor, Reviewer) from the
    same theme/language, optionally steered by fresh guidance, overwriting
    this product's row in place — mirrors bookmarks' _redo_product.
    """
    product = _load_product_or_404(product_id)
    card_copy = json.loads(product.get("listing_copy") or "{}")
    theme = product.get("theme") or ""
    language = card_copy.get("language")

    # If Sheraj had pinned the quote on this card, a "redo everything" keeps it
    # pinned — a redo changes the artwork/framing, never his chosen words. The
    # stored quote is the exact pinned text (quote_pinned was set at save time),
    # so re-supplying it re-verifies it verbatim and re-locks it for the redo.
    pinned_quote = card_copy.get("quote", "") if card_copy.get("quote_pinned") else ""

    progress("Redoing the whole card from scratch...")
    card_req = CardPipelineRequest(theme=theme, language=language, guidance=req.guidance,
                                   pinned_quote=pinned_quote)
    return _run_card_pipeline(card_req, progress, on_turn, request_human_input,
                              existing_product_id=product_id)


@router.post("/products/{product_id}/regenerate-card-all")
def regenerate_card_all(product_id: str, req: RegenerateAllRequest):
    """
    Background job: redo the ENTIRE quote card (artwork, quote, translation,
    score) from its theme plus fresh guidance, overwriting this product in
    place. Returns {job_id} immediately; poll GET /pipeline/status/{job_id}.
    """
    _require_card(_load_product_or_404(product_id))
    job_id = _start_job(
        "redo-card",
        lambda progress, on_turn, ask: _redo_card(product_id, req, progress, on_turn, ask),
    )
    return {"job_id": job_id, "status": "running"}
