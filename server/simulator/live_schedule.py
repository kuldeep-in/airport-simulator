"""Live flight schedule from AeroDataBox (RapidAPI), replacing the static
data/arrivalRoster.json + data/departureRoster.json demo rosters.

Fetches the "Flights by airport (relative)" endpoint for Hamad Intl (OTHH)
over a rolling window (default: the next 6 hours), parses it into the exact
roster-dict shapes seed_runner.py and departures.py already consume, and
re-fetches every hour. Aircraft then activate at their real scheduled times
(the broadcaster runs sim_time on the real UTC clock) — taxi movement, routing
and gate simulation stay entirely local.

Endpoint (RapidAPI):
    GET https://aerodatabox.p.rapidapi.com/flights/airports/iata/DOH
        ?offsetMinutes=0&durationMinutes=360&withLeg=true&direction=Both
        &withCancelled=false&withCodeshared=false
        &withCargo=false&withPrivate=false&withLocation=false
    headers: X-RapidAPI-Key: <secret airport-sim/rapidapi-key>
             X-RapidAPI-Host: aerodatabox.p.rapidapi.com
Verified live against iata/DOH: ~80 departures + ~100 arrivals per 6h window.

`withCodeshared=false` means the API returns only the operating carrier's row
per physical flight (the primary callsign, e.g. QR202 rather than its VA/UL/MH
codeshare duplicates), so no client-side codeshare de-duplication is needed;
`withCancelled=false` drops cancelled flights server-side. We still filter
defensively.

Response shape (per movement item):
    {
      "departure": {"scheduledTime": {"utc": "2026-07-24 12:20Z", ...},
                    "revisedTime": {"utc": ...}, "gate": "C12", ...},
      "arrival":   {"airport": {"iata": "RUH", ...}, "scheduledTime": {...}, ...},
      "number": "QR 1238", "callSign": "QTR...", "status": "Departed",
      "aircraft": {"model": "Airbus A350-900"}, "airline": {"iata": "QR", ...}
    }
For a departure the HIA-side movement is `departure` (carries the real gate) and
the other airport is under `arrival.airport`; for an arrival it's the reverse.
"""

import asyncio
import json
import re
from datetime import datetime, timezone

import requests

from server.config import get_secret_value
from server.db import db
from server.simulator import flight_store

# ── Configuration ─────────────────────────────────────────────────────────────
AIRPORT_IATA = "DOH"           # Hamad International (verified live against the iata/DOH endpoint)
OFFSET_MINUTES = -240          # start 4h in the past: the -4h tail lets already-operated flights get their FINAL status/gate written to their persisted row (see flight_store)
WINDOW_MINUTES = 720           # 12h — the API max. Covered interval = [now-4h, now+8h].
# The -4h tail updates history in flight_schedule; the +8h head gives the map/board
# upcoming flights to simulate. The persisted dataset (flight_store) is the app's
# READ source; live_schedule just fetches + upserts into it.

# ── API-call cadence: fixed 4-hour UTC slots (00:00, 04:00, 08:00, …) ───────────
# The AeroDataBox API is expensive/quota-limited, so we call it at most ONCE per
# 4-hour slot. The raw payload is cached in Lakebase keyed by the slot boundary;
# on startup (every deploy restarts the app) we REUSE the cached payload if it's
# for the current slot — so deploys, tests and validation never hit the API. A
# real fetch only happens when the wall clock crosses into a new slot with no
# cached payload for it.
SLOT_HOURS = 4
_CACHE_TABLE = "flight_feed_cache"
RAPIDAPI_HOST = "aerodatabox.p.rapidapi.com"
SECRETS_SCOPE = "airport-sim"
SECRET_KEY = "rapidapi-key"
_BASE_URL = f"https://{RAPIDAPI_HOST}/flights/airports/iata/{AIRPORT_IATA}"

# ── Latest parsed rosters (populated by refresh(), read by the loaders) ─────────
_arrivals: list[dict] = []
_departures: list[dict] = []
_last_status: dict = {"stage": "not-run"}  # last fetch/parse outcome, for /api/live-debug
_current_slot: datetime | None = None      # 4-hour slot whose data is currently published


def get_arrivals() -> list[dict]:
    return list(_arrivals)


def get_departures() -> list[dict]:
    return list(_departures)


