import { GeoJsonLayer } from '@deck.gl/layers';

const STATUS_COLORS = {
  AVAILABLE: [34, 197, 94], // green
  OCCUPIED: [234, 179, 8], // yellow
  BOARDING: [234, 179, 8], // yellow
  CLOSED: [239, 68, 68], // red
  DEFAULT: [71, 85, 105], // gray
};

/**
 * Build the GeoJsonLayer for gate polygons. Colored by live status from store.
 * @param {object} geojson  hia_gates.geojson
 * @param {object} gates    map gateId -> { status }
 * @param {function} onSelect  (id) => void
 */
export function buildGatesLayer(geojson, gates, onSelect) {
  if (!geojson) return null;
  return new GeoJsonLayer({
    id: 'gates-layer',
    data: geojson,
    pickable: true,
    stroked: true,
    filled: true,
    getFillColor: (f) => {
      const st = gates[f.properties.id]?.status || 'DEFAULT';
      const c = STATUS_COLORS[st] || STATUS_COLORS.DEFAULT;
      return [c[0], c[1], c[2], 200];
    },
    getLineColor: [226, 232, 240, 180],
    getLineWidth: 2,
    lineWidthUnits: 'pixels',
    updateTriggers: {
      getFillColor: [gates],
    },
    onClick: (info) => {
      if (info.object) onSelect(info.object.properties.id);
    },
  });
}

export function gateTooltip(object, gates) {
  const p = object.properties;
  const g = gates[p.id];
  return {
    html: `<b>Gate ${p.label}</b><br/>Concourse ${p.concourse} · ${
      p.capacity_class
    }<br/>Status: ${g?.status || 'AVAILABLE'}`,
  };
}
