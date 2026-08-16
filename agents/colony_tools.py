"""
The tools each workforce agent can actually use when Sheraj chats with it in
the Colony tab.

Design, mirroring secretary_tools.py (rule 22): every action is a REAL tool
call executed here, never an intent tag parsed out of reply text. The safety
gate lives in the handler, not in the prompt — the model cannot talk its way
past it.

The gate (owner decision, 2026-08-13): a tool that only READS free, local data
runs immediately; a tool that SPENDS MONEY or CHANGES A SAVED PRODUCT queues a
row in colony_actions and does nothing until Sheraj approves it from the
dashboard. Same unified queue and same spirit as the Secretary's
pending_actions (rules 20/24/25) — the difference between the two systems is
what counts as free, not how approval works.

Toolsets are deliberately TINY (two or three per agent). Every non-Grok agent
runs on local Qwen, and hard rule 1 exists because long prompts made Qwen burn
its whole token budget and return "{" — a big tool schema is a long prompt.
"""

import json

from agents import colony

# Kinds that never run without approval. Kept as data (not scattered `if`s) so
# the test suite can assert the whole paid/writing surface is gated.
GATED_KINDS = {
    "generate_image",     # paid xAI image generation
    "score_product",      # paid Grok vision review of a saved product
    "translate_quote",    # paid Grok translation
    # wallet_send is deliberately NOT listed here. It is not a simple
    # "queue everything" tool: it has its own TIERED gate in _h_wallet_send —
    # under the auto-send limit it goes straight through (owner decision,
    # 2026-08-14), above it queues for approval, and over the hard cap or off
    # the allowlist it is refused outright. Listing it here would silently turn
    # every payment into an approval and remove the autonomy Sheraj asked for.
}

# Kinds that move real money. Kept separate so the test suite can assert that
# nothing reaches them except the paths intended to.
MONEY_KINDS = {"wallet_send"}


# ── Tool schemas (OpenAI-style; both xAI and Ollama accept this shape) ─────────

def _tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required},
        },
    }


_SEARCH_LIBRARY = _tool(
    "search_library",
    "Search the indexed Bahá'í texts for passages relevant to a topic. "
    "Returns real passages with their sources — never invent a quote instead.",
    {"query": {"type": "string", "description": "What to search for, e.g. 'unity of mankind'"},
     "ruhi_only": {"type": "boolean",
                   "description": "true to search only Ruhi Book 1 (the quote-card source)"}},
    ["query"])

_VERIFY_QUOTE = _tool(
    "verify_quote",
    "Check a quotation against the indexed texts and report whether it is accurate "
    "and correctly attributed.",
    {"text": {"type": "string", "description": "The exact quotation to check"}},
    ["text"])

_LIST_SOURCES = _tool(
    "list_sources",
    "List the Bahá'í texts currently in the library index.",
    {}, [])

_DRAFT_IMAGE_PROMPT = _tool(
    "draft_image_prompt",
    "Compose (but do not generate) an image prompt for a theme, in the house style. Free.",
    {"theme": {"type": "string", "description": "The theme or subject"},
     "for_card": {"type": "boolean", "description": "true for a quote card, false for a bookmark"}},
    ["theme"])

_GENERATE_IMAGE = _tool(
    "generate_image",
    "Actually generate artwork from a prompt. This costs money, so it is queued "
    "for Sheraj's approval rather than run immediately.",
    {"prompt": {"type": "string", "description": "The full image prompt"},
     "aspect_ratio": {"type": "string", "description": "e.g. '2:3' for a bookmark"}},
    ["prompt"])

_LIST_PRODUCTS = _tool(
    "list_products",
    "List recently created products (bookmarks and quote cards) with their scores.",
    {"limit": {"type": "integer", "description": "How many to list, default 10"},
     "product_type": {"type": "string", "description": "'bookmark' or 'quote_card'"}},
    [])

