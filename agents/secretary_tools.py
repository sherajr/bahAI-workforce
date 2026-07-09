"""
Tool schemas + dispatch for the Secretary's tool-calling loop
(agents/router.py's call_claude_agentic).

Every action she can take — read OR write — is a real Claude tool call now
(CLAUDE.md rule 22, migrated 2026-07-07). The previous design asked her to
embed custom `<event>`/`<sheet_append>`/etc. markup inside her plain-text
reply, parsed afterward by regex in secretary.py. Live testing showed that
design was unreliable at the exact thing it needed to be reliable at: in a
long session, she would repeatedly write a confident sentence ("Adding that
now") with NO markup behind it, or malformed markup, and nothing happened.
Structured tool-calling is Claude's native, heavily-trained mechanism for
"call this function with these validated arguments" — a far stronger
guarantee than "remember to also type this exact syntax." Every ownership/
approval gate that used to live in secretary.py's regex handlers (Calendar
rule 20, Drive rule 24, Gmail rule 25, WhatsApp rules 26-28) now lives here
instead, inside each write tool's handler — the safety model is unchanged,
only the trigger mechanism is.

SEND_WHATSAPP_TOOL is deliberately the only WhatsApp-related tool exposed
here: there is no tool for managing the `contacts` allowlist itself (rule
28) — that stays dashboard/API-only so the model can never grant itself a
new trusted recipient mid-conversation.

Every executor wraps its call in try/except and returns an error STRING on
failure (e.g. "not connected") rather than raising — a failed action is
something she can react to and relay honestly, not a hard stop. Because a
real tool result tells her the true outcome, she can (and should) report it
truthfully — no more "never state whether it worked" hedge needed for tool
calls that actually ran; that hedge remains only as a last-resort textual
safety net in secretary.py for the rare case she narrates without calling
anything.
"""

import json
from datetime import datetime

SEARCH_CALENDAR_TOOL = {
    "name": "search_calendar",
    "description": (
        "Search Sheraj's calendar over an arbitrary date range (past or "
        "future), optionally filtered by a keyword. Use this whenever he "
        "asks about anything beyond today/tomorrow, wants to look back, or "
        "is searching for a specific event by name — do not guess or say "
        "you can't see it; call this instead."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "start_date": {"type": "string", "description": "YYYY-MM-DD, inclusive"},
            "end_date": {"type": "string", "description": "YYYY-MM-DD, inclusive"},
            "query": {"type": "string", "description": "optional keyword filter"},
        },
        "required": ["start_date", "end_date"],
        "additionalProperties": False,
    },
}

SEARCH_DRIVE_TOOL = {
    "name": "search_drive",
    "description": (
        "Search Sheraj's Google Drive by name or content. Use this to find "
        "a file/Doc/Sheet/Slide deck before reading or acting on it — you "
        "need the id this returns for read_doc/read_sheet/read_slide_text "
        "or any append_doc/append_sheet_rows/organize_drive_file call."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "name or content keyword"},
            "mime_type": {"type": "string", "description": "optional, e.g. "
                         "'application/vnd.google-apps.document' to filter to Docs only"},
        },
        "required": ["query"],
        "additionalProperties": False,
    },
}

READ_DOC_TOOL = {
    "name": "read_doc",
    "description": "Read the full text of a Google Doc by its document id (from search_drive).",
    "input_schema": {
        "type": "object",
        "properties": {"document_id": {"type": "string"}},
        "required": ["document_id"],
        "additionalProperties": False,
    },
}

READ_SHEET_TOOL = {
    "name": "read_sheet",
    "description": (
        "Read a range of cells from a Google Sheet by its spreadsheet id "
        "(from search_drive). Omit range for a reasonable default (A1:Z100)."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "spreadsheet_id": {"type": "string"},
            "range": {"type": "string", "description": "A1 notation, e.g. 'Sheet1!A1:D20'"},
        },
        "required": ["spreadsheet_id"],
        "additionalProperties": False,
    },
}

READ_SLIDE_TEXT_TOOL = {
    "name": "read_slide_text",
    "description": "Read all text content from a Google Slides presentation by its id (from search_drive).",
    "input_schema": {
        "type": "object",
        "properties": {"presentation_id": {"type": "string"}},
        "required": ["presentation_id"],
        "additionalProperties": False,
    },
}