def debug_status() -> dict:
    """Last fetch/parse outcome + current roster sizes, for a diagnostic
    endpoint. Never includes the API key."""
    return {**_last_status, "arrivals": len(_arrivals), "departures": len(_departures),
            "base_url": _BASE_URL, "offset_minutes": OFFSET_MINUTES,
            "window_minutes": WINDOW_MINUTES, "slot_hours": SLOT_HOURS,
            "current_slot": _current_slot.isoformat() if _current_slot else None}


# ── Aircraft model-name → short ICAO-ish type code ──────────────────────────────
# The short codes must land in the buckets gates.capacity_class_of() and the
# frontend Aircraft3D.jsx::resolveModel() recognise:
#   A380  -> A388 (A380 gate class)
#   WIDE  -> A359 / A35K / A333 / B77W / B788 / B789
#   NARROW-> A320 / A21N (everything else, incl. 737s / regional jets)
# Ordered list: first regex whose pattern matches the model string wins.
_MODEL_RULES: list[tuple[str, str]] = [
    (r"A380|A388",              "A388"),
    (r"A350-?1000|A35K",        "A35K"),
    (r"A350",                   "A359"),
    (r"A330|A33[0-9]",          "A333"),
    (r"777",                    "B77W"),
    (r"787-?9|B789",            "B789"),
    (r"787-?8|B788|787",        "B788"),
    (r"767",                    "B788"),   # widebody -> nearest wide model we render
    (r"747",                    "B77W"),
    (r"A321|A32[1-9]|A21N",     "A21N"),
    (r"A320|A319|A318",         "A320"),
    (r"737|B73[0-9]|MAX",       "A320"),   # narrowbody -> render as A320-class
    (r"E19|E17|E29|CRJ|RJ|A220","A320"),   # regional jets -> narrow
]
_DEFAULT_TYPE = "A320"  # unknown / missing model: safe NARROW default


def map_model_to_icao(model) -> str:
    """Map an AeroDataBox aircraft.model string ('Airbus A350-900',
    'Boeing 777-300ER', ...) to a short type code the capacity/render logic
    understands. Missing/unknown models fall back to a NARROW default."""
    if not model:
        return _DEFAULT_TYPE
    m = str(model).upper()
    for pattern, code in _MODEL_RULES:
        if re.search(pattern, m):
            return code
    return _DEFAULT_TYPE


