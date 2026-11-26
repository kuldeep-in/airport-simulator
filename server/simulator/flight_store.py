"""The flight dataset (Lakebase) — READ side for the app and generator.

Source: the airport's TRIGGERED synced table (airports.FLIGHT_TABLE, e.g.
`airport_simulator_doh.flight_schedule_doh`), maintained by the ingest job:

    AeroDataBox → UC volume → SDP bronze/silver → silver_flight_schedule_<iata> (SCD1)
                → synced table in Lakebase (one row per flight, read-only)

Nothing here writes flight rows any more (that was the old fetcher's upsert). The one
sim-derived fact we persist is the gate the simulation actually used, in
`sim_gate_assignment` (same key), which every read LEFT JOINs back in as `sim_gate`.

Read path: load_window(now) returns the roster rows the simulator consumes; the
board / dashboard / gate-history helpers below query the same joined source.
"""

import zlib
from datetime import datetime, timedelta, timezone

from server import airports
from server.db import db

# The flight dataset: the airport's read-only synced table (see server/airports.py).
_TABLE = airports.FLIGHT_TABLE
# The gate the SIMULATION used per flight — written by the generator, joined on read.
_GATES = "sim_gate_assignment"
# Read source: flight dataset + its sim gate, so every query can keep using `sim_gate`.
_SRC = (f"(SELECT f.*, g.sim_gate FROM {_TABLE} f "
        f"LEFT JOIN {_GATES} g USING (airport, flight_date, flight_number, direction)) fs")
_BOARD_GRACE_SEC = 10 * 60.0   # board/map show flights scheduled >= now - 10 min
# The SIMULATOR roster (load_window) looks much further back than the board: an
# arrival stays on the ground up to taxi(~15m)+dwell(30m), and a just-pushed-back
# departure taxis out for ~15m. Loading that whole tail is what lets the generator
# RECONSTRUCT flights still on the airport after a restart or an airport switch
# (see seed_runner/departures reconstruction), instead of only ever activating from
# the touchdown/pushback moment forward. Flights past their on-ground life are
# loaded too but the reconstruction step drops them, so a generous value is safe.
_SIM_LOOKBACK_SEC = 50 * 60.0

# Gate-occupancy dwell for the DERIVED gate-history windows (Gates page).
#
# Two regimes, split by a FIXED cutover (roughly the deploy moment):
#   - Flights scheduled BEFORE _DWELL_SPREAD_CUTOFF ("existing" at rollout) keep the
#     original fixed dwell (arrival 30 min, departure 45 min) forever, so their shown
#     gate time never changes retroactively.
#   - Flights scheduled AT/AFTER the cutover ("future") get a deterministic
#     pseudo-random dwell within a per-direction range, so the history reads as organic
#     turnaround times rather than "always 30 / always 45".
# The cutover is a fixed timestamp (not `now`) on purpose: a flight's dwell stays
# stable as it crosses from future into past — it never flips regime.
#
# For ARRIVALS this is also the sim's real on-gate time — seed_runner._ground_time_sec()
# calls _dwell_seconds, so the map and this history agree per flight. Departures' map
# lead (30–45 min, departures.py) is separate from their display dwell here.
#   arrival:   touchdown            → touchdown + dwell
#   departure: pushback − dwell     → pushback
_DWELL_SPREAD_CUTOFF = datetime(2026, 8, 19, 20, 50, 0, tzinfo=timezone.utc)
_ARR_DWELL_SEC = 30 * 60.0        # legacy fixed dwell for pre-cutover arrivals
_DEP_LEAD_SEC = 45 * 60.0         # legacy fixed dwell for pre-cutover departures
_ARR_DWELL_MIN_RANGE = (25, 55)   # minutes — post-cutover arrivals
_DEP_DWELL_MIN_RANGE = (30, 50)   # minutes — post-cutover departures


