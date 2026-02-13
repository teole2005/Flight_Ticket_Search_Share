from datetime import date

from app.schemas import SearchCreateRequest
from app.services.query_hash import build_query_hash


def test_query_hash_is_stable_across_source_order() -> None:
    query_a = SearchCreateRequest(
        origin="kul",
        destination="bkk",
        departure_date="2026-03-20",
        return_date="2026-03-25",
        trip_type="round_trip",
        adults=1,
        currency="myr",
        sources=["trip_com", "airasia"],
    )
    query_b = SearchCreateRequest(
        origin="KUL",
        destination="BKK",
        departure_date="2026-03-20",
        return_date="2026-03-25",
        trip_type="round_trip",
        adults=1,
        currency="MYR",
        sources=["airasia", "trip_com"],
    )
    assert build_query_hash(query_a) == build_query_hash(query_b)


def test_query_hash_changes_when_query_changes() -> None:
    query_a = SearchCreateRequest(
        origin="KUL",
        destination="BKK",
        departure_date="2026-03-20",
        return_date="2026-03-25",
        trip_type="round_trip",
        adults=1,
        currency="MYR",
        sources=["trip_com", "airasia"],
    )
    query_b = query_a.model_copy(update={"departure_date": date(2026, 3, 21)})
    assert build_query_hash(query_a) != build_query_hash(query_b)
