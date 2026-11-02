"""Spoken-language parsing: amounts in digits or words, payee alias matching, amounts read back as words.

Used by the offline model and by the output validator (to check that the amount the model repeats
matches the draft).
"""
from __future__ import annotations

import re

_ONES = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
         "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
         "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80,
         "ninety": 90}
_SCALES = {"hundred": 100, "thousand": 1000, "k": 1000, "grand": 1000, "million": 1_000_000}
_UNIT_WORDS = {"dollar", "dollars", "buck", "bucks", "usd"}
_NUM_WORDS = set(_ONES) | set(_TENS) | set(_SCALES)


def words_to_number(tokens: list[str]) -> float | None:
    """['two', 'hundred', 'and', 'fifty'] -> 250, ['a', 'thousand'] -> 1000, ['twelve', 'point', 'five'] -> 12.5.
    Spoken shorthand: ['one', 'fifty'] -> 150, ['two', 'twenty', 'five'] -> 225, ['nineteen', 'fifty'] -> 1950
    (a number up to nineteen straight before a tens word means hundreds, as people say prices)."""
    if not tokens:
        return None
    total, current, frac, seen = 0, 0, "", False
    in_frac, prev = False, ""
    for i, t in enumerate(tokens):
        if in_frac:
            if t in _ONES and _ONES[t] < 10:
                frac += str(_ONES[t])
                continue
            return None
        if t == "point":
            in_frac = True
        elif t == "and":
            continue
        elif t == "a" and i + 1 < len(tokens) and tokens[i + 1] in _SCALES:
            current = 1
        elif t in _ONES:
            current += _ONES[t]
            seen = True
        elif t in _TENS:
            if prev in _ONES and 1 <= current <= 19:
                current = current * 100 + _TENS[t]          # "one fifty" = 150, not 51
            else:
                current += _TENS[t]
            seen = True
        elif t == "hundred":
            current = (current or 1) * 100
            seen = True
        elif t == "million":
            total = (total + (current or 1)) * 1_000_000 if not total else total + (current or 1) * 1_000_000
            current, seen = 0, True
        elif t in ("thousand", "k", "grand"):
            total += (current or 1) * 1000
            current, seen = 0, True
        else:
            return None
        prev = t
    if not seen:
        return None
    value = total + current
    return float(f"{value}.{frac}") if frac else float(value)


_MONEY_RE = re.compile(
    r"(?:\$|usd\s*)?\s*(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{1,2}))?\s*(k\b|thousand|grand|dollars?|bucks?|usd)?",
    re.I)
_TOKEN_RE = re.compile(r"[a-z]+")


def _word_runs(text: str):
    """Yield (tokens_of_number_run, tokens_after_it) for each run of number words."""
    for run, rest, _, _ in _word_spans(text):
        yield run, rest


def _word_spans(text: str):
    """The same runs, with where each one starts and ends in the text."""
    found = list(_TOKEN_RE.finditer(text.lower().replace("-", " ")))
    toks = [m.group(0) for m in found]
    i = 0
    while i < len(toks):
        t = toks[i]
        starts = t in _NUM_WORDS - {"k", "grand"} or (t == "a" and i + 1 < len(toks) and toks[i + 1] in _SCALES)
        if not starts:
            i += 1
            continue
        j = i
        run = []
        while j < len(toks) and (toks[j] in _NUM_WORDS or toks[j] == "point"
                                 or (toks[j] == "a" and j + 1 < len(toks) and toks[j + 1] in _SCALES)
                                 or (toks[j] == "and" and run and j + 1 < len(toks) and toks[j + 1] in _NUM_WORDS)
                                 or (run and run[-1] == "point" and toks[j] in _ONES)):
            run.append(toks[j])
            j += 1
        yield run, toks[j:], found[i].start(), found[j - 1].end()
        i = j


def parse_amount(text: str) -> float | None:
    """Extract an amount from one sentence. Returns None if there isn't one. Never guesses."""
    # An explicit self-correction supersedes the earlier amount.
    fixed = re.search(r"\b(?:change|correct|revise)\s+(?:the\s+)?amount\s+to\s+(.+)|\bmake\s+(?:it|that)\s+(.+)|\b(?:no|sorry|actually|I mean)\s*[,;]?\s+(.+)", text, re.I)
    if fixed:
        text = next(g for g in fixed.groups() if g)
    text = re.sub(r"\ba couple of hundred\b", "two hundred", text, flags=re.I)
    fraction = re.search(r"\b(half|a quarter)\s+(?:of\s+)?(.+)", text, re.I)
    if fraction:
        base = parse_amount(fraction[2])
        if base is None:
            return None
        from decimal import Decimal
        cents = Decimal(str(base)) * 100 / (2 if fraction[1].lower() == "half" else 4)
        return float(cents / 100) if cents == cents.to_integral_value() else None
    return _choose(_candidates(text), text)