def _dwell_seconds(flight_number: str, direction: str, sched: datetime) -> float:
    """Gate dwell (seconds) for the history display.

    Pre-cutover flights keep the legacy fixed dwell (30 min arr / 45 min dep).
    Post-cutover flights get a deterministic pseudo-random dwell, seeded off the
    flight number + its scheduled date via zlib.crc32 (NOT the builtin hash(),
    which is salted per-process and would change every restart), so the same flight
    always shows the same dwell across refreshes/restarts. Spread into a whole-minute
    value inside the per-direction range."""
    if sched < _DWELL_SPREAD_CUTOFF:
        return _ARR_DWELL_SEC if direction == "arrival" else _DEP_LEAD_SEC
    lo, hi = _ARR_DWELL_MIN_RANGE if direction == "arrival" else _DEP_DWELL_MIN_RANGE
    seed = f"{flight_number}|{direction}|{sched.date().isoformat()}".encode()
    dwell_min = lo + (zlib.crc32(seed) % (hi - lo + 1))
    return dwell_min * 60.0


async def ensure_table() -> None:
    """Create the sim-side gate table if absent. Called once at startup (app + generator).

    The flight dataset itself (airports.FLIGHT_TABLE) is NOT created here: it's the
    TRIGGERED synced table the ingest job maintains (UC silver → Lakebase), and it's
    read-only. The one sim-derived fact we persist — the gate the simulation actually
    used — lives in `sim_gate_assignment`, keyed like the dataset and LEFT JOINed on read."""
    if db.is_demo_mode:
        return
    await db.execute(
        f"""CREATE TABLE IF NOT EXISTS {_GATES} (
              airport       text        NOT NULL,
              flight_date   date        NOT NULL,
              flight_number text        NOT NULL,
              direction     text        NOT NULL,
              sim_gate      text        NOT NULL,
              assigned_at   timestamptz NOT NULL DEFAULT now(),
              PRIMARY KEY (airport, flight_date, flight_number, direction)
            )"""
    )
    await db.execute(f"CREATE INDEX IF NOT EXISTS {_GATES}_gate_idx ON {_GATES} (airport, sim_gate)")
    await _retire_legacy_dataset()


# Retired tables from the pre-Delta design (the old Python fetcher wrote them). Their
# full contents were archived to UC <catalog>.airport_simulator_doh.flight_schedule_legacy_doh
# before retirement. Owned by the app's Postgres role, so only the app/generator can drop them.
_LEGACY_DATASET = "public.flight_schedule"
_LEGACY_CACHE = "public.flight_feed_cache"


async def _retire_legacy_dataset() -> None:
    """One-time, idempotent migration off the legacy table: copy its sim_gate history
    into sim_gate_assignment (so the Gates page keeps its history), then drop the legacy
    dataset + raw-payload cache. A no-op once they're gone. Best-effort: never breaks
    startup, and the legacy table is only dropped if the copy succeeded."""
    try:
        if await db.fetchval("SELECT to_regclass($1) IS NOT NULL", _LEGACY_DATASET):
            await db.execute(
                f"""INSERT INTO {_GATES} (airport, flight_date, flight_number, direction, sim_gate, assigned_at)
                    SELECT airport, flight_date, flight_number, direction, sim_gate, updated_at
                      FROM {_LEGACY_DATASET} WHERE sim_gate IS NOT NULL
                    ON CONFLICT (airport, flight_date, flight_number, direction) DO NOTHING""")
            n = await db.fetchval(f"SELECT count(*) FROM {_GATES}")
            await db.execute(f"DROP TABLE {_LEGACY_DATASET}")
            print(f"[flight_store] migrated sim_gate history ({n} rows in {_GATES}); "
                  f"dropped legacy {_LEGACY_DATASET}")
        if await db.fetchval("SELECT to_regclass($1) IS NOT NULL", _LEGACY_CACHE):
            await db.execute(f"DROP TABLE {_LEGACY_CACHE}")
            print(f"[flight_store] dropped legacy {_LEGACY_CACHE}")
    except Exception as e:  # noqa: BLE001
        print(f"[flight_store] legacy retirement skipped: {e}")