_SCORE_PRODUCT = _tool(
    "score_product",
    "Run a full constitutional review of a saved product. This costs money, so it is "
    "queued for Sheraj's approval rather than run immediately.",
    {"product_id": {"type": "string", "description": "The product's id"}},
    ["product_id"])

_SPEND_REPORT = _tool(
    "spend_report",
    "The metered API spend: all-time, this month, and the breakdown by kind.",
    {}, [])

_DEEDS_REPORT = _tool(
    "deeds_report",
    "The giving ledger — how many cards and bookmarks have been given away.",
    {}, [])

_TRUST_REPORT = _tool(
    "trust_report",
    "Every agent's trust level, clean-run rate and total judged runs.",
    {}, [])

_TRANSLATE_QUOTE = _tool(
    "translate_quote",
    "Translate a quotation into another language for a card. This costs money, so it is "
    "queued for Sheraj's approval rather than run immediately.",
    {"quote": {"type": "string", "description": "The English quotation"},
     "language": {"type": "string", "description": "Language code, e.g. 'es', 'ar', 'zh'"}},
    ["quote", "language"])

_LIST_VIDEO_PROJECTS = _tool(
    "list_video_projects",
    "List the video projects with their status and shot counts.",
    {}, [])

_WALLET_BALANCES = _tool(
    "wallet_balances",
    "The project wallet's holdings across every chain, plus the treasury addresses. "
    "Read-only.",
    {}, [])

_WALLET_SEND = _tool(
    "wallet_send",
    "Send USDC from the project wallet to one of the APPROVED addresses. Only "
    "approved addresses are possible, there are hard limits, and larger amounts "
    "need Sheraj's approval before they go.",
    {"to": {"type": "string",
            "description": "The approved address, or the name it was saved under"},
     "amount": {"type": "string", "description": "Amount in USDC, e.g. '2.50'"},
     "chain": {"type": "string", "description": "Which network, e.g. 'base'"},
     "note": {"type": "string", "description": "What the payment is for"}},
    ["to", "amount"])


# agent id → its tools. Small on purpose (rule 1).
AGENT_TOOLS: dict[str, list[dict]] = {
    "librarian":   [_SEARCH_LIBRARY, _VERIFY_QUOTE, _LIST_SOURCES],
    "artist":      [_DRAFT_IMAGE_PROMPT, _GENERATE_IMAGE, _LIST_PRODUCTS],
    "scribe":      [_LIST_PRODUCTS, _SEARCH_LIBRARY],
    "reviewer":    [_LIST_PRODUCTS, _SCORE_PRODUCT],
    # The Steward carries two extra tools because the wallet is her domain.
    # Still lean for local Qwen (rule 1) — both schemas are small.
    "steward":     [_SPEND_REPORT, _DEEDS_REPORT, _TRUST_REPORT,
                    _WALLET_BALANCES, _WALLET_SEND],
    "translator":  [_TRANSLATE_QUOTE, _LIST_PRODUCTS],
    "director":    [_LIST_VIDEO_PROJECTS],
    "videographer": [_LIST_VIDEO_PROJECTS],
}


def tools_for(agent: str) -> list[dict]:
    return AGENT_TOOLS.get(agent, [])


# ── Immediate (free, read-only) handlers ──────────────────────────────────────

def _h_search_library(args: dict) -> str:
    from agents.librarian import format_citation, retrieve, retrieve_ruhi_book1
    query = str(args.get("query", "")).strip()
    if not query:
        return "No query given — say what to search for."
    passages = (retrieve_ruhi_book1(query, n_results=3) if args.get("ruhi_only")
                else retrieve(query, n_results=3))
    if not passages:
        return ("Nothing found in the index for that. The index may not be built, or the "
                "topic may not appear in the indexed texts. Do not substitute a remembered "
                "quote — say plainly that nothing was found.")
    return "\n\n".join(format_citation(p) for p in passages)


