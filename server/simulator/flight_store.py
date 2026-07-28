"""Persistent flight dataset (Lakebase) — ONE row per flight, upserted.

This is the durable, query-friendly source of truth for the live schedule,
replacing the transient `flight_feed_cache` raw-JSON blob as the app's READ
source. Every AeroDataBox fetch (or cached-payload reload) upserts its parsed
arrivals + departures here, keyed by (flight_date, flight_number, direction), so
re-observing the same flight UPDATES its row in place (new scheduled time, gate,
status) rather than appending — exactly one authoritative record per flight.

Why:
  - The fetch window is [now-4h, now+8h]. The -4h tail lets already-operated
    flights get their FINAL status/gate written to their row (historical update);
    the +8h head gives the map/board upcoming flights to simulate.
  - Because it's a real columnar table (not a JSON cache that's trimmed every 2
    days), the app doubles as a growing dataset for historical analysis
    (BI / Genie / ML) — the Databricks value story.

Read path: load_window(now) returns the roster rows the simulator consumes,
filtered to scheduled_utc >= now - _BOARD_GRACE (so the board/map start ~10 min
in the past and look forward); older rows stay in the table for history but
aren't loaded into the live roster.

NOTE: only the REAL API fields (schedule, gate, status) are stored here — they're
genuine observations. Sim-derived facts (which gate we actually assigned, taxi
duration) are NOT persisted here; they're runtime-only and demo-synthetic.
"""

from datetime import datetime, timedelta, timezone

from server.db import db

_TABLE = "flight_schedule"
_BOARD_GRACE_SEC = 10 * 60.0   # board/map show flights scheduled >= now - 10 min

# Gate-occupancy dwell (must match the sim's own dwell so the derived window equals
# what actually rendered) — same values gate_log used before it was retired:
#   arrival:   touchdown            → touchdown + _ARR_DWELL_SEC   (30 min at gate)
#   departure: pushback − _DEP_LEAD_SEC → pushback                 (45 min at gate)
_ARR_DWELL_SEC = 30 * 60.0
_DEP_LEAD_SEC = 45 * 60.0


async def ensure_table() -> None:
    """Create the flight dataset table if absent. Called once at startup."""
    if db.is_demo_mode:
        return
    await db.execute(
        f"""CREATE TABLE IF NOT EXISTS {_TABLE} (
              flight_date   date        NOT NULL,
              flight_number text        NOT NULL,
              direction     text        NOT NULL,   -- 'arrival' | 'departure'
              callsign      text,
              airline       text,
              aircraft_type text,
              origin        text,
              destination   text,
              scheduled_utc timestamptz,      -- effective time (revised-or-scheduled); drives the sim clock
              sched_time_utc  timestamptz,     -- original scheduled time from the API
              revised_time_utc timestamptz,    -- revised/estimated time from the API (may be null)
              api_gate      text,
              sim_gate      text,        -- gate the SIMULATION actually used (runtime; see set_sim_gate)
              aircraft_reg  text,        -- tail number (often absent in the feed)
              baggage_belt  text,        -- arrival baggage carousel (arrivals only)
              api_status    text,
              updated_at    timestamptz NOT NULL DEFAULT now(),
              PRIMARY KEY (flight_date, flight_number, direction)
            )"""
    )
    # Add columns to any pre-existing table (created before these existed).
    for col, typ in (("sim_gate", "text"), ("sched_time_utc", "timestamptz"),
                     ("revised_time_utc", "timestamptz"), ("aircraft_reg", "text"),
                     ("baggage_belt", "text")):
        await db.execute(f"ALTER TABLE {_TABLE} ADD COLUMN IF NOT EXISTS {col} {typ}")
    # Retire tables from the old design (their code paths are gone) — gate history
    # is derived from flight_schedule.sim_gate; the departure roster + plane_status
    # live in flight_schedule / live_positions now. These drops are BEST-EFFORT:
    # `airport_app` doesn't own admin-created tables, so DROP raises
    # InsufficientPrivilegeError ("must be owner") — which must NOT break startup.
    # Leaving an orphaned empty table is harmless; the code no longer touches it.
    for _t in ("gate_assignments", "departure_schedule", "plane_status"):
        try:
            await db.execute(f"DROP TABLE IF EXISTS {_t}")
        except Exception as e:  # noqa: BLE001
            print(f"[flight_store] could not drop {_t} (leaving it): {e}")


