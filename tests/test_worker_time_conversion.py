from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from app.workers.search_worker import _as_utc


def test_as_utc_converts_malaysia_time_to_utc() -> None:
    malaysia = ZoneInfo("Asia/Kuala_Lumpur")
    value = datetime(2026, 3, 20, 8, 10, tzinfo=malaysia)
    converted = _as_utc(value)
    assert converted.tzinfo == UTC
    assert converted.hour == 0
    assert converted.minute == 10


def test_as_utc_keeps_naive_as_utc() -> None:
    value = datetime(2026, 3, 20, 8, 10)
    converted = _as_utc(value)
    assert converted.tzinfo == UTC
    assert converted.hour == 8
    assert converted.minute == 10