def _h_verify_quote(args: dict) -> str:
    from agents.librarian import verify
    text = str(args.get("text", "")).strip()
    if not text:
        return "No text given to check."
    result = verify(text)
    lines = [f"Verified: {result.get('verified')}"]
    for issue in result.get("issues", []):
        lines.append(f"Issue: {issue}")
    for p in result.get("supporting_passages", [])[:2]:
        lines.append(f"Supporting: {p.get('source', '?')} — {(p.get('text') or '')[:200]}")
    return "\n".join(lines)


def _h_list_sources(args: dict) -> str:
    from agents.librarian import list_library_sources
    sources = list_library_sources()
    if not sources:
        return "The library index has no texts in it yet."
    return "\n".join(f"- {s['name']} ({s['slug']})" for s in sources)


def _h_draft_image_prompt(args: dict) -> str:
    from agents.artist import build_card_image_prompt, build_image_prompt
    theme = str(args.get("theme", "")).strip()
    if not theme:
        return "No theme given."
    builder = build_card_image_prompt if args.get("for_card") else build_image_prompt
    return builder(theme)


def _h_list_products(args: dict) -> str:
    from agents.state import get_all_products
    limit = int(args.get("limit") or 10)
    wanted = args.get("product_type")
    rows = get_all_products()
    if wanted:
        rows = [r for r in rows if (r.get("product_type") or "bookmark") == wanted]
    rows = rows[:limit]
    if not rows:
        return "No products saved yet."
    out = []
    for r in rows:
        scores = r.get("reviewer_scores")
        try:
            overall = json.loads(scores).get("overall") if isinstance(scores, str) and scores else None
        except json.JSONDecodeError:
            overall = None
        out.append(f"[{r.get('id')}] {r.get('title')} "
                   f"({r.get('product_type') or 'bookmark'}"
                   + (f", scored {overall}" if overall is not None else ", unscored") + ")")
    return "\n".join(out)


def _h_spend_report(args: dict) -> str:
    from agents.state import get_spend_summary
    s = get_spend_summary()
    if s.get("error"):
        return f"The spend ledger could not be read: {s['error']}"
    kinds = ", ".join(f"{k}: ${v}" for k, v in sorted(s["by_kind"].items())) or "nothing yet"
    return f"All-time ${s['total']}; this month ${s['month']}. By kind — {kinds}."


def _h_deeds_report(args: dict) -> str:
    from agents.state import get_deeds_summary
    return json.dumps(get_deeds_summary(), ensure_ascii=False)[:1200]


def _h_trust_report(args: dict) -> str:
    from agents.state import TRUST_LEVELS, get_all_agent_statuses
    lines = []
    for s in get_all_agent_statuses():
        if s["total_runs"] == 0:
            continue
        lines.append(f"{s['name']}: {TRUST_LEVELS.get(s['trust_level'], '?')} "
                     f"({s['trust_score']:.0f}% clean over {s['total_runs']} judged runs)")
    return "\n".join(lines) or "No agent has completed a judged run yet."


def _h_list_video_projects(args: dict) -> str:
    from agents.video_store import list_projects
    projects = list_projects()
    if not projects:
        return "No video projects yet."
    return "\n".join(f"[{p.get('id')}] {p.get('title')} — {p.get('status')}"
                     for p in projects[:10])