# ── sim_gate writes (the gate the simulation actually used) ─────────────────────
# Gate assignment happens inside the synchronous advance() call chain, which can't
# await a DB write — so callers enqueue() the (flight, gate) and broadcaster._tick()
# flushes them asynchronously (same pattern as gate_log).
_pending_gate: list[tuple] = []   # (flight_date, flight_number, direction, sim_gate)


def enqueue_sim_gate(flight_number: str, direction: str, scheduled_utc: datetime,
                     sim_gate: str | None) -> None:
    """Queue the sim-assigned gate for a flight; written to sim_gate_assignment on the
    next flush(). Sync — safe to call from advance().

    `scheduled_utc` is the sim's EFFECTIVE time, which may fall on a different UTC
    date than the row's `flight_date` (keyed on the ORIGINAL scheduled time — see
    the silver pipeline). So it's matched as a time RANGE around that instant rather than an
    exact date equality, which would silently update zero rows for a flight delayed
    across midnight."""
    if sim_gate is None:
        return
    _pending_gate.append((flight_number, direction, sim_gate, scheduled_utc))


async def flush_sim_gates(airport: str = "DOH") -> None:
    """Persist queued sim_gate updates into sim_gate_assignment for the active `airport`
    (only one airport is simulated at a time), resolving each flight's dataset key.
    No-op in demo mode / when nothing is queued."""
    global _pending_gate
    if not _pending_gate:
        return
    rows, _pending_gate = _pending_gate, []
    if db.is_demo_mode:
        return
    # Match on airport + flight_number + direction + a ±1-day scheduled_utc window
    # instead of the exact flight_date, so a flight whose effective time crossed UTC
    # midnight still resolves to its single row (see enqueue_sim_gate).
    tagged = [(fn, direction, gate, sched, airport) for (fn, direction, gate, sched) in rows]
    await db.executemany(
        f"""INSERT INTO {_GATES} (airport, flight_date, flight_number, direction, sim_gate)
            SELECT airport, flight_date, flight_number, direction, $3
              FROM {_TABLE}
             WHERE flight_number=$1 AND direction=$2 AND airport=$5
               AND scheduled_utc BETWEEN $4::timestamptz - interval '1 day'
                                     AND $4::timestamptz + interval '1 day'
            ON CONFLICT (airport, flight_date, flight_number, direction)
            DO UPDATE SET sim_gate=EXCLUDED.sim_gate, assigned_at=now()""",
        tagged,
    )


def _to_roster_row(rec, direction: str) -> dict:
    """A DB record → the roster-row shape seed_runner / departures consume."""
    return {
        "id":            rec["flight_number"],
        "callsign":      rec["callsign"] or rec["flight_number"],
        "airline":       rec["airline"] or "QR",
        "aircraft_type": rec["aircraft_type"],
        # origin/destination are stored correctly per airport now (the home airport
        # is the destination for an arrival, the origin for a departure).
        "origin":        rec["origin"],
        "destination":   rec["destination"],
        # arrivals reserve a simulated gate at activation → always None here;
        # departures carry the real API gate (may be None / unmodelled).
        "assigned_gate": None if direction == "arrival" else rec["api_gate"],
        # The gate the sim PREVIOUSLY used for this flight (persisted). On
        # reconstruction (restart / airport switch) reusing it keeps the aircraft
        # at the same stand + route it had before, so positions don't jump.
        "sim_gate":      rec["sim_gate"],
        "scheduled_utc": rec["scheduled_utc"],
        "api_status":    rec["api_status"],
        # Tail number — carried into the sim roster so each per-tick position row
        # can record WHICH AIRFRAME it belongs to (live_positions.aircraft_reg).
        # Sparse in the feed (the airline assigns a tail close to departure), hence
        # nullable and never used as a key. See positions.py for why it matters.
        "aircraft_reg":  rec["aircraft_reg"],
    }


