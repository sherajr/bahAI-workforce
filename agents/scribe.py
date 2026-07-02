"""
Scribe agent — writes Etsy listing copy for Bahá'í-inspired bookmarks.
Routes to Grok for higher-quality creative/marketing copy.
"""

import json
import re
from pathlib import Path
from dotenv import load_dotenv

from agents.router import call_llm
from agents.system_prompt_builder import build_system_prompt

load_dotenv(dotenv_path=str(Path(__file__).parent.parent / ".env"))


def write_listing(
    theme: str,
    image_prompt: str,
    citations: list[dict] = None,
    image_url: str = None,
    consultation_context: str = "",
    verified_quote: str = "",
) -> dict:
    """
    Write a complete Etsy listing for a Bahá'í-inspired bookmark.
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

    if verified_quote:
        quote_field = (
            f'  "bookmark_quote": "USE EXACTLY THIS TEXT — verified by the Librarian from actual '
            f'Bahá\'í writings (do not change it): {verified_quote}",\n'
        )
    else:
        quote_field = (
            '  "bookmark_quote": "A verse or phrase printed on the bookmark face — '
            "2 to 4 lines of poetry or prose, 120–180 characters total. "
            "Spiritually uplifting, drawn from the theme or citations. "
            "No quotation marks in the value itself.\",\n"
        )

    user_message = (
        f"Write a complete Etsy listing for a handmade Bahá'í-inspired bookmark.\n\n"
        f"Theme: {theme}\n"
        f"Image description: {image_prompt[:400]}\n"
        f"{citation_block}"
        f"{consult_block}\n"
        "Product details:\n"
        "- Designed by Sheraj, a Bahá'í artist\n"
        "- 2\" × 6\" printed on premium cardstock, available laminated\n"
        "- Designed in Canva, printed to order — each one is made with care\n"
        "- Ships within 3–5 business days\n\n"
        "Return ONLY this JSON object — no other text:\n"
        "{\n"
        '  "title": "Etsy listing title — max 80 chars, natural keywords, no ALL CAPS",\n'
        '  "description": "Full listing — 3–5 paragraphs. Open with a spiritual/emotional hook. '
        "Describe what the buyer receives. Mention it's Bahá'í-inspired and what that means. "
        "Include size, materials, and care info. Warm and honest — not salesy.\",\n"
        + quote_field +
        '  "tags": ["up to 13 Etsy tags", "single words or 2-3 word phrases only"],\n'
        '  "materials": ["Premium cardstock", "Soy-based inks"],\n'
        '  "price_note": "Suggested retail price with brief reasoning"\n'
        "}"
    )

    raw = call_llm("scribe", [
        {"role": "system", "content": system_prompt},
        {"role": "user",   "content": user_message},
    ], temperature=0.7, max_tokens=1500).strip()

    return _parse_json(raw, fallback_title=theme)


def revise_listing(
    theme: str,
    image_prompt: str,
    citations: list[dict] = None,
    image_url: str = None,
    reviewer_notes: str = "",
    reviewer_scores: dict = None,
    consultation_context: str = "",
    verified_quote: str = "",
) -> dict:
    """
    Rewrite a listing that scored below 7, incorporating specific reviewer feedback.
    Called automatically on borderline scores (6–7 range) before a second review pass.
    """
    system_prompt = build_system_prompt("scribe", "copy")

    weak_scores = ""
    if reviewer_scores:
        lines = []
        for principle, detail in reviewer_scores.items():
            s = detail.get("score", 0) if isinstance(detail, dict) else 0
            note = detail.get("note", "") if isinstance(detail, dict) else ""
            if s < 7:
                lines.append(f"  - {principle}: {s}/10 — {note}")
        if lines:
            weak_scores = "\n\nPrinciples that need improvement:\n" + "\n".join(lines)

    citation_block = ""
    if citations:
        citation_block = "\n\nSpiritual citations to weave in naturally:\n"
        for c in (citations or [])[:2]:
            citation_block += f'  — "{c.get("text", "")[:180]}" ({c.get("source", "")})\n'

    consult_block = f"\n\n{consultation_context}\n" if consultation_context else ""

    if verified_quote:
        revise_quote_field = (
            f'  "bookmark_quote": "KEEP EXACTLY THIS TEXT — Librarian-verified from actual '
            f'Bahá\'í writings: {verified_quote}",\n'
        )
    else:
        revise_quote_field = '  "bookmark_quote": "2 to 4 lines, 120–180 characters, no quotation marks",\n'

    user_message = (
        f"Revise this Etsy bookmark listing based on reviewer feedback.\n\n"
        f"Theme: {theme}\n"
        f"Image description: {image_prompt[:400]}\n"
        f"{citation_block}"
        f"{consult_block}\n"
        f"Reviewer recommendation: {reviewer_notes}\n"
        f"{weak_scores}\n\n"
        "Address the reviewer's concerns. Keep the listing warm, authentic, and Bahá'í-inspired.\n\n"
        "Return ONLY this JSON object — no other text:\n"
        "{\n"
        '  "title": "Etsy listing title — max 80 chars, natural keywords",\n'
        '  "description": "Full revised listing — 3–5 paragraphs",\n'
        + revise_quote_field +
        '  "tags": ["up to 13 Etsy tags"],\n'
        '  "materials": ["Premium cardstock", "Soy-based inks"],\n'
        '  "price_note": "Suggested retail price"\n'
        "}"
    )

    raw = call_llm("scribe", [
        {"role": "system", "content": system_prompt},
        {"role": "user",   "content": user_message},
    ], temperature=0.7, max_tokens=1500).strip()

    return _parse_json(raw, fallback_title=theme)


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
    return {
        "title": fallback_title,
        "description": raw,
        "tags": [],
        "materials": [],
        "price_note": "",
    }
