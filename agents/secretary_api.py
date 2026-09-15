"""
The Secretary's (Abigail's) HTTP routes -- Google Workspace OAuth, WhatsApp
(rules 15-28), and her chat/memory/tasks/reminders/approvals surface --
extracted from api.py.

Business logic lives in agents/secretary*.py, agents/gcal.py + g*.py,
agents/whatsapp.py, agents/google_auth.py and agents/scheduler.py; every
route here is a thin, lazily-imported wrapper, unchanged from its original
body in api.py. The WhatsApp webhook is the one endpoint in this whole API
meant to be reachable from the public internet (rule 26); its own HMAC
signature check is unaffected by which router file it lives in, since the
owner-gate middleware and the webhook's own auth both run on the raw request
path, before FastAPI has decided which router handles it.

No prefix on this router -- see wallet_api.py's docstring for why.
"""

import json
import os
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel

from agents.jobs import _esc

router = APIRouter()


# --- Google Workspace OAuth (the Secretary's; mirrors the Etsy flow) ---
# One consent screen, one token, shared by Calendar/Gmail/Drive/Docs/Sheets/
# Slides (agents/google_auth.py). Renamed from /gcal/* now that it covers
# more than Calendar — single-user project, no back-compat shim needed.

@router.get("/google/oauth/start")
def google_oauth_start():
    """Step 1 of Google OAuth. Open in a browser — one-time approval."""
    from agents.google_auth import build_auth_url
    from fastapi.responses import RedirectResponse, HTMLResponse
    if not os.getenv("GOOGLE_CLIENT_ID"):
        return HTMLResponse("""
            <html><body style="font-family:sans-serif;padding:2em">
            <h2>⚠️ Google credentials missing</h2>
            <p>Add Google credentials to <strong>.env</strong> first (one-time, ~5 minutes):</p>
            <ol>
              <li>Go to <a href="https://console.cloud.google.com/projectcreate" target="_blank">console.cloud.google.com</a> and create a project (any name, e.g. "bahAI Secretary")</li>
              <li>In <em>APIs &amp; Services → Library</em>, enable: <strong>Google Calendar API</strong>,
              <strong>Gmail API</strong>, <strong>Google Drive API</strong>, <strong>Google Docs API</strong>,
              <strong>Google Sheets API</strong>, and <strong>Google Slides API</strong></li>
              <li>In <em>APIs &amp; Services → OAuth consent screen</em>: choose <strong>External</strong>, fill in the app name and your email, and add yourself (sherajr22@gmail.com) as a <strong>Test user</strong></li>
              <li>In <em>APIs &amp; Services → Credentials → Create credentials → OAuth client ID</em>: choose <strong>Web application</strong> and add this authorized redirect URI: <code>http://localhost:8765/google/oauth/callback</code></li>
              <li>Copy the Client ID into <code>GOOGLE_CLIENT_ID</code> and the secret into <code>GOOGLE_CLIENT_SECRET</code> in <code>.env</code></li>
              <li>Restart the API, then revisit this page</li>
            </ol>
            </body></html>
        """, status_code=400)
    return RedirectResponse(url=build_auth_url())


@router.get("/google/oauth/callback")
def google_oauth_callback(
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
):
    """Google redirects here after approval. Exchanges the code and creates
    her Calendar + Drive sandboxes (idempotent — safe on reconnect too)."""
    from agents.google_auth import exchange_code
    from agents.gcal import ensure_secretary_calendar, SECRETARY_CALENDAR_NAME
    from agents.gdrive import ensure_secretary_folder, SECRETARY_FOLDER_NAME
    from fastapi.responses import HTMLResponse

    if error or not code or not state:
        return HTMLResponse(f"""
            <html><body style="font-family:sans-serif;padding:2em">
            <h2>❌ Google authorisation failed</h2>
            <p>{_esc(error or 'No authorisation code returned.')}</p>
            <p><a href="/google/oauth/start">Try again</a>.</p>
            </body></html>
        """, status_code=400)
    try:
        exchange_code(code, state, on_connected=lambda: (
            ensure_secretary_calendar(), ensure_secretary_folder()))
        return HTMLResponse(f"""
            <html><body style="font-family:sans-serif;padding:2em">
            <h2>✅ Google Workspace connected!</h2>
            <p>Your Secretary created her own calendar, <strong>"{SECRETARY_CALENDAR_NAME}"</strong>,
            and her own Drive folder, <strong>"{SECRETARY_FOLDER_NAME}"</strong>, and can now see your
            schedule and search/read your Gmail, Drive, Docs, Sheets, and Slides. You can close this
            tab and go back to the dashboard.</p>
            </body></html>
        """)
    except Exception as e:
        return HTMLResponse(f"""
            <html><body style="font-family:sans-serif;padding:2em">
            <h2>❌ Token exchange failed</h2><p>{_esc(e)}</p>
            <p><a href="/google/oauth/start">Try again</a>.</p>
            </body></html>
        """, status_code=400)


