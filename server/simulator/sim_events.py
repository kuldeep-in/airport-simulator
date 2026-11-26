"""Simulation events — the low-volume history of what happened on the ground.

Positions are 1 Hz and overwritten in place (only the current state is useful live), so
they are NOT kept as history. Instead the state machines emit one event per lifecycle
transition, which is all that taxi-time / dwell / runway / turnaround analytics need:

    arrivals:   TOUCHDOWN → GATE_IN → GATE_OUT   (or TURNAROUND_HANDOVER to its departure)
    departures: AT_GATE → PUSHBACK → HOLD_SHORT → RUNWAY_ENTER → TAKEOFF

`emit()` is synchronous (it's called from inside the advance() call chains, which can't
await) and only buffers; broadcaster._tick() calls `flush()` each tick, which writes one
JSONL file per 15-minute window to the airport's volume (raw_telemetry/events). The ingest
pipeline loads those into Delta `sim_events_<iata>`.
"""
from datetime import datetime

from server import airports
from server.simulator.telemetry_files import TelemetryFileSink

_sink = TelemetryFileSink("events")


def emit(event: str, flight_id: str, direction: str, ts: datetime, meta: dict | None = None,
         **extra) -> None:
    """Record one lifecycle event. `meta` is the flight's roster dict (identity fields
    are copied from it); `extra` carries event-specific values (gate, runway, …)."""
    m = meta or {}
    _sink.add({
        "airport": airports.IATA,
        "ts": ts,
        "event": event,
        "flight_id": flight_id,
        "direction": direction,
        "callsign": m.get("callsign") or flight_id,
        "airline": m.get("airline"),
        "aircraft_type": m.get("aircraft_type"),
        "aircraft_reg": m.get("aircraft_reg"),
        "origin": m.get("origin"),
        "destination": m.get("destination"),
        "gate": extra.pop("gate", None) or m.get("assigned_gate"),
        "runway": extra.pop("runway", None) or m.get("runway"),
        "detail": extra or None,
    })


async def flush(now: datetime | None = None) -> None:
    await _sink.maybe_flush(now)
