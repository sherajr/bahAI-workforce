"""
Routes each task type to the right LLM backend:
  local → Ollama (qwen3-16k, free, private) — everything by default
  grok  → xAI Grok API (paid, higher quality, vision) — task types in GROK_TASK_TYPES

Usage:
    result = call_llm("reviewer", messages=[...])
    result = call_llm("plan", messages=[...])
"""

import base64
import json
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
GROK_TASK_TYPES = {"creative_writing", "reviewer"}

# Opt-in escape hatch for the video Director. Its prompts are long by design
# and it makes one call per story beat, so on a busy 8GB card a full plan can
# take 10-15 minutes on local Qwen and is prone to timing out. Setting
# VIDEO_DIRECTOR_MODEL=grok routes ONLY the video planning stages to the paid
# API (metered like every other Grok call); the default stays local and free,
# and no other pipeline is affected either way.
if os.getenv("VIDEO_DIRECTOR_MODEL", "local").strip().lower() == "grok":
    GROK_TASK_TYPES = GROK_TASK_TYPES | {"video_direction"}

# Flat per-call cost estimates (USD) for the Steward's metered P&L. Rough but
# consistent — refine against real xAI invoices; the point is that repaint-heavy
# runs cost visibly more than clean ones (Moderation, principle 5).
EST_COST_USD = {"grok_chat": 0.005, "grok_vision": 0.01, "image_gen": 0.05,
                "claude_chat": 0.01}

# Appended verbatim to a call_claude_agentic reply that hit the max_tokens
# ceiling mid-generation, so a cut-off never masquerades as a complete answer
# (a truncated reply can end mid-intent-tag — the action then never runs).
TRUNCATION_NOTICE = ("\n\n(I ran out of room mid-reply there, so part of what I was "
                     "saying or doing may have been cut off — ask me to continue.)")


def record_api_spend(kind: str):
    """Meter one paid API call into the spend table. Never raises."""
    record_spend(kind, EST_COST_USD.get(kind, 0.0))


def _resolve_route(task_type: str, agent: str | None) -> tuple[str, str]:
    """
    (provider, model) for this call — the per-agent override if one is set,
    otherwise exactly what task_type alone would have chosen.

    Imported lazily and failing open: agents.models imports FROM this module,
    and a model registry that can't be read must never take a pipeline down
    with it. No override configured is the overwhelmingly common path and
    returns today's routing unchanged.
    """
    if not agent:
        return ("grok" if task_type in GROK_TASK_TYPES else "ollama",
                XAI_MODEL if task_type in GROK_TASK_TYPES else OLLAMA_MODEL)
    try:
        from agents.models import resolve
        provider, model, note = resolve(task_type, agent)
        if note:
            print(f"[router] {note}")
        return ("grok" if provider == "xai" else "ollama"), model
    except Exception:
        return ("grok" if task_type in GROK_TASK_TYPES else "ollama",
                XAI_MODEL if task_type in GROK_TASK_TYPES else OLLAMA_MODEL)


def call_llm(task_type: str, messages: list[dict], temperature: float = 0.7, max_tokens: int = 4096,
             json_mode: bool = False, timeout: int | None = None,
             agent: str | None = None) -> str:
    """
    Send messages to the right LLM based on task_type.
    Returns the assistant's reply as a string.
    json_mode=True constrains the model to emit valid JSON (Ollama format=json /
    Grok response_format) — use for any call whose output gets json.loads()'d.

    timeout (seconds) overrides the default for slow, deliberately long
    generations. The video Director's shot planning needs this: its prompts ask
    for several hundred words of detail and routinely run past the 120s default
    on a busy GPU, which surfaced as a read timeout mid-plan.

    agent (optional) is the agent making the call — "scribe", "reviewer", and
    so on. When that agent has a model saved in the Colony tab, it wins over
    the task_type default; otherwise routing is unchanged. Task types are
    shared between agents ("creative_writing" is both the Artist and the
    Translator), which is exactly why the agent has to be passed explicitly
    rather than reverse-derived from the task type.
    """
    provider, model = _resolve_route(task_type, agent)
    if provider == "grok":
        return _call_grok(messages, temperature, max_tokens, json_mode=json_mode,
                          timeout=timeout, model=model)
    return _call_ollama(messages, temperature, max_tokens, json_mode=json_mode,
                        timeout=timeout, model=model)