def _h_wallet_balances(args: dict) -> str:
    from agents import wallet
    try:
        held = wallet.balances()
    except wallet.WalletError as e:
        return f"Could not read the wallet: {e}"
    if not held.get("address"):
        return "No project wallet has been created yet."
    lines = [f"Project wallet {held['address']} — {held['total_usdc']} USDC of REAL money."]
    if held.get("total_testnet_usdc", "0.00") != "0.00":
        lines.append(f"  (plus {held['total_testnet_usdc']} USDC of test-network play "
                     "money, which is NOT real and must never be reported as holdings)")
    for row in held["chains"]:
        if row["reachable"]:
            lines.append(f"  {row['name']}: {row['usdc']} USDC, "
                         f"{row['native']} {row['native_symbol']}")
        else:
            # An unreachable chain is NOT zero. Saying so keeps the report honest.
            lines.append(f"  {row['name']}: could not be reached — balance unknown, "
                         "not zero.")
    for t in wallet.list_treasury():
        lines.append(f"  Treasury (watch-only, you cannot spend it): "
                     f"{t['label']} {t['address']}")
    limits = wallet.status()["limits"]
    lines.append(f"Limits: ${limits['auto_send_usdc']} per payment without asking, "
                 f"${limits['max_per_tx_usdc']} maximum, "
                 f"${limits['daily_cap_usdc']} a day "
                 f"(${limits['spent_today_usdc']} already sent today).")
    return "\n".join(lines)


def _resolve_recipient(to: str) -> dict | None:
    """Accept either a raw address or the label it was saved under."""
    from agents import wallet
    if wallet.is_address(to):
        return wallet.allowlisted(to)
    wanted = (to or "").strip().lower()
    for entry in wallet.list_allowlist():
        if entry["label"].strip().lower() == wanted:
            return entry
    return None


def _h_wallet_send(agent: str, args: dict, effects: dict) -> str:
    """
    The tiered money gate. Every branch is decided in code, from the on-chain
    ledger and the owner-managed allowlist — never from anything the model
    asserts about the amount, the recipient or what it already spent.
    """
    from decimal import Decimal, InvalidOperation

    from agents import colony, wallet

    to_raw = str(args.get("to", "")).strip()
    entry = _resolve_recipient(to_raw)
    if not entry:
        known = ", ".join(e["label"] for e in wallet.list_allowlist()) or "none yet"
        return (f"'{to_raw}' is not an approved address, so nothing was sent. Only "
                f"Sheraj can approve addresses, in the Treasury tab. Approved: {known}.")
    try:
        amount = Decimal(str(args.get("amount", "")).replace("$", "").strip())
    except (InvalidOperation, ValueError):
        return f"'{args.get('amount')}' is not an amount I can read. Nothing was sent."

    chain = str(args.get("chain") or wallet.DEFAULT_CHAIN).strip()
    try:
        wallet.get_chain(chain)
    except wallet.WalletError as e:
        return f"{e} Nothing was sent."

    verdict = wallet.check_limits(amount, entry["address"])
    note = str(args.get("note", ""))[:200]

    if verdict["decision"] == "refused":
        return f"Refused: {verdict['reason']} Nothing was sent."

    if verdict["decision"] == "approval":
        action_id = colony.queue_action(
            agent, "wallet_send",
            f"Send {amount} USDC to {entry['label']} ({entry['address'][:10]}…) "
            f"on {wallet.CHAINS[chain]['name']}" + (f" — {note}" if note else ""),
            {"chain": chain, "to": entry["address"], "amount": str(amount), "note": note})
        effects.setdefault("queued", []).append(
            {"id": action_id, "description": f"Send {amount} USDC to {entry['label']}"})
        return (f"Queued for Sheraj's approval as action #{action_id}: {verdict['reason']} "
                "NOTHING has been sent yet — tell him it is waiting.")

    try:
        result = wallet.send_usdc(chain, entry["address"], amount,
                                  initiated_by=agent, note=note)
    except wallet.WalletError as e:
        return f"The payment did not go through: {e}"
    effects.setdefault("sent", []).append(result)
    return (f"Sent {amount} USDC to {entry['label']} on {wallet.CHAINS[chain]['name']}. "
            f"Transaction {result['tx_hash']}.")


IMMEDIATE_HANDLERS = {
    "search_library": _h_search_library,
    "wallet_balances": _h_wallet_balances,
    "verify_quote": _h_verify_quote,
    "list_sources": _h_list_sources,
    "draft_image_prompt": _h_draft_image_prompt,
    "list_products": _h_list_products,
    "spend_report": _h_spend_report,
    "deeds_report": _h_deeds_report,
    "trust_report": _h_trust_report,
    "list_video_projects": _h_list_video_projects,
}


