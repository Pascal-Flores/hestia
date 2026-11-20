"""French text normalization at the boundary with the speech synthesizer.

Only the *spoken* text is modified: routing, structured tool outputs and the
HTTP response keep their original numeric values. No LLM call is needed.
"""

from __future__ import annotations

import re
from num2words import num2words

# The wake word / STT are unrelated. This file is used by both TTS entry points.
# Keep URLs and e-mail addresses intact: they are identifiers, not quantities.
_PROTECTED = re.compile(r"https?://[^\s<>()]+|www\.[^\s<>()]+|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
_DATE = re.compile(
    r"(?<![\w/])(?P<day>0?[1-9]|[12]\d|3[01])/(?P<month>0?[1-9]|1[0-2])/(?P<year>\d{4})(?![\w/])"
)
_ISO_DATE = re.compile(
    r"(?<!\w)(?P<year>\d{4})-(?P<month>0?[1-9]|1[0-2])-(?P<day>0?[1-9]|[12]\d|3[01])(?!\w)"
)
_TIME = re.compile(
    r"(?<!\w)(?P<hour>[01]?\d|2[0-3])\s*(?:[hH]|:)\s*(?P<minute>[0-5]\d)(?!\w)"
)
_HOUR = re.compile(r"(?<!\w)(?P<hour>[01]?\d|2[0-3])\s*[hH](?!\w)")
_ORDINAL = re.compile(r"(?<!\w)(?P<num>\d+)(?P<suffix>ère|ere|er|re|ème|eme|è|e)(?!\w)", re.I)
# Grouped thousands (1 234 / 12 345 678) and ordinary decimal numbers.
_NUMBER_SOURCE = r"(?:\d{1,3}(?:[ \u00a0\u202f]\d{3})+|\d+)(?:[.,]\d+)?"
_TEMPERATURE = re.compile(rf"(?<![\w/])(?P<value>[+\-−]?{_NUMBER_SOURCE})\s*°\s*(?P<unit>[CcFf])(?!\w)")
_CURRENCY = re.compile(rf"(?<![\w/])(?P<value>[+\-−]?{_NUMBER_SOURCE})\s*€")
_PERCENT = re.compile(rf"(?<![\w/])(?P<value>[+\-−]?{_NUMBER_SOURCE})\s*%")
_NUMBER = re.compile(rf"(?<![\w/])(?P<value>[+\-−]?{_NUMBER_SOURCE})(?![\w/])")
_MONTHS = (
    "", "janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
    "septembre", "octobre", "novembre", "décembre",
)
_DIGIT_WORDS = ("zéro", "un", "deux", "trois", "quatre", "cinq", "six", "sept", "huit", "neuf")


def _integer(text: str) -> str:
    """Convert digits to French words, preserving leading zeros when meaningful."""
    digits = text.replace(" ", "").replace("\u00a0", "").replace("\u202f", "")
    if len(digits) > 1 and digits[0] == "0":
        return " ".join(_DIGIT_WORDS[int(d)] for d in digits)
    # Huge opaque numeric sequences are not safe to coerce into float or convert
    # to an arbitrarily large French cardinal; read them digit by digit.
    if len(digits) > 18:
        return " ".join(_DIGIT_WORDS[int(d)] for d in digits)
    return num2words(int(digits), lang="fr")


def _say(value: str) -> str:
    sign = ""
    if value.startswith(("-", "−", "+")):
        sign = ("moins " if value[0] in "-−" else "plus ")
        value = value[1:]
    if "," in value or "." in value:
        integral, fraction = re.split(r"[.,]", value, maxsplit=1)
        # 2,05 needs to be 'deux virgule zéro cinq', not 'deux virgule cinq'.
        fractional = (
            " ".join(_DIGIT_WORDS[int(d)] for d in fraction)
            if fraction.startswith("0") or len(fraction) > 3
            else _integer(fraction)
        )
        return f"{sign}{_integer(integral)} virgule {fractional}"
    return sign + _integer(value)


def _part(text: str) -> str:
    def date(match: re.Match) -> str:
        day = int(match["day"])
        return f"{'premier' if day == 1 else _integer(str(day))} {_MONTHS[int(match['month'])]} {_integer(match['year'])}"

    text = _DATE.sub(date, text)

    def iso_date(match: re.Match) -> str:
        day = int(match["day"])
        return f"{'premier' if day == 1 else _integer(str(day))} {_MONTHS[int(match['month'])]} {_integer(match['year'])}"

    text = _ISO_DATE.sub(iso_date, text)

    def time(match: re.Match) -> str:
        hour, minute = int(match["hour"]), int(match["minute"])
        hour_words = "une" if hour == 1 else _integer(str(hour))
        return f"{hour_words} heure{'s' if hour != 1 else ''}" + (f" {_integer(str(minute))}" if minute else "")

    text = _TIME.sub(time, text)
    text = _HOUR.sub(lambda match: f"{'une' if int(match['hour']) == 1 else _integer(str(int(match['hour'])))} heure{'s' if int(match['hour']) != 1 else ''}", text)

    def ordinal(match: re.Match) -> str:
        number = int(match["num"])
        suffix = match["suffix"].lower()
        if number == 1:
            return "première" if suffix in {"ère", "ere", "re"} else "premier"
        return num2words(number, to="ordinal", lang="fr")

    text = _ORDINAL.sub(ordinal, text)

    def degrees(match: re.Match) -> str:
        value = match["value"]
        singular = value.lstrip("+−-").replace(" ", "") in ("1", "1,0", "1.0")
        unit = "Celsius" if match["unit"].lower() == "c" else "Fahrenheit"
        return f"{_say(value)} degré{'s' if not singular else ''} {unit}"

    text = _TEMPERATURE.sub(degrees, text)

    def euros(match: re.Match) -> str:
        value = match["value"]
        # A currency-like decimal with two digits represents euros + cents.
        if re.search(r"[.,]\d{2}$", value):
            whole, cents = re.split(r"[.,]", value, maxsplit=1)
            count = int(cents)
            whole_count = int(whole.replace(" ", "").replace("\u00a0", "").replace("\u202f", ""))
            words = f"{_say(whole)} euro{'s' if abs(whole_count) != 1 else ''}"
            if count:
                words += f" et {_integer(cents)} centime{'s' if count != 1 else ''}"
            return words
        num = int(re.split(r"[.,]", value.replace(" ", ""), 1)[0])
        return f"{_say(value)} euro{'s' if abs(num) != 1 else ''}"

    text = _CURRENCY.sub(euros, text)
    text = _PERCENT.sub(lambda match: _say(match["value"]) + " pour cent", text)
    return _NUMBER.sub(lambda match: _say(match["value"]), text)


def normalize_for_speech(text: str) -> str:
    """Spell ordinary French numbers without modifying source tool/API data.

    Supports integers, signed values, decimals, grouped thousands, times,
    dates, temperatures, percentages, ordinal numbers, and euros. Identifiers
    such as URLs and email addresses stay unchanged by design.
    """
    if not isinstance(text, str):
        raise TypeError("Speech text must be a string")
    segments = []
    cursor = 0
    for match in _PROTECTED.finditer(text):
        segments.append(_part(text[cursor:match.start()]))
        segments.append(match.group())
        cursor = match.end()
    segments.append(_part(text[cursor:]))
    return "".join(segments)
