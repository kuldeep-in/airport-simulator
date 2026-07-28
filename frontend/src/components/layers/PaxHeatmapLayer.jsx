import { HeatmapLayer } from '@deck.gl/aggregation-layers';

function centroid(coords) {
  const ring = coords[0];
  let x = 0;
  let y = 0;
  for (const p of ring) {
    x += p[0];
    y += p[1];
  }
  return [x / ring.length, y / ring.length];
}

/**
 * Passenger density heatmap. One weighted point per zone centroid.
 * @param {object} zonesGeojson  hia_zones.geojson
 * @param {object} zoneMetrics   map zone_id -> { PAX_DENSITY, TEMPERATURE }
 */
export function buildPaxHeatmapLayer(zonesGeojson, zoneMetrics) {
  if (!zonesGeojson) return null;
  const data = zonesGeojson.features.map((f) => ({
    position: centroid(f.geometry.coordinates),
    weight: zoneMetrics[f.properties.zone_id]?.PAX_DENSITY || 0.05,
  }));

  return new HeatmapLayer({
    id: 'pax-heatmap',
    data,
    getPosition: (d) => d.position,
    getWeight: (d) => d.weight,
    radiusPixels: 90,
    intensity: 1.4,
    threshold: 0.03,
    aggregation: 'SUM',
    colorRange: [
      [56, 189, 248, 60], // blue (low)
      [56, 189, 248, 140],
      [251, 191, 36, 180], // amber
      [251, 191, 36, 220],
      [239, 68, 68, 240], // red (high)
      [239, 68, 68, 255],
    ],
    updateTriggers: {
      getWeight: [zoneMetrics],
    },
  });
}
