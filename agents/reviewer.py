"""
Reviewer agent — scores bookmark deliverables against the 7 constitution principles.
Routes to Grok for nuanced critical assessment.
"""

import json
import re
from pathlib import Path
from dotenv import load_dotenv

from agents.router import call_llm
from agents.system_prompt_builder import build_system_prompt

load_dotenv(dotenv_path=str(Path(__file__).parent.parent / ".env"))

PASS_THRESHOLD = 6.0  # average score needed across all 7 principles to pass


def score(
    theme: str,
    image_prompt: str,
    listing: dict,
    librarian_issues: list[str] = None,
) -> dict:
    """
    Score the image prompt + Etsy listing against the 7 constitution principles.
    Returns: {scores, overall, passed, recommendation}
    """
    system_prompt = build_system_prompt("reviewer", "review")

    issues_block = ""
    if librarian_issues:
        issues_block = "\n\nLibrarian flagged the following concerns:\n"
        issues_block += "\n".join(f"  - {i}" for i in librarian_issues)

    user_message = (
        f"Score this bookmark product against all 7 bahAI Workforce constitution principles.\n\n"
        f"Theme: {theme}\n\n"
        f"Image prompt:\n{image_prompt[:500]}\n\n"
        f"Etsy listing:\n{json.dumps(listing, indent=2)[:1500]}\n"
        f"{issues_block}\n\n"
        "Score each principle 1–10. A score below 6 means revision required before shipping.\n\n"
        "Return ONLY this JSON — no other text:\n"
        "{\n"
        '  "scores": {\n'
        '    "1_work_as_worship":  {"score": 8, "note": "one or two sentences"},\n'
        '    "2_fruit_not_words":  {"score": 7, "note": "..."},\n'
        '    "3_trustworthiness":  {"score": 9, "note": "..."},\n'
        '    "4_consultation":     {"score": 6, "note": "..."},\n'
        '    "5_moderation":       {"score": 8, "note": "..."},\n'
        '    "6_deeds_over_words": {"score": 7, "note": "..."},\n'
        '    "7_craft_in_service": {"score": 9, "note": "..."}\n'
        "  },\n"
        '  "overall": 7.7,\n'
        '  "passed": true,\n'
        '  "recommendation": "Ship it / Revise X before shipping / Reject because Y"\n'
        "}"
    )

    raw = call_llm("reviewer", [
        {"role": "system", "content": system_prompt},
        {"role": "user",   "content": user_message},
    ], temperature=0.3, max_tokens=800).strip()

    return _parse_review(raw)


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

    return result