SEARCH_GMAIL_TOOL = {
    "name": "search_gmail",
    "description": (
        "Search Sheraj's Gmail using Gmail search syntax (e.g. "
        "'from:jane subject:dinner', 'is:unread'). Returns id/from/subject/"
        "date/snippet for each match — use read_gmail_message for the full body."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "max_results": {"type": "integer", "description": "default 10"},
        },
        "required": ["query"],
        "additionalProperties": False,
    },
}

READ_GMAIL_MESSAGE_TOOL = {
    "name": "read_gmail_message",
    "description": "Read the full body of one Gmail message by its id (from search_gmail).",
    "input_schema": {
        "type": "object",
        "properties": {"message_id": {"type": "string"}},
        "required": ["message_id"],
        "additionalProperties": False,
    },
}

LIST_PRODUCTS_TOOL = {
    "name": "list_products",
    "description": (
        "List products from the bahAI Workforce pipeline itself — the "
        "bookmarks and quote cards shown in the app's Products tab. Use "
        "this whenever Sheraj asks what products/bookmarks/cards exist, "
        "how many there are, or their status/revenue — never say you "
        "don't have access to the Products tab; call this instead."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "product_type": {
                "type": "string", "enum": ["bookmark", "quote_card"],
                "description": "Filter to one product type. Omit for both.",
            },
            "status": {
                "type": "string",
                "description": "Filter to an exact status value (e.g. 'draft', 'published'). Omit for all.",
            },
            "limit": {"type": "integer", "description": "Max most-recent products to return. Default 20."},
        },
        "additionalProperties": False,
    },
}

READ_TOOLS = [
    SEARCH_CALENDAR_TOOL, SEARCH_DRIVE_TOOL, READ_DOC_TOOL, READ_SHEET_TOOL,
    READ_SLIDE_TEXT_TOOL, SEARCH_GMAIL_TOOL, READ_GMAIL_MESSAGE_TOOL,
    LIST_PRODUCTS_TOOL,
]

# ── Write tools — CLAUDE.md rule 22 (migrated 2026-07-07) ──────────────────────

REMEMBER_TOOL = {
    "name": "remember",
    "description": "Save a durable fact about Sheraj for future conversations (not a to-do — use add_task for those).",
    "input_schema": {
        "type": "object",
        "properties": {
            "note": {"type": "string", "description": "short topic name, e.g. 'preferences'"},
            "fact": {"type": "string", "description": "the fact to remember"},
        },
        "required": ["fact"],
        "additionalProperties": False,
    },
}

ADD_TASK_TOOL = {
    "name": "add_task",
    "description": "Track a to-do for Sheraj.",
    "input_schema": {
        "type": "object",
        "properties": {
            "description": {"type": "string"},
            "due": {"type": "string", "description": "YYYY-MM-DD, omit if none"},
        },
        "required": ["description"],
        "additionalProperties": False,
    },
}

CREATE_EVENT_TOOL = {
    "name": "create_event",
    "description": (
        "Create a calendar event. Always lands on Sheraj's own 'bahAI "
        "Secretary' calendar — always free, no approval needed. For an "
        "all-day event use a date with no time in start (and end, if "
        "multi-day). Google Calendar's all-day end date is EXCLUSIVE — a "
        "range covering Aug 30 through Sep 4 needs end='2026-09-05'. Call "
        "this immediately when asked to add something to the calendar — "
        "never just say you will."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "start": {"type": "string", "description": "YYYY-MM-DDTHH:MM or YYYY-MM-DD (all-day)"},
            "end": {"type": "string", "description": "same format as start; omit for a 1-hour default"},
            "location": {"type": "string"},
            "description": {"type": "string"},
        },
        "required": ["title", "start"],
        "additionalProperties": False,
    },
}

UPDATE_EVENT_TOOL = {
    "name": "update_event",
    "description": (
        "Change fields on an existing event, referenced by its [E#] id from "
        "context or search_calendar. Only include the fields that change. "
        "If the event isn't on Sheraj's own calendar this queues for his "
        "approval instead of applying immediately — the tool result tells "
        "you which happened. There is no 'color' field — Calendar colors "
        "aren't controllable through this connection."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "ref": {"type": "string", "description": "e.g. 'E3'"},
            "summary": {"type": "string", "description": "new title"},
            "start": {"type": "string"},
            "end": {"type": "string"},
            "location": {"type": "string"},
            "description": {"type": "string"},
        },
        "required": ["ref"],
        "additionalProperties": False,
    },
}

