"""
Reviewer agent — scores bookmark deliverables against the 9 constitution principles.
Routes to Grok for nuanced critical assessment.
"""

import json
import re
from pathlib import Path
from dotenv import load_dotenv

from agents.router import call_llm, call_grok_vision
from agents.system_prompt_builder import build_system_prompt

load_dotenv(dotenv_path=str(Path(__file__).parent.parent / ".env"))

PASS_THRESHOLD = 7.0  # average score needed across all 9 principles to pass (raised from 6.0 — Phase 2B)


def _clip(text: str, limit: int) -> str:
    """
    Truncate at a word boundary with an explicit marker. A hard slice cuts
    mid-sentence and the Reviewer then reads the cutoff as sloppy work — one
    production run docked 'Work as Worship' for a 'prompt cutoff' that was
    purely our plumbing.
    """
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + " …[trimmed for brevity by the pipeline — the actual text continues; do not judge this cutoff]"


def score(
    theme: str,
    image_prompt: str,
    listing: dict,
    librarian_issues: list[str] = None,
    consultation_transcript: list = None,
    image_path: str = None,
    previous_review: dict = None,
    changes_applied: list = None,
    consultation_decision: dict = None,
) -> dict:
    """
    Score the image prompt + Etsy listing against the 9 constitution principles.
    When image_path points to an existing file, the Reviewer sees the actual
    artwork via Grok vision instead of judging from the prompt alone.
    previous_review (when re-scoring a revision) lets the Reviewer credit
    implemented fixes instead of re-anchoring at its last score.
    changes_applied lists the edits mechanically applied since that review, so
    the Reviewer never re-requests a change that is already in the text.
    consultation_decision is the team's round-2 synthesized brief (see
    consultation.py's _synthesize_brief) — the settled outcome of consultation,
    which this scoring pass must treat as binding rather than silently
    relitigating (see the decision_block below).
    Returns: {scores, overall, passed, recommendation}
    """
    system_prompt = build_system_prompt("reviewer", "review")

    issues_block = ""
    if librarian_issues:
        issues_block = "\n\nLibrarian flagged the following concerns:\n"
        issues_block += "\n".join(f"  - {i}" for i in librarian_issues)

    consultation_block = ""
    if consultation_transcript:
        consultation_block = (
            "\n\nCONSULTATION TRANSCRIPT — the team consulted in two rounds before writing:\n"
        )
        for turn in consultation_transcript:
            agent = turn.get("agent", "?")
            role = turn.get("role", "")
            msg = turn.get("message", "")[:600]
            consultation_block += f"\n[{agent} — {role}]:\n{msg}\n"
        consultation_block += (
            "\nWhen scoring Principle 4 (Consultation), this transcript is your evidence. "
            "The team DID consult — score based on the quality and depth of that consultation, "
            "not on whether it happened.\n"
        )

    decision_block = ""
    if consultation_decision:
        d = consultation_decision
        decision_parts = []
        if d.get("agreed_direction"):
            decision_parts.append(f"Agreed direction: {d['agreed_direction']}")
        if d.get("tone"):
            decision_parts.append(f"Agreed tone: {d['tone']}")
        if d.get("key_elements"):
            decision_parts.append("Agreed visual elements: " + "; ".join(d["key_elements"]))
        if decision_parts:
            decision_block = (
                "\n\nTHE TEAM'S SETTLED CONSULTATION DECISION (round 2):\n"
                + "\n".join(f"  {p}" for p in decision_parts) + "\n"
                "\"...record their vote and abide by the voice of the majority... never to be "
                "challenged, and always to be whole-heartedly enforced.\" — Shoghi Effendi. Once "
                "reached, this became the WHOLE team's decision, not just one agent's preference. "
                "Score and recommend WITHIN this decision — do not silently contradict it. The one "
                "exception: if the actual artwork or listing demonstrably diverges from a specific "
                "factual premise the team agreed on (e.g. they agreed on a motif count that the "
                "rendered image doesn't show), you MUST say so explicitly as 'REOPENING team "
                "decision: <what changed>' rather than scoring against it as if it were never "
                "discussed. A silent contradiction is not correction, it's failing to consult.\n"
            )

    previous_block = ""
    if previous_review:
        prev_overall = previous_review.get("overall", 0)
        prev_rec = previous_review.get("recommendation", "")
        prev_weak = ", ".join(
            f"{k.split('_', 1)[-1].replace('_', ' ')} ({v.get('score', 0)}/10)"
            for k, v in (previous_review.get("scores") or {}).items()
            if isinstance(v, dict) and v.get("score", 0) < 7
        )
        previous_block = (
            "\n\nRE-SCORING A REVISION — your previous review of this same product:\n"
            f"  Previous overall: {prev_overall}/10\n"
            f"  Previously weak: {prev_weak or 'n/a'}\n"
            f"  You recommended: {prev_rec}\n"
            "The Scribe revised the listing specifically to address that recommendation. "
            "Judge the revision on its merits:\n"
            "  - If your recommendation was implemented faithfully, the principles it affected "
            "MUST score higher than last time — do not repeat your previous numbers out of habit.\n"
            "  - If it was ignored or implemented poorly, say so plainly and score accordingly.\n"
            "  - NEVER re-issue a recommendation that has been fulfilled; name the next most "
            "valuable text improvement instead.\n"
        )
        if changes_applied:
            previous_block += (
                "\nWhat happened to the listing since that review:\n"
                + "\n".join(f"  {i + 1}. {c}" for i, c in enumerate(changes_applied))
                + "\nLines NOT starting with 'REJECTED' were executed MECHANICALLY and verified — "
                "they ARE present in the listing above; do not claim they're missing, and if they "
                "fulfilled your recommendation the affected principle scores MUST rise. Lines "
                "starting with 'REJECTED' targeted the locked bookmark_quote field and were "
                "BLOCKED — nothing changed for those; if that was your fix, choose a different, "
                "achievable one this round (reframe the description, never the quote) instead of "
                "repeating the same blocked request.\n"
            )

    user_message = (
        f"Score this bookmark product against all 9 bahAI Workforce constitution principles.\n\n"
        f"Theme: {theme}\n\n"
        f"Image prompt (this is what was REQUESTED of the generator — any exact counts in it, "
        f"e.g. '9 rays', are creative targets that the generator is not guaranteed to have hit. "
        f"Judge the actual attached image, not this text — never assume a requested count "
        f"rendered accurately):\n{_clip(image_prompt, 500)}\n\n"
        f"Etsy listing:\n{_clip(json.dumps(listing, indent=2), 6000)}\n"
        f"{issues_block}"
        f"{consultation_block}"
        f"{decision_block}"
        f"{previous_block}\n\n"
        "Score each principle 1–10 using a strict, calibrated scale:\n"
        "  9–10: Exceptional — this principle is actively embodied; would serve as a teaching example\n"
        "  7–8:  Good — solid alignment, only minor gaps\n"
        "  5–6:  Mediocre — principle is present but weakly executed; revision strongly recommended\n"
        "  3–4:  Poor — principle violated or consistently ignored; revision required\n"
        "  1–2:  Failure — actively contradicts this principle\n\n"
        "Calibration expectations:\n"
        "  A first-draft bookmark listing should typically score 5–7 on most principles.\n"
        "  Scores of 9–10 should be genuinely rare — hard-earned, not the default.\n"
        "  If you find yourself scoring most principles 8+, you are not being critical enough.\n"
        "  Scores below 5 must appear whenever the deliverable is genuinely weak on a principle.\n"
        "  The overall score is the mean of individual principles — if most are 6–7, "
        "overall should be ~6.5, not 8.\n\n"
        "Calibration examples — what a 4 looks like vs an 8:\n"
        "  Trustworthiness: 4 = the description implies each bookmark is individually hand-painted "
        "when it is a digital print; 8 = every claim matches exactly what the buyer receives.\n"
        "  Moderation: 4 = title stuffed with keywords and superlatives ('STUNNING!! PERFECT GIFT!!'), "
        "description twice as long as it needs to be; 8 = one clear promise, calmly made.\n"
        "  Craft in Service: 4 = interchangeable inspirational copy that could sell any bookmark "
        "on Etsy; 8 = copy that leaves the reader genuinely knowing something about this theme.\n\n"
        "Scope of your recommendation — read carefully:\n"
        "  At this stage the ARTWORK IS FINAL and cannot be regenerated; only the listing "
        "text (title, description, tags, quote framing) can still be revised. Your "
        "'recommendation' must therefore consist ONLY of concrete actions the Scribe can take on "
        "the listing text — never recommend changing, regenerating, or re-rendering the "
        "image — put any image concerns in 'image_notes' instead, where they inform future "
        "designs. The bookmark_quote field is LOCKED — the pipeline will reject and discard "
        "any edit against it, no matter how you phrase the recommendation or Fix: note. "
        "If the quote feels mismatched with the theme or image, the ONLY correction available "
        "to you is to reframe the DESCRIPTION so it bridges the quote to the theme explicitly "
        "— e.g. add a sentence like 'This restless unlocking mirrors the Valley's demand for "
        "surrender,' as an edit against the description field. Never target bookmark_quote. "
        "A recommendation the team cannot act on wastes the revision cycle.\n\n"
        "Description format — non-negotiable: EXACTLY 3 short paragraphs (1-3 sentences each), "
        "separated by a blank line. If the description you're scoring has 4+ paragraphs or a "
        "paragraph longer than 3 sentences, that is itself a Moderation violation — score it "
        "accordingly and end the note with a Fix: that merges the overflow into an existing "
        "paragraph (never as a new 4th paragraph). Your own edits and Fix: notes must never grow "
        "the description past this 3-paragraph shape — an addition must replace or fold into "
        "existing text, not stack a new paragraph on top.\n\n"
        "NEVER assert or instruct an exact count of a repeated visual motif — rays, points, "
        "petals, arches, tiles, stars, columns, or any 'N-pointed'/'N-rayed' phrasing — in a "
        "recommendation, Fix: note, or edit, even if the brief requested a specific number "
        "(e.g. the sacred numbers 9 or 19). Image generators cannot guarantee an exact "
        "repetition count, and this exact failure has shipped before: a Reviewer asserted "
        "'nine-pointed star' for artwork that actually had twelve points, the claim was "
        "applied verbatim, and Trustworthiness was then scored as if it were accurate — a "
        "fabrication scoring itself as true. You are judging THIS image, not the brief's "
        "intention for it. Describe motifs qualitatively instead — 'a multi-pointed star', "
        "'radiating rays', 'a ring of lotus blooms' — never with a specific number attached.\n\n"
        "Note requirement: for EVERY principle you score below 7, the note must END with "
        "'Fix: <one concrete change to the listing text that would raise this score>'. "
        "A diagnosis without a prescription leaves the Scribe guessing and wastes a revision.\n\n"
        "Recommendation requirement — this is the single most-read field in your response, "
        "so make it earn that attention. A vague recommendation like 'tighten the description' "
        "or 'one text-only revision: improve quote framing' tells the Scribe nothing it can act "
        "on precisely and wastes the round. Instead:\n"
        "  - Quote the EXACT sentence or phrase you mean, verbatim, in single quotes.\n"
        "  - State exactly what changes about it (deleted / replaced with what / moved where).\n"
        "  - Name the principle(s) it fixes in parentheses at the end.\n"
        "  - If more than one change matters this round, list them as short numbered clauses "
        "in the SAME 'recommendation' string rather than picking only one — you are not limited "
        "to a single sentence; a thorough recommendation naming 2-4 specific changes is far more "
        "useful than one vague sentence.\n"
        "  Example of a WEAK recommendation (do not write like this): "
        "\"One text-only revision: tighten description and quote framing to emphasize consuming surrender.\"\n"
        "  Example of a STRONG recommendation (write like this): "
        "\"1) Delete the redundant sentence 'Its flames demand total surrender.' — it repeats the "
        "prior sentence's idea (Fruit not Words). 2) Replace 'visual hymn to the divine' with "
        "'flame-wreathed lotus evoking the Valley's fire' — the current phrase is generic praise, "
        "not specific to this theme (Craft in Service). 3) In tags, delete 'spiritual jewelry' — "
        "this is a bookmark, not jewelry (Trustworthiness).\"\n"
        "  Every specific change named in the recommendation MUST also appear as a matching entry "
        "in the 'edits' array below — the recommendation explains the WHY in prose, 'edits' is "
        "the exact mechanical WHAT.\n\n"
        "Surgical edits — MANDATORY whenever you recommend any text change:\n"
        "  Fill the 'edits' array with 2–8 find-and-replace operations that fully implement "
        "your recommendation AND every 'Fix:' note for every principle scored below 7 — do not "
        "leave a weak principle's Fix unaddressed just because another edit covers a different "
        "principle. More actionable edits per round means the Scribe fixes more in one pass "
        "instead of needing another round to discover the same problem. The pipeline applies "
        "these MECHANICALLY — "
        "no writer interprets them — so each 'find' must be copied CHARACTER-FOR-CHARACTER "
        "from the listing above (a snippet of roughly 4–15 words that appears exactly once), "
        "and 'replace' is the exact new text ('' to delete the snippet). Never paraphrase the "
        "listing inside 'find' — if the string does not match exactly, the edit is lost. "
        "For tags, 'find' is the whole tag. Cover every occurrence you want changed with its "
        "own edit.\n"
        "  To INSERT new text, set 'find' to an existing anchor sentence and 'replace' to that "
        "same sentence followed by the new text.\n"
        "  Before returning, mentally apply your edits and re-read the resulting listing as a "
        "buyer would: it must contain no repeated phrases, no duplicated ideas, and no "
        "grammatical seams. Never insert wording that already appears elsewhere in the "
        "listing; if an earlier revision introduced redundancy, your edits must REMOVE it.\n"
        "  This check also applies WITHIN this same response: if two different principles' "
        "Fix: notes, or two entries in 'edits', would each add or rephrase text making the "
        "SAME underlying point (e.g. one principle's Fix inserts a sentence asserting X, and "
        "another principle's Fix independently asserts X again in different words), satisfy "
        "that point ONCE — pick the single best edit for it, and do not repeat it in a second "
        "edit or a second Fix: note. Applying two edits that each independently say the same "
        "thing produces back-to-back near-duplicate sentences, which is itself a Fruit-not-"
        "Words and Moderation violation you would then be penalising in the NEXT review.\n\n"
        "Field order matters: write 'edits' right after 'scores', BEFORE 'overall'/'passed'/"
        "'recommendation'/'image_notes'. Those trailing prose fields are the least valuable "
        "content in this response — if you ever have to write less than planned, better to "
        "shorten those than to leave an edit's find/replace text cut off mid-word.\n\n"
        "Two extra 1-10 scores, separate from the 9 principles (they do NOT factor into "
        "'overall' — that stays the mean of the 9 principles only; these are diagnostic, shown "
        "on the dashboard so a genuinely bad image or quote is visible at a glance):\n"
        "  image_fit: does the ACTUAL attached image (not the prompt's aspiration for it) "
        "genuinely match the theme and Bahá'í aesthetic? A generic or mismatched image scores "
        "low even if the listing text is excellent.\n"
        "  quote_quality: is the printed bookmark_quote authentic and well-formatted? Use the "
        "Librarian's verdict in the consultation transcript above (GROUNDED IN SOURCES vs "
        "ORIGINAL COMPOSITION) plus whether it reads cleanly at 2-4 lines — an ungrounded or "
        "awkwardly-formatted quote scores low even if everything else is strong.\n\n"
        "Return ONLY this JSON — no other text:\n"
        "{\n"
        '  "scores": {\n'
        '    "1_work_as_worship":  {"score": 6, "note": "one or two sentences; if below 7 end with Fix: ..."},\n'
        '    "2_fruit_not_words":  {"score": 5, "note": "..."},\n'
        '    "3_trustworthiness":  {"score": 7, "note": "..."},\n'
        '    "4_consultation":     {"score": 6, "note": "..."},\n'
        '    "5_moderation":       {"score": 7, "note": "..."},\n'
        '    "6_deeds_over_words": {"score": 5, "note": "..."},\n'
        '    "7_craft_in_service": {"score": 7, "note": "..."},\n'
        '    "8_justice":          {"score": 6, "note": "..."},\n'
        '    "9_independent_investigation": {"score": 6, "note": "..."}\n'
        "  },\n"
        '  "edits": [\n'
        '    {"field": "description", "find": "exact snippet copied from the listing", "replace": "new text, or empty string to delete"},\n'
        '    {"field": "tags", "find": "sacred numbers", "replace": ""}\n'
        "  ],\n"
        '  "overall": 6.1,\n'
        '  "passed": false,\n'
        '  "image_fit": 7,\n'
        '  "quote_quality": 8,\n'
        '  "recommendation": "Ship it / OR 1) Delete \'exact phrase\' — reason (Principle). 2) Replace '
        '\'exact phrase\' with \'new phrase\' — reason (Principle). / Reject because Y",\n'
        '  "image_notes": "optional — concerns about the artwork itself, for future designs"\n'
        "}"
    )

    # Judge with your own eyes (Principle 8): when the artwork file is available,
    # score while seeing the actual image via Grok vision. Falls back to text-only.
    raw = None
    if image_path and Path(image_path).exists():
        try:
            raw = call_grok_vision(
                image_path,
                "The attached image is the ACTUAL generated bookmark artwork. "
                "Judge what you see — not what the image prompt promised.\n\n" + user_message,
                system=system_prompt,
                temperature=0.15,
                max_tokens=5200,
                json_mode=True,
            ).strip()
        except Exception:
            raw = None
    if raw is None:
        raw = call_llm("reviewer", [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_message},
        ], temperature=0.15, max_tokens=5200, json_mode=True).strip()

    return _parse_review(raw)


