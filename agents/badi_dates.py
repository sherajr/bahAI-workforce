"""
Hand-curated Badi' (Baha'i) calendar dates, 2026-2028 (B.E. 182 tail - 185).

HARD RULE (CLAUDE.md / secretary briefing): Feast and Holy Day dates come from
this table and ONLY this table - never from an LLM's memory. A hallucinated
Holy Day date is a Trustworthiness failure in the one domain this project
exists to honor.

Sources (fetched 2026-07-05):
  [A] https://www.bahai.org/action/devotional-life/calendar  (official; full
      183 B.E. month starts + Holy Days, exactly as published)
  [B] https://en.wikipedia.org/wiki/Bah%C3%A1%CA%BC%C3%AD_Holy_Days ("Holy
      Days of the Baha'i calendar" table, sourced to the Universal House of
      Justice tables) - 2027/2028 Holy Days incl. the lunar Twin Birthdays
  [C] https://en.wikipedia.org/wiki/Bah%C3%A1%CA%BC%C3%AD_calendar (Naw-Ruz
      dates per UHJ tables: 2025=Mar 20, 2026=Mar 21, 2027=Mar 21, 2028=Mar 20)
  [D] Derived: Feast (month-start) dates for 182/184/185 B.E. computed as
      Naw-Ruz + 19*(month-1), with 'Ala' = following Naw-Ruz - 19. The 183
      B.E. derivation reproduces [A] exactly, byte for byte.

Verification status: cross-checked across [A][B][C] and internally consistent
(2026 Holy Days match bahai.org exactly; every year's months + Ayyam-i-Ha sum
to the next Naw-Ruz). Per the briefing, SHERAJ must still spot-check an
upcoming Feast and Holy Day against bahai.org before Phase 2 is considered
shipped. If a requested date is outside 2026-2028, the Secretary says so and
links [A] - she never guesses.

Extending this table = add entries + sources here, human-verify, done. No
code change needed.
"""

from datetime import date, timedelta

OFFICIAL_CALENDAR_URL = "https://www.bahai.org/action/devotional-life/calendar"

# Year covered by this table (inclusive). Outside this range the Secretary
# must decline and link OFFICIAL_CALENDAR_URL.
COVERAGE_START = date(2026, 1, 1)
COVERAGE_END = date(2028, 12, 31)

# --- The 11 Holy Days ---------------------------------------------------------
# work_suspended: the nine Holy Days on which work is suspended.
# Observance runs sunset-to-sunset; dates below are the Gregorian calendar day
# as published by [A]/[B].