DELETE_EVENT_TOOL = {
    "name": "delete_event",
    "description": (
        "Delete an existing event by its [E#] ref. Queues for Sheraj's "
        "approval instead of deleting immediately if it isn't on his own "
        "calendar."
    ),
    "input_schema": {
        "type": "object",
        "properties": {"ref": {"type": "string"}},
        "required": ["ref"],
        "additionalProperties": False,
    },
}

SET_EVENT_REMINDERS_TOOL = {
    "name": "set_event_reminders",
    "description": "Adjust an event's reminder lead-times (minutes before start).",
    "input_schema": {
        "type": "object",
        "properties": {
            "ref": {"type": "string"},
            "offsets": {"type": "string", "description": "comma-separated minutes, e.g. '120,30'; empty string turns reminders off"},
        },
        "required": ["ref", "offsets"],
        "additionalProperties": False,
    },
}

SET_REMINDER_TOOL = {
    "name": "set_reminder",
    "description": (
        "Set a one-off or recurring reminder message (separate from calendar "
        "events). Default lead-times already exist for located events and "
        "Holy Day/Feast evenings — don't duplicate those with this tool."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "message": {"type": "string"},
            "at": {"type": "string", "description": "YYYY-MM-DD HH:MM"},
            "recurrence": {"type": "string", "description": "'', 'daily', 'weekly', or 'every:Nd' (e.g. 'every:19d')"},
            "wake_me": {"type": "boolean", "description": "true only if Sheraj explicitly says to override quiet hours"},
        },
        "required": ["message", "at"],
        "additionalProperties": False,
    },
}

SEND_EMAIL_TOOL = {
    "name": "send_email",
    "description": (
        "Send an email. ALWAYS queues for Sheraj's explicit approval, no "
        "matter who it's to — never sent automatically, so it's safe to "
        "call this whenever asked to send an email."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "to": {"type": "array", "items": {"type": "string"}},
            "subject": {"type": "string"},
            "body": {"type": "string"},
        },
        "required": ["to", "body"],
        "additionalProperties": False,
    },
}

CREATE_DOC_TOOL = {
    "name": "create_doc",
    "description": "Create a new Google Doc. Always lands in Sheraj's own 'bahAI Secretary' Drive folder — always free.",
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "text": {"type": "string", "description": "initial text, can be empty"},
        },
        "required": ["title"],
        "additionalProperties": False,
    },
}

APPEND_DOC_TOOL = {
    "name": "append_doc",
    "description": (
        "Append text to an EXISTING Doc (id from search_drive/read_doc). "
        "Free if it's already in Sheraj's Drive folder, otherwise queues "
        "for his approval — the tool result tells you which happened."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "document_id": {"type": "string"},
            "text": {"type": "string"},
        },
        "required": ["document_id", "text"],
        "additionalProperties": False,
    },
}

CREATE_SHEET_TOOL = {
    "name": "create_sheet",
    "description": "Create a new Google Sheet with an optional header row and initial data rows. Always lands in Sheraj's own Drive folder — always free.",
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "header": {"type": "array", "items": {"type": "string"}, "description": "optional column headers"},
            "rows": {"type": "array", "items": {"type": "array", "items": {"type": "string"}},
                     "description": "optional initial data rows"},
        },
        "required": ["title"],
        "additionalProperties": False,
    },
}

APPEND_SHEET_ROWS_TOOL = {
    "name": "append_sheet_rows",
    "description": (
        "Append one or MANY rows to an EXISTING Sheet (id from search_drive) "
        "in a single call — for a bulk fill, pass all the rows you have at "
        "once rather than calling this once per row. Free if the Sheet is "
        "already in Sheraj's Drive folder, otherwise queues for his approval."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "spreadsheet_id": {"type": "string"},
            "rows": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}},
        },
        "required": ["spreadsheet_id", "rows"],
        "additionalProperties": False,
    },
}

