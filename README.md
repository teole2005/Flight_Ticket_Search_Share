# Flight Search Service

Automated flight search backend that:
- accepts a search query,
- runs multiple website connectors in parallel (`trip_com`, `airasia`, etc.),
- normalizes and ranks offers by cheapest total price,
- returns flight details and booking URLs,
- stores history in PostgreSQL/SQLite and caches repeated queries in Redis.

## Quick start

1. Copy environment config:
```bash
cp .env.example .env
```

2. Install dependencies:
```bash
pip install -e .[dev]
python -m playwright install chromium
```

3. Run API:
```bash
python -m uvicorn app.main:app --reload
```

4. Open docs:
`http://localhost:8000/docs`

5. Open web UI:
`http://localhost:8000/`

## Docker

```bash
docker compose up --build
```

## API

- `POST /v1/search`
- `GET /v1/search/{search_id}`
- `GET /v1/search/{search_id}/offers/{offer_id}`
- `GET /v1/health/connectors`

## Ticket Details In UI

- Offer cards are expandable and fetch ticket detail from `GET /v1/search/{search_id}/offers/{offer_id}` after search results render.
- The detail panel shows flight numbers, cabin, baggage, fare rules, fare brand, price breakdown, route timing, stops, and booking-link verification.

## Troubleshooting: No Best Offer / Alternatives

If connector cards show only errors and no fares:

1. Install browser runtime once:
```bash
python -m playwright install chromium
```
2. Restart API after changing `.env`:
```bash
python -m uvicorn app.main:app --reload
```
3. Start with `airasia` source only, then add `trip_com` after it works.
4. If `trip_com` still fails, keep `ENABLE_BROWSER_CONNECTORS=true` and re-check Playwright install.
5. If browser extraction fails, AirAsia now falls back to API fare + fallback booking link so offers can still render.
- If a search is `completed` with no fares, the UI shows per-source diagnostics (status, latency, offer count, error) plus troubleshooting hints.

Example request:

```json
{
  "origin": "KUL",
  "destination": "BKK",
  "departure_date": "2026-03-20",
  "return_date": "2026-03-25",
  "trip_type": "round_trip",
  "adults": 1,
  "cabin": "economy",
  "currency": "MYR",
  "stop_preference": "non_stop",
  "sources": ["trip_com", "airasia"]
}
```

## Notes for real-world scraping

- Prefer official APIs/affiliate feeds where available.
- Browser selectors are configurable in `.env`; update them when websites change.
- Respect each website's terms of service and rate limits.
- `trip_com` currently uses the verified card selector: `[data-testid^='u-flight-card-']`.
- `airasia` uses a hybrid flow:
  - station lookup + auth (`/api/auth`) + lowfare API for date pricing,
  - deep-link generation (`/deeplink/v1/encryptdeeplink`) for booking URL,
  - schedule extraction from the resulting `/select/...` page.
