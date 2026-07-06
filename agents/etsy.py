"""
Etsy Open API v3 client — OAuth 2.0 (PKCE), draft listing creation, image upload.

One-time setup:
  1. Create an app at https://www.etsy.com/developers/your-apps
     — set the callback URL to: http://localhost:8765/etsy/oauth/callback
  2. Add to .env:
       ETSY_CLIENT_ID=<your keystring>
       ETSY_CLIENT_SECRET=<your shared secret>   (kept for completeness; v3 OAuth uses PKCE)
       ETSY_SHOP_ID=<your numeric shop id>
  3. Visit http://localhost:8765/etsy/oauth/start in a browser to authorise.
  4. Tokens auto-refresh after that (Etsy access tokens last 1 hour).

Everything created here is a DRAFT listing. Nothing goes live without Sheraj
activating it inside Etsy — deliberate human-on-the-loop for real-money actions.
"""

import base64
import hashlib
import json
import os
import secrets
import time
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=str(Path(__file__).parent.parent / ".env"), override=True)

ETSY_CLIENT_ID     = os.getenv("ETSY_CLIENT_ID", "")
ETSY_CLIENT_SECRET = os.getenv("ETSY_CLIENT_SECRET", "")   # not used by PKCE token flow
ETSY_SHOP_ID       = os.getenv("ETSY_SHOP_ID", "")
ETSY_REDIRECT_URI  = os.getenv("ETSY_REDIRECT_URI", "http://localhost:8765/etsy/oauth/callback")
ETSY_TAXONOMY_ID   = os.getenv("ETSY_TAXONOMY_ID", "")     # auto-discovered if empty

ETSY_AUTH_URL  = "https://www.etsy.com/oauth/connect"
ETSY_TOKEN_URL = "https://api.etsy.com/v3/public/oauth/token"
ETSY_API_BASE  = "https://openapi.etsy.com/v3"

_PROJECT_ROOT   = Path(__file__).parent.parent
TOKEN_FILE      = _PROJECT_ROOT / "etsy_token.json"
PKCE_STATE_FILE = _PROJECT_ROOT / "etsy_pkce_state.json"

SCOPES = ["listings_w", "listings_r", "shops_r"]

# Deterministic pricing policy (fair exchange / Moderation): the one
# real-money number in the system is set HERE by the owner, never parsed out
# of model prose. The Scribe's price_note stays a display-only suggestion on
# the dashboard. Override per environment with ETSY_BOOKMARK_PRICE.
BOOKMARK_PRICE   = float(os.getenv("ETSY_BOOKMARK_PRICE", "5.99"))
DEFAULT_QUANTITY = 25     # made-to-order stock level

# Honesty disclosure (constitution principle 3) — code-appended to every
# published listing, never left to the Scribe: the buyer must know the artwork
# is AI-generated before purchase. Same discipline as the card pipeline's
# translation disclaimers (translator.LANGUAGES) and scribe._sanitize_claims.
AI_ART_DISCLOSURE = (
    "Artwork created with AI image-generation tools, art-directed and curated by Sheraj "
    "— a digitally designed, made-to-order print."
)


# ── PKCE ─────────────────────────────────────────────────────────────────────

def _generate_pkce() -> dict:
    code_verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(code_verifier.encode()).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return {"code_verifier": code_verifier, "code_challenge": code_challenge}


# ── Token storage ─────────────────────────────────────────────────────────────

def _load_token() -> Optional[dict]:
    if TOKEN_FILE.exists():
        return json.loads(TOKEN_FILE.read_text())
    return None


def _save_token(token_data: dict) -> None:
    TOKEN_FILE.write_text(json.dumps(token_data, indent=2))


