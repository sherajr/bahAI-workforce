"""
Consultation — multi-agent dialogue about generated artwork before the listing is written.

Flow:
  Artist  → views the image with Claude vision, describes spiritual elements and mood
  Scribe  → reads the Artist's description, proposes quote directions and listing tone
  Reviewer → reads both, gives constitution guidance (no scoring yet — this is coaching)

The full transcript is returned so Sheraj can follow the team's reasoning.
The merged context is passed to the Scribe when writing the actual listing.
"""

import base64
import os
import requests
from pathlib import Path
from dotenv import load_dotenv

from agents.router import call_llm

load_dotenv(dotenv_path=str(Path(__file__).parent.parent / ".env"))

ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"


def _call_claude_vision(image_path: str, prompt: str) -> str:
    """Send an image + prompt to Claude claude-haiku-4-5 for visual analysis."""
    if not ANTHROPIC_KEY:
        return "(Vision unavailable — ANTHROPIC_API_KEY not set in .env)"

    suffix = Path(image_path).suffix.lower()
    media_type = "image/png" if suffix == ".png" else "image/jpeg"

    with open(image_path, "rb") as f:
        b64 = base64.standard_b64encode(f.read()).decode()

    payload = {
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 800,
        "messages": [{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": media_type, "data": b64},
                },
                {"type": "text", "text": prompt},
            ],
        }],
    }
    headers = {
        "x-api-key": ANTHROPIC_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    resp = requests.post(ANTHROPIC_URL, headers=headers, json=payload, timeout=60)
    resp.raise_for_status()
    return resp.json()["content"][0]["text"].strip()


def run_consultation(
    image_path: str,
    theme: str,
    image_prompt: str,
    citations: list,
    progress=None,
) -> dict:
    """
    Run a four-turn consultation about the generated image.
      Turn 1 — Artist: Claude vision describes the image (falls back to image_prompt if key unavailable)
      Turn 2 — Scribe: proposes quote directions
      Turn 3 — Reviewer: gives constitution guidance
      Turn 4 — Librarian: verifies quotes against retrieved citations or known Bahá'í texts

    Returns:
        transcript     — list of {agent, role, message} (shown to Sheraj)
        context        — merged text for the Scribe to use when writing the listing
        verified_quote — Librarian-extracted quote; empty string if extraction failed
    """
    transcript = []

    def _progress(msg: str):
        if progress:
            progress(msg)

    # ── Turn 1: Artist views the image ──────────────────────────────────────
    _progress("Consultation — turn 1/4: Artist is studying the image...")
    artist_prompt = (
        "You are the Artist agent for bahAI Workforce, a Bahá'í-inspired art and craft "
        "business run by Sheraj. You just created this image as a bookmark design. "
        "The center strip (roughly the middle half) will be the front face of the bookmark "
        "— the part the buyer sees with the quote printed on it. "
        f"The requested theme was: {theme}\n\n"
        "Report back to your team:\n"
        "1. The dominant visual elements — colors, light, motifs, composition\n"
        "2. Which Bahá'í themes or symbols this image evokes\n"
        "3. The emotional mood — what a buyer will feel when they hold this\n"
        "4. What stands out most in the center strip (the front face)\n\n"
        "Speak naturally, as if presenting your work to colleagues. 3–4 paragraphs."
    )
    try:
        artist_msg = _call_claude_vision(image_path, artist_prompt)
    except Exception as vision_err:
        artist_msg = (
            f"(Vision unavailable — {vision_err})\n\n"
            f"Image was generated from this prompt: {image_prompt[:300]}"
        )
    transcript.append({
        "agent": "Artist",
        "role": "image observation",
        "message": artist_msg,
    })

    # ── Turn 2: Scribe proposes a direction ─────────────────────────────────
    _progress("Consultation — turn 2/4: Scribe is proposing quote directions...")
    citation_block = ""
    if citations:
        citation_block = "\n\nAvailable spiritual citations:\n"
        for c in citations[:2]:
            citation_block += f'  — "{c.get("text", "")[:140]}" ({c.get("source", "")})\n'

    scribe_input = (
        f"You are the Scribe agent for bahAI Workforce. The Artist just described the image:\n\n"
        f"{artist_msg}\n\n"
        f"Theme: {theme}{citation_block}\n\n"
        "Think aloud with the team — don't write the final listing yet:\n"
        "1. What spiritual truth does this image most powerfully express?\n"
        "2. Propose 1–2 possible bookmark quotes (2–4 lines, 120–180 characters total, "
        "poetic and uplifting — no quotation marks)\n"
        "3. What emotional tone should the Etsy listing carry?\n"
        "4. Any question for the Artist or Reviewer before you finalise?\n\n"
        "Be conversational — this is a team discussion."
    )
    scribe_msg = call_llm(
        "scribe",
        [{"role": "user", "content": scribe_input}],
        temperature=0.85,
        max_tokens=600,
    ).strip()
    transcript.append({
        "agent": "Scribe",
        "role": "quote & listing proposal",
        "message": scribe_msg,
    })

    # ── Turn 3: Reviewer gives constitution guidance ─────────────────────────
    _progress("Consultation — turn 3/4: Reviewer is giving constitution guidance...")
    reviewer_input = (
        "You are the Reviewer agent for bahAI Workforce. The team is preparing a Bahá'í-inspired "
        "bookmark listing and you are in consultation — not scoring yet, just guiding.\n\n"
        f"Artist's observation:\n{artist_msg}\n\n"
        f"Scribe's proposal:\n{scribe_msg}\n\n"
        f"Theme: {theme}\n\n"
        "Offer brief guidance:\n"
        "1. Which 2–3 of the 7 constitution principles are most alive in this piece?\n"
        "2. Any concern about spiritual authenticity in the Scribe's direction?\n"
        "3. One specific recommendation to make this listing exceptional\n"
        "4. A word of encouragement to the team\n\n"
        "Keep it warm and under 200 words."
    )
    reviewer_msg = call_llm(
        "reviewer",
        [{"role": "user", "content": reviewer_input}],
        temperature=0.4,
        max_tokens=400,
    ).strip()
    transcript.append({
        "agent": "Reviewer",
        "role": "constitution guidance",
        "message": reviewer_msg,
    })

    # ── Turn 4: Librarian verifies proposed quotes against actual citations ──
    _progress("Consultation — turn 4/4: Librarian is verifying quote authenticity...")
    verified_quote = ""

    if citations:
        citations_block = "\n\n".join(
            f'  [{i+1}] "{c.get("text", "").strip()[:300]}"\n'
            f'       — {c.get("source", "")} · {c.get("link", "")}'
            for i, c in enumerate(citations[:3])
        )
        source_instruction = (
            f"Verified passages retrieved from our Bahá'í text index:\n{citations_block}\n\n"
            "Write ONE verified bookmark quote adapted directly from these passages. "
            "You may condense or lightly rephrase but it must be traceable to a specific passage above. "
            "Do NOT invent new spiritual language.\n"
        )
    else:
        source_instruction = (
            "No specific passages were retrieved. Draw the verified quote from well-known, "
            "attributable Bahá'í writings (Bahá'u'lláh, 'Abdu'l-Bahá, the Báb, or Shoghi Effendi). "
            "Choose a short passage you are certain is authentic and name the exact source.\n"
        )

    librarian_input = (
        "You are the Librarian agent for bahAI Workforce. "
        "Your role is to ensure every bookmark quote is grounded in actual verified Bahá'í writings — "
        "not original poetry or language invented by the Scribe.\n\n"
        f"{source_instruction}\n"
        f"Scribe's proposed quotes:\n{scribe_msg}\n\n"
        "Your task:\n"
        "1. State whether the Scribe's quotes are drawn from the sources above "
        "or are original composition.\n"
        "2. Provide ONE verified bookmark quote (2–4 lines, 120–180 characters total). "
        "Write it as a single line — use a slash (/) between lines if needed, "
        "e.g.: 'The earth is but one country / and mankind its citizens'\n"
        "3. Name the source author and work.\n\n"
        "Reply in EXACTLY this format — nothing before VERDICT, nothing after REASONING:\n"
        "VERDICT: [GROUNDED IN SOURCES / ORIGINAL COMPOSITION]\n"
        "VERIFIED QUOTE: [the quote on a single line]\n"
        "SOURCE: [author, work]\n"
        "REASONING: [one sentence]"
    )
    librarian_msg = call_llm(
        "librarian",
        [{"role": "user", "content": librarian_input}],
        temperature=0.2,
        max_tokens=400,
    ).strip()

    # Extract VERIFIED QUOTE — find the line, then grab everything until SOURCE:
    lines = librarian_msg.splitlines()
    for i, line in enumerate(lines):
        if line.upper().startswith("VERIFIED QUOTE:"):
            candidate = line.split(":", 1)[1].strip().strip('"')
            # Collect continuation lines until the next labelled field
            j = i + 1
            while j < len(lines) and not lines[j].upper().startswith(("SOURCE:", "REASONING:", "VERDICT:")):
                extra = lines[j].strip().strip('"')
                if extra:
                    candidate += " / " + extra
                j += 1
            verified_quote = candidate
            break

    transcript.append({
        "agent": "Librarian",
        "role": "citation verification",
        "message": librarian_msg,
    })

    # Hard constraint injected into Scribe's prompt when verified_quote is available
    quote_instruction = (
        f'\n\nCRITICAL — The bookmark_quote field MUST use exactly this Librarian-verified text '
        f'(do not alter it):\n"{verified_quote}"'
        if verified_quote else ""
    )

    context = (
        f"TEAM CONSULTATION FOR THEME: {theme}\n\n"
        f"[Artist described the image]:\n{artist_msg}\n\n"
        f"[Scribe proposed]:\n{scribe_msg}\n\n"
        f"[Reviewer guided]:\n{reviewer_msg}\n\n"
        f"[Librarian verified]:\n{librarian_msg}"
        f"{quote_instruction}\n\n"
        "Use this consultation when writing the listing. The quote must come from the Librarian's "
        "verified text above. The tone should reflect what the Artist saw and what the Reviewer emphasised."
    )

    return {"transcript": transcript, "context": context, "verified_quote": verified_quote}