@router.get("/google/status")
def google_status():
    from agents.google_auth import is_authorised
    from agents.gcal import her_calendar_id
    return {
        "configured": bool(os.getenv("GOOGLE_CLIENT_ID")),
        "authorised": is_authorised(),
        "secretary_calendar": her_calendar_id(),
    }


# --- WhatsApp (Secretary Phase 3, Meta Cloud API) ---
#
# The webhook below is the one endpoint in this whole API meant to be
# reachable from the public internet (via a Cloudflare Tunnel restricted to
# this path only — see /whatsapp/setup). It has no session/cookie auth like
# a browser-facing endpoint would; agents.whatsapp.verify_signature() is the
# entire security boundary. Never relax or bypass that check.

@router.get("/whatsapp/setup")
def whatsapp_setup():
    """Guided setup page, same style as /google/oauth/start's inline
    instructions — Sheraj is non-technical and this involves several
    external steps (Meta Developer account, test number, Cloudflare
    Tunnel) with no simple one-click OAuth flow to walk him through."""
    from agents import whatsapp
    configured = whatsapp.is_configured()
    status_line = ("✅ All WhatsApp settings are filled in below." if configured else
                   "⚠️ Some settings below are still empty.")
    return HTMLResponse(f"""
        <html><body style="font-family:sans-serif;max-width:700px;margin:2em auto;line-height:1.5">
        <h2>Connect the Secretary to WhatsApp</h2>
        <p>{status_line}</p>
        <h3>1. Create a Meta Developer app</h3>
        <ol>
          <li>Go to <a href="https://developers.facebook.com/apps" target="_blank">developers.facebook.com/apps</a>
              and create an app of type <strong>"Business"</strong>.</li>
          <li>Add the <strong>WhatsApp</strong> product to the app.</li>
          <li>Meta gives you a <strong>free test phone number</strong> automatically — start with that
              before requesting a real one.</li>
        </ol>
        <h3>2. Collect three values from the WhatsApp → API Setup page</h3>
        <ul>
          <li><code>WHATSAPP_TOKEN</code> — the temporary access token shown there (or a permanent
              one from System Users, once you're ready to go beyond testing)</li>
          <li><code>WHATSAPP_PHONE_NUMBER_ID</code> — shown right above the token</li>
          <li><code>WHATSAPP_APP_SECRET</code> — App Settings → Basic → App Secret (click "Show")</li>
        </ul>
        <h3>3. Pick your own values for two more</h3>
        <ul>
          <li><code>WHATSAPP_VERIFY_TOKEN</code> — any password-like string you make up (used only to
              confirm to Meta that the webhook is really yours)</li>
          <li><code>WHATSAPP_OWNER_NUMBER</code> — YOUR WhatsApp number in international format,
              e.g. <code>+15551234567</code> (this is the only number that gets full Secretary access)</li>
        </ul>
        <p>Put all five into your <code>.env</code> file (already has empty placeholders) and restart the API.</p>
        <h3>4. Expose this server to the internet — ONE path only</h3>
        <p>Meta needs to reach <code>/whatsapp/webhook</code> on this machine. Don't tunnel your whole API —
           install <a href="https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"
           target="_blank">cloudflared</a> and use a config that only proxies the webhook path, e.g.:</p>
        <pre style="background:#f4f4f4;padding:1em;border-radius:6px">tunnel: bahai-secretary
credentials-file: &lt;path cloudflared gives you after 'cloudflared tunnel login'&gt;

ingress:
  - hostname: your-chosen-subdomain.your-domain.com
    path: /whatsapp/webhook
    service: http://localhost:8765
  - service: http_status:404</pre>
        <p>Then run <code>cloudflared tunnel run bahai-secretary</code> and leave it running alongside the API.</p>
        <h3>5. Point Meta at the webhook</h3>
        <ol>
          <li>In WhatsApp → Configuration, set the Callback URL to
              <code>https://your-chosen-subdomain.your-domain.com/whatsapp/webhook</code> and the
              Verify Token to whatever you picked for <code>WHATSAPP_VERIFY_TOKEN</code>.</li>
          <li>Click <strong>Verify and Save</strong> — Meta will call the webhook once to confirm it.</li>
          <li>Subscribe to the <strong>messages</strong> field.</li>
          <li>You'll also need to <strong>publish the app</strong> (requires a privacy policy URL —
              use <code>/whatsapp/privacy</code> on this same tunnel) before Meta will deliver real
              messages, not just dashboard test events.</li>
          <li><strong>Easy to miss:</strong> none of the above actually tells your WhatsApp Business
              Account (WABA) to send its events to THIS app — that's a separate link. Check with
              <code>GET https://graph.facebook.com/v21.0/&lt;WABA_ID&gt;/subscribed_apps</code> (bearer
              token = <code>WHATSAPP_TOKEN</code>). If your app isn't in the list (e.g. after
              reconnecting the app in Meta's UI, which can silently repoint it at Meta's own
              "WA DevX Webhook Events 1P App"), fix it with
              <code>POST</code> to that same URL. Meta's "Check test webhooks" log will show real
              messages arriving even when this is broken — it doesn't confirm delivery to your
              callback, only that Meta generated the event.</li>
        </ol>
        <h3>6. Message the test number from your phone</h3>
        <p>Save the test number as a contact and send it a message — the Secretary should reply.</p>
        <h3>7. (Later) the 24-hour-window fallback template</h3>
        <p>WhatsApp only allows free-form replies within 24 hours of your last message. For a reminder
           sent after a quiet day, submit a simple template for Meta's review (Message Templates →
           Create): name it <code>{whatsapp.WHATSAPP_UPDATE_TEMPLATE}</code>, category "Utility", body
           text <code>Update from Sheraj's assistant: {{{{1}}}}</code>. Approval can take up to a day —
           reminders work over the dashboard regardless while you wait.</p>
        <p><a href="/secretary/status">Check current connection status</a></p>
        </body></html>
    """)


