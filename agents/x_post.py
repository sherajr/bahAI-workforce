"""
Post to X pipeline — a giveaway outreach post for @peaceAntz, never sold, never
auto-posted. Mirrors the style of _run_full_pipeline and _run_card_pipeline in
agents/api.py: Librarian retrieval -> Artist artwork -> real multi-agent
consultation (with the same round-2 human pause) -> quote locked from what the
team actually discussed -> Scribe draft -> Reviewer QA revision loop, routed
to whichever agent can actually fix what's wrong.

  1. Coordinator Briefing — frozen in code, no LLM call (CLAUDE.md rule 10:
     framing stays hand-authored, never LLM-generated).
  2. Librarian research   — retrieves up to 3 verbatim candidate passages,
     filtered to the 5 allowed authors. No quote is locked yet.
  3. Artist image         — generated before consultation (consultation needs
     a real image for its vision-based turns, same ordering as the bookmark
     and card pipelines).
  4. Consultation         — agents.consultation.run_consultation, product=
     "x_post": the same 3-round dialogue + round-2 human pause as bookmarks
     and cards. The team genuinely deliberates over the retrieved candidates.
  5. Quote lock           — AFTER consultation, whatever the team converged on
     is snapped to the nearest verbatim candidate (_best_matching_quote, same
     bag-of-words technique as api._best_matching_citation for quote cards)
     — never the team's own possibly-reworded text. Locking a quote BEFORE
     consultation (an earlier version of this pipeline did that) let the team
     spend 3 rounds discussing one quote while the Scribe drafted around a
     completely different, undiscussed one — a real bug caught live, 2026-07.
  6. Scribe draft         — writes the tweet honoring the consultation's
     agreed direction/tone/key elements.
  7. Reviewer QA          — deterministic mechanical checks (preamble, exact
     quote, attribution, length) plus a constitution-scored quality pass that
     also sees the consultation transcript. Below 7/10, a mini team
     consultation (agents.consultation.run_x_post_revision_consultation)
     decides WHICH agent should act — Scribe (revise_text), Artist (repaint),
     or Librarian (requote) — instead of always defaulting to the Scribe,
     which previously left the Reviewer asking for image or quote changes
     that had no way to actually happen. Max 2 revisions, best kept.

`include_quote` (default True) toggles the whole run: WITH a quote, step 5
locks a completely unaltered verbatim excerpt (shortenable only with "...").
WITHOUT one, the Librarian still retrieves passages and the team still
discusses them in consultation as genuine inspiration, but nothing is quoted
or attributed in the tweet — the Scribe writes an original reflection on the
topic instead (scribe_write_tweet_no_quote), and the Reviewer's mechanical
checks flip accordingly (no quotation marks, no named author at all, rather
than requiring an exact-matching attributed quote).

Posting to X itself (post_tweet) is a separate, human-approved step — see
api.py's /x-post/approve/{id}.
"""

import os
import re
from pathlib import Path

from dotenv import load_dotenv

from agents.artist import build_x_post_image_prompt, generate_image
from agents.librarian import retrieve
from agents.reviewer import _parse_review
from agents.router import call_llm
from agents.system_prompt_builder import build_system_prompt

load_dotenv(dotenv_path=str(Path(__file__).parent.parent / ".env"), override=True)

# The only five authors a post may ever quote (owner directive) — the local
# 7-text index only contains the first two, so this filter is mostly a hard
# safety net against a future index expansion ever slipping in an unverified
# or off-limits source.
ALLOWED_AUTHORS = ("Bahá'u'lláh", "'Abdu'l-Bahá", "Shoghi Effendi", "The Báb",
                   "Universal House of Justice")

TWEET_SOFT_TARGET = 200
TWEET_HARD_MAX = 280
PASS_THRESHOLD = 7.0
MAX_ATTEMPTS = 3  # 1 initial draft + 2 revisions

# AI-artwork/provenance disclosure — code-appended, never LLM-written (same
# class as etsy.AI_ART_DISCLOSURE and the card pipeline's CARD_ART_DISCLOSURE).
# Appended at POST time (post_tweet); drafts must leave room for it
# (TWEET_DRAFT_MAX) so the disclosure is never silently dropped.
AI_DISCLOSURE_SUFFIX = " · AI-assisted art"
TWEET_DRAFT_MAX = TWEET_HARD_MAX - len(AI_DISCLOSURE_SUFFIX)


# --- Phase 1: Coordinator Briefing (frozen, no LLM) -------------------------

def coordinator_briefing(topic: str) -> dict:
    """
    Emitted before any LLM call. Under 100 words total. Not LLM-generated —
    frozen framing, same discipline as consultation.py's _PRODUCT_FRAMES.
    """
    topic = topic.strip()
    return {
        "theme_sentence": f'A short reflection on "{topic}", grounded in a verified Bahá\'í teaching.',
        "librarian_keywords": topic,
        "scribe_tone": "Warm, grounded, and sincere — a brief reflection, never a sermon or a sales pitch.",
        "artist_mood": "Serene and luminous — nature, light, gardens, sky, or water; nothing esoteric that needs explaining.",
    }


# --- Phase 2: Librarian research --------------------------------------------

_SENTENCE_END_RE = re.compile(r'[.!?](?=\s|$)')


def _trim_quote(text: str, limit: int = 220) -> str:
    """Trim to a tweet-appropriate excerpt at a sentence boundary — never a
    mid-sentence hard cut (same discipline as api._trim_card_quote)."""
    text = text.strip()
    if len(text) <= limit:
        return text
    ends = [m.end() for m in _SENTENCE_END_RE.finditer(text)]
    if not ends:
        return text
    fits = [e for e in ends if e <= limit]
    cut = fits[-1] if fits else ends[0]
    return text[:cut].strip()