# ── Gated handlers: describe the action, queue it, do nothing else ────────────

def _describe_gated(kind: str, args: dict) -> str:
    if kind == "generate_image":
        prompt = str(args.get("prompt", "")).strip()
        return f"Generate artwork (paid) from: \"{prompt[:160]}\""
    if kind == "score_product":
        return f"Run a full paid review of product {args.get('product_id')}"
    if kind == "translate_quote":
        return (f"Translate into '{args.get('language')}' (paid): "
                f"\"{str(args.get('quote', ''))[:120]}\"")
    return f"{kind} {json.dumps(args, ensure_ascii=False)[:160]}"


def _queue_gated(agent: str, kind: str, args: dict, effects: dict) -> str:
    description = _describe_gated(kind, args)
    action_id = colony.queue_action(agent, kind, description, args)
    effects.setdefault("queued", []).append({"id": action_id, "description": description})
    return (f"Queued for Sheraj's approval as action #{action_id}: {description}. "
            "It has NOT run yet — tell him it is waiting for his approval, and do not "
            "claim the work is done.")


# ── Executor ──────────────────────────────────────────────────────────────────

def make_executor(agent: str, effects: dict):
    """
    Build the executor `call_llm_agentic` calls. Same contract as
    secretary_tools.make_executor: it never raises — an error comes back as an
    ordinary tool result the model can respond to in words.

    The identical-call dedup guard is carried over from the Secretary for the
    same reason (rule 22): a model that restates a call in a later round must
    not perform the action twice — which for a gated tool would mean two
    approval rows for one intent.
    """
    seen: set[tuple[str, str]] = set()

    def execute(name: str, args: dict) -> str:
        args = args if isinstance(args, dict) else {}
        if name not in tools_for_names(agent):
            return (f"'{name}' is not one of your tools. Yours are: "
                    f"{', '.join(tools_for_names(agent)) or 'none'}.")
        signature = (name, json.dumps(args, sort_keys=True, ensure_ascii=False, default=str))
        if signature in seen:
            return "Already done in this turn — reuse that result rather than repeating it."
        seen.add(signature)

        if name in GATED_KINDS:
            return _queue_gated(agent, name, args, effects)
        if name in MONEY_KINDS:
            # Its own tiered gate (allowlist → caps → auto/queue/refuse), and it
            # needs `agent` and `effects` that the plain handlers don't take.
            try:
                return _h_wallet_send(agent, args, effects)
            except Exception as e:
                return f"The payment did not go through: {type(e).__name__}: {e}"
        handler = IMMEDIATE_HANDLERS.get(name)
        if not handler:
            return f"'{name}' has no handler."
        try:
            result = handler(args)
        except Exception as e:
            return f"That didn't work: {type(e).__name__}: {e}"
        effects.setdefault("used", []).append(name)
        return str(result)[:4000]

    return execute


def tools_for_names(agent: str) -> list[str]:
    return [t["function"]["name"] for t in tools_for(agent)]


# ── Executing an approved action ──────────────────────────────────────────────

