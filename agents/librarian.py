"""
Librarian agent — the citation fact-check backstop for bahAI Workforce.

Two modes:
1. RETRIEVE: given a topic, find the best-matching verified quotation from
   the local ChromaDB index of the 7 source Bahá'í texts.
2. VERIFY: given a piece of text that may contain a spiritual claim or quote,
   check it against the index and flag hallucinations or paraphrases.

Falls back to live reference.bahai.org search if the local index doesn't
cover what's needed (i.e., for texts outside the 7 core works).

The index is built by scripts/ingest_texts.py — run that first.
"""

import os
import re
import requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(dotenv_path=str(Path(__file__).parent.parent / ".env"))

VECTOR_STORE_PATH = str(Path(__file__).parent.parent / "vector_store")
OLLAMA_BASE = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")

RUHI_BOOK1_COLLECTION = "ruhi_book1_quotes"

_chroma_client = None
_collections: dict = {}   # collection name -> Collection (or None if missing)


def _get_collection(name: str = "bahai_texts"):
    global _chroma_client
    if name in _collections:
        return _collections[name]
    import chromadb
    if _chroma_client is None:
        _chroma_client = chromadb.PersistentClient(path=VECTOR_STORE_PATH)
    try:
        _collections[name] = _chroma_client.get_collection(name)
    except Exception:
        _collections[name] = None
    return _collections[name]


def _embed(text: str) -> list[float]:
    resp = requests.post(
        f"{OLLAMA_BASE}/api/embeddings",
        json={"model": EMBED_MODEL, "prompt": text},
        timeout=60
    )
    resp.raise_for_status()
    return resp.json()["embedding"]


def retrieve(query: str, n_results: int = 3, collection_name: str = "bahai_texts") -> list[dict]:
    """
    Find the top-N most relevant passages from the local index.
    Returns list of dicts with: text, source, section, link, score.
    Returns empty list if the index hasn't been built yet.
    collection_name selects which ChromaDB collection to search — the default
    "bahai_texts" is the full 7-text index the bookmark pipeline uses; see
    retrieve_ruhi_book1() for the Quote Card pipeline's restricted index.
    """
    collection = _get_collection(collection_name)
    if collection is None:
        return []

    embedding = _embed(query)
    results = collection.query(
        query_embeddings=[embedding],
        n_results=min(n_results, collection.count()),
        include=["documents", "metadatas", "distances"],
    )

    passages = []
    for doc, meta, dist in zip(
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0],
    ):
        passages.append({
            "text": doc,
            "source": meta.get("source", ""),
            "section": meta.get("section", ""),
            "link": meta.get("link", ""),
            "score": round(1 - dist, 4),  # cosine similarity (approximate)
        })
    return passages


def retrieve_ruhi_book1(query: str, n_results: int = 3) -> list[dict]:
    """
    Quote Card pipeline ONLY — searches the restricted index built from
    agents/ruhi_book1_source.py (Ruhi Institute Book 1, "Reflections on the
    Life of the Spirit") instead of the full 7-text corpus. Owner decision,
    2026-07: quote cards may only ever print a quote from this one book.
    Build/rebuild the index with scripts/ingest_ruhi_book1.py. Returns []
    if that index hasn't been built yet — callers must treat this as a hard
    stop, not fall back to the general index (that would silently violate
    the restriction).
    """
    return retrieve(query, n_results=n_results, collection_name=RUHI_BOOK1_COLLECTION)