def _rows_for(direction: str, roster_rows: list[dict]) -> list[tuple]:
    """Shape parsed roster rows → upsert tuples for `direction`."""
    out = []
    for r in roster_rows:
        sched: datetime = r["scheduled_utc"]
        out.append((
            sched.date(),                 # flight_date (UTC)
            r["callsign"],                # flight_number == callsign convention
            direction,
            r["callsign"],
            r.get("airline") or "QR",
            r.get("aircraft_type"),
            r.get("origin"),
            r.get("destination"),
            sched,
            r.get("sched_time_utc"),
            r.get("revised_time_utc"),
            r.get("assigned_gate"),       # real API gate (None for arrivals)
            r.get("aircraft_reg"),
            r.get("baggage_belt"),
            r.get("api_status"),
        ))
    return out


async def upsert(arrivals: list[dict], departures: list[dict]) -> int:
    """Upsert a fetched window's arrivals + departures. Re-observing a flight
    updates its single row; returns the number of rows written. No-op in demo mode.

    `api_status` is only refreshed for FUTURE flights (scheduled_utc >= now()) — a
    flight already in the past keeps whatever status it last had, so the -4h tail
    doesn't churn the historical status of flights that have already operated. All
    other fields (times, gate, etc.) still refresh for every row."""
    if db.is_demo_mode:
        return 0
    rows = _rows_for("arrival", arrivals) + _rows_for("departure", departures)
    if not rows:
        return 0
    await db.executemany(
        f"""INSERT INTO {_TABLE}
              (flight_date, flight_number, direction, callsign, airline,
               aircraft_type, origin, destination, scheduled_utc, sched_time_utc,
               revised_time_utc, api_gate, aircraft_reg, baggage_belt, api_status)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
            ON CONFLICT (flight_date, flight_number, direction) DO UPDATE SET
              callsign=EXCLUDED.callsign, airline=EXCLUDED.airline,
              aircraft_type=EXCLUDED.aircraft_type, origin=EXCLUDED.origin,
              destination=EXCLUDED.destination, scheduled_utc=EXCLUDED.scheduled_utc,
              sched_time_utc=EXCLUDED.sched_time_utc,
              revised_time_utc=EXCLUDED.revised_time_utc,
              api_gate=EXCLUDED.api_gate,
              aircraft_reg=COALESCE(EXCLUDED.aircraft_reg, {_TABLE}.aircraft_reg),
              baggage_belt=COALESCE(EXCLUDED.baggage_belt, {_TABLE}.baggage_belt),
              api_status=CASE WHEN EXCLUDED.scheduled_utc >= now()
                              THEN EXCLUDED.api_status
                              ELSE {_TABLE}.api_status END,
              updated_at=now()""",
        rows,
    )
    return len(rows)


# ── sim_gate writes (the gate the simulation actually used) ─────────────────────
# Gate assignment happens inside the synchronous advance() call chain, which can't
# await a DB write — so callers enqueue() the (flight, gate) and broadcaster._tick()
# flushes them asynchronously (same pattern as gate_log).
_pending_gate: list[tuple] = []   # (flight_date, flight_number, direction, sim_gate)


def enqueue_sim_gate(flight_number: str, direction: str, scheduled_utc: datetime,
                     sim_gate: str | None) -> None:
    """Queue the sim-assigned gate for a flight; written to its flight_schedule row
    on the next flush(). Sync — safe to call from advance()."""
    if sim_gate is None:
        return
    _pending_gate.append((scheduled_utc.date(), flight_number, direction, sim_gate))


async def flush_sim_gates() -> None:
    """Persist queued sim_gate updates onto existing flight_schedule rows. No-op in
    demo mode / when nothing is queued."""
    global _pending_gate
    if not _pending_gate:
        return
    rows, _pending_gate = _pending_gate, []
    if db.is_demo_mode:
        return
    await db.executemany(
        f"""UPDATE {_TABLE} SET sim_gate=$4, updated_at=now()
             WHERE flight_date=$1 AND flight_number=$2 AND direction=$3""",
        rows,
    )


def _to_roster_row(rec, direction: str) -> dict:
    """A DB record → the roster-row shape seed_runner / departures consume."""
    return {
        "id":            rec["flight_number"],
        "callsign":      rec["callsign"] or rec["flight_number"],
        "airline":       rec["airline"] or "QR",
        "aircraft_type": rec["aircraft_type"],
        "origin":        rec["origin"] if direction == "arrival" else "DOH",
        "destination":   "DOH" if direction == "arrival" else rec["destination"],
        # arrivals reserve a simulated gate at activation → always None here;
        # departures carry the real API gate (may be None / unmodelled).
        "assigned_gate": None if direction == "arrival" else rec["api_gate"],
        "scheduled_utc": rec["scheduled_utc"],
        "api_status":    rec["api_status"],
    }