def run_approved_action(action: dict) -> str:
    """
    Perform an action Sheraj approved. Called only from the approval endpoint —
    never from the chat loop, mirroring secretary.execute_pending_action.
    """
    kind = action["kind"]
    payload = json.loads(action["payload"])

    if kind == "generate_image":
        from agents.artist import generate_image
        result = generate_image(payload["prompt"],
                                aspect_ratio=payload.get("aspect_ratio") or "2:3")
        return f"Artwork generated: {result.get('image_url')}"

    if kind == "translate_quote":
        from agents.translator import translate_quote
        # translate_quote returns the text under "text" (with "translation"
        # being the key only inside its own internal JSON parse) — reading the
        # wrong one returned a confident, empty "Translated:" line.
        result = translate_quote(payload["quote"], payload["language"])
        # Rule 8: a translation is never shown without its code-owned
        # AI-assisted label, in chat exactly as on a printed card.
        return (f"{result.get('name', payload['language'])}: {result.get('text', '')[:400]}\n\n"
                f"{result.get('disclaimer_en', '')}")

    if kind == "score_product":
        return _run_score_product(payload["product_id"])

    if kind == "wallet_send":
        from agents import wallet
        # bypass_limits: the caps were checked when this was QUEUED, and Sheraj
        # has since approved it explicitly. Re-checking would refuse a payment
        # he authorised (e.g. the daily total moved on since). The allowlist and
        # the token-contract verification are NOT bypassed — those hold always.
        result = wallet.send_usdc(
            payload["chain"], payload["to"], payload["amount"],
            initiated_by="sheraj-approved", note=payload.get("note", ""),
            bypass_limits=True)
        return (f"Sent {result['amount']} USDC to {result['to_label']}. "
                f"Transaction {result['tx_hash']}")

    raise ValueError(f"Unknown action kind '{kind}'")


def _run_score_product(product_id: str) -> str:
    """
    Re-score a saved product through the real Reviewer.

    This DOES move the Reviewer's trust, and correctly so: a review verdict is
    exactly the judged outcome rule 14 says may pass passed_review. It does not
    overwrite the product's stored scores — a chat-initiated second opinion is
    not the pipeline's verdict, and silently replacing the shipped score would
    make the product's history disagree with the run that produced it.
    """
    import json as _json

    from agents.reviewer import score, score_quote_card
    from agents.state import get_all_products, log_run

    product = next((p for p in get_all_products() if p.get("id") == product_id), None)
    if not product:
        return f"No product with id {product_id}."

    listing_raw = product.get("listing_copy")
    copy = _json.loads(listing_raw) if isinstance(listing_raw, str) and listing_raw else {}
    theme = product.get("theme") or product.get("title") or ""
    prev = _json.loads(product.get("reviewer_scores") or "{}") or None

    if (product.get("product_type") or "bookmark") == "quote_card":
        # The card rubric scores the RENDERED faces, not the raw artwork
        # (reviewer.score_quote_card's own contract) — pass both stored faces.
        # The stored card copy flattens the translation across several fields;
        # rebuild the dict shape the Reviewer reads, and only when there really
        # is a translation (an empty one would invite it to score a blank).
        translation = None
        if copy.get("translation_text"):
            translation = {
                "code": copy.get("language"),
                "name": copy.get("language_name") or "",
                "text": copy.get("translation_text") or "",
                "disclaimer_native": copy.get("translation_disclaimer_native") or "",
                "disclaimer_en": copy.get("translation_disclaimer_en") or "",
            }
        review = score_quote_card(
            theme,
            copy.get("quote", ""),
            copy.get("citation", ""),
            bool(copy.get("quote_grounded")),
            front_image_path=product.get("front_image"),
            back_image_path=product.get("back_image"),
            translation=translation,
            previous_review=prev,
            # Rule 11: a web-tier quote is never presented as verified.
            quote_web_unverified=str(copy.get("quote_provenance") or "").startswith("web:"),
        )
    else:
        review = score(theme, product.get("image_prompt") or "", copy,
                       image_path=product.get("image_url"), previous_review=prev)
    overall = review.get("overall", 0.0)
    log_run(product.get("task_id") or product_id, "reviewer", "colony_second_opinion",
            f"re-score {product_id}", f"overall {overall}",
            passed_review=bool(review.get("passed")), reviewer_scores=review)
    return (f"Second opinion on \"{product.get('title')}\": {overall}/10, "
            f"{'passed' if review.get('passed') else 'did not pass'}. "
            "(The product's stored score is unchanged — this is a second opinion, "
            "not a re-issue of the pipeline's verdict.)")
