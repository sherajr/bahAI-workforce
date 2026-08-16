"""
Which model each agent runs on.

The router has always chosen a model from the TASK TYPE (GROK_TASK_TYPES vs
local Ollama). This module adds a per-AGENT override on top of that, so Sheraj
can put Clara on a bigger local model or move Amos onto a newer Grok, from the
dashboard, without touching .env — owner ask, 2026-08-13.

Two things here are load-bearing rather than convenient:

  * **The provider boundary is enforced in CODE** (`validate_choice`), not by
    what the dropdown happens to list. Hard rule 16 reserves Claude for the
    Secretary; a workforce agent must be structurally unable to reach it, and
    she must be structurally unable to leave it.
  * **No stored choice means today's behaviour, byte-for-byte.** `resolve()`
    with no override returns exactly what the router would have picked on its
    own, so this feature is inert until Sheraj actually uses it.

Model lists are DISCOVERED from each provider rather than hardcoded, because a
hardcoded list goes stale silently and then offers models that no longer exist.
"""

import os
import time

import requests

from agents.router import (
    ANTHROPIC_KEY, GROK_TASK_TYPES, OLLAMA_BASE, OLLAMA_MODEL,
    XAI_BASE, XAI_KEY, XAI_MODEL,
)

OLLAMA = "ollama"
XAI = "xai"
ANTHROPIC = "anthropic"

# Which providers each agent may use. The Secretary is Claude-only and the
# workforce is Claude-never — rule 16, in code (see module docstring).
WORKFORCE_PROVIDERS = (OLLAMA, XAI)
SECRETARY_PROVIDERS = (ANTHROPIC,)


def allowed_providers(agent: str) -> tuple[str, ...]:
    return SECRETARY_PROVIDERS if agent == "secretary" else WORKFORCE_PROVIDERS


# Ollama models that are not chat models. nomic-embed-text backs
# router.get_embedding and the whole citation index — offering it as a chat
# model would let someone quietly break retrieval.
_EMBEDDING_HINTS = ("embed", "bge-", "e5-", "gte-")

# xAI ids that are not chat completions endpoints. The grok-imagine-* family is
# image and video generation; artist.generate_image reaches those through its
# own path and they would 404 a /chat/completions call.
_NON_CHAT_XAI = ("imagine",)

# Fallback Claude list, used only when the Anthropic models endpoint can't be
# reached. Kept short and current rather than exhaustive.
_CLAUDE_FALLBACK = [
    ("claude-opus-5", "Claude Opus 5"),
    ("claude-sonnet-5", "Claude Sonnet 5"),
    ("claude-haiku-4-5-20251001", "Claude Haiku 4.5"),
]

_CACHE: dict[str, tuple[float, list[dict], bool]] = {}
_CACHE_TTL_S = 120


def _cached(provider: str):
    hit = _CACHE.get(provider)
    if hit and (time.time() - hit[0]) < _CACHE_TTL_S:
        return hit[1], hit[2]
    return None


def _store(provider: str, models: list[dict], ok: bool):
    _CACHE[provider] = (time.time(), models, ok)


def _list_ollama() -> tuple[list[dict], bool]:
    """(models, discovery_succeeded). Never raises."""
    hit = _cached(OLLAMA)
    if hit:
        return hit
    models: list[dict] = []
    ok = False
    try:
        resp = requests.get(f"{OLLAMA_BASE}/api/tags", timeout=8)
        resp.raise_for_status()
        for m in resp.json().get("models", []):
            name = m.get("name") or ""
            if not name or any(h in name.lower() for h in _EMBEDDING_HINTS):
                continue
            size_gb = round((m.get("size") or 0) / 1e9, 1)
            models.append({
                "id": name,
                "provider": OLLAMA,
                "label": name,
                "paid": False,
                "note": f"{size_gb}GB local" if size_gb else "local",
            })
        ok = True
    except Exception:
        ok = False  # Ollama not running — say so rather than pretending it's empty
    models.sort(key=lambda m: m["id"])
    _store(OLLAMA, models, ok)
    return models, ok


def _list_xai() -> tuple[list[dict], bool]:
    hit = _cached(XAI)
    if hit:
        return hit
    models: list[dict] = []
    ok = False
    if XAI_KEY:
        try:
            resp = requests.get(f"{XAI_BASE}/models",
                                headers={"Authorization": f"Bearer {XAI_KEY}"}, timeout=12)
            resp.raise_for_status()
            for m in resp.json().get("data", []):
                mid = m.get("id") or ""
                if not mid or any(bad in mid.lower() for bad in _NON_CHAT_XAI):
                    continue
                models.append({
                    "id": mid, "provider": XAI, "label": mid,
                    "paid": True, "note": "paid API",
                })
            ok = True
        except Exception:
            ok = False
    models.sort(key=lambda m: m["id"])
    _store(XAI, models, ok)
    return models, ok


