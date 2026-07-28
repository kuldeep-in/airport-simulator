"""Real, user-supplied inbound->outbound connection list, replacing the
earlier synthetic model (frontend picked a random ~70% of inbound flights
and a random up-to-5 outbound matches within a time window). This is now
authored data: data/connectionsRoster.json maps each arrival callsign to its
real connecting departure callsign(s), in the given order.

A handful of the given pairs reference departure callsigns that aren't in
data/departureRoster.json (per explicit user choice: drop those pairs rather
than pad the departure roster with invented flights) — connections_for_schedule()
filters to only departure ids that actually exist in the roster passed in, so
if departureRoster.json is ever extended to include one of those callsigns,
its connections light up automatically with no change needed here.
"""

import json
import os

_CONNECTIONS_ROSTER_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "connectionsRoster.json")
with open(_CONNECTIONS_ROSTER_PATH) as _f:
    _CONNECTIONS_ROSTER: dict[str, list[str]] = json.load(_f)


def connections_for_schedule(known_departure_ids) -> dict[str, list[str]]:
    """Arrival callsign -> ordered list of connecting departure callsigns.

    The live AeroDataBox feed has no inbound->outbound connection source, so
    this now returns an empty map for live flights (the /api/schedule response
    keeps the `connections` key so the frontend needs no change; the connection
    dots simply don't render). The authored data/connectionsRoster.json is only
    meaningful for the old static demo callsigns, so we no longer overlay it —
    matching it against unrelated live callsigns would be misleading."""
    return {}
