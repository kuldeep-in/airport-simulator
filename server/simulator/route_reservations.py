"""Plan-time taxi-route reservations — a light, DIRECTIONAL edge check that keeps
opposing routes off the same single-lane taxiway segment.

WHY
---
The taxiway router (`taxiway_graph._dijkstra`) plans each flight's path in
isolation, with no awareness of any other flight and no time dimension — so an
arrival taxiing IN and a departure taxiing OUT can be handed the same taxiway
segment in OPPOSITE directions (a head-on). The run-time separation layer
(`traffic.py`) then either stalls both aircraft or, after a few ticks, forces a
break-through close pass. This module removes most of those head-ons BEFORE they
are ever planned: while a flight is taxiing it RESERVES the directed graph edges
of its route; when a new route is computed, any edge another flight holds in the
OPPOSING direction is penalised so Dijkstra prefers a different taxiway.

DELIBERATELY "LIGHT"
--------------------
  * DIRECTION only, no clock. Same-direction following is NOT blocked (traffic.py
    already spaces a queue); only opposing (head-on) use of a shared edge is
    avoided. This targets exactly the observed symptom (arrival vs departure
    head-on), without the fragility of plan-time timing that drifts the moment
    traffic.py clamps a flight.
  * SOFT avoidance. The penalty is ADDED to the edge weight (see
    `taxiway_graph._AVOID_PENALTY_M`), not a hard ban — so if the only path
    genuinely runs against traffic, the router still finds it (and traffic.py
    remains the runtime backstop) rather than failing to route.
  * A flight holds its WHOLE route until it stops taxiing (arrival PARKED /
    departure on the runway or done). Already-traversed edges stay reserved — a
    conservative over-reservation that is fine for a light pass.

MECHANICS
---------
Edges use the SAME integer node ids `taxiway_graph` assigns, so a reservation is
just the flight's Dijkstra node path stored as consecutive (from, to) pairs.
Release is keyed by flight id. Everything lives only in this process's memory
(like the rest of the generator's route state); nothing is persisted, so a
generator restart starts from an empty table — call `clear_all()` then.
"""

# fid -> list of directed edges (from_node, to_node) the flight is currently taxiing.
_reserved: dict[str, list[tuple[int, int]]] = {}


def reserve(fid: str, node_path: list[int] | None) -> None:
    """Record the directed edges of `fid`'s taxi route. Replaces any prior
    reservation for the same flight, so a re-routed departure re-reserves cleanly.
    A path with fewer than two nodes reserves nothing (and clears any prior one)."""
    if not node_path or len(node_path) < 2:
        _reserved.pop(fid, None)
        return
    _reserved[fid] = [(node_path[i], node_path[i + 1]) for i in range(len(node_path) - 1)]


def release(fid: str) -> None:
    """Drop `fid`'s reservation — it has stopped taxiing (parked / on the runway /
    gone) or left the roster. Safe to call when nothing is reserved."""
    _reserved.pop(fid, None)


def clear_all() -> None:
    """Forget every reservation. Called on generator startup and active-airport
    switch, where the whole in-memory world is rebuilt."""
    _reserved.clear()


def blocked_for(fid: str) -> set[tuple[int, int]]:
    """Directed edges a NEW route for `fid` should avoid: for every edge another
    flight is traversing (a→b), the OPPOSING traversal (b→a) is returned. Same-
    direction edges are intentionally NOT returned (following is handled at run
    time by traffic.py). `fid`'s own reservation is excluded so re-routing it never
    fights itself."""
    blocked: set[tuple[int, int]] = set()
    for other, edges in _reserved.items():
        if other == fid:
            continue
        for a, b in edges:
            blocked.add((b, a))
    return blocked


def active_count() -> int:
    """How many flights currently hold a taxi reservation (for diagnostics)."""
    return len(_reserved)
