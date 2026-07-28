/**
 * Temporary debug overlay: lat/lon grid + coordinate labels at gates and buildings.
 * Remove the <LatLonGrid /> line from Airport3D.jsx when no longer needed.
 */
import { useMemo } from 'react';
import * as THREE from 'three';
import { Text } from '@react-three/drei';
import { CENTER_LON, CENTER_LAT, LON_SCALE, LAT_SCALE } from '../../geo';
import { GATE_POSITIONS, GATE_DIRS, SIGN_OFFSETS } from '../../data/gateData.js';
import hiaBase from '../../data/hiaBase.json';

// ── Grid coverage ─────────────────────────────────────────────────────────────
const LON_MIN = 51.580, LON_MAX = 51.640;
const LAT_MIN = 25.248, LAT_MAX = 25.308;
const MAJOR_LON_STEP = 0.002;
const MAJOR_LAT_STEP = 0.002;
const MINOR_LON_STEP = 0.0005;
const MINOR_LAT_STEP = 0.0005;
const GRID_Y       = 0.32;   // lines hug the ground
const LABEL_Y      = 4.8;    // above tallest building (Main Terminal = 3.0 wu)
const LABEL_OFFSET = 8;

// Must match GateMarker.jsx
const TIP_EXTRA = 3.5;

// ── Visible gates (same set as GateMarker) ─────────────────────────────────────
const VISIBLE_GATES = new Set([
  'A1',  'A3',  'A4',  'A5',  'A6',  'A7',  'A8',  'A9',  'A10', 'A11',
  'B1',  'B2',  'B3',  'B4',  'B5',  'B6',  'B7',  'B8',  'B9',  'B10',
  'C1',  'C2',  'C3',  'C4',  'C5',  'C6',  'C7',  'C8',  'C9',
  'C10', 'C11', 'C12', 'C13',
  'D1',  'D2',  'D3',  'D4',
  'E1',  'E2',  'E3',  'E4',
]);

// ── Terminal building features from geojson ───────────────────────────────────
const BUILDING_FEATURES = hiaBase.features.filter(f => f.properties?.type === 'terminal');

// ── Terminal building centroids (named, for centroid label) ───────────────────
const BUILDINGS = [
  { name: 'Main Terminal', lon: 51.60928, lat: 25.27246 },
  { name: 'Concourse A',   lon: 51.61030, lat: 25.26786 },
  { name: 'Concourse B',   lon: 51.60785, lat: 25.27400 },
  { name: 'Concourse C',   lon: 51.61030, lat: 25.27720 },
  { name: 'Concourse D',   lon: 51.61218, lat: 25.27319 },
  { name: 'Concourse E',   lon: 51.61458, lat: 25.27038 },
];

// ── Coordinate helpers ────────────────────────────────────────────────────────
function lonToX(lon) { return (lon - CENTER_LON) * LON_SCALE; }
function latToZ(lat) { return -(lat - CENTER_LAT) * LAT_SCALE; }
function xToLon(x)   { return x / LON_SCALE + CENTER_LON; }
function zToLat(z)   { return -z / LAT_SCALE + CENTER_LAT; }

function snap(v, step) { return Math.round(v / step) * step; }
function range(min, max, step) {
  const out = [];
  const start = snap(min, step);
  for (let v = start; v <= max + step * 0.01; v = Math.round((v + step) * 1e6) / 1e6) {
    if (v >= min - step * 0.01) out.push(Math.round(v * 1e6) / 1e6);
  }
  return out;
}

// ── Build grid line geometry ──────────────────────────────────────────────────
function buildGrid() {
  const majorPts = [], minorPts = [];
  const xMin = lonToX(LON_MIN), xMax = lonToX(LON_MAX);
  const zMin = latToZ(LAT_MAX), zMax = latToZ(LAT_MIN);

  const majorLonSet = new Set(range(LON_MIN, LON_MAX, MAJOR_LON_STEP).map(String));
  const majorLatSet = new Set(range(LAT_MIN, LAT_MAX, MAJOR_LAT_STEP).map(String));

  for (const lon of range(LON_MIN, LON_MAX, MINOR_LON_STEP)) {
    const x = lonToX(lon);
    const arr = majorLonSet.has(String(lon)) ? majorPts : minorPts;
    arr.push(x, GRID_Y, zMin, x, GRID_Y, zMax);
  }
  for (const lat of range(LAT_MIN, LAT_MAX, MINOR_LAT_STEP)) {
    const z = latToZ(lat);
    const arr = majorLatSet.has(String(lat)) ? majorPts : minorPts;
    arr.push(xMin, GRID_Y, z, xMax, GRID_Y, z);
  }

  const makeGeo = pts => {
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.Float32BufferAttribute(pts, 3));
    return g;
  };

  return {
    majorGeo: makeGeo(majorPts),
    minorGeo: makeGeo(minorPts),
    majorLonList: range(LON_MIN, LON_MAX, MAJOR_LON_STEP),
    majorLatList: range(LAT_MIN, LAT_MAX, MAJOR_LAT_STEP),
    xMin, xMax, zMin, zMax,
  };
}

