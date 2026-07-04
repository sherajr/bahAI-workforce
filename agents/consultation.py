"""
Consultation — multi-agent dialogue about generated artwork before the listing is written.

Flow (two rounds):
  Round 1 — Exploration
    Artist  → views the image with Grok vision (xAI), describes spiritual elements and mood
    Scribe  → reads the Artist's description, proposes quote directions and listing tone
    Reviewer → reads both, names what's at stake and raises its strongest challenge to the
               direction (no scoring yet — differing opinions strike the spark of truth)
    Librarian → verifies quotes against retrieved citations or known Bahá'í texts

  Round 2 — Convergence (builds on Round 1)
    Artist  → answers the Reviewer's challenge, refines focus to the most relevant element
    Scribe  → responds to the challenge, commits to a single quote and framing (no more "options")
    Reviewer → checks convergence and whether its challenge was engaged; green-light or hold
    Librarian → finalises the verified quote with the refined direction in mind

The full transcript from both rounds is returned.
The Round 2 context (which contains Round 1) is passed to the Scribe when writing the listing.
"""

import json
import re
from pathlib import Path
from dotenv import load_dotenv

from agents.router import call_llm, call_grok_vision

load_dotenv(dotenv_path=str(Path(__file__).parent.parent / ".env"))

# Hand-curated excerpts from "Consultation: A Compilation" (Universal House of
# Justice, Research Department, Feb. 1978 / rev. Nov. 1990), each tagged to the
# specific consultation moment it grounds. Deliberately NOT a vector index: the
# compilation is small and stable (46 passages) and our consultation has a
# small, fixed set of structural moments — matching moment to passage is an
# editorial judgment, not a search problem, and a hand-picked citation can't
# drift to a topically-similar-but-pedagogically-wrong passage the way a
# semantic-similarity retrieval could. Each excerpt is under ~40 words to
# respect Qwen's context budget — the Scribe and Librarian consultation turns
# route to the local model (see router.py's GROK_TASK_TYPES), which degrades
# on long prompts.
CONSULTATION_SCRIPTURE = {
    "round1_challenge": (
        "\"The shining spark of truth cometh forth only after the clash of "
        "differing opinions.\" — 'Abdu'l-Bahá"
    ),
    "round1_tone": (
        "\"Every member expresseth with absolute freedom his own opinion... "
        "should any one oppose, he must on no account feel hurt.\" — 'Abdu'l-Bahá"
    ),
    "round2_response": (
        "\"If he finds that a previously expressed opinion is more true and "
        "worthy, he should accept it immediately and not willfully hold to an "
        "opinion of his own.\" — 'Abdu'l-Bahá"
    ),
    "round2_verification": (
        "\"Consultation bestoweth greater awareness and transmuteth conjecture "
        "into certitude.\" — Bahá'u'lláh"
    ),
    "round2_decision": (
        "\"...record their vote and abide by the voice of the majority... "
        "never to be challenged, and always to be whole-heartedly enforced.\" "
        "— Shoghi Effendi"
    ),
    "framing_contribution": (
        "\"He who expresses an opinion should not voice it as correct and "
        "right but set it forth as a contribution to the consensus of "
        "opinion.\" — 'Abdu'l-Bahá"
    ),
}


def _parse_verdict_grounded(librarian_msg: str) -> bool:
    """True only if the Librarian's own VERDICT line says GROUNDED IN SOURCES."""
    m = re.search(r'VERDICT:\s*(.+)', librarian_msg, re.IGNORECASE)
    return bool(m) and "grounded" in m.group(1).lower()


def _normalize_quote(quote: str) -> str:
    """
    Clean up a Librarian-extracted quote:
    - Strip surrounding quotes and whitespace
    - Convert slash line-separators to real newlines
    - Capitalize the first letter of each line (preserve the rest for proper nouns)
    """
    quote = quote.strip().strip('"').strip("'").strip()
    # Convert " / " or "/" separators to newlines
    parts = re.split(r'\s*/\s*', quote)
    lines = []
    for part in parts:
        part = part.strip().strip('"').strip("'").strip()
        if part:
            lines.append(part[0].upper() + part[1:] if part else part)
    return '\n'.join(lines)


