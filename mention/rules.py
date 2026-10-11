"""Parse mention-market questions and count qualifying mentions in a transcript.

Counting follows Polymarket's Event Mentions Contract
(market_products/Mentions/Event+Mentions+Contract+DeFi.pdf):
case-insensitive; plurals and possessives count; open compounds and
hyphenated constructs count; tense/derivation changes do not; expanded
acronyms do not; digits and number words are interchangeable; a full-name
mention ("Joe Biden") counts once for "Joe" or "Biden".
"""
import re

NUMBERS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
           "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen",
           "eighteen", "nineteen", "twenty"]
NUM_ALT = {**{w: f"(?:{w}|{i})" for i, w in enumerate(NUMBERS)},
           **{str(i): f"(?:{i}|{w})" for i, w in enumerate(NUMBERS)}}


def parse_question(q):
    """Return {"terms": [...], "threshold": n} or None for non-term markets ("not air")."""
    quoted = re.findall(r'["“”]([^"“”]+)["“”]', q)
    # "AI / Artificial Intelligence" inside one pair of quotes lists alternatives.
    terms = [t.strip() for t in quoted for t in re.split(r"\s+/\s+", t) if t.strip()]
    if not terms:
        return None
    if re.search(r"≤|\d+\s*[–-]\s*\d+ times", q):
        return None  # bracket markets (count ranges) are a different contract
    m = re.search(r"(\d+)(?:\+| or more)\s*times", q)
    return {"terms": terms, "threshold": int(m.group(1)) if m else 1}


def normalize(text):
    text = text.replace("’", "'").replace("‘", "'")
    # "A.I." -> "AI", "U.S." -> "US": spoken initialisms written with periods.
    return re.sub(r"\b((?:[A-Za-z]\.){2,})", lambda m: m.group(1).replace(".", ""), text)


def term_regex(term):
    tokens = normalize(term).split()
    parts = []
    for i, tok in enumerate(tokens):
        low = tok.lower()
        if low in NUM_ALT:
            core = NUM_ALT[low]
        else:
            core = re.escape(tok)
        if i == len(tokens) - 1:
            # Plurals and possessives of the final word.
            alts = [core + r"(?:s|es)?(?:'s|')?"]
            if low.endswith("y") and low not in NUM_ALT:
                alts.append(re.escape(tok[:-1]) + r"ies'?")
            core = "(?:" + "|".join(alts) + ")"
        parts.append(core)
    # ponytail: closed compounds ("wildfire" for "fire") are not counted; that needs
    # a dictionary to tell them from derivations ("joyful"). Undercounts slightly.
    # Between words of a phrase, transcripts may put hyphens or punctuation ("drill, baby, drill").
    return re.compile(r"(?<![A-Za-z0-9])" + r"[\s,.;:!?-]+".join(parts) + r"(?![A-Za-z0-9])", re.I)


def count(terms, text):
    """Qualifying mentions of any of `terms`; adjacent hits merge into one (name components)."""
    text = normalize(text)
    spans = sorted({m.span() for t in terms for m in term_regex(t).finditer(text)})
    merged = []
    for start, end in spans:
        if merged and (start <= merged[-1][1] or not text[merged[-1][1]:start].strip(" -")):
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return len(merged)


def demo():
    assert parse_question('Will Trump say "Border" 10+ times during his rally?') == {"terms": ["Border"], "threshold": 10}
    assert parse_question('Will Trump say "Million", "Billion" or "Trillion" 25+ times?')["terms"] == ["Million", "Billion", "Trillion"]
    assert parse_question('Will Trump say "alien" 5 or more times at the rally?')["threshold"] == 5
    assert parse_question('Will Microsoft say "AI / Artificial Intelligence" during earnings call?')["terms"] == [
        "AI", "Artificial Intelligence"]
    assert parse_question("Will the earnings call not air?") is None
    assert parse_question('Will X tweet "Y" 41–50 times during March?') is None
    assert count(["Immigrant"], "Immigrants and an immigrant's view, not immigration.") == 2
    assert count(["Russia"], "Russia's army, not Russian.") == 1
    assert count(["Party"], "Two parties, one party.") == 2
    assert count(["Veterans"], "Our veterans, one veteran, veterans' day.") == 2
    assert count(["Palestine"], "He is pro-Palestine.") == 1
    assert count(["fire"], "The fire station, not firefighter.") == 1
    assert count(["AI"], "A.I. and AI, but not artificial intelligence or said.") == 2
    assert count(["Eight Wars"], "I ended 8 wars, eight wars.") == 2
    assert count(["Joe", "Biden"], "Joe Biden, then Biden, then Joe.") == 3
    assert count(["Run"], "Running, ran.") == 0
    assert count(["drill baby drill"], "We will drill, baby, drill.") == 1
    assert count(["Alexa+"], "Alexa+ is growing; Alexa+.") == 2


if __name__ == "__main__":
    demo()
    print("ok")
