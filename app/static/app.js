const API_PREFIX = "/v1";
const POLL_INTERVAL_MS = 2200;
const MAX_POLL_ATTEMPTS = 90;
const MALAYSIA_LOCALE = "en-MY";
const MALAYSIA_TIME_ZONE = "Asia/Kuala_Lumpur";
const DEFAULT_CURRENCY = "MYR";

const state = {
  activeSearchId: null,
  pollAttempts: 0,
  pollTimer: null,
  pollToken: 0,
  offerDetailCache: new Map(),
};

document.addEventListener("DOMContentLoaded", () => {
  setDefaultDates();
  bindTripSwitch();
  bindForm();
  bindHelpers();
  refreshHealth();
});

function byId(id) {
  return document.getElementById(id);
}

function setDefaultDates() {
  const departure = byId("departureDate");
  const ret = byId("returnDate");
  const today = new Date();
  const depDate = addDays(today, 14);
  const retDate = addDays(today, 21);
  const minDate = toIsoDate(today);
  departure.min = minDate;
  ret.min = minDate;
  departure.value = toIsoDate(depDate);
  ret.value = toIsoDate(retDate);
}

function addDays(date, days) {
  const clone = new Date(date);
  clone.setDate(clone.getDate() + days);
  return clone;
}

function toIsoDate(date) {
  return date.toISOString().slice(0, 10);
}

function bindTripSwitch() {
  const tripSwitch = byId("tripSwitch");
  const tripType = byId("tripType");
  const returnField = byId("returnField");
  const returnInput = byId("returnDate");

  tripSwitch.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLButtonElement)) {
      return;
    }
    const nextTrip = target.dataset.trip;
    if (!nextTrip) {
      return;
    }
    tripType.value = nextTrip;
    for (const button of tripSwitch.querySelectorAll(".trip-btn")) {
      button.classList.toggle("is-active", button === target);
    }
    const oneWay = nextTrip === "one_way";
    returnField.classList.toggle("hidden", oneWay);
    returnInput.disabled = oneWay;
  });
}

function bindHelpers() {
  byId("sampleRouteButton").addEventListener("click", () => {
    byId("origin").value = "KUL";
    byId("destination").value = "BKK";
    byId("currency").value = "MYR";
    byId("adults").value = "1";
    byId("stopPreference").value = "any";
    byId("cabin").value = "economy";
    setFeedback("Loaded sample route KUL -> BKK.");
  });

  byId("refreshHealthButton").addEventListener("click", () => {
    void refreshHealth();
  });
}

function bindForm() {
  const form = byId("searchForm");
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    void startSearch();
  });
}