def _run_round(
    image_path: str,
    theme: str,
    image_prompt: str,
    citations: list,
    progress=None,
    on_turn=None,
    round_number: int = 1,
    prior_context: str = "",
    prior_artist_observation: str = "",
) -> dict:
    """
    Run one four-turn consultation round.
    Round 2 receives prior_context (Round 1's full context) and prior_artist_observation
    so the Artist doesn't need to re-describe the image from scratch.
    on_turn(entry), if given, fires immediately after each agent's turn completes —
    this is what lets the dashboard render the consultation as a live chat instead of
    only showing the transcript once the whole run finishes.
    """
    transcript = []

    def _progress(msg: str):
        if progress:
            progress(msg)

    def _emit(entry: dict):
        transcript.append(entry)
        if on_turn:
            on_turn(entry)

    round_label = f"round {round_number}/2"

    prior_block = ""
    if prior_context:
        prior_block = (
            f"\n\nROUND 1 CONSULTATION:\n{prior_context}\n\n"
            "— This is round 2. Build on the above: refine and commit rather than explore anew. —\n"
        )

    # ── Turn 1: Artist ──────────────────────────────────────────────────────
    _progress(f"Consultation {round_label} — turn 1/4: Artist is studying the image...")

    if round_number == 1:
        artist_prompt = (
            "You are the Artist agent for bahAI Workforce, a Bahá'í-inspired art and craft "
            "business run by Sheraj. You just created this image as a bookmark design. "
            "The center strip (roughly the middle half) will be the front face of the bookmark "
            "— the part the buyer sees with the quote printed on it. "
            f"The requested theme was: {theme}\n\n"
            "Report back to your team in exactly 4 bullet points, one line each, no preamble:\n"
            "- Visual elements: colors, light, motifs, composition (one line)\n"
            "- Bahá'í themes or symbols evoked (one line)\n"
            "- Emotional mood — what a buyer will feel (one line)\n"
            "- What stands out most in the center strip / front face (one line)\n\n"
            "Describe motifs qualitatively (a radiant star, a ring of lotus blooms). Do NOT state "
            "an exact count of rays, points, petals, or arches — image generation cannot guarantee "
            "a requested count actually rendered, so naming a specific number risks asserting a "
            "fact you haven't verified. Terse and concrete. No throat-clearing, no restating the "
            "prompt. Under 60 words total."
        )
        try:
            artist_msg = call_grok_vision(image_path, artist_prompt,
                                          temperature=0.7, max_tokens=220).strip()
        except Exception as vision_err:
            artist_msg = (
                f"(Vision unavailable — {vision_err})\n\n"
                f"Image was generated from this prompt: {image_prompt[:300]}"
            )
    else:
        # Round 2: skip the vision call — refine in text using round 1 as context
        artist_input = (
            f"You are the Artist agent for bahAI Workforce.{prior_block}\n"
            f"Theme: {theme}\n\n"
            f"{CONSULTATION_SCRIPTURE['round2_response']} Ask yourself that, genuinely, about "
            "the Reviewer's round 1 challenge before answering.\n\n"
            "Answer in exactly 3 bullet points, one line each, no preamble:\n"
            "- Do you now believe the Reviewer's challenge is more true than your round 1 "
            "direction? If yes, adopt it and say why in one clause. If no, state your one "
            "strongest piece of concrete visual evidence — once, without repeating it.\n"
            "- The single visual element that best expresses the agreed direction\n"
            "- What the Scribe should emphasise to connect the buyer to it\n\n"
            "Terse and concrete. Under 50 words total."
        )
        artist_msg = call_llm(
            "creative_writing",  # Artist stays on Grok per routing directive
            [{"role": "user", "content": artist_input}],
            temperature=0.75,
            max_tokens=180,
        ).strip()

    _emit({
        "agent": "Artist",
        "role": f"image observation ({round_label})",
        "message": artist_msg,
    })

    # ── Turn 2: Scribe ──────────────────────────────────────────────────────
    _progress(f"Consultation {round_label} — turn 2/4: Scribe is proposing quote directions...")

    citation_block = ""
    if citations:
        citation_block = "\n\nAvailable spiritual citations:\n"
        for c in citations[:2]:
            citation_block += f'  — "{c.get("text", "")[:140]}" ({c.get("source", "")})\n'

    if round_number == 1:
        scribe_instruction = (
            "Answer in exactly 3 bullet points, one line each, no preamble:\n"
            "- The spiritual truth this image most powerfully expresses (one line)\n"
            "- ONE candidate bookmark quote (2–4 lines, 120–180 characters total, "
            "poetic, no quotation marks)\n"
            "- The emotional tone the listing should carry (one line)\n\n"
            "Terse. No throat-clearing, no restating the theme. Under 70 words total "
            "(the quote itself doesn't count against this)."
        )
    else:
        scribe_instruction = (
            f"Round 1 is done — now commit. {CONSULTATION_SCRIPTURE['round2_response']} Ask "
            "yourself that about the Reviewer's challenge before you decide.\n\n"
            "Answer in exactly 3 bullet points, one line each:\n"
            "- Do you now believe the Reviewer's challenge is more true than your round 1 "
            "direction? If yes, adopt it plainly and say why. If no, state your case once, "
            "clearly — not out of attachment to your own first draft.\n"
            "- THE quote you recommend, offered as the team's best shared direction rather than "
            "a personal claim (2–4 lines, 120–180 characters total, no quotation marks)\n"
            "- Why it fits, in one short clause\n\n"
            "Terse and decisive. Under 50 words total (the quote itself doesn't count against this)."
        )

    scribe_input = (
        f"You are the Scribe agent for bahAI Workforce.{prior_block}"
        f"The Artist just reported:\n\n{artist_msg}\n\n"
        f"Theme: {theme}{citation_block}\n\n"
        f"{scribe_instruction}"
    )
    scribe_msg = call_llm(
        "scribe",
        [{"role": "user", "content": scribe_input}],
        temperature=0.85 if round_number == 1 else 0.6,
        max_tokens=300,
    ).strip()
    _emit({
        "agent": "Scribe",
        "role": f"quote & listing proposal ({round_label})",
        "message": scribe_msg,
    })

    # ── Turn 3: Reviewer ────────────────────────────────────────────────────
    if round_number == 1:
        _progress(f"Consultation {round_label} — turn 3/4: Reviewer is challenging the direction...")
    else:
        _progress(f"Consultation {round_label} — turn 3/4: Reviewer is checking convergence...")

    if round_number == 1:
        reviewer_instruction = (
            f"You are the team's constitutional critic. {CONSULTATION_SCRIPTURE['round1_challenge']} "
            "— so your duty in this round is to supply the differing opinion, not to harmonise "
            f"early. {CONSULTATION_SCRIPTURE['round1_tone']} Challenge the IDEA, never the agent.\n\n"
            "Answer in exactly 3 bullet points, one line each, no preamble:\n"
            "- The 1–2 constitution principles most at stake (name only, no explanation)\n"
            "- Your strongest concrete disagreement with the direction so far — what could "
            "ring hollow, mislead a buyer, or drift from the theme. Only say 'none' if you "
            "genuinely find no weakness, and name what convinced you.\n"
            "- One alternative direction the team must weigh\n\n"
            "Terse and direct — a challenge, not an essay. Under 60 words total."
        )
    else:
        reviewer_instruction = (
            f"Check convergence — the team is about to commit. {CONSULTATION_SCRIPTURE['round2_verification']} "
            "Your job this round is to turn assumption into verified fact: re-examine the attached "
            "image with fresh eyes rather than confirming what round 1 assumed — if you cannot be "
            "certain of a specific visual detail (especially a count), say so, don't assert it.\n\n"
            "Answer in exactly 3 bullet points, one line each, no preamble:\n"
            "- Was your round 1 challenge adopted, adapted, rebutted, or ignored? Say which.\n"
            "- Does the committed quote serve the constitution? One principle name + confirm/flag.\n"
            f"- Green-light or hold, plus one short reason. {CONSULTATION_SCRIPTURE['round2_decision']} "
            "A green-light here becomes the WHOLE team's decision, not just your preference — hold "
            "if an ignored concern still matters; never green-light just to keep the peace.\n\n"
            "Terse and direct. Under 50 words total."
        )

    reviewer_input = (
        f"You are the Reviewer agent for bahAI Workforce.{prior_block}\n"
        f"Artist's observation:\n{artist_msg}\n\n"
        f"Scribe's proposal:\n{scribe_msg}\n\n"
        f"Theme: {theme}\n\n"
        "Caution: if the Artist's observation above names an exact count of a motif (e.g. "
        "'nine-rayed'), treat that as unverified — it may reflect what was requested rather "
        "than what actually rendered. Judge only what you see in the attached image, and never "
        "repeat or assert a specific count yourself; describe motifs qualitatively instead.\n\n"
        f"{reviewer_instruction}"
    )
    # The Reviewer sees the actual artwork (Grok vision) — it judges with its
    # own eyes rather than through the Artist's description. Falls back to
    # text-only if the vision call fails.
    try:
        reviewer_msg = call_grok_vision(
            image_path,
            "The attached image is the actual bookmark artwork under discussion. "
            "Examine it with your own eyes before responding.\n\n" + reviewer_input,
            temperature=0.65,
            max_tokens=180,
        ).strip()
    except Exception:
        reviewer_msg = call_llm(
            "reviewer",
            [{"role": "user", "content": reviewer_input}],
            temperature=0.65,
            max_tokens=180,
        ).strip()
    _emit({
        "agent": "Reviewer",
        "role": f"constitution guidance ({round_label})",
        "message": reviewer_msg,
    })

    # ── Turn 4: Librarian ───────────────────────────────────────────────────
    _progress(f"Consultation {round_label} — turn 4/4: Librarian is verifying quote authenticity...")
    verified_quote = ""

    if citations:
        citations_block = "\n\n".join(
            f'  [{i+1}] "{c.get("text", "").strip()[:300]}"\n'
            f'       — {c.get("source", "")} · {c.get("link", "")}'
            for i, c in enumerate(citations[:3])
        )
        source_instruction = (
            f"Verified passages retrieved from our Bahá'í text index:\n{citations_block}\n\n"
            "Write ONE verified bookmark quote adapted directly from these passages. "
            "You may condense or lightly rephrase but it must be traceable to a specific passage above. "
            "Do NOT invent new spiritual language.\n"
        )
    else:
        source_instruction = (
            "No specific passages were retrieved. Draw the verified quote from well-known, "
            "attributable Bahá'í writings (Bahá'u'lláh, 'Abdu'l-Bahá, the Báb, or Shoghi Effendi). "
            "Choose a short passage you are certain is authentic and name the exact source.\n"
        )

    round2_note = ""
    if round_number == 2:
        round2_note = (
            f"This is round 2 — the Scribe has committed to a specific direction. "
            f"{CONSULTATION_SCRIPTURE['round2_verification']} Your citation verdict is that "
            "certitude for the QUOTE specifically — GROUNDED or ORIGINAL COMPOSITION, never a "
            "guess. (Note: you verify citations only; you do not see the artwork, so never "
            "certify any visual or numeric claim about the image — that is outside your scope.) "
            "If the round 1 quote works well, keep it. If the Scribe's round 2 direction "
            "suggests a better or more fitting quote, use that instead.\n\n"
        )

    librarian_input = (
        f"You are the Librarian agent for bahAI Workforce.{prior_block}"
        "Your role is to ensure every bookmark quote is grounded in actual verified Bahá'í writings — "
        "not original poetry or language invented by the Scribe.\n\n"
        f"{round2_note}"
        f"{source_instruction}\n"
        f"Scribe's proposed quotes:\n{scribe_msg}\n\n"
        "Your task:\n"
        "1. State whether the Scribe's quotes are drawn from the sources above "
        "or are original composition.\n"
        "2. Provide ONE verified bookmark quote (2–4 lines, 120–180 characters total). "
        "Write each line on its own line — do NOT use forward slashes as separators.\n"
        "3. Name the source author and work.\n\n"
        "Reply in EXACTLY this format — nothing before VERDICT, nothing after REASONING:\n"
        "VERDICT: [GROUNDED IN SOURCES / ORIGINAL COMPOSITION]\n"
        "VERIFIED QUOTE: [first line of the quote]\n"
        "[second line if needed]\n"
        "[third line if needed]\n"
        "SOURCE: [author, work]\n"
        "REASONING: [one sentence]"
    )
    librarian_msg = call_llm(
        "librarian",
        [{"role": "user", "content": librarian_input}],
        temperature=0.2,
        max_tokens=300,
    ).strip()

    # Extract VERIFIED QUOTE — collect all continuation lines until the next labelled field
    lines = librarian_msg.splitlines()
    for i, line in enumerate(lines):
        if line.upper().startswith("VERIFIED QUOTE:"):
            candidate = line.split(":", 1)[1].strip().strip('"')
            j = i + 1
            while j < len(lines) and not lines[j].upper().startswith(("SOURCE:", "REASONING:", "VERDICT:")):
                extra = lines[j].strip().strip('"')
                if extra:
                    candidate += "\n" + extra
                j += 1
            verified_quote = _normalize_quote(candidate)
            break

    quote_grounded = _parse_verdict_grounded(librarian_msg) if verified_quote else False

    _emit({
        "agent": "Librarian",
        "role": f"citation verification ({round_label})",
        "message": librarian_msg,
    })

    context = (
        f"TEAM CONSULTATION — ROUND {round_number} — THEME: {theme}\n\n"
        f"[Artist]:\n{artist_msg}\n\n"
        f"[Scribe]:\n{scribe_msg}\n\n"
        f"[Reviewer]:\n{reviewer_msg}\n\n"
        f"[Librarian]:\n{librarian_msg}\n"
    )

    return {
        "transcript": transcript,
        "context": context,
        "verified_quote": verified_quote,
        "quote_grounded": quote_grounded,
        "artist_observation": artist_msg,
    }


