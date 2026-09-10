"""
Exact quotation verification (rule 111).

One service, used by every machine-generated path that is allowed to put a
"verified" label on a quotation. It answers a narrow question and refuses to
answer a wider one: *are these the source's own words, in the source's own
order, with an honest beginning and end, attributed to the source they actually
came from?*

WHY THIS EXISTS
---------------
The bookmark pipeline decided that question with word overlap: at least 60% of
the quote's distinct content words had to appear in one retrieved passage. That
is a similarity score, and similarity is not quotation. Reproduced on the code
before this module existed, with an invented sentence:

    Source:    The group must not publish confidential meeting notes.
    Candidate: The group must publish confidential meeting notes.

The check returned VERIFIED, with the reason "100% of content words traceable".
"not" is three letters and sits in the stop-word list, so deleting it cost the
candidate nothing and reversed the meaning completely. Word order was free too:
the same 100% comes back however the words are arranged.

For ordinary marketing copy an approximate check is a reasonable backstop. For
scripture it is not, because the specific harm here is text that reads as the
Writings while not being them — the same harm rule 84 exists to prevent on the
Live Consultation side, and the same discipline as `_sanitize_claims` (rule 4)
and the code-appended disclosures (rule 8): honesty-critical claims are decided
in code, never by a similarity number and never by a model's own say-so.

WHAT IT DOES NOT DO
-------------------
It does not find candidates. Embedding search, the Librarian's index and the
retrieval already in the pipelines are all good ways to LOCATE a passage; this
module is what happens next, against the located passage's actual characters.

It does not judge meaning. A quotation can be word-perfect and still be a
distortion, and no code here pretends otherwise — what it can guarantee is that
the words, their order, and the attribution are the source's own, and that the
excerpt does not begin or end somewhere that manufactures a different sentence.

It never edits the corpus, and it never prints the candidate's characters: a
verified result carries the SOURCE's exact characters, so a curly apostrophe or
a diacritic typed differently by a model cannot reach the printed card.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Optional, Sequence

# Bumped when the matching rules change, and STORED with a verification, so an
# old record cannot be silently read as though it had passed today's check.
# `overlap-0` is the retired word-overlap check; nothing produces it any more,
# and it exists so a historical product can say honestly which check it had.
VERIFIER_VERSION = "exact-1"
LEGACY_OVERLAP_VERSION = "overlap-0"

# Presentation-only normalisation, applied to BOTH sides before comparing.
#
# Every entry here is a difference in how the same character is typed, never a
# difference in what was said. Nothing in this table may ever drop a word, a
# negation, a clause or a mark that changes meaning -- that is the whole
# difference between normalising and manufacturing a match.
_PUNCT_FOLD = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'",   # single quotes
    "“": '"', "”": '"', "„": '"', "‟": '"',   # double quotes
    "‐": "-", "‑": "-", "‒": "-", "–": "-",   # dashes
    "—": "-", "―": "-", "−": "-",
    " ": " ", " ": " ", " ": " ", " ": " ",   # spaces
    "…": "...",                                              # ellipsis
    "ʼ": "'", "ʻ": "'",                                 # modifier apostrophes
}

# The marks a legitimate elision may use. An excerpt that stops early has to SAY
# it stopped early; these are how it says so.
_ELISION = ("...", ". . .", "…", "[...]", "[…]")


def fold(text: str) -> str:
    """Presentation normalisation only. Documented, reversible in meaning."""
    text = unicodedata.normalize("NFKC", text or "")
    text = "".join(_PUNCT_FOLD.get(ch, ch) for ch in text)
    return re.sub(r"\s+", " ", text).strip()


def _token_key(word: str) -> str:
    """One word, compared as the same word whichever way it was typed.

    Diacritics are folded because a model routinely retypes `Bahá'u'lláh` as
    `Baha'u'llah`, and refusing that would reject a correct quotation for a
    keyboard difference. The CANONICAL characters are still what gets printed,
    so nothing is lost by being lenient HERE: the source's own spelling is what
    reaches the card either way.
    """
    w = fold(word).lower()
    w = unicodedata.normalize("NFD", w)
    w = "".join(c for c in w if not unicodedata.combining(c))
    # Leading/trailing punctuation is presentation; internal punctuation is not.
    return w.strip("\"'()[]{}.,;:!?-*")


def _tokens(text: str) -> list[str]:
    return [t for t in (_token_key(w) for w in fold(text).split()) if t]


def strip_elision(text: str) -> tuple[str, bool]:
    """Take the elision marks off the ends. Returns (body, was_elided)."""
    body = fold(text).strip()
    elided = False
    changed = True
    while changed:
        changed = False
        for mark in _ELISION:
            if body.startswith(mark):
                body, elided, changed = body[len(mark):].strip(), True, True
            if body.endswith(mark):
                body, elided, changed = body[: -len(mark)].strip(), True, True
    return body.strip(" \"'"), elided


@dataclass
class Verdict:
    """What a verification actually establishes, and nothing more."""
    verified: bool
    reason: str
    # The SOURCE's exact characters for the matched span. This -- never the
    # candidate's -- is what a caller prints.
    canonical_text: str = ""
    source: str = ""
    section: str = ""
    link: str = ""
    # A reproducible locator: where in the passage the span sits, so somebody
    # can find it again without re-running a search.
    locator: str = ""
    text_sha256: str = ""
    method: str = VERIFIER_VERSION
    elided: bool = False
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "verified": self.verified, "reason": self.reason,
            "canonical_text": self.canonical_text, "source": self.source,
            "section": self.section, "link": self.link, "locator": self.locator,
            "text_sha256": self.text_sha256, "method": self.method,
            "elided": self.elided, "notes": list(self.notes),
        }


def _sha(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _find_span(candidate: str, passage: str) -> Optional[tuple[str, int, int]]:
    """Locate the candidate inside the passage as a contiguous run of WHOLE
    words, and return (the passage's own characters, first word index, last).

    Contiguous and in order. This is the check the overlap score could not make:
    a deleted "not" breaks the run, and so does a reordering.
    """
    want = _tokens(candidate)
    if not want:
        return None
    spans = [(m.group(0), m.start(), m.end()) for m in re.finditer(r"\S+", passage)]
    keys = [_token_key(s[0]) for s in spans]
    n = len(want)
    for i in range(len(keys) - n + 1):
        if keys[i:i + n] == want:
            return passage[spans[i][1]:spans[i + n - 1][2]], i, i + n - 1
    return None


def _boundaries_honest(passage: str, span: str, elided: bool) -> tuple[bool, str]:
    """Does the excerpt begin and end where it may?

    A contiguous substring is not enough. Starting one word after a negation
    turns a prohibition into an instruction while every word remains the
    source's own, and the run-of-words check above cannot see it -- the shorter
    span matches perfectly. So an excerpt must begin at a sentence start and end
    at sentence punctuation, and an early stop must be marked as an elision.
    """
    start = passage.find(span)
    if start < 0:
        return False, "the matched span could not be located in the passage"

    before = passage[:start].rstrip()
    if before:
        tail = before.rstrip("\"')]}")
        if not tail or tail[-1] not in ".!?":
            return False, ("the excerpt begins in the middle of a sentence, which can "
                           "reverse or obscure what the passage says -- quote from the "
                           "start of a sentence, or mark the omission")
    else:
        # A passage that itself opens mid-sentence (overlap chunking does this)
        # must not be printed as though it were complete.
        first_alpha = next((c for c in span if c.isalpha()), "")
        if first_alpha and first_alpha.islower():
            return False, ("the passage itself begins mid-sentence, so this cannot be "
                           "printed as a complete quotation")

    after = passage[start + len(span):].strip()
    body = span.rstrip().rstrip("\"')]}")
    ends_clean = bool(body) and body[-1] in ".!?"
    if not ends_clean and not elided:
        if after:
            return False, ("the excerpt stops in the middle of a sentence without "
                           "marking the omission")
        return False, "the excerpt does not end at the end of a sentence"
    return True, ""


def verify_quotation(candidate: str, passages: Sequence[dict],
                     expect_source: str = "",
                     allow_elision: bool = True) -> Verdict:
    """
    Is `candidate` genuinely a quotation from one of `passages`?

    `passages` are dicts with at least `text`, normally also `source`, `section`
    and `link` -- whatever the local corpus actually knows. They are the
    AUTHORIZED sources for this particular run; nothing outside them can verify
    anything, and an empty list is an honest "cannot verify", never a pass.

    `expect_source` is checked when given: correct words under an invented
    author are not a verified quotation (requirement 5). A mismatch fails, and
    says which source the words actually came from.
    """
    text = (candidate or "").strip()
    if not text:
        return Verdict(False, "there is no quotation to check")
    if not passages:
        return Verdict(False, ("no source passages were available to check against, so "
                               "this cannot be called verified"))

    body, elided = strip_elision(text)
    if elided and not allow_elision:
        return Verdict(False, "this quotation is an excerpt, and excerpts are not "
                              "allowed here")
    if not _tokens(body):
        return Verdict(False, "the quotation has no words to check")

    near_misses: list[str] = []
    for passage in passages:
        raw = str((passage or {}).get("text") or "")
        if not raw.strip():
            continue
        # Matched against the passage's RAW characters, never a folded copy of
        # them: the span handed back is what will be PRINTED, and it has to be
        # the corpus's own characters (requirement 3). Folding happens per word
        # inside `_token_key`, so a candidate typed with straight quotes still
        # matches a source with curly ones -- and still prints the curly ones.
        found = _find_span(body, raw)
        if not found:
            continue
        span, first, last = found

        ok, why = _boundaries_honest(raw, span, elided)
        if not ok:
            near_misses.append(why)
            continue

        source = str(passage.get("source") or "").strip()
        if expect_source and source and fold(expect_source).lower() != fold(source).lower():
            near_misses.append(
                f"these words are from {source}, not from {expect_source}")
            continue
        if expect_source and not source:
            near_misses.append("the passage carries no attribution to check against")
            continue

        return Verdict(
            verified=True,
            reason=("word-for-word from the source, from a sentence start to a sentence "
                    "end" + (", with the omission marked" if elided else "")),
            # The CORPUS's characters, never the candidate's.
            canonical_text=span,
            source=source,
            section=str(passage.get("section") or "").strip(),
            link=str(passage.get("link") or "").strip(),
            locator=f"words {first + 1}-{last + 1} of the passage",
            text_sha256=_sha(span),
            elided=elided,
        )

    if near_misses:
        # A near miss is a FAILURE, never a correction (rule 84's discipline).
        return Verdict(False, near_misses[0], notes=near_misses[:3])
    return Verdict(False, ("these words do not appear in the selected sources in this "
                           "order, so this is not a verified quotation"))