def _call_ollama(messages: list[dict], temperature: float, max_tokens: int,
                 json_mode: bool = False, timeout: int | None = None,
                 model: str | None = None) -> str:
    payload = {
        "model": model or OLLAMA_MODEL,
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
    resp = requests.post(f"{OLLAMA_BASE}/api/chat", json=payload, timeout=timeout or 120)
    resp.raise_for_status()
    return resp.json()["message"]["content"]


def _call_grok(messages: list[dict], temperature: float, max_tokens: int, _attempt: int = 0,
               model: str = None, json_mode: bool = False, kind: str = "grok_chat",
               timeout: int | None = None) -> str:
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
        resp = requests.post(f"{XAI_BASE}/chat/completions", headers=headers, json=payload,
                             timeout=timeout or 210)
        resp.raise_for_status()
        record_api_spend(kind)
        return resp.json()["choices"][0]["message"]["content"]
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 400 and json_mode:
            # Model/endpoint doesn't accept response_format — retry unconstrained
            return _call_grok(messages, temperature, max_tokens, _attempt, model=model,
                              json_mode=False, kind=kind, timeout=timeout)
        if _attempt < 2 and e.response is not None and e.response.status_code in (429, 500, 502, 503):
            time.sleep(3 * (_attempt + 1))
            return _call_grok(messages, temperature, max_tokens, _attempt + 1, model=model,
                              json_mode=json_mode, kind=kind, timeout=timeout)
        raise


def call_grok_vision(image_path: str | list[str], prompt: str, system: str = None,
                     temperature: float = 0.7, max_tokens: int = 800,
                     json_mode: bool = False) -> str:
    """
    Send one or more local images + a prompt to Grok for visual analysis
    (xAI multimodal API). Used by the Artist and Reviewer so they can see
    the actual artwork.

    image_path may be a single path (str) or an ordered list of paths. Each
    image becomes one content block, in order, before the text block.
    Multi-image callers should reference images by order in the prompt
    ("the FIRST image", "the SECOND image").
    """
    paths = [image_path] if isinstance(image_path, str) else list(image_path)
    content = []
    for path in paths:
        suffix = Path(path).suffix.lower()
        media_type = "image/png" if suffix == ".png" else "image/jpeg"
        with open(path, "rb") as f:
            b64 = base64.standard_b64encode(f.read()).decode()
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:{media_type};base64,{b64}", "detail": "high"},
        })
    content.append({"type": "text", "text": prompt})

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({
        "role": "user",
        "content": content,
    })
    return _call_grok(messages, temperature, max_tokens, model=XAI_VISION_MODEL,
                      json_mode=json_mode, kind="grok_vision")


def call_claude(messages: list[dict], system: str = None, max_tokens: int = 2048,
                _attempt: int = 0, model: str | None = None) -> str:
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
            model=model or ANTHROPIC_MODEL,
            max_tokens=max_tokens,
            system=system or anthropic.NOT_GIVEN,
            thinking={"type": "disabled"},
            messages=messages,
        )
    except anthropic.RateLimitError:
        if _attempt < 2:
            time.sleep(3 * (_attempt + 1))
            return call_claude(messages, system=system, max_tokens=max_tokens,
                               _attempt=_attempt + 1, model=model)
        raise
    except anthropic.APIStatusError as e:
        if e.status_code >= 500 and _attempt < 2:
            time.sleep(3 * (_attempt + 1))
            return call_claude(messages, system=system, max_tokens=max_tokens,
                               _attempt=_attempt + 1, model=model)
        raise
    record_api_spend("claude_chat")
    if response.stop_reason == "refusal":
        return "I wasn't able to answer that one. Could you rephrase it for me?"
    return "".join(b.text for b in response.content if b.type == "text")


def _claude_round(messages: list[dict], system: str, max_tokens: int, tools=None,
                  tool_choice=None, _attempt: int = 0, model: str | None = None):
    """
    One metered Claude API call, shared by call_claude_agentic's rounds.
    Same retry-with-backoff shape as call_claude (router.py:142-176) — kept
    separate so call_claude itself stays untouched.
    """
    import anthropic

    if not ANTHROPIC_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY is not set — add it to .env to enable the Secretary")

    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    kwargs = {
        "model": model or ANTHROPIC_MODEL,
        "max_tokens": max_tokens,
        "system": system or anthropic.NOT_GIVEN,
        "thinking": {"type": "disabled"},
        "messages": messages,
    }
    if tools:
        kwargs["tools"] = tools
    if tool_choice:
        kwargs["tool_choice"] = tool_choice
    try:
        response = client.messages.create(**kwargs)
    except anthropic.RateLimitError:
        if _attempt < 2:
            time.sleep(3 * (_attempt + 1))
            return _claude_round(messages, system, max_tokens, tools=tools,
                                 tool_choice=tool_choice, _attempt=_attempt + 1,
                                 model=model)
        raise
    except anthropic.APIStatusError as e:
        if e.status_code >= 500 and _attempt < 2:
            time.sleep(3 * (_attempt + 1))
            return _claude_round(messages, system, max_tokens, tools=tools,
                                 tool_choice=tool_choice, _attempt=_attempt + 1,
                                 model=model)
        raise
    record_api_spend("claude_chat")
    return response


