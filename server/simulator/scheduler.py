"""Deterministic QR hub-wave flight schedule generator.

Hamad International is Qatar Airways' single mega-hub. Traffic arrives in
"waves": banks of ~10-14 aircraft land within ~1.5h, passengers connect, and
the same aircraft depart 2-3h later. We model four waves per day at
02:00, 09:30, 15:00, 20:00 GST (UTC+3).

The schedule is deterministic (seeded) so a cold start always produces the
same day of operations, and re-seeding is idempotent.
"""

import random
from datetime import datetime, timedelta, timezone

GST = timezone(timedelta(hours=3))

# Fleet mix: aircraft_type -> count available
FLEET = {
    "A359": 12,
    "A35K": 6,
    "B77W": 14,
    "A388": 4,
    "A20N": 8,
    "B789": 6,
}

# A388 (A380) can only use A-concourse gates -> capacity class A380
CAPACITY_OF_TYPE = {
    "A388": "A380",
    "A35K": "WIDE",
    "B77W": "WIDE",
    "A359": "WIDE",
    "B789": "WIDE",
    "A20N": "NARROW",
}

DESTINATIONS = [
    "LHR", "JFK", "CDG", "SYD", "BKK", "DXB", "KUL", "MNL", "NRT", "LAX",
    "ORD", "FRA", "SIN", "BOM", "DEL", "CGK", "IST", "MUC", "AMS", "MAD",
    "HKG", "ICN", "PER", "MEL", "CPT", "JNB", "GRU", "YYZ", "IAD", "BAH",
]

# Wave start hours (GST) and approximate flight count per wave
WAVES = [
    (2, 0, 12),
    (9, 30, 14),
    (15, 0, 12),
    (20, 0, 13),
]

PAX_BY_TYPE = {
    "A388": (450, 517),
    "A35K": (300, 360),
    "B77W": (300, 354),
    "A359": (260, 311),
    "B789": (240, 290),
    "A20N": (140, 180),
}


def sim_day_start(now: datetime) -> datetime:
    """Return midnight GST of the sim day containing `now`."""
    local = now.astimezone(GST)
    return local.replace(hour=0, minute=0, second=0, microsecond=0)


def build_fleet_pool():
    pool = []
    for atype, count in FLEET.items():
        for i in range(count):
            pool.append(atype)
    return pool


def generate_schedule(now: datetime | None = None, seed: int = 20240601):
    """Generate the full list of flight dicts for the current sim day."""
    if now is None:
        now = datetime.now(timezone.utc)
    day0 = sim_day_start(now)
    rng = random.Random(seed + day0.toordinal())

    fleet_pool = build_fleet_pool()
    rng.shuffle(fleet_pool)

    flights = []
    tail_idx = 0
    flight_no = 1000

    for wave_i, (hh, mm, n_flights) in enumerate(WAVES):
        wave_start = day0 + timedelta(hours=hh, minutes=mm)
        for k in range(n_flights):
            if tail_idx >= len(fleet_pool):
                tail_idx = 0
                rng.shuffle(fleet_pool)
            atype = fleet_pool[tail_idx]
            tail_idx += 1

            # arrivals spread across a 90-minute band
            arr_offset = rng.uniform(0, 90)
            scheduled_arr = wave_start + timedelta(minutes=arr_offset)
            # turnaround 2-3h later
            turn = rng.uniform(120, 180)
            scheduled_dep = scheduled_arr + timedelta(minutes=turn)

            origin = rng.choice(DESTINATIONS)
            destination = rng.choice([d for d in DESTINATIONS if d != origin])

            pax_lo, pax_hi = PAX_BY_TYPE[atype]
            pax = rng.randint(pax_lo, pax_hi)

            flight_no += rng.randint(1, 4)
            arr_callsign = f"QR{flight_no}"
            fid = f"F{day0.strftime('%m%d')}{wave_i}{k:02d}"

            flights.append({
                "id": fid,
                "callsign": arr_callsign,
                "airline": "QR",
                "aircraft_type": atype,
                "origin": origin,
                "destination": destination,
                "scheduled_arr": scheduled_arr,
                "actual_arr": None,
                "scheduled_dep": scheduled_dep,
                "actual_dep": None,
                "status": "SCHEDULED",
                "assigned_gate": None,  # assigned by seeder
                "pax_count": pax,
                "capacity_class": CAPACITY_OF_TYPE[atype],
            })

    return flights


def active_wave(sim_time: datetime):
    """Return (wave_index, wave_label) for the wave currently boarding/arriving."""
    local = sim_time.astimezone(GST)
    minutes_now = local.hour * 60 + local.minute
    labels = ["Wave 1 (02:00)", "Wave 2 (09:30)", "Wave 3 (15:00)", "Wave 4 (20:00)"]
    best = None
    for i, (hh, mm, _n) in enumerate(WAVES):
        start = hh * 60 + mm
        # a wave is "active" from 30 min before start to 3h after
        if start - 30 <= minutes_now <= start + 210:
            best = (i, labels[i])
    if best is None:
        return (-1, "Between waves")
    return best