HOLY_DAYS = [
    # 2026 (183 B.E.) — source [A] (official), confirmed by [B]
    {"date": date(2026, 3, 21),  "name": "Naw-Rúz (Bahá'í New Year)",     "work_suspended": True,  "source": "A"},
    {"date": date(2026, 4, 21),  "name": "First Day of Riḍván",           "work_suspended": True,  "source": "A"},
    {"date": date(2026, 4, 29),  "name": "Ninth Day of Riḍván",           "work_suspended": True,  "source": "A"},
    {"date": date(2026, 5, 2),   "name": "Twelfth Day of Riḍván",         "work_suspended": True,  "source": "A"},
    {"date": date(2026, 5, 24),  "name": "Declaration of the Báb",        "work_suspended": True,  "source": "A"},
    {"date": date(2026, 5, 29),  "name": "Ascension of Bahá'u'lláh",      "work_suspended": True,  "source": "A"},
    {"date": date(2026, 7, 10),  "name": "Martyrdom of the Báb",          "work_suspended": True,  "source": "A"},
    {"date": date(2026, 11, 10), "name": "Birth of the Báb",              "work_suspended": True,  "source": "A"},
    {"date": date(2026, 11, 11), "name": "Birth of Bahá'u'lláh",          "work_suspended": True,  "source": "A"},
    {"date": date(2026, 11, 26), "name": "Day of the Covenant",           "work_suspended": False, "source": "A"},
    {"date": date(2026, 11, 28), "name": "Ascension of 'Abdu'l-Bahá",     "work_suspended": False, "source": "A"},
    # 2027 (184 B.E.) — source [B]
    {"date": date(2027, 3, 21),  "name": "Naw-Rúz (Bahá'í New Year)",     "work_suspended": True,  "source": "B"},
    {"date": date(2027, 4, 21),  "name": "First Day of Riḍván",           "work_suspended": True,  "source": "B"},
    {"date": date(2027, 4, 29),  "name": "Ninth Day of Riḍván",           "work_suspended": True,  "source": "B"},
    {"date": date(2027, 5, 2),   "name": "Twelfth Day of Riḍván",         "work_suspended": True,  "source": "B"},
    {"date": date(2027, 5, 24),  "name": "Declaration of the Báb",        "work_suspended": True,  "source": "B"},
    {"date": date(2027, 5, 29),  "name": "Ascension of Bahá'u'lláh",      "work_suspended": True,  "source": "B"},
    {"date": date(2027, 7, 10),  "name": "Martyrdom of the Báb",          "work_suspended": True,  "source": "B"},
    {"date": date(2027, 10, 30), "name": "Birth of the Báb",              "work_suspended": True,  "source": "B"},
    {"date": date(2027, 10, 31), "name": "Birth of Bahá'u'lláh",          "work_suspended": True,  "source": "B"},
    {"date": date(2027, 11, 26), "name": "Day of the Covenant",           "work_suspended": False, "source": "B"},
    {"date": date(2027, 11, 28), "name": "Ascension of 'Abdu'l-Bahá",     "work_suspended": False, "source": "B"},
    # 2028 (185 B.E.) — source [B]
    {"date": date(2028, 3, 20),  "name": "Naw-Rúz (Bahá'í New Year)",     "work_suspended": True,  "source": "B"},
    {"date": date(2028, 4, 20),  "name": "First Day of Riḍván",           "work_suspended": True,  "source": "B"},
    {"date": date(2028, 4, 28),  "name": "Ninth Day of Riḍván",           "work_suspended": True,  "source": "B"},
    {"date": date(2028, 5, 1),   "name": "Twelfth Day of Riḍván",         "work_suspended": True,  "source": "B"},
    {"date": date(2028, 5, 23),  "name": "Declaration of the Báb",        "work_suspended": True,  "source": "B"},
    {"date": date(2028, 5, 28),  "name": "Ascension of Bahá'u'lláh",      "work_suspended": True,  "source": "B"},
    {"date": date(2028, 7, 9),   "name": "Martyrdom of the Báb",          "work_suspended": True,  "source": "B"},
    {"date": date(2028, 10, 19), "name": "Birth of the Báb",              "work_suspended": True,  "source": "B"},
    {"date": date(2028, 10, 20), "name": "Birth of Bahá'u'lláh",          "work_suspended": True,  "source": "B"},
    {"date": date(2028, 11, 25), "name": "Day of the Covenant",           "work_suspended": False, "source": "B"},
    {"date": date(2028, 11, 27), "name": "Ascension of 'Abdu'l-Bahá",     "work_suspended": False, "source": "B"},
]

# --- Nineteen Day Feasts (Badí' month starts) ----------------------------------
# The Feast is held on the FIRST DAY of each Badí' month (observance may begin
# at sunset the evening before). 183 B.E. is the official list [A]; the other
# years are derived [D] from the UHJ Naw-Rúz dates [C].