def call_claude_agentic(messages: list[dict], system: str, tools: list[dict],
                        executor, max_tokens: int = 1500, max_rounds: int = 6,
                        model: str | None = None) -> str:
    """
    Multi-round tool-calling loop for the Secretary. Every action she takes —
    read OR write — is a real Claude tool call executed here (CLAUDE.md rule 22;
    migrated 2026-07-07 off the earlier design where writes were custom intent
    tags parsed out of her reply text). Each write tool's own handler in
    secretary_tools.make_executor enforces its ownership/approval gate, so the
    safety model lives in the executor, not in this loop. A manual loop rather
    than the SDK's beta tool-runner, so every round is individually metered as
    "claude_chat" (a 4-round tool conversation must show 4 spend entries, not
    one merged line) and a hard round cap always terminates the conversation.

    `executor(name, tool_input) -> str` runs a tool and returns its result as
    text; it must never raise — return an error string instead, which still
    becomes a normal (non-error) tool_result so the model can react to it in
    its own words rather than looping on a hard failure.

    `messages` is mutated with the tool back-and-forth for the duration of
    this call, but the caller's own history (secretary_store) never sees
    those intermediate turns — only the returned text does.

    The returned text is EVERY round's text concatenated, never just the
    final round's. The model interleaves prose — and, for the Secretary,
    intent tags — with its tool calls; returning only the last round
    silently discarded tags the model had already emitted (observed live:
    "she said she was appending rows but nothing landed in the sheet",
    while the model, seeing its own tag in the conversation history,
    reasonably believed it had acted).
    """
    working = list(messages)  # never mutate the caller's list in place
    collected: list[str] = []
    for round_num in range(max_rounds):
        forcing_final = round_num == max_rounds - 1
        response = _claude_round(
            working, system, max_tokens, tools=tools,
            tool_choice={"type": "none"} if forcing_final else None,
            model=model,
        )
        if response.stop_reason == "refusal":
            return "I wasn't able to answer that one. Could you rephrase it for me?"
        round_text = "".join(b.text for b in response.content if b.type == "text").strip()
        if round_text:
            collected.append(round_text)
        if response.stop_reason != "tool_use" or forcing_final:
            final = "\n\n".join(collected)
            if response.stop_reason == "max_tokens":
                final += TRUNCATION_NOTICE
            return final or ("I'm having trouble finishing that thought — "
                             "could you try asking again?")

        working.append({"role": "assistant", "content": response.content})
        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            try:
                result_text = executor(block.name, block.input)
            except Exception as e:
                result_text = f"Tool error ({type(e).__name__}): {e}"
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": str(result_text),
            })
        working.append({"role": "user", "content": tool_results})

    # Exhausted max_rounds without a final round producing text (shouldn't
    # happen given forcing_final above, but never leave the turn silent).
    return "\n\n".join(collected) or \
        "I'm having trouble finishing that thought — could you try asking again?"


def _grok_tool_round(messages: list[dict], tools: list[dict], temperature: float,
                     max_tokens: int, force_text: bool = False, _attempt: int = 0,
                     model: str | None = None) -> dict:
    """
    One metered xAI call that may return tool calls. Returns the raw assistant
    message dict (OpenAI-compatible shape: {role, content, tool_calls}).
    """
    headers = {"Authorization": f"Bearer {XAI_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": model or XAI_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "tools": tools,
        "tool_choice": "none" if force_text else "auto",
    }
    try:
        resp = requests.post(f"{XAI_BASE}/chat/completions", headers=headers, json=payload,
                             timeout=210)
        resp.raise_for_status()
    except requests.HTTPError as e:
        if _attempt < 2 and e.response is not None and \
                e.response.status_code in (429, 500, 502, 503):
            time.sleep(3 * (_attempt + 1))
            return _grok_tool_round(messages, tools, temperature, max_tokens,
                                    force_text=force_text, _attempt=_attempt + 1,
                                    model=model)
        raise
    record_api_spend("grok_chat")
    return resp.json()["choices"][0]["message"]