# What follows or precedes a number that isn't money: "for 3 months", "room 2", "at 5pm", "on October 3"
_NOT_MONEY_AFTER = re.compile(r"\s*-?\s*(?:months?|years?|weeks?|days?|hours?|hrs?|minutes?|mins?|seconds?|times?|people|persons?|kids?|children|"
                              r"grandkids?|boxes|items?|am|pm|a\.m|p\.m|o'?clock|st|nd|rd|th)\b", re.I)
_NOT_MONEY_BEFORE = re.compile(r"\b(?:room|apartment|apt|unit|flight|page|number|no\.|floor|gate|bus|route|account|at|january|february|march|"
                               r"april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)\s*#?\s*$|#\s*$", re.I)
_NEXT_TO_VERB = re.compile(r"\b(?:send|sending|pay|paying|give|giving|transfer|wire|lend|request|ask\s+[a-z]+\s+for)\s+((?:[a-z'.-]+\s+){0,3})$", re.I)
_BREAKS_THE_LINK = {"for", "in", "at", "on", "of", "by", "from", "about", "with", "after", "before", "room", "his", "her", "their", "because"}


def _next_to_verb(before: str) -> bool:
    m = _NEXT_TO_VERB.search(before)
    return bool(m) and not _BREAKS_THE_LINK & {w.lower() for w in m.group(1).split()}


def _candidates(text: str) -> list[dict]:
    """Every number in the sentence that could be money, in order, each with what gives it away."""
    out = []
    for m in _MONEY_RE.finditer(text):
        digits, cents, unit = m.group(1), m.group(2), (m.group(3) or "").lower()
        end = m.end(1) + (len(cents) + 1 if cents else 0)
        if is_percent(text, end):
            continue                                  # "20%" is a share, not $20
        # Skip long numbers that look like account numbers, phone numbers or dates
        raw = digits.replace(",", "")
        before = re.sub(r"(?:\$|usd\s*)\s*$", "", text[:m.start(1)], flags=re.I)
        marked = len(before) != len(text[:m.start(1)]) or bool(unit)
        after_cents = re.match(r"\s*cents?\b", text[end:], re.I)
        if len(raw) >= 7 and not unit and "," not in digits:
            continue                                  # "1,000,000" is grouped like money; "12345678" is an account or a phone
        if not marked and not after_cents and (_NOT_MONEY_AFTER.match(text[end:]) or _NOT_MONEY_BEFORE.search(before)):
            continue
        val = float(raw + (f".{cents}" if cents else ""))
        if unit in ("k", "thousand", "grand"):
            val *= 1000
        if after_cents:
            val, marked = val / 100, True
        out.append({"value": val, "start": len(before), "end": m.end(), "unit": marked, "verb": _next_to_verb(before), "cents": bool(after_cents)})
    for run, rest, start, end in _word_spans(text):
        base = words_to_number(run)
        if base is None or base == 0:
            continue
        has_unit = bool(rest) and rest[0] in _UNIT_WORDS
        is_cents = bool(rest) and rest[0] in ("cent", "cents")
        if len(run) == 1 and base < 10 and not has_unit and not is_cents:
            continue               # a lone "two" or "five" is probably not an amount: better to ask than to guess
        if not has_unit and not is_cents and (_NOT_MONEY_AFTER.match(text[end:]) or _NOT_MONEY_BEFORE.search(text[:start])):
            continue
        if has_unit:   # "twenty dollars fifty" / "twenty dollars and fifty cents" -> 20.5
            after = rest[1:]
            if after and after[0] == "and":
                after = after[1:]
            cents_run = []
            while after and after[0] in set(_ONES) | set(_TENS):
                cents_run.append(after.pop(0))
            c = words_to_number(cents_run)
            if c is not None and 0 < c < 100:
                base += c / 100
                end = start + len(text[start:])    # those words are part of this amount, not an amount of their own
        if is_cents:
            base /= 100
        out.append({"value": base, "start": start, "end": end, "unit": has_unit or is_cents, "verb": _next_to_verb(text[:start]), "cents": is_cents})
    out.sort(key=lambda c: c["start"])
    merged = []
    for c in out:                  # "86 dollars and 40 cents" is one amount
        if merged and c["cents"] and not merged[-1]["cents"] and merged[-1]["end"] < len(text) and re.fullmatch(r"\s*(?:and|,)?\s*", text[merged[-1]["end"]:c["start"]], re.I):
            merged[-1] = {**merged[-1], "value": merged[-1]["value"] + c["value"], "end": c["end"]}
        elif not any(m["start"] <= c["start"] < m["end"] for m in merged):
            merged.append(c)
    return merged


