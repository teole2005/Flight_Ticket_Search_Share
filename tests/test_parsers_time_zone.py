from datetime import date, time

from app.connectors.parsers import build_datetime


def test_build_datetime_uses_malaysia_timezone() -> None:
    value = build_datetime(date(2026, 3, 20), time(8, 10), fallback_hour=9)
    assert value.utcoffset() is not None
    assert value.utcoffset().total_seconds() == 8 * 3600
    assert value.hour == 8
    assert value.minute == 10
