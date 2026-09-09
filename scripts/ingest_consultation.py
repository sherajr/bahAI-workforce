"""
Chunk, embed and ingest `Consultation: A Compilation` into its OWN ChromaDB
collection. Run after `scripts/download_consultation_compilation.py`.

Embeddings via Ollama nomic-embed-text (must be running) — the same model the
seven-text index uses, so the two are comparable.

TWO THINGS ARE DELIBERATE AND SHOULD NOT BE "TIDIED":

1. **Its own collection (`consultation_compilation`), not `bahai_texts`.**
   `bahai_texts` is what a quote card can print from via `lib:<slug>`
   (rule 11), and that rule says never widen the printable sources silently.
   Live Consultation is not making a product, so it may read a broader library
   (rule 84) — but the product pipelines must not inherit that, and with a
   separate collection they structurally cannot.

2. **One passage is one chunk.** `ingest_texts.py` splits at 512 characters
   with a 100-character overlap, which is right for a book you are searching
   for a theme. Here a passage IS the unit: it has its own citation printed
   beneath it, and half a passage would be a fragment attributed to a source
   that says something more complete. Rule 11's `_span_boundary_ok` exists
   because overlap chunking can open a chunk mid-sentence; not chunking at all
   removes that failure mode rather than guarding against it.

Re-running replaces the collection, so a corrected download can simply be
ingested again.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import chromadb
import requests

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "texts" / "consultation-compilation.json"
VECTOR_STORE = str(ROOT / "vector_store")
OLLAMA_BASE = "http://127.0.0.1:11434"
EMBED_MODEL = "nomic-embed-text"
COLLECTION_NAME = "consultation_compilation"


def embed(text: str) -> list[float]:
    resp = requests.post(
        f"{OLLAMA_BASE}/api/embeddings",
        json={"model": EMBED_MODEL, "prompt": text},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["embedding"]


def main() -> int:
    if not SOURCE.exists():
        print(f"ERROR: {SOURCE.name} not found. "
              "Run scripts/download_consultation_compilation.py first.")
        return 1

    try:
        r = requests.get(f"{OLLAMA_BASE}/api/tags", timeout=5)
        models = [m["name"] for m in r.json().get("models", [])]
        if not any(EMBED_MODEL in m for m in models):
            print(f"ERROR: '{EMBED_MODEL}' not found in Ollama. "
                  f"Run: ollama pull {EMBED_MODEL}")
            return 1
    except Exception as e:
        print(f"ERROR: Ollama is not reachable at {OLLAMA_BASE} ({type(e).__name__}). "
              "Start it and try again.")
        return 1

    data = json.loads(SOURCE.read_text(encoding="utf-8"))
    passages = data.get("passages") or []
    if not passages:
        print("ERROR: no passages in the source file.")
        return 1

    client = chromadb.PersistentClient(path=VECTOR_STORE)
    try:
        client.delete_collection(COLLECTION_NAME)
        print(f"Replaced the existing '{COLLECTION_NAME}' collection.")
    except Exception:
        pass
    # `hnsw:space` MUST match the other collections. Chroma's default is L2,
    # and `librarian.retrieve` reports `1 - distance` as a similarity score --
    # which is only meaningful for cosine. Left at the default this collection
    # returned scores around -260 while `bahai_texts` returned 0.72, so results
    # from the two could not be ranked against each other and
    # `librarian.THRESHOLD` would have been meaningless here. The search still
    # LOOKED right, which is what made it worth checking.
    collection = client.create_collection(COLLECTION_NAME,
                                          metadata={"hnsw:space": "cosine"})

    ids, docs, metas, embeds = [], [], [], []
    for i, passage in enumerate(passages):
        text = (passage.get("text") or "").strip()
        if not text:
            continue
        ids.append(f"{data['slug']}_{i}")
        docs.append(text)
        metas.append({
            # `source` and `section` are the keys `librarian.retrieve` reads,
            # and they must mean here what they mean in `bahai_texts` or every
            # consumer would need a special case for this collection.
            # source  = who said it, and where it is collected
            # section = the passage's OWN citation, printed beneath it on the
            #           source page -- what makes it checkable (rule 84)
            "source": ((passage.get("section", "")
                        .replace("From the Writings and Utterances of ", "")
                        .replace("From the Writings of ", "")
                        .replace("From Letters Written on Behalf of ", "On behalf of ")
                        .replace("From Letters Written by ", "")
                        .replace("From a Letter Written on Behalf of ", "On behalf of ")
                        .strip() or data["title"]) + ", " + data["title"]),
            "title": data["title"],
            "slug": data["slug"],
            # The passage's OWN source, not the compilation's. This is the
            # whole reason the compilation gets its own downloader: a citation
            # that named only "Consultation: A Compilation" would be true and
            # useless, because nobody could check it against anything.
            "section": passage.get("citation") or passage.get("section", ""),
            "author_group": passage.get("section", ""),
            "link": passage.get("link", data.get("source_url", "")),
            # Guidance addressed to elected Bahá'í institutions. A heuristic,
            # labelled as one wherever it surfaces, and used to keep Assembly
            # guidance out of a general (non-Bahá'í) consultation rather than
            # to hide it from a Bahá'í one.
            "institutional": bool(passage.get("institutional")),
        })
        print(f"  [{i + 1}/{len(passages)}] embedding ({len(text)} chars)...")
        embeds.append(embed(text))

    collection.add(ids=ids, documents=docs, metadatas=metas, embeddings=embeds)
    inst = sum(1 for m in metas if m["institutional"])
    print()
    print(f"Ingested {len(ids)} passages into '{COLLECTION_NAME}' "
          f"({inst} addressed to institutions).")
    print("`bahai_texts` was not touched, so nothing changed for quote cards (rule 11).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
