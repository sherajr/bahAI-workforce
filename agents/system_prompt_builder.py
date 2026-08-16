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
    "cite":     [3],              # Librarian: Trustworthiness (+ citation protocol appended separately)
    "steward":  [2, 3, 5],       # Steward: Fruit, Trustworthiness, Moderation
    "assist":   [1, 3, 5],       # Secretary: Work as Worship, Trustworthiness, Moderation
    "all":      list(range(1, 10)),
}

AGENT_ROLE_DESCRIPTIONS = {
    "librarian": "You are the Librarian — the fact-check backstop for all spiritual and values claims in bahAI Workforce. Your job is to retrieve verified, precisely-cited quotations from the Bahá'í Reference Library. You never quote from memory or training data — you search first, then cite exactly. When you are uncertain, you say so and link to the source.",
    "artist": "You are the Artist — the visual and design agent of bahAI Workforce. You produce design briefs, creative direction, and image generation prompts. Your work earns its place only if it delights the person who receives it and serves a genuine good.",
    "scribe": "You are the Scribe — the copywriter of bahAI Workforce. You write listings, marketing copy, and long-form text. Your words should be true, clear, and proportionate — never more than the task requires.",
    "reviewer": "You are the Reviewer — the constitutional critic of bahAI Workforce. You score every deliverable against the 9 principles before it ships. Your role is not to praise but to find what's weak, wrong, or out of alignment — and say so plainly. When the team agrees too easily, you supply the differing opinion: the constitution teaches that the spark of truth comes only from the clash of differing opinions, and unexamined agreement is a disservice to the work.",
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


def _goal_steer(agent_name: str) -> str:
    """
    The one short line of team-goal steering this agent carries (Colony tab,
    owner decision 2026-08-13: a goal must actually shape the work, not just
    decorate a card).

    Injected HERE, in the single place every agent prompt is assembled, so a
    goal reaches pipeline runs and chat alike and can never be applied in one
    path but forgotten in another. Two properties are load-bearing:

      - It is ONE line, already hard-capped at colony.GOAL_NOTE_MAX_CHARS.
        Most agents run on local Qwen, and hard rule 1 exists because long
        prompts made Qwen burn its whole budget and return "{".
      - It fails OPEN. A missing colony table or an unreadable database must
        degrade to "no goal set", never break a pipeline run that would
        otherwise have succeeded — steering is an enhancement, not a
        dependency.
    """
    try:
        from agents.colony import goal_note_for_agent
        note = goal_note_for_agent(agent_name)
    except Exception:
        return ""
    if not note:
        return ""
    return ("\n\n## Sheraj's current goal for your team\n\n"
            f"{note}\n\n"
            "Let it shape what you choose and prioritise. It does not override the "
            "principles above, and it is never a reason to overstate what you have done.")


def _instructions_steer(agent_name: str) -> str:
    """
    Sheraj's standing instructions for this agent, injected in the SAME single
    place as the goal note above and for the same reason (rule 39).

    Until 2026-08-14 these were only added to Colony chat prompts, so a brief
    Sheraj (or Abigail on his behalf) wrote for an agent never reached the
    pipeline work it was written about. They are already hard-capped at
    colony.INSTRUCTIONS_NOTE_MAX_CHARS on read, and this fails OPEN for the
    same reason the goal note does: steering is an enhancement, never a
    dependency of a run that would otherwise have succeeded.
    """
    try:
        from agents.colony import instructions_note_for_agent
        note = instructions_note_for_agent(agent_name)
    except Exception:
        return ""
    if not note:
        return ""
    return ("\n\n## Sheraj's standing instructions for you\n\n"
            f"{note}\n\n"
            "Follow them in this task. They do not override the principles above, "
            "and they are never a reason to claim more than you actually did.")


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
    parts.append(_goal_steer(agent_name))
    parts.append(_instructions_steer(agent_name))

    if extra_context:
        parts.append(f"\n\n## Task Context\n\n{extra_context}")

    return "".join(parts)


if __name__ == "__main__":
    prompt = build_system_prompt("reviewer", "review")
    print(prompt[:1200])
    print(f"\n--- Total characters: {len(prompt)} ---")
