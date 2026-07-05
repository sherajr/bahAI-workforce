"""
Artist agent — builds FLUX.1-style image prompts and generates images via xAI Grok.

Two steps:
1. build_image_prompt(theme, citations) → detailed prompt text (Ollama/Qwen3)
2. generate_image(prompt)              → image via xAI /v1/images/generations
"""

import os
import base64
import requests
from pathlib import Path
from dotenv import load_dotenv

from agents.router import call_llm
from agents.system_prompt_builder import build_system_prompt

load_dotenv(dotenv_path=str(Path(__file__).parent.parent / ".env"))

XAI_KEY        = os.getenv("XAI_API_KEY", "")
XAI_BASE       = os.getenv("XAI_BASE_URL", "https://api.x.ai/v1")
XAI_IMAGE_MODEL = os.getenv("XAI_IMAGE_MODEL", "grok-imagine-image-quality")

OUTPUTS_DIR = Path(__file__).parent.parent / "outputs"
OUTPUTS_DIR.mkdir(exist_ok=True)


def build_image_prompt(theme: str, citations: list[dict] | None = None) -> str:
    """
    Use local Qwen3 to write a detailed image generation prompt
    grounded in the theme and any Librarian citations.
    """
    system_prompt = build_system_prompt("artist", "design")

    citation_block = ""
    if citations:
        citation_block = "\n\nSpiritual citations to inspire the image:\n"
        for c in (citations or [])[:2]:
            citation_block += f'  — "{c.get("text", "")[:180]}" ({c.get("source", "")})\n'

    user_message = (
        f"Write an image generation prompt for a Bahá'í-inspired bookmark.\n\n"
        f"Theme: {theme}\n"
        f"{citation_block}\n\n"
        "Requirements:\n"
        "- Portrait orientation (tall, narrow — 1:3)\n"
        "- Inspired by Bahá'í aesthetics: Persian illuminated manuscript style, "
        "garden motifs, rays of divine light, lotus flowers, cypress trees, "
        "intricate arabesque borders\n"
        "- Sacred number 9: express through 9 flower petals, 9 arches, 9 geometric tiles, "
        "9 rays of light, 9 garden paths, 9 lotus petals — any motif repeated exactly 9 times\n"
        "- Sacred number 19: express through 19 border elements, 19 leaf repeats, "
        "19 arabesque units, 19 arch columns — any motif repeated exactly 19 times\n"
        "- Let the numbers 9 and 19 govern the geometry and repetition throughout the composition\n"
        "- Beautiful and spiritually uplifting — suitable as a gift\n"
        "- NO text, letters, or words anywhere in the image\n"
        "- Photorealistic fine art or painterly style, rich jewel tones\n\n"
        "Output ONLY the image generation prompt. No explanation, no preamble."
    )

    return call_llm("design", [
        {"role": "system", "content": system_prompt},
        {"role": "user",   "content": user_message},
    ], temperature=0.8, max_tokens=300).strip()


def build_card_image_prompt(theme: str, citations: list[dict] | None = None) -> str:
    """
    Image prompt for a QUOTE CARD — a giveaway outreach piece for someone who
    has never encountered the Faith. Differs from the bookmark brief on
    purpose: welcoming and universally beautiful, no esoteric symbolism or
    numeric motif requirements, and composed so two landscape bands (the
    card's front and back faces) can be cropped from the portrait output.
    Runs on local Qwen (lean prompt — see CLAUDE.md rule 1).
    """
    system_prompt = build_system_prompt("artist", "design")

    citation_block = ""
    if citations:
        citation_block = "\n\nSpiritual citations to inspire the image:\n"
        for c in (citations or [])[:2]:
            citation_block += f'  — "{c.get("text", "")[:180]}" ({c.get("source", "")})\n'

    user_message = (
        f"Write an image generation prompt for a small spiritual gift card.\n\n"
        f"Theme: {theme}\n"
        f"{citation_block}\n\n"
        "Requirements:\n"
        "- The viewer may know NOTHING about the Bahá'í Faith: the image must feel "
        "welcoming and beautiful on its own — nature, light, gardens, sky, water — "
        "no religious iconography that needs explaining, no esoteric symbols\n"
        "- Serene and luminous; gentle dawn light, rich but soft jewel tones\n"
        "- Composition: beauty spread across the WHOLE frame with a calm, luminous "
        "middle region — no single critical detail at the extreme top or bottom edge\n"
        "- NO text, letters, or words anywhere in the image\n"
        "- Painterly fine art style, suitable for printing at business-card size\n\n"
        "Output ONLY the image generation prompt. No explanation, no preamble."
    )

    return call_llm("design", [
        {"role": "system", "content": system_prompt},
        {"role": "user",   "content": user_message},
    ], temperature=0.8, max_tokens=300).strip()


def _save_image_locally(image_bytes: bytes, prefix: str = "bookmark") -> Path:
    import uuid
    filename = f"{prefix}-{uuid.uuid4().hex[:8]}.jpg"
    out_path = OUTPUTS_DIR / filename
    out_path.write_bytes(image_bytes)
    return out_path


def generate_image(prompt: str, aspect_ratio: str = "2:3") -> dict:
    """
    Generate an image via xAI's image generation API.
    Always downloads and saves to outputs/ so the file persists after the remote URL expires.
    Returns: {image_url (local path), remote_url, model}
    """
    if not XAI_KEY:
        raise RuntimeError("XAI_API_KEY not set in .env")

    headers = {
        "Authorization": f"Bearer {XAI_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": XAI_IMAGE_MODEL,
        "prompt": prompt,
        "n": 1,
        "response_format": "url",
    }

    resp = requests.post(
        f"{XAI_BASE}/images/generations",
        headers=headers,
        json=payload,
        timeout=120,
    )
    resp.raise_for_status()
    data = resp.json()

    item = data["data"][0]

    if item.get("url"):
        remote_url = item["url"]
        # Download and save locally so the file outlives the temporary xAI URL
        dl = requests.get(remote_url, timeout=60)
        dl.raise_for_status()
        local_path = _save_image_locally(dl.content)
        return {
            "image_url": str(local_path),
            "remote_url": remote_url,
            "model": XAI_IMAGE_MODEL,
        }

    if item.get("b64_json"):
        local_path = _save_image_locally(base64.b64decode(item["b64_json"]))
        return {
            "image_url": str(local_path),
            "remote_url": None,
            "model": XAI_IMAGE_MODEL,
        }

    raise RuntimeError(f"Unexpected xAI image response format: {list(item.keys())}")
