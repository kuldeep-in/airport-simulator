"""Passenger density and temperature models for terminal interior zones.

Each zone gets two metrics:
  - PAX_DENSITY (pax/m^2): baseline 0.1-0.3, rising sharply while a hub wave
    boards (up to ~2.5) with Gaussian noise.
  - TEMPERATURE (deg C): baseline 21-23, a slow time-of-day drift, plus an
    occasional +3C HVAC-fault anomaly on a random zone.

Values are deterministic-ish given (zone_id, sim_time) but include noise so the
heatmaps shimmer realistically frame to frame.
"""

import math
import random
from datetime import datetime, timezone, timedelta

GST = timezone(timedelta(hours=3))

# Wave centres in minutes-of-day GST (the airport's real daily traffic waves)
WAVE_CENTERS = [2 * 60 + 45, 9 * 60 + 45 + 30, 15 * 60 + 45, 20 * 60 + 45]
WAVE_SIGMA = 55.0  # minutes


def _wave_intensity(minutes_now: float) -> float:
    """0..1 boarding intensity — sum of Gaussians centred on each wave."""
    total = 0.0
    for c in WAVE_CENTERS:
        d = min(abs(minutes_now - c), 1440 - abs(minutes_now - c))
        total += math.exp(-(d * d) / (2 * WAVE_SIGMA * WAVE_SIGMA))
    return min(1.0, total)


def compute_zone_metrics(zones, sim_time: datetime, anomaly_seed: int = 0):
    """Return list of dicts: {zone_id, metric_type, value, unit}.

    `zones` is a list of {"zone_id","zone_name","gate_facing": bool} — gate
    facing / departures zones swing harder with the wave than arrivals.
    """
    local = sim_time.astimezone(GST)
    minutes_now = local.hour * 60 + local.minute + local.second / 60.0
    intensity = _wave_intensity(minutes_now)

    # RNG seeded per-minute so consecutive ticks vary but replay is stable
    rng = random.Random(int(minutes_now) * 131 + anomaly_seed)

    # Pick a zone for an HVAC anomaly roughly 25% of the time
    anomaly_zone = None
    if rng.random() < 0.25 and zones:
        anomaly_zone = rng.choice(zones)["zone_id"]

    # time-of-day temperature envelope: warmer mid-afternoon
    tod = math.sin((minutes_now / 1440.0) * 2 * math.pi - math.pi / 2)
    temp_drift = 1.2 * tod  # +-1.2C

    out = []
    for z in zones:
        zr = random.Random(hash(z["zone_id"]) & 0xFFFFFFFF)
        base_density = 0.10 + zr.random() * 0.20  # 0.1..0.3
        gate_facing = z.get("gate_facing", False)
        swing = 2.2 if gate_facing else 1.4
        density = base_density + intensity * swing
        density += rng.uniform(-0.08, 0.08)  # noise
        density = max(0.02, round(density, 3))

        base_temp = 21.0 + zr.random() * 2.0  # 21..23
        temp = base_temp + temp_drift + rng.uniform(-0.4, 0.4)
        if z["zone_id"] == anomaly_zone:
            temp += 3.0
        temp = round(temp, 2)

        out.append({"zone_id": z["zone_id"], "metric_type": "PAX_DENSITY",
                    "value": density, "unit": "pax/m2"})
        out.append({"zone_id": z["zone_id"], "metric_type": "TEMPERATURE",
                    "value": temp, "unit": "C"})
    return out