def librarian_research(topic: str, keywords: str) -> dict:
    """
    Deterministic: the LLM never picks or phrases quote text — only the
    RETRIEVED verbatim passages are ever candidates, filtered to the 5
    allowed authors. Returns {quotes, formatted, none_found}. `formatted`
    is the machine-parsed QUOTE:/BY:/WHY: block the spec calls for; `quotes`
    is the same data as a list for downstream code.
    """
    query = (keywords or topic).strip()
    try:
        passages = retrieve(query, n_results=5) or []
    except Exception:
        passages = []

    quotes = []
    for p in passages:
        source = str(p.get("source") or "").strip()
        author = source.split(",")[0].strip() if source else ""
        if author not in ALLOWED_AUTHORS:
            continue
        text = _trim_quote(str(p.get("text") or "").strip())
        if not text:
            continue
        quotes.append({
            "quote": text,
            "by": author,
            "source": source,
            "why": f'Speaks directly to "{topic.strip()}" from the verified writings.',
        })
        if len(quotes) == 3:
            break

    formatted = (
        "\n\n".join(f"QUOTE: {q['quote']}\nBY: {q['by']}\nWHY: {q['why']}" for q in quotes)
        if quotes else "NONE_FOUND"
    )
    return {"quotes": quotes, "formatted": formatted, "none_found": not quotes}


# --- Phase 3: Scribe draft ---------------------------------------------------

def scribe_select_quote(topic: str, tone: str, quotes: list[dict]) -> dict:
    """
    The model picks the best candidate by INDEX ONLY — it never re-types the
    quote or author, so the locked quote can never drift from what the
    Librarian actually retrieved (same discipline as the bookmark_quote lock).
    """
    if not quotes:
        return {"quote": "", "by": "", "source": ""}
    if len(quotes) == 1:
        return quotes[0]

    system_prompt = build_system_prompt("scribe", "copy")
    listing = "\n".join(f'{i + 1}. "{q["quote"]}" — {q["by"]}' for i, q in enumerate(quotes))
    user_message = (
        f"Topic: {topic}\nTone: {tone}\n\nCandidate quotes:\n{listing}\n\n"
        "Reply with ONLY the number of the single best quote for a short X (Twitter) post "
        "on this topic. No words, no punctuation — just the digit."
    )
    raw = call_llm("scribe", [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ], temperature=0.3, max_tokens=10).strip()
    m = re.search(r"\d+", raw)
    idx = int(m.group()) - 1 if m else 0
    if not (0 <= idx < len(quotes)):
        idx = 0
    return quotes[idx]


def _best_matching_quote(proposed: str, candidates: list[dict]) -> dict:
    """
    Snap a freely-discussed quote (the consultation team's real, converged-on
    conclusion) to the nearest verbatim candidate the Librarian actually
    retrieved — same bag-of-words technique as api._best_matching_citation
    for quote cards, and for the same reason: a multi-round dialogue may
    condense or lightly reword a quote over 3 rounds of genuine deliberation,
    but what SHIPS must always be one of the retrieved verbatim candidates,
    never the team's own possibly-drifted wording. This is what the pipeline
    locks quote/author to AFTER consultation — picking a candidate before
    consultation even starts (an earlier version of this pipeline did that)
    let the team spend 3 rounds converging on one quote while the Scribe
    drafted around a completely different, undiscussed one; the Reviewer
    then flagged a mismatch neither revise_text nor repaint could ever fix
    (observed live, 2026-07).
    """
    def norm(s: str) -> set:
        return set(re.sub(r"[^a-z0-9 ]", " ", s.lower()).split())

    proposed_words = norm(proposed)
    best, best_score = candidates[0], -1
    for c in candidates:
        score = len(proposed_words & norm(c["quote"]))
        if score > best_score:
            best, best_score = c, score
    return best


def brief_to_direction_note(brief: dict) -> str:
    """
    Turn the consultation's synthesized brief (agents.consultation._synthesize_brief)
    into a short instruction for the Scribe — the team's round-2 settled
    decision, honored the same way the bookmark pipeline honors it (rule 7:
    round-2 is binding, never silently ignored downstream).
    """
    if not brief:
        return ""
    parts = []
    if brief.get("agreed_direction"):
        parts.append(f"Team's agreed direction: {brief['agreed_direction']}")
    if brief.get("tone"):
        parts.append(f"Agreed tone: {brief['tone']}")
    if brief.get("key_elements"):
        parts.append("Reference these visual elements if they fit naturally: "
                     + "; ".join(brief["key_elements"]))
    return " ".join(parts)