async def load_window(now: datetime, airport: str = "DOH") -> tuple[list[dict], list[dict]]:
    """Return (arrivals, departures) roster rows the simulator should hold right
    now for `airport`: scheduled_utc >= now - _BOARD_GRACE_SEC, sorted ascending.
    Older rows remain in the table (history) but aren't loaded into the live roster."""
    if db.is_demo_mode:
        return [], []
    cutoff = now - timedelta(seconds=_SIM_LOOKBACK_SEC)
    recs = await db.fetch(
        f"""SELECT flight_number, callsign, airline, aircraft_type, origin,
                   destination, scheduled_utc, api_gate, sim_gate, api_status,
                   direction, aircraft_reg
              FROM {_SRC}
             WHERE scheduled_utc >= $1 AND airport = $2
             ORDER BY scheduled_utc""",
        cutoff, airport,
    )
    arrivals, departures = [], []
    for rec in recs:
        if rec["direction"] == "arrival":
            arrivals.append(_to_roster_row(rec, "arrival"))
        else:
            departures.append(_to_roster_row(rec, "departure"))
    return arrivals, departures


async def fetch_status(now: datetime, airport: str = "DOH") -> dict:
    """When was `flight_schedule` last written, and how far ahead does it reach?

    Lets the app report INGEST health without any API client (the old live_schedule.py was 544 lines
    of AeroDataBox client, RapidAPI key handling and slot caching that a web-facing
    process has no business holding). Derived purely from the data:

      last_write        — max(fetched_at); when the newest landed fetch was taken
      horizon_utc       — max(scheduled_utc); the forward edge of the window
      horizon_hours     — hours of forward coverage remaining. A fetch covers +8h, and
                          the fetcher runs 4-hourly, so this should sit between ~4 and 8.
                          Falling toward 0 means the fetcher has stopped — the exact
                          failure that silently thinned the schedule board before.
      stale             — True if nothing has been written for over 5 hours (one missed
                          4-hour slot plus grace)
    """
    if db.is_demo_mode:
        return {"demo_mode": True}
    row = await db.fetchrow(
        f"""SELECT max(fetched_at) AS last_write,
                   max(scheduled_utc) AS horizon,
                   count(*) FILTER (WHERE scheduled_utc >= $1) AS future_rows
              FROM {_SRC}
             WHERE airport = $2"""
        , now, airport)
    if not row or row["last_write"] is None:
        return {"last_write": None, "stale": True, "detail": f"{_TABLE} is empty"}
    age_h = (now - row["last_write"]).total_seconds() / 3600.0
    horizon_h = ((row["horizon"] - now).total_seconds() / 3600.0) if row["horizon"] else 0.0
    return {
        "last_write": row["last_write"].isoformat(),
        "last_write_hours_ago": round(age_h, 2),
        "horizon_utc": row["horizon"].isoformat() if row["horizon"] else None,
        "horizon_hours": round(horizon_h, 2),
        "future_rows": row["future_rows"],
        "stale": age_h > 5.0,
    }


