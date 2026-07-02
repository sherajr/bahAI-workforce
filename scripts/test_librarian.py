"""
Test the Librarian's vector retrieval after ingest_texts.py has been run.
Run from the project root: python scripts/test_librarian.py
"""

import sys
import io
from pathlib import Path
# Force UTF-8 on Windows so Arabic transliteration characters (Ḥ, ā, etc.) don't crash
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.librarian import retrieve, verify, format_citation

# Queries relevant to Sheraj's Etsy craft/art bookmark work
RETRIEVE_TESTS = [
    ("work as worship service God praise", "Work = worship — core Bahá'í principle"),
    ("craftsmanship arts sciences reflection", "Source of crafts & arts"),
    ("beauty light radiance spirit", "Beauty in the spiritual writings"),
    ("heart soul love joy peace", "Inner spiritual life — good for bookmark quotes"),
    ("unity humanity oneness all people", "Unity of humanity"),
    ("knowledge wisdom truth seeker", "Spiritual knowledge & seeking"),
]

# Verify tests — checking accuracy of citations Sheraj might put on a product
VERIFY_TESTS = [
    (
        '"Blessed is he who preferreth his brother before himself." — Bahá\'u\'lláh, Hidden Words',
        "Exact quote from Hidden Words Arabic — should find verbatim match",
    ),
    (
        "Bahá'u'lláh said that work done in the spirit of service is worship.",
        "Paraphrase attributed to Bahá'u'lláh — closest match may be 'Abdu'l-Bahá",
    ),
    (
        '"The source of crafts, sciences and arts is the power of reflection."',
        "Direct quote from Tablets of Bahá'u'lláh — should verify cleanly",
    ),
    (
        "Love gives life to the lifeless. Love lights a flame in the heart that is cold.",
        "Passage from Paris Talks — no author named, general claim",
    ),
]


def truncate_at_word(text: str, max_len: int) -> str:
    """Truncate at a word boundary rather than mid-character."""
    if len(text) <= max_len:
        return text
    cut = text[:max_len].rsplit(" ", 1)[0]
    return cut + " …"


def separator(char="-", width=60):
    print(char * width)


def run_retrieve():
    separator("=")
    print("RETRIEVAL TESTS")
    separator("=")

    for query, description in RETRIEVE_TESTS:
        print(f"\nQuery : {query}")
        print(f"About : {description}")
        separator()

        results = retrieve(query, n_results=3)
        if not results:
            print("  No results — index not built. Run scripts/ingest_texts.py first.")
            continue

        for r in results:
            text_preview = truncate_at_word(r["text"].replace("\n", " "), 160)
            print(f"  [{r['score']:.4f}]  {r['source']}")
            print(f"           {r['section']}")
            print(f"           \"{text_preview}\"")
            print(f"           {r['link']}")
            print()


def run_verify():
    separator("=")
    print("VERIFY TESTS  (citation accuracy check)")
    separator("=")

    for text, note in VERIFY_TESTS:
        print(f"\nClaim : {truncate_at_word(text, 100)}")
        print(f"Note  : {note}")
        separator()

        result = verify(text)
        status = "VERIFIED ✓" if result["verified"] else "NEEDS REVIEW ✗"
        print(f"  Status : {status}")

        for issue in result["issues"]:
            print(f"  Issue  : {truncate_at_word(issue, 140)}")

        if result["supporting_passages"]:
            print(f"  Supporting passages:")
            for p in result["supporting_passages"][:2]:
                preview = truncate_at_word(p["text"].replace("\n", " "), 110)
                print(f"    [{p['score']:.4f}] {p['source']}, {p['section']}")
                print(f"             \"{preview}\"")
        print()


def run_citation_format():
    separator("=")
    print("CITATION FORMAT  (how citations appear in prompts)")
    separator("=")
    queries = [
        "work service worship praise",
        "love heart divine",
    ]
    for q in queries:
        results = retrieve(q, n_results=1)
        if results:
            print(f"\nQuery: \"{q}\"")
            print(f"  {format_citation(results[0])}")
    if not any(retrieve(q, n_results=1) for q in queries):
        print("  (no results — build the index first)")


if __name__ == "__main__":
    print("bahAI Workforce — Librarian Test Suite\n")
    run_retrieve()
    run_verify()
    run_citation_format()
    print("\nDone.")
