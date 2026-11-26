"""Inbound → outbound passenger connections for the Schedule board.

The live AeroDataBox feed has no connection data, so this returns an empty map; the
/api/schedule response keeps its `connections` key so the frontend needs no change (the
connection dots simply don't render). If a real connection source is added later, this
is the one place to plug it in: return {arrival_callsign: [departure_callsign, …]}.
"""


def connections_for_schedule(known_departure_ids) -> dict[str, list[str]]:
    """Arrival callsign → ordered list of connecting departure callsigns (none today)."""
    return {}