async function startSearch() {
  clearPollTimer();
  const searchButton = byId("searchButton");
  searchButton.disabled = true;
  searchButton.textContent = "Searching...";
  clearOffers();
  state.offerDetailCache.clear();

  try {
    const payload = buildPayload();
    setStatus("running");
    setFeedback(`Launching ${payload.sources.join(", ")} and waiting for results...`);

    const response = await fetch(`${API_PREFIX}/search`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await readJsonSafe(response);
    if (!response.ok || !data) {
      throw new Error(buildErrorMessage(data, "Failed to create search."));
    }

    state.activeSearchId = data.search_id;
    state.pollAttempts = 0;
    state.pollToken += 1;
    byId("searchIdValue").textContent = data.search_id;
    byId("lastCheckedValue").textContent = formatDateTime(data.created_at);
    setStatus(data.status || "queued");
    setFeedback("Search created. Polling for final prices...");
    void pollSearch(data.search_id, state.pollToken);
  } catch (error) {
    setStatus("failed");
    setFeedback(error instanceof Error ? error.message : "Could not start search.");
  } finally {
    searchButton.disabled = false;
    searchButton.textContent = "Search Cheapest Flights";
  }
}

function buildPayload() {
  const tripType = byId("tripType").value;
  const origin = byId("origin").value.trim().toUpperCase();
  const destination = byId("destination").value.trim().toUpperCase();
  const currency = byId("currency").value.trim().toUpperCase();
  const departureDate = byId("departureDate").value;
  const returnDate = byId("returnDate").value;
  const adults = Number.parseInt(byId("adults").value, 10);
  const cabin = byId("cabin").value;
  const stopPreference = byId("stopPreference").value;
  const sources = Array.from(document.querySelectorAll("input[name='source']:checked")).map(
    (item) => item.value
  );

  if (origin.length !== 3 || destination.length !== 3) {
    throw new Error("Origin and destination must be 3-letter IATA codes.");
  }
  if (!departureDate) {
    throw new Error("Departure date is required.");
  }
  if (tripType === "round_trip" && !returnDate) {
    throw new Error("Return date is required for round-trip searches.");
  }
  if (sources.length === 0) {
    throw new Error("Select at least one source.");
  }

  return {
    origin,
    destination,
    departure_date: departureDate,
    return_date: tripType === "round_trip" ? returnDate : null,
    trip_type: tripType,
    adults: Number.isNaN(adults) ? 1 : adults,
    stop_preference: stopPreference || "any",
    cabin,
    currency: currency || DEFAULT_CURRENCY,
    sources,
  };
}

async function pollSearch(searchId, token) {
  if (token !== state.pollToken) {
    return;
  }

  try {
    const response = await fetch(`${API_PREFIX}/search/${encodeURIComponent(searchId)}`);
    const data = await readJsonSafe(response);
    if (!response.ok || !data) {
      throw new Error(buildErrorMessage(data, "Failed to fetch search result."));
    }
    if (token !== state.pollToken) {
      return;
    }

    renderResult(data);
    if (data.status === "completed" || data.status === "failed") {
      return;
    }
  } catch (error) {
    setStatus("failed");
    setFeedback(error instanceof Error ? error.message : "Polling failed.");
    return;
  }

  state.pollAttempts += 1;
  if (state.pollAttempts >= MAX_POLL_ATTEMPTS) {
    setStatus("failed");
    setFeedback("Search timed out. Try a new query or reduce selected sources.");
    return;
  }

  clearPollTimer();
  state.pollTimer = window.setTimeout(() => {
    void pollSearch(searchId, token);
  }, POLL_INTERVAL_MS);
}

function renderResult(result) {
  setStatus(result.status);
  byId("lastCheckedValue").textContent = formatDateTime(result.price_last_checked_at);

  const route = result.query ? `${result.query.origin} -> ${result.query.destination}` : "your route";
  if (result.status === "completed") {
    setFeedback(`Search completed for ${route}.`);
  } else if (result.status === "running" || result.status === "queued") {
    setFeedback(`Still searching ${route}.`);
  } else {
    setFeedback(`Search ended with status: ${result.status}.`);
  }

  renderCheapest(result.search_id, result.cheapest_flight);
  renderAlternatives(result.search_id, result.alternatives || []);
  bindOfferDetailFetch(result.search_id);
  renderFailures(result.failures || []);
  renderNoResultsPanel(result.status, result.cheapest_flight, result.connector_runs || []);
}

function renderCheapest(searchId, offer) {
  const container = byId("cheapestContainer");
  if (!offer) {
    container.className = "empty";
    container.textContent = "No offer found yet.";
    return;
  }
  container.className = "offer-list";
  container.innerHTML = offerCardMarkup(searchId, offer, true, 0);
}

function renderAlternatives(searchId, offers) {
  const container = byId("alternativesContainer");
  if (offers.length === 0) {
    container.innerHTML = "<div class='empty'>No alternative fares for this search.</div>";
    return;
  }
  container.innerHTML = offers
    .map((offer, index) => offerCardMarkup(searchId, offer, false, index + 1))
    .join("");
}

function offerCardMarkup(searchId, offer, primary, index) {
  const animationDelay = `${Math.min(index * 70, 320)}ms`;
  const flightNumbers = formatFlightNumbers(offer.flight_numbers);
  const cabin = formatMaybe(offer.cabin, "Not provided");
  const baggage = formatMaybe(offer.baggage, "Not specified");
  const fareRules = formatMaybe(offer.fare_rules, "Rules not provided by source");
  const linkStatusClass = offer.deep_link_valid ? "valid" : "invalid";
  const linkStatusText = offer.deep_link_valid ? "Booking link verified" : "Booking link unverified";
  const fallbackBreakdown = `Total ${formatMoney(offer.total_price, offer.currency)}`;

  return `
    <details
      class="offer-card ${primary ? "primary" : ""}"
      style="animation-delay:${animationDelay}"
      data-search-id="${escapeAttribute(searchId)}"
      data-offer-id="${escapeAttribute(offer.offer_id)}"
      data-detail-state="idle"
      ${primary ? "open" : ""}
    >
      <summary class="offer-summary">
        <div class="offer-top">
          <div>
            <p class="offer-source">${escapeHtml(formatSource(offer.source))}</p>
            <p class="offer-airline">${escapeHtml(offer.airline || "Unknown Airline")}</p>
            <p class="offer-route">${escapeHtml(flightNumbers)}</p>
          </div>
          <div class="offer-price-wrap">
            <p class="offer-price">${formatMoney(offer.total_price, offer.currency)}</p>
            <span class="summary-hint">Ticket details</span>
          </div>
        </div>

        <div class="offer-meta">
          <span class="tag">${escapeHtml(formatDateTime(offer.departure_at))}</span>
          <span class="tag">${escapeHtml(formatDateTime(offer.arrival_at))}</span>
          <span class="tag">${escapeHtml(formatStops(offer.stops))}</span>
          <span class="tag">${escapeHtml(formatDuration(offer.duration_minutes))}</span>
        </div>
      </summary>

      <div class="offer-details">
        <div class="detail-grid">
          <div class="detail-item">
            <span>Flight Numbers</span>
            <strong data-field="flight-numbers">${escapeHtml(flightNumbers)}</strong>
          </div>
          <div class="detail-item">
            <span>Cabin</span>
            <strong data-field="cabin">${escapeHtml(cabin)}</strong>
          </div>
          <div class="detail-item">
            <span>Baggage</span>
            <strong data-field="baggage">${escapeHtml(baggage)}</strong>
          </div>
          <div class="detail-item">
            <span>Fare Rules</span>
            <strong data-field="fare-rules">${escapeHtml(fareRules)}</strong>
          </div>
          <div class="detail-item">
            <span>Fare Brand</span>
            <strong data-field="fare-brand">Loading...</strong>
          </div>
          <div class="detail-item">
            <span>Price Breakdown</span>
            <strong data-field="price-breakdown">${escapeHtml(fallbackBreakdown)}</strong>
          </div>
        </div>

        <p class="detail-status hidden" data-detail-status></p>
        <div class="offer-actions">
          <span class="link-badge ${linkStatusClass}" data-field="link-badge">${escapeHtml(linkStatusText)}</span>
          <a
            class="book-link"
            data-field="book-link"
            href="${escapeAttribute(offer.booking_url)}"
            target="_blank"
            rel="noopener noreferrer"
          >
            Book Offer
          </a>
        </div>
      </div>
    </details>
  `;
}

function bindOfferDetailFetch(searchId) {
  if (!searchId) {
    return;
  }

  const cards = document.querySelectorAll(".offer-card[data-search-id]");
  for (const card of cards) {
    if (!(card instanceof HTMLDetailsElement)) {
      continue;
    }
    if (card.dataset.searchId !== searchId) {
      continue;
    }
    if (card.dataset.detailBound !== "1") {
      card.dataset.detailBound = "1";
      card.addEventListener("toggle", () => {
        if (!card.open) {
          return;
        }
        void ensureOfferDetail(card);
      });
    }
    if (card.open) {
      void ensureOfferDetail(card);
    }
  }
}

async function ensureOfferDetail(card) {
  const searchId = card.dataset.searchId;
  const offerId = card.dataset.offerId;
  if (!searchId || !offerId) {
    return;
  }

  const cacheKey = `${searchId}:${offerId}`;
  const cached = state.offerDetailCache.get(cacheKey);
  if (cached) {
    applyOfferDetail(card, cached);
    return;
  }
  if (card.dataset.detailState === "loading") {
    return;
  }

  card.dataset.detailState = "loading";
  setDetailStatus(card, "Fetching ticket detail...", "loading");
  try {
    const response = await fetch(
      `${API_PREFIX}/search/${encodeURIComponent(searchId)}/offers/${encodeURIComponent(offerId)}`
    );
    const data = await readJsonSafe(response);
    if (!response.ok || !data) {
      throw new Error(buildErrorMessage(data, "Failed to fetch ticket detail."));
    }
    if (!document.body.contains(card)) {
      return;
    }

    state.offerDetailCache.set(cacheKey, data);
    applyOfferDetail(card, data);
    card.dataset.detailState = "loaded";
    setDetailStatus(card, "", "loading");
  } catch (error) {
    card.dataset.detailState = "error";
    setDetailStatus(card, error instanceof Error ? error.message : "Ticket detail unavailable.", "error");
  }
}

function applyOfferDetail(card, detail) {
  setOfferCardText(card, "flight-numbers", formatFlightNumbers(detail.flight_numbers));
  setOfferCardText(card, "cabin", formatMaybe(detail.cabin, "Not provided"));
  setOfferCardText(card, "baggage", formatMaybe(detail.baggage, "Not specified"));
  setOfferCardText(card, "fare-rules", formatMaybe(detail.fare_rules, "Rules not provided by source"));
  setOfferCardText(card, "fare-brand", formatMaybe(detail.fare_brand, "Not provided"));
  setOfferCardText(card, "price-breakdown", formatPriceBreakdown(detail));

  const linkBadge = card.querySelector("[data-field='link-badge']");
  if (linkBadge instanceof HTMLElement) {
    const valid = Boolean(detail.deep_link_valid);
    linkBadge.className = `link-badge ${valid ? "valid" : "invalid"}`;
    linkBadge.textContent = valid ? "Booking link verified" : "Booking link unverified";
  }

  const bookLink = card.querySelector("[data-field='book-link']");
  if (bookLink instanceof HTMLAnchorElement) {
    bookLink.href = detail.booking_url || "#";
  }
}

function setOfferCardText(card, field, value) {
  const node = card.querySelector(`[data-field='${field}']`);
  if (node instanceof HTMLElement) {
    node.textContent = value;
  }
}

function setDetailStatus(card, message, level) {
  const statusNode = card.querySelector("[data-detail-status]");
  if (!(statusNode instanceof HTMLElement)) {
    return;
  }
  statusNode.classList.remove("hidden", "loading", "error");
  if (!message) {
    statusNode.textContent = "";
    statusNode.classList.add("hidden");
    return;
  }
  statusNode.textContent = message;
  statusNode.classList.add(level === "error" ? "error" : "loading");
}

function formatPriceBreakdown(offer) {
  const currency = offer.currency || "USD";
  const basePrice = Number(offer.base_price);
  const taxes = Number(offer.taxes);
  const fees = Number(offer.fees);
  const parts = [];

  if (Number.isFinite(basePrice)) {
    parts.push(`Base ${formatMoney(basePrice, currency)}`);
  }
  if (Number.isFinite(taxes)) {
    parts.push(`Taxes ${formatMoney(taxes, currency)}`);
  }
  if (Number.isFinite(fees)) {
    parts.push(`Fees ${formatMoney(fees, currency)}`);
  }

  const total = `Total ${formatMoney(offer.total_price, currency)}`;
  if (parts.length === 0) {
    return total;
  }
  return `${parts.join(" + ")} | ${total}`;
}

function renderNoResultsPanel(status, cheapestFlight, connectorRuns) {
  const panel = byId("noResultsPanel");
  const diagnostics = byId("noResultsDiagnostics");
  const showPanel = status === "completed" && !cheapestFlight;

  if (!showPanel) {
    panel.classList.add("hidden");
    diagnostics.innerHTML = "";
    return;
  }

  panel.classList.remove("hidden");
  if (connectorRuns.length === 0) {
    diagnostics.innerHTML = "<div class='empty'>No connector diagnostics available for this search.</div>";
    return;
  }

  diagnostics.innerHTML = connectorRuns.map((run) => connectorRunMarkup(run)).join("");
}

function connectorRunMarkup(run) {
  const errorText = formatMaybe(run.error_message, "No connector error message.");
  const latency = Number.isFinite(run.latency_ms) ? `${run.latency_ms} ms` : "N/A";
  const countLabel = `${Number(run.offer_count || 0)} offers`;

  return `
    <article class="diagnostic-card">
      <div class="diagnostic-head">
        <h4>${escapeHtml(formatSource(run.source))}</h4>
        <span class="pill ${escapeHtml(run.status)}">${escapeHtml(run.status)}</span>
      </div>
      <p>Offers: ${escapeHtml(countLabel)}</p>
      <p>Latency: ${escapeHtml(latency)}</p>
      <p>Error: ${escapeHtml(errorText)}</p>
    </article>
  `;
}

function renderFailures(failures) {
  const list = byId("failuresList");
  if (failures.length === 0) {
    list.innerHTML = "<li class='muted'>No connector errors for this search.</li>";
    return;
  }

  list.innerHTML = failures
    .map(
      (item) =>
        `<li><strong>${escapeHtml(item.source)}</strong> (${escapeHtml(item.status)}): ${escapeHtml(item.message)}</li>`
    )
    .join("");
}

async function refreshHealth() {
  const grid = byId("healthGrid");
  grid.innerHTML = "<div class='empty'>Loading connector health...</div>";

  try {
    const response = await fetch(`${API_PREFIX}/health/connectors`);
    const data = await readJsonSafe(response);
    if (!response.ok || !data) {
      throw new Error(buildErrorMessage(data, "Failed to fetch connector health."));
    }
    renderHealth(data.connectors || []);
  } catch (error) {
    grid.innerHTML = `<div class="empty">${escapeHtml(
      error instanceof Error ? error.message : "Connector health unavailable."
    )}</div>`;
  }
}

function renderHealth(connectors) {
  const grid = byId("healthGrid");
  if (connectors.length === 0) {
    grid.innerHTML = "<div class='empty'>No connector data yet.</div>";
    return;
  }
  grid.innerHTML = connectors.map((item) => healthCardMarkup(item)).join("");
}

function healthCardMarkup(item) {
  const latency = Number.isFinite(item.last_latency_ms) ? `${item.last_latency_ms} ms` : "N/A";
  const checkedAt = formatDateTime(item.last_checked_at);
  return `
    <article class="health-card">
      <h4>${escapeHtml(formatSource(item.source))}</h4>
      <span class="pill ${escapeHtml(item.status)}">${escapeHtml(item.status)}</span>
      <p>Latency: ${escapeHtml(latency)}</p>
      <p>Checked: ${escapeHtml(checkedAt)}</p>
    </article>
  `;
}

function setStatus(status) {
  const pill = byId("statusPill");
  const normalized = status || "idle";
  pill.className = `pill ${normalized}`;
  pill.textContent = normalized.replace(/_/g, " ");
}

function setFeedback(message) {
  byId("feedbackText").textContent = message;
}

function clearOffers() {
  byId("cheapestContainer").className = "empty";
  byId("cheapestContainer").textContent = "Searching for best fare...";
  byId("alternativesContainer").innerHTML = "";
  byId("failuresList").innerHTML = "<li class='muted'>No connector errors for this search.</li>";
  byId("noResultsPanel").classList.add("hidden");
  byId("noResultsDiagnostics").innerHTML = "";
}

function clearPollTimer() {
  if (state.pollTimer) {
    window.clearTimeout(state.pollTimer);
    state.pollTimer = null;
  }
}

function formatSource(value) {
  if (!value) {
    return "unknown";
  }
  return value.replace(/_/g, " ");
}

function formatStops(value) {
  const stops = Number(value);
  if (!Number.isFinite(stops) || stops < 0) {
    return "stops unknown";
  }
  if (stops === 0) {
    return "non-stop";
  }
  if (stops === 1) {
    return "1 stop";
  }
  return `${stops} stops`;
}

function formatDuration(minutes) {
  const totalMinutes = Number(minutes);
  if (!Number.isFinite(totalMinutes) || totalMinutes <= 0) {
    return "duration N/A";
  }
  const hours = Math.floor(totalMinutes / 60);
  const mins = totalMinutes % 60;
  return `${hours}h ${mins}m`;
}

function formatDateTime(value) {
  if (!value) {
    return "-";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "-";
  }
  return new Intl.DateTimeFormat(MALAYSIA_LOCALE, {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone: MALAYSIA_TIME_ZONE,
    timeZoneName: "short",
  }).format(date);
}

function formatMoney(value, currency) {
  const amount = Number(value);
  if (!Number.isFinite(amount)) {
    return "N/A";
  }
  try {
    return new Intl.NumberFormat(MALAYSIA_LOCALE, {
      style: "currency",
      currency: currency || DEFAULT_CURRENCY,
      maximumFractionDigits: 2,
    }).format(amount);
  } catch {
    return `${amount.toFixed(2)} ${currency || DEFAULT_CURRENCY}`;
  }
}

function formatMaybe(value, fallbackText) {
  const trimmed = String(value ?? "").trim();
  return trimmed ? trimmed : fallbackText;
}

function formatFlightNumbers(flightNumbers) {
  if (!Array.isArray(flightNumbers) || flightNumbers.length === 0) {
    return "Not provided";
  }
  const cleaned = flightNumbers.map((item) => String(item).trim()).filter((item) => item);
  if (cleaned.length === 0) {
    return "Not provided";
  }
  return cleaned.join(", ");
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function escapeAttribute(value) {
  return escapeHtml(value);
}

async function readJsonSafe(response) {
  try {
    return await response.json();
  } catch {
    return null;
  }
}

function buildErrorMessage(payload, fallback) {
  if (!payload || !payload.detail) {
    return fallback;
  }
  if (typeof payload.detail === "string") {
    return payload.detail;
  }
  if (Array.isArray(payload.detail)) {
    return payload.detail.map((entry) => entry.msg || "Invalid payload").join("; ");
  }
  return fallback;
}