SEND_WHATSAPP_TOOL = {
    "name": "send_whatsapp",
    "description": (
        "Send a WhatsApp message to someone OTHER than Sheraj on his behalf "
        "(e.g. 'text Jane and let her know...'). You never need this to "
        "reply to Sheraj himself — that happens automatically. Free and "
        "immediate for an allowlisted contact if they've messaged in the "
        "last 24 hours (otherwise falls back to a template automatically); "
        "anyone not on the allowlist queues for Sheraj's approval instead."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "to": {"type": "string", "description": "phone number in E.164, e.g. +15551234567"},
            "body": {"type": "string"},
        },
        "required": ["to", "body"],
        "additionalProperties": False,
    },
}

ORGANIZE_DRIVE_FILE_TOOL = {
    "name": "organize_drive_file",
    "description": (
        "Move a Drive file into Sheraj's own folder, rename it, or trash it. "
        "Moving INTO his folder is always free. Renaming/trashing is free "
        "only if the file is already in his folder, otherwise queues for "
        "his approval."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "file_id": {"type": "string"},
            "action": {"type": "string", "enum": ["move_to_mine", "rename", "trash"]},
            "new_name": {"type": "string", "description": "required for action='rename'"},
        },
        "required": ["file_id", "action"],
        "additionalProperties": False,
    },
}

EDIT_PRODUCT_TOOL = {
    "name": "edit_product",
    "description": (
        "Directly overwrite one or more fields of a bookmark listing (by id, "
        "from list_products) with EXACTLY what Sheraj dictates — title, "
        "description, the bookmark's quote text, tags, materials, or a "
        "price note. This is a literal text overwrite of what he says, not "
        "a rewrite you compose yourself — never invent or improve the "
        "wording. Bookmarks only; quote cards have no listing to edit this "
        "way. Doesn't publish anything or cost money — just updates the "
        "saved listing, same as a manual dashboard edit."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "product_id": {"type": "string"},
            "title": {"type": "string"},
            "description": {"type": "string"},
            "bookmark_quote": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}},
            "materials": {"type": "array", "items": {"type": "string"}},
            "price_note": {"type": "string"},
        },
        "required": ["product_id"],
        "additionalProperties": False,
    },
}

WRITE_TOOLS = [
    REMEMBER_TOOL, ADD_TASK_TOOL, CREATE_EVENT_TOOL, UPDATE_EVENT_TOOL,
    DELETE_EVENT_TOOL, SET_EVENT_REMINDERS_TOOL, SET_REMINDER_TOOL,
    SEND_EMAIL_TOOL, CREATE_DOC_TOOL, APPEND_DOC_TOOL, CREATE_SHEET_TOOL,
    APPEND_SHEET_ROWS_TOOL, ORGANIZE_DRIVE_FILE_TOOL, SEND_WHATSAPP_TOOL,
    EDIT_PRODUCT_TOOL,
]

ALL_TOOLS = READ_TOOLS + WRITE_TOOLS


def _fmt_files(files: list[dict]) -> str:
    if not files:
        return "No matching files found."
    lines = []
    for f in files:
        lines.append(f"[{f['id']}] {f['name']} ({f.get('mimeType', '')})")
    return "\n".join(lines)


def _new_ref(event_map: dict) -> str:
    return f"E{len(event_map) + 1}"


