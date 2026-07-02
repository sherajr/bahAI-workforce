"""
Routes each task type to the right LLM backend:
  local  → Ollama (qwen3-16k, free, fast, private)
  grok   → xAI Grok API (paid, higher quality for creative/complex tasks)
  image  → Replicate API (image generation)

Usage:
    result = call_llm("copy", messages=[...])
    result = call_llm("plan", messages=[...])
"""

import os
import json
import time
import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=str(__import__("pathlib").Path(__file__).parent.parent / ".env"))

OLLAMA_BASE = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3-16k:latest")
XAI_KEY = os.getenv("XAI_API_KEY", "")
XAI_BASE = os.getenv("XAI_BASE_URL", "https://api.x.ai/v1")
XAI_MODEL = os.getenv("XAI_MODEL", "grok-2-1212")

# Task types routed to Grok; everything else goes local
GROK_TASK_TYPES = {"copy", "copywriting", "review", "creative_writing", "complex_analysis", "scribe", "reviewer", "librarian"}


def call_llm(task_type: str, messages: list[dict], temperature: float = 0.7, max_tokens: int = 4096) -> str:
    """
    Send messages to the right LLM based on task_type.
    Returns the assistant's reply as a string.
    """
    if task_type in GROK_TASK_TYPES:
        return _call_grok(messages, temperature, max_tokens)
    return _call_ollama(messages, temperature, max_tokens)


def _call_ollama(messages: list[dict], temperature: float, max_tokens: int) -> str:
    payload = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
        # num_predict covers thinking tokens + response tokens for Qwen3.
        # Qwen3's thinking can use several hundred tokens before writing content,
        # so we always budget at least 2000 even for short expected outputs.
        "options": {"temperature": temperature, "num_predict": max(max_tokens, 2000)},
    }
    resp = requests.post(f"{OLLAMA_BASE}/api/chat", json=payload, timeout=120)
    resp.raise_for_status()
    return resp.json()["message"]["content"]


def _call_grok(messages: list[dict], temperature: float, max_tokens: int, _attempt: int = 0) -> str:
    headers = {"Authorization": f"Bearer {XAI_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": XAI_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    try:
        resp = requests.post(f"{XAI_BASE}/chat/completions", headers=headers, json=payload, timeout=210)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    except requests.HTTPError as e:
        if _attempt < 2 and e.response is not None and e.response.status_code in (429, 500, 502, 503):
            time.sleep(3 * (_attempt + 1))
            return _call_grok(messages, temperature, max_tokens, _attempt + 1)
        raise


def get_embedding(text: str) -> list[float]:
    """Generate an embedding using nomic-embed-text via Ollama."""
    embed_model = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")
    payload = {"model": embed_model, "prompt": text}
    resp = requests.post(f"{OLLAMA_BASE}/api/embeddings", json=payload, timeout=60)
    resp.raise_for_status()
    return resp.json()["embedding"]


if __name__ == "__main__":
    print("Testing local (Ollama)...")
    reply = call_llm("plan", [{"role": "user", "content": "Say 'local model OK' and nothing else."}], max_tokens=20)
    print(f"Local: {reply.strip()}")

    print("\nTesting Grok...")
    reply = call_llm("copy", [{"role": "user", "content": "Say 'Grok OK' and nothing else."}], max_tokens=20)
    print(f"Grok: {reply.strip()}")