// ── Component ─────────────────────────────────────────────────────────────────
export default function LatLonGrid() {
  const { majorGeo, minorGeo, majorLonList, majorLatList, xMin, xMax, zMin, zMax } =
    useMemo(buildGrid, []);

  // Gate centroid coordinate labels (yellow)
  const gateCoords = useMemo(() =>
    Object.entries(GATE_POSITIONS)
      .filter(([id]) => VISIBLE_GATES.has(id))
      .map(([id, [wx, , wz]]) => ({
        id,
        wx, wz,
        lon: xToLon(wx).toFixed(5),
        lat: zToLat(wz).toFixed(5),
      })),
  []);

  // Gate TIP coordinate labels — at the actual painted-number position (light green)
  const gateTipCoords = useMemo(() =>
    Object.entries(GATE_POSITIONS)
      .filter(([id]) => VISIBLE_GATES.has(id))
      .map(([id, [gx, , gz]]) => {
        const dir     = GATE_DIRS[id] ?? [1, 0, 0];
        const faceOff = SIGN_OFFSETS[id] ?? 2.0;
        const tipX    = gx + dir[0] * (faceOff + TIP_EXTRA);
        const tipZ    = gz + dir[2] * (faceOff + TIP_EXTRA);
        return {
          id,
          wx: tipX, wz: tipZ,
          lon: xToLon(tipX).toFixed(5),
          lat: zToLat(tipZ).toFixed(5),
        };
      }),
  []);

  // Building centroid labels (magenta, named)
  const buildingCoords = useMemo(() =>
    BUILDINGS.map(b => ({
      ...b,
      wx: lonToX(b.lon),
      wz: latToZ(b.lat),
    })),
  []);

  // Building bounding-box corner + edge-midpoint labels (cyan)
  const buildingCorners = useMemo(() => {
    const result = [];
    for (const f of BUILDING_FEATURES) {
      const ring = f.geometry.type === 'Polygon'
        ? f.geometry.coordinates[0]
        : f.geometry.coordinates[0][0];

      const lons = ring.map(c => c[0]);
      const lats = ring.map(c => c[1]);
      const minLon = Math.min(...lons), maxLon = Math.max(...lons);
      const minLat = Math.min(...lats), maxLat = Math.max(...lats);
      const midLon = (minLon + maxLon) / 2;
      const midLat = (minLat + maxLat) / 2;

      // 4 corners + 4 edge midpoints = 8 reference points per building
      const pts = [
        [minLon, minLat], [maxLon, minLat],  // SW, SE corners
        [maxLon, maxLat], [minLon, maxLat],  // NE, NW corners
        [midLon, minLat], [maxLon, midLat],  // S mid, E mid
        [midLon, maxLat], [minLon, midLat],  // N mid, W mid
      ];
      for (const [lon, lat] of pts) {
        result.push({
          key: `${lon.toFixed(6)}-${lat.toFixed(6)}`,
          wx:  lonToX(lon),
          wz:  latToZ(lat),
          lon: lon.toFixed(5),
          lat: lat.toFixed(5),
        });
      }
    }
    return result;
  }, []);

  return (
    <group>
      {/* ── Grid lines — orange ── */}
      <lineSegments geometry={minorGeo}>
        <lineBasicMaterial color="#cc5500" opacity={0.25} transparent />
      </lineSegments>
      <lineSegments geometry={majorGeo}>
        <lineBasicMaterial color="#ff8800" opacity={0.70} transparent />
      </lineSegments>

      {/* ── Grid edge labels: longitudes ── */}
      {majorLonList.map(lon => {
        const x = lonToX(lon);
        return (
          <group key={`lon-${lon}`}>
            <Text position={[x, LABEL_Y, zMax + LABEL_OFFSET]}
              rotation={[-Math.PI / 2, 0, 0]} fontSize={1.0} fontWeight={700}
              color="#ff8800" anchorX="center" anchorY="middle"
              outlineWidth={0.08} outlineColor="#1a0800" depthOffset={-10}>
              {lon.toFixed(3)}
            </Text>
            <Text position={[x, LABEL_Y, zMin - LABEL_OFFSET]}
              rotation={[-Math.PI / 2, 0, 0]} fontSize={1.0} fontWeight={700}
              color="#ff8800" anchorX="center" anchorY="middle"
              outlineWidth={0.08} outlineColor="#1a0800" depthOffset={-10}>
              {lon.toFixed(3)}
            </Text>
          </group>
        );
      })}

      {/* ── Grid edge labels: latitudes ── */}
      {majorLatList.map(lat => {
        const z = latToZ(lat);
        return (
          <group key={`lat-${lat}`}>
            <Text position={[xMin - LABEL_OFFSET, LABEL_Y, z]}
              rotation={[-Math.PI / 2, 0, 0]} fontSize={1.0} fontWeight={700}
              color="#ff8800" anchorX="center" anchorY="middle"
              outlineWidth={0.08} outlineColor="#1a0800" depthOffset={-10}>
              {lat.toFixed(3)}
            </Text>
            <Text position={[xMax + LABEL_OFFSET, LABEL_Y, z]}
              rotation={[-Math.PI / 2, 0, 0]} fontSize={1.0} fontWeight={700}
              color="#ff8800" anchorX="center" anchorY="middle"
              outlineWidth={0.08} outlineColor="#1a0800" depthOffset={-10}>
              {lat.toFixed(3)}
            </Text>
          </group>
        );
      })}

      {/* ── Gate centroid labels — yellow ── */}
      {gateCoords.map(({ id, wx, wz, lon, lat }) => (
        <group key={`gcoord-${id}`}>
          <Text position={[wx, LABEL_Y, wz]}
            rotation={[-Math.PI / 2, 0, 0]} fontSize={0.60}
            color="#ffe88a" anchorX="center" anchorY="middle"
            outlineWidth={0.05} outlineColor="#1a1200" depthOffset={-10}>
            {lon}
          </Text>
          <Text position={[wx, LABEL_Y, wz + 1.3]}
            rotation={[-Math.PI / 2, 0, 0]} fontSize={0.60}
            color="#ffe88a" anchorX="center" anchorY="middle"
            outlineWidth={0.05} outlineColor="#1a1200" depthOffset={-10}>
            {lat}
          </Text>
        </group>
      ))}

      {/* ── Gate TIP labels — light green, at the painted-number position ── */}
      {gateTipCoords.map(({ id, wx, wz, lon, lat }) => (
        <group key={`gtip-${id}`}>
          <Text position={[wx, LABEL_Y, wz - 1.0]}
            rotation={[-Math.PI / 2, 0, 0]} fontSize={0.55}
            color="#aaffcc" anchorX="center" anchorY="middle"
            outlineWidth={0.04} outlineColor="#001a0a" depthOffset={-10}>
            {lon}
          </Text>
          <Text position={[wx, LABEL_Y, wz + 0.3]}
            rotation={[-Math.PI / 2, 0, 0]} fontSize={0.55}
            color="#aaffcc" anchorX="center" anchorY="middle"
            outlineWidth={0.04} outlineColor="#001a0a" depthOffset={-10}>
            {lat}
          </Text>
        </group>
      ))}

      {/* ── Building centroid labels — magenta, name + lon/lat ── */}
      {buildingCoords.map(({ name, wx, wz, lon, lat }) => (
        <group key={`bcoord-${name}`}>
          <Text position={[wx, LABEL_Y, wz - 2.0]}
            rotation={[-Math.PI / 2, 0, 0]} fontSize={1.1} fontWeight={700}
            color="#ff88ff" anchorX="center" anchorY="middle"
            outlineWidth={0.09} outlineColor="#2a002a" depthOffset={-10}>
            {name}
          </Text>
          <Text position={[wx, LABEL_Y, wz + 1.5]}
            rotation={[-Math.PI / 2, 0, 0]} fontSize={0.75}
            color="#ffaaff" anchorX="center" anchorY="middle"
            outlineWidth={0.06} outlineColor="#1a001a" depthOffset={-10}>
            {lon.toFixed(5)}
          </Text>
          <Text position={[wx, LABEL_Y, wz + 3.0]}
            rotation={[-Math.PI / 2, 0, 0]} fontSize={0.75}
            color="#ffaaff" anchorX="center" anchorY="middle"
            outlineWidth={0.06} outlineColor="#1a001a" depthOffset={-10}>
            {lat.toFixed(5)}
          </Text>
        </group>
      ))}

      {/* ── Building bounding-box corner + edge-midpoint labels — cyan ── */}
      {buildingCorners.map(({ key, wx, wz, lon, lat }) => (
        <group key={`bcorner-${key}`}>
          <Text position={[wx, LABEL_Y, wz - 0.75]}
            rotation={[-Math.PI / 2, 0, 0]} fontSize={0.58}
            color="#44ddff" anchorX="center" anchorY="middle"
            outlineWidth={0.04} outlineColor="#001a22" depthOffset={-10}>
            {lon}
          </Text>
          <Text position={[wx, LABEL_Y, wz + 0.65]}
            rotation={[-Math.PI / 2, 0, 0]} fontSize={0.58}
            color="#44ddff" anchorX="center" anchorY="middle"
            outlineWidth={0.04} outlineColor="#001a22" depthOffset={-10}>
            {lat}
          </Text>
        </group>
      ))}
    </group>
  );
}
