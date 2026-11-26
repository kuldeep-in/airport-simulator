"""Historical analytics aggregations over the airport's flight dataset (the Lakebase
synced table, joined with sim_gate_assignment — see flight_store._SRC).

These power the Operations Dashboard's HISTORICAL charts (delay-by-hour heatmap,
rolling on-time trend, gate×hour occupancy heatmap, turnaround distribution,
top-routes-with-delay, airline share) — the things that need aggregation over the
whole accumulated dataset (~2k+ rows), not just the live board window. The near-
term / "as of now" KPIs are computed client-side from /api/schedule; this module
is only the heavier historical rollups, exposed via GET /api/analytics.

All queries are parameterized and guarded for demo mode (return empty). Delay is
`revised_time_utc - sched_time_utc` in minutes, only where both are present.
"""

from datetime import datetime, timedelta

from server.db import db
from server.simulator import flight_store

# Same read source as flight_store: the airport's synced table + its sim gates.
_TABLE = flight_store._SRC

# Delay in minutes = revised - scheduled, only when both timestamps exist.
_DELAY_MIN = ("EXTRACT(EPOCH FROM (revised_time_utc - sched_time_utc)) / 60.0")


async def delay_by_hour(now: datetime, airport: str = "DOH", hours_back: int = 24) -> list[dict]:
    """Average delay (min) per clock hour over the last `hours_back` hours, split
    by direction. Feeds the delay heatmap. Row: {hour ISO, label 'HH',
    arr_delay, dep_delay, arr_n, dep_n}."""
    if db.is_demo_mode:
        return []
    since = now - timedelta(hours=hours_back)
    recs = await db.fetch(
        f"""SELECT date_trunc('hour', scheduled_utc) AS hr, direction,
                   avg({_DELAY_MIN}) AS avg_delay, count(*) AS n
              FROM {_TABLE}
             WHERE scheduled_utc >= $1 AND scheduled_utc <= $2 AND airport = $3
               AND sched_time_utc IS NOT NULL AND revised_time_utc IS NOT NULL
             GROUP BY hr, direction
             ORDER BY hr""",
        since, now, airport,
    )
    by_hour: dict = {}
    for r in recs:
        slot = by_hour.setdefault(r["hr"], {"arr_delay": 0.0, "dep_delay": 0.0, "arr_n": 0, "dep_n": 0})
        if r["direction"] == "arrival":
            slot["arr_delay"] = round(r["avg_delay"] or 0.0, 1)
            slot["arr_n"] = r["n"]
        else:
            slot["dep_delay"] = round(r["avg_delay"] or 0.0, 1)
            slot["dep_n"] = r["n"]
    return [{"hour": hr.isoformat(), "label": hr.strftime("%H"), **by_hour[hr]}
            for hr in sorted(by_hour)]


async def delay_distribution(now: datetime, airport: str = "DOH", hours_back: int = 24) -> list[dict]:
    """Count of flights per delay bucket over the window. Feeds the histogram."""
    if db.is_demo_mode:
        return []
    since = now - timedelta(hours=hours_back)
    recs = await db.fetch(
        f"""SELECT {_DELAY_MIN} AS d
              FROM {_TABLE}
             WHERE scheduled_utc >= $1 AND scheduled_utc <= $2 AND airport = $3
               AND sched_time_utc IS NOT NULL AND revised_time_utc IS NOT NULL""",
        since, now, airport,
    )
    buckets = [
        ("Early", lambda d: d < -1),
        ("On time", lambda d: -1 <= d <= 15),
        ("15–30", lambda d: 15 < d <= 30),
        ("30–60", lambda d: 30 < d <= 60),
        ("60+", lambda d: d > 60),
    ]
    counts = {label: 0 for label, _ in buckets}
    for r in recs:
        d = r["d"] or 0.0
        for label, test in buckets:
            if test(d):
                counts[label] += 1
                break
    return [{"label": label, "value": counts[label]} for label, _ in buckets]