def score_quote_card(
    theme: str,
    quote: str,
    citation_source: str,
    quote_grounded: bool,
    front_image_path: str = None,
    translation: dict = None,
    consultation_transcript: list = None,
    consultation_decision: dict = None,
    previous_review: dict = None,
    revision_note: str = None,
) -> dict:
    """
    Score a QUOTE CARD — the giveaway outreach product. Deliberately NOT the
    9-principle Etsy rubric: there is no listing. Four (five with a
    translation) purpose-built criteria on the same calibrated 1-10 scale,
    with the same 'Fix:' note discipline the listing Reviewer uses.

    The vision call sends the RENDERED FRONT FACE, not the raw artwork —
    print legibility can only be judged on the actual composited card.

    Returns {scores, overall, passed, recommendation, action, action_guidance}.
    `action` is the machine-readable revision lever ("ship" | "requote" |
    "repaint") — the card revision loop acts on it mechanically, the same
    discipline as the listing pipeline's `edits` array: compliance never
    depends on interpreting prose.
    """
    system_prompt = build_system_prompt("reviewer", "review")

    consultation_block = ""
    if consultation_transcript:
        consultation_block = (
            "\n\nCONSULTATION TRANSCRIPT — the team consulted in two rounds before this card:\n"
        )
        for turn in consultation_transcript:
            consultation_block += (
                f"\n[{turn.get('agent', '?')} — {turn.get('role', '')}]:\n"
                f"{turn.get('message', '')[:600]}\n"
            )

    decision_block = ""
    if consultation_decision:
        d = consultation_decision
        decision_parts = [p for p in (
            f"Agreed direction: {d['agreed_direction']}" if d.get("agreed_direction") else "",
            f"Agreed tone: {d['tone']}" if d.get("tone") else "",
            ("Agreed visual elements: " + "; ".join(d["key_elements"])) if d.get("key_elements") else "",
        ) if p]
        if decision_parts:
            decision_block = (
                "\n\nTHE TEAM'S SETTLED CONSULTATION DECISION (round 2):\n"
                + "\n".join(f"  {p}" for p in decision_parts) + "\n"
                "Score and recommend WITHIN this decision — do not silently contradict it. "
                "If the finished card demonstrably diverges from a factual premise the team "
                "agreed on, say so explicitly as 'REOPENING team decision: <what changed>' "
                "rather than scoring against it as if it were never discussed.\n"
            )

    translation_block = ""
    if translation:
        translation_block = (
            f"\n\nTRANSLATION ({translation.get('name', '')}) printed beneath the English:\n"
            f"{translation.get('text', '')}\n"
            f"The card also prints this fixed disclaimer in {translation.get('name', '')}: "
            f"\"{translation.get('disclaimer_native', '')}\" — the translation is honestly "
            "labeled as AI-assisted and unofficial; judge the translation's FIDELITY and "
            "register, not the labeling.\n"
        )

    previous_block = ""
    if previous_review:
        previous_block = (
            "\n\nRE-SCORING A REVISION — your previous review of this card:\n"
            f"  Previous overall: {previous_review.get('overall', 0)}/10\n"
            f"  You recommended: {previous_review.get('recommendation', '')}\n"
            f"  What the pipeline then did: {revision_note or 'n/a'}\n"
            "Judge the new card on its merits — if your concern was addressed, the affected "
            "score MUST move; if it was not, say so plainly. Never repeat a previous number "
            "out of habit.\n"
        )

    grounding_line = (
        "The Librarian's verdict on this quote: GROUNDED IN SOURCES (verified against indexed texts)."
        if quote_grounded else
        "The Librarian could NOT ground this quote in a specific indexed source — weigh that "
        "heavily under quote_citation and say so in the note."
    )

    # score_quote_card is card-only (bookmarks use score()), so the Book-1
    # sourcing constraint always applies here — see CARD_QUOTE_SOURCING_NOTE's
    # own comment for why the Reviewer needs this spelled out explicitly.
    from agents.consultation import CARD_QUOTE_SOURCING_NOTE
    sourcing_note = f"\n{CARD_QUOTE_SOURCING_NOTE}\n"

    translation_score_line = (
        '    "translation":      {"score": 6, "note": "fidelity to the English, register, natural phrasing; if below 7 end with Fix: ..."},\n'
        if translation else ""
    )

    user_message = (
        "Score this QUOTE CARD — a 3.5×2 inch giveaway card (front: artwork under a vignette "
        "with the quote printed; back: clean artwork). It is NOT sold; it exists to share one "
        "beautiful idea from the Bahá'í writings with someone who may have never encountered "
        "the Faith. That person is the standard of judgment throughout.\n\n"
        f"Theme: {theme}\n\n"
        f"Printed quote:\n{quote}\n\n"
        f"Citation printed on the card: {citation_source or '(none)'}\n"
        f"{grounding_line}"
        f"{sourcing_note}"
        f"{translation_block}"
        f"{consultation_block}"
        f"{decision_block}"
        f"{previous_block}\n\n"
        "Score each criterion 1–10 on the same strict calibrated scale as all reviews here "
        "(9–10 exceptional and rare; 7–8 good; 5–6 mediocre, revise; below 5 genuinely weak). "
        "For EVERY criterion below 7 the note must END with 'Fix: <one concrete achievable "
        "change>'.\n\n"
        "Criteria:\n"
        "  quote_citation: is the quote accurate, well-chosen for the theme, and correctly "
        "attributed? An ungrounded quote caps this at 4.\n"
        + ("  translation: is the translation faithful and complete, in a natural, reverent "
           "register a native reader would trust? Flag ANY added, dropped, or distorted meaning.\n"
           if translation else "")
        + "  artwork_fit: does the ACTUAL rendered card artwork genuinely express the theme "
        "and reward a closer look, front and back?\n"
        "  newcomer_accessibility: the heart of this product — would someone with ZERO "
        "background find this card welcoming, clear, and beautiful? Anything esoteric, "
        "jargon-dependent, or insider-coded scores low, however devotionally excellent. "
        "Judge this against what the pool can actually supply (verbatim 19th-century "
        "scripture, some archaic register unavoidable) — do not require modern phrasing "
        "the sourcing rule forbids.\n"
        "  legibility: judge the attached image — the real front face. Is every piece of "
        "text comfortably readable at business-card size (quote, translation if any, "
        "citation, disclaimer)? Crowding, low contrast, or tiny type scores low.\n\n"
        "Decide ONE next action for the pipeline (machine-executed — choose exactly one):\n"
        "  \"ship\"    — the card is ready (typical when overall ≥ target).\n"
        "  \"requote\" — a genuinely different, available passage from the pool would help "
        "(fit or length — not register, see above): put the search phrase in action_guidance.\n"
        "  \"repaint\" — the weakness is the ARTWORK: the pipeline regenerates it; put the "
        "imperative change in action_guidance.\n"
        "Text layout problems alone (legibility) usually mean \"requote\" toward a SHORTER "
        "quote. Never pick an action your guidance doesn't support.\n\n"
        "Return ONLY this JSON — field order matters, keep it exactly:\n"
        "{\n"
        '  "scores": {\n'
        '    "quote_citation":   {"score": 6, "note": "one or two sentences; if below 7 end with Fix: ..."},\n'
        f"{translation_score_line}"
        '    "artwork_fit":      {"score": 6, "note": "..."},\n'
        '    "newcomer_accessibility": {"score": 6, "note": "..."},\n'
        '    "legibility":       {"score": 7, "note": "..."}\n'
        "  },\n"
        '  "action": "ship",\n'
        '  "action_guidance": "empty string when shipping; otherwise the concrete steer",\n'
        '  "overall": 6.2,\n'
        '  "passed": false,\n'
        '  "recommendation": "one or two sentences a human reads on the dashboard"\n'
        "}"
    )

    raw = None
    if front_image_path and Path(front_image_path).exists():
        try:
            raw = call_grok_vision(
                front_image_path,
                "The attached image is the ACTUAL rendered front face of the quote card — "
                "the physical thing a stranger would be handed. Judge what you see.\n\n"
                + user_message,
                system=system_prompt,
                temperature=0.15,
                max_tokens=1600,
                json_mode=True,
            ).strip()
        except Exception:
            raw = None
    if raw is None:
        raw = call_llm("reviewer", [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_message},
        ], temperature=0.15, max_tokens=1600, json_mode=True).strip()

    review = _parse_review(raw)
    action = str(review.get("action") or "").strip().lower()
    if action not in ("ship", "requote", "repaint"):
        # Malformed/missing action — treat as ship-nothing: let the score
        # decide, but never invent a revision lever the Reviewer didn't pick.
        review["action"] = "ship" if review.get("passed") else "requote"
    review["action_guidance"] = str(review.get("action_guidance") or "").strip()
    return review


