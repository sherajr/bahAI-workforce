"""
Embed and ingest agents/ruhi_book1_source.py's curated quote list into its own
ChromaDB collection ("ruhi_book1_quotes"), in the SAME vector_store/ used by
the main Librarian index — this is a separate collection, not a rebuild of
"bahai_texts", so the bookmark pipeline's retrieval is completely unaffected.

Each quote is short and already a discrete unit (unlike ingest_texts.py,
which chunks long passages) — one embedding per quote, no chunking needed.

Run after editing agents/ruhi_book1_source.py. Requires Ollama running with
nomic-embed-text (same requirement as ingest_texts.py).
"""

import requests
import chromadb
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from agents.ruhi_book1_source import RUHI_BOOK1_QUOTES

VECTOR_STORE = str(Path(__file__).parent.parent / "vector_store")
OLLAMA_BASE = "http://127.0.0.1:11434"
EMBED_MODEL = "nomic-embed-text"
COLLECTION_NAME = "ruhi_book1_quotes"


def embed(text: str) -> list[float]:
    resp = requests.post(
        f"{OLLAMA_BASE}/api/embeddings",
        json={"model": EMBED_MODEL, "prompt": text},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["embedding"]


def ingest():
    try:
        r = requests.get(f"{OLLAMA_BASE}/api/tags", timeout=5)
        models = [m["name"] for m in r.json().get("models", [])]
        if not any(EMBED_MODEL in m for m in models):
            print(f"ERROR: '{EMBED_MODEL}' not found in Ollama. Run: ollama pull {EMBED_MODEL}")
            return
        print(f"Ollama OK — using {EMBED_MODEL} for embeddings\n")
    except Exception as e:
        print(f"ERROR: Can't reach Ollama at {OLLAMA_BASE}: {e}")
        return

    client = chromadb.PersistentClient(path=VECTOR_STORE)

    try:
        client.delete_collection(COLLECTION_NAME)
        print(f"Deleted existing '{COLLECTION_NAME}' collection (rebuilding fresh).\n")
    except Exception:
        pass

    collection = client.create_collection(COLLECTION_NAME, metadata={"hnsw:space": "cosine"})

    ids, docs, metas, embeds = [], [], [], []
    for i, q in enumerate(RUHI_BOOK1_QUOTES):
        text = q["text"].strip()
        try:
            embedding = embed(text)
        except Exception as e:
            print(f"  Embed error (quote {i}): {e}")
            continue
        ids.append(f"ruhi-book1-q{i}")
        docs.append(text)
        metas.append({"source": q["source"], "section": q["section"], "link": ""})
        embeds.append(embedding)
        print(f"  [{i + 1}/{len(RUHI_BOOK1_QUOTES)}] embedded — {q['source'][:60]}")

    collection.add(ids=ids, documents=docs, metadatas=metas, embeddings=embeds)

    print(f"\n{'=' * 60}")
    print(f"Ingestion complete. Collection '{COLLECTION_NAME}': {collection.count()} quotes indexed.")


if __name__ == "__main__":
    print("bahAI Workforce — Building Ruhi Book 1 Quote-Card Index\n")
    ingest()