def verify(claim_text: str) -> dict:
    """
    Check whether the text contains verifiable Bahá'í quotations or spiritual claims.
    Returns: {verified: bool, issues: list[str], supporting_passages: list[dict]}

    Checks:
    1. Verbatim quote accuracy — searches top 5 passages, not just the best match
    2. Author attribution — flags if the claim names Bahá'u'lláh but the best
       supporting passage is from 'Abdu'l-Bahá or vice versa
    3. Low confidence — flags if no passage scores above the similarity threshold
    """
    passages = retrieve(claim_text, n_results=5)
    issues = []

    if not passages:
        issues.append(
            "Local index not built yet — run scripts/ingest_texts.py before the Librarian can verify claims. "
            "Until then, cross-check manually at reference.bahai.org."
        )
        return {"verified": False, "issues": issues, "supporting_passages": []}

    THRESHOLD = 0.55
    best = passages[0]

    if best["score"] < THRESHOLD:
        issues.append(
            f"No close match found in the 7 source texts (best score: {best['score']:.2f}). "
            f"The claim may be paraphrased, outside the indexed texts, or fabricated. "
            f"Verify at reference.bahai.org before using."
        )

    # Verbatim quote check — search ALL top passages, not just the single best match.
    # The exact passage may score lower than a thematically similar one.
    # Normalize whitespace before comparing: HTML scraping leaves double-spaces in stored text.
    def _norm(s: str) -> str:
        return re.sub(r'\s+', ' ', s).lower().strip()

    quoted = re.findall(r'"([^"]{20,})"', claim_text)
    all_passage_texts_norm = [_norm(p["text"]) for p in passages]
    for q in quoted:
        q_norm = _norm(q)
        found_in_any = any(q_norm in t for t in all_passage_texts_norm)
        if not found_in_any:
            issues.append(
                f'Quoted text not found verbatim in top {len(passages)} indexed passages: '
                f'"{q[:80]}{"…" if len(q) > 80 else ""}" — '
                f"verify exact wording at {best.get('link', 'reference.bahai.org')}."
            )

    # Author attribution check — if the claim names a specific author, confirm
    # the best-matching passage agrees. Misattribution is a common error in spiritual content.
    AUTHOR_KEYS = {
        "bahá'u'lláh": "Bahá'u'lláh",
        "bahaullah": "Bahá'u'lláh",
        "'abdu'l-bahá": "'Abdu'l-Bahá",
        "abdu'l-baha": "'Abdu'l-Bahá",
        "abdul-baha": "'Abdu'l-Bahá",
        "the báb": "The Báb",
        "the bab": "The Báb",
    }
    claim_lower = claim_text.lower()
    for key, canonical in AUTHOR_KEYS.items():
        if key in claim_lower:
            best_source = best.get("source", "")
            if canonical not in best_source:
                issues.append(
                    f'The claim attributes a statement to {canonical}, but the closest indexed '
                    f'passage is from {best_source} (score {best["score"]:.2f}). '
                    f"Confirm the attribution at {best.get('link', 'reference.bahai.org')}."
                )
            break

    return {
        "verified": len(issues) == 0,
        "issues": issues,
        "supporting_passages": passages[:3],
    }


def live_search(query: str) -> str:
    """
    Fallback: search reference.bahai.org for the query.
    Returns raw excerpt text for the Librarian LLM to interpret.
    This should only be called when the local index doesn't cover what's needed.
    """
    search_url = f"https://www.bahai.org/search/#q={requests.utils.quote(query)}&lang=en"
    try:
        from bs4 import BeautifulSoup
        headers = {"User-Agent": "bahAI-Workforce-Librarian/1.0 (personal research tool)"}
        resp = requests.get(search_url, headers=headers, timeout=15)
        soup = BeautifulSoup(resp.text, "html.parser")
        # Extract visible text from search result snippets
        snippets = soup.select(".search-result-excerpt, .excerpt, p")
        texts = [s.get_text(strip=True) for s in snippets[:5] if len(s.get_text(strip=True)) > 40]
        return "\n\n".join(texts) if texts else f"No excerpts found. Visit: {search_url}"
    except Exception as e:
        return f"Live search failed: {e}. Visit manually: {search_url}"


def format_citation(passage: dict) -> str:
    """Format a retrieved passage into a clean citation string for use in prompts."""
    parts = []
    if passage.get("text"):
        parts.append(f'"{passage["text"].strip()}"')
    if passage.get("source"):
        parts.append(f"— {passage['source']}")
    if passage.get("section"):
        parts.append(f", {passage['section']}")
    if passage.get("link"):
        parts.append(f" · {passage['link']}")
    return " ".join(parts)


if __name__ == "__main__":
    print("Testing retrieve (requires index to be built)...")
    results = retrieve("work worship craft")
    if results:
        for r in results:
            print(f"[{r['score']:.3f}] {r['source']} — {r['text'][:120]}")
    else:
        print("Index not built yet. Run scripts/ingest_texts.py first.")
