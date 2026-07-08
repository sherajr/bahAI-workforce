"""
Google Drive for the Secretary — search/read (unrestricted) plus a
sandboxed free-write folder, mirroring gcal.py's Calendar ownership model
(CLAUDE.md rule 24).

Ownership hard rule: she creates/organizes freely ONLY inside the
"bahAI Secretary" Drive folder she creates on first connect. Any
rename/move/trash touching a file NOT in that folder queues for Sheraj's
approval — `is_in_her_folder()` is the gate, same shape as
gcal.is_her_calendar(). gdocs.py and gsheets.py build on this same folder
for their own "create = free, edit-elsewhere = gated" split.
"""

import json

import requests

from agents import secretary_store as store
from agents.google_auth import get_valid_token, _headers  # noqa: F401 (re-exported)

DRIVE_API = "https://www.googleapis.com/drive/v3"
DRIVE_UPLOAD_API = "https://www.googleapis.com/upload/drive/v3/files"

SECRETARY_FOLDER_NAME = "bahAI Secretary"

FILE_FIELDS = "id,name,mimeType,parents,trashed,webViewLink,modifiedTime"


def ensure_secretary_folder() -> str:
    """Find or create the 'bahAI Secretary' Drive folder; cache its id in
    the private DB (same KV store gcal.py uses for secretary_calendar_id)."""
    store.init_db()
    folder_id = store.get_setting("secretary_drive_folder_id")
    if folder_id:
        return folder_id
    resp = requests.get(f"{DRIVE_API}/files", headers=_headers(), params={
        "q": f"name = '{SECRETARY_FOLDER_NAME}' and mimeType = "
             "'application/vnd.google-apps.folder' and trashed = false",
        "fields": f"files({FILE_FIELDS})",
    }, timeout=30)
    resp.raise_for_status()
    items = resp.json().get("files", [])
    if items:
        folder_id = items[0]["id"]
        store.set_setting("secretary_drive_folder_id", folder_id)
        return folder_id
    resp = requests.post(f"{DRIVE_API}/files", headers=_headers(), json={
        "name": SECRETARY_FOLDER_NAME,
        "mimeType": "application/vnd.google-apps.folder",
    }, timeout=30)
    resp.raise_for_status()
    folder_id = resp.json()["id"]
    store.set_setting("secretary_drive_folder_id", folder_id)
    return folder_id


def her_folder_id() -> str | None:
    store.init_db()
    return store.get_setting("secretary_drive_folder_id")


def is_in_her_folder(file_id: str) -> bool:
    """The gate: True only if the file's parents include her sandbox folder."""
    her_folder = her_folder_id()
    if not her_folder:
        return False
    try:
        meta = get_file_metadata(file_id)
    except Exception:
        return False
    return her_folder in (meta.get("parents") or [])


# ── Reads — unrestricted, same principle as gcal.list_events ──────────────────

def search_files(query: str, mime_type: str = None, max_results: int = 10) -> list[dict]:
    q_parts = ["trashed = false"]
    if query:
        safe_query = query.replace("'", "\\'").replace("\\", "\\\\")
        q_parts.append(f"(name contains '{safe_query}' or fullText contains '{safe_query}')")
    if mime_type:
        q_parts.append(f"mimeType = '{mime_type}'")
    resp = requests.get(f"{DRIVE_API}/files", headers=_headers(), params={
        "q": " and ".join(q_parts),
        "fields": f"files({FILE_FIELDS})",
        "pageSize": max_results,
    }, timeout=30)
    resp.raise_for_status()
    return resp.json().get("files", [])


def get_file_metadata(file_id: str) -> dict:
    resp = requests.get(f"{DRIVE_API}/files/{file_id}", headers=_headers(),
                        params={"fields": FILE_FIELDS}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def download_text(file_id: str) -> str:
    """Best-effort plain-text read for a non-Google-native file. Google Docs/
    Sheets/Slides have their own read functions (gdocs.py/gsheets.py/
    gslides.py) that export via their own APIs instead of this endpoint."""
    resp = requests.get(f"{DRIVE_API}/files/{file_id}", headers=_headers(),
                        params={"alt": "media"}, timeout=30)
    resp.raise_for_status()
    return resp.text


# ── Writes — create is always free (lands in her folder); everything else
# touching a file outside her folder is gated via pending_actions ────────────

def create_text_file(name: str, content: str, mime_type: str = "text/plain") -> dict:
    """Always lands inside her sandbox folder — a free action, never gated."""
    folder_id = ensure_secretary_folder()
    metadata = {"name": name, "parents": [folder_id]}
    boundary = "bahai_secretary_boundary"
    body = (
        f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n"
        f"{json.dumps(metadata)}\r\n"
        f"--{boundary}\r\nContent-Type: {mime_type}\r\n\r\n"
        f"{content}\r\n--{boundary}--"
    )
    headers = _headers()
    headers["Content-Type"] = f"multipart/related; boundary={boundary}"
    resp = requests.post(f"{DRIVE_UPLOAD_API}?uploadType=multipart&fields={FILE_FIELDS}",
                         headers=headers, data=body.encode("utf-8"), timeout=30)
    resp.raise_for_status()
    return resp.json()


def move_file(file_id: str, new_parent_id: str) -> dict:
    meta = get_file_metadata(file_id)
    old_parents = ",".join(meta.get("parents") or [])
    resp = requests.patch(f"{DRIVE_API}/files/{file_id}", headers=_headers(),
                          params={"addParents": new_parent_id, "removeParents": old_parents,
                                  "fields": FILE_FIELDS},
                          timeout=30)
    resp.raise_for_status()
    return resp.json()


def rename_file(file_id: str, new_name: str) -> dict:
    resp = requests.patch(f"{DRIVE_API}/files/{file_id}", headers=_headers(),
                          json={"name": new_name}, params={"fields": FILE_FIELDS}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def trash_file(file_id: str) -> dict:
    resp = requests.patch(f"{DRIVE_API}/files/{file_id}", headers=_headers(),
                          json={"trashed": True}, params={"fields": FILE_FIELDS}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def apply_write(payload: dict) -> dict:
    """Dispatch for the gated 'drive_write' pending_actions kind (called
    from secretary.py's execute_pending_action after Sheraj approves)."""
    action = payload.get("action")
    file_id = payload["file_id"]
    if action == "move":
        return move_file(file_id, payload["new_parent_id"])
    elif action == "rename":
        return rename_file(file_id, payload["new_name"])
    elif action == "trash":
        return trash_file(file_id)
    raise ValueError(f"Unknown drive_write action: {action}")