def _list_anthropic() -> tuple[list[dict], bool]:
    hit = _cached(ANTHROPIC)
    if hit:
        return hit
    models: list[dict] = []
    ok = False
    if ANTHROPIC_KEY:
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
            for m in client.models.list(limit=50).data:
                models.append({
                    "id": m.id, "provider": ANTHROPIC,
                    "label": getattr(m, "display_name", None) or m.id,
                    "paid": True, "note": "paid API",
                })
            ok = True
        except Exception:
            ok = False
    if not models:
        models = [{"id": mid, "provider": ANTHROPIC, "label": label,
                   "paid": True, "note": "paid API"} for mid, label in _CLAUDE_FALLBACK]
    models.sort(key=lambda m: m["id"])
    _store(ANTHROPIC, models, ok)
    return models, ok


_LISTERS = {OLLAMA: _list_ollama, XAI: _list_xai, ANTHROPIC: _list_anthropic}


def list_models(agent: str | None = None) -> dict:
    """
    The models on offer, plus whether each provider could actually be reached.

    `reachable` matters: an empty Ollama list because Ollama is DOWN is a very
    different fact from an empty list because nothing is installed, and
    resolve() depends on the difference (see _is_known_missing).
    """
    providers = allowed_providers(agent) if agent else (OLLAMA, XAI, ANTHROPIC)
    out: list[dict] = []
    reachable: dict[str, bool] = {}
    for p in providers:
        models, ok = _LISTERS[p]()
        out.extend(models)
        reachable[p] = ok
    return {"models": out, "reachable": reachable}


def provider_of(model_id: str) -> str | None:
    """Which provider offers this id, or None if nobody currently does."""
    for p, lister in _LISTERS.items():
        models, _ = lister()
        if any(m["id"] == model_id for m in models):
            return p
    return None


def validate_choice(agent: str, model_id: str) -> str:
    """
    Check a model choice for an agent and return its provider.

    Raises ValueError on anything that crosses the rule-16 boundary in either
    direction, or on a model no provider offers. Called by the settings
    endpoint, so an out-of-bounds choice can never reach storage — the UI's
    dropdown is a convenience, never the guarantee.
    """
    if not model_id:
        return ""
    provider = provider_of(model_id)
    allowed = allowed_providers(agent)
    if provider is None:
        raise ValueError(
            f"No provider currently offers a model called '{model_id}'. "
            "If it is a local model, pull it in Ollama first.")
    if provider not in allowed:
        if provider == ANTHROPIC:
            raise ValueError(
                f"'{model_id}' is a Claude model, and Claude is Abigail's alone — "
                f"{agent} cannot be moved onto it.")
        raise ValueError(
            f"Abigail runs on Claude only; '{model_id}' is a {provider} model.")
    return provider


def _is_known_missing(model_id: str, provider_hint: str | None = None) -> bool:
    """
    True only when we SUCCESSFULLY listed the providers and the id was in none
    of them. A provider we couldn't reach is not evidence of absence — treating
    a stopped Ollama as "that model is gone" would silently move every local
    agent onto a different model the moment the service blipped.
    """
    any_reachable = False
    for p, lister in _LISTERS.items():
        if provider_hint and p != provider_hint:
            continue
        models, ok = lister()
        if not ok:
            continue
        any_reachable = True
        if any(m["id"] == model_id for m in models):
            return False
    return any_reachable


def default_for(task_type: str) -> tuple[str, str]:
    """Exactly what the router would pick with no override — today's behaviour."""
    if task_type in GROK_TASK_TYPES:
        return XAI, XAI_MODEL
    return OLLAMA, OLLAMA_MODEL


def default_for_agent(agent: str, task_type: str) -> tuple[str, str]:
    """
    The agent's default with no override.

    The Secretary does not go through call_llm or GROK_TASK_TYPES at all — she
    runs on Anthropic via call_claude — so resolving her against a task type
    would report the local model as her default, which is simply false.
    """
    if agent == "secretary":
        from agents.router import ANTHROPIC_MODEL
        return ANTHROPIC, ANTHROPIC_MODEL
    return default_for(task_type)


def stored_model(agent: str | None) -> str:
    """The agent's saved choice, or "" for none. Never raises."""
    if not agent:
        return ""
    try:
        from agents.colony import get_agent_settings
        return (get_agent_settings(agent).get("model") or "").strip()
    except Exception:
        return ""


def resolve(task_type: str, agent: str | None = None) -> tuple[str, str, str]:
    """
    (provider, model_id, note) for this call.

    `note` is empty on the normal path and carries a plain-language explanation
    when a stored choice could NOT be honoured — the caller surfaces it rather
    than quietly substituting a different model, which is the kind of silent
    swap that makes output changes impossible to explain later.
    """
    chosen = stored_model(agent)
    if not chosen:
        provider, model = default_for(task_type)
        return provider, model, ""

    if _is_known_missing(chosen):
        provider, model = default_for(task_type)
        return provider, model, (
            f"{agent}'s chosen model '{chosen}' is no longer available, so this ran on "
            f"{model} instead. Pick a different model in the Colony tab.")

    provider = provider_of(chosen)
    if provider is None:
        # Unreachable providers: trust the stored id rather than overriding it.
        # Infer the provider from the id so the call still goes somewhere sane.
        provider = XAI if chosen.startswith("grok") else OLLAMA
    if provider not in allowed_providers(agent or ""):
        provider, model = default_for(task_type)
        return provider, model, (
            f"'{chosen}' is not a model {agent} is allowed to use, so this ran on {model}.")
    return provider, chosen, ""