def make_executor(event_map: dict, effects: dict):
    """
    Returns executor(name, input) -> str for use with call_claude_agentic.
    Read tools just look things up. Write tools actually perform the action
    (or queue it for approval per the ownership gates below) and record it
    into `effects` — the same structure secretary._ground_truth_confirmation
    renders into a code-authored status line, and the same one
    secretary._finalize_reply checks to catch a reply that narrates an
    action without ever having called a tool for it.

    Mutates event_map in place: any calendar event this surfaces (via
    search_calendar, create_event, or update_event's fresh copy) gets/keeps
    an E# ref so it's referenceable by update_event/delete_event/
    set_event_reminders later in the SAME turn.

    A write tool called twice with byte-identical arguments in the same turn
    executes only once — the second call gets told it already happened
    instead of silently repeating a create/send/append. This is the
    tool-call-era equivalent of the old text-tag dedupe (CLAUDE.md rule 22).
    """
    from agents import gcal, gdrive, gdocs, gsheets, gslides, gmail, whatsapp, state
    from agents import secretary_store as store

    done_calls: set[str] = set()
    write_tool_names = {t["name"] for t in WRITE_TOOLS}

    def _queue(kind: str, desc: str, payload: dict) -> int:
        return store.add_pending_action(kind, desc, json.dumps(payload))

    def executor(name: str, tool_input: dict) -> str:
        if name in write_tool_names:
            call_key = name + ":" + json.dumps(tool_input, sort_keys=True, default=str)
            if call_key in done_calls:
                return "Already done earlier in this turn — no need to repeat it."
            done_calls.add(call_key)
        try:
            if name == "search_calendar":
                start = tool_input.get("start_date", "")
                end = tool_input.get("end_date", "")
                query = tool_input.get("query") or None
                events = gcal.search_events(start, end, query=query)
                if not events:
                    return "No matching events found."
                lines = []
                for ev in events:
                    ref = _new_ref(event_map)
                    event_map[ref] = ev
                    when = ev["start"].replace("T", " ")[:16]
                    own = "yours" if ev["editable_by_secretary"] else f"cal: {ev['calendar_name'] or 'other'}"
                    loc = f" @ {ev['location']}" if ev["location"] else ""
                    lines.append(f"[{ref}] {when} | {ev['summary']}{loc} | ({own})")
                return "\n".join(lines)

            elif name == "search_drive":
                files = gdrive.search_files(tool_input["query"], mime_type=tool_input.get("mime_type"))
                return _fmt_files(files)

            elif name == "read_doc":
                return gdocs.read_document(tool_input["document_id"]) or "(empty document)"

            elif name == "read_sheet":
                rng = tool_input.get("range") or "A1:Z100"
                rows = gsheets.read_range(tool_input["spreadsheet_id"], rng)
                return "\n".join(", ".join(row) for row in rows) if rows else "(empty range)"

            elif name == "read_slide_text":
                return gslides.read_presentation_text(tool_input["presentation_id"])

            elif name == "search_gmail":
                msgs = gmail.search_messages(tool_input["query"], max_results=tool_input.get("max_results", 10))
                if not msgs:
                    return "No matching emails found."
                return "\n".join(
                    f"[{m['id']}] {m['from']} — {m['subject']} ({m['date']}): {m['snippet']}"
                    for m in msgs)

            elif name == "read_gmail_message":
                msg = gmail.get_message(tool_input["message_id"])
                return (f"From: {msg['from']}\nTo: {msg['to']}\nSubject: {msg['subject']}\n"
                        f"Date: {msg['date']}\n\n{msg['body']}")

            elif name == "list_products":
                products = state.get_all_products()
                ptype = tool_input.get("product_type")
                status_filter = tool_input.get("status")
                limit = tool_input.get("limit") or 20
                if ptype:
                    products = [p for p in products if (p.get("product_type") or "bookmark") == ptype]
                if status_filter:
                    products = [p for p in products if p.get("status") == status_filter]
                products = products[:limit]
                if not products:
                    return "No products found matching that filter."
                lines = []
                for p in products:
                    published = "published to Etsy" if p.get("etsy_listing_id") else "not published"
                    best_effort = " (best effort — target not fully reached)" if p.get("target_reached") == 0 else ""
                    lines.append(
                        f"[{p['id']}] {p.get('title') or '(untitled)'} — "
                        f"{p.get('product_type') or 'bookmark'}, {p.get('status')}, {published}, "
                        f"revenue ${p.get('revenue') or 0:.2f}, created {p.get('created_at')}{best_effort}")
                return "\n".join(lines)

            elif name == "remember":
                note = tool_input.get("note") or "general"
                fact = tool_input.get("fact", "").strip()
                if not fact:
                    return "No fact given — nothing saved."
                store.write_memory_note(note, fact)
                effects["remembered"].append(fact)
                return f"Saved under '{note}': {fact}"

            elif name == "add_task":
                desc = tool_input.get("description", "").strip()
                due = tool_input.get("due") or None
                if not desc:
                    return "No description given — nothing added."
                store.add_task(desc, due=due)
                effects["tasks_added"].append(desc)
                return f"Added task: {desc}" + (f" (due {due})" if due else "")

            elif name == "create_event":
                title = tool_input.get("title", "").strip()
                start = tool_input.get("start", "")
                if not title or not start:
                    return "Missing title or start — nothing created."
                ev = gcal.create_event(title, start, end_iso=tool_input.get("end") or None,
                                       location=tool_input.get("location") or "",
                                       description=tool_input.get("description") or "")
                ref = _new_ref(event_map)
                event_map[ref] = ev
                effects["events"].append(f"created: {ev['summary']} at {ev['start']}")
                return f"Created (ref {ref}): '{ev['summary']}' at {ev['start']}."

            elif name == "update_event":
                ref = tool_input.get("ref", "")
                ev = event_map.get(ref)
                if not ev:
                    return f"Unknown ref {ref!r} — look it up again with search_calendar first."
                fields = {"summary": tool_input.get("summary"), "location": tool_input.get("location"),
                          "description": tool_input.get("description"),
                          "start_iso": tool_input.get("start"), "end_iso": tool_input.get("end")}
                fields = {k: v for k, v in fields.items() if v is not None}
                if not fields:
                    return "No fields given to change — nothing updated."
                desc = f"Update '{ev['summary']}' ({ev['calendar_name'] or 'calendar'}): " + \
                       ", ".join(f"{k}={v}" for k, v in fields.items())
                if ev["editable_by_secretary"]:
                    new_ev = gcal.update_event(ev["calendar_id"], ev["id"], **fields)
                    event_map[ref] = new_ev
                    effects["events"].append(f"updated: {new_ev['summary']} ({', '.join(fields)})")
                    return f"Updated '{new_ev['summary']}': changed {', '.join(fields)}."
                aid = _queue("event_update", desc,
                            {"calendar_id": ev["calendar_id"], "event_id": ev["id"], "fields": fields})
                effects["queued_for_approval"].append(f"#{aid} {desc}")
                return f"That event isn't on your own calendar — queued as action #{aid} for Sheraj's approval."

            elif name == "delete_event":
                ref = tool_input.get("ref", "")
                ev = event_map.get(ref)
                if not ev:
                    return f"Unknown ref {ref!r} — look it up again with search_calendar first."
                if ev["editable_by_secretary"]:
                    gcal.delete_event(ev["calendar_id"], ev["id"])
                    effects["events"].append(f"deleted: {ev['summary']}")
                    return f"Deleted '{ev['summary']}'."
                desc = f"Delete '{ev['summary']}' from {ev['calendar_name'] or 'his calendar'}"
                aid = _queue("event_delete", desc, {"calendar_id": ev["calendar_id"], "event_id": ev["id"]})
                effects["queued_for_approval"].append(f"#{aid} {desc}")
                return f"That event isn't on your own calendar — queued as action #{aid} for Sheraj's approval."

            elif name == "set_event_reminders":
                ref = tool_input.get("ref", "")
                offsets = tool_input.get("offsets", "")
                ev = event_map.get(ref)
                if not ev:
                    return f"Unknown ref {ref!r} — look it up again with search_calendar first."
                store.set_event_override(ev["id"], offsets)
                effects["reminders"].append(f"lead-times for '{ev['summary']}' -> {offsets or 'off'}")
                return f"Updated reminder lead-times for '{ev['summary']}'."

            elif name == "set_reminder":
                message = tool_input.get("message", "").strip()
                at = tool_input.get("at", "")
                recurrence = tool_input.get("recurrence") or ""
                wake_me = bool(tool_input.get("wake_me", False))
                if not message or not at:
                    return "Missing message or time — nothing set."
                try:
                    fire_at = datetime.fromisoformat(at.replace(" ", "T", 1) if "T" not in at else at)
                except ValueError:
                    return f"'{at}' doesn't parse as a time — use YYYY-MM-DD HH:MM."
                store.add_reminder(message, fire_at.strftime("%Y-%m-%d %H:%M:%S"),
                                   recurrence=recurrence or None, wake_me=wake_me)
                effects["reminders"].append(f"{message} at {at}" + (f" ({recurrence})" if recurrence else ""))
                return f"Reminder set for {at}: {message}"

            elif name == "send_email":
                to = tool_input.get("to") or []
                subject = tool_input.get("subject", "")
                body = tool_input.get("body", "").strip()
                if not to or not body:
                    return "Missing recipient(s) or body — nothing queued."
                desc = f"Send email to {', '.join(to)}: subject '{subject}'"
                aid = _queue("gmail_send", desc, {"to": to, "subject": subject, "body": body})
                effects["queued_for_approval"].append(f"#{aid} {desc}")
                return f"Queued as action #{aid} — email sends always need Sheraj's approval first."

            elif name == "create_doc":
                title = tool_input.get("title", "").strip()
                if not title:
                    return "Missing title — nothing created."
                doc = gdocs.create_document(title, tool_input.get("text") or "")
                effects["workspace"].append(f"created Doc '{title}' ({doc['document_id']})")
                return f"Created Doc '{title}' (id {doc['document_id']})."

            elif name == "append_doc":
                doc_id = tool_input.get("document_id", "").strip()
                text = tool_input.get("text", "").strip()
                if not doc_id or not text:
                    return "Missing document_id or text — nothing appended."
                desc = f"Append text to Doc {doc_id}"
                if gdrive.is_in_her_folder(doc_id):
                    gdocs.append_text(doc_id, text)
                    effects["workspace"].append(f"appended to Doc {doc_id}")
                    return f"Appended text to Doc {doc_id}."
                aid = _queue("docs_write", desc, {"action": "append", "document_id": doc_id, "text": text})
                effects["queued_for_approval"].append(f"#{aid} {desc}")
                return f"That Doc isn't in your Drive folder — queued as action #{aid} for Sheraj's approval."

            elif name == "create_sheet":
                title = tool_input.get("title", "").strip()
                header = tool_input.get("header") or None
                rows = tool_input.get("rows") or []
                if not title:
                    return "Missing title — nothing created."
                sheet = gsheets.create_spreadsheet(title, header)
                if rows:
                    gsheets.append_rows(sheet["spreadsheet_id"], rows)
                extra = f" with {len(rows)} data rows" if rows else ""
                effects["workspace"].append(f"created Sheet '{title}'{extra} ({sheet['spreadsheet_id']})")
                return f"Created Sheet '{title}' (id {sheet['spreadsheet_id']}){extra}."

            elif name == "append_sheet_rows":
                sheet_id = tool_input.get("spreadsheet_id", "").strip()
                rows = tool_input.get("rows") or []
                if not sheet_id or not rows:
                    return "Missing spreadsheet_id or rows — nothing appended."
                noun = f"{len(rows)} rows" if len(rows) > 1 else "row"
                desc = f"Append {noun} to Sheet {sheet_id}"
                if gdrive.is_in_her_folder(sheet_id):
                    gsheets.append_rows(sheet_id, rows)
                    effects["workspace"].append(f"appended {noun} to Sheet {sheet_id}")
                    return f"Appended {noun} to Sheet {sheet_id}."
                aid = _queue("sheets_write", desc, {"spreadsheet_id": sheet_id, "rows": rows})
                effects["queued_for_approval"].append(f"#{aid} {desc}")
                return f"That Sheet isn't in your Drive folder — queued as action #{aid} for Sheraj's approval."

            elif name == "send_whatsapp":
                to = tool_input.get("to", "").strip()
                body = tool_input.get("body", "").strip()
                if not to or not body:
                    return "Missing recipient or body — nothing sent."
                if not whatsapp.is_configured():
                    return "WhatsApp isn't connected yet — nothing sent."
                if whatsapp.is_owner(to) or store.is_allowlisted(to):
                    whatsapp.send_best_effort(to, body)
                    effects["workspace"].append(f"sent WhatsApp message to {to}")
                    return f"Sent WhatsApp message to {to}."
                desc = f"Send WhatsApp message to {to}: {body[:60]}"
                aid = _queue("whatsapp_send", desc, {"to": to, "body": body})
                effects["queued_for_approval"].append(f"#{aid} {desc}")
                return f"{to} isn't on your trusted contacts list — queued as action #{aid} for your approval."

            elif name == "organize_drive_file":
                file_id = tool_input.get("file_id", "").strip()
                action = tool_input.get("action", "")
                new_name = (tool_input.get("new_name") or "").strip()
                if not file_id or action not in ("move_to_mine", "rename", "trash"):
                    return f"Bad file_id or action ({action!r}) — nothing done."
                already_hers = gdrive.is_in_her_folder(file_id)
                if action == "move_to_mine":
                    gdrive.move_file(file_id, gdrive.ensure_secretary_folder())
                    effects["workspace"].append(f"moved file {file_id} into your Drive folder")
                    return f"Moved file {file_id} into your Drive folder."
                elif action == "rename":
                    if not new_name:
                        return "Missing new_name — nothing renamed."
                    if already_hers:
                        gdrive.rename_file(file_id, new_name)
                        effects["workspace"].append(f"renamed file {file_id} to '{new_name}'")
                        return f"Renamed file {file_id} to '{new_name}'."
                    aid = _queue("drive_write", f"Rename file {file_id} to '{new_name}'",
                                {"action": "rename", "file_id": file_id, "new_name": new_name})
                    effects["queued_for_approval"].append(f"#{aid} Rename file {file_id} to '{new_name}'")
                    return f"That file isn't in your Drive folder — queued as action #{aid} for approval."
                else:  # trash
                    if already_hers:
                        gdrive.trash_file(file_id)
                        effects["workspace"].append(f"trashed file {file_id}")
                        return f"Trashed file {file_id}."
                    aid = _queue("drive_write", f"Trash file {file_id}", {"action": "trash", "file_id": file_id})
                    effects["queued_for_approval"].append(f"#{aid} Trash file {file_id}")
                    return f"That file isn't in your Drive folder — queued as action #{aid} for approval."

            elif name == "edit_product":
                product_id = (tool_input.get("product_id") or "").strip()
                if not product_id:
                    return "No product id given — nothing edited."
                with state._connect() as conn:
                    row = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
                if not row:
                    return f"No product found with id {product_id}."
                product = dict(row)
                if (product.get("product_type") or "bookmark") != "bookmark":
                    return "That's a quote card, not a bookmark — edit_product only works on bookmark listings."
                listing_copy = product.get("listing_copy") or "{}"
                listing = json.loads(listing_copy) if listing_copy else {}
                editable = {"title", "description", "bookmark_quote", "tags", "materials", "price_note"}
                edits = {k: v for k, v in tool_input.items()
                         if k in editable and k != "product_id" and v is not None}
                if not edits:
                    return "No fields to edit were given."

                # Mirror api.edit_product: capture old quote before edits,
                # scrub marketing text, demote verification + re-render on
                # real quote change (rules 4 & 8; owner hand-edit path).
                old_quote = (listing.get("bookmark_quote") or "").strip()
                for field, value in edits.items():
                    listing[field] = value
                new_quote = (listing.get("bookmark_quote") or "").strip()
                quote_changed = "bookmark_quote" in edits and new_quote != old_quote

                from agents.scribe import _sanitize_claims
                listing = _sanitize_claims(listing)

                if quote_changed:
                    listing["quote_verified"] = False

                update_kwargs = {"listing_copy": json.dumps(listing)}
                if "title" in edits:
                    # Post-scrub title, not the raw edit value.
                    update_kwargs["title"] = listing.get("title")

                rerender_note = None
                if quote_changed:
                    try:
                        from agents.compositor import render_bookmark_pair
                        from agents import layout as layout_opts
                        layout = layout_opts.sanitize(
                            "bookmark",
                            json.loads(product.get("layout_json") or "null"),
                        )
                        rendered = render_bookmark_pair(
                            product.get("image_url") or "",
                            listing.get("bookmark_quote") or "",
                            layout=layout,
                        )
                        update_kwargs["front_image"] = rendered["front_path"]
                        update_kwargs["back_image"] = rendered["back_path"]
                    except Exception as e:
                        rerender_note = (
                            f"Text saved, but the printed face could not be "
                            f"re-rendered ({e})."
                        )

                state.update_product(product_id, **update_kwargs)
                changed = ", ".join(edits.keys())
                effects["workspace"].append(f"edited product {product_id} ({changed})")
                msg = f"Updated {changed} on product {product_id}."
                if quote_changed:
                    msg += " The quote is no longer Librarian-verified."
                if rerender_note:
                    msg += f" {rerender_note}"
                return msg

            else:
                return f"Unknown tool: {name}"
        except Exception as e:
            if name in write_tool_names:
                effects["errors"].append(f"{name} failed: {type(e).__name__}")
            return f"{name} failed ({type(e).__name__}): {e}"

    return executor
