import * as THREE from 'three';
import { useSimStore } from '../../store/useSimStore';
import { lonLatToShape } from '../../geo';
import hiaBase from '../../data/hiaBase.json';

// ── Static building data ──────────────────────────────────────────────────────
const TERMINAL_FEATURES = hiaBase.features.filter(
  (f) => f.properties?.type === 'terminal',
);

// Split large buildings into N–S lat bands so each band can hold a distinct
// metric colour.  Bands defined by lat breakpoints from hia_base.geojson
// endpoints — no guesswork about world units.
const LAT_SPLITS = {
  'Main Terminal Hall': [25.2703051, 25.2720000, 25.2740000, 25.2756949],
  'Concourse A':        [25.2659932, 25.2683000, 25.2706644],
  'Concourse C':        [25.2753356, 25.2774920, 25.2800068],
  // Single-band buildings use building min/max automatically
};

// Which backend zone_ids contribute to each band's colour (averaged)
const BAND_ZONES = {
  'Main Terminal Hall': [
    ['zone_arr_hall', 'zone_baggage'],
    ['zone_immigration', 'zone_security', 'zone_transfer', 'zone_duty_free'],
    ['zone_dep_b', 'zone_lounge'],
  ],
  'Concourse A': [['zone_a_south'], ['zone_a_north']],
  'Concourse B': [['zone_b_west']],
  'Concourse C': [['zone_c_mid'], ['zone_c_north']],
  'Concourse D': [['zone_d_east']],
  'Concourse E': [['zone_e_remote']],
};

// Build a ShapeGeometry for one lat band of a building.
// Uses lonLatToShape — the SAME function TerminalBuilding uses — so the overlay
// is always pixel-perfect within the building footprint.
function bandGeo(lon0, lon1, lat0, lat1) {
  const [ax, ay] = lonLatToShape(lon0, lat0);
  const [bx, by] = lonLatToShape(lon1, lat0);
  const [cx, cy] = lonLatToShape(lon1, lat1);
  const [dx, dy] = lonLatToShape(lon0, lat1);
  const s = new THREE.Shape();
  s.moveTo(ax, ay);
  s.lineTo(bx, by);
  s.lineTo(cx, cy);
  s.lineTo(dx, dy);
  s.closePath();
  return new THREE.ShapeGeometry(s);
}

// One entry per rendered band — created once at module load
const SEGMENTS = [];
TERMINAL_FEATURES.forEach((f) => {
  const name  = f.properties.name;
  const ring  = f.geometry.type === 'Polygon'
    ? f.geometry.coordinates[0]
    : f.geometry.coordinates[0][0];

  const lons   = ring.map((c) => c[0]);
  const lats   = ring.map((c) => c[1]);
  const minLon = Math.min(...lons);
  const maxLon = Math.max(...lons);
  const minLat = Math.min(...lats);
  const maxLat = Math.max(...lats);

  const splits = LAT_SPLITS[name] ?? [minLat, maxLat];
  const bZones = BAND_ZONES[name] ?? [[]];

  for (let i = 0; i < splits.length - 1; i++) {
    SEGMENTS.push({
      id:    `${name}_${i}`,
      geo:   bandGeo(minLon, maxLon, splits[i], splits[i + 1]),
      zones: bZones[i] ?? [],
    });
  }
});

// ── Colour scales ─────────────────────────────────────────────────────────────
const PAX_STOPS  = [[0.00, 0x1565c0],[0.20, 0x0288d1],[0.45, 0xf9a825],[0.70, 0xe64a19],[1.00, 0xb71c1c]];
const TEMP_STOPS = [[0.00, 0x0d47a1],[0.35, 0x1976d2],[0.55, 0x43a047],[0.75, 0xf57c00],[1.00, 0xc62828]];

const _ca = new THREE.Color();
const _cb = new THREE.Color();
function colorAt(stops, t) {
  t = Math.max(0, Math.min(1, t));
  for (let i = 0; i < stops.length - 1; i++) {
    const [t0, h0] = stops[i];
    const [t1, h1] = stops[i + 1];
    if (t <= t1) {
      _ca.setHex(h0); _cb.setHex(h1);
      return _ca.clone().lerp(_cb, (t - t0) / (t1 - t0));
    }
  }
  return new THREE.Color(stops[stops.length - 1][1]);
}

function avgMetric(zoneMetrics, ids, key, fallback) {
  let sum = 0, n = 0;
  for (const id of ids) {
    const v = zoneMetrics[id]?.[key];
    if (v != null) { sum += v; n++; }
  }
  return n > 0 ? sum / n : fallback;
}

// ── Component ─────────────────────────────────────────────────────────────────
export default function ZoneOverlay({ metric }) {
  const zoneMetrics = useSimStore((s) => s.zoneMetrics);
  const isPax   = metric === 'PAX_DENSITY';
  const stops   = isPax ? PAX_STOPS : TEMP_STOPS;
  const norm    = isPax
    ? (v) => Math.min(v / 2.5, 1)
    : (v) => Math.min(Math.max((v - 19) / 7, 0), 1);

  return (
    // Same group rotation as TerminalBuilding — guaranteed co-planar with roofs
    <group rotation={[-Math.PI / 2, 0, 0]}>
      {SEGMENTS.map(({ id, geo, zones }) => {
        const v       = avgMetric(zoneMetrics, zones, metric, isPax ? 0.1 : 22);
        const t       = norm(v);
        const color   = colorAt(stops, t);
        const opacity = isPax
          ? 0.32 + t * 0.40   // 0.32 (quiet) → 0.72 (packed)
          : 0.20 + t * 0.30;  // 0.20 (cold)  → 0.50 (hot)

        return (
          <mesh
            key={id}
            geometry={geo}
            // Sit just above the building floor in local (pre-rotation) space.
            // In world space this becomes slightly above ground level.
            position={[0, 0, -0.1]}
            renderOrder={10}
          >
            <meshBasicMaterial
              color={color}
              transparent
              opacity={opacity}
              depthTest={false}
              depthWrite={false}
              side={THREE.DoubleSide}
            />
          </mesh>
        );
      })}
    </group>
  );
}
