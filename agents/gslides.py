"""
Google Slides for the Secretary — READ ONLY (CLAUDE.md rule 23: Slides
writes are explicitly out of scope this round). No write functions, no
pending_actions kind, no intent tag — the one module in the Workspace set
that's read-only by construction.
"""

import requests

from agents.google_auth import _headers  # noqa: F401 (re-exported)

SLIDES_API = "https://slides.googleapis.com/v1/presentations"


def _text_from_shape(shape: dict) -> str:
    out = []
    for el in shape.get("text", {}).get("textElements", []) or []:
        run = el.get("textRun")
        if run:
            out.append(run.get("content", ""))
    return "".join(out)


def read_presentation_text(presentation_id: str) -> str:
    resp = requests.get(f"{SLIDES_API}/{presentation_id}", headers=_headers(), timeout=30)
    resp.raise_for_status()
    data = resp.json()
    lines = []
    for i, slide in enumerate(data.get("slides", []), 1):
        slide_lines = []
        for el in slide.get("pageElements", []) or []:
            shape = el.get("shape")
            if shape:
                text = _text_from_shape(shape).strip()
                if text:
                    slide_lines.append(text)
        if slide_lines:
            lines.append(f"[Slide {i}]\n" + "\n".join(slide_lines))
    return "\n\n".join(lines) if lines else "(no text found)"