FEASTS = [
    # 182 B.E. tail (months falling in early 2026) — [D] from Naw-Rúz 2025-03-20
    {"date": date(2026, 1, 18),  "month": "Sulṭán (Sovereignty)", "source": "D"},
    {"date": date(2026, 2, 6),   "month": "Mulk (Dominion)",      "source": "D"},
    {"date": date(2026, 3, 2),   "month": "'Alá' (Loftiness)",    "source": "D"},
    # 183 B.E. — official [A]
    {"date": date(2026, 3, 21),  "month": "Bahá (Splendour)",     "source": "A"},
    {"date": date(2026, 4, 9),   "month": "Jalál (Glory)",        "source": "A"},
    {"date": date(2026, 4, 28),  "month": "Jamál (Beauty)",       "source": "A"},
    {"date": date(2026, 5, 17),  "month": "'Aẓamat (Grandeur)",   "source": "A"},
    {"date": date(2026, 6, 5),   "month": "Núr (Light)",          "source": "A"},
    {"date": date(2026, 6, 24),  "month": "Raḥmat (Mercy)",       "source": "A"},
    {"date": date(2026, 7, 13),  "month": "Kalimát (Words)",      "source": "A"},
    {"date": date(2026, 8, 1),   "month": "Kamál (Perfection)",   "source": "A"},
    {"date": date(2026, 8, 20),  "month": "Asmá' (Names)",        "source": "A"},
    {"date": date(2026, 9, 8),   "month": "'Izzat (Might)",       "source": "A"},
    {"date": date(2026, 9, 27),  "month": "Mashíyyat (Will)",     "source": "A"},
    {"date": date(2026, 10, 16), "month": "'Ilm (Knowledge)",     "source": "A"},
    {"date": date(2026, 11, 4),  "month": "Qudrat (Power)",       "source": "A"},
    {"date": date(2026, 11, 23), "month": "Qawl (Speech)",        "source": "A"},
    {"date": date(2026, 12, 12), "month": "Masá'il (Questions)",  "source": "A"},
    {"date": date(2026, 12, 31), "month": "Sharaf (Honour)",      "source": "A"},
    {"date": date(2027, 1, 19),  "month": "Sulṭán (Sovereignty)", "source": "A"},
    {"date": date(2027, 2, 7),   "month": "Mulk (Dominion)",      "source": "A"},
    {"date": date(2027, 3, 2),   "month": "'Alá' (Loftiness)",    "source": "A"},
    # 184 B.E. — [D] from Naw-Rúz 2027-03-21 [C]
    {"date": date(2027, 3, 21),  "month": "Bahá (Splendour)",     "source": "D"},
    {"date": date(2027, 4, 9),   "month": "Jalál (Glory)",        "source": "D"},
    {"date": date(2027, 4, 28),  "month": "Jamál (Beauty)",       "source": "D"},
    {"date": date(2027, 5, 17),  "month": "'Aẓamat (Grandeur)",   "source": "D"},
    {"date": date(2027, 6, 5),   "month": "Núr (Light)",          "source": "D"},
    {"date": date(2027, 6, 24),  "month": "Raḥmat (Mercy)",       "source": "D"},
    {"date": date(2027, 7, 13),  "month": "Kalimát (Words)",      "source": "D"},
    {"date": date(2027, 8, 1),   "month": "Kamál (Perfection)",   "source": "D"},
    {"date": date(2027, 8, 20),  "month": "Asmá' (Names)",        "source": "D"},
    {"date": date(2027, 9, 8),   "month": "'Izzat (Might)",       "source": "D"},
    {"date": date(2027, 9, 27),  "month": "Mashíyyat (Will)",     "source": "D"},
    {"date": date(2027, 10, 16), "month": "'Ilm (Knowledge)",     "source": "D"},
    {"date": date(2027, 11, 4),  "month": "Qudrat (Power)",       "source": "D"},
    {"date": date(2027, 11, 23), "month": "Qawl (Speech)",        "source": "D"},
    {"date": date(2027, 12, 12), "month": "Masá'il (Questions)",  "source": "D"},
    {"date": date(2027, 12, 31), "month": "Sharaf (Honour)",      "source": "D"},
    {"date": date(2028, 1, 19),  "month": "Sulṭán (Sovereignty)", "source": "D"},
    {"date": date(2028, 2, 7),   "month": "Mulk (Dominion)",      "source": "D"},
    {"date": date(2028, 3, 1),   "month": "'Alá' (Loftiness)",    "source": "D"},
    # 185 B.E. — [D] from Naw-Rúz 2028-03-20 [C]
    {"date": date(2028, 3, 20),  "month": "Bahá (Splendour)",     "source": "D"},
    {"date": date(2028, 4, 8),   "month": "Jalál (Glory)",        "source": "D"},
    {"date": date(2028, 4, 27),  "month": "Jamál (Beauty)",       "source": "D"},
    {"date": date(2028, 5, 16),  "month": "'Aẓamat (Grandeur)",   "source": "D"},
    {"date": date(2028, 6, 4),   "month": "Núr (Light)",          "source": "D"},
    {"date": date(2028, 6, 23),  "month": "Raḥmat (Mercy)",       "source": "D"},
    {"date": date(2028, 7, 12),  "month": "Kalimát (Words)",      "source": "D"},
    {"date": date(2028, 7, 31),  "month": "Kamál (Perfection)",   "source": "D"},
    {"date": date(2028, 8, 19),  "month": "Asmá' (Names)",        "source": "D"},
    {"date": date(2028, 9, 7),   "month": "'Izzat (Might)",       "source": "D"},
    {"date": date(2028, 9, 26),  "month": "Mashíyyat (Will)",     "source": "D"},
    {"date": date(2028, 10, 15), "month": "'Ilm (Knowledge)",     "source": "D"},
    {"date": date(2028, 11, 3),  "month": "Qudrat (Power)",       "source": "D"},
    {"date": date(2028, 11, 22), "month": "Qawl (Speech)",        "source": "D"},
    {"date": date(2028, 12, 11), "month": "Masá'il (Questions)",  "source": "D"},
    {"date": date(2028, 12, 30), "month": "Sharaf (Honour)",      "source": "D"},
]


