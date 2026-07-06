"""
Scribe agent — writes Etsy listing copy for Bahá'í-inspired bookmarks.
Runs on the local model (Ollama) per the routing directive; the paid xAI API
is reserved for the Artist and Reviewer, who need vision.
"""

import json
import re
from pathlib import Path
from dotenv import load_dotenv

from agents.router import call_llm
from agents.system_prompt_builder import build_system_prompt

load_dotenv(dotenv_path=str(Path(__file__).parent.parent / ".env"))

# Trustworthiness guardrails, injected into every write/revise prompt. Each of
# these claims has cost real Reviewer rounds: the model calls a printed design
# "handcrafted" or asserts motif counts nobody verified, and the Reviewer then
# spends its one recommendation correcting honesty instead of improving copy.
_HONESTY_RULES = (
    "\nHonesty rules (Trustworthiness — non-negotiable):\n"
    "- This is a digitally designed, made-to-order art print on cardstock. NEVER call it "
    "handcrafted, handmade, hand-painted, or one-of-a-kind.\n"
    "- The artwork is created with AI image-generation tools, art-directed and curated by "
    "Sheraj. NEVER imply it was hand-illustrated, hand-drawn, or painted by a person — the "
    "buyer must be able to tell what they are buying.\n"
    "- NEVER make numeric claims about the artwork — no counts of petals, rays, arches or "
    "stars, and no references to the numbers 9 or 19. The final image is not guaranteed to "
    "contain exact counts, so describe the motifs without numbers.\n"
    "- Every factual claim must match the product details given. When unsure, leave it out.\n"
)


def write_listing(
    theme: str,
    image_prompt: str,
    citations: list[dict] = None,
    image_url: str = None,
    consultation_context: str = "",
    verified_quote: str = "",
    quote_grounded: bool = True,
) -> dict:
    """
    Write a complete Etsy listing for a Bahá'í-inspired bookmark.
    quote_grounded controls the honesty of the quote framing: only claim
    "verified by the Librarian" when the Librarian's own verdict actually
    said GROUNDED — an ungrounded quote passed off as verified is a
    Trustworthiness violation the Scribe would otherwise bake in unknowingly.
    Returns: {title, description, tags, materials, price_note}
    """
    system_prompt = build_system_prompt("scribe", "copy")

    citation_block = ""
    if citations:
        citation_block = (
            "\n\nSpiritual citations to weave in naturally "
            "(paraphrase or adapt; if quoting directly, credit the author):\n"
        )
        for c in (citations or [])[:2]:
            citation_block += f'  — "{c.get("text", "")[:180]}" ({c.get("source", "")})\n'

    consult_block = f"\n\n{consultation_context}\n" if consultation_context else ""

    if verified_quote and quote_grounded:
        quote_field = (
            f'  "bookmark_quote": "USE EXACTLY THIS TEXT — verified by the Librarian from actual '
            f'Bahá\'í writings (do not change it): {verified_quote}",\n'
        )
    elif verified_quote:
        quote_field = (
            f'  "bookmark_quote": "USE EXACTLY THIS TEXT — the team\'s chosen phrase for this piece '
            f'(do not change it; do NOT describe it as a direct scriptural quotation since it was not '
            f'verified against a source): {verified_quote}",\n'
        )
    else:
        quote_field = (
            '  "bookmark_quote": "A verse or phrase printed on the bookmark face — '
            "2 to 4 lines of poetry or prose, 120–180 characters total. "
            "Spiritually uplifting, drawn from the theme or citations. "
            "No quotation marks in the value itself.\",\n"
        )

    user_message = (
        f"Write a complete Etsy listing for a Bahá'í-inspired bookmark.\n\n"
        f"Theme: {theme}\n"
        f"Image description: {image_prompt[:400]}\n"
        f"{citation_block}"
        f"{consult_block}\n"
        "Product details:\n"
        "- Artwork created with AI image-generation tools, art-directed and curated by "
        "Sheraj, a Bahá'í designer\n"
        "- 2\" × 6\" printed on premium cardstock, available laminated\n"
        "- Designed in Canva, printed to order — each one is made with care\n"
        "- Ships within 3–5 business days\n"
        f"{_HONESTY_RULES}\n"
        "Return ONLY this JSON object — no other text:\n"
        "{\n"
        '  "title": "Etsy listing title — max 80 chars, natural keywords, no ALL CAPS",\n'
        '  "description": "EXACTLY 3 short paragraphs, separated by a blank line, each 1-3 '
        "sentences — no more, no fewer. Paragraph 1: a spiritual/emotional hook tied to the "
        "theme. Paragraph 2: what the buyer receives and what Bahá'í-inspired means here. "
        "Paragraph 3: size, materials, and shipping. Warm and honest, not salesy — every "
        "sentence must earn its place; do not pad a paragraph past 3 sentences to sound "
        "fuller.\",\n"
        + quote_field +
        '  "tags": ["up to 13 Etsy tags", "single words or 2-3 word phrases only"],\n'
        '  "materials": ["Premium cardstock", "Soy-based inks"],\n'
        '  "price_note": "Suggested retail price with brief reasoning (a suggestion shown to '
        'Sheraj only — the actual shop price is set by his pricing policy, not this text)"\n'
        "}"
    )

    return _call_and_parse(system_prompt, user_message, fallback_title=theme)