def scribe_write_tweet_no_quote(topic: str, tone: str, inspiration: list[dict],
                                direction_note: str = "") -> str:
    """
    The "without an authoritative quote" mode: the Librarian's retrieved
    passages are shown to the Scribe as background inspiration ONLY — the
    tweet must be the Scribe's own original reflection on the topic, never a
    reproduction (verbatim or lightly reworded) of the retrieved text, and
    never attributed to Bahá'u'lláh/'Abdu'l-Bahá/Shoghi Effendi/The Báb/the
    Universal House of Justice, since nothing of theirs is actually being
    quoted. Same 'output ONLY the tweet' contract as scribe_write_tweet."""
    system_prompt = build_system_prompt("scribe", "copy")
    direction_block = f"\n{direction_note}\n" if direction_note else ""
    inspiration_block = ""
    if inspiration:
        inspiration_block = "\n\nBackground inspiration ONLY (do not quote or paraphrase closely — for context only):\n"
        for q in inspiration[:2]:
            inspiration_block += f'  — a teaching from {q["by"]}, on: {q["quote"][:120]}\n'
    user_message = (
        f"Write one tweet for @peaceAntz's X account about: {topic}\n"
        f"Tone: {tone}\n"
        f"{direction_block}"
        f"{inspiration_block}\n\n"
        "This post does NOT quote anyone directly — it's the team's own original reflection, "
        "inspired by (but not reproducing) the background above.\n\n"
        "Rules:\n"
        f"- Aim for about {TWEET_SOFT_TARGET} characters; {TWEET_DRAFT_MAX} is the hard maximum "
        f"(a short AI-art disclosure is appended at post time).\n"
        "- No quotation marks, and do not name or attribute any words to Bahá'u'lláh, "
        "'Abdu'l-Bahá, Shoghi Effendi, The Báb, or the Universal House of Justice — this is your "
        "own reflection, not their words.\n"
        "- Warm and grounded, never salesy.\n"
        "- Output ONLY the tweet text. The first character of your reply must be the first "
        'character of the tweet — no preamble, no "Here is the tweet:", no explanation before '
        "or after.\n"
    )
    return call_llm("scribe", [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ], temperature=0.8, max_tokens=180).strip()


def scribe_revise_tweet_no_quote(topic: str, tone: str, inspiration: list[dict],
                                 previous_tweet: str, feedback: str) -> str:
    """Revision pass for the no-quote mode's Phase 4 loop."""
    system_prompt = build_system_prompt("scribe", "copy")
    inspiration_block = ""
    if inspiration:
        inspiration_block = "\n\nBackground inspiration ONLY (do not quote or paraphrase closely):\n"
        for q in inspiration[:2]:
            inspiration_block += f'  — a teaching from {q["by"]}, on: {q["quote"][:120]}\n'
    user_message = (
        f"Revise this tweet for @peaceAntz's X account about: {topic}\nTone: {tone}\n\n"
        f"Previous draft:\n{previous_tweet}\n\n"
        f"Reviewer's feedback (address this specifically):\n{feedback or '(no feedback given)'}\n"
        f"{inspiration_block}\n\n"
        "This post does NOT quote anyone directly — keep it an original reflection.\n\n"
        "Rules:\n"
        f"- Aim for about {TWEET_SOFT_TARGET} characters; {TWEET_DRAFT_MAX} is the hard maximum "
        f"(a short AI-art disclosure is appended at post time).\n"
        "- No quotation marks, and do not name or attribute any words to Bahá'u'lláh, "
        "'Abdu'l-Bahá, Shoghi Effendi, The Báb, or the Universal House of Justice.\n"
        "- Output ONLY the revised tweet text. The first character of your reply must be the "
        "first character of the tweet — no preamble, no labels, no explanation.\n"
    )
    return call_llm("scribe", [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ], temperature=0.8, max_tokens=180).strip()


def scribe_write_tweet(topic: str, tone: str, quote: str, author: str,
                       direction_note: str = "") -> str:
    """Writes the tweet. The whole raw reply IS the tweet — no delimiter, no
    JSON, enforced entirely by the prompt (same convention as
    artist.build_image_prompt's 'Output ONLY the prompt'). direction_note, if
    given, is the team's consultation brief (see brief_to_direction_note)."""
    system_prompt = build_system_prompt("scribe", "copy")
    direction_block = f"\n{direction_note}\n" if direction_note else ""
    user_message = (
        f"Write one tweet for @peaceAntz's X account about: {topic}\n"
        f"Tone: {tone}\n"
        f"{direction_block}\n"
        f"Weave in these exact words from a verified Bahá'í source (you may shorten with "
        f'"..." if needed, but every word you keep must match this text exactly):\n'
        f'"{quote}"\n— {author}\n\n'
        "Rules:\n"
        f"- Aim for about {TWEET_SOFT_TARGET} characters; {TWEET_DRAFT_MAX} is the hard maximum "
        f"(a short AI-art disclosure is appended at post time).\n"
        f"- Include attribution to {author}.\n"
        "- Warm and grounded, never salesy.\n"
        "- Output ONLY the tweet text. The first character of your reply must be the first "
        'character of the tweet — no preamble, no "Here is the tweet:", no explanation before '
        "or after.\n"
    )
    return call_llm("scribe", [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ], temperature=0.7, max_tokens=180).strip()


def scribe_revise_tweet(topic: str, tone: str, quote: str, author: str,
                        previous_tweet: str, feedback: str) -> str:
    """Revision pass for the Phase 4 loop — same 'output ONLY the tweet' contract."""
    system_prompt = build_system_prompt("scribe", "copy")
    user_message = (
        f"Revise this tweet for @peaceAntz's X account about: {topic}\nTone: {tone}\n\n"
        f"Previous draft:\n{previous_tweet}\n\n"
        f"Reviewer's feedback (address this specifically):\n{feedback or '(no feedback given)'}\n\n"
        "The locked quote you must keep using (verbatim; may shorten with \"...\"):\n"
        f'"{quote}"\n— {author}\n\n'
        "Rules:\n"
        f"- Aim for about {TWEET_SOFT_TARGET} characters; {TWEET_DRAFT_MAX} is the hard maximum "
        f"(a short AI-art disclosure is appended at post time).\n"
        f"- Include attribution to {author}.\n"
        "- Output ONLY the revised tweet text. The first character of your reply must be the "
        "first character of the tweet — no preamble, no labels, no explanation.\n"
    )
    return call_llm("scribe", [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ], temperature=0.7, max_tokens=180).strip()


