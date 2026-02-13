from app.connectors.parsers import extract_stops


def test_extract_stops_from_explicit_stop_label() -> None:
    text = "Emirates 18:25 KUL 26h 50m 1 stop 08:15 JFK"
    assert extract_stops(text) == 1


def test_extract_stops_from_layover_text() -> None:
    text = "Korean Air 23:55 KUL 23h 5m 2h 45m in Seoul 10:00 JFK +1"
    assert extract_stops(text) == 1


def test_extract_stops_from_multiple_layovers() -> None:
    text = "Carrier 08:00 KUL 1h 20m in SIN 2h 10m in NRT 21:00 JFK"
    assert extract_stops(text) == 2


def test_extract_stops_non_stop_takes_priority() -> None:
    text = "Carrier non-stop 08:00 KUL 10:00 BKK"
    assert extract_stops(text) == 0


def test_extract_stops_nonstop_takes_priority() -> None:
    text = "Carrier nonstop 08:00 KUL 10:00 BKK"
    assert extract_stops(text) == 0
