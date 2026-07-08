"""
Consultation — multi-agent dialogue about generated artwork before the listing is written.

Flow (three rounds, with a single human pause between rounds 2 and 3):
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

  — Pause: Sheraj is asked once for guidance (with a rendered front-face preview) —

  Round 3 — Final cycle (builds on Round 2 + Sheraj's guidance, if any)
    Same four turns again — the team's dialogue genuinely continues after the
    human's review instead of stopping the moment a human has spoken. Sheraj's
    guidance, if given, is folded into every turn's prompt and outranks the
    team's own prior conclusions.

The full transcript from all three rounds (with the pause turns in their real
chronological place, between rounds 2 and 3) is returned. Round 3's context
(which was built on Round 2, which was built on Round 1) is what's passed to
the Scribe when writing the listing.
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


# Product-specific wording injected into the consultation prompts. The
# structure of the consultation (4 turns × 2 rounds, verdict parsing, brief
# synthesis) is shared between product lines — only the framing differs.
# The "bookmark" values are the pipeline's original wording, character for
# character: the bookmark path must behave exactly as before this dict
# existed. "quote_card" reframes for the giveaway card: the reader is a
# possible newcomer to the Faith (accessibility is the design test, not
# marketability), there is no Etsy listing, and the printed quote must be
# shorter to stay legible on a 3.5×2 inch face.
_PRODUCT_FRAMES = {
    "bookmark": {
        "artist_context": (
            "a bookmark design. "
            "The center strip (roughly the middle half) will be the front face of the bookmark "
            "— the part the buyer sees with the quote printed on it."
        ),
        "audience": "buyer",
        "front_region": "the center strip / front face",
        "output": "listing",
        "quote_name": "bookmark quote",
        "quote_spec": "2–4 lines, 120–180 characters total",
        "reviewer_risk": "ring hollow, mislead a buyer, or drift from the theme",
        "reviewer_extra": "",
        "synth_subject": "a Bahá'í-inspired bookmark",
        "ask_message": (
            "Round 2 is done — that's our direction. Any guidance before I write the "
            "listing? Leave it blank and send to continue as-is."
        ),
        "source_scope": "",
    },
    "quote_card": {
        "artist_context": (
            "a quote card design — a small 3.5×2 inch giveaway card whose purpose is to "
            "introduce someone who may know nothing about the Bahá'í Faith to one beautiful "
            "idea. A wide middle band of the image becomes the card's front face with the "
            "quote printed over it; a neighbouring band becomes the clean back face."
        ),
        "audience": "recipient — possibly a complete newcomer to the Faith",
        "front_region": "the wide middle band / front face",
        "output": "card's visual styling",
        "quote_name": "card quote",
        "quote_spec": (
            "1–3 lines, 60–140 characters total — this is a business-card-sized giveaway, "
            "shorter is better"
        ),
        "reviewer_risk": (
            "ring hollow, feel esoteric or exclusionary to someone with zero background "
            "in the Faith, or drift from the theme"
        ),
        "reviewer_extra": (
            " Weigh above all: does this actually work as a FIRST introduction — "
            "welcoming, clear, beautiful without needing explanation?"
        ),
        "synth_subject": (
            "a Bahá'í quote card — a small giveaway card meant as a first introduction "
            "to the Faith for someone unfamiliar with it"
        ),
        "ask_message": (
            "Round 2 is done — that's our direction for the card. Any guidance before we "
            "finalise it? Leave it blank and send to continue as-is."
        ),
        # Owner decision, 2026-07: quote cards may ONLY ever print a quote from
        # Ruhi Institute Book 1 ("Reflections on the Life of the Spirit"). The
        # citations shown to the Librarian below are already restricted to that
        # book (see librarian.retrieve_ruhi_book1 and api.py's card pipeline) —
        # this note is defense in depth against the model substituting a
        # different, well-known passage from memory instead of the ones given.
        "source_scope": (
            " These citations are drawn specifically from Reflections on the Life of the "
            "Spirit (Ruhi Institute, Book 1) — this card's quote must be one of them, "
            "adapted at most lightly; never substitute a different Bahá'í passage from "
            "memory, however well known or fitting it may seem."
        ),
    },
    "x_post": {
        "artist_context": (
            "a single image for an X (Twitter) post. There is no front/back split — the "
            "whole image is the post's attached photo, and X renders the tweet's own text "
            "separately, so nothing is printed on the image itself."
        ),
        "audience": "a stranger scrolling past on X, who may know nothing about the Bahá'í Faith",
        "front_region": "the whole image (a single social post image, not a front/back split)",
        "output": "tweet",
        "quote_name": "tweet's quoted line",
        "quote_spec": "a short excerpt, roughly 100-200 characters, that fits inside a ~280-character tweet",
        "reviewer_risk": "ring hollow, read as an ad or a sermon, or drift from the theme",
        "reviewer_extra": (
            " Weigh above all: would a stranger scrolling past find this genuine and worth "
            "pausing for, not promotional?"
        ),
        "synth_subject": (
            "a short X (Twitter) post sharing a Bahá'í teaching as a public reflection — "
            "a giveaway, never sold"
        ),
        "ask_message": (
            "Round 2 is done — that's our direction for the post. Any guidance before the "
            "Scribe writes the tweet? Leave it blank and send to continue as-is."
        ),
        "source_scope": "",
    },
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


def _pick_final_quote(rounds: list) -> tuple:
    """
    Pick the quote to actually ship out of N consultation rounds. The
    Librarian's own GROUNDED/ORIGINAL-COMPOSITION verdict decides, never mere
    recency (see the long comment this replaced, previously inlined at the
    two-round call site) — but among equally-grounded (or equally ungrounded)
    rounds, prefer the latest, since it reflects the most consultation.
    """
    for r in reversed(rounds):
        if r.get("verified_quote") and r.get("quote_grounded"):
            return r["verified_quote"], True
    for r in reversed(rounds):
        if r.get("verified_quote"):
            return r["verified_quote"], False
    return "", False


def _run_round(
    image_path: str,
    theme: str,
    image_prompt: str,
    citations: list,
    progress=None,
    on_turn=None,
    round_number: int = 1,
    total_rounds: int = 3,
    prior_context: str = "",
    prior_artist_observation: str = "",
    human_note: str = "",
    product: str = "bookmark",
) -> dict:
    """
    Run one four-turn consultation round.
    Rounds after the first receive prior_context (the previous round's full
    context) and prior_artist_observation so the Artist doesn't need to
    re-describe the image from scratch. Round 3 additionally receives
    human_note (Sheraj's guidance from the pause, if any) folded into every
    turn's prompt — it outranks the team's own prior conclusions.
    on_turn(entry), if given, fires immediately after each agent's turn completes —
    this is what lets the dashboard render the consultation as a live chat instead of
    only showing the transcript once the whole run finishes.
    """
    transcript = []
    frame = _PRODUCT_FRAMES[product]

    def _progress(msg: str):
        if progress:
            progress(msg)

    def _emit(entry: dict):
        transcript.append(entry)
        if on_turn:
            on_turn(entry)

    round_label = f"round {round_number}/{total_rounds}"
    prev_round_number = round_number - 1

    prior_block = ""
    if prior_context:
        prior_block = (
            f"\n\nROUND {prev_round_number} CONSULTATION:\n{prior_context}\n\n"
            f"— This is round {round_number}. Build on the above: refine and commit rather than explore anew. —\n"
        )
    if human_note:
        prior_block += (
            f"\n\nSHERAJ'S GUIDANCE (just given after round {prev_round_number} — outranks "
            f"the team's own prior conclusions wherever it conflicts): {human_note}\n"
        )

    # ── Turn 1: Artist ──────────────────────────────────────────────────────
    _progress(f"Consultation {round_label} — turn 1/4: Artist is studying the image...")

    if round_number == 1:
        artist_prompt = (
            "You are the Artist agent for bahAI Workforce, a Bahá'í-inspired art and craft "
            f"business run by Sheraj. You just created this image as {frame['artist_context']} "
            f"The requested theme was: {theme}\n\n"
            "Report back to your team in exactly 4 bullet points, one line each, no preamble:\n"
            "- Visual elements: colors, light, motifs, composition (one line)\n"
            "- Bahá'í themes or symbols evoked (one line)\n"
            f"- Emotional mood — what a {frame['audience']} will feel (one line)\n"
            f"- What stands out most in {frame['front_region']} (one line)\n\n"
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
            f"- What the Scribe should emphasise to connect the {frame['audience']} to it\n\n"
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
            f"- ONE candidate {frame['quote_name']} ({frame['quote_spec']}, "
            "poetic, no quotation marks)\n"
            f"- The emotional tone the {frame['output']} should carry (one line)\n\n"
            "Terse. No throat-clearing, no restating the theme. Under 70 words total "
            "(the quote itself doesn't count against this)."
        )
    else:
        scribe_instruction = (
            f"Round {prev_round_number} is done — now commit. {CONSULTATION_SCRIPTURE['round2_response']} Ask "
            "yourself that about the Reviewer's challenge before you decide.\n\n"
            "Answer in exactly 3 bullet points, one line each:\n"
            "- Do you now believe the Reviewer's challenge is more true than your round 1 "
            "direction? If yes, adopt it plainly and say why. If no, state your case once, "
            "clearly — not out of attachment to your own first draft.\n"
            "- THE quote you recommend, offered as the team's best shared direction rather than "
            f"a personal claim ({frame['quote_spec']}, no quotation marks)\n"
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
            f"{frame['reviewer_risk']}. Only say 'none' if you "
            "genuinely find no weakness, and name what convinced you.\n"
            "- One alternative direction the team must weigh\n\n"
            f"Terse and direct — a challenge, not an essay. Under 60 words total.{frame['reviewer_extra']}"
        )
    else:
        reviewer_instruction = (
            f"Check convergence — the team is about to commit. {CONSULTATION_SCRIPTURE['round2_verification']} "
            "Your job this round is to turn assumption into verified fact: re-examine the attached "
            f"image with fresh eyes rather than confirming what round {prev_round_number} assumed — if you cannot be "
            "certain of a specific visual detail (especially a count), say so, don't assert it.\n\n"
            "Answer in exactly 3 bullet points, one line each, no preamble:\n"
            f"- Was your round {prev_round_number} challenge adopted, adapted, rebutted, or ignored? Say which.\n"
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

    later_round_note = ""
    if round_number > 1:
        later_round_note = (
            f"This is round {round_number} — the Scribe has committed to a specific direction. "
            f"{CONSULTATION_SCRIPTURE['round2_verification']} Your citation verdict is that "
            "certitude for the QUOTE specifically — GROUNDED or ORIGINAL COMPOSITION, never a "
            "guess. (Note: you verify citations only; you do not see the artwork, so never "
            "certify any visual or numeric claim about the image — that is outside your scope.) "
            f"If the round {prev_round_number} quote works well, keep it. If this round's "
            "direction suggests a better or more fitting quote, use that instead.\n\n"
        )

    librarian_input = (
        f"You are the Librarian agent for bahAI Workforce.{prior_block}"
        f"Your role is to ensure every {frame['quote_name']} is grounded in actual verified Bahá'í writings — "
        f"not original poetry or language invented by the Scribe.{frame['source_scope']}\n\n"
        f"{later_round_note}"
        f"{source_instruction}\n"
        f"Scribe's proposed quotes:\n{scribe_msg}\n\n"
        "Your task:\n"
        "1. State whether the Scribe's quotes are drawn from the sources above "
        "or are original composition.\n"
        f"2. Provide ONE verified {frame['quote_name']} ({frame['quote_spec']}). "
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


def _synthesize_brief(round_contexts: list, theme: str,
                      human_note: str = "", progress=None,
                      product: str = "bookmark") -> dict:
    """
    Distill the N consultation rounds into a compact, structured decision brief.

    Two reasons this step exists:
    1. The Scribe runs on the local model, and feeding it every full transcript
       (thousands of tokens of dialogue) reliably made it truncate its JSON output.
       ~200 tokens of decisions works; thousands of tokens of process does not.
    2. If the team agreed the ARTWORK should change (e.g. "add ordinary people
       exchanging kindnesses"), that decision must become machine-readable so
       the pipeline can actually regenerate the image — otherwise the Reviewer
       later punishes the gap between what was agreed and what shipped.

    human_note is Sheraj's optional input, given between rounds 2 and 3 — when
    present it must outrank every round, since a human overseeing the team
    outranks the team's own conclusions about itself.
    """
    if progress:
        progress("Consultation — synthesizing the team's decisions into a brief...")

    n = len(round_contexts)
    human_block = (
        f"\n\nSHERAJ'S GUIDANCE (given between rounds 2 and 3 — this OUTRANKS the team's "
        f"own conclusions wherever it conflicts with them): {human_note}\n"
    ) if human_note else ""

    override_note = ", and Sheraj's guidance overrides all of them" if human_note else ""
    rounds_block = "\n\n".join(round_contexts)
    synth_input = (
        f"Below is a {n}-round team consultation about {_PRODUCT_FRAMES[product]['synth_subject']} "
        f"(theme: {theme}). Extract the team's FINAL decisions — each later round overrides "
        f"earlier rounds wherever they differ{override_note}.\n\n"
        f"{rounds_block}\n{human_block}\n"
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
    product: str = "bookmark",
    render_preview=None,
    preview_note: str = "",
) -> dict:
    """
    Run three rounds of consultation about the generated image, with a single
    human pause between rounds 2 and 3.

    Round 1 is exploratory — the team describes, proposes, guides, and verifies.
    Round 2 is convergent — the team refines, commits, checks, and finalises.
    If request_human_input is given, the Reviewer then asks Sheraj for
    guidance — a real pause point, not a rhetorical one: request_human_input(prompt)
    is expected to BLOCK until a human responds (or times out), so the whole
    pipeline genuinely waits. Sheraj is asked exactly once.
    Round 3 then runs as a full additional four-turn cycle, carrying Sheraj's
    guidance (if any) into every turn — the team's dialogue genuinely
    continues after the human's review rather than stopping the moment a
    human has spoken. Only after round 3 does the brief get synthesised.

    on_turn(entry), if given, fires after each agent turn, in the actual
    chronological order the pipeline ran them (round 1, round 2, the
    pause/human turns, then round 3) — this is what lets the dashboard render
    consultation as a live chat instead of only showing it once the whole run
    finishes.

    render_preview(quote, transcript) -> web image path, if given, is called
    just before the pause so the Reviewer's ask-for-input turn carries an
    actual rendered front-face image ("image" key on the turn) — Sheraj steers
    from what the product LOOKS like, not a text description. It must be
    LLM-free and cheap (pure compositor render); a failure only skips the
    preview, never blocks the pause. preview_note is appended to the ask
    message when a preview rendered (e.g. the card pipeline's honesty caveat
    that the translation is added after the pause).

    Returns:
        transcript     — combined list of {agent, role, message} from all three
                         rounds plus the pause/human turns, in chronological order
        context        — compact decision brief for the Scribe (NOT the raw dialogue;
                         the local model truncates on transcript-sized prompts)
        verified_quote — the latest Librarian-grounded quote across all three
                         rounds (falls back to the latest quote of any kind)
        brief          — structured decisions incl. image_adjustment for the pipeline
    """
    # ── Round 1: Exploration ─────────────────────────────────────────────────
    round1 = _run_round(
        image_path, theme, image_prompt, citations,
        progress=progress, on_turn=on_turn, round_number=1, total_rounds=3, product=product,
    )

    # ── Round 2: Convergence ─────────────────────────────────────────────────
    round2 = _run_round(
        image_path, theme, image_prompt, citations,
        progress=progress, on_turn=on_turn, round_number=2, total_rounds=3,
        prior_context=round1["context"],
        prior_artist_observation=round1["artist_observation"],
        product=product,
    )

    # Interim pick from rounds 1-2 only, purely to drive the pre-pause preview
    # below — _pick_final_quote is re-run after round 3 for the real answer.
    preview_quote, _ = _pick_final_quote([round1, round2])

    # ── Pause: the Reviewer asks Sheraj for input before round 3 ─────────────
    human_note = ""
    pause_turns = []
    if request_human_input:
        ask_message = _PRODUCT_FRAMES[product]["ask_message"]
        preview_image = ""
        if render_preview and preview_quote:
            try:
                preview_image = render_preview(
                    preview_quote, round1["transcript"] + round2["transcript"]
                ) or ""
            except Exception as e:
                # A broken preview must never block the pause — but it must
                # not vanish either (Activity Log discipline).
                if progress:
                    progress(f"Front-face preview could not be rendered ({e}) — "
                             "continuing without it.")
        if preview_image and preview_note:
            ask_message = f"{ask_message}\n\n{preview_note}"
        ask_turn = {"agent": "Reviewer", "role": "asking Sheraj for input", "message": ask_message}
        if preview_image:
            ask_turn["image"] = preview_image
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

    # ── Round 3: Final cycle — the team's dialogue continues after review ────
    round3 = _run_round(
        image_path, theme, image_prompt, citations,
        progress=progress, on_turn=on_turn, round_number=3, total_rounds=3,
        prior_context=round2["context"],
        prior_artist_observation=round2["artist_observation"],
        human_note=human_note,
        product=product,
    )

    # Prefer a GROUNDED quote over an ungrounded one, regardless of round — the
    # Librarian's own verdict decides, not recency (see _pick_final_quote).
    # Previously round 2 always won even when its verdict said ORIGINAL
    # COMPOSITION while round 1 had a verdict of GROUNDED IN SOURCES: the
    # pipeline locked in an unverified quote as "Librarian-verified" for the
    # rest of the run, which the Scribe then could never fix (the quote is
    # locked) and the Reviewer kept correctly flagging as a
    # Trustworthiness/Justice gap with no possible resolution — a structural
    # dead end, not a compliance failure.
    final_quote, quote_grounded = _pick_final_quote([round1, round2, round3])

    # ── Synthesis: distill all three rounds (+ human note) into a decision brief ──
    brief = _synthesize_brief(
        [round1["context"], round2["context"], round3["context"]], theme,
        human_note=human_note, progress=progress, product=product)

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
        # Synthesis failed — fall back to the Round 3 dialogue (the most
        # recent; it already builds on rounds 1-2 via its own prior_block)
        context_lines.append(round3["context"])
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
            f"\nSheraj's guidance, given between rounds 2 and 3 (top priority — must be honoured "
            f"over anything the team concluded on its own): {human_note}"
        )
    full_context = "\n".join(context_lines)

    return {
        "transcript": round1["transcript"] + round2["transcript"] + pause_turns + round3["transcript"],
        "context": full_context,
        "verified_quote": final_quote,
        "quote_grounded": quote_grounded,
        "brief": brief,
        "human_note": human_note,
    }


# Card quotes are drawn from a small, hand-curated pool of ~65 verbatim
# passages from Ruhi Institute Book 1 (see librarian.retrieve_ruhi_book1,
# CLAUDE.md hard rule 11) — never a fresh search of the full Bahá'í writings,
# and never a paraphrase or modernisation. Bookmarks have no such
# restriction (their Librarian searches the general 7-text index). Before
# this note existed, the Reviewer's post-render calls kept asking for
# "modern, plain-language" rewordings the pool cannot structurally supply —
# every passage is 19th-century scripture — which drove unproductive
# requote loops (owner feedback, 2026-07: "the reviewer seems to be off the
# mark... give them more thinking ability"). Surfacing this explicitly to
# every post-render turn keeps their diagnoses honest about what a
# "requote" can and can't fix.
CARD_QUOTE_SOURCING_NOTE = (
    "This card's quote must be a VERBATIM excerpt from a small hand-curated pool of Ruhi "
    "Institute Book 1 passages (Reflections on the Life of the Spirit) — about 65 short "
    "passages total, searched semantically a few at a time. A \"requote\" can surface a "
    "different passage from that same pool via a new search phrase, but nothing outside it "
    "and nothing reworded, shortened-by-paraphrase, or modernised — only a different "
    "verbatim excerpt. These are 19th-century scriptural passages, so some archaic register "
    "(\"thee\", \"ere\", \"summoned to a reckoning\") is often unavoidable across the whole "
    "pool. Judge and steer within that reality — asking for \"modern, plain-language\" "
    "phrasing is not an achievable fix here."
)


def run_card_revision_consultation(
    theme: str,
    quote: str,
    citation_source: str,
    front_image_path: str,
    citations: list,
    review: dict,
    progress=None,
    on_turn=None,
    attempt: int = 1,
    history: list = None,
) -> dict:
    """
    Quote-card post-render revision — the team weighs in, not just the
    Reviewer alone. Previously the Reviewer's own `action`/`action_guidance`
    fields drove requote/repaint unilaterally ("the last part just has the
    reviewer saying stuff" — owner feedback, 2026-07). Now the Artist and
    Librarian each react to the Reviewer's scored concerns first, and the
    Reviewer casts the final call after hearing them — same "differing
    opinions" discipline as the pre-render consultation, condensed to three
    turns since the card is already scored.

    Every turn is told CARD_QUOTE_SOURCING_NOTE up front (the Reviewer
    previously didn't know the quote pool was Book-1-only and kept asking
    for fixes — "modern phrasing" — the pool can't supply). `history`, if
    given, is the prior {attempt, action, guidance, overall, prev_overall}
    entries from this same run: it lets the Reviewer see whether a similar
    ask already failed to help, rather than repeating it a third time. If
    the Reviewer's final call overrules the Librarian's own recommendation,
    it must start with "REOPENING LIBRARIAN'S READ: <specific new reason>"
    — the same override discipline used elsewhere for consultation decisions
    — rather than silently talking past the team.

    The Reviewer's own `review["action"]`/`review["action_guidance"]` are the
    fallback whenever a turn fails or returns something malformed — a broken
    group discussion must never block the revision loop the pipeline already
    depends on.

    Returns {transcript, action, action_guidance} — action is always one of
    "ship" | "requote" | "repaint", the same machine-readable contract
    score_quote_card already established.
    """
    transcript = []

    def _progress(msg: str):
        if progress:
            progress(msg)

    def _emit(entry: dict):
        transcript.append(entry)
        if on_turn:
            on_turn(entry)

    scores = review.get("scores") or {}
    weak = [c for c, v in scores.items() if isinstance(v, dict) and v.get("score", 10) < 7]
    weak_notes = "\n".join(f"- {c}: {scores[c].get('note', '')}" for c in weak) or "(no criterion scored below 7)"
    fallback_action = str(review.get("action") or "ship").strip().lower()
    fallback_guidance = str(review.get("action_guidance") or "").strip()

    history_lines = []
    for h in (history or []):
        delta = ("improved" if h["overall"] > h["prev_overall"]
                 else "no change" if h["overall"] == h["prev_overall"] else "got worse")
        history_lines.append(
            f'  Attempt {h["attempt"]}: {h["action"]} — "{h["guidance"][:100]}" '
            f'-> {h["overall"]}/10 ({delta} from {h["prev_overall"]}/10)'
        )
    history_block = (
        "\n\nPRIOR REVISION ATTEMPTS THIS RUN:\n" + "\n".join(history_lines) + "\n"
        "If a similar ask was already tried and it didn't help, that is real evidence — "
        "don't repeat it a third time hoping for a different result.\n"
    ) if history_lines else ""

    # ── Turn 1: Artist reacts to the Reviewer's concerns ─────────────────────
    _progress(f"Consultation (revision {attempt}) — Artist is weighing in on the Reviewer's score...")
    artist_prompt = (
        f"You are the Artist agent for bahAI Workforce. The Reviewer just scored this printed "
        f"quote card {review.get('overall', '?')}/10 for theme \"{theme}\". Weak points it flagged:\n"
        f"{weak_notes}\n\n"
        "Answer in exactly 2 bullet points, one line each, no preamble:\n"
        "- Do you believe the ARTWORK is the actual problem, or is it the quote/text? Say which, plainly.\n"
        "- If it's the artwork, the one concrete visual change you'd make; if not, say 'not the artwork'.\n\n"
        "Terse and concrete. Under 55 words total."
    )
    try:
        artist_msg = call_grok_vision(front_image_path, artist_prompt,
                                      temperature=0.6, max_tokens=200).strip()
    except Exception as e:
        artist_msg = f"(Artist unavailable — {e})"
    _emit({"agent": "Artist", "role": f"revision consult {attempt}", "message": artist_msg})

    # ── Turn 2: Librarian reacts, offers an alternative passage if relevant ──
    _progress(f"Consultation (revision {attempt}) — Librarian is weighing in on the quote fit...")
    other_passages = "\n".join(
        f'  [{i+1}] "{c.get("text", "").strip()[:180]}" — {c.get("source", "")}'
        for i, c in enumerate(citations[:3])
    ) or "(none retrieved)"
    librarian_prompt = (
        f"You are the Librarian agent for bahAI Workforce. {CARD_QUOTE_SOURCING_NOTE}\n\n"
        f"The printed quote is:\n\"{quote}\"\n— {citation_source}\n\n"
        f"The Reviewer's weak points:\n{weak_notes}\n\n"
        f"Passages from the pool already retrieved for this theme (a differently-worded "
        f"search can surface others from the same pool, but never anything outside it):\n"
        f"{other_passages}"
        f"{history_block}\n"
        "Answer in exactly 2 bullet points, one line each, no preamble:\n"
        "- Is the QUOTE itself the problem (fit, length, clarity)? Say plainly — and if the "
        "pool genuinely has no better option, say so rather than proposing a change for its own sake.\n"
        "- If yes and a real alternative exists above, name it by number and why in one clause; "
        "otherwise say 'keep it'.\n\n"
        "Terse. Under 55 words total."
    )
    try:
        librarian_msg = call_llm(
            "librarian", [{"role": "user", "content": librarian_prompt}],
            temperature=0.3, max_tokens=220,
        ).strip()
    except Exception as e:
        librarian_msg = f"(Librarian unavailable — {e})"
    _emit({"agent": "Librarian", "role": f"revision consult {attempt}", "message": librarian_msg})

    # ── Turn 3: Reviewer makes the final, machine-readable call ──────────────
    _progress(f"Consultation (revision {attempt}) — Reviewer is making the final call...")
    decision_prompt = (
        f"You are the Reviewer agent for bahAI Workforce. {CARD_QUOTE_SOURCING_NOTE}\n\n"
        f"You scored this quote card {review.get('overall', '?')}/10 for theme \"{theme}\". "
        f"Your own concerns:\n{weak_notes}\n\n"
        f"The Artist said:\n{artist_msg}\n\nThe Librarian said:\n{librarian_msg}"
        f"{history_block}\n\n"
        f"{CONSULTATION_SCRIPTURE['round2_response']} Weigh the team's input honestly — "
        "especially the Librarian, who has the clearest view of what the pool can actually "
        "supply. If the Librarian says to keep the quote and you still want to requote, you "
        "must name a SPECIFIC, NEW reason the Librarian didn't already address — 'archaic "
        "language' is not a new reason if the entire pool is archaic scripture. If you cannot "
        "name a genuinely new, achievable fix, choose \"ship\" rather than repeat a request "
        "the team has already told you the pool can't satisfy.\n\n"
        "Decide ONE next action (machine-executed — choose exactly one):\n"
        "  \"ship\"    — the card is ready as-is, or no achievable fix remains.\n"
        "  \"requote\" — a genuinely different, available passage would help: put the search "
        "phrase in action_guidance.\n"
        "  \"repaint\" — the artwork is the weakness: the pipeline regenerates it; put the "
        "imperative change in action_guidance.\n\n"
        "Return ONLY this JSON. If you overrule the Librarian's own recommendation, team_note "
        "must start with the literal words REOPENING LIBRARIAN'S READ: followed by the "
        "specific new reason.\n"
        "{\n"
        '  "action": "ship",\n'
        '  "action_guidance": "empty string when shipping; otherwise the concrete steer",\n'
        '  "team_note": "one or two sentences on how the team\'s input shaped this call"\n'
        "}"
    )
    action, action_guidance, team_note = fallback_action, fallback_guidance, ""
    try:
        raw = call_llm(
            "reviewer", [{"role": "user", "content": decision_prompt}],
            temperature=0.2, max_tokens=320, json_mode=True,
        ).strip()
        decision = json.loads(raw)
        candidate_action = str(decision.get("action") or "").strip().lower()
        if candidate_action in ("ship", "requote", "repaint"):
            action = candidate_action
            action_guidance = str(decision.get("action_guidance") or "").strip()
        team_note = str(decision.get("team_note") or "").strip()
    except Exception:
        pass  # keep the Reviewer's own scored action/guidance as the fallback

    summary = f"Action: {action}."
    if action_guidance:
        summary += f" {action_guidance}"
    if team_note:
        summary += f"\n{team_note}"
    _emit({"agent": "Reviewer", "role": f"revision consult {attempt} — final call", "message": summary})

    return {"transcript": transcript, "action": action, "action_guidance": action_guidance}


# X posts have no "book-only" restriction (they draw from the general 7-text
# index, same as bookmarks) — but the same "never invent, never paraphrase
# into modern language" discipline applies, so this is card sourcing note's
# sibling rather than a copy of it.
X_POST_QUOTE_SOURCING_NOTE = (
    "This post's quote must be a VERBATIM excerpt retrieved from the indexed Bahá'í writings, "
    "authored by Bahá'u'lláh, 'Abdu'l-Bahá, Shoghi Effendi, The Báb, or the Universal House of "
    "Justice — selected BY INDEX from retrieved candidates, never invented or retyped by a model. "
    "A \"requote\" surfaces a different retrieved passage via a new search phrase; it can never "
    "invent new text or paraphrase into modern language."
)

# The "without an authoritative quote" mode's sibling note: the same retrieved
# passages are shown to the team, but only as background inspiration — the
# tweet must never quote or attribute anything to the retrieved authors.
X_POST_INSPIRATION_SOURCING_NOTE = (
    "This post does NOT quote anyone directly — it's an original reflection inspired by "
    "retrieved Bahá'í passages, shown to the team as background only. A \"requote\" surfaces "
    "different inspiration passages via a new search phrase; the tweet must still never quote "
    "or attribute specific words to any author."
)


def run_x_post_revision_consultation(
    topic: str,
    tweet: str,
    quote: str,
    author: str,
    image_path: str,
    citations: list,
    review: dict,
    progress=None,
    on_turn=None,
    attempt: int = 1,
    history: list = None,
    include_quote: bool = True,
) -> dict:
    """
    Post-score revision for X posts — the whole team weighs in on what's
    actually wrong, not just the Reviewer alone (same discipline as
    run_card_revision_consultation), extended with a fourth lever: unlike a
    quote card, a tweet has its own wording separate from the quote and image,
    so the Scribe can fix WORDING alone without touching either.

    Turn 1 (Artist): is the ARTWORK the problem?
    Turn 2 (Librarian): is the QUOTE the problem — mismatched with the topic
    or the image — and does a better verified passage exist?
    Turn 3 (Reviewer): the final, machine-readable call among "ship" /
    "revise_text" / "repaint" / "requote", weighing what the team said. If it
    overrules the Artist's or Librarian's read, it must start with "REOPENING
    ARTIST'S READ: ..." or "REOPENING LIBRARIAN'S READ: ..." followed by the
    specific new reason — same override discipline as the card pipeline.

    This exists because a prior version let the Reviewer recommend "revise
    the image" round after round with no way for that to actually happen —
    the Scribe can only ever touch tweet text, so the score plateaued with no
    achievable fix (observed live, 2026-07: a quote about newspapers paired
    with an unrelated nature image, stuck at 5.2/10 for two full revision
    rounds). Routing to the agent who can actually act closes that dead end.

    Returns {transcript, action, action_guidance}. On any parse failure the
    action falls back to "revise_text" (the previously-only lever) rather
    than silently shipping an unreviewed decision.
    """
    transcript = []

    def _progress(msg: str):
        if progress:
            progress(msg)

    def _emit(entry: dict):
        transcript.append(entry)
        if on_turn:
            on_turn(entry)

    scores = review.get("scores") or {}
    weak = [c for c, v in scores.items() if isinstance(v, dict) and v.get("score", 10) < 7]
    weak_notes = "\n".join(f"- {c}: {scores[c].get('note', '')}" for c in weak) or "(no principle scored below 7)"
    fallback_action = "revise_text"

    # Deterministic mechanical checks (agents.x_post.review_tweet's `checks`)
    # are a DIFFERENT signal from the constitution scores above — a pure
    # length/format failure is a text problem no repaint or requote can ever
    # fix, but the team can't know that unless it's told explicitly. Without
    # this, a tweet that's simply too long could burn a whole repaint or
    # requote round on a problem only the Scribe can solve (observed live,
    # 2026-07: a 296-character tweet stayed over the limit through a repaint
    # AND a requote before finally getting shortened).
    mech_failures = [
        f"{k}: {v.get('detail', '')}" for k, v in (review.get("checks") or {}).items()
        if isinstance(v, dict) and not v.get("ok", True)
    ]
    mech_block = (
        "\n\nDETERMINISTIC MECHANICAL CHECKS (never LLM-judged, always trust these) — any "
        "failure here is a pure TEXT/FORMAT problem that ONLY \"revise_text\" can fix; never "
        "route a mechanical failure to \"repaint\" or \"requote\":\n"
        + "\n".join(f"  - {f}" for f in mech_failures) + "\n"
    ) if mech_failures else ""

    history_lines = []
    for h in (history or []):
        delta = ("improved" if h["overall"] > h["prev_overall"]
                 else "no change" if h["overall"] == h["prev_overall"] else "got worse")
        history_lines.append(
            f'  Attempt {h["attempt"]}: {h["action"]} — "{h["guidance"][:100]}" '
            f'-> {h["overall"]}/10 ({delta} from {h["prev_overall"]}/10)'
        )
    history_block = (
        "\n\nPRIOR REVISION ATTEMPTS THIS RUN:\n" + "\n".join(history_lines) + "\n"
        "If a similar ask was already tried and it didn't help, that is real evidence — "
        "don't repeat it a third time hoping for a different result.\n"
    ) if history_lines else ""

    # ── Turn 1: Artist reacts to the Reviewer's concerns ─────────────────────
    _progress(f"Consultation (revision {attempt}) — Artist is weighing in on the Reviewer's score...")
    artist_prompt = (
        f"You are the Artist agent for bahAI Workforce. The Reviewer just scored this draft tweet "
        f"{review.get('overall', '?')}/10 for topic \"{topic}\". The tweet:\n{tweet}\n\n"
        f"Weak points it flagged:\n{weak_notes}"
        f"{mech_block}\n\n"
        "Answer in exactly 2 bullet points, one line each, no preamble:\n"
        "- Do you believe the ARTWORK is the actual problem, or is it the tweet's text/quote? "
        "Say which, plainly.\n"
        "- If it's the artwork, the one concrete visual change you'd make; if not, say 'not the artwork'.\n\n"
        "Terse and concrete. Under 55 words total."
    )
    try:
        artist_msg = call_grok_vision(image_path, artist_prompt, temperature=0.6, max_tokens=200).strip()
    except Exception as e:
        artist_msg = f"(Artist unavailable — {e})"
    _emit({"agent": "Artist", "role": f"revision consult {attempt}", "message": artist_msg})

    # ── Turn 2: Librarian reacts, offers an alternative quote if relevant ────
    sourcing_note = X_POST_QUOTE_SOURCING_NOTE if include_quote else X_POST_INSPIRATION_SOURCING_NOTE
    _progress(f"Consultation (revision {attempt}) — Librarian is weighing in on the {'quote' if include_quote else 'inspiration'} fit...")
    other_passages = "\n".join(
        f'  [{i + 1}] "{c.get("text", "").strip()[:180]}" — {c.get("source", "")}'
        for i, c in enumerate(citations[:3])
    ) or "(none retrieved)"
    locked_block = (
        f"The locked quote is:\n\"{quote}\"\n— {author}\n\n"
        if include_quote else
        "No quote is locked — the tweet is an original reflection; the passages below are "
        "background inspiration only, never to be quoted or attributed in the tweet.\n\n"
    )
    librarian_prompt = (
        f"You are the Librarian agent for bahAI Workforce. {sourcing_note}\n\n"
        f"{locked_block}"
        f"The Reviewer's weak points:\n{weak_notes}"
        f"{mech_block}\n"
        f"Other candidate passages already retrieved for this topic (a differently-worded search "
        f"can surface others, but never anything invented):\n{other_passages}"
        f"{history_block}\n"
        "Answer in exactly 2 bullet points, one line each, no preamble:\n"
        + ("- Is the QUOTE itself the problem (fit with the topic or the image, mismatch, length)? "
           "Say plainly.\n"
           if include_quote else
           "- Is the INSPIRATION itself the problem (fit with the topic or the image)? Say plainly.\n") +
        "- If yes and a real alternative exists above, name it by number and why in one clause; "
        "otherwise suggest a new search phrase, or say 'keep it' if nothing would help.\n\n"
        "Terse. Under 55 words total."
    )
    try:
        librarian_msg = call_llm(
            "librarian", [{"role": "user", "content": librarian_prompt}],
            temperature=0.3, max_tokens=220,
        ).strip()
    except Exception as e:
        librarian_msg = f"(Librarian unavailable — {e})"
    _emit({"agent": "Librarian", "role": f"revision consult {attempt}", "message": librarian_msg})

    # ── Turn 3: Reviewer makes the final, machine-readable call ──────────────
    _progress(f"Consultation (revision {attempt}) — Reviewer is making the final call...")
    decision_prompt = (
        f"You are the Reviewer agent for bahAI Workforce. {sourcing_note}\n\n"
        f"You scored this draft tweet {review.get('overall', '?')}/10 for topic \"{topic}\". "
        f"Your own concerns:\n{weak_notes}"
        f"{mech_block}\n"
        f"The Artist said:\n{artist_msg}\n\nThe Librarian said:\n{librarian_msg}"
        f"{history_block}\n\n"
        "Weigh the team's input honestly. If you overrule the Artist's or Librarian's read, you "
        "must name a SPECIFIC, NEW reason they didn't already address. If you cannot name a "
        "genuinely new, achievable fix, choose \"ship\" rather than repeat a request the team has "
        "already told you won't help.\n\n"
        "Decide ONE next action (machine-executed — choose exactly one):\n"
        "  \"ship\"        — the tweet is ready as-is, or no achievable fix remains.\n"
        "  \"revise_text\" — the WORDING is the weakness (Scribe rewrites; the quote and image "
        "stay unchanged): put the concrete instruction in action_guidance.\n"
        "  \"repaint\"     — the ARTWORK is the weakness: the pipeline regenerates it; put the "
        "imperative change in action_guidance.\n"
        "  \"requote\"     — a different verified passage would fit the topic or image better: "
        "put the search phrase in action_guidance.\n\n"
        "Return ONLY this JSON. If you overrule the team, team_note must start with the literal "
        "words REOPENING ARTIST'S READ: or REOPENING LIBRARIAN'S READ: followed by the specific "
        "new reason.\n"
        "{\n"
        '  "action": "revise_text",\n'
        '  "action_guidance": "empty string when shipping; otherwise the concrete steer",\n'
        '  "team_note": "one or two sentences on how the team\'s input shaped this call"\n'
        "}"
    )
    action, action_guidance, team_note = fallback_action, "", ""
    try:
        raw = call_llm(
            "reviewer", [{"role": "user", "content": decision_prompt}],
            temperature=0.2, max_tokens=320, json_mode=True,
        ).strip()
        decision = json.loads(raw)
        candidate_action = str(decision.get("action") or "").strip().lower()
        if candidate_action in ("ship", "revise_text", "repaint", "requote"):
            action = candidate_action
            action_guidance = str(decision.get("action_guidance") or "").strip()
        team_note = str(decision.get("team_note") or "").strip()
    except Exception:
        pass  # keep the safe fallback ("revise_text") — a broken decision must never silently ship

    summary = f"Action: {action}."
    if action_guidance:
        summary += f" {action_guidance}"
    if team_note:
        summary += f"\n{team_note}"
    _emit({"agent": "Reviewer", "role": f"revision consult {attempt} — final call", "message": summary})

    return {"transcript": transcript, "action": action, "action_guidance": action_guidance}