# --- Phase 4: Reviewer QA -----------------------------------------------------

_PREAMBLE_RE = re.compile(
    r"^(here(\'s| is)\b|sure[,!]?\b|certainly\b|acknowledged\b|of course\b|tweet:|draft:|\*\*)",
    re.IGNORECASE,
)


def _check_preamble(tweet: str) -> tuple[bool, str]:
    if _PREAMBLE_RE.match(tweet.strip()):
        return False, f'tweet opens with a leaked preamble/label: "{tweet[:40]}..."'
    return True, "no preamble detected"


_QUOTE_SPAN_RE = re.compile(r'["“]([^"”]{8,})["”]')


def _norm_words(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", s.lower())).strip()


def _check_quote_exact(tweet: str, quote: str) -> tuple[bool, str]:
    """Mechanical, not LLM-trusted (CLAUDE.md rule 4's discipline): any span
    inside quotation marks must match a substring of the locked quote,
    word-for-word (ellipsis-split segments checked independently)."""
    spans = _QUOTE_SPAN_RE.findall(tweet)
    if not spans:
        return True, "no quoted span to verify (tweet paraphrases without quotation marks)"
    quote_norm = _norm_words(quote)
    for span in spans:
        for part in re.split(r"\.{3}|…", span):
            part_norm = _norm_words(part)
            if part_norm and part_norm not in quote_norm:
                return False, f'quoted span "{part.strip()}" does not match the locked quote verbatim'
    return True, "quoted span(s) verified verbatim against the locked quote"


def _ascii_fold(s: str) -> str:
    return re.sub(r"[^\x00-\x7f]", "", s)


def _check_attribution(tweet: str, author: str) -> tuple[bool, str]:
    if not author:
        return True, "no author to attribute"
    tweet_norm = tweet.replace("’", "'").lower()
    key = author.replace("’", "'").lower()
    if key in tweet_norm or _ascii_fold(key) in _ascii_fold(tweet_norm):
        return True, "attribution present"
    return False, f"tweet never names {author} — attribution missing"


def _check_invented_attribution(tweet: str, author: str) -> tuple[bool, str]:
    tweet_norm = _ascii_fold(tweet.lower())
    for a in ALLOWED_AUTHORS:
        if a == author:
            continue
        if _ascii_fold(a.lower()) in tweet_norm:
            return False, f"tweet also names {a}, but the locked quote is from {author}"
    return True, "no conflicting attribution found"


def _check_no_quote_marks(tweet: str) -> tuple[bool, str]:
    """No-quote mode only: this post is an original reflection, so no span
    should be presented as a quotation at all (regardless of whether it'd
    happen to match anything verbatim)."""
    spans = _QUOTE_SPAN_RE.findall(tweet)
    if spans:
        return False, f'tweet uses quotation marks ("{spans[0][:40]}...") but this post has no locked quote to attribute them to'
    return True, "no quotation marks present"


def _check_no_named_authors(tweet: str) -> tuple[bool, str]:
    """No-quote mode only: naming one of the allowed authors would falsely
    imply their words are being quoted when nothing of theirs actually is."""
    tweet_norm = _ascii_fold(tweet.lower())
    for a in ALLOWED_AUTHORS:
        if _ascii_fold(a.lower()) in tweet_norm:
            return False, f"tweet names {a}, but nothing is actually quoted from them in no-quote mode"
    return True, "no author named"


def _check_length(tweet: str) -> tuple[bool, str]:
    """Draft budget reserves room for AI_DISCLOSURE_SUFFIX (appended at post time).
    Never auto-truncate — a too-long draft fails review so the revision loop
    (or a human edit) shortens it with the quote intact."""
    n = len(tweet)
    if n > TWEET_DRAFT_MAX:
        return False, (
            f"tweet is {n} characters — exceeds the {TWEET_DRAFT_MAX} draft maximum "
            f"(must leave room for the AI-art disclosure; posted hard limit is "
            f"{TWEET_HARD_MAX})"
        )
    return True, f"{n}/{TWEET_DRAFT_MAX} draft characters ({TWEET_HARD_MAX} posted max)"


def review_tweet(topic: str, tweet: str, quote: str, author: str,
                 previous_review: dict = None, consultation_transcript: list = None,
                 include_quote: bool = True) -> dict:
    """
    Mechanical checks run in code first (never trust LLM compliance for
    honesty/format facts); a constitution-scored quality pass follows. A
    mechanical failure caps the score regardless of the LLM's read, so it
    always drives the revision loop below 7/10. consultation_transcript, when
    given, is evidence for scoring — the same discipline as reviewer.score's
    consultation_transcript param for bookmarks: the team DID consult, so
    Principle 4 is judged on the quality of that record, not its absence.

    include_quote toggles which honesty checks apply: with a locked quote,
    any quoted span must match it verbatim and be attributed; without one
    (an original reflection merely inspired by retrieved passages), the
    checks flip — no quotation marks and no named author at all, since
    either would falsely imply words are being quoted that aren't.
    """
    if include_quote:
        checks = {
            "no_preamble":              _check_preamble(tweet),
            "quote_exact":              _check_quote_exact(tweet, quote),
            "attribution":              _check_attribution(tweet, author),
            "no_invented_attribution":  _check_invented_attribution(tweet, author),
            "length":                   _check_length(tweet),
        }
    else:
        checks = {
            "no_preamble":       _check_preamble(tweet),
            "no_quote_marks":    _check_no_quote_marks(tweet),
            "no_named_authors":  _check_no_named_authors(tweet),
            "length":            _check_length(tweet),
        }
    mechanical_failures = [f"{k}: {msg}" for k, (ok, msg) in checks.items() if not ok]

    system_prompt = build_system_prompt("reviewer", "review")

    consult_block = ""
    if consultation_transcript:
        consult_block = (
            "\n\nCONSULTATION TRANSCRIPT — the team consulted (3 rounds, with a round-2 pause "
            "for Sheraj's guidance) before this tweet was drafted:\n"
        )
        for turn in consultation_transcript:
            consult_block += (
                f"\n[{turn.get('agent', '?')} — {turn.get('role', '')}]:\n"
                f"{turn.get('message', '')[:400]}\n"
            )
        consult_block += (
            "\nThis is your evidence the team consulted — judge its quality, not whether it "
            "happened.\n"
        )

    previous_block = ""
    if previous_review:
        previous_block = (
            f"\n\nRE-SCORING A REVISION — your previous overall was "
            f"{previous_review.get('overall', 0)}/10 and you recommended: "
            f"{previous_review.get('recommendation', '')}\n"
            "Judge the new draft on its merits: if your concern was addressed the affected "
            "score MUST rise; never repeat a previous number out of habit.\n"
        )
    mech_block = "\n".join(f"  - {f}" for f in mechanical_failures) or "  - none — all mechanical checks passed"

    quote_block = (
        f'Locked quote (verbatim, from the Librarian): "{quote}" — {author}\n'
        if include_quote else
        "This post deliberately does NOT quote anyone directly — it's an original reflection "
        "inspired by retrieved Bahá'í passages. Do not penalize the absence of a direct quote; "
        "judge the reflection on its own merits and the mechanical checks below.\n"
    )

    user_message = (
        "Score this short X (Twitter) post for @peaceAntz against the relevant bahAI Workforce "
        "constitution principles.\n\n"
        f"Topic: {topic}\n"
        f"{quote_block}"
        f"Draft tweet:\n{tweet}\n\n"
        f"Deterministic mechanical checks already run in code (trust these over your own reading):\n"
        f"{mech_block}\n\n"
        "Any mechanical failure above is itself a Trustworthiness or Fruit-not-Words problem — "
        "reflect it in your notes and recommendation.\n"
        f"{consult_block}"
        f"{previous_block}\n"
        "Score each principle 1-10 (9-10 rare and hard-earned; 7-8 good; 5-6 mediocre, revise; "
        "below 5 genuinely weak). For any principle below 7, the note must end with "
        "'Fix: <one concrete change>'.\n\n"
        "Return ONLY this JSON:\n"
        "{\n"
        '  "scores": {\n'
        '    "2_fruit_not_words":  {"score": 6, "note": "..."},\n'
        '    "3_trustworthiness":  {"score": 6, "note": "..."},\n'
        '    "4_consultation":     {"score": 6, "note": "..."},\n'
        '    "5_moderation":       {"score": 6, "note": "..."},\n'
        '    "7_craft_in_service": {"score": 6, "note": "..."}\n'
        "  },\n"
        '  "overall": 6.0,\n'
        '  "recommendation": "one or two sentences of concrete feedback for the Scribe"\n'
        "}"
    )
    raw = call_llm("reviewer", [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ], temperature=0.2, max_tokens=500, json_mode=True).strip()

    review = _parse_review(raw)
    if mechanical_failures:
        review["overall"] = min(review.get("overall", 0), 4.0)
        review["recommendation"] = (
            "Mechanical check failed — " + "; ".join(mechanical_failures) + ". "
            + review.get("recommendation", "")
        )
    review["passed"] = review.get("overall", 0) >= PASS_THRESHOLD and not mechanical_failures
    review["checks"] = {k: {"ok": ok, "detail": msg} for k, (ok, msg) in checks.items()}
    return review


# --- Orchestration ------------------------------------------------------------

def run_x_post_pipeline(topic: str, include_quote: bool = True, progress=None, on_turn=None,
                        request_human_input=None) -> dict:
    """
    Runs as a background job (see api._run_x_post_job) because the
    consultation's round-2 pause genuinely blocks the worker thread awaiting
    Sheraj's input — the same contract as _generate_bookmark/_run_card_pipeline.
    on_turn streams consultation turns live for the dashboard chat view;
    request_human_input, if given, blocks after round 2 for guidance.

    include_quote toggles the whole team's mode, decided up front (never
    mid-run): with a quote (default), the tweet weaves in a completely
    unaltered verbatim excerpt (shortenable only with "..."), same as before.
    Without one, the Librarian still retrieves passages and the team still
    discusses them in consultation — genuine inspiration, not decoration —
    but nothing gets quoted or attributed in the tweet itself; the Scribe
    writes an original reflection on the topic instead.

    Order of operations: briefing -> Librarian retrieval (candidates only,
    nothing locked yet) -> Artist paints the image (must exist before
    consultation, which judges it via vision) -> consultation (3 rounds +
    pause, product="x_post" — same team dialogue regardless of include_quote,
    since the retrieved passages are worth discussing either way) -> quote
    LOCKED (only if include_quote) by snapping the team's real conclusion to
    the nearest verbatim candidate (_best_matching_quote) -> Scribe drafts
    honoring the consultation's brief -> Reviewer QA revision loop, where a
    mini team consultation routes each fix to whichever agent can actually
    make it (Scribe/Artist/Librarian), not just the Scribe.

    Returns: {topic, briefing, tweet_text, include_quote, quote_locked,
              quote_author, citation, inspired_by, review, attempts,
              image_path, consultation}
    `consultation` is the full transcript (consultation dialogue + the QA
    revision log), same shape as the bookmark pipeline's combined transcript,
    for ConsultationTranscript to render as-is.
    Raises RuntimeError if the Librarian finds nothing (never falls back to
    an unverified or invented quote/inspiration — CLAUDE.md rule 11's
    discipline — this applies in both modes).
    """
    from agents.consultation import run_consultation, run_x_post_revision_consultation

    def _progress(msg):
        if progress:
            progress(msg)

    topic = topic.strip()
    if not topic:
        raise ValueError("topic is required")

    _progress("Coordinator is briefing the team...")
    briefing = coordinator_briefing(topic)

    _progress("Librarian is searching the verified writings...")
    research = librarian_research(topic, briefing["librarian_keywords"])
    if research["none_found"]:
        raise RuntimeError(
            "No verified quote found by Bahá'u'lláh, 'Abdu'l-Bahá, Shoghi Effendi, The Báb, or "
            "the Universal House of Justice for this topic. Try different wording, or run "
            "scripts/ingest_texts.py if the index isn't built."
        )

    _progress("Artist is composing the image brief (local Qwen3)...")
    image_prompt = build_x_post_image_prompt(topic, briefing["artist_mood"])
    _progress("Artist is generating the artwork (xAI)...")
    gen = generate_image(image_prompt, "16:9")
    image_path = gen.get("image_url", "")

    # ── Consultation (product="x_post") — same 3-round dialogue + round-2
    # human pause as the bookmark/card pipelines. The quote is deliberately
    # NOT locked yet: the team genuinely deliberates over these candidates
    # for 3 rounds (Artist/Scribe/Reviewer/Librarian), and locking one before
    # that discussion even starts meant the Scribe could end up drafting
    # around a quote the team never actually talked about (observed live,
    # 2026-07 — see _best_matching_quote's docstring).
    citations_for_consult = [
        {"text": q["quote"], "source": q["source"] or q["by"]} for q in research["quotes"]
    ]

    def _preview(_quote, _transcript):
        # No compositor step for X posts (nothing gets overlaid on the
        # image) — the preview IS the generated artwork itself.
        return image_path

    consultation = run_consultation(
        image_path, topic, image_prompt, citations_for_consult,
        progress=progress, on_turn=on_turn, request_human_input=request_human_input,
        product="x_post", render_preview=_preview,
        preview_note=("This is the generated image for the post as it stands right now — the "
                      "tweet text isn't written yet, and nothing gets printed on the image "
                      "itself (X renders the tweet's text separately)."),
    )
    brief = consultation.get("brief") or {}
    direction_note = brief_to_direction_note(brief)
    consult_transcript = consultation.get("transcript", [])

    def _inspired_by(quotes: list[dict]) -> str:
        return ", ".join(sorted({q["by"] for q in quotes})) if quotes else ""

    if include_quote:
        # Lock the quote AFTER real deliberation, not before it: snap whatever
        # the team actually converged on to the nearest verbatim candidate
        # (never the team's own possibly-reworded text) — see _best_matching_quote.
        proposed_quote = (consultation.get("verified_quote") or "").strip()
        picked = _best_matching_quote(proposed_quote, research["quotes"]) if proposed_quote else research["quotes"][0]
        quote, author, citation = picked["quote"], picked["by"], picked.get("source", "")
        inspired_by = ""
        _progress("Scribe is drafting the tweet...")
        tweet = scribe_write_tweet(topic, briefing["scribe_tone"], quote, author, direction_note)
    else:
        # No quote is locked — the retrieved passages stay what they are:
        # background inspiration for the Scribe's own original reflection.
        quote, author, citation = "", "", ""
        inspired_by = _inspired_by(research["quotes"])
        _progress("Scribe is drafting the tweet (original reflection, no direct quote)...")
        tweet = scribe_write_tweet_no_quote(topic, briefing["scribe_tone"], research["quotes"], direction_note)

    _progress("Reviewer is scoring the tweet against the constitution...")
    review = review_tweet(topic, tweet, quote, author, consultation_transcript=consult_transcript,
                          include_quote=include_quote)

    editing_log = [
        {"agent": "Scribe", "role": "tweet draft — attempt 1 (editing)", "message": tweet},
        {"agent": "Reviewer", "role": "score — attempt 1 (editing)",
         "message": f"Overall {review.get('overall', 0)}/10.\nRecommendation: {review.get('recommendation', '')}"},
    ]

    # ── Reviewer QA revision loop — the whole team decides WHAT needs fixing,
    # not just the Reviewer alone. Previously the Reviewer could only ever ask
    # the Scribe to revise text, so a recommendation like "the image doesn't
    # match the quote" had no achievable path and the score plateaued forever
    # (observed live, 2026-07). Now the Artist/Librarian/Reviewer mini-
    # consultation picks exactly one of: ship / revise_text (Scribe) /
    # repaint (Artist regenerates the image) / requote (Librarian re-picks a
    # different verified quote, by index, from a fresh search) — same
    # discipline as the quote card pipeline's run_card_revision_consultation.
    latest_quotes = research["quotes"]  # kept current after any successful requote
    revision_history = []  # [{attempt, action, guidance, overall, prev_overall}, ...]

    def _team_decide(cur_tweet, cur_quote, cur_author, cur_image_path, cur_review, attempt):
        if cur_review.get("overall", 0) >= PASS_THRESHOLD:
            return {"action": "ship", "action_guidance": "", "transcript": []}
        return run_x_post_revision_consultation(
            topic, cur_tweet, cur_quote, cur_author, cur_image_path, latest_quotes, cur_review,
            progress=progress, on_turn=on_turn, attempt=attempt, history=revision_history,
            include_quote=include_quote,
        )

    best_tweet, best_review = tweet, review
    best_quote, best_author, best_image_path, best_image_prompt = quote, author, image_path, image_prompt
    best_citation, best_inspired_by = citation, inspired_by
    best_inspiration = research["quotes"]
    cur_tweet, cur_review = tweet, review
    cur_quote, cur_author, cur_image_path, cur_image_prompt = quote, author, image_path, image_prompt
    cur_citation, cur_inspired_by = best_citation, best_inspired_by
    cur_inspiration = best_inspiration
    attempt = 1

    decision = (_team_decide(cur_tweet, cur_quote, cur_author, cur_image_path, review, 1)
                if attempt < MAX_ATTEMPTS else {"action": "ship", "action_guidance": "", "transcript": []})
    editing_log.extend(decision.get("transcript", []))
    cur_action, cur_guidance = decision["action"], decision["action_guidance"]

    while best_review.get("overall", 0) < PASS_THRESHOLD and attempt < MAX_ATTEMPTS:
        action, guidance = cur_action, cur_guidance
        if action == "ship":
            editing_log.append({"agent": "System", "role": "editing stopped",
                                "message": "The team decided to ship as-is — keeping the best "
                                           f"version ({best_review.get('overall', 0)}/10)."})
            break
        attempt += 1

        new_quote, new_author, new_image_path = cur_quote, cur_author, cur_image_path
        new_citation, new_inspired_by = cur_citation, cur_inspired_by
        new_inspiration = cur_inspiration
        new_image_prompt = cur_image_prompt
        revised_tweet = None

        if action == "revise_text":
            _progress(f"Scribe is revising the tweet (attempt {attempt}/{MAX_ATTEMPTS})...")
            if include_quote:
                candidate = scribe_revise_tweet(topic, briefing["scribe_tone"], cur_quote, cur_author,
                                                cur_tweet, guidance or cur_review.get("recommendation", ""))
            else:
                candidate = scribe_revise_tweet_no_quote(topic, briefing["scribe_tone"], cur_inspiration,
                                                         cur_tweet, guidance or cur_review.get("recommendation", ""))
            if candidate.strip() == cur_tweet.strip():
                editing_log.append({"agent": "System", "role": "editing stopped",
                                    "message": "The revision produced no change — keeping the best "
                                               f"version ({best_review.get('overall', 0)}/10)."})
                break
            revised_tweet = candidate
            editing_log.append(
                {"agent": "Scribe", "role": f"revision — attempt {attempt} (editing)", "message": revised_tweet})

        elif action == "repaint":
            _progress(f"Artist is repainting per the team: {guidance[:100]}...")
            try:
                new_prompt = f"{cur_image_prompt}\n\nIMPORTANT change requested by the team: {guidance or 'better express the theme'}"
                regen = generate_image(new_prompt, "16:9")
                candidate_path = regen.get("image_url", "")
                if not (candidate_path and Path(candidate_path).exists()):
                    raise RuntimeError("no image returned")
            except Exception as e:
                editing_log.append({"agent": "System", "role": "editing stopped",
                                    "message": f"Repaint failed ({e}) — keeping the best version "
                                               f"({best_review.get('overall', 0)}/10)."})
                break
            new_image_path = candidate_path
            new_image_prompt = new_prompt
            revised_tweet = cur_tweet
            editing_log.append(
                {"agent": "Artist", "role": f"revision — attempt {attempt} (editing)",
                 "message": f"Repainted the artwork per the team's steer: {guidance}"})

        elif action == "requote":
            label = "quote" if include_quote else "inspiration"
            _progress(f"Librarian is re-picking the {label}: {guidance[:100] or topic}...")
            new_research = librarian_research(guidance.strip() or topic, briefing["librarian_keywords"])
            if include_quote:
                candidates = [q for q in (new_research["quotes"] or []) if q["quote"] != cur_quote]
            else:
                cur_texts = {q["quote"] for q in cur_inspiration}
                candidates = [q for q in (new_research["quotes"] or []) if q["quote"] not in cur_texts]
            if not candidates:
                editing_log.append({"agent": "System", "role": "editing stopped",
                                    "message": f"No different verified passage found for the team's "
                                               f"steer — keeping the best version ({best_review.get('overall', 0)}/10)."})
                break
            latest_quotes = candidates

            if include_quote:
                picked2 = scribe_select_quote(topic, briefing["scribe_tone"], candidates)
                new_quote, new_author = picked2["quote"], picked2["by"]
                new_citation = picked2.get("source", "")
                revised_tweet = scribe_write_tweet(topic, briefing["scribe_tone"], new_quote, new_author, direction_note)
                editing_log.append(
                    {"agent": "Librarian", "role": f"revision — attempt {attempt} (editing)",
                     "message": f'Re-picked the quote per the team\'s steer: now "{new_quote[:120]}" — {new_author}'})
            else:
                new_inspiration = candidates
                new_inspired_by = _inspired_by(candidates)
                revised_tweet = scribe_write_tweet_no_quote(topic, briefing["scribe_tone"], candidates, direction_note)
                editing_log.append(
                    {"agent": "Librarian", "role": f"revision — attempt {attempt} (editing)",
                     "message": f"Re-picked the inspiration passages per the team's steer: now drawing "
                                f"on {new_inspired_by} (still not quoted in the tweet)."})
            editing_log.append(
                {"agent": "Scribe", "role": f"revision — attempt {attempt} (editing)", "message": revised_tweet})

        else:
            break  # unrecognized action — safety net, never loop forever on nothing

        _progress(f"Reviewer is re-scoring revision {attempt}/{MAX_ATTEMPTS}...")
        new_review = review_tweet(topic, revised_tweet, new_quote, new_author, previous_review=cur_review,
                                  consultation_transcript=consult_transcript, include_quote=include_quote)
        prev_overall = cur_review.get("overall", 0)
        new_overall = new_review.get("overall", 0)
        editing_log.append(
            {"agent": "Reviewer", "role": f"score — attempt {attempt} (editing)",
             "message": f"Overall {new_overall}/10 (was {prev_overall}/10 — "
                        f"{'improved' if new_overall > prev_overall else 'did not improve'}).\n"
                        f"Recommendation: {new_review.get('recommendation', '')}"})

        revision_history.append({"attempt": attempt, "action": action, "guidance": guidance,
                                 "overall": new_overall, "prev_overall": prev_overall})
        cur_tweet, cur_review = revised_tweet, new_review
        cur_quote, cur_author, cur_image_path, cur_citation = new_quote, new_author, new_image_path, new_citation
        cur_inspired_by, cur_inspiration = new_inspired_by, new_inspiration
        cur_image_prompt = new_image_prompt

        if new_overall >= best_review.get("overall", 0):
            best_tweet, best_review = revised_tweet, new_review
            best_quote, best_author, best_image_path = new_quote, new_author, new_image_path
            best_citation, best_inspired_by = new_citation, new_inspired_by
            best_image_prompt = new_image_prompt

        decision = (_team_decide(cur_tweet, cur_quote, cur_author, cur_image_path, new_review, attempt)
                    if attempt < MAX_ATTEMPTS else {"action": "ship", "action_guidance": "", "transcript": []})
        editing_log.extend(decision.get("transcript", []))
        cur_action, cur_guidance = decision["action"], decision["action_guidance"]

    return {
        "topic": topic,
        "briefing": briefing,
        "tweet_text": best_tweet.strip(),
        "include_quote": include_quote,
        "quote_locked": best_quote,
        "quote_author": best_author,
        "citation": best_citation,
        "inspired_by": best_inspired_by,
        "review": best_review,
        "attempts": attempt,
        "image_path": best_image_path or None,
        "image_prompt": best_image_prompt,
        "consultation": consult_transcript + editing_log,
    }


# --- Posting to X (OAuth 1.0a via tweepy) -------------------------------------

TWITTER_API_KEY       = os.getenv("TWITTER_API_KEY", "")
TWITTER_API_SECRET    = os.getenv("TWITTER_API_SECRET", "")
TWITTER_ACCESS_TOKEN  = os.getenv("TWITTER_ACCESS_TOKEN", "")
TWITTER_ACCESS_SECRET = os.getenv("TWITTER_ACCESS_SECRET", "")
TWITTER_DRY_RUN = os.getenv("TWITTER_DRY_RUN", "false").strip().lower() == "true"

X_HANDLE = "peaceAntz"


def _with_disclosure(tweet_text: str) -> str:
    """Code-appended, never LLM-written (CLAUDE.md rule 8). Always returns text
    that includes the disclosure — never silently drops it. Drafts must stay
    within TWEET_DRAFT_MAX so this always fits; if they don't, fail loudly."""
    candidate = f"{tweet_text}{AI_DISCLOSURE_SUFFIX}"
    if len(candidate) > TWEET_HARD_MAX:
        raise ValueError(
            f"Tweet with AI-art disclosure is {len(candidate)} characters "
            f"(limit {TWEET_HARD_MAX}). Shorten the draft to at most "
            f"{TWEET_DRAFT_MAX} characters before posting — disclosure is never omitted."
        )
    return candidate


def post_tweet(tweet_text: str, image_path: str = None) -> dict:
    """
    Post via OAuth 1.0a (User Auth). TWITTER_DRY_RUN=true logs instead of
    posting — for testing the pipeline end-to-end without touching the real
    account. Returns {dry_run, tweet_id, text, url, media_uploaded}.
    """
    final_text = _with_disclosure(tweet_text)

    if TWITTER_DRY_RUN:
        return {"dry_run": True, "tweet_id": None, "text": final_text,
                "url": None, "media_uploaded": bool(image_path)}

    if not (TWITTER_API_KEY and TWITTER_API_SECRET and TWITTER_ACCESS_TOKEN and TWITTER_ACCESS_SECRET):
        raise RuntimeError(
            "Twitter/X credentials are not set in .env (TWITTER_API_KEY, TWITTER_API_SECRET, "
            "TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_SECRET) — set TWITTER_DRY_RUN=true in .env to "
            "test the pipeline without posting."
        )

    import tweepy

    auth = tweepy.OAuth1UserHandler(
        TWITTER_API_KEY, TWITTER_API_SECRET, TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_SECRET,
    )
    media_ids = None
    if image_path and Path(image_path).exists():
        api_v1 = tweepy.API(auth)  # media upload is a v1.1-only endpoint
        media = api_v1.media_upload(filename=image_path)
        media_ids = [media.media_id]

    client = tweepy.Client(
        consumer_key=TWITTER_API_KEY, consumer_secret=TWITTER_API_SECRET,
        access_token=TWITTER_ACCESS_TOKEN, access_token_secret=TWITTER_ACCESS_SECRET,
    )
    resp = client.create_tweet(text=final_text, media_ids=media_ids)
    tweet_id = str(resp.data["id"])
    return {
        "dry_run": False,
        "tweet_id": tweet_id,
        "text": final_text,
        "url": f"https://x.com/{X_HANDLE}/status/{tweet_id}",
        "media_uploaded": bool(media_ids),
    }
