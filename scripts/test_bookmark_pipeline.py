"""
Test the Phase 3 bookmark pipeline agents end-to-end.
Run from the project root: python scripts/test_bookmark_pipeline.py

By default DRY_RUN=True — skips Replicate image generation to save API credits.
Set DRY_RUN=False to actually call Replicate and produce a real image URL.
"""

import sys
import io
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent.parent))

# --- Config ---
DRY_RUN = True
THEME = "The unity of humanity and the oneness of all people"
PLACEHOLDER_IMAGE_URL = "https://replicate.delivery/placeholder/bahai-bookmark.jpg"


def sep(char="=", width=60):
    print(char * width)


# ─── Step 1: Librarian ───────────────────────────────────────────────────────

def run_librarian() -> list[dict]:
    sep()
    print("STEP 1: Librarian — find relevant citations")
    sep()
    from agents.librarian import retrieve
    citations = retrieve(THEME, n_results=3)
    if not citations:
        print("  No results — is the vector index built? Run: python scripts/ingest_texts.py")
        return []
    for c in citations:
        preview = c["text"].replace("\n", " ")[:120]
        print(f"  [{c['score']:.4f}] {c['source']}")
        print(f"           \"{preview} ...\"")
    print()
    return citations


# ─── Step 2: Artist — build image prompt ─────────────────────────────────────

def run_artist_brief(citations: list[dict]) -> str:
    sep()
    print("STEP 2: Artist — build FLUX.1 image prompt (Ollama/Qwen3)")
    sep()
    from agents.artist import build_image_prompt
    prompt = build_image_prompt(THEME, citations)
    print(f"  Generated prompt ({len(prompt)} chars):")
    print(f"  {prompt[:400]}")
    print()
    return prompt


# ─── Step 3: Artist — generate image ─────────────────────────────────────────

def run_artist_generate(image_prompt: str) -> str:
    sep()
    print("STEP 3: Artist — generate image via xAI Grok")
    sep()
    if DRY_RUN:
        print("  [DRY RUN] Skipping Grok image API call.")
        print(f"  Placeholder URL: {PLACEHOLDER_IMAGE_URL}")
        print()
        return PLACEHOLDER_IMAGE_URL
    from agents.artist import generate_image
    print("  Calling xAI image generation...")
    result = generate_image(image_prompt)
    print(f"  Image URL: {result['image_url']}")
    print(f"  Model:     {result['model']}")
    print()
    return result["image_url"]


# ─── Step 4: Scribe — write Etsy listing ─────────────────────────────────────

def run_scribe(image_prompt: str, citations: list[dict], image_url: str) -> dict:
    sep()
    print("STEP 4: Scribe — write Etsy listing (Grok)")
    sep()
    from agents.scribe import write_listing
    listing = write_listing(THEME, image_prompt, citations, image_url)
    title = listing.get("title", "—")
    tags  = listing.get("tags", [])
    price = listing.get("price_note", "—")
    desc  = listing.get("description", "")
    print(f"  Title      : {title}")
    print(f"  Tags       : {', '.join(tags[:8])} ...")
    print(f"  Price note : {price}")
    print(f"  Description (first 200 chars):")
    print(f"    {desc[:200]} ...")
    print()
    return listing


# ─── Step 5: Reviewer — score against constitution ────────────────────────────

def run_reviewer(image_prompt: str, listing: dict) -> dict:
    sep()
    print("STEP 5: Reviewer — score against 7 constitution principles (Grok)")
    sep()
    from agents.reviewer import score
    result = score(THEME, image_prompt, listing)
    overall = result.get("overall", 0)
    passed  = result.get("passed", False)
    rec     = result.get("recommendation", "—")
    status  = "PASSED ✓" if passed else "NEEDS REVISION ✗"
    print(f"  Overall      : {overall}/10 — {status}")
    print(f"  Recommendation: {rec}")
    print()
    print("  Principle scores:")
    for k, v in result.get("scores", {}).items():
        if isinstance(v, dict):
            note = v.get("note", "")[:80]
            print(f"    {k}: {v.get('score', '?')}/10 — {note}")
    print()
    return result


# ─── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("bahAI Workforce — Bookmark Pipeline Test")
    print(f"Theme   : {THEME}")
    print(f"Dry run : {DRY_RUN} (image {'SKIPPED' if DRY_RUN else 'WILL BE GENERATED via Replicate'})\n")

    citations   = run_librarian()
    image_prompt = run_artist_brief(citations)
    image_url   = run_artist_generate(image_prompt)
    listing     = run_scribe(image_prompt, citations, image_url)
    review      = run_reviewer(image_prompt, listing)

    sep()
    print("PIPELINE COMPLETE")
    sep()
    print(f"  Theme      : {THEME}")
    print(f"  Image      : {image_url}")
    print(f"  Title      : {listing.get('title', '—')}")
    print(f"  Review     : {review.get('overall', 0)}/10 — {review.get('recommendation', '—')}")
    print()
    print("Next steps:")
    print("  1. Start the API:    python agents/api.py")
    print("  2. Import workflow:  n8n-workflows/bookmark-pipeline.json")
    print("  3. Test via n8n with POST to /webhook/bookmark-create")
    print('     Body: {"theme": "your bookmark theme here"}')