# Map typographic variants to plain ASCII so the Reviewer's quoted 'find'
# snippets match the listing even when curly quotes or dashes differ.
_TYPO_NORM = str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'",
                            "—": "-", "–": "-"})


def _find_span(text: str, find: str) -> int:
    """Locate `find` in `text`, tolerant of case and typographic quote/dash variants."""
    if find in text:
        return text.find(find)
    return text.translate(_TYPO_NORM).lower().find(find.translate(_TYPO_NORM).lower())


def apply_edits(listing: dict, edits: list) -> tuple[dict, list, list]:
    """
    Apply the Reviewer's surgical find-and-replace edits to a listing in code.
    This exists because revision compliance previously depended on the small
    local model obeying prose instructions — observed in production returning
    the byte-identical listing three attempts in a row. Mechanical application
    cannot disobey.

    Returns (edited_listing, unapplied_edits, rejected_locked). unapplied edits
    are ones whose 'find' string didn't match — a writer can still attempt
    them in prose. rejected_locked edits targeted bookmark_quote, which is
    Librarian-locked and can NEVER be edited by the Scribe either — these must
    be reported back to the Reviewer as blocked, not silently dropped (a prior
    bug let them vanish and get miscounted as "applied", which told the
    Reviewer its fix landed when the quote never actually changed).
    """
    result = dict(listing)
    unapplied = []
    rejected_locked = []
    for e in edits or []:
        if not isinstance(e, dict):
            continue
        field = (e.get("field") or "description").strip().lower()
        find = str(e.get("find") or "")
        replace = str(e.get("replace") or "")
        if field == "bookmark_quote":  # quote is Librarian-locked — never editable
            rejected_locked.append(e)
            continue
        if not find:
            unapplied.append(e)
            continue
        if field == "tags" and isinstance(result.get("tags"), list):
            tags = result["tags"]
            matched = [t for t in tags if isinstance(t, str) and _find_span(t, find) >= 0]
            if matched:
                kept = [t for t in tags if t not in matched]
                if replace and replace not in kept:
                    kept.append(replace)
                result["tags"] = kept
            else:
                unapplied.append(e)
        elif isinstance(result.get(field), str):
            idx = _find_span(result[field], find)
            if idx >= 0:
                result[field] = result[field][:idx] + replace + result[field][idx + len(find):]
            else:
                unapplied.append(e)
        else:
            unapplied.append(e)

    # Tidy the seams left by deletions
    for k in ("title", "description"):
        if isinstance(result.get(k), str):
            v = re.sub(r"[ \t]{2,}", " ", result[k])
            v = re.sub(r" +([,.;:!?])", r"\1", v)
            v = re.sub(r"([,;:]) *([,.;:])", r"\2", v)
            v = re.sub(r"\n{3,}", "\n\n", v)
            result[k] = v.strip()
    return result, unapplied, rejected_locked


