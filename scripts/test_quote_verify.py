"""
Exact quotation verification (rule 111).

    python scripts/test_quote_verify.py

Offline and free: no LLM calls, no network, no keys, no private database. The
network tripwire below is the same one `test_live_consultation.py` uses and for
the same reason (rule 99) -- installed before any import, and deriving from
BaseException so no `except Exception` in this repo can swallow it.

EVERY passage in this file is INVENTED. Nothing here is scripture, nothing here
is presented as scripture, and the trusted corpus is never opened, never read
and never edited. The sentence the review specified --

    Source:    The group must not publish confidential meeting notes.
    Candidate: The group must publish confidential meeting notes.

-- is a synthetic test string chosen precisely because its meaning inverts on
one three-letter word, which is exactly what the retired word-overlap check
could not see.

Console output is ASCII only (Windows cp1252 -- see AGENTS.md gotchas).
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# --- The network tripwire (rule 99) ------------------------------------------
import socket as _socket

_OUTBOUND: list = []
_real_connect = _socket.socket.connect
_real_getaddrinfo = _socket.getaddrinfo


class _NetworkAttempted(BaseException):
    """Deliberately NOT an Exception, exactly as in the consultation suite."""


def _is_loopback(address) -> bool:
    host = address[0] if isinstance(address, tuple) else address
    if not isinstance(host, str):
        return False
    return host.startswith("127.") or host in ("::1", "localhost", "0.0.0.0", "")


def _tripwire_connect(self, address, *args, **kwargs):
    if _is_loopback(address):
        return _real_connect(self, address, *args, **kwargs)
    _OUTBOUND.append(str(address))
    raise _NetworkAttempted(f"NETWORK TRIPWIRE: this suite is offline; {address} refused.")


def _tripwire_getaddrinfo(host, *args, **kwargs):
    if _is_loopback(host):
        return _real_getaddrinfo(host, *args, **kwargs)
    _OUTBOUND.append(f"DNS {host}")
    raise _NetworkAttempted(f"NETWORK TRIPWIRE: this suite is offline; DNS {host} refused.")


_socket.socket.connect = _tripwire_connect
_socket.getaddrinfo = _tripwire_getaddrinfo

from agents import quote_verify  # noqa: E402

PASS = FAIL = 0
FAILURES: list = []


def check(label: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        FAILURES.append(label + ((" -- " + detail) if detail else ""))


def section(name: str) -> None:
    print(f"\n-- {name} --")


# INVENTED. Not scripture. See the module docstring.
SYNTHETIC = [{
    "text": ("The group must not publish confidential meeting notes. "
             "It is a matter of trust between the friends. "
             "Consultation requires frankness and courtesy in equal measure."),
    "source": "Invented Test Source",
    "section": "Chapter 1",
    "link": "https://example.invalid/test",
}]

OTHER = [{
    "text": "A different invented passage about something else entirely.",
    "source": "Another Invented Source",
}]


section("the sentence that broke the old check")

_v = quote_verify.verify_quotation(
    "The group must not publish confidential meeting notes.", SYNTHETIC)
check("the exact sentence verifies", _v.verified, _v.reason)

# THE case. The retired check reported "100% of content words traceable" for
# this, because `not` was in its stop-word list.
_v = quote_verify.verify_quotation(
    "The group must publish confidential meeting notes.", SYNTHETIC)
check("the SAME sentence with the negation removed does NOT verify", not _v.verified)
check("and the reason says the words are not there in that order",
      "order" in _v.reason or "do not appear" in _v.reason, _v.reason)


section("altered text is never verified")

for _label, _candidate in [
    ("reordered words", "Confidential meeting notes the group must not publish."),
    ("a substituted word", "The group must not share confidential meeting notes."),
    ("an inserted word", "The group must not ever publish confidential meeting notes."),
    ("a dropped word", "The group must not publish meeting notes."),
    ("two passages stitched together",
     "The group must not publish confidential meeting notes. "
     "A different invented passage about something else entirely."),
    ("a sentence from a source that was not selected",
     "A different invented passage about something else entirely."),
]:
    _v = quote_verify.verify_quotation(_candidate, SYNTHETIC)
    check(f"{_label} is refused", not _v.verified, _v.reason)


section("honest excerpt boundaries")

_v = quote_verify.verify_quotation("publish confidential meeting notes.", SYNTHETIC)
check("starting AFTER the negation is refused even though every word is genuine",
      not _v.verified, _v.reason)
check("and it says why, in words a person can act on",
      "middle of a sentence" in _v.reason, _v.reason)

_v = quote_verify.verify_quotation("The group must not publish confidential", SYNTHETIC)
check("stopping mid-sentence with no mark is refused", not _v.verified, _v.reason)

_v = quote_verify.verify_quotation("The group must not publish confidential ...", SYNTHETIC)
check("stopping early WITH the omission marked is allowed", _v.verified, _v.reason)
check("and the excerpt is flagged as one", _v.elided)

_v = quote_verify.verify_quotation(
    "It is a matter of trust between the friends.", SYNTHETIC)
check("a complete middle sentence verifies", _v.verified, _v.reason)

_partial = [{"text": "immensity of the heavens, until the friends had gathered. "
                     "Then a complete sentence follows.",
             "source": "Invented Fragment Source"}]
_v = quote_verify.verify_quotation(
    "immensity of the heavens, until the friends had gathered.", _partial)
check("a passage that itself opens mid-sentence cannot print as a whole quotation",
      not _v.verified, _v.reason)


section("attribution is part of verification")

_v = quote_verify.verify_quotation(
    "The group must not publish confidential meeting notes.", SYNTHETIC,
    expect_source="Invented Test Source")
check("the right words under the right source verify", _v.verified, _v.reason)

_v = quote_verify.verify_quotation(
    "The group must not publish confidential meeting notes.", SYNTHETIC,
    expect_source="A Book It Did Not Come From")
check("the right words under an INVENTED source do not verify", not _v.verified)
check("and the failure names where the words actually came from",
      "Invented Test Source" in _v.reason, _v.reason)


section("no sources means unverifiable, never verified")

_v = quote_verify.verify_quotation("Anything at all.", [])
check("an empty corpus cannot verify anything", not _v.verified)
check("and says so rather than blaming the quotation",
      "no source passages" in _v.reason, _v.reason)
_v = quote_verify.verify_quotation("", SYNTHETIC)
check("an empty quotation is refused", not _v.verified)


section("what is printed is the SOURCE's characters")

_typographic = [{
    "text": "The Bahá’í friends gathered at dawn. Truly they did.",
    "source": "Invented Diacritic Source",
}]
_v = quote_verify.verify_quotation("The Baha'i friends gathered at dawn.", _typographic)
check("a candidate typed without diacritics still matches", _v.verified, _v.reason)
check("but what comes back is the corpus's own characters",
      _v.canonical_text == "The Bahá’í friends gathered at dawn.",
      repr(_v.canonical_text))
check("it is not the candidate's reconstruction",
      _v.canonical_text != "The Baha'i friends gathered at dawn.")

_v = quote_verify.verify_quotation(
    "“The group must not publish confidential meeting notes.”", SYNTHETIC)
check("surrounding smart quotes do not defeat a match", _v.verified, _v.reason)


section("a verification carries its own evidence")

_v = quote_verify.verify_quotation(
    "The group must not publish confidential meeting notes.", SYNTHETIC)
check("it names the source", _v.source == "Invented Test Source")
check("it carries the section", _v.section == "Chapter 1")
check("it carries a reproducible locator", bool(_v.locator), _v.locator)
check("it carries an integrity hash of the exact printed text",
      len(_v.text_sha256) == 64)
check("the hash is of the CANONICAL text, not the candidate",
      _v.text_sha256 == quote_verify._sha(_v.canonical_text))
check("it records which verification method passed it",
      _v.method == quote_verify.VERIFIER_VERSION)
check("and the retired method has a different name, so the two can be told apart",
      quote_verify.VERIFIER_VERSION != quote_verify.LEGACY_OVERLAP_VERSION)


section("normalisation may never manufacture a match")

_folded = quote_verify.fold("The  group   must not publish.")
check("whitespace and non-breaking spaces are normalised",
      _folded == "The group must not publish.", repr(_folded))
check("but no word is dropped by folding", "not" in _folded)
_body, _elided = quote_verify.strip_elision("... the friends had gathered ...")
check("elision marks are stripped from both ends", _body == "the friends had gathered",
      repr(_body))
check("and the fact that it WAS an excerpt is kept", _elided)


section("the bookmark pipeline uses this service, not a similarity score")

os.environ.setdefault("DASHBOARD_API_KEY", "test-" + ("c" * 59))
from agents import api as _api  # noqa: E402

_citations = [{"text": SYNTHETIC[0]["text"], "source": SYNTHETIC[0]["source"]}]

_ok, _why = _api._check_quote_grounding(
    "The group must not publish confidential meeting notes.", _citations)
check("an exact quotation is grounded through the pipeline's own entry point", _ok, _why)

# The end-to-end version of the case above, through the function the bookmark
# pipeline actually calls.
_bad, _why = _api._check_quote_grounding(
    "The group must publish confidential meeting notes.", _citations)
check("and the negation-removed version is NOT grounded there either", not _bad, _why)
check("the old '100% of content words traceable' wording is gone for good",
      "content words traceable" not in _why, _why)

_v = _api.verify_bookmark_quote(
    "The group must not publish confidential meeting notes.", _citations)
check("the pipeline's verdict carries the method", _v.method == quote_verify.VERIFIER_VERSION)

# No citations must never become "verified" via an embedding fallback.
_none, _why = _api._check_quote_grounding("Anything at all.", [])
check("with nothing retrieved, the pipeline reports unverifiable", not _none, _why)
check("and does not fall back to an embedding score",
      "similarity" not in _why.lower(), _why)

# Bounded recovery: offer something that WOULD pass.
_offer = _api.eligible_excerpt(_citations)
check("a real, exactly-verifiable excerpt is offered for review", bool(_offer), str(_offer))
if _offer:
    _re = quote_verify.verify_quotation(_offer["text"], SYNTHETIC)
    check("and the offered excerpt really does verify", _re.verified, _re.reason)
check("nothing was offered from an empty retrieval", not _api.eligible_excerpt([]))


section("the trusted corpus was never touched")

check("this suite opened no vector store", "chromadb" not in sys.modules)
check("no outbound network call was attempted during the whole run",
      not _OUTBOUND, "; ".join(sorted(set(_OUTBOUND))))
_armed = False
try:
    _socket.socket().connect(("api.openai.com", 443))
except _NetworkAttempted:
    _armed = True
except BaseException:
    _armed = False
check("the tripwire is still armed at the end of the run", _armed)
check("the tripwire cannot be swallowed by an `except Exception`",
      not issubclass(_NetworkAttempted, Exception))

print("\n" + "=" * 66)
print(f"Quote verification: {PASS} passed, {FAIL} failed  ({PASS + FAIL} checks)")
if FAILURES:
    print("\nFailures:")
    for f in FAILURES:
        print("  - " + f)
print("=" * 66)
sys.exit(1 if FAIL else 0)
