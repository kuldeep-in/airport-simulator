"""Delay-risk scores for the app — the ML step's output, served from Lakebase.

    UC model @champion ──score (hourly job, :15)──▶ gold_delay_risk_<iata> (Delta)
        ──TRIGGERED sync──▶ Lakebase <schema>.delay_risk_<iata>  ◀── this module

Read-only, like the flight schedule. If the model hasn't scored yet (table not synced),
every read returns empty and the app simply shows no risk — never an error.
"""
from datetime import datetime, timedelta, timezone

from server import airports
from server.db import db

TABLE = f"{airports.SCHEMA}.delay_risk_{airports.IATA.lower()}"
ALERT_RISK = 0.5            # "high" band — matches ml/score_delay_risk.py HIGH
ALERT_WITHIN_MIN = 90       # alert only for departures in the next 90 minutes


async def upcoming(hours: float = 6.0) -> list[dict]:
    """Scored departures from 15 min ago to `hours` ahead, highest risk first."""
    if db.is_demo_mode:
        return []
    now = datetime.now(timezone.utc)
    try:
        rows = await db.fetch(
            f"""SELECT flight_number, airline, destination, aircraft_type, sched_time_utc,
                       scheduled_utc, risk, risk_band, reasons, model_version, scored_at
                  FROM {TABLE}
                 WHERE sched_time_utc BETWEEN $1 AND $2
                 ORDER BY risk DESC""",
            now - timedelta(minutes=15), now + timedelta(hours=hours))
    except Exception as e:  # noqa: BLE001 — table not synced yet / no grant yet
        print(f"[delay_risk] read skipped: {str(e)[:160]}")
        return []
    out = []
    for r in rows:
        mins = (r["sched_time_utc"] - now).total_seconds() / 60.0
        out.append({
            "flight_number": r["flight_number"], "airline": r["airline"], "destination": r["destination"],
            "aircraft_type": r["aircraft_type"], "sched_time_utc": r["sched_time_utc"].isoformat(),
            "minutes_to_departure": round(mins), "risk": round(float(r["risk"]), 4),
            "risk_band": r["risk_band"], "reasons": [x for x in (r["reasons"] or "").split(" · ") if x],
            "model_version": r["model_version"], "scored_at": r["scored_at"].isoformat() if r["scored_at"] else None,
            "alert": float(r["risk"]) >= ALERT_RISK and 0 <= mins <= ALERT_WITHIN_MIN,
        })
    return out


async def by_flight() -> dict[str, dict]:
    """{flight_number: {risk, risk_band, reasons}} for joining onto board rows."""
    return {r["flight_number"]: r for r in await upcoming(hours=24)}