@router.get("/whatsapp/privacy")
def whatsapp_privacy():
    """Privacy policy for Meta's app-publish requirement. Meta requires a
    publicly reachable URL before an app can leave development mode — this
    is that page, describing the one real thing this app does: a private,
    single-user assistant for Sheraj, never a public product."""
    return HTMLResponse("""
        <html><body style="font-family:sans-serif;max-width:700px;margin:2em auto;line-height:1.6">
        <h2>Privacy Policy — bahAI Secretary</h2>
        <p><em>Last updated 2026-07-07</em></p>
        <p>This application is a private, single-user personal assistant built for and used by
           one person (its owner). It is not a public product, is not distributed to other users,
           and does not knowingly collect data from anyone other than its owner.</p>
        <h3>What data is handled</h3>
        <ul>
          <li>Messages sent to and from the owner's WhatsApp number, calendar events, tasks, and
              reminders the owner creates through the assistant.</li>
          <li>This data is used solely to operate the assistant for its owner — scheduling,
              reminders, and answering questions the owner asks it.</li>
        </ul>
        <h3>Where it's stored</h3>
        <p>All personal data is stored in a private local database on the owner's own machine.
           It is never sold, shared for advertising, or made available to any third party except
           the service providers strictly necessary to operate the assistant:</p>
        <ul>
          <li><strong>Meta WhatsApp Business Cloud API</strong> — transports messages to and from
              WhatsApp.</li>
          <li><strong>Anthropic (Claude)</strong> — processes message text to generate the
              assistant's replies.</li>
          <li><strong>Google Workspace APIs</strong> (Calendar/Gmail/Drive/Docs/Sheets), only when
              the owner has connected them — used solely to read/write the owner's own data at the
              owner's request.</li>
        </ul>
        <h3>Data retention and deletion</h3>
        <p>Data is retained until the owner deletes it. As the sole user, the owner can delete any
           stored data directly at any time.</p>
        <h3>Contact</h3>
        <p>Questions about this policy: <a href="mailto:sherajr22@gmail.com">sherajr22@gmail.com</a></p>
        </body></html>
    """)


