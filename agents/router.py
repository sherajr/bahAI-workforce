"""
Routes each task type to the right LLM backend:
  local → Ollama (qwen3-16k, free, private) — everything by default
  grok  → xAI Grok API (paid, higher quality, vision) — task types in GROK_TASK_TYPES

Usage:
    result = call_llm("copy", messages=[...])
    result = call_llm("plan", messages=[...])
"""

import base64
import os
import time
import requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(dotenv_path=str(Path(__file__).parent.parent / ".env"))

from agents.state import record_spend

OLLAMA_BASE = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3-16k:latest")
XAI_KEY = os.getenv("XAI_API_KEY", "")
XAI_BASE = os.getenv("XAI_BASE_URL", "https://api.x.ai/v1")
XAI_MODEL = os.getenv("XAI_MODEL", "grok-2-1212")
XAI_VISION_MODEL = os.getenv("XAI_VISION_MODEL", XAI_MODEL)

# Anthropic (Claude Sonnet) — the Secretary's model and hers alone. The
# existing Artist/Scribe/Reviewer/Librarian routing stays on Ollama/Grok;
# never add pipeline task types here.
ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5")

# Task types routed to Grok; everything else goes local.
# Sheraj's directive (2026-07): only the Reviewer and Artist use the paid xAI API
# (they need vision); the Scribe and Librarian run on the local model.
GROK_TASK_TYPES = {"copy", "copywriting", "review", "creative_writing", "complex_analysis", "reviewer"}

# Flat per-call cost estimates (USD) for the Steward's metered P&L. Rough but
# consistent — refine against real xAI invoices; the point is that repaint-heavy
# runs cost visibly more than clean ones (Moderation, principle 5).
EST_COST_USD = {"grok_chat": 0.005, "grok_vision": 0.01, "image_gen": 0.05,
                "claude_chat": 0.01}


def record_api_spend(kind: str):
    """Meter one paid API call into the spend table. Never raises."""
    record_spend(kind, EST_COST_USD.get(kind, 0.0))


def call_llm(task_type: str, messages: list[dict], temperature: float = 0.7, max_tokens: int = 4096,
             json_mode: bool = False) -> str:
    """
    Send messages to the right LLM based on task_type.
    Returns the assistant's reply as a string.
    json_mode=True constrains the model to emit valid JSON (Ollama format=json /
    Grok response_format) — use for any call whose output gets json.loads()'d.
    """
    if task_type in GROK_TASK_TYPES:
        return _call_grok(messages, temperature, max_tokens, json_mode=json_mode)
    return _call_ollama(messages, temperature, max_tokens, json_mode=json_mode)


def _call_ollama(messages: list[dict], temperature: float, max_tokens: int,
                 json_mode: bool = False) -> str:
    payload = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
        # Qwen3 is a hybrid-thinking model: by default it spends part of its
        # num_predict budget on an invisible <think> pass before writing the
        # real answer. On long prompts (full listing + consultation context)
        # thinking can consume the entire budget and leave content empty —
        # observed in production as a listing with a blank title/description.
        # think=False skips reasoning and puts the full budget into the answer.
        "think": False,
        "options": {"temperature": temperature, "num_predict": max(max_tokens, 2000)},
    }
    if json_mode:
        payload["format"] = "json"
    resp = requests.post(f"{OLLAMA_BASE}/api/chat", json=payload, timeout=120)
    resp.raise_for_status()
    return resp.json()["message"]["content"]


def _call_grok(messages: list[dict], temperature: float, max_tokens: int, _attempt: int = 0,
               model: str = None, json_mode: bool = False, kind: str = "grok_chat") -> str:
    headers = {"Authorization": f"Bearer {XAI_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": model or XAI_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    try:
        resp = requests.post(f"{XAI_BASE}/chat/completions", headers=headers, json=payload, timeout=210)
        resp.raise_for_status()
        record_api_spend(kind)
        return resp.json()["choices"][0]["message"]["content"]
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 400 and json_mode:
            # Model/endpoint doesn't accept response_format — retry unconstrained
            return _call_grok(messages, temperature, max_tokens, _attempt, model=model,
                              json_mode=False, kind=kind)
        if _attempt < 2 and e.response is not None and e.response.status_code in (429, 500, 502, 503):
            time.sleep(3 * (_attempt + 1))
            return _call_grok(messages, temperature, max_tokens, _attempt + 1, model=model,
                              json_mode=json_mode, kind=kind)
        raise


def call_grok_vision(image_path: str, prompt: str, system: str = None,
                     temperature: float = 0.7, max_tokens: int = 800,
                     json_mode: bool = False) -> str:
    """
    Send a local image + prompt to Grok for visual analysis (xAI multimodal API).
    Used by the Artist and Reviewer so they can see the actual artwork.
    """
    suffix = Path(image_path).suffix.lower()
    media_type = "image/png" if suffix == ".png" else "image/jpeg"
    with open(image_path, "rb") as f:
        b64 = base64.standard_b64encode(f.read()).decode()

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({
        "role": "user",
        "content": [
            {"type": "image_url",
             "image_url": {"url": f"data:{media_type};base64,{b64}", "detail": "high"}},
            {"type": "text", "text": prompt},
        ],
    })
    return _call_grok(messages, temperature, max_tokens, model=XAI_VISION_MODEL,
                      json_mode=json_mode, kind="grok_vision")


def call_claude(messages: list[dict], system: str = None, max_tokens: int = 2048,
                _attempt: int = 0) -> str:
    """
    Claude Sonnet via the official Anthropic SDK — the Secretary's brain.
    Every call is metered as "claude_chat" (hard rule: her spend shows in the
    Steward report from day one). Sonnet 5 rejects temperature/top_p; thinking
    is disabled for chat so replies stay fast and the whole budget goes to the
    answer.
    """
    import anthropic  # lazy: pipelines that never use the Secretary don't need it

    if not ANTHROPIC_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY is not set — add it to .env to enable the Secretary")

    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    try:
        response = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=max_tokens,
            system=system or anthropic.NOT_GIVEN,
            thinking={"type": "disabled"},
            messages=messages,
        )
    except anthropic.RateLimitError:
        if _attempt < 2:
            time.sleep(3 * (_attempt + 1))
            return call_claude(messages, system=system, max_tokens=max_tokens,
                               _attempt=_attempt + 1)
        raise
    except anthropic.APIStatusError as e:
        if e.status_code >= 500 and _attempt < 2:
            time.sleep(3 * (_attempt + 1))
            return call_claude(messages, system=system, max_tokens=max_tokens,
                               _attempt=_attempt + 1)
        raise
    record_api_spend("claude_chat")
    if response.stop_reason == "refusal":
        return "I wasn't able to answer that one. Could you rephrase it for me?"
    return "".join(b.text for b in response.content if b.type == "text")


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