def _refresh_token(refresh_token: str) -> dict:
    resp = requests.post(
        ETSY_TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type": "refresh_token",
            "client_id": ETSY_CLIENT_ID,
            "refresh_token": refresh_token,
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    data["expires_at"] = time.time() + data.get("expires_in", 3600)
    _save_token(data)
    return data


def get_valid_token() -> str:
    """Return a valid access token, auto-refreshing if within 5 min of expiry."""
    data = _load_token()
    if not data:
        raise RuntimeError(
            "Etsy not authorised. "
            "Visit http://localhost:8765/etsy/oauth/start to connect."
        )
    if time.time() >= data.get("expires_at", 0) - 300:
        data = _refresh_token(data["refresh_token"])
    return data["access_token"]


def is_authorised() -> bool:
    try:
        get_valid_token()
        return True
    except Exception:
        return False


# ── OAuth flow ────────────────────────────────────────────────────────────────

def build_auth_url() -> str:
    """Build the Etsy OAuth URL and save PKCE state for the callback."""
    if not ETSY_CLIENT_ID:
        raise RuntimeError("ETSY_CLIENT_ID not set in .env")
    pkce = _generate_pkce()
    state = secrets.token_urlsafe(16)
    PKCE_STATE_FILE.write_text(json.dumps({
        "code_verifier": pkce["code_verifier"],
        "state": state,
    }))
    scope_str = "%20".join(SCOPES)
    return (
        f"{ETSY_AUTH_URL}"
        f"?response_type=code"
        f"&client_id={ETSY_CLIENT_ID}"
        f"&redirect_uri={ETSY_REDIRECT_URI}"
        f"&scope={scope_str}"
        f"&state={state}"
        f"&code_challenge={pkce['code_challenge']}"
        f"&code_challenge_method=S256"
    )


def exchange_code(code: str, state: str) -> dict:
    """Exchange the authorization code for tokens. Called from /etsy/oauth/callback."""
    if not PKCE_STATE_FILE.exists():
        raise RuntimeError("No PKCE state found. Restart the OAuth flow.")
    saved = json.loads(PKCE_STATE_FILE.read_text())
    if saved["state"] != state:
        raise ValueError("OAuth state mismatch — possible CSRF. Restart the flow.")

    resp = requests.post(
        ETSY_TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type": "authorization_code",
            "client_id": ETSY_CLIENT_ID,
            "redirect_uri": ETSY_REDIRECT_URI,
            "code": code,
            "code_verifier": saved["code_verifier"],
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    data["expires_at"] = time.time() + data.get("expires_in", 3600)
    _save_token(data)
    PKCE_STATE_FILE.unlink(missing_ok=True)
    return data


def _auth_headers() -> dict:
    return {
        "x-api-key": ETSY_CLIENT_ID,
        "Authorization": f"Bearer {get_valid_token()}",
    }


# ── Taxonomy discovery ────────────────────────────────────────────────────────

_cached_taxonomy_id: Optional[int] = None

def _find_bookmark_taxonomy_id() -> int:
    """
    Resolve the Etsy taxonomy id for 'Bookmarks'.
    Priority: ETSY_TAXONOMY_ID from .env → live lookup in the seller taxonomy tree.
    The result is cached for the life of the process.
    """
    global _cached_taxonomy_id
    if ETSY_TAXONOMY_ID:
        return int(ETSY_TAXONOMY_ID)
    if _cached_taxonomy_id is not None:
        return _cached_taxonomy_id

    resp = requests.get(
        f"{ETSY_API_BASE}/application/seller-taxonomy/nodes",
        headers={"x-api-key": ETSY_CLIENT_ID},
        timeout=30,
    )
    resp.raise_for_status()
    nodes = resp.json().get("results", [])

    def _walk(items):
        for node in items:
            if node.get("name", "").strip().lower() == "bookmarks":
                return node.get("id")
            found = _walk(node.get("children", []) or [])
            if found:
                return found
        return None

    found = _walk(nodes)
    if not found:
        raise RuntimeError(
            "Could not find a 'Bookmarks' node in Etsy's seller taxonomy. "
            "Set ETSY_TAXONOMY_ID in .env manually "
            "(browse GET /v3/application/seller-taxonomy/nodes to find it)."
        )
    _cached_taxonomy_id = int(found)
    return _cached_taxonomy_id


# ── Listing helpers ───────────────────────────────────────────────────────────

def _clean_tags(tags: list) -> list:
    """Etsy allows at most 13 tags, each 20 chars or fewer."""
    cleaned = []
    for t in tags or []:
        t = str(t).strip()
        if t and len(t) <= 20 and t.lower() not in [c.lower() for c in cleaned]:
            cleaned.append(t)
        if len(cleaned) == 13:
            break
    return cleaned


def _resolve_front_image(product: dict) -> Optional[Path]:
    """
    Find the best image file for the listing. The product row's front_image
    column holds the Compositor's actual quote-overlaid front render — use it
    first. (A previous version only guessed filenames like <stem>-front.png,
    which never matched the Compositor's real bookmark-front-<uid>.png naming,
    so Etsy silently got the raw 2:3 artwork instead of the front face.)
    Falls back to the original generated artwork for very old rows.
    """
    front = product.get("front_image") or ""
    if front:
        p = Path(front)
        if not p.is_absolute():
            p = _PROJECT_ROOT / p
        if p.exists():
            return p

    image_url = product.get("image_url") or ""
    if not image_url:
        return None
    original = Path(image_url)
    outputs = _PROJECT_ROOT / "outputs"
    candidates = [
        outputs / f"{original.stem}-front.png",
        outputs / f"{original.stem}_front.png",
        original if original.is_absolute() else _PROJECT_ROOT / original,
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def publish_draft_listing(product: dict) -> dict:
    """
    Create a DRAFT Etsy listing from a saved product row and upload its image.
    Returns {listing_id, state, url, image_uploaded, image_error} or {skipped, reason}.
    """
    if not ETSY_CLIENT_ID or not ETSY_SHOP_ID:
        return {
            "skipped": True,
            "reason": "Etsy not configured. Add ETSY_CLIENT_ID, ETSY_CLIENT_SECRET, "
                      "and ETSY_SHOP_ID to .env, then visit /etsy/oauth/start.",
        }
    if not is_authorised():
        return {
            "skipped": True,
            "reason": "Etsy not authorised. Visit http://localhost:8765/etsy/oauth/start.",
        }

    listing = json.loads(product.get("listing_copy") or "{}")
    title       = (listing.get("title") or product.get("title") or "Bahá'í-inspired bookmark")[:140]
    description = listing.get("description") or title
    quote       = (listing.get("bookmark_quote") or "").strip()
    if quote and quote not in description:
        description = f'{description}\n\nBookmark quote:\n"{quote}"'
    description += "\n\nSize: 2\" × 6\" premium cardstock bookmark, printed to order."
    # Code-appended AI-artwork disclosure — never trusted to the Scribe's copy
    # (which may or may not mention it after manual edits).
    if AI_ART_DISCLOSURE not in description:
        description += f"\n\n{AI_ART_DISCLOSURE}"

    payload = {
        "quantity":     DEFAULT_QUANTITY,
        "title":        title,
        "description":  description,
        "price":        BOOKMARK_PRICE,
        "who_made":     "i_did",
        "when_made":    "made_to_order",
        "taxonomy_id":  _find_bookmark_taxonomy_id(),
        "type":         "physical",
        "should_auto_renew": False,
    }
    tags = _clean_tags(listing.get("tags", []))
    if tags:
        payload["tags"] = ",".join(tags)
    materials = _clean_tags(listing.get("materials", []))
    if materials:
        payload["materials"] = ",".join(materials)

    resp = requests.post(
        f"{ETSY_API_BASE}/application/shops/{ETSY_SHOP_ID}/listings",
        headers={**_auth_headers(), "Content-Type": "application/x-www-form-urlencoded"},
        data=payload,
        timeout=60,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"Etsy createDraftListing {resp.status_code}: {resp.text[:400]}")
    created = resp.json()
    listing_id = created.get("listing_id")

    # Upload the front image (non-fatal — the draft exists either way)
    image_uploaded, image_error = False, None
    image_file = _resolve_front_image(product)
    if image_file:
        try:
            with open(image_file, "rb") as f:
                img_resp = requests.post(
                    f"{ETSY_API_BASE}/application/shops/{ETSY_SHOP_ID}/listings/{listing_id}/images",
                    headers=_auth_headers(),
                    files={"image": (image_file.name, f, "image/png")},
                    timeout=120,
                )
            if img_resp.status_code >= 400:
                image_error = f"{img_resp.status_code}: {img_resp.text[:200]}"
            else:
                image_uploaded = True
        except Exception as e:
            image_error = str(e)
    else:
        image_error = "No local image file found for this product"

    return {
        "listing_id":     listing_id,
        "state":          created.get("state", "draft"),
        "url":            f"https://www.etsy.com/your/shops/me/listing-editor/edit/{listing_id}",
        "image_uploaded": image_uploaded,
        "image_error":    image_error,
    }


if __name__ == "__main__":
    print(f"Configured: {bool(ETSY_CLIENT_ID and ETSY_SHOP_ID)}")
    print(f"Authorised: {is_authorised()}")