@router.get("/whatsapp/status")
def whatsapp_status():
    from agents import whatsapp
    return {
        "configured": whatsapp.is_configured(),
        "owner_number_set": bool(whatsapp.WHATSAPP_OWNER_NUMBER),
    }


@router.get("/whatsapp/webhook")
def whatsapp_webhook_verify(request: Request):
    """Meta's one-time handshake when you click 'Verify and Save' in the
    WhatsApp Configuration page."""
    from agents import whatsapp
    q = request.query_params
    challenge = whatsapp.verify_webhook_challenge(
        q.get("hub.mode", ""), q.get("hub.verify_token", ""), q.get("hub.challenge", ""))
    if challenge is None:
        raise HTTPException(status_code=403, detail="Verification failed")
    return PlainTextResponse(challenge)


def _handle_whatsapp_message(msg: dict):
    """Runs in a background task so the webhook can ack Meta immediately —
    Meta may retry the whole webhook delivery if it doesn't get a fast 200,
    which would otherwise risk a duplicate reply to the same message."""
    from agents import whatsapp, secretary, secretary_store
    # Dedupe on Meta's message id (ids only in private DB — rule 15).
    # Retries of the same delivery must not re-run a full Secretary turn.
    message_id = (msg.get("message_id") or "").strip()
    if message_id and secretary_store.seen_wa_message(message_id):
        return
    phone = msg["from"]
    secretary_store.record_inbound_contact(phone)
    # Three tiers (rule 27 + owner decision 2026-07-12):
    #   owner        → full secretary.chat (tools + memory)
    #   allowlisted  → tool-less guest_chat (own thread, no personal context)
    #   everyone else → canned reply; never reach any chat loop
    # Strangers must never reach secretary.chat — that would hand whoever
    # texts this number full access to Sheraj's calendar/Gmail/Drive via
    # her tool-calling loop. Allowlisted contacts get guest_chat only
    # (structurally tool-less; owner decision 2026-07-12).
    if whatsapp.is_owner(phone):
        try:
            result = secretary.chat(msg["text"], channel="whatsapp")
            whatsapp.send_text(phone, result["reply"])
        except Exception as e:
            secretary_store.add_notification("scheduler_error", f"WhatsApp reply failed: {whatsapp.why(e)}")
    else:
        contact = secretary_store.get_contact_by_phone(phone)
        if contact and contact.get("allowlisted"):
            try:
                result = secretary.guest_chat(contact, msg["text"])
                whatsapp.send_text(phone, result["reply"])
            except Exception as e:
                secretary_store.add_notification("scheduler_error",
                    f"WhatsApp guest reply to {contact['name']} failed: {whatsapp.why(e)}")
        else:
            try:
                whatsapp.send_text(phone, "This is Abigail, Sheraj's personal assistant — "
                                          "I can only take instructions from him directly.")
            except Exception:
                pass
            secretary_store.add_notification(
                "whatsapp", f"Message from a non-owner number ({phone[-4:]}) — auto-replied, not processed")


@router.post("/whatsapp/webhook")
async def whatsapp_webhook_receive(request: Request, background_tasks: BackgroundTasks):
    from agents import whatsapp
    raw = await request.body()
    if not whatsapp.verify_signature(raw, request.headers.get("X-Hub-Signature-256")):
        raise HTTPException(status_code=403, detail="Invalid signature")
    payload = json.loads(raw)
    for msg in whatsapp.parse_webhook_messages(payload):
        background_tasks.add_task(_handle_whatsapp_message, msg)
    return {"status": "ok"}


# --- Secretary (Phase 1: chat + private memory) ---
#
# Privacy hard rule: everything below returns personal content ONLY to the
# dashboard's Secretary tab. Never log message content to log_run, job
# progress, or stdout.

class SecretaryChatRequest(BaseModel):
    message: str