def _synthesize_brief(round1_context: str, round2_context: str, theme: str,
                      human_note: str = "", progress=None) -> dict:
    """
    Distill the two consultation rounds into a compact, structured decision brief.

    Two reasons this step exists:
    1. The Scribe runs on the local model, and feeding it both full transcripts
       (~2,000 tokens of dialogue) reliably made it truncate its JSON output.
       ~200 tokens of decisions works; 2,000 tokens of process does not.
    2. If the team agreed the ARTWORK should change (e.g. "add ordinary people
       exchanging kindnesses"), that decision must become machine-readable so
       the pipeline can actually regenerate the image — otherwise the Reviewer
       later punishes the gap between what was agreed and what shipped.

    human_note is Sheraj's optional input, given after round 2 — when present
    it must outrank both rounds, since a human overseeing the team outranks
    the team's own conclusions about itself.
    """
    if progress:
        progress("Consultation — synthesizing the team's decisions into a brief...")

    human_block = (
        f"\n\nSHERAJ'S GUIDANCE (given after round 2 — this OUTRANKS the team's own "
        f"conclusions wherever it conflicts with them): {human_note}\n"
    ) if human_note else ""

    override_note = ", and Sheraj's guidance overrides both" if human_note else ""
    synth_input = (
        "Below is a two-round team consultation about a Bahá'í-inspired bookmark "
        f"(theme: {theme}). Extract the team's FINAL decisions — round 2 overrides "
        f"round 1 wherever they differ{override_note}.\n\n"
        f"{round1_context}\n\n{round2_context}\n{human_block}\n"
        "Return ONLY this JSON object:\n"
        "{\n"
        '  "agreed_direction": "the spiritual/creative direction the team committed to, one sentence",\n'
        '  "tone": "the emotional tone agreed for the listing, a few words",\n'
        '  "key_elements": ["2-4 visual elements of the artwork the listing should reference"],\n'
        '  "image_adjustment": "if the team agreed the ARTWORK itself must change, describe the '
        "change in one imperative sentence suitable for an image generator; if the artwork was "
        'accepted as-is, exactly the empty string"\n'
        "}"
    )
    try:
        raw = call_llm(
            "plan",
            [{"role": "user", "content": synth_input}],
            temperature=0.2,
            max_tokens=300,
            json_mode=True,
        ).strip()
        brief = json.loads(raw)
        if not isinstance(brief, dict):
            raise ValueError("brief is not a JSON object")
    except Exception:
        brief = {}

    adjustment = str(brief.get("image_adjustment") or "").strip()
    if adjustment.lower() in ("none", "no change", "n/a", "null", "-"):
        adjustment = ""
    return {
        "agreed_direction": str(brief.get("agreed_direction") or "").strip(),
        "tone": str(brief.get("tone") or "").strip(),
        "key_elements": [str(e).strip() for e in (brief.get("key_elements") or []) if str(e).strip()][:4],
        "image_adjustment": adjustment,
    }