def _ollama_tool_round(messages: list[dict], tools: list[dict], temperature: float,
                       max_tokens: int, force_text: bool = False,
                       timeout: int | None = None, model: str | None = None) -> dict:
    """
    One local Ollama call that may return tool calls. Returns the raw assistant
    message dict (Ollama shape: {role, content, tool_calls}).

    force_text drops the tools from the request entirely — Ollama has no
    tool_choice parameter, and a model with no tools available cannot call one,
    which is exactly what the final forced round needs.
    """
    payload = {
        "model": model or OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
        "think": False,  # same reason as _call_ollama — see its comment
        "options": {"temperature": temperature, "num_predict": max(max_tokens, 2000)},
    }
    if tools and not force_text:
        payload["tools"] = tools
    resp = requests.post(f"{OLLAMA_BASE}/api/chat", json=payload, timeout=timeout or 180)
    resp.raise_for_status()
    return resp.json()["message"]


def _normalize_tool_calls(message: dict) -> list[dict]:
    """
    Flatten either provider's tool-call shape into [{id, name, arguments}].

    Grok returns arguments as a JSON STRING; Ollama returns them as an already
    decoded dict. Normalising here means the executor contract is identical for
    both, so a tool works the same whether the agent runs local or paid.
    """
    calls = []
    for i, call in enumerate(message.get("tool_calls") or []):
        fn = call.get("function", {})
        raw_args = fn.get("arguments", {})
        if isinstance(raw_args, str):
            try:
                args = json.loads(raw_args) if raw_args.strip() else {}
            except json.JSONDecodeError:
                args = {"_raw": raw_args}
        else:
            args = raw_args or {}
        calls.append({
            "id": call.get("id") or f"call_{i}",
            "name": fn.get("name", ""),
            "arguments": args if isinstance(args, dict) else {"_raw": args},
        })
    return calls


def call_llm_agentic(task_type: str, messages: list[dict], system: str, tools: list[dict],
                     executor, temperature: float = 0.7, max_tokens: int = 1200,
                     max_rounds: int = 4, timeout: int | None = None,
                     agent: str | None = None) -> str:
    """
    Multi-round tool-calling loop for the WORKFORCE agents (the Colony tab's
    per-agent chat), routed by task_type exactly like call_llm — Grok for
    GROK_TASK_TYPES, local Ollama for everything else.

    This exists because hard rule 16 reserves Claude for the Secretary alone:
    Ruth, Theo, Clara, Amos, Nora and Sofia cannot borrow call_claude_agentic,
    so they need their own loop on their own models. It deliberately mirrors
    that function's contract — same executor signature, same hard round cap,
    same forced-final-round termination, same every-round-metered rule (a
    3-round tool conversation must show 3 spend entries, not one) — and the
    same reason for real tool calls over parsed text tags (rule 22).

    Differences forced by the providers, not by preference:
      - `tools` are OpenAI-style function schemas (both xAI and Ollama take
        this shape), not Anthropic's.
      - max_rounds defaults LOWER than the Secretary's 6. Local Qwen holds a
        much tighter context than Sonnet (rule 1) and every round appends
        another tool result to it; 4 keeps a chat turn inside its budget.

    `executor(name, arguments) -> str` must never raise — return an error
    string instead, which comes back as an ordinary tool result the model can
    respond to in its own words.
    """
    # Same per-agent override as call_llm — an agent chatting in the Colony
    # must run on the model it was assigned, not on the task-type default.
    provider, model = _resolve_route(task_type, agent)
    use_grok = provider == "grok"
    working = [{"role": "system", "content": system}] + list(messages)
    collected: list[str] = []

    for round_num in range(max_rounds):
        forcing_final = round_num == max_rounds - 1
        if use_grok:
            reply = _grok_tool_round(working, tools, temperature, max_tokens,
                                     force_text=forcing_final, model=model)
        else:
            reply = _ollama_tool_round(working, tools, temperature, max_tokens,
                                       force_text=forcing_final, timeout=timeout,
                                       model=model)

        text = (reply.get("content") or "").strip()
        if text:
            collected.append(text)
        calls = _normalize_tool_calls(reply)
        if not calls or forcing_final:
            return "\n\n".join(collected) or (
                "I'm having trouble finishing that thought — could you ask me again?")

        # Echo the assistant turn back verbatim so the model sees its own calls.
        working.append(reply)
        for call in calls:
            try:
                result_text = executor(call["name"], call["arguments"])
            except Exception as e:  # an executor that raises must not kill the turn
                result_text = f"Tool error ({type(e).__name__}): {e}"
            result_msg = {"role": "tool", "content": str(result_text)}
            if use_grok:
                # xAI matches results to calls by id; Ollama matches by order.
                result_msg["tool_call_id"] = call["id"]
            result_msg["name"] = call["name"]
            working.append(result_msg)

    return "\n\n".join(collected) or \
        "I'm having trouble finishing that thought — could you ask me again?"


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
    reply = call_llm("reviewer", [{"role": "user", "content": "Say 'Grok OK' and nothing else."}], max_tokens=20)
    print(f"Grok: {reply.strip()}")
