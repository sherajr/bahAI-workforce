"""
Google Sheets for the Secretary — create/read/append-row. Same Drive
sandbox gate as gdocs.py (CLAUDE.md rule 24): a brand new spreadsheet
always lands inside her folder (free); appending to one outside it is
gated via "sheets_write". Append-only, never a destructive overwrite of
existing cells.
"""

import requests

from agents.gdrive import ensure_secretary_folder, DRIVE_API
from agents.google_auth import _headers  # noqa: F401 (re-exported)

SHEETS_API = "https://sheets.googleapis.com/v4/spreadsheets"


def create_spreadsheet(title: str, header_row: list = None) -> dict:
    """Always inside her Drive folder — a free action."""
    folder_id = ensure_secretary_folder()
    resp = requests.post(f"{DRIVE_API}/files", headers=_headers(), json={
        "name": title, "mimeType": "application/vnd.google-apps.spreadsheet",
        "parents": [folder_id],
    }, timeout=30)
    resp.raise_for_status()
    spreadsheet_id = resp.json()["id"]
    if header_row:
        append_row(spreadsheet_id, header_row)
    return {"spreadsheet_id": spreadsheet_id, "title": title}


def read_range(spreadsheet_id: str, range_a1: str) -> list[list]:
    resp = requests.get(f"{SHEETS_API}/{spreadsheet_id}/values/{range_a1}",
                        headers=_headers(), timeout=30)
    resp.raise_for_status()
    return resp.json().get("values", [])


def append_row(spreadsheet_id: str, row: list, range_a1: str = "A1") -> dict:
    return append_rows(spreadsheet_id, [row], range_a1)


def append_rows(spreadsheet_id: str, rows: list[list], range_a1: str = "A1") -> dict:
    """
    One values:append call for the whole batch — the API takes many rows
    natively, so a 173-row fill is one metered HTTP call and one atomic
    append, not 173 round-trips.
    """
    resp = requests.post(
        f"{SHEETS_API}/{spreadsheet_id}/values/{range_a1}:append",
        headers=_headers(), params={"valueInputOption": "USER_ENTERED"},
        json={"values": rows}, timeout=30)
    resp.raise_for_status()
    return resp.json()
