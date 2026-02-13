from __future__ import annotations

import re
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation

_CURRENCY_MAP = {
    "$": "USD",
    "\u20ac": "EUR",
    "\u00a3": "GBP",
    "RM": "MYR",
    "MYR": "MYR",
    "USD": "USD",
    "SGD": "SGD",
    "THB": "THB",
    "IDR": "IDR",
    "VND": "VND",
    "PHP": "PHP",
    "JPY": "JPY",
}

_PRICE_PATTERNS = [
    re.compile(
        r"(?P<currency>MYR|USD|SGD|THB|IDR|VND|PHP|JPY|RM|\$|\u20ac|\u00a3)\s*(?P<amount>\d[\d,]*(?:\.\d{1,2})?)"
    ),
    re.compile(
        r"(?P<amount>\d[\d,]*(?:\.\d{1,2})?)\s*(?P<currency>MYR|USD|SGD|THB|IDR|VND|PHP|JPY|RM|\$|\u20ac|\u00a3)"
    ),
]

_TIME_PATTERN = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")
_STOPS_PATTERN = re.compile(r"(\d+)\s*stop")
_FLIGHT_NUMBER_PATTERN = re.compile(r"\b([A-Z0-9]{2,3}\s?\d{2,4})\b")
_AIRLINE_NOISE_PATTERNS = [
    re.compile(r"\bcarry-?on baggage included\b", re.IGNORECASE),
    re.compile(r"\bchecked baggage included\b", re.IGNORECASE),
    re.compile(r"\bbaggage included\b", re.IGNORECASE),
    re.compile(r"\bcarry-?on baggage\b", re.IGNORECASE),
    re.compile(r"\bchecked baggage\b", re.IGNORECASE),
    re.compile(r"\b-\s*\d+%\s*co2e\b", re.IGNORECASE),
    re.compile(r"\bco2e\b", re.IGNORECASE),
    re.compile(r"\bnon[- ]?stop\b", re.IGNORECASE),
    re.compile(r"\bdirect\b", re.IGNORECASE),
]
_AIRLINE_WORD_BLACKLIST = {"carry-on", "baggage", "included", "co2e", "direct", "nonstop"}


def normalize_text(raw_text: str) -> str:
    return re.sub(r"\s+", " ", raw_text).strip()


def extract_price(text: str, fallback_currency: str) -> tuple[Decimal | None, str]:
    fallback = fallback_currency.upper()
    for pattern in _PRICE_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        currency = _CURRENCY_MAP.get(match.group("currency").upper(), fallback)
        amount = match.group("amount").replace(",", "")
        try:
            return Decimal(amount), currency
        except InvalidOperation:
            continue
    return None, fallback


def extract_times(text: str) -> tuple[time | None, time | None]:
    matches = _TIME_PATTERN.findall(text)
    if not matches:
        return None, None
    dep = time(hour=int(matches[0][0]), minute=int(matches[0][1]))
    arr: time | None = None
    if len(matches) > 1:
        arr = time(hour=int(matches[1][0]), minute=int(matches[1][1]))
    return dep, arr


def extract_stops(text: str) -> int:
    lowered = text.lower()
    if "non-stop" in lowered or "non stop" in lowered or "direct" in lowered:
        return 0
    match = _STOPS_PATTERN.search(lowered)
    if match:
        return int(match.group(1))
    return 0


def extract_flight_numbers(text: str) -> list[str]:
    return [match.replace(" ", "") for match in _FLIGHT_NUMBER_PATTERN.findall(text)]


def extract_airline_name(text: str, default: str) -> str:
    time_match = _TIME_PATTERN.search(text)
    if time_match:
        prefix = text[: time_match.start()].replace("|", " ")
        for pattern in _AIRLINE_NOISE_PATTERNS:
            prefix = pattern.sub(" ", prefix)
        prefix = re.sub(r"^\s*included\s+", " ", prefix, flags=re.IGNORECASE)
        prefix = normalize_text(prefix).strip(" -")
        if prefix and not any(char.isdigit() for char in prefix):
            return prefix

    for chunk in text.split(" "):
        if not chunk:
            continue
        if any(char.isdigit() for char in chunk):
            continue
        cleaned = chunk.strip(".,:;")
        if cleaned.lower() in _AIRLINE_WORD_BLACKLIST:
            continue
        if len(cleaned) < 2:
            continue
        return cleaned
    return default


def build_datetime(date_value: date, parsed_time: time | None, fallback_hour: int) -> datetime:
    resolved_time = parsed_time or time(hour=fallback_hour, minute=0)
    return datetime.combine(date_value, resolved_time).replace(tzinfo=UTC)


def compute_duration_minutes(departure_at: datetime, arrival_at: datetime) -> int:
    if arrival_at <= departure_at:
        arrival_at = arrival_at + timedelta(days=1)
    return int((arrival_at - departure_at).total_seconds() // 60)