@router.post("/secretary/chat")
def secretary_chat(req: SecretaryChatRequest):
    from agents import secretary
    text = (req.message or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Empty message")
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise HTTPException(status_code=503,
                            detail="ANTHROPIC_API_KEY is not set — add it to .env to enable Abigail")
    try:
        return secretary.chat(text, channel="dashboard")
    except Exception as e:
        # Surface the failure class, not the conversation content
        raise HTTPException(status_code=502, detail=f"Abigail is unavailable: {type(e).__name__}")


@router.get("/secretary/history")
def secretary_history(limit: int = 50):
    from agents import secretary_store
    secretary_store.init_db()
    return {"messages": secretary_store.get_recent_messages(min(limit, 200))}


@router.get("/secretary/status")
def secretary_status():
    from agents import secretary_store, whatsapp
    from agents.google_auth import is_authorised as google_authorised
    from agents.router import ANTHROPIC_MODEL
    secretary_store.init_db()
    return {
        "enabled": bool(os.getenv("ANTHROPIC_API_KEY")),
        "model": ANTHROPIC_MODEL,
        "notes": len(secretary_store.list_memory_notes()),
        "open_tasks": len(secretary_store.get_open_tasks()),
        "google_configured": bool(os.getenv("GOOGLE_CLIENT_ID")),
        "google_authorised": google_authorised(),
        "whatsapp_configured": whatsapp.is_configured(),
        "pending_reminders": len(secretary_store.get_pending_reminders()),
        "pending_approvals": len(secretary_store.get_pending_actions()),
    }


@router.get("/secretary/upcoming")
def secretary_upcoming(days: int = 14):
    """Merged, tagged calendar view + verified Bahá'í dates + pending reminders."""
    from datetime import date, timedelta
    from agents import badi_dates, secretary_store
    from agents import gcal
    secretary_store.init_db()
    events = []
    if gcal.is_authorised():
        try:
            events = gcal.list_events(days_ahead=min(days, 60))
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Calendar unreachable: {type(e).__name__}")
    today = date.today()
    badi = [{"date": e["date"].isoformat(), "name": e["name"], "kind": e["kind"],
             "work_suspended": e["work_suspended"]}
            for e in badi_dates.events_between(today, today + timedelta(days=min(days, 60)))]
    return {
        "events": events,
        "badi_events": badi,
        "reminders": secretary_store.get_pending_reminders(),
        "badi_source": badi_dates.OFFICIAL_CALENDAR_URL,
    }


@router.get("/secretary/notifications")
def secretary_notifications(after_id: int = 0):
    """Scheduler fires/failures for the dashboard (titles only — hard rule 8)."""
    from agents import secretary_store
    secretary_store.init_db()
    return {"notifications": secretary_store.get_notifications(after_id=after_id)}


@router.get("/secretary/approvals")
def secretary_approvals():
    from agents import secretary_store
    secretary_store.init_db()
    return {"pending": secretary_store.get_pending_actions()}


class ApprovalRequest(BaseModel):
    approve: bool


@router.post("/secretary/approvals/{action_id}")
def secretary_resolve_approval(action_id: int, req: ApprovalRequest):
    """Sheraj's per-event confirmation for writes to calendars she doesn't own."""
    from agents import secretary, secretary_store
    secretary_store.init_db()
    if not req.approve:
        secretary_store.resolve_pending_action(action_id, "rejected")
        return {"result": "rejected"}
    return {"result": secretary.execute_pending_action(action_id)}


# --- WhatsApp contacts (the allowlist — owner-controlled only, never
# LLM-writable; see agents/secretary_tools.py's SEND_WHATSAPP_TOOL docstring) ---

class ContactRequest(BaseModel):
    name: str
    phone: str
    allowlisted: bool = False


@router.get("/secretary/contacts")
def secretary_list_contacts():
    from agents import secretary_store
    secretary_store.init_db()
    return {"contacts": secretary_store.list_contacts()}


@router.post("/secretary/contacts")
def secretary_add_contact(req: ContactRequest):
    from agents import secretary_store
    secretary_store.init_db()
    if not req.name.strip() or not req.phone.strip():
        raise HTTPException(status_code=400, detail="Name and phone are both required")
    cid = secretary_store.add_contact(req.name.strip(), req.phone.strip(), req.allowlisted)
    return {"id": cid}


class AllowlistRequest(BaseModel):
    allowlisted: bool


@router.post("/secretary/contacts/{contact_id}/allowlist")
def secretary_set_contact_allowlisted(contact_id: int, req: AllowlistRequest):
    from agents import secretary_store
    secretary_store.set_contact_allowlisted(contact_id, req.allowlisted)
    return {"result": "ok"}


@router.delete("/secretary/contacts/{contact_id}")
def secretary_remove_contact(contact_id: int):
    from agents import secretary_store
    secretary_store.remove_contact(contact_id)
    return {"result": "ok"}


# --- Secretary: personality / custom instructions ---

class PersonalityRequest(BaseModel):
    custom_instructions: str


@router.get("/secretary/personality")
def secretary_get_personality():
    from agents import secretary_store
    secretary_store.init_db()
    return {"custom_instructions": secretary_store.get_setting("custom_instructions", "") or ""}


@router.post("/secretary/personality")
def secretary_set_personality(req: PersonalityRequest):
    from agents import secretary_store
    secretary_store.init_db()
    secretary_store.set_setting("custom_instructions", req.custom_instructions)
    return {"result": "ok"}


# --- Secretary: notes (manual view/edit of private/memory/*.md) ---

class NoteRequest(BaseModel):
    name: str
    content: str


@router.get("/secretary/notes")
def secretary_list_notes():
    from agents import secretary_store
    secretary_store.init_db()
    return {"notes": secretary_store.list_memory_notes()}


@router.post("/secretary/notes")
def secretary_save_note(req: NoteRequest):
    from agents import secretary_store
    secretary_store.init_db()
    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Note name is required")
    secretary_store.overwrite_memory_note(name, req.content)
    return {"result": "ok"}


@router.delete("/secretary/notes/{name}")
def secretary_delete_note(name: str):
    from agents import secretary_store
    secretary_store.delete_memory_note(name)
    return {"result": "ok"}


# --- Secretary: tasks (manual view/edit — she still only sees open ones) ---

class TaskRequest(BaseModel):
    description: str
    due: Optional[str] = None


class TaskEditRequest(BaseModel):
    description: Optional[str] = None
    due: Optional[str] = None
    done: Optional[bool] = None


@router.get("/secretary/tasks")
def secretary_list_tasks():
    from agents import secretary_store
    secretary_store.init_db()
    return {"tasks": secretary_store.get_all_tasks()}


@router.post("/secretary/tasks")
def secretary_add_task(req: TaskRequest):
    from agents import secretary_store
    secretary_store.init_db()
    desc = req.description.strip()
    if not desc:
        raise HTTPException(status_code=400, detail="Description is required")
    tid = secretary_store.add_task(desc, due=req.due)
    return {"id": tid}


@router.patch("/secretary/tasks/{task_id}")
def secretary_edit_task(task_id: int, req: TaskEditRequest):
    from agents import secretary_store
    edits = req.model_dump(exclude_unset=True)
    if not edits:
        raise HTTPException(status_code=400, detail="No fields provided to edit")
    secretary_store.update_task(task_id, **edits)
    return {"result": "ok"}


@router.delete("/secretary/tasks/{task_id}")
def secretary_delete_task(task_id: int):
    from agents import secretary_store
    secretary_store.delete_task(task_id)
    return {"result": "ok"}


# --- Secretary: reminders (manual view/edit) ---

class ReminderRequest(BaseModel):
    message: str
    fire_at: str
    recurrence: Optional[str] = None
    wake_me: bool = False


class ReminderEditRequest(BaseModel):
    message: Optional[str] = None
    fire_at: Optional[str] = None
    recurrence: Optional[str] = None
    wake_me: Optional[bool] = None


@router.get("/secretary/reminders")
def secretary_list_reminders():
    from agents import secretary_store
    secretary_store.init_db()
    return {"reminders": secretary_store.get_all_reminders()}


@router.post("/secretary/reminders")
def secretary_add_reminder(req: ReminderRequest):
    from agents import secretary_store
    secretary_store.init_db()
    msg = req.message.strip()
    if not msg or not req.fire_at.strip():
        raise HTTPException(status_code=400, detail="Message and fire_at are both required")
    rid = secretary_store.add_reminder(msg, req.fire_at, recurrence=req.recurrence, wake_me=req.wake_me)
    return {"id": rid}


@router.patch("/secretary/reminders/{reminder_id}")
def secretary_edit_reminder(reminder_id: int, req: ReminderEditRequest):
    from agents import secretary_store
    edits = req.model_dump(exclude_unset=True)
    if not edits:
        raise HTTPException(status_code=400, detail="No fields provided to edit")
    secretary_store.update_reminder(reminder_id, **edits)
    return {"result": "ok"}


@router.delete("/secretary/reminders/{reminder_id}")
def secretary_delete_reminder(reminder_id: int):
    from agents import secretary_store
    secretary_store.delete_reminder(reminder_id)
    return {"result": "ok"}
