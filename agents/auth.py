"""
Authentication for the dashboard API (rule 70).

Every endpoint in `api.py` is owner-only. Until this module existed there was
nothing establishing WHO was calling: the server bound loopback and that was
treated as the control. Loopback is not authentication — the browser Sheraj
reads email in can reach 127.0.0.1, and with the wildcard CORS that used to sit
on the app, any page on the internet could both CALL the API and READ the
reply. That reaches Abigail's messages, the Material World map, the approval
queue and the wallet.

The key is a 64-char hex secret in `private/api_key.txt` (git-ignored, rule 15),
generated on first use so there is nothing for Sheraj to configure. The Vite
dev server reads the same file and injects the header on every proxied request
(`dashboard/vite.config.ts`), so the dashboard is unchanged — including images
and video, which it already loads through the proxy.
"""

import hmac
import os
import secrets
from pathlib import Path

from starlette.responses import JSONResponse

PRIVATE_DIR = Path(__file__).parent.parent / "private"
KEY_PATH = PRIVATE_DIR / "api_key.txt"

# The cookie a browser tab gets once it has authenticated some other way, so a
# page the API itself serves can link on to another page it serves. SameSite
# =Lax is load-bearing, not a default: it means the cookie rides a top-level
# navigation the owner performs but is NOT attached to a cross-site fetch or
# form POST, which is what stops it re-opening the CSRF hole the header closes.
COOKIE_NAME = "bahai_api_key"

# Reachable without the key. Deliberately tiny, and every entry has a reason:
#   /health              — a liveness probe must not need a secret.
#   /whatsapp/webhook    — Meta calls it from the public internet (rule 26);
#                          its authentication is the HMAC signature, which
#                          fails CLOSED and is strictly stronger than this key.
#   /whatsapp/privacy    — a public policy page Meta requires to be readable.
PUBLIC_PATHS = {
    "/health",
    "/whatsapp/webhook",
    "/whatsapp/privacy",
}

# The OAuth callbacks are exempt because the provider redirects the browser
# here and cannot be told to carry a header or a query secret. They are not
# unguarded: each one verifies the PKCE `state` it generated itself
# (canva/etsy/google `exchange_code` all raise on a mismatch), so a forged call
# reaches a token exchange that refuses. Their error pages escape everything
# they echo (rule 71) precisely because they sit outside this gate.
PUBLIC_OAUTH_CALLBACKS = {
    "/canva/oauth/callback",
    "/etsy/oauth/callback",
    "/google/oauth/callback",
}


def get_or_create_key() -> str:
    """
    The shared secret, made on first use. `DASHBOARD_API_KEY` in .env wins if
    set, for anyone who would rather manage it themselves.

    The exclusive create plus re-read is not paranoia about threads: the API
    and the Vite dev server both call an equivalent function and can genuinely
    race on a cold start. Whoever loses reads the winner's key instead of
    overwriting it, which would otherwise lock the dashboard out silently.
    """
    env_key = os.getenv("DASHBOARD_API_KEY", "").strip()
    if env_key:
        return env_key
    if KEY_PATH.exists():
        existing = KEY_PATH.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    PRIVATE_DIR.mkdir(exist_ok=True)
    try:
        with open(KEY_PATH, "x", encoding="utf-8") as fh:
            fh.write(secrets.token_hex(32))
    except FileExistsError:
        pass
    return KEY_PATH.read_text(encoding="utf-8").strip()


def is_public(path: str) -> bool:
    return path in PUBLIC_PATHS or path in PUBLIC_OAUTH_CALLBACKS


def _presented(request) -> str:
    """
    The key the caller offered, by any of the three routes it can arrive on.
    Header is what the dashboard uses. The query parameter is for a link opened
    by hand. The cookie is only ever one this middleware set itself.
    """
    header = request.headers.get("x-api-key", "")
    if header:
        return header.strip()
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    q = request.query_params.get("k", "")
    if q:
        return q.strip()
    return request.cookies.get(COOKIE_NAME, "").strip()


async def api_key_middleware(request, call_next):
    path = request.url.path
    if is_public(path):
        return await call_next(request)

    presented = _presented(request)
    # compare_digest, not ==, so a wrong key cannot be narrowed down by timing.
    if not presented or not hmac.compare_digest(presented, get_or_create_key()):
        return JSONResponse(
            status_code=401,
            content={
                "detail": (
                    "Not authorised. This API is owner-only; the dashboard "
                    "supplies its key automatically. See AGENTS.md rule 70."
                )
            },
        )

    response = await call_next(request)
    # Hand a browser tab that authenticated by header or query a Lax cookie, so
    # a page the API served can link on to another one without the owner having
    # to paste the key again.
    if not request.cookies.get(COOKIE_NAME):
        response.set_cookie(
            COOKIE_NAME, presented, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 30
        )
    return response