# ── Parsing helpers ─────────────────────────────────────────────────────────────
def _parse_utc(movement: dict):
    """Return an aware UTC datetime from a movement's revisedTime (preferred)
    or scheduledTime, or None. AeroDataBox format: 'YYYY-MM-DD HH:MMZ'."""
    for key in ("revisedTime", "scheduledTime"):
        block = movement.get(key) or {}
        raw = block.get("utc")
        if not raw:
            continue
        txt = raw.strip().replace("Z", "").strip()
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(txt, fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    return None


def _parse_time_key(movement: dict, key: str):
    """Parse ONE specific time block ('scheduledTime' or 'revisedTime') to an
    aware UTC datetime, or None if absent. (`_parse_utc` above returns the
    effective time — revised-or-scheduled — used for the sim clock; this returns
    a single named field so we can persist scheduled and revised separately.)"""
    raw = (movement.get(key) or {}).get("utc")
    if not raw:
        return None
    txt = raw.strip().replace("Z", "").strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(txt, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _callsign(flight: dict) -> str | None:
    """Use the operating flight number as the identity ('QR 1238' -> 'QR1238'),
    matching the app's flight_id == flight-number convention; fall back to the
    ATC callSign only if number is absent. Returns None if neither is present."""
    raw = flight.get("number") or flight.get("callSign")
    if not raw:
        return None
    return re.sub(r"\s+", "", str(raw)).upper() or None


def _is_excluded(flight: dict) -> bool:
    """Defensive filter — the API already drops these with the query flags, but
    guarantee we only keep operating, non-cargo, non-cancelled movements."""
    if (flight.get("status") or "").lower() in ("canceled", "cancelled"):
        return True
    if flight.get("isCargo"):
        return True
    if (flight.get("codeshareStatus") or "") == "IsCodeshared":
        return True
    return False


def _dedupe_by_callsign(rows: list[dict]) -> list[dict]:
    """Keep the earliest-scheduled row per callsign (final safety net against
    the same operating flight appearing twice in a window)."""
    best: dict[str, dict] = {}
    for r in rows:
        fid = r["callsign"]
        if fid not in best or r["scheduled_utc"] < best[fid]["scheduled_utc"]:
            best[fid] = r
    return sorted(best.values(), key=lambda r: r["scheduled_utc"])


def parse_departures(payload: dict) -> list[dict]:
    """HIA-side outbound movements → departures-roster shape. The HIA movement
    is `departure` (carries the real gate); the other airport is `arrival`.

    ALL operating departures are kept and shown on the Schedule board (like a
    real FIDS board), regardless of gate. The gate only gates 3D-map placement,
    not the listing: a departure is placed on the map (taxis out) only once it
    has a gate we actually model (data/gateLonLat.json) — see
    departures.advance(). Flights with no gate yet (the airport assigns gates
    close to departure) or a gate we don't model (e.g. C30/C82) still appear on
    the board with their real API gate; they just never get a simulated
    aircraft. We do NOT invent a fallback gate for them (no fake data)."""
    rows = []
    for f in payload.get("departures", []) or []:
        if _is_excluded(f):
            continue
        fid = _callsign(f)
        dep_mv = f.get("departure") or {}
        sched = _parse_utc(dep_mv)
        if not fid or sched is None:
            continue
        gate = dep_mv.get("gate")  # real HIA gate, or None (may be a gate we don't model)
        other = (f.get("arrival") or {}).get("airport") or {}
        rows.append({
            "id":            fid,
            "callsign":      fid,
            "airline":       (f.get("airline") or {}).get("iata") or "QR",
            "aircraft_type": map_model_to_icao((f.get("aircraft") or {}).get("model")),
            "origin":        "DOH",
            "destination":   other.get("iata") or "----",
            "assigned_gate": gate,        # honoured at activation if it exists in the sim
            "scheduled_utc": sched,       # effective time (revised-or-scheduled) — drives the sim clock
            "sched_time_utc":   _parse_time_key(dep_mv, "scheduledTime"),  # original scheduled
            "revised_time_utc": _parse_time_key(dep_mv, "revisedTime"),    # revised/estimated (may be None)
            "aircraft_reg":  (f.get("aircraft") or {}).get("reg"),         # tail number (often absent)
            "baggage_belt":  None,        # departures have no baggage belt
            "api_status":    f.get("status"),  # real-world status: Boarding/Departed/Delayed/Expected/...
        })
    return _dedupe_by_callsign(rows)


def parse_arrivals(payload: dict) -> list[dict]:
    """HIA-side inbound movements → arrivals-roster shape. The HIA movement is
    `arrival` (no HIA gate — only baggageBelt); the other airport is `departure`."""
    rows = []
    for f in payload.get("arrivals", []) or []:
        if _is_excluded(f):
            continue
        fid = _callsign(f)
        arr_mv = f.get("arrival") or {}
        sched = _parse_utc(arr_mv)
        if not fid or sched is None:
            continue
        other = (f.get("departure") or {}).get("airport") or {}
        rows.append({
            "id":            fid,
            "callsign":      fid,
            "airline":       (f.get("airline") or {}).get("iata") or "QR",
            "aircraft_type": map_model_to_icao((f.get("aircraft") or {}).get("model")),
            "origin":        other.get("iata") or "----",
            "destination":   "DOH",
            "assigned_gate": None,        # arrivals reserve a simulated gate at activation
            "scheduled_utc": sched,       # effective time (revised-or-scheduled) — drives the sim clock
            "sched_time_utc":   _parse_time_key(arr_mv, "scheduledTime"),  # original scheduled
            "revised_time_utc": _parse_time_key(arr_mv, "revisedTime"),    # revised/estimated (may be None)
            "aircraft_reg":  (f.get("aircraft") or {}).get("reg"),         # tail number (often absent)
            "baggage_belt":  arr_mv.get("baggageBelt"),                    # arrival baggage carousel
            "api_status":    f.get("status"),  # real-world status: Expected/Arrived/Delayed/...
        })
    return _dedupe_by_callsign(rows)


# ── Fetch ───────────────────────────────────────────────────────────────────────
def _fetch_sync() -> dict | None:
    """Blocking HTTP call (run off the event loop via asyncio.to_thread)."""
    global _last_status
    key = get_secret_value(SECRETS_SCOPE, SECRET_KEY)
    if not key:
        _last_status = {"stage": "no-key"}
        print("[live_schedule] no RapidAPI key (secret airport-sim/rapidapi-key) — skipping fetch.")
        return None
    _last_status = {"stage": "key-ok", "key_len": len(key)}
    params = {
        "offsetMinutes": OFFSET_MINUTES,
        "durationMinutes": WINDOW_MINUTES,
        "withLeg": "true",           # required — without it the per-movement scheduledTime/gate come back empty
        "direction": "Both",
        "withCancelled": "false",
        "withCodeshared": "false",
        "withCargo": "false",
        "withPrivate": "false",
        "withLocation": "false",
    }
    headers = {"X-RapidAPI-Key": key, "X-RapidAPI-Host": RAPIDAPI_HOST}
    try:
        resp = requests.get(_BASE_URL, params=params, headers=headers, timeout=15)
        if resp.ok:
            body = resp.json()
            raw_deps = body.get("departures", []) or []
            sample = {}
            if raw_deps:
                f0 = raw_deps[0]
                dep_mv = f0.get("departure") or {}
                sample = {
                    "number": f0.get("number"),
                    "status": f0.get("status"),
                    "codeshareStatus": f0.get("codeshareStatus"),
                    "isCargo": f0.get("isCargo"),
                    "dep_keys": list(dep_mv.keys())[:8],
                    "scheduledTime": dep_mv.get("scheduledTime"),
                    "revisedTime": dep_mv.get("revisedTime"),
                }
            _last_status = {
                "stage": "http-ok", "http": resp.status_code, "key_len": len(key),
                "raw_dep": len(raw_deps),
                "raw_arr": len(body.get("arrivals", []) or []),
                "top_keys": list(body.keys())[:6],
                "sample": sample,
            }
            return body
        _last_status = {"stage": "http-error", "http": resp.status_code,
                        "body": resp.text[:300], "key_len": len(key)}
        print(f"[live_schedule] HTTP {resp.status_code}: {resp.text[:200]}")
    except Exception as e:  # noqa: BLE001
        _last_status = {"stage": "exception", "error": f"{type(e).__name__}: {e}", "key_len": len(key)}
        print(f"[live_schedule] fetch error: {e}")
    return None


async def fetch_window() -> dict | None:
    """Fetch the raw AeroDataBox payload for the configured window, or None on
    any failure (never raises — callers keep whatever they last had)."""
    return await asyncio.to_thread(_fetch_sync)


# ── 4-hour slot + Lakebase payload cache ────────────────────────────────────────
def _slot_start(now: datetime) -> datetime:
    """The current 4-hour UTC slot boundary (00:00 / 04:00 / 08:00 / …)."""
    h = (now.hour // SLOT_HOURS) * SLOT_HOURS
    return now.replace(hour=h, minute=0, second=0, microsecond=0)


def _seconds_to_next_slot(now: datetime) -> float:
    """Seconds until the next 4-hour boundary (always > 0)."""
    from datetime import timedelta
    nxt = _slot_start(now) + timedelta(hours=SLOT_HOURS)
    return max(1.0, (nxt - now).total_seconds())


async def ensure_cache_table() -> None:
    if db.is_demo_mode:
        return
    await db.execute(
        f"""CREATE TABLE IF NOT EXISTS {_CACHE_TABLE} (
              slot_start timestamptz PRIMARY KEY,
              payload    jsonb NOT NULL,
              fetched_at timestamptz NOT NULL DEFAULT now()
            )"""
    )


async def _load_cached_payload(slot: datetime) -> dict | None:
    """Return the cached raw payload for `slot` if present, else None."""
    if db.is_demo_mode:
        return None
    try:
        row = await db.fetchrow(f"SELECT payload FROM {_CACHE_TABLE} WHERE slot_start=$1", slot)
    except Exception as e:  # noqa: BLE001
        print(f"[live_schedule] cache read error: {e}")
        return None
    if not row:
        return None
    raw = row["payload"]
    return json.loads(raw) if isinstance(raw, str) else raw


async def _store_cached_payload(slot: datetime, payload: dict) -> None:
    if db.is_demo_mode:
        return
    try:
        await db.execute(
            f"""INSERT INTO {_CACHE_TABLE} (slot_start, payload) VALUES ($1, $2::jsonb)
                ON CONFLICT (slot_start) DO UPDATE SET payload=EXCLUDED.payload, fetched_at=now()""",
            slot, json.dumps(payload),
        )
        # keep only the last few slots
        await db.execute(
            f"DELETE FROM {_CACHE_TABLE} WHERE slot_start < $1 - interval '2 days'", slot,
        )
    except Exception as e:  # noqa: BLE001
        print(f"[live_schedule] cache write error: {e}")


async def _publish(payload: dict, now: datetime) -> None:
    """Upsert the FULL fetched window into the persistent dataset (flight_store),
    then publish the live rosters by LOADING them back from that dataset.

    The full window (incl. the -4h tail) is upserted so already-operated flights
    get their final status/gate written to their row (history). The rosters we
    then expose are loaded from the store with its board filter (scheduled >=
    now-10min), so the board/map start ~10 min in the past and look forward.

    The dataset is the source of truth: even a flight the current payload dropped
    (e.g. slid out of the window) but that's still within the board window remains
    visible, because we read from the table, not just this payload."""
    global _arrivals, _departures
    await flight_store.upsert(parse_arrivals(payload), parse_departures(payload))
    _arrivals, _departures = await flight_store.load_window(now)


async def refresh(now: datetime | None = None, force: bool = False) -> bool:
    """Publish rosters for the current 4-hour slot, calling the AeroDataBox API
    AT MOST once per slot. Order of preference:
      1. If we already published this slot's data in-process, no-op (return False).
      2. Else if Lakebase has this slot's cached payload, reuse it (NO API call).
         → this is what makes deploys / tests free: a restart mid-slot reloads
           from cache instead of hitting the API.
      3. Else call the API once, publish, and cache the payload for the slot.
    `force=True` bypasses the in-process/cache checks (manual override only)."""
    global _current_slot, _last_status
    now = now or datetime.now(timezone.utc)
    slot = _slot_start(now)

    if not force and _current_slot == slot and (_arrivals or _departures):
        return False  # already have this slot's data live

    if not force:
        cached = await _load_cached_payload(slot)
        if cached is not None:
            await _publish(cached, now)
            _current_slot = slot
            _last_status = {"stage": "cache-hit", "slot": slot.isoformat(),
                            "arrivals": len(_arrivals), "departures": len(_departures)}
            print(f"[live_schedule] slot {slot:%H:%M}Z served from cache "
                  f"({len(_arrivals)} arr / {len(_departures)} dep) — no API call.")
            return True

    payload = await fetch_window()
    if payload is None:
        return False
    await _publish(payload, now)
    _current_slot = slot
    await _store_cached_payload(slot, payload)
    print(f"[live_schedule] slot {slot:%H:%M}Z FETCHED from API "
          f"({len(_arrivals)} arr / {len(_departures)} dep) and cached.")
    return True


async def run_loop() -> None:
    """Slot-aligned refresh loop. The startup fetch/cache-load is done in
    broadcaster._load_world() (before the roster loaders run); this loop sleeps
    until each 4-hour UTC boundary (00:00/04:00/…), then refreshes and hands the
    new window to the two roster owners' merge hooks."""
    # Local imports to avoid a circular import at module load.
    from server.simulator import seed_runner, departures, broadcaster
    from server.websocket_manager import manager
    while True:
        try:
            now = broadcaster.SIM["sim_time"]
            await asyncio.sleep(_seconds_to_next_slot(now))
            if await refresh(broadcaster.SIM["sim_time"]):
                now = broadcaster.SIM["sim_time"]
                removed = await seed_runner.refresh_roster(get_arrivals(), now)
                await departures.refresh_roster(get_departures())
                # Tell open tabs to drop any arrivals that were reverted to
                # SCHEDULED (e.g. a delay published between refreshes) or dropped
                # from the window — each re-lands at its corrected time.
                if removed:
                    await manager.broadcast({
                        "type": "aircraft_update",
                        "flights": [{"id": fid, "phase": "INACTIVE"} for fid in removed],
                    })
        except Exception as e:  # noqa: BLE001
            print(f"[live_schedule] refresh loop error: {e}")