async def ontime_trend(now: datetime, airport: str = "DOH", hours_back: int = 12) -> list[dict]:
    """Rolling on-time % per hour (share with delay ≤ 15 min). Feeds the trend
    line. Row: {hour ISO, label 'HH', pct, n}."""
    if db.is_demo_mode:
        return []
    since = now - timedelta(hours=hours_back)
    recs = await db.fetch(
        f"""SELECT date_trunc('hour', scheduled_utc) AS hr,
                   count(*) FILTER (WHERE {_DELAY_MIN} <= 15) AS ontime,
                   count(*) AS n
              FROM {_TABLE}
             WHERE scheduled_utc >= $1 AND scheduled_utc <= $2 AND airport = $3
               AND sched_time_utc IS NOT NULL AND revised_time_utc IS NOT NULL
             GROUP BY hr ORDER BY hr""",
        since, now, airport,
    )
    return [{"hour": r["hr"].isoformat(), "label": r["hr"].strftime("%H"),
             "pct": round(100.0 * r["ontime"] / r["n"]) if r["n"] else 0, "n": r["n"]}
            for r in recs]


async def gate_hour_heatmap(now: datetime, airport: str = "DOH", hours_back: int = 12) -> list[dict]:
    """Occupancy intensity per concourse × hour (count of flights using a gate in
    that concourse that hour). Feeds the gate heatmap. Row: {concourse, hour ISO,
    label 'HH', n}."""
    if db.is_demo_mode:
        return []
    since = now - timedelta(hours=hours_back)
    recs = await db.fetch(
        f"""SELECT left(sim_gate, 1) AS concourse,
                   date_trunc('hour', scheduled_utc) AS hr, count(*) AS n
              FROM {_TABLE}
             WHERE scheduled_utc >= $1 AND scheduled_utc <= $2 AND airport = $3
               AND sim_gate IS NOT NULL
             GROUP BY concourse, hr ORDER BY concourse, hr""",
        since, now, airport,
    )
    return [{"concourse": r["concourse"], "hour": r["hr"].isoformat(),
             "label": r["hr"].strftime("%H"), "n": r["n"]} for r in recs]


async def top_routes(now: datetime, airport: str = "DOH", hours_back: int = 24, limit: int = 8) -> list[dict]:
    """Busiest endpoints (the OTHER airport of each movement) with their average
    delay, over the window. Feeds the delay-tinted route bars. Row: {label airport,
    value count, delay avg-min}."""
    if db.is_demo_mode:
        return []
    since = now - timedelta(hours=hours_back)
    recs = await db.fetch(
        f"""SELECT CASE WHEN origin = $3 THEN destination ELSE origin END AS ap,
                   count(*) AS n,
                   avg({_DELAY_MIN}) FILTER (
                       WHERE sched_time_utc IS NOT NULL AND revised_time_utc IS NOT NULL
                   ) AS avg_delay
              FROM {_TABLE}
             WHERE scheduled_utc >= $1 AND scheduled_utc <= $2 AND airport = $3
             GROUP BY ap
             HAVING CASE WHEN origin = $3 THEN destination ELSE origin END NOT IN ('----', '')
             ORDER BY n DESC LIMIT {int(limit)}""",
        since, now, airport,
    )
    return [{"label": r["ap"], "value": r["n"], "delay": round(r["avg_delay"] or 0.0, 1)}
            for r in recs if r["ap"]]


async def airline_share(now: datetime, airport: str = "DOH", hours_back: int = 24, limit: int = 8) -> list[dict]:
    """Share of movements per airline over the window. Row: {label, value}."""
    if db.is_demo_mode:
        return []
    since = now - timedelta(hours=hours_back)
    recs = await db.fetch(
        f"""SELECT coalesce(nullif(airline, ''), '--') AS al, count(*) AS n
              FROM {_TABLE}
             WHERE scheduled_utc >= $1 AND scheduled_utc <= $2 AND airport = $3
             GROUP BY al ORDER BY n DESC LIMIT {int(limit)}""",
        since, now, airport,
    )
    return [{"label": r["al"], "value": r["n"]} for r in recs]


async def dashboard_analytics(now: datetime, airport: str = "DOH") -> dict:
    """Bundle every historical rollup into one payload for GET /api/analytics,
    scoped to `airport`.

    `demand_curve` is included here (rather than being derived client-side from
    /api/schedule) because the live rosters only look forward — see
    flight_store.demand_curve for why the chart's past half was empty."""
    return {
        "delay_by_hour": await delay_by_hour(now, airport),
        "delay_distribution": await delay_distribution(now, airport),
        "ontime_trend": await ontime_trend(now, airport),
        "gate_hour_heatmap": await gate_hour_heatmap(now, airport),
        "top_routes": await top_routes(now, airport),
        "airline_share": await airline_share(now, airport),
        "demand_curve": await flight_store.demand_curve(now, airport),
    }