def events_between(start: date, end: date) -> list[dict]:
    """
    Feasts + Holy Days in [start, end], sorted. Each item:
    {date, name, kind: 'holy_day'|'feast', work_suspended, in_coverage}.
    Callers must check covered() for ranges outside the table.
    """
    out = []
    for hd in HOLY_DAYS:
        if start <= hd["date"] <= end:
            out.append({"date": hd["date"], "name": hd["name"], "kind": "holy_day",
                        "work_suspended": hd["work_suspended"]})
    for f in FEASTS:
        if start <= f["date"] <= end:
            out.append({"date": f["date"], "name": f"Nineteen Day Feast — {f['month']}",
                        "kind": "feast", "work_suspended": False})
    return sorted(out, key=lambda e: e["date"])


def covered(day: date) -> bool:
    """True when `day` falls inside the hand-verified table's coverage."""
    return COVERAGE_START <= day <= COVERAGE_END


def _self_check():
    """Internal consistency: every year's 19 months + Ayyám-i-Há reach the next Naw-Rúz."""
    feasts = sorted(f["date"] for f in FEASTS)
    for a, b in zip(feasts, feasts[1:]):
        gap = (b - a).days
        assert gap in (19, 23, 24), f"month gap {gap} between {a} and {b}"
    naw_ruz = [hd["date"] for hd in HOLY_DAYS if hd["name"].startswith("Naw-Rúz")]
    for nr in naw_ruz:
        assert nr in feasts, f"Naw-Rúz {nr} is not a month start"
    return True


if __name__ == "__main__":
    _self_check()
    from datetime import date as _d
    print("self-check OK;", len(HOLY_DAYS), "holy days,", len(FEASTS), "feasts")
    for e in events_between(_d(2026, 7, 1), _d(2026, 12, 31)):
        print(e["date"], "-", e["name"])