def run_consultation(
    image_path: str,
    theme: str,
    image_prompt: str,
    citations: list,
    progress=None,
    on_turn=None,
    request_human_input=None,
) -> dict:
    """
    Run two rounds of consultation about the generated image.

    Round 1 is exploratory — the team describes, proposes, guides, and verifies.
    Round 2 is convergent — the team refines, commits, checks, and finalises.
    Once both rounds are done, if request_human_input is given, the Reviewer
    asks Sheraj for guidance before the Scribe writes — a real pause point,
    not a rhetorical one: request_human_input(prompt) is expected to BLOCK
    until a human responds (or times out), so the whole pipeline genuinely
    waits. Whatever comes back outranks the team's own conclusions.
    A synthesis step then distills both rounds (plus any human note) into a
    structured decision brief.

    on_turn(entry), if given, fires after each agent turn (both rounds AND the
    pause/human turns) — this is what lets the dashboard render consultation
    as a live chat instead of only showing it once the whole run finishes.

    Returns:
        transcript     — combined list of {agent, role, message} from both rounds
                         plus the pause/human turns if any
        context        — compact decision brief for the Scribe (NOT the raw dialogue;
                         the local model truncates on transcript-sized prompts)
        verified_quote — Librarian's final quote from Round 2 (falls back to Round 1)
        brief          — structured decisions incl. image_adjustment for the pipeline
    """
    # ── Round 1: Exploration ─────────────────────────────────────────────────
    round1 = _run_round(
        image_path, theme, image_prompt, citations,
        progress=progress, on_turn=on_turn, round_number=1,
    )

    # ── Round 2: Convergence ─────────────────────────────────────────────────
    round2 = _run_round(
        image_path, theme, image_prompt, citations,
        progress=progress, on_turn=on_turn, round_number=2,
        prior_context=round1["context"],
        prior_artist_observation=round1["artist_observation"],
    )

    # ── Pause: the Reviewer asks Sheraj for input before the team commits ────
    human_note = ""
    pause_turns = []
    if request_human_input:
        ask_message = (
            "Round 2 is done — that's our direction. Any guidance before I write the "
            "listing? Leave it blank and send to continue as-is."
        )
        ask_turn = {"agent": "Reviewer", "role": "asking Sheraj for input", "message": ask_message}
        pause_turns.append(ask_turn)
        if on_turn:
            on_turn(ask_turn)
        if progress:
            progress("Waiting for Sheraj's input...")
        human_note = (request_human_input(ask_message) or "").strip()
        if human_note:
            human_turn = {"agent": "Sheraj", "role": "guidance", "message": human_note}
            pause_turns.append(human_turn)
            if on_turn:
                on_turn(human_turn)

    # Prefer a GROUNDED quote over an ungrounded one, regardless of round — the
    # Librarian's own verdict decides, not recency. Previously round 2 always
    # won even when its verdict said ORIGINAL COMPOSITION while round 1 had a
    # verdict of GROUNDED IN SOURCES: the pipeline locked in an unverified
    # quote as "Librarian-verified" for the rest of the run, which the Scribe
    # then could never fix (the quote is locked) and the Reviewer kept
    # correctly flagging as a Trustworthiness/Justice gap with no possible
    # resolution — a structural dead end, not a compliance failure.
    r1_quote, r1_grounded = round1.get("verified_quote", ""), round1.get("quote_grounded", False)
    r2_quote, r2_grounded = round2.get("verified_quote", ""), round2.get("quote_grounded", False)

    if r2_quote and r2_grounded:
        final_quote, quote_grounded = r2_quote, True
    elif r1_quote and r1_grounded:
        final_quote, quote_grounded = r1_quote, True
    elif r2_quote:
        final_quote, quote_grounded = r2_quote, False
    else:
        final_quote, quote_grounded = r1_quote, False

    # ── Synthesis: distill both rounds (+ human note) into a decision brief ──
    brief = _synthesize_brief(round1["context"], round2["context"], theme,
                              human_note=human_note, progress=progress)

    # Scribe-facing context: decisions only, not dialogue. The full transcripts
    # stay in `transcript` for the dashboard and the Reviewer's Principle-4 evidence.
    context_lines = [f"TEAM CONSULTATION OUTCOME — THEME: {theme}", ""]
    if brief.get("agreed_direction"):
        context_lines.append(f"Agreed direction: {brief['agreed_direction']}")
    if brief.get("tone"):
        context_lines.append(f"Listing tone: {brief['tone']}")
    if brief.get("key_elements"):
        context_lines.append("Visual elements of the artwork to reference: "
                             + "; ".join(brief["key_elements"]))
    if len(context_lines) == 2:
        # Synthesis failed — fall back to the Round 2 dialogue (shorter than both rounds)
        context_lines.append(round2["context"])
    context_lines.append(
        "\nThe listing must clearly reflect this agreed direction, tone, and imagery."
    )
    if final_quote and quote_grounded:
        context_lines.append(
            "\nCRITICAL — The bookmark_quote field MUST use exactly this Librarian-verified "
            f'text (do not alter it):\n"{final_quote}"'
        )
    elif final_quote:
        context_lines.append(
            "\nCRITICAL — The bookmark_quote field MUST use exactly this text (do not alter it), "
            "but the Librarian could NOT trace it to a specific source, so do not describe it as "
            f'a verified scriptural quotation — call it the team\'s guiding phrase instead:\n"{final_quote}"'
        )
    if human_note:
        context_lines.append(
            f"\nSheraj's guidance, given after round 2 (top priority — must be honoured over "
            f"anything the team concluded on its own): {human_note}"
        )
    full_context = "\n".join(context_lines)

    return {
        "transcript": round1["transcript"] + round2["transcript"] + pause_turns,
        "context": full_context,
        "verified_quote": final_quote,
        "quote_grounded": quote_grounded,
        "brief": brief,
        "human_note": human_note,
    }