async def load_window(now: datetime) -> tuple[list[dict], list[dict]]:
    """Return (arrivals, departures) roster rows the simulator should hold right
    now: scheduled_utc >= now - _BOARD_GRACE_SEC, sorted ascending. Older rows
    remain in the table (history) but aren't loaded into the live roster."""
    if db.is_demo_mode:
        return [], []
    cutoff = now - timedelta(seconds=_BOARD_GRACE_SEC)
    recs = await db.fetch(
        f"""SELECT flight_number, callsign, airline, aircraft_type, origin,
                   destination, scheduled_utc, api_gate, api_status, direction
              FROM {_TABLE}
             WHERE scheduled_utc >= $1
             ORDER BY scheduled_utc""",
        cutoff,
    )
    arrivals, departures = [], []
    for rec in recs:
        if rec["direction"] == "arrival":
            arrivals.append(_to_roster_row(rec, "arrival"))
        else:
            departures.append(_to_roster_row(rec, "departure"))
    return arrivals, departures


async def movements_per_hour(now: datetime, hours_back: int = 12) -> list[dict]:
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
              FROM {_TABLE}
             WHERE scheduled_utc >= $1 AND scheduled_utc <= $2
             GROUP BY hr, direction
             ORDER BY hr""",
        since, now,
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


async def recent_gate_history(now: datetime, limit_per_gate: int = 5) -> dict[str, list[dict]]:
    """The last `limit_per_gate` (default 5) aircraft appearances PER GATE, DERIVED
    from the flight_schedule dataset (keyed on `sim_gate` — the gate each flight
    actually used). Not time-windowed: every gate shows its most recent stays even
    if they're old. For each flight the occupancy window is computed from its real
    scheduled time + fixed dwell (arrival: touchdown→+30min; departure:
    pushback−45min→pushback). Returned grouped by gate, most-recent-first — the
    exact shape the Gates page consumes. Empty dict in demo mode."""
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
                  FROM {_TABLE}
                 WHERE sim_gate IS NOT NULL
              ) t
             WHERE rn <= $1
             ORDER BY sim_gate, scheduled_utc DESC""",
        limit_per_gate,
    )
    out: dict[str, list[dict]] = {}
    for r in recs:
        sched = r["scheduled_utc"]
        if r["direction"] == "arrival":
            occ_from, occ_until = sched, sched + timedelta(seconds=_ARR_DWELL_SEC)
        else:
            occ_from, occ_until = sched - timedelta(seconds=_DEP_LEAD_SEC), sched
        out.setdefault(r["sim_gate"], []).append({
            "flight_id":      r["flight_number"],
            "direction":      r["direction"],
            "api_status":     r["api_status"],
            "occupied_from":  occ_from.isoformat(),
            "occupied_until": occ_until.isoformat(),
            "duration_min":   round((occ_until - occ_from).total_seconds() / 60.0, 1),
        })
    return out


async def debug_sample() -> dict:
    """Diagnostic: row count + per-column non-null coverage + a few sample rows
    from flight_schedule. Lets /api/live-debug confirm the upsert is persisting
    all columns (incl. sched/revised time, aircraft_reg, baggage_belt, sim_gate).
    Read-only; empty in demo mode."""
    if db.is_demo_mode:
        return {"demo_mode": True}
    total = await db.fetchval(f"SELECT count(*) FROM {_TABLE}")
    cov_cols = ("sched_time_utc", "revised_time_utc", "api_gate", "sim_gate",
                "aircraft_reg", "baggage_belt", "api_status")
    coverage = {}
    for c in cov_cols:
        coverage[c] = await db.fetchval(
            f"SELECT count({c}) FROM {_TABLE}"  # count(col) ignores NULLs
        )
    recs = await db.fetch(
        f"""SELECT flight_number, direction, aircraft_type, scheduled_utc,
                   sched_time_utc, revised_time_utc, api_gate, sim_gate,
                   aircraft_reg, baggage_belt, api_status, updated_at
              FROM {_TABLE} ORDER BY updated_at DESC LIMIT 3"""
    )
    def iso(v):
        return v.isoformat() if hasattr(v, "isoformat") else v
    samples = [{k: iso(r[k]) for k in r.keys()} for r in recs]
    return {"total_rows": total, "non_null_coverage": coverage, "recent_rows": samples}
