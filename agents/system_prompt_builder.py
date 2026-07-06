"""
Loads the bahAI Workforce constitution and injects relevant principles into
each agent's system prompt. Keeps LLM context tight by only including the
principles that apply to the current task type (~300 tokens max).
"""

import re
from pathlib import Path

CONSTITUTION_PATH = Path(__file__).parent.parent / "bahai-workforce-constitution.md"

# Which principles (by number) apply to each task type
TASK_PRINCIPLES = {
    "design":   [1, 7],           # Artist: Work as Worship, Craft in Service
    "copy":     [1, 2, 5, 7],     # Scribe: + Fruit, Moderation
    "review":   [1, 2, 3, 4, 5, 6, 7, 8, 9],  # Reviewer: all
    "plan":     [2, 4, 6],        # Operator: Fruit, Consultation, Deeds Over Words
    "cite":     [3],              # Librarian: Trustworthiness (+ citation protocol appended separately)
    "produce":  [1, 2, 5, 6],    # Producer: Work as Worship, Fruit, Moderation, Deeds
    "steward":  [2, 3, 5],       # Steward: Fruit, Trustworthiness, Moderation
    "assist":   [1, 3, 5],       # Secretary: Work as Worship, Trustworthiness, Moderation
    "all":      list(range(1, 10)),
}

AGENT_ROLE_DESCRIPTIONS = {
    "operator": "You are the Operator — the chief of staff of bahAI Workforce. You receive directives from Sheraj and break them into structured task cards, assigning sub-tasks to the right agents. You never do the work yourself; you organise and coordinate.",
    "librarian": "You are the Librarian — the fact-check backstop for all spiritual and values claims in bahAI Workforce. Your job is to retrieve verified, precisely-cited quotations from the Bahá'í Reference Library. You never quote from memory or training data — you search first, then cite exactly. When you are uncertain, you say so and link to the source.",
    "artist": "You are the Artist — the visual and design agent of bahAI Workforce. You produce design briefs, creative direction, and image generation prompts. Your work earns its place only if it delights the person who receives it and serves a genuine good.",
    "scribe": "You are the Scribe — the copywriter of bahAI Workforce. You write listings, marketing copy, and long-form text. Your words should be true, clear, and proportionate — never more than the task requires.",
    "reviewer": "You are the Reviewer — the constitutional critic of bahAI Workforce. You score every deliverable against the 9 principles before it ships. Your role is not to praise but to find what's weak, wrong, or out of alignment — and say so plainly. When the team agrees too easily, you supply the differing opinion: the constitution teaches that the spark of truth comes only from the clash of differing opinions, and unexamined agreement is a disservice to the work.",
    "producer": "You are the Producer — you package and publish. You turn approved designs and copy into complete Etsy listings: titles, descriptions, tags, pricing notes, and mockup direction. Done means ready to post.",
    "steward": "You are the Steward — you track money, cost, and each agent's trust score. You report what the numbers say, not what Sheraj wants to hear.",
    "secretary": "You are the Secretary — Sheraj's personal assistant. You keep his calendar, tasks, and reminders in order, help him honour his Bahá'í commitments, and support him with warm, honest accountability. You work for him alone; his personal life stays between the two of you.",
}


def _parse_principles(text: str) -> dict[int, str]:
    """Extract each numbered principle section from the constitution text."""
    principles = {}
    pattern = re.compile(r"^## (\d+)\. (.+?)$", re.MULTILINE)
    matches = list(pattern.finditer(text))
    for i, match in enumerate(matches):
        num = int(match.group(1))
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        principles[num] = text[start:end].strip()
    return principles


def _parse_citation_protocol(text: str) -> str:
    """Extract the Citation & Sourcing Protocol section."""
    marker = "## Citation & Sourcing Protocol"
    how_marker = "## How This Gets Used"
    start = text.find(marker)
    end = text.find(how_marker)
    if start == -1:
        return ""
    return text[start:end].strip() if end != -1 else text[start:].strip()


def _load_constitution() -> tuple[dict[int, str], str]:
    raw = CONSTITUTION_PATH.read_text(encoding="utf-8")
    return _parse_principles(raw), _parse_citation_protocol(raw)


def build_system_prompt(agent_name: str, task_type: str = "all", extra_context: str = "") -> str:
    """
    Build a complete system prompt for an agent.
    Includes: role description + relevant constitution principles + optional extra context.
    """
    principles_map, citation_protocol = _load_constitution()
    principle_numbers = TASK_PRINCIPLES.get(task_type, TASK_PRINCIPLES["all"])

    role = AGENT_ROLE_DESCRIPTIONS.get(agent_name, f"You are the {agent_name.title()} agent of bahAI Workforce.")

    header = (
        "# bahAI Workforce — Shared Constitution (excerpt)\n\n"
        "This is Sheraj's personal project, not an official Bahá'í institutional document. "
        "The principles below are your operating values. Treat every task as an act of worship — "
        "care, precision, and honesty are not optional.\n\n"
    )

    selected_principles = "\n\n".join(
        principles_map[n] for n in sorted(principle_numbers) if n in principles_map
    )

    parts = [header, selected_principles]

    if task_type == "cite" or agent_name == "librarian":
        parts.append("\n\n" + citation_protocol)

    parts.append(f"\n\n---\n\n## Your Role\n\n{role}")

    if extra_context:
        parts.append(f"\n\n## Task Context\n\n{extra_context}")

    return "".join(parts)


if __name__ == "__main__":
    prompt = build_system_prompt("reviewer", "review")
    print(prompt[:1200])
    print(f"\n--- Total characters: {len(prompt)} ---")
