import { ScatterplotLayer } from '@deck.gl/layers';

const PHASE_COLORS = {
  PARKED: [226, 232, 240], // white
  SCHEDULED: [226, 232, 240],
  GROUND_SERVICE: [148, 163, 184],
  TAXIING_IN: [56, 189, 248], // cyan
  TAXIING_OUT: [56, 189, 248], // cyan
  PUSHBACK: [251, 191, 36], // amber
  BOARDING: [34, 197, 94], // green
};

// relative size by aircraft type (radius metres)
const SIZE_BY_TYPE = {
  A388: 45,
  A35K: 38,
  B77W: 38,
  A359: 34,
  B789: 32,
  A20N: 24,
};

/**
 * Aircraft positions as colored dots. Airborne aircraft are hidden.
 * @param {Array} positions   list of { flight_id, lat, lon, heading, phase }
 * @param {object} flights    map id -> flight (for type + callsign)
 * @param {function} onSelect (flight_id) => void
 */
export function buildAircraftLayer(positions, flights, onSelect) {
  const data = positions.filter((p) => p.phase !== 'AIRBORNE');
  return new ScatterplotLayer({
    id: 'aircraft-layer',
    data,
    pickable: true,
    stroked: true,
    filled: true,
    radiusUnits: 'meters',
    lineWidthUnits: 'pixels',
    getPosition: (d) => [d.lon, d.lat],
    getRadius: (d) => {
      const t = flights[d.flight_id]?.aircraft_type;
      return SIZE_BY_TYPE[t] || 30;
    },
    getFillColor: (d) => PHASE_COLORS[d.phase] || [226, 232, 240],
    getLineColor: [10, 14, 26, 255],
    getLineWidth: 1.5,
    radiusMinPixels: 4,
    radiusMaxPixels: 22,
    updateTriggers: {
      getFillColor: [positions],
      getPosition: [positions],
    },
    onClick: (info) => {
      if (info.object) onSelect(info.object.flight_id);
    },
  });
}

export function aircraftTooltip(object, flights) {
  const f = flights[object.flight_id];
  if (!f) return null;
  return {
    html: `<b>${f.callsign}</b> · ${f.aircraft_type}<br/>${object.phase}<br/>${
      f.origin || '—'
    } → ${f.destination || '—'}`,
  };
}
