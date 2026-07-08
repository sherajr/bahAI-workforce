"""
Google Docs for the Secretary — create/read/append. Uses agents/gdrive.py's
Drive sandbox for the ownership gate (CLAUDE.md rule 24): a brand new Doc
always lands inside her folder (free); appending to one outside it is
gated via the "docs_write" pending_actions kind, decided by the caller
(secretary.py) checking gdrive.is_in_her_folder() before choosing free vs
queued. Append-only, never a destructive replace-all — a single mistaken
edit can't wipe an existing document's content.
"""

import requests

from agents.gdrive import ensure_secretary_folder, DRIVE_API
from agents.google_auth import _headers  # noqa: F401 (re-exported)

DOCS_API = "https://docs.googleapis.com/v1/documents"


def create_document(title: str, initial_text: str = "") -> dict:
    """Always inside her Drive folder — a free action."""
    folder_id = ensure_secretary_folder()
    resp = requests.post(f"{DRIVE_API}/files", headers=_headers(), json={
        "name": title, "mimeType": "application/vnd.google-apps.document",
        "parents": [folder_id],
    }, timeout=30)
    resp.raise_for_status()
    doc_id = resp.json()["id"]
    if initial_text:
        append_text(doc_id, initial_text)
    return {"document_id": doc_id, "title": title}


def _extract_text(elements: list[dict]) -> str:
    out = []
    for el in elements:
        para = el.get("paragraph")
        if not para:
            continue
        for run in para.get("elements", []):
            text_run = run.get("textRun")
            if text_run:
                out.append(text_run.get("content", ""))
    return "".join(out)


def read_document(document_id: str) -> str:
    resp = requests.get(f"{DOCS_API}/{document_id}", headers=_headers(), timeout=30)
    resp.raise_for_status()
    body = resp.json().get("body", {}).get("content", [])
    return _extract_text(body)


def _end_index(document_id: str) -> int:
    resp = requests.get(f"{DOCS_API}/{document_id}", headers=_headers(), timeout=30)
    resp.raise_for_status()
    content = resp.json().get("body", {}).get("content", [])
    if not content:
        return 1
    return content[-1].get("endIndex", 1)


def append_text(document_id: str, text: str) -> dict:
    end = _end_index(document_id)
    insert_at = max(end - 1, 1)  # the doc's final index is an implicit newline
    resp = requests.post(f"{DOCS_API}/{document_id}:batchUpdate", headers=_headers(), json={
        "requests": [{"insertText": {"location": {"index": insert_at}, "text": text}}],
    }, timeout=30)
    resp.raise_for_status()
    return resp.json()


def apply_write(payload: dict) -> dict:
    """Dispatch for the gated 'docs_write' pending_actions kind."""
    action = payload.get("action")
    if action == "create":
        return create_document(payload["title"], payload.get("text", ""))
    elif action == "append":
        return append_text(payload["document_id"], payload["text"])
    raise ValueError(f"Unknown docs_write action: {action}")
