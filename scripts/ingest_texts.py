"""
Chunk, embed, and ingest downloaded Bahá'í texts into ChromaDB.
Run after scripts/download_texts.py.

Embeddings via Ollama nomic-embed-text (must be running).
"""

import json
import time
import requests
import chromadb
from chonkie import SentenceChunker
from pathlib import Path

TEXTS_DIR = Path(__file__).parent.parent / "texts"
VECTOR_STORE = str(Path(__file__).parent.parent / "vector_store")
OLLAMA_BASE = "http://127.0.0.1:11434"
EMBED_MODEL = "nomic-embed-text"
COLLECTION_NAME = "bahai_texts"

MIN_CHUNK_LEN = 25    # skip chunks shorter than this (invocations, headers)
BATCH_SIZE = 40       # embed + upsert in batches

# SentenceChunker with tokenizer="character" (default) — chunk_size is in characters.
# 512 chars ≈ 3-5 Bahá'í sentences; 100-char overlap carries roughly 1 sentence of context.
_chunker = SentenceChunker(chunk_size=512, chunk_overlap=100, min_sentences_per_chunk=1)


def embed(text: str) -> list[float]:
    resp = requests.post(
        f"{OLLAMA_BASE}/api/embeddings",
        json={"model": EMBED_MODEL, "prompt": text},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["embedding"]


def chunk_text(text: str) -> list[str]:
    """Split text at sentence boundaries using Chonkie SentenceChunker."""
    text = text.strip()
    if not text:
        return []
    chunks = _chunker(text)
    return [c.text.strip() for c in chunks if len(c.text.strip()) >= MIN_CHUNK_LEN]


def flush_batch(collection, ids, docs, metas, embeds, total):
    collection.add(ids=ids, documents=docs, metadatas=metas, embeddings=embeds)
    print(f"    → Added {len(ids)} chunks (running total: {total})")


def ingest():
    # Verify Ollama is up and the embed model exists
    try:
        r = requests.get(f"{OLLAMA_BASE}/api/tags", timeout=5)
        models = [m["name"] for m in r.json().get("models", [])]
        if not any(EMBED_MODEL in m for m in models):
            print(f"ERROR: '{EMBED_MODEL}' not found in Ollama.")
            print(f"Run: ollama pull {EMBED_MODEL}")
            return
        print(f"Ollama OK — using {EMBED_MODEL} for embeddings\n")
    except Exception as e:
        print(f"ERROR: Can't reach Ollama at {OLLAMA_BASE}: {e}")
        return

    text_files = sorted(TEXTS_DIR.glob("*.json"))
    if not text_files:
        print("No text files found in texts/")
        print("Run: python scripts/download_texts.py")
        return

    print(f"Found {len(text_files)} text files in texts/\n")

    client = chromadb.PersistentClient(path=VECTOR_STORE)

    # Fresh build — delete old collection
    try:
        client.delete_collection(COLLECTION_NAME)
        print("Deleted existing 'bahai_texts' collection (rebuilding fresh).\n")
    except Exception:
        pass

    collection = client.create_collection(
        COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )

    grand_total = 0
    ids_buf, docs_buf, metas_buf, embeds_buf = [], [], [], []

    for path in text_files:
        data = json.loads(path.read_text(encoding="utf-8"))
        title = data["title"]
        author = data["author"]
        slug = data["slug"]
        source_url = data.get("source_url", "")
        passages = data.get("passages", [])

        if not passages:
            print(f"  SKIP {slug}.json — 0 passages (re-run download_texts.py?)")
            continue

        print(f"=== {title} ({len(passages)} passages) ===")
        text_chunk_count = 0

        for i, p in enumerate(passages):
            raw = p.get("text", "").strip()
            if not raw or len(raw) < MIN_CHUNK_LEN:
                continue

            section = p.get("section", "")
            link = p.get("link", source_url)
            chunks = chunk_text(raw)

            for j, chunk in enumerate(chunks):
                chunk_id = f"{slug}-p{i}-c{j}"

                try:
                    embedding = embed(chunk)
                except Exception as e:
                    print(f"  Embed error ({chunk_id}): {e}")
                    continue

                ids_buf.append(chunk_id)
                docs_buf.append(chunk)
                metas_buf.append({
                    "source": f"{author}, {title}",
                    "section": section,
                    "link": link,
                    "slug": slug,
                })
                embeds_buf.append(embedding)
                text_chunk_count += 1
                grand_total += 1

                if len(ids_buf) >= BATCH_SIZE:
                    flush_batch(collection, ids_buf, docs_buf, metas_buf, embeds_buf, grand_total)
                    ids_buf, docs_buf, metas_buf, embeds_buf = [], [], [], []
                    time.sleep(0.05)

        print(f"  {text_chunk_count} chunks from {title}\n")

    # Flush remainder
    if ids_buf:
        flush_batch(collection, ids_buf, docs_buf, metas_buf, embeds_buf, grand_total)

    final_count = collection.count()
    print(f"\n{'='*60}")
    print(f"Ingestion complete.")
    print(f"Collection '{COLLECTION_NAME}': {final_count} chunks indexed.")
    print(f"Next: python scripts/test_librarian.py")


if __name__ == "__main__":
    print("bahAI Workforce — Building Librarian Vector Index\n")
    ingest()
