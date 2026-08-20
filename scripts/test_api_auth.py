"""
Offline regression suite for the dashboard API's owner gate (rules 70-71).

    python scripts/test_api_auth.py

Free and fast -- no LLM calls, no network, no keys, its own temp databases.

The point of this suite is COVERAGE, not a sample: it walks the real route
table off `app.routes` and requires every route that is not on the tiny public
list to refuse an unauthenticated call. A hand-written list of endpoints to
check would pass for ever while a new unprotected endpoint was added next to
it -- the same reason `colony_tools.GATED_KINDS` is asserted as data.

Console output is ASCII only (Windows cp1252 -- see AGENTS.md gotchas).
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

_TMP = Path(tempfile.mkdtemp(prefix="apiauth_test_"))
os.environ["ANTHROPIC_API_KEY"] = os.environ.get("ANTHROPIC_API_KEY", "test-key")
# The gate must be exercised through its FILE path, not an env override, since
# that is what Sheraj's machine actually uses.
os.environ.pop("DASHBOARD_API_KEY", None)
os.environ.pop("DASHBOARD_ORIGINS", None)

import agents.state as state  # noqa: E402

state.DB_PATH = _TMP / "workforce.db"

import agents.colony as colony  # noqa: E402

colony.DB_PATH = state.DB_PATH
state.init_db()

import agents.auth as auth  # noqa: E402

# Never read or create the real private/api_key.txt from a test.
auth.PRIVATE_DIR = _TMP
auth.KEY_PATH = _TMP / "api_key.txt"

import agents.api as api  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(api.app)
KEY = auth.get_or_create_key()

PASS = FAIL = 0
FAILURES: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        FAILURES.append(label + ((" -- " + detail) if detail else ""))


def section(name: str) -> None:
    print("\n-- " + name + " --")


# --- 1. The key itself -------------------------------------------------------

section("the key")
check("key is 64 hex chars",
      len(KEY) == 64 and all(c in "0123456789abcdef" for c in KEY), KEY[:12])
check("key persisted to disk", auth.KEY_PATH.exists())
check("second call returns the same key", auth.get_or_create_key() == KEY)
check("real private/api_key.txt untouched by this suite", auth.KEY_PATH.parent == _TMP)

# A pre-existing key is never clobbered -- that would lock the dashboard out.
_other = _TMP / "other_key.txt"
_other.write_text("deadbeef" * 8, encoding="utf-8")
_saved = auth.KEY_PATH
auth.KEY_PATH = _other
check("existing key file is reused, not overwritten",
      auth.get_or_create_key() == "deadbeef" * 8)
auth.KEY_PATH = _saved

# --- 2. Route table coverage -------------------------------------------------

section("route table coverage")


def _routes():
    out = []
    for r in api.app.routes:
        path = getattr(r, "path", None)
        methods = getattr(r, "methods", None)
        if not path or not methods:
            continue
        for m in sorted(set(methods) - {"HEAD", "OPTIONS"}):
            out.append((m, path))
    return out


ROUTES = _routes()
check("route table is non-trivial", len(ROUTES) > 150, "found " + str(len(ROUTES)))

PUBLIC = auth.PUBLIC_PATHS | auth.PUBLIC_OAUTH_CALLBACKS
unguarded = []
for method, path in ROUTES:
    if path in PUBLIC:
        continue
    # The middleware runs ahead of routing, so an unfilled {param} is still
    # gated -- a 404 would mean the request got past the gate.
    resp = client.request(method, path)
    if resp.status_code != 401:
        unguarded.append(method + " " + path + " -> " + str(resp.status_code))

check("every non-public route refuses an unauthenticated call",
      not unguarded, "; ".join(unguarded[:8]))

# Spot-check the routes whose exposure was the reason for all this.
section("the endpoints that mattered most")
for method, path in [
    ("GET", "/secretary/messages"),
    ("GET", "/secretary/pending"),
    ("GET", "/secretary/contacts"),
    ("GET", "/nuclei"),
    ("POST", "/wallet/create"),
    ("POST", "/wallet/allowlist"),
    ("POST", "/wallet/send"),
    ("GET", "/colony"),
    ("GET", "/outputs/anything.png"),
]:
    r = client.request(method, path)
    check(method + " " + path + " is 401 unauthenticated",
          r.status_code == 401, str(r.status_code))

# --- 3. The public list ------------------------------------------------------

section("the public list")
check("/health is reachable", client.get("/health").status_code == 200)
check("/whatsapp/privacy is reachable", client.get("/whatsapp/privacy").status_code == 200)
# The webhook's own gate is the HMAC signature (rule 26), which is stronger
# than the key and must keep failing closed on an unsigned POST.
check("webhook verify handshake is not 401",
      client.get("/whatsapp/webhook").status_code != 401)
check("unsigned webhook POST is still rejected",
      client.post("/whatsapp/webhook", json={"object": "x"}).status_code in (401, 403))
check("public list stays tiny", len(auth.PUBLIC_PATHS) == 3, str(auth.PUBLIC_PATHS))
check("only the OAuth callbacks are exempt beyond it",
      auth.PUBLIC_OAUTH_CALLBACKS == {
          "/canva/oauth/callback", "/etsy/oauth/callback", "/google/oauth/callback"})
# The OAuth START pages are NOT exempt -- the dashboard reaches them through
# the proxy, so they have no reason to be open.
for path in ["/canva/oauth/start", "/etsy/oauth/start", "/google/oauth/start"]:
    check(path + " is gated", client.get(path).status_code == 401)
check("/whatsapp/setup is gated", client.get("/whatsapp/setup").status_code == 401)

# --- 4. Accepted credentials -------------------------------------------------

section("accepted credentials")
check("X-API-Key header works",
      client.get("/health", headers={"X-API-Key": KEY}).status_code == 200)
r = client.get("/nuclei", headers={"X-API-Key": KEY})
check("a real endpoint opens with the header", r.status_code != 401, str(r.status_code))
check("Authorization: Bearer works",
      client.get("/nuclei", headers={"Authorization": "Bearer " + KEY}).status_code != 401)
check("?k= query parameter works", client.get("/nuclei?k=" + KEY).status_code != 401)

section("rejected credentials")
# A cookie-free client: the shared one authenticated above and would otherwise
# pass these on its cookie, hiding whether the header check works at all.
naked = TestClient(api.app)
for label, headers in [
    ("empty key", {"X-API-Key": ""}),
    ("wrong key", {"X-API-Key": "0" * 64}),
    ("truncated key", {"X-API-Key": KEY[:-1]}),
    ("key with trailing junk", {"X-API-Key": KEY + "x"}),
    ("bearer with wrong key", {"Authorization": "Bearer " + "f" * 64}),
]:
    check(label + " is refused", naked.get("/nuclei", headers=headers).status_code == 401)
check("wrong query key is refused", naked.get("/nuclei?k=nope").status_code == 401)

# A 401 must not hand the caller the thing it is guarding.
body = naked.get("/nuclei").text
check("the 401 body does not leak the key", KEY not in body)
check("the 401 body explains itself", "owner-only" in body.lower())

# --- 5. The cookie -----------------------------------------------------------

section("the browser cookie")
fresh = TestClient(api.app)
# A GATED path on purpose: the middleware short-circuits on a public one, so
# /health would never reach the cookie code and would prove nothing.
r = fresh.get("/nuclei", headers={"X-API-Key": KEY})
set_header = r.headers.get("set-cookie", "").lower()
check("a successful call sets the cookie", auth.COOKIE_NAME in set_header, set_header)
check("cookie is httponly", "httponly" in set_header, set_header)
# Lax is what keeps the cookie off a cross-site fetch/POST, so re-opening the
# CSRF hole would take deleting that attribute, not just guessing a path.
check("cookie is SameSite=Lax", "samesite=lax" in set_header, set_header)
check("the cookie then authenticates the same tab", fresh.get("/nuclei").status_code != 401)
check("a failed call sets no cookie", "set-cookie" not in naked.get("/nuclei").headers)

# --- 6. CORS -----------------------------------------------------------------

section("CORS")
r = client.get("/health", headers={"Origin": "https://evil.example"})
check("no wildcard Access-Control-Allow-Origin",
      r.headers.get("access-control-allow-origin") != "*",
      r.headers.get("access-control-allow-origin", "(absent)"))
check("an unknown origin gets no CORS grant at all",
      "access-control-allow-origin" not in r.headers,
      r.headers.get("access-control-allow-origin", ""))

# --- 7. Source-level invariants ---------------------------------------------

section("source-level invariants")
_root = Path(__file__).parent.parent
_api_src = (_root / "agents" / "api.py").read_text(encoding="utf-8")
check("api.py no longer binds 0.0.0.0",
      '"0.0.0.0"' not in _api_src and "'0.0.0.0'" not in _api_src)
check("api.py __main__ binds loopback", '127.0.0.1", port=8765' in _api_src)
check("api.py __main__ does not reload", "reload=True" not in _api_src)
check("no wildcard allow_origins anywhere", 'allow_origins=["*"]' not in _api_src)

_vite = (_root / "dashboard" / "vite.config.ts").read_text(encoding="utf-8")
check("vite proxy injects the key header", '"X-API-Key"' in _vite)
check("vite reads the same key file", "api_key.txt" in _vite)

# The key must never reach the browser bundle.
_src_files = list((_root / "dashboard" / "src").rglob("*.ts")) + \
             list((_root / "dashboard" / "src").rglob("*.tsx"))
_texts = {f: f.read_text(encoding="utf-8") for f in _src_files}
_leaks = [f.name for f, t in _texts.items()
          if "api_key.txt" in t or "DASHBOARD_API_KEY" in t]
check("no dashboard source reads the key", not _leaks, ", ".join(_leaks))

# Nothing may point straight at the API port and skip the proxy that adds the
# key. Comment lines are stripped first: describing the proxy is fine, and a
# check that forbids saying "localhost:8765" in prose would only teach the next
# person to delete the explanation.
def _bypasses_proxy(text: str) -> bool:
    for ln in text.splitlines():
        if ln.strip().startswith(("//", "*", "/*")):
            continue
        if "localhost:8765" in ln:
            return True
    return False


_bypass = [f.name for f, t in _texts.items() if _bypasses_proxy(t)]
check("no dashboard source bypasses the proxy", not _bypass, ", ".join(_bypass))

_gitignore = (_root / ".gitignore").read_text(encoding="utf-8")
check("private/ is git-ignored (covers the key)", "private/" in _gitignore)
check("canva_pkce_state.json is git-ignored", "canva_pkce_state.json" in _gitignore)
check("stray .env variants are git-ignored", ".env.*" in _gitignore)

# --- 8. Escaped OAuth callbacks (rule 71) -----------------------------------

section("OAuth callback escaping")
XSS = "<script>alert(1)</script>"
for path in sorted(auth.PUBLIC_OAUTH_CALLBACKS):
    r = client.get(path, params={"error": XSS, "error_description": XSS})
    check(path + " is reachable without the key", r.status_code != 401, str(r.status_code))
    check(path + " does not reflect raw script", "<script>" not in r.text, r.text[:120])
    check(path + " escapes and still shows the error",
          "&lt;script&gt;" in r.text, r.text[:120])

# A callback with no PKCE state on disk must fail, not connect anything.
r = client.get("/google/oauth/callback", params={"code": "forged", "state": "forged"})
check("a forged callback does not succeed", "connected" not in r.text.lower(), r.text[:120])

print("\n" + "=" * 60)
print("PASSED: " + str(PASS) + "   FAILED: " + str(FAIL))
if FAILURES:
    print("\nFailures:")
    for f in FAILURES:
        print("  - " + f)
print("=" * 60)
sys.exit(1 if FAIL else 0)