def _choose(candidates: list[dict], text: str) -> float | None:
    """The number next to the verb ("send 20 ..."), else the one marked as money, else the only one. Two equally good
    candidates means Star asks: a guessed amount on a card that looks right is the mistake to avoid."""
    near = next((c for c in candidates if c["verb"]), None)
    if near:
        return near["value"]
    marked = {c["value"] for c in candidates if c["unit"]}
    if marked:
        return marked.pop() if len(marked) == 1 else None
    values = {c["value"] for c in candidates}
    return values.pop() if len(values) == 1 else None


def has_overlong_number(text: str) -> bool:
    """Seven digits or more with no "dollars": parse_amount won't read it as money, so say so instead of just asking again."""
    return bool(re.search(r"(?<![\d.,])\d{7,}(?!\d)", text))


def is_percent(text: str, end: int) -> bool:
    return bool(re.match(r"\s?(%|per ?cent\b|percent\b)", text[end:], re.I))


def find_raw_account(text: str) -> str | None:
    m = re.search(r"(?<!\d)(\d[\d\- ]{6,}\d)(?!\d)", text)
    if not m:
        return None
    digits = re.sub(r"\D", "", m.group(1))
    return digits if len(digits) >= 7 else None


def match_payees(text: str, payees: list[dict]) -> list[dict]:
    """Find payees by alias in the text; return every hit (the caller must ask if there's more than one)."""
    low = text.lower()
    hits = []
    for p in payees:
        names = [p["name"].lower(), *[a.lower() for a in p.get("aliases", [])]]
        if any(n and re.search(rf"(?<![a-z]){re.escape(n)}(?![a-z])", low) for n in names):
            hits.append(p)
    if len(hits) > 1:
        # "my grandson ... Chen Wei": a name only one candidate has beats a word they all share
        shared = set.intersection(*({p["name"].lower(), *map(str.lower, p.get("aliases", []))} for p in hits))
        specific = [p for p in hits
                    if any(n not in shared and re.search(rf"(?<![a-z]){re.escape(n)}(?![a-z])", low)
                           for n in [p["name"].lower(), *map(str.lower, p.get("aliases", []))])]
        if len(specific) == 1:
            return specific
    return hits


# ---------------------------------------------------------------- Amounts as words
_ONES_W = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven",
           "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
_TENS_W = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]


def _int_to_en(n: int) -> str:
    if n < 20:
        return _ONES_W[n]
    if n < 100:
        t, o = divmod(n, 10)
        return _TENS_W[t] + (f"-{_ONES_W[o]}" if o else "")
    if n < 1000:
        h, r = divmod(n, 100)
        return f"{_ONES_W[h]} hundred" + (f" and {_int_to_en(r)}" if r else "")
    for scale, name in ((1_000_000, "million"), (1000, "thousand")):
        if n >= scale:
            hi, lo = divmod(n, scale)
            rest = ("" if not lo else (" and " if lo < 100 else " ") + _int_to_en(lo))
            return f"{_int_to_en(hi)} {name}{rest}"
    return str(n)


def amount_words_en(cents: int) -> str:
    """20000 -> 'two hundred dollars'; 8640 -> 'eighty-six dollars and forty cents'. For the card and read-back."""
    dollars, rem = divmod(cents, 100)
    parts = []
    if dollars:
        parts.append(f"{_int_to_en(dollars)} dollar{'s' if dollars != 1 else ''}")
    if rem:
        parts.append(f"{_int_to_en(rem)} cent{'s' if rem != 1 else ''}")
    return " and ".join(parts) or "zero dollars"