async def board_window(now: datetime, airport: str = "DOH") -> tuple[list, list]:
    """Return (arrival_records, departure_records) for the Schedule BOARD, read
    LIVE from the dataset at request time.

    Deliberately separate from load_window() above, which feeds the SIMULATOR and
    must keep returning the roster-row shape it already does. This one returns raw
    DB records and applies the board's single time rule:

        scheduled_utc >= now - _BOARD_GRACE_SEC   (10 min), no upper bound

    `now` is the REQUEST time, which is the point of this function: load_window's
    cutoff is evaluated once per 4-hour slot at publish time and then slides ever
    further into the past as the slot ages, so the board's own filtering had to be
    redone per request downstream. Here the cutoff is always genuinely `now - 10min`.

    This is now the ONLY past-flight filter for either direction — departures'
    _SCHEDULE_PAST_WINDOW_SEC (15 min) and arrivals' implicit GONE skip were both
    removed in favour of it. Empty in demo mode."""
    if db.is_demo_mode:
        return [], []
    cutoff = now - timedelta(seconds=_BOARD_GRACE_SEC)
    recs = await db.fetch(
        f"""SELECT flight_number, callsign, airline, aircraft_type, origin,
                   destination, origin_name, destination_name, scheduled_utc,
                   api_gate, sim_gate, api_status, direction
              FROM {_SRC}
             WHERE scheduled_utc >= $1 AND airport = $2
             ORDER BY scheduled_utc""",
        cutoff, airport,
    )
    arrivals, departures = [], []
    for rec in recs:
        (arrivals if rec["direction"] == "arrival" else departures).append(rec)
    return arrivals, departures


async def movements_per_hour(now: datetime, airport: str = "DOH", hours_back: int = 12) -> list[dict]:
    """Arrivals + departures counted per clock hour over the last `hours_back`
    hours, from the persisted flight_schedule dataset (so it reflects real
    history, not just the live board window). Returns rows sorted oldest→newest:
      { hour: '<ISO hour>', label: 'HH', arrivals: n, departures: n }
    Empty list in demo mode."""
    if db.is_demo_mode:
        return []
    since = now - timedelta(hours=hours_back)
    recs = await db.fetch(
        f"""SELECT date_trunc('hour', scheduled_utc) AS hr,
                   direction,
                   count(*) AS n
              FROM {_SRC}
             WHERE scheduled_utc >= $1 AND scheduled_utc <= $2 AND airport = $3
             GROUP BY hr, direction
             ORDER BY hr""",
        since, now, airport,
    )
    by_hour: dict = {}
    for r in recs:
        hr = r["hr"]
        slot = by_hour.setdefault(hr, {"arrivals": 0, "departures": 0})
        if r["direction"] == "arrival":
            slot["arrivals"] = r["n"]
        else:
            slot["departures"] = r["n"]
    out = []
    for hr in sorted(by_hour):
        s = by_hour[hr]
        out.append({"hour": hr.isoformat(), "label": hr.strftime("%H"),
                    "arrivals": s["arrivals"], "departures": s["departures"]})
    return out