def _parse_review(raw: str) -> dict:
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    result = None
    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if match:
            try:
                result = json.loads(match.group())
            except json.JSONDecodeError:
                pass
        truncated = False
        if result is None:
            from agents.scribe import _repair_truncated_json
            repaired = _repair_truncated_json(raw)
            if repaired:
                try:
                    parsed = json.loads(repaired)
                    if isinstance(parsed, dict) and parsed.get("scores"):
                        result = parsed
                        truncated = True
                except json.JSONDecodeError:
                    pass

        if truncated and isinstance(result.get("edits"), list) and result["edits"]:
            # The response hit the token ceiling mid-generation and had to be
            # closed by force. Live testing showed the LAST edit's 'find' or
            # 'replace' string gets cut mid-word (e.g. "...mirror the quot")
            # and the mechanical applier then copies that broken fragment
            # verbatim into the shipped listing — actively corrupting text
            # rather than just failing to improve it. The last edit is the one
            # that was being written when the cutoff hit, so it's the only one
            # at risk; drop it and keep the rest, which were fully written
            # before the cutoff and are safe.
            result["edits"] = result["edits"][:-1]

    if result is None:
        return {
            "scores": {},
            "overall": 0.0,
            "passed": False,
            "recommendation": f"Reviewer output could not be parsed: {raw[:200]}",
        }

    # Compute overall if not supplied
    if "overall" not in result and "scores" in result:
        vals = [v.get("score", 0) for v in result["scores"].values() if isinstance(v, dict)]
        result["overall"] = round(sum(vals) / len(vals), 1) if vals else 0.0

    if "passed" not in result:
        result["passed"] = result.get("overall", 0) >= PASS_THRESHOLD

    # image_fit / quote_quality are diagnostic-only (never feed 'overall') —
    # coerce to a clean 1-10 float or drop them rather than ship a malformed
    # value the dashboard would render oddly.
    for key in ("image_fit", "quote_quality"):
        try:
            result[key] = round(max(1.0, min(10.0, float(result[key]))), 1)
        except (KeyError, TypeError, ValueError):
            result.pop(key, None)

    return result