def amount_digits_spoken(cents: int) -> str:
    """Read digit by digit so it can't be misheard: 20000 -> 'two, zero, zero'."""
    return ", ".join(_ONES_W[int(c)] for c in str(cents // 100))


def money_mentions(text: str) -> list[float]:
    """Extract every number that looks like an amount (for the output validator)."""
    vals = []
    for m in re.finditer(r"(?:\$)\s*(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{1,2}))?", text):
        vals.append(float(m.group(1).replace(",", "") + (f".{m.group(2)}" if m.group(2) else "")))
    return vals


# ---------------------------------------------------------------- Relative amounts ("half of it")
# These are computed by code, never by the model: the model is bad at arithmetic and a wrong share of someone's money is
# exactly the mistake that must not reach the confirmation card.
_RELATIVE = [
    (re.compile(r"\btwo[- ]thirds?\b", re.I), 2 / 3, "two thirds"),
    (re.compile(r"\b(?:a |one )?third\b", re.I), 1 / 3, "a third"),
    (re.compile(r"\b(?:a |one )?quarter\b", re.I), 1 / 4, "a quarter"),
    (re.compile(r"\bhalf\b", re.I), 1 / 2, "half"),
    # "the rest" / "whatever is left" are NOT here: the rest of what is unclear, so the agent asks how much
    (re.compile(r"\ball (?:of )?(?:my|the|our) (?:money|savings|balance|cash)\b|\beverything (?:in|from) (?:my|the)\b|"
                r"\bthe whole (?:amount|balance)\b|\ball of it\b", re.I), 1.0, "all"),
]
_PERCENT = re.compile(r"\b(\d{1,2}(?:\.\d)?)\s?(?:%|per ?cent\b)", re.I)
_OF_IT = re.compile(r"\b(?:of|with) (?:it|that|this)\b|\bhalf (?:of )?(?:it|that)\b", re.I)


def relative_in(text: str) -> tuple[float, str, str] | None:
    """('half of my money') -> (0.5, 'half', 'balance'); ('with half of it') -> (0.5, 'half', 'it'); else None.
    Only when the sentence has no plain amount of its own (then the plain amount wins)."""
    m = _PERCENT.search(text)
    hit = (float(m.group(1)) / 100, f"{m.group(1)}%") if m else next(
        ((frac, label) for rx, frac, label in _RELATIVE if rx.search(text)), None)
    if not hit:
        return None
    base = "it" if _OF_IT.search(text) else "balance"
    if base == "balance" and parse_amount(_PERCENT.sub("", text)):
        return None                               # "send 200 to my son and half to..." -> handled by the plan
    return hit[0], hit[1], base


# ---------------------------------------------------------------- speech-recognition slips on amounts
# Used ONLY by the offline planner, as a stand-in for what a real model works out by itself ("a grant" in "send a
# grant to my daughter" means "a grand"). With a model, the model reads the user's words; the kernel then checks
# its reading (evidence.sounds_alike) instead of rewriting anything.
_COUNT = r"(a|one|two|three|four|five|six|seven|eight|nine|ten|\d{1,3})"
_GRAND_SLIP = re.compile(rf"\b{_COUNT}\s+(grant|grants|grind|grinds|gran|grain|grains|grond)\b(?!\s+(?:from|for|of|to apply|application|scheme)\b)", re.I)
_MONEY_VERB = re.compile(r"\b(move|moving|send|sending|transfer|put|invest|pay|give|top ?up|buy|withdraw|deposit|"
                         r"how about|make it)\b", re.I)
_ANSWER_SLIP = re.compile(r"^\s*(agree|a green|a grin|a gran|a grant|a grind|a grain|uh grand|ah grand)\s*[.!]*\s*$", re.I)


def fix_asr(text: str, asked_amount: bool = False) -> tuple[str, list[dict]]:
    """Fix speech-recognition slips on amounts. Returns (fixed text, [{"heard", "meant"}]); unchanged if unsure."""
    fixes: list[dict] = []
    if asked_amount and _ANSWER_SLIP.match(text):           # a guess: ask before using it
        heard = text.strip().rstrip(".!")
        return "a grand", [{"heard": heard, "meant": "a grand", "sure": False}]
    if not (_MONEY_VERB.search(text) or asked_amount):
        return text, fixes

    def sub(m):
        meant = f"{m.group(1)} grand"
        fixes.append({"heard": m.group(0), "meant": meant, "sure": True})
        return meant
    return _GRAND_SLIP.sub(sub, text), fixes