async def demand_curve(now: datetime, airport: str = "DOH", hours_back: int = 1,
                       hours_ahead: int = 3, bucket_minutes: int = 30) -> list[dict]:
    """Arrivals + departures per `bucket_minutes` bucket across a window that
    STRADDLES now: [now - hours_back, now + hours_ahead].

    Powers the Dashboard's "Live demand" curve. This needs its OWN query rather
    than reusing /api/schedule, because that endpoint is deliberately
    forward-looking: board_window() only returns flights scheduled >= now - 10 min.
    Building the curve client-side from that list therefore left every bucket left
    of `now` structurally empty — the flat zero stretch on the left half of the
    chart. Querying the dataset directly gives the real past-hour counts, since rows
    persist there after a flight has operated.

    Buckets are generated for the WHOLE window, so quiet periods report a genuine
    zero rather than being absent. Rows are oldest→newest:
      { start: '<ISO bucket start>', label: 'HH:MM' (UTC), arrivals: n, departures: n }
    Empty list in demo mode."""
    if db.is_demo_mode:
        return []
    bucket = timedelta(minutes=bucket_minutes)
    start = _floor_to_bucket(now - timedelta(hours=hours_back), bucket_minutes)
    # Emit exactly (hours_back + hours_ahead) worth of buckets. Flooring `start` moves it
    # back by up to one bucket, so deriving `end` from the floored start (not from
    # `now`) keeps the count fixed instead of occasionally yielding one extra
    # part-filled bucket on the right edge.
    n_buckets = int(((hours_back + hours_ahead) * 60) // bucket_minutes)
    end = start + n_buckets * bucket
    recs = await db.fetch(
        # to_timestamp(floor(epoch / width) * width) buckets at an arbitrary width
        # (30 min isn't a date_trunc unit).
        f"""SELECT to_timestamp(
                     floor(EXTRACT(EPOCH FROM scheduled_utc) / $3) * $3
                   ) AS bucket_start,
                   direction, count(*) AS n
              FROM {_SRC}
             WHERE scheduled_utc >= $1 AND scheduled_utc < $2 AND airport = $4
             GROUP BY bucket_start, direction
             ORDER BY bucket_start""",
        start, end, int(bucket.total_seconds()), airport,
    )
    counts: dict = {}
    for r in recs:
        slot = counts.setdefault(r["bucket_start"], {"arrivals": 0, "departures": 0})
        if r["direction"] == "arrival":
            slot["arrivals"] = r["n"]
        else:
            slot["departures"] = r["n"]

    out = []
    t = start
    while t < end:
        s = counts.get(t, {"arrivals": 0, "departures": 0})
        out.append({"start": t.isoformat(), "label": t.strftime("%H:%M"),
                    "arrivals": s["arrivals"], "departures": s["departures"]})
        t += bucket
    return out


def _floor_to_bucket(ts: datetime, bucket_minutes: int) -> datetime:
    """Round `ts` down to the nearest `bucket_minutes` boundary of the hour, so
    bucket edges are stable (…09:00, 09:30, 10:00…) instead of drifting with the
    request time — otherwise every poll would shift the whole curve sideways."""
    floored_min = (ts.minute // bucket_minutes) * bucket_minutes
    return ts.replace(minute=floored_min, second=0, microsecond=0)


async def recent_gate_history(now: datetime, airport: str = "DOH", limit_per_gate: int = 5) -> dict[str, list[dict]]:
    """The last `limit_per_gate` (default 5) aircraft appearances PER GATE, DERIVED
    from the flight_schedule dataset (keyed on `sim_gate` — the gate each flight
    actually used). Not time-windowed: every gate shows its most recent stays even
    if they're old. For each flight the occupancy window is computed from its real
    scheduled time + a deterministic pseudo-random dwell (see _dwell_seconds):
    arrival → touchdown→+dwell; departure → pushback−dwell→pushback. Returned
    grouped by gate, most-recent-first — the exact shape the Gates page consumes.
    Empty dict in demo mode."""
    if db.is_demo_mode:
        return {}
    # Per gate, keep only the N most recent flights by scheduled_utc (ROW_NUMBER
    # window partitioned by sim_gate). Windows are computed in Python below.
    recs = await db.fetch(
        f"""SELECT flight_number, direction, api_status, scheduled_utc, sim_gate
              FROM (
                SELECT flight_number, direction, api_status, scheduled_utc, sim_gate,
                       row_number() OVER (PARTITION BY sim_gate
                                          ORDER BY scheduled_utc DESC) AS rn
                  FROM {_SRC}
                 WHERE sim_gate IS NOT NULL AND airport = $2
              ) t
             WHERE rn <= $1
             ORDER BY sim_gate, scheduled_utc DESC""",
        limit_per_gate, airport,
    )
    out: dict[str, list[dict]] = {}
    for r in recs:
        sched = r["scheduled_utc"]
        dwell = _dwell_seconds(r["flight_number"], r["direction"], sched)
        if r["direction"] == "arrival":
            occ_from, occ_until = sched, sched + timedelta(seconds=dwell)
        else:
            occ_from, occ_until = sched - timedelta(seconds=dwell), sched
        out.setdefault(r["sim_gate"], []).append({
            "flight_id":      r["flight_number"],
            "direction":      r["direction"],
            "api_status":     r["api_status"],
            "occupied_from":  occ_from.isoformat(),
            "occupied_until": occ_until.isoformat(),
            "duration_min":   round((occ_until - occ_from).total_seconds() / 60.0, 1),
        })
    return out


async def gate_utilization_24h(now: datetime, airport: str = "DOH") -> dict[str, float]:
    """Per-gate occupancy % over the trailing 24 h, computed from ALL stays in the
    window (NOT the 5-most-recent cap that recent_gate_history applies for the
    display list). That cap made the KPI structurally wrong: with ~35-55 min dwells,
    5 stays can only ever fill ~19% of 24 h, so no gate could exceed ~19% and the
    amber/red thresholds were unreachable. Here every stay whose occupancy window
    overlaps [now-24h, now] contributes, so the percentage is real.

    Occupancy windows mirror recent_gate_history: arrival = touchdown→+dwell,
    departure = pushback−dwell→pushback (deterministic dwell, see _dwell_seconds).
    Returns {gate_id: pct in 0..100}. Empty in demo mode."""
    if db.is_demo_mode:
        return {}
    win_start = now - timedelta(hours=24)
    # Pull stays scheduled within a padded window so ones whose dwell overlaps the
    # edges are included (a departure boarding before now-24h, or one scheduled just
    # after now whose pre-departure occupancy is already underway). Overlap is then
    # clamped to [win_start, now] below, so the padding can't over-count.
    recs = await db.fetch(
        f"""SELECT flight_number, direction, scheduled_utc, sim_gate
              FROM {_SRC}
             WHERE sim_gate IS NOT NULL AND airport = $1
               AND scheduled_utc >= $2 AND scheduled_utc <= $3""",
        airport,
        win_start - timedelta(hours=2),
        now + timedelta(hours=2),
    )
    occ_sec: dict[str, float] = {}
    for r in recs:
        sched = r["scheduled_utc"]
        dwell = _dwell_seconds(r["flight_number"], r["direction"], sched)
        if r["direction"] == "arrival":
            occ_from, occ_until = sched, sched + timedelta(seconds=dwell)
        else:
            occ_from, occ_until = sched - timedelta(seconds=dwell), sched
        a = max(occ_from, win_start)
        b = min(occ_until, now)
        if b > a:
            occ_sec[r["sim_gate"]] = occ_sec.get(r["sim_gate"], 0.0) + (b - a).total_seconds()
    day_sec = 24 * 60 * 60.0
    return {g: max(0.0, min(100.0, s / day_sec * 100.0)) for g, s in occ_sec.items()}


async def debug_sample() -> dict:
    """Diagnostic: row count + per-column non-null coverage + a few sample rows
    from flight_schedule. Lets /api/live-debug confirm the upsert is persisting
    all columns (incl. sched/revised time, aircraft_reg, baggage_belt, sim_gate).
    Read-only; empty in demo mode."""
    if db.is_demo_mode:
        return {"demo_mode": True}
    total = await db.fetchval(f"SELECT count(*) FROM {_SRC}")
    cov_cols = ("sched_time_utc", "revised_time_utc", "api_gate", "sim_gate",
                "aircraft_reg", "baggage_belt", "api_status")
    coverage = {}
    for c in cov_cols:
        coverage[c] = await db.fetchval(
            f"SELECT count({c}) FROM {_SRC}"  # count(col) ignores NULLs
        )
    recs = await db.fetch(
        f"""SELECT flight_number, direction, aircraft_type, scheduled_utc,
                   sched_time_utc, revised_time_utc, api_gate, sim_gate,
                   aircraft_reg, baggage_belt, api_status, delay_minutes, fetched_at
              FROM {_SRC} ORDER BY fetched_at DESC LIMIT 3"""
    )
    def iso(v):
        return v.isoformat() if hasattr(v, "isoformat") else v
    samples = [{k: iso(r[k]) for k in r.keys()} for r in recs]
    return {"total_rows": total, "non_null_coverage": coverage, "recent_rows": samples}
