import { GeoJsonLayer } from '@deck.gl/layers';

function tempColor(t) {
  if (t == null) return [71, 85, 105, 60];
  if (t < 20) return [30, 64, 175, 102]; // blue #1e40af @ 0.4
  if (t <= 24) return [22, 163, 74, 102]; // green #16a34a @ 0.4
  return [234, 88, 12, 102]; // orange #ea580c @ 0.4
}

/**
 * Terminal interior temperature zones, filled by TEMPERATURE metric.
 * @param {object} zonesGeojson  hia_zones.geojson
 * @param {object} zoneMetrics   map zone_id -> { TEMPERATURE }
 */
export function buildTempZoneLayer(zonesGeojson, zoneMetrics) {
  if (!zonesGeojson) return null;
  return new GeoJsonLayer({
    id: 'temp-zone-layer',
    data: zonesGeojson,
    pickable: true,
    stroked: true,
    filled: true,
    getFillColor: (f) => tempColor(zoneMetrics[f.properties.zone_id]?.TEMPERATURE),
    getLineColor: [148, 163, 184, 120],
    getLineWidth: 1,
    lineWidthUnits: 'pixels',
    updateTriggers: {
      getFillColor: [zoneMetrics],
    },
  });
}

export function tempTooltip(object, zoneMetrics) {
  const zid = object.properties.zone_id;
  const t = zoneMetrics[zid]?.TEMPERATURE;
  return {
    html: `<b>${object.properties.zone_name}</b><br/>Temp: ${
      t != null ? t.toFixed(1) + ' °C' : '—'
    }`,
  };
}