def revise_listing_light(current_listing: dict, instructions: list[str],
                         verified_quote: str = "") -> dict:
    """
    Minimal-payload revision for the local model: the current listing plus a
    numbered list of edits — no consultation context, no image description, no
    schema essay. The heavy thinking already happened in the Reviewer; this is
    a copy-editing task, and small models execute it far more reliably when
    the prompt contains nothing else to attend to.
    """
    cur = {k: current_listing.get(k) for k in
           ("title", "description", "bookmark_quote", "tags", "materials", "price_note")
           if current_listing.get(k) is not None}
    numbered = "\n".join(f"{i + 1}. {ins}" for i, ins in enumerate(instructions))
    quote_note = ('\nDo not change "bookmark_quote" — it is locked.\n'
                  if verified_quote else "")
    system_prompt = (
        "You are a precise copy editor. You apply the requested edits faithfully "
        "and change nothing else."
    )
    user_message = (
        "Here is an Etsy listing as JSON:\n\n"
        + json.dumps(cur, ensure_ascii=False, indent=2)
        + "\n\nApply these edits EXACTLY. Keep everything else word-for-word identical:\n"
        + numbered
        + "\n" + quote_note +
        "\nThe description must stay EXACTLY 3 short paragraphs (1-3 sentences each), separated "
        "by a blank line. If an edit adds a sentence, fold it into the most relevant existing "
        "paragraph rather than creating a 4th.\n"
        "\nReturn the complete edited listing as a JSON object with the same fields."
    )
    result = _call_and_parse(system_prompt, user_message, fallback_title="")
    if not isinstance(result, dict) or _is_unusable(result, ""):
        # Editor failed — better to keep the current listing than merge junk
        return dict(current_listing)
    # Never let the editor drop fields — overlay its output on the original
    merged = dict(current_listing)
    for k, v in result.items():
        if v not in (None, "", []):
            merged[k] = v
    if verified_quote:
        merged["bookmark_quote"] = verified_quote
    return merged


def _is_unusable(result: dict, theme: str) -> bool:
    """
    True if a parsed listing is too thin to ship. Catches both total failure
    (empty fields) and partial failure — e.g. the local model occasionally
    emits just the opening "{" of the JSON object and stops, which is a
    non-empty but useless string that a plain truthiness check would miss.
    """
    title = (result.get("title") or "").strip()
    description = (result.get("description") or "").strip()
    return len(title) < 5 or len(description) < 40 or title == theme.strip()


# Deterministic scrub of claims the model keeps making despite the prompt rules.
# The product is a made-to-order digital print — 'handcrafted' etc. is false and
# costs a full Reviewer round every time it slips through.
_CLAIM_FIXES = [
    (re.compile(r'\bhand[-\s]?crafted\b', re.IGNORECASE), 'made-to-order'),
    (re.compile(r'\bhand[-\s]?made\b',    re.IGNORECASE), 'made-to-order'),
    (re.compile(r'\bhand[-\s]?painted\b', re.IGNORECASE), 'artfully designed'),
    (re.compile(r'\bone[-\s]of[-\s]a[-\s]kind\b', re.IGNORECASE), 'thoughtfully designed'),
]

# Exact repeated-motif counts ("nine-pointed star", "9 gold rays") are claims
# NO agent in this pipeline can actually verify: image generators cannot
# guarantee an exact repetition count, and vision models reliably hallucinate
# the "expected" count (e.g. the real Bahá'í nine-pointed star) instead of
# recounting the rendered pixels. One real run shipped "a nine-pointed star"
# for artwork that actually had twelve points — the Reviewer had asserted the
# count, the listing repeated it, and the Reviewer then scored its own
# fabrication as "matches the actual image exactly." This scrub is the
# backstop that can't be argued out of: it runs on every listing regardless
# of which agent (or which application path — mechanical edit or light-editor
# instruction) introduced the claim.
_NUMBER_WORDS = ("one two three four five six seven eight nine ten eleven twelve thirteen "
                 "fourteen fifteen sixteen seventeen eighteen nineteen twenty").split()
_NUM_PATTERN = "|".join(_NUMBER_WORDS) + r"|\d{1,2}"
_MOTIF_ADJ_SUFFIXES = ["pointed", "rayed", "armed", "petaled", "petalled", "sided", "spoked"]
_MOTIF_NOUNS = ("point points ray rays arm arms petal petals arch arches tile tiles path paths "
                "star stars bloom blooms flower flowers panel panels border borders column columns "
                "beam beams unit units element elements repeat repeats leaf leaves").split()

_MOTIF_ADJ_RE = re.compile(
    rf"\b({_NUM_PATTERN})-({'|'.join(_MOTIF_ADJ_SUFFIXES)})\b", re.IGNORECASE
)
_MOTIF_COUNT_RE = re.compile(
    rf"\b({_NUM_PATTERN})\s+((?:[a-zA-Z]+\s+){{0,2}}(?:{'|'.join(_MOTIF_NOUNS)}))\b", re.IGNORECASE
)


def _strip_motif_counts(text: str) -> str:
    def _adj_sub(m):
        repl = f"multi-{m.group(2).lower()}"
        return repl[0].upper() + repl[1:] if m.group(0)[0].isupper() else repl
    text = _MOTIF_ADJ_RE.sub(_adj_sub, text)

    def _count_sub(m):
        # Drop just the leading number token; whatever follows (an optional
        # adjective plus the motif noun) still reads naturally without it.
        rest = m.group(0)[len(m.group(1)):].lstrip()
        return rest[0].upper() + rest[1:] if m.group(0)[0].isupper() else rest
    return _MOTIF_COUNT_RE.sub(_count_sub, text)


def _enforce_paragraph_limit(text: str, max_paragraphs: int = 3) -> str:
    """
    Merge (never delete) any paragraphs beyond max_paragraphs into the last
    kept one. A deterministic backstop for the standardized 3-short-paragraph
    description format: the Scribe's prompt asks for exactly 3, but a small
    local model sometimes drifts to 4-5 anyway — the same class of compliance
    gap _sanitize_claims already exists to catch for other rules. Merging
    instead of truncating means no sentence is silently lost, only reflowed.
    """
    paragraphs = [p.strip() for p in re.split(r'\n\s*\n', text.strip()) if p.strip()]
    if len(paragraphs) <= max_paragraphs:
        return text
    kept = paragraphs[:max_paragraphs - 1]
    kept.append(" ".join(paragraphs[max_paragraphs - 1:]))
    return "\n\n".join(kept)


def _sanitize_claims(result: dict) -> dict:
    """Replace known-false product claims in all text fields of a parsed listing."""
    def _fix(text: str) -> str:
        for pattern, replacement in _CLAIM_FIXES:
            def _sub(m, _r=replacement):
                # Preserve the capitalisation of the claim being replaced
                return _r[0].upper() + _r[1:] if m.group()[0].isupper() else _r
            text = pattern.sub(_sub, text)
        return _strip_motif_counts(text)

    for key in ("title", "description", "price_note"):
        if isinstance(result.get(key), str):
            result[key] = _fix(result[key])
    if isinstance(result.get("description"), str):
        result["description"] = _enforce_paragraph_limit(result["description"])
    if isinstance(result.get("tags"), list):
        result["tags"] = [_fix(t) if isinstance(t, str) else t for t in result["tags"]]
    return result


def _call_and_parse(system_prompt: str, user_message: str, fallback_title: str) -> dict:
    """
    Call the Scribe's LLM and parse the JSON listing. Retries (up to 3 attempts
    total) if the result is unusable — the local model occasionally cuts off
    right after the opening brace on long, complex prompts (see router.py).
    Free local retries are cheap insurance; keeps the best attempt seen.
    """
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user",   "content": user_message},
    ]
    best = None
    for _ in range(3):
        raw = call_llm("scribe", messages, temperature=0.7, max_tokens=1900, json_mode=True).strip()
        result = _parse_json(raw, fallback_title=fallback_title)
        if not _is_unusable(result, fallback_title):
            return _sanitize_claims(result)
        if best is None or len(result.get("description") or "") > len(best.get("description") or ""):
            best = result

    return _sanitize_claims(best) if best else best


def _repair_truncated_json(raw: str) -> str | None:
    """
    Close an unterminated string and any open braces/brackets so a listing the
    model cut off mid-generation still parses — a partial description beats
    shipping the raw fragment as the whole listing.
    """
    start = raw.find("{")
    if start == -1:
        return None
    s = raw[start:]
    stack = []
    in_str = False
    escaped = False
    for ch in s:
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append(ch)
        elif ch in "}]" and stack:
            stack.pop()
    if in_str:
        s += '"'
    s = re.sub(r'[,\s]+$', '', s)
    # A dangling key with no value ("key": ) can't be closed sensibly — drop it
    s = re.sub(r',?\s*"[^"]*"\s*:\s*$', '', s)
    while stack:
        s += "}" if stack.pop() == "{" else "]"
    return s


def _parse_json(raw: str, fallback_title: str = "") -> dict:
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        repaired = _repair_truncated_json(raw)
        if repaired:
            try:
                result = json.loads(repaired)
                if isinstance(result, dict):
                    return result
            except json.JSONDecodeError:
                pass
    return {
        "title": fallback_title,
        "description": raw,
        "tags": [],
        "materials": [],
        "price_note": "",
    }
