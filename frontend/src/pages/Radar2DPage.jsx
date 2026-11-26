import { Component, useMemo, useRef, useState, useCallback, useEffect } from 'react';
import { Radio, Plane, Users, Thermometer } from 'lucide-react';
import { useSimStore } from '../store/useSimStore';
import hiaBase from '../data/hiaBase.json';
import hiaWays from '../data/hiaWays.json';
import hiaRoads from '../data/hiaRoads.json';
import oldAirportWays from '../data/oldAirportWays.json';
import dohaContext from '../data/dohaContext.json';
import CoordDisplay from '../components/panels/CoordDisplay';
import { getPlaneSprite, SPRITE_FLIP_DEG } from './planeSprites.js';
import { AIRPORT } from '../lib/airport.js';
import { PHASE_LABEL as _PHASE_LABEL, PHASE_COLOR as _PHASE_COLOR } from '../lib/phases.js';
import { GATE_LONLAT } from '../data/gateData.js';

// Body/tail livery pairs mirror Aircraft3D.jsx so the rendered 3D-model sprites
// are coloured identically to the 3D scene.
const LIVERY_BT = {
  QR: { body: '#800020', tail: '#5C0632' }, EY: { body: '#c8a15a', tail: '#3a2f22' },
  FZ: { body: '#e9552a', tail: '#1c2b4a' }, EK: { body: '#d71a21', tail: '#c8102e' },
  MS: { body: '#0a4a8f', tail: '#c8a02a' }, SV: { body: '#0a6b3b', tail: '#0a6b3b' },
  KU: { body: '#0a6b3b', tail: '#1c2b4a' }, GF: { body: '#b48a2e', tail: '#5a2036' },
  ME: { body: '#c8102e', tail: '#0a4a8f' }, RJ: { body: '#3a4a5a', tail: '#7a2030' },
  WY: { body: '#7a2036', tail: '#c8a15a' }, '6E': { body: '#0a3a7a', tail: '#0a3a7a' },
  IX: { body: '#c8102e', tail: '#e07a1a' }, G9: { body: '#5a2a7a', tail: '#e9552a' },
  QP: { body: '#1c6ba0', tail: '#e9552a' }, XY: { body: '#0a6b3b', tail: '#c8a02a' },
  WB: { body: '#0a6b5a', tail: '#c8a02a' },
  // European carriers (OSL / AGP and majors) — SK = SAS, per request.
  SK: { body: '#0a3a6b', tail: '#0a2f55' }, DY: { body: '#c8102e', tail: '#8a0020' },
  WF: { body: '#0a7d4a', tail: '#075a36' }, VY: { body: '#e0a800', tail: '#1a2b4a' },
  IB: { body: '#c8102e', tail: '#e0a800' }, UX: { body: '#0a3a7a', tail: '#0a2f55' },
  FR: { body: '#0a3d91', tail: '#f1c40f' }, U2: { body: '#ee6100', tail: '#d1560a' },
  W6: { body: '#c2168c', tail: '#8a0f63' }, LH: { body: '#05164d', tail: '#e0b000' },
  BA: { body: '#1d3d78', tail: '#c8102e' }, AF: { body: '#0a2d6b', tail: '#c8102e' },
  KL: { body: '#0a6ba8', tail: '#0a4a78' }, TP: { body: '#0a6b3b', tail: '#c8102e' },
  AY: { body: '#0a3a8a', tail: '#0a2f66' }, LX: { body: '#c8102e', tail: '#8a0020' },
};
function liveryBT(code) {
  const c = (code || '').toUpperCase();
  if (LIVERY_BT[c]) return LIVERY_BT[c];
  const hue = hashHue(c || 'XX');
  return { body: `hsl(${hue}, 55%, 45%)`, tail: `hsl(${hue}, 60%, 30%)` };
}

// ── Self-contained 2D "radar" view (FlightRadar-style) ─────────────────────────
// A brand-new page that does NOT touch the existing 3D scene. It renders the same
// live store data (positions/flights/gates) on a plain SVG, with:
//   - the real airport footprint drawn from hiaBase.json (lon/lat polygons),
//   - constant-size, heading-rotated plane icons coloured per airline livery,
//   - callsign + route labels, click-to-select (shared selectedEntity),
//   - pan (drag) + zoom (wheel).
// No external map tiles, no deck.gl, no new deps — reliable inside a Databricks App.

// ── Airline livery (mirrors Aircraft3D.jsx so colours match across views) ───────
const AIRLINE_LIVERY = {
  QR: '#800020', EY: '#c8a15a', FZ: '#e9552a', EK: '#d71a21', MS: '#0a4a8f',
  SV: '#0a6b3b', KU: '#1c6b3b', GF: '#b48a2e', ME: '#c8102e', RJ: '#3a4a5a',
  WY: '#7a2036', '6E': '#0a3a7a', IX: '#c8102e', G9: '#5a2a7a', QP: '#1c6ba0',
  XY: '#0a6b3b', WB: '#0a6b5a',
  SK: '#0a3a6b', DY: '#c8102e', WF: '#0a7d4a', VY: '#e0a800', IB: '#c8102e',
  UX: '#0a3a7a', FR: '#0a3d91', U2: '#ee6100', W6: '#c2168c', LH: '#05164d',
  BA: '#1d3d78', AF: '#0a2d6b', KL: '#0a6ba8', TP: '#0a6b3b', AY: '#0a3a8a', LX: '#c8102e',
};
function hashHue(code) {
  let h = 0;
  for (let i = 0; i < code.length; i++) h = (h * 31 + code.charCodeAt(i)) >>> 0;
  return h % 360;
}
function liveryFor(code) {
  const c = (code || '').toUpperCase();
  return AIRLINE_LIVERY[c] || `hsl(${hashHue(c || 'XX')}, 60%, 50%)`;
}

// ── Base-feature colours (light, single-ground style) ───────────────────────────
// Simplified per request: ONE light ground colour for every land / airport-surface
// feature (runway/taxiway/apron/parking/terminal/military/default all share it),
// so the map reads as a single clean surface instead of a patchwork of tones. Only
// water stays visually distinct (blue). The runway + taxiway NETWORK is drawn
// separately as darker lines on top (see RUNWAY_LINES/TAXIWAY_LINES), which is what
// actually conveys the layout — the polygons just form the light landmass.
const PAGE_BG = '#d4c69d';       // Doha desert-sand ground; canvas background matches so edges blend
const GROUND = { fill: '#d4c69d', stroke: '#c2b489' };       // all land/airport surface (sand; stroke a shade darker to blend, no seams)
const TERMINAL = { fill: '#aeb9c6', stroke: '#97a3b2' };     // terminals a touch darker to hint buildings
const WATER = { fill: '#7fa8c9', stroke: '#6f9bbf' };        // the only distinct colour
// Context areas OUTSIDE the operational airport (military compounds, big car/
// staff parking) — the grey blobs visible in the reference. Kept within the
// light-grey family (per the flat-ground palette) but a shade darker so they
// read as distinct built-up land rather than blending into the ground.
const MILITARY = { fill: '#b7bdc4', stroke: '#a4abb3' };
const PARKING  = { fill: '#bcc4cd', stroke: '#a9b3bd' };
const BASE_STYLE = {
  water: WATER, sea: WATER,
  military: MILITARY, parking: PARKING,
  _terminal: TERMINAL,
};
function styleFor(props) {
  const t = props.subtype || props.type || '';
  if (BASE_STYLE[t]) return BASE_STYLE[t];
  if (t.startsWith('concourse') || t.startsWith('terminal')) return TERMINAL;
  return GROUND;  // everything else (runway/taxiway/apron/…) = one light ground
}

// ── Projection: lon/lat → SVG world units (equirectangular, y flipped up) ───────
const CENTER = { lon: 51.6086, lat: 25.266 };
const M_PER_DEG_LAT = 111320;
const M_PER_DEG_LON = M_PER_DEG_LAT * Math.cos((CENTER.lat * Math.PI) / 180);
// scale metres→svg units so the ~9km-wide field spans a comfortable viewport
const SVG_SCALE = 0.05;
function project(lon, lat) {
  const x = (lon - CENTER.lon) * M_PER_DEG_LON * SVG_SCALE;
  const y = -(lat - CENTER.lat) * M_PER_DEG_LAT * SVG_SCALE; // SVG y grows downward
  return [x, y];
}
// Inverse of project(): SVG world units → lon/lat. Used by click-to-inspect so
// clicking anywhere on the map yields the real geo-coordinate (like the 3D view).
function unproject(x, y) {
  const lon = x / (M_PER_DEG_LON * SVG_SCALE) + CENTER.lon;
  const lat = -y / (M_PER_DEG_LAT * SVG_SCALE) + CENTER.lat;
  return { lon: lon.toFixed(6), lat: lat.toFixed(6) };
}

// Precompute SVG path strings for every base polygon (static — done once).
function ringToPath(ring) {
  return ring
    .map((c, i) => {
      const [x, y] = project(c[0], c[1]);
      return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(' ') + ' Z';
}
const BASE_PATHS = hiaBase.features.map((f) => {
  const g = f.geometry;
  const polys = g.type === 'MultiPolygon' ? g.coordinates : [g.coordinates];
  const d = polys.map((poly) => poly.map(ringToPath).join(' ')).join(' ');
  return { d, style: styleFor(f.properties || {}) };
});

// Terminal-building footprint (concourses + terminal hall) as SVG paths — used
// to CLIP the density heatmap so it only shows inside the terminal, mirroring the
// 3D view (whose heatmap plane is masked to the terminal). Without this, the
// per-sensor blobs bleed out over the apron/taxiways.
const TERMINAL_CLIP_PATHS = hiaBase.features
  .filter((f) => {
    const t = String(f.properties?.subtype || f.properties?.type || '');
    return t.startsWith('concourse') || t.startsWith('terminal');
  })
  .map((f) => {
    const g = f.geometry;
    const polys = g.type === 'MultiPolygon' ? g.coordinates : [g.coordinates];
    return polys.map((poly) => poly.map(ringToPath).join(' ')).join(' ');
  });

// ── Density overlays (Pax / Temperature) ────────────────────────────────────────
// Rendered as soft blobs at each live sensor's real lon/lat (see sensorReadings
// in the store), using the same colour ramps as the 3D HeatmapOverlay so the two
// views agree. (An earlier version used hia_zones.geojson polygons, but those are
// coarse boxes that don't line up with the terminal footprint — the sensors carry
// accurate positions.)
// Colour ramps (t in 0..1) — mirror HeatmapOverlay.jsx.
const PAX_STOPS = [
  [0.00, [13, 71, 161]], [0.13, [0, 172, 130]], [0.25, [76, 175, 80]],
  [0.45, [205, 220, 57]], [0.63, [255, 160, 0]], [0.80, [229, 57, 53]], [1.00, [183, 28, 28]],
];
const TEMP_STOPS = [
  [0.00, [13, 71, 161]], [0.18, [30, 136, 229]], [0.32, [0, 172, 130]], [0.45, [76, 175, 80]],
  [0.60, [205, 220, 57]], [0.75, [255, 160, 0]], [0.90, [229, 57, 53]], [1.00, [183, 28, 28]],
];
function rampColor(stops, t) {
  t = Math.min(1, Math.max(0, t));
  for (let i = 1; i < stops.length; i++) {
    if (t <= stops[i][0]) {
      const [t0, c0] = stops[i - 1], [t1, c1] = stops[i];
      const f = (t - t0) / (t1 - t0 || 1);
      const c = [0, 1, 2].map((k) => Math.round(c0[k] + (c1[k] - c0[k]) * f));
      return `rgb(${c[0]},${c[1]},${c[2]})`;
    }
  }
  const c = stops[stops.length - 1][1];
  return `rgb(${c[0]},${c[1]},${c[2]})`;
}
// value → normalised t for each metric (PAX 0–4 pax/m², TEMP 18–40 °C).
const paxT = (v) => (v == null ? 0 : v / 4);
const tempT = (v) => (v == null ? 0 : (v - 18) / 22);

// ── Taxiway / runway centerline network (from hiaWays.json) ─────────────────────
// hiaBase.json only has coarse surface polygons; the actual runway + taxiway
// network lines live in hiaWays.json (same source the 3D scene routes on), but in
// a local "shape" coordinate system. Empirically that space maps into our project
// SVG space by:  svg_x = shape_x * 0.5 + 12.08,  svg_y = shape_z * 0.5
// (verified by matching the two runway centrelines onto hiaBase's runway polygons).
const WAYS_K = 0.5;       // scale ratio between shape space and project space
const WAYS_OX = 12.08;    // x offset (centre-lon difference between the two systems)
function shapeToSvg(p) {
  return [p[0] * WAYS_K + WAYS_OX, p[1] * WAYS_K];
}
function wayToPath(pts) {
  return pts
    .map((p, i) => {
      const [x, y] = shapeToSvg(p);
      return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(' ');
}
// Split into runways (wide, bright) and taxiways (thin, dimmer) so they read
// distinctly — same visual hierarchy FlightRadar uses.
const _RUNWAYS = hiaWays.filter((w) => w.t === 'runway');
const RUNWAY_LINES = _RUNWAYS.map((w) => wayToPath(w.p));
const TAXIWAY_LINES = hiaWays.filter((w) => w.t !== 'runway').map((w) => wayToPath(w.p));

// ── Surrounding context lines (same shape-space as hiaWays, same projection) ────
// These are what makes the 2D map read like the 3D scene OUTSIDE the airfield:
//   - hiaRoads.json    : the real road network (F-Ring Road, cloverleaf
//                        interchanges, terminal access) — carries a per-segment
//                        width `w`, mapped to an SVG stroke width.
//   - oldAirportWays.json : the decommissioned Doha International Airport
//                        runways/taxiways to the west (decorative — deliberately
//                        kept out of the routing graph, mirrors AirportGround.jsx).
// Drawn BEHIND the live airfield network so the operational HIA always sits on top.
const ROAD_LINES = hiaRoads.map((r) => ({ d: wayToPath(r.p), w: (r.w || 0.4) * WAYS_K }));
const OLD_RUNWAY_LINES = oldAirportWays.filter((w) => w.t === 'runway').map((w) => wayToPath(w.p));
const OLD_TAXIWAY_LINES = oldAirportWays.filter((w) => w.t !== 'runway').map((w) => wayToPath(w.p));

// Focus extent = the airport OPERATIONAL area (runways + taxiways in SVG space),
// NOT the full hiaBase data extent (which includes big empty western land that
// shoved the airport small & off to one side). The default/min-zoom fits THIS,
// so the map opens centred and tight on the airfield like the reference.
// Includes the live airfield PLUS the surrounding context (roads + old airport)
// so the default fit and the pan/zoom clamp let you see everything the 3D scene
// shows outside the operational area — not just the runways/taxiways.
const FOCUS_EXTENT = (() => {
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  const eat = (p) => {
    const [x, y] = shapeToSvg(p);
    if (x < minX) minX = x; if (x > maxX) maxX = x;
    if (y < minY) minY = y; if (y > maxY) maxY = y;
  };
  for (const w of hiaWays) for (const p of w.p) eat(p);
  for (const w of oldAirportWays) for (const p of w.p) eat(p);
  for (const r of hiaRoads) for (const p of r.p) eat(p);
  // small margin so nothing is flush to the edge
  const mx = (maxX - minX) * 0.06, my = (maxY - minY) * 0.06;
  return { minX: minX - mx, minY: minY - my, maxX: maxX + mx, maxY: maxY + my };
})();

// ── Surrounding context: REAL OpenStreetMap geometry (dohaContext.json) ─────────
// Built by adhoc/build_doha_context.py from OSM Overpass pulls (coastline, water,
// major roads), simplified and stored as raw lon/lat so it projects identically to
// the airport geometry. Replaces the earlier hand-traced low-fidelity backdrop.
//   - land:    mainland polygon (coastline closed off along the map's west edge)
//   - islands: closed coastline rings (land in the sea)
//   - water:   natural=water polygons (lagoons/marinas cut into the land)
//   - roads:   motorway/trunk (mtr) + primary (pri) arterials only (simplified)
// Drawn BEHIND the airport polygons/network so the detailed airfield sits on top.
const CONTEXT_LABELS = [
  { lon: 51.500, lat: 25.280, text: 'DOHA', size: 12, fill: '#6b7580' },
  { lon: 51.628, lat: 25.360, text: 'PERSIAN GULF', size: 11, fill: '#5f7f9c', italic: true },
  { lon: 51.582, lat: 25.316, text: 'Ras Abu Aboud', size: 6, fill: '#8a7a6a' },
];
const SEA = '#aad3df';   // OSM-style sea blue

// Road styling per class — mirrors OSM's carto palette (motorway/trunk red-ish,
// primary orange, secondary yellow). `w` is the base SVG stroke width (divided by
// the live zoom scale at render time so it stays a constant on-screen thickness).
const ROAD_STYLE = {
  mtr: { stroke: '#e28f7a', w: 2.2 },
  pri: { stroke: '#efb36b', w: 1.7 },
};

function lonLatRingToPath(ring) {
  return ring
    .map((c, i) => {
      const [x, y] = project(c[0], c[1]);
      return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(' ') + ' Z';
}
function lonLatLineToPath(pts) {
  return pts
    .map((c, i) => {
      const [x, y] = project(c[0], c[1]);
      return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(' ');
}
// Precompute all context SVG paths once (static data).
const LAND_PATH = lonLatRingToPath(dohaContext.land);
const ISLAND_PATHS = dohaContext.islands.map(lonLatRingToPath);
const WATER_PATHS = dohaContext.water.map(lonLatRingToPath);

// ── Gate-number labels (empty stands) ───────────────────────────────────────────
// Painted stand numbers on the apron, mirroring the 3D scene's GateMarker: shown
// only for the real modeled stands (VISIBLE_GATES) and only while the stand is
// EMPTY — once a flight is parked / gate-parked / taxiing out of it the number is
// hidden (the plane sits on it, redundant clutter). Positions come from the same
// GATE_LONLAT source of truth the 3D scene uses. Pre-projected once at module load.
const GATE_VISIBLE = new Set([
  'A1', 'A3', 'A4', 'A5', 'A6', 'A7', 'A8', 'A9', 'A10', 'A11',
  'B1', 'B2', 'B3', 'B4', 'B5', 'B6', 'B7', 'B8', 'B9', 'B10',
  'C1', 'C2', 'C3', 'C4', 'C5', 'C6', 'C7', 'C8', 'C9',
  'C10', 'C11', 'C12', 'C13', 'C14', 'C15', 'C16', 'C18', 'C19',
  'C20', 'C22', 'C23', 'C24', 'C25', 'C26',
  'D1', 'D2', 'D3', 'D4', 'D5', 'D6', 'D7', 'D8', 'D9',
  'E1', 'E2', 'E3', 'E4', 'E5', 'E6', 'E7', 'E8',
]);
// Phases where the stand's number is hidden (mirrors Airport3D's HIDE_NUM_PHASES).
const GATE_HIDE_PHASES = new Set(['PARKED', 'GATE_PARKED', 'TAXIING_OUT']);
const GATE_LABEL_POS = Object.entries(GATE_LONLAT)
  .filter(([id]) => GATE_VISIBLE.has(id))
  .map(([id, ll]) => {
    const [x, y] = project(ll[0], ll[1]);
    return { id, x, y };
  });
// Group roads by class so each class draws in one <g> with its own stroke.
const CONTEXT_ROADS_BY_CLASS = ['mtr', 'pri'].map((cls) => ({
  cls,
  paths: dohaContext.roads.filter((r) => r.c === cls).map((r) => lonLatLineToPath(r.p)),
}));

// The context spans well beyond the airfield; a big sea rect behind everything
// covers the viewport at any zoom. Sized around both the focus extent and the
// real land polygon so it never runs out.
const CONTEXT_EXTENT = (() => {
  let minX = FOCUS_EXTENT.minX, minY = FOCUS_EXTENT.minY,
      maxX = FOCUS_EXTENT.maxX, maxY = FOCUS_EXTENT.maxY;
  for (const c of dohaContext.land) {
    const [x, y] = project(c[0], c[1]);
    if (x < minX) minX = x; if (x > maxX) maxX = x;
    if (y < minY) minY = y; if (y > maxY) maxY = y;
  }
  return { minX, minY, maxX, maxY };
})();
// Sea backdrop rect — padded generously beyond the context so it always fills.
const SEA_RECT = (() => {
  const w = CONTEXT_EXTENT.maxX - CONTEXT_EXTENT.minX;
  const h = CONTEXT_EXTENT.maxY - CONTEXT_EXTENT.minY;
  return {
    x: CONTEXT_EXTENT.minX - w, y: CONTEXT_EXTENT.minY - h,
    w: w * 3, h: h * 3,
  };
})();

// Default view extent = the airfield, pulled back a bit so some surrounding
// coastline/sea shows around it (per the "zoom out a bit by default" choice).
// Expands FOCUS_EXTENT outward by DEFAULT_ZOOM_OUT× its size on each side. Used
// for the initial fit and as the pan/zoom-out clamp bound, so the user can see
// (and pan over) the context but still can't drag the airport into empty sea.
const DEFAULT_ZOOM_OUT = 0.55;
const VIEW_EXTENT = (() => {
  const w = FOCUS_EXTENT.maxX - FOCUS_EXTENT.minX;
  const h = FOCUS_EXTENT.maxY - FOCUS_EXTENT.minY;
  const ex = w * DEFAULT_ZOOM_OUT, ey = h * DEFAULT_ZOOM_OUT;
  return {
    minX: FOCUS_EXTENT.minX - ex, minY: FOCUS_EXTENT.minY - ey,
    maxX: FOCUS_EXTENT.maxX + ex, maxY: FOCUS_EXTENT.maxY + ey,
  };
})();

// The airfield itself — runways + taxiways only (no roads / old airport). The home
// view's default AND minimum zoom is set from this: the airfield fills
// HOME_AIRFIELD_FILL of the map's limiting dimension, which reproduces the agreed
// reference view (whole airfield visible with coastline around it) on any screen size.
const AIRFIELD_EXTENT = (() => {
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const w of hiaWays) for (const p of w.p) {
    const [x, y] = shapeToSvg(p);
    if (x < minX) minX = x; if (x > maxX) maxX = x;
    if (y < minY) minY = y; if (y > maxY) maxY = y;
  }
  return { minX, minY, maxX, maxY };
})();
// Home view = minimum zoom: the airfield fills this share of the map's limiting
// dimension. History: 0.7 (first reference) → ÷1.3 (one step out) → ×1.3² (two steps in).
const HOME_AIRFIELD_FILL = 0.7 * 1.3;
// Pan bounds: VIEW_EXTENT made SYMMETRIC around the airfield centre, so the home view
// (centred on the airfield) is always a valid position — otherwise the first drag on a
// wide screen snapped the map sideways back inside the old, off-centre bounds.
const PAN_EXTENT = (() => {
  const cx = (AIRFIELD_EXTENT.minX + AIRFIELD_EXTENT.maxX) / 2;
  const cy = (AIRFIELD_EXTENT.minY + AIRFIELD_EXTENT.maxY) / 2;
  const hx = Math.max(cx - VIEW_EXTENT.minX, VIEW_EXTENT.maxX - cx);
  const hy = Math.max(cy - VIEW_EXTENT.minY, VIEW_EXTENT.maxY - cy);
  return { minX: cx - hx, maxX: cx + hx, minY: cy - hy, maxY: cy + hy };
})();

// Runway stroke width in SVG units (matches the <path> below: 6 * WAYS_K).
const RWY_W = 6 * WAYS_K;
const RWY_HW = RWY_W / 2;

// Runway identifiers — same mapping the 3D view uses (RunwayMarkings.jsx):
// runway[0] = western (16R / 34L), runway[1] = eastern (16L / 34R). "start" is
// the NW threshold (pts[0]), "end" is the SE threshold (last point).
const RWY_IDS = [
  { startLabel: '16R', endLabel: '34L' },
  { startLabel: '16L', endLabel: '34R' },
];

// Build 2D runway markings (threshold piano keys, dashed centreline, and number
// labels) in SVG space — the flat-map analogue of RunwayMarkings.jsx.
const RUNWAY_MARKINGS = _RUNWAYS.map((rw, idx) => {
  const svgPts = rw.p.map(shapeToSvg);
  const [x0, y0] = svgPts[0];
  const [xN, yN] = svgPts[svgPts.length - 1];
  // unit vector along the runway (start → end) and its perpendicular
  const dx = xN - x0, dy = yN - y0;
  const len = Math.hypot(dx, dy) || 1;
  const ux = dx / len, uy = dy / len;       // along-runway
  const px = -uy, py = ux;                    // across-runway (perpendicular)

  // Threshold "piano keys": a block of LONGITUDINAL stripes (running ALONG the
  // runway) laid side-by-side across the width, symmetric about the centreline
  // with a gap in the middle — exactly the real pattern in the satellite image.
  const keys = [];
  const STRIPE_LEN  = RWY_HW * 1.6;   // length ALONG the runway (long)
  const STRIPE_W    = 0.28;           // width ACROSS the runway (narrow)
  const CROSS_GAP   = 0.34;           // gap between adjacent stripes (across)
  const CL_GAP      = 0.55;           // clear gap straddling the centreline
  const EDGE_INSET  = 0.5;            // keep stripes off the very runway edge
  const START_INSET = 1.2;            // block starts this far in from the threshold

  const ends = [
    { ex: x0, ey: y0, inx: ux, iny: uy },        // NW threshold, into runway = +u
    { ex: xN, ey: yN, inx: -ux, iny: -uy },      // SE threshold, into runway = -u
  ];
  for (const e of ends) {
    // stripe centres marched outward from the centre gap toward each edge
    for (const dir of [-1, 1]) {
      let off = CL_GAP / 2 + STRIPE_W / 2;
      while (off + STRIPE_W / 2 <= RWY_HW - EDGE_INSET) {
        const coff = dir * off;                       // across-runway offset
        // stripe rectangle: STRIPE_W across × STRIPE_LEN along, starting
        // START_INSET in from the threshold and extending inward.
        const nearx = e.ex + e.inx * START_INSET, neary = e.ey + e.iny * START_INSET;
        const cx = nearx + px * coff, cy = neary + py * coff;
        const ax = px * STRIPE_W / 2, ay = py * STRIPE_W / 2;      // half across
        const lx = e.inx * STRIPE_LEN,  ly = e.iny * STRIPE_LEN;   // full length inward
        keys.push(
          `M${(cx - ax).toFixed(1)},${(cy - ay).toFixed(1)} ` +
          `L${(cx + ax).toFixed(1)},${(cy + ay).toFixed(1)} ` +
          `L${(cx + ax + lx).toFixed(1)},${(cy + ay + ly).toFixed(1)} ` +
          `L${(cx - ax + lx).toFixed(1)},${(cy - ay + ly).toFixed(1)} Z`
        );
        off += STRIPE_W + CROSS_GAP;
      }
    }
  }

  // Centreline (single dashed path along the runway, inset from both thresholds).
  const inset = 6;
  const centre = `M${(x0 + ux * inset).toFixed(1)},${(y0 + uy * inset).toFixed(1)} ` +
                 `L${(xN - ux * inset).toFixed(1)},${(yN - uy * inset).toFixed(1)}`;

  // Number labels, placed just inside each threshold, oriented to read ALONG the
  // runway in each end's direction of travel (like real painted runway numbers):
  //  - NW threshold "16x": a pilot lands heading SE (travel = +u) → text reads +u.
  //  - SE threshold "34x": a pilot lands heading NW (travel = −u) → text reads −u.
  const TEXT_DIST = START_INSET + STRIPE_LEN + 3;  // just past the piano-key block
  const { startLabel, endLabel } = RWY_IDS[idx] ?? { startLabel: '', endLabel: '' };
  const LABEL_ROT_OFFSET = 90;  // rotate number labels 90° clockwise (SVG rotate is clockwise-positive)
  const labels = [
    { x: x0 + ux * TEXT_DIST, y: y0 + uy * TEXT_DIST, text: startLabel,
      rot: (Math.atan2(uy, ux) * 180) / Math.PI + LABEL_ROT_OFFSET },
    { x: xN - ux * TEXT_DIST, y: yN - uy * TEXT_DIST, text: endLabel,
      rot: (Math.atan2(-uy, -ux) * 180) / Math.PI + LABEL_ROT_OFFSET },
  ];

  return { keys, centre, labels };
});

// Overall data extent (for the initial fit-to-view).
const EXTENT = (() => {
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const f of hiaBase.features) {
    const g = f.geometry;
    const polys = g.type === 'MultiPolygon' ? g.coordinates : [g.coordinates];
    for (const poly of polys)
      for (const ring of poly)
        for (const c of ring) {
          const [x, y] = project(c[0], c[1]);
          if (x < minX) minX = x; if (x > maxX) maxX = x;
          if (y < minY) minY = y; if (y > maxY) maxY = y;
        }
  }
  return { minX, minY, maxX, maxY };
})();

// ── Commercial-airliner glyph, built from SEPARATE primitives ───────────────────
// Nose points UP (−y = forward) before the heading rotation. Instead of one
// fragile hand-traced outline (which came out looking like an arrowhead), the
// plane is composed of three clean shapes, all filled the same colour so they
// read as one silhouette:
//   1. FUSELAGE  — a long tapered capsule (rounded nose, pointed tail)
//   2. WINGS     — one swept-back delta polygon spanning both sides
//   3. TAILPLANE — a small swept polygon near the tail
// Coordinates are in a base frame ~26 units long / ~26 wide; per-class `scale`
// sizes it and `engines` place the nacelle dots (4 for A380, 2 for twins).
// FUSELAGE — full-bodied airliner, sharply pointed nose (up), tapered tail.
const FUSELAGE_PATH =
  'M0,-14 '                       // nose tip
  + 'C1.5,-13.2 2.0,-10 2.0,-6.5 '// round out to full (wider) width
  + 'L2.0,7 '                     // straight body
  + 'C2.0,10.6 1.1,12.5 0,13 '    // taper to tail point
  + 'C-1.1,12.5 -2.0,10.6 -2.0,7 '// (mirror)
  + 'L-2.0,-6.5 '
  + 'C-2.0,-10 -1.5,-13.2 0,-14 Z';
// WINGS — mid-mounted, moderate sweep, fatter chord, pointed tips.
const WINGS_PATH =
  'M1.6,-2.6 '      // right wing root, leading edge (mid fuselage)
  + 'L13,5 '        // right wingtip (swept out & aft)
  + 'L2.0,3.8 '     // right trailing edge back to fuselage (deeper chord)
  + 'L-2.0,3.8 '    // across to left root trailing
  + 'L-13,5 '       // left wingtip
  + 'L-1.6,-2.6 Z'; // left leading edge back to root
// TAILPLANE — small horizontal stabiliser at the very back (a touch fatter).
const TAIL_PATH =
  'M1.3,8.2 '
  + 'L5.5,12 '
  + 'L1.3,10.6 '
  + 'L-1.3,10.6 '
  + 'L-5.5,12 '
  + 'L-1.3,8.2 Z';

function resolveType(aircraftType) {
  const t = (aircraftType || '').toUpperCase();
  if (/A38[08]/.test(t)) return 'JUMBO';
  if (/B77|A35[09]|A35K|B78[789]|A33[023]|B74|B76/.test(t)) return 'WIDE';
  return 'NARROW';
}
// Engines sit on the swept wing line (root ~(1.2,-1.5) → tip ~(12.5,5)):
// inner engines inboard/forward, outer engines outboard/aft.
const CLASS = {
  JUMBO:  { scale: 1.15, engines: [[-8, 3.2], [-4.5, 1.5], [4.5, 1.5], [8, 3.2]], er: 0.7 },
  WIDE:   { scale: 0.95, engines: [[-5.5, 2], [5.5, 2]],                          er: 0.85 },
  NARROW: { scale: 0.7,  engines: [[-4.8, 1.7], [4.8, 1.7]],                      er: 0.9 },
};

// Sprite footprint in SVG world units per class (the sprite is a square; the
// plane fills it). Bumped so planes read clearly, sized by aircraft class.
const SPRITE_SPAN = { JUMBO: 34, WIDE: 30, NARROW: 24 };

function AircraftGlyph({ flight, pos, scale, selected, onSelect, onSpriteReady, showDetails = true }) {
  if (pos?.lat == null || pos?.lon == null) return null;
  const [x, y] = project(pos.lon, pos.lat);
  const heading = pos.heading || 0; // 0=N, clockwise; SVG rotate is clockwise from up
  // Partial counter-scale: exponent 1 = constant on-screen size at every zoom;
  // <1 lets the glyph grow a bit as you zoom in (screen size ∝ scale^(1−exp)).
  const ZOOM_GROWTH = 0.8;
  const s = 1 / Math.pow(scale, ZOOM_GROWTH);
  const callsign = flight?.callsign || flight?.id || '';
  const route = `${flight?.origin || '—'}→${flight?.destination || '—'}`;
  const modelKey = resolveType(flight?.aircraft_type);
  const color = liveryFor(flight?.airline);

  // The exact 3D model rendered top-down as a sprite; falls back to the SVG
  // glyph until the sprite is generated.
  const sprite = getPlaneSprite(modelKey, liveryBT(flight?.airline), onSpriteReady);
  const span = SPRITE_SPAN[modelKey];
  const cls = CLASS[modelKey];
  const bodyColor = liveryBT(flight?.airline).body;  // aircraft body colour, for the labels

  return (
    <g transform={`translate(${x},${y}) scale(${s})`} style={{ cursor: 'pointer' }}
       onClick={(e) => { e.stopPropagation(); onSelect(selected ? null : flight?.id); }}>
      {/* Invisible hit-area — the sprite <image> has pointerEvents:none (so it
          can't trigger native image-drag), which would leave the plane
          unclickable. This transparent circle captures the click/select. */}
      <circle r={span * 0.55} fill="#000" fillOpacity={0.001} />
      {selected && (
        <circle r={span * 0.62} fill="none" stroke="#38bdf8" strokeWidth={1.5} opacity={0.9} />
      )}
      <g transform={`rotate(${heading + SPRITE_FLIP_DEG})`}>
        {sprite ? (
          <image href={sprite} x={-span / 2} y={-span / 2} width={span} height={span}
                 draggable={false}
                 style={{ imageRendering: 'auto', WebkitUserDrag: 'none', userSelect: 'none', pointerEvents: 'none' }} />
        ) : (
          <g transform={`scale(${cls.scale})`}>
            <path d={WINGS_PATH} fill={color} stroke="#0a0e1a" strokeWidth={0.5} strokeLinejoin="round" />
            <path d={TAIL_PATH}  fill={color} stroke="#0a0e1a" strokeWidth={0.5} strokeLinejoin="round" />
            <path d={FUSELAGE_PATH} fill={color} stroke="#0a0e1a" strokeWidth={0.5} strokeLinejoin="round" />
            {cls.engines.map(([ex, ey], i) => (
              <circle key={i} cx={ex} cy={ey} r={cls.er} fill="#0a0e1a" opacity={0.85} />
            ))}
          </g>
        )}
      </g>
      {/* When SELECTED the rich popup is drawn in a separate top layer (see
          SelectedAircraftPopup) so it's never hidden under other planes; the
          minimal callsign+route labels are hidden to avoid clutter beneath it.
          `s` (the zoom counter-scale) keeps the labels constant on-screen. */}
      {selected ? null : showDetails ? (
        <>
          {/* callsign + route in the aircraft's body colour, plain bold text
              (no double-edge stroke) */}
          <text x={span * 0.55} y={-4} fontSize={9} fontWeight={700} fill={bodyColor}>
            {callsign}
          </text>
          <text x={span * 0.55} y={6} fontSize={5.5} fontWeight={700} fill={bodyColor}>
            {route}
          </text>
        </>
      ) : null}
    </g>
  );
}

// Rich info card shown beside a SELECTED aircraft. Rendered in a dedicated TOP
// layer (after all planes) so it's never hidden under other aircraft, but still
// inside the pan/zoom group so it tracks the plane. Airline logo + callsign
// (type), gate, origin→destination, and the scheduled arrival/departure time.
const _fmtSched = (iso, tz = 'Asia/Qatar') => {
  if (!iso) return null;
  try {
    return new Date(iso).toLocaleTimeString('en-GB', {
      hour: '2-digit', minute: '2-digit', timeZone: tz,
    });
  } catch { return null; }
};

// Relative luminance of a hex colour (0=black, 1=white) → pick readable text.
function _lum(hex) {
  const h = (hex || '').replace('#', '');
  const n = h.length === 3 ? h.split('').map((c) => c + c).join('') : h;
  const r = parseInt(n.slice(0, 2), 16), g = parseInt(n.slice(2, 4), 16), b = parseInt(n.slice(4, 6), 16);
  if ([r, g, b].some(Number.isNaN)) return 0;
  return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255;
}

// Card footprint in SVG world units (counter-scaled by the parent so it's a
// constant size on-screen). Bigger than before, per request.
const POPUP_W = 132, POPUP_H = 68;

// Just the card artwork, drawn from its top-left corner at (0,0). Background is
// the aircraft's livery colour; text colour flips to stay readable on light or
// dark liveries; a white chip guarantees the airline logo is always legible.
function AircraftPopup({ flight, tz = 'Asia/Qatar', tzAbbr = 'AST', homeIata = 'DOH' }) {
  const isArrival = flight?.destination === homeIata;
  const callsign = flight?.callsign || flight?.id || '';
  const type = flight?.aircraft_type || '—';
  const gate = flight?.assigned_gate;
  const origin = flight?.origin || '—';
  const dest = flight?.destination || '—';
  const sched = _fmtSched(isArrival ? flight?.scheduled_arr : flight?.scheduled_dep, tz);
  const airline = (flight?.airline || '').toUpperCase();
  const bg = liveryBT(flight?.airline).body;         // aircraft livery colour = card bg
  const dark = _lum(bg) < 0.55;
  const fg = dark ? '#ffffff' : '#0a0e1a';
  const muted = dark ? 'rgba(255,255,255,0.74)' : 'rgba(10,14,26,0.66)';
  const border = dark ? 'rgba(255,255,255,0.38)' : 'rgba(10,14,26,0.32)';
  const pad = 9;
  const logo = `/airline-logos/${airline}.png`;

  return (
    <>
      {/* soft drop shadow to separate the card from the busy map */}
      <rect x={1.5} y={2} width={POPUP_W} height={POPUP_H} rx={8} fill="#000" opacity={0.4} />
      {/* card — filled with the aircraft's livery colour */}
      <rect x={0} y={0} width={POPUP_W} height={POPUP_H} rx={8} fill={bg} stroke={border} strokeWidth={1.2} />
      {/* white chip so the airline logo is legible on any livery — snug to the top */}
      <rect x={pad} y={6} width={56} height={16} rx={3.5} fill="#ffffff" />
      <image href={logo} x={pad + 3} y={7.5} width={50} height={13}
             preserveAspectRatio="xMidYMid meet" onError={(e) => { e.target.style.display = 'none'; }} />
      {/* callsign (type) */}
      <text x={pad} y={37} fontSize={13} fontWeight={800} fill={fg}>
        {callsign} <tspan fontSize={8.5} fontWeight={500} fill={muted}>({type})</tspan>
      </text>
      {/* gate + route on one row */}
      <text x={pad} y={49} fontSize={8} fill={muted}>
        GATE <tspan fill={fg} fontWeight={700} fontSize={9}>{gate || '—'}</tspan>
      </text>
      <text x={pad + 48} y={49} fontSize={9} fontWeight={800} fill={fg}>
        {origin} <tspan fill={muted}>→</tspan> {dest}
      </text>
      {/* scheduled arr/dep time — leaves ~9u of breathing room below to the card edge */}
      <text x={pad} y={59} fontSize={8} fill={muted}>
        {isArrival ? 'ARR' : 'DEP'} <tspan fill={fg} fontWeight={700} fontSize={9}>{sched || '—'}</tspan>
        <tspan fill={muted}> {tzAbbr}</tspan>
      </text>
    </>
  );
}

// Positions the card beside the selected plane, counter-scaled so it stays a
// constant on-screen size (mirrors AircraftGlyph's own counter-scale).
function SelectedAircraftPopup({ pos, flight, scale, tz, tzAbbr, homeIata }) {
  if (pos?.lat == null || pos?.lon == null) return null;
  const [x, y] = project(pos.lon, pos.lat);
  const s = 1 / Math.pow(scale, 0.8);
  const span = SPRITE_SPAN[resolveType(flight?.aircraft_type)];
  return (
    <g transform={`translate(${x},${y}) scale(${s})`} style={{ pointerEvents: 'none' }}>
      <g transform={`translate(${span * 0.6}, ${-POPUP_H / 2})`}>
        <AircraftPopup flight={flight} tz={tz} tzAbbr={tzAbbr} homeIata={homeIata} />
      </g>
    </g>
  );
}

function Radar2DInner() {
  const positions = useSimStore((s) => s.positions);
  const flights = useSimStore((s) => s.flights);
  const selectedEntity = useSimStore((s) => s.selectedEntity);
  const setSelectedEntity = useSimStore((s) => s.setSelectedEntity);
  const setMapClickCoord = useSimStore((s) => s.setMapClickCoord);
  const simTime = useSimStore((s) => s.simTime);
  const sensorReadings = useSimStore((s) => s.sensorReadings);
  const feed = useSimStore((s) => s.feed);
  const { tz, tz_abbr: tzAbbr } = AIRPORT;
  const homeIata = AIRPORT.iata;
  const airportLabel = AIRPORT.iata;

  // 2D-only layer toggles (local — does NOT touch the 3D view's layers). Here
  // "Aircraft" toggles the DETAIL LABELS only (callsign + route); the plane icon
  // always shows. Pax/Temperature draw the zone density overlays.
  const [radarLayers, setRadarLayers] = useState({ aircraft: true, pax: false, temp: false });
  const toggleRadar = useCallback((k) => setRadarLayers((s) => ({ ...s, [k]: !s[k] })), []);
  // Only one density overlay at a time reads clearly; turning one on turns the
  // other off.
  const setDensity = useCallback((k) => setRadarLayers((s) => ({
    ...s, pax: k === 'pax' ? !s.pax : false, temp: k === 'temp' ? !s.temp : false,
  })), []);

  const svgRef = useRef(null);
  const [size, setSize] = useState({ w: 1000, h: 700 });
  // Bumped whenever a plane sprite finishes rendering, to re-draw with it.
  const [, setSpriteTick] = useState(0);
  const onSpriteReady = useCallback(() => setSpriteTick((t) => t + 1), []);

  // Fit the airport extent into the viewport on first mount / resize. This
  // fit-to-view scale is ALSO the minimum zoom — you can't zoom out further, so
  // the airport always fills the viewport (no vast empty margins around it).
  const pad = 40;
  // How much further than fit-to-view you can zoom in. Bumped 12 → 18 to allow one
  // more zoom-in level.
  const ZOOM_RANGE = 18;
  // Fit-to-view scale + airfield centre (the furthest you can zoom out).
  const fitInfo = useMemo(() => {
    const ew = VIEW_EXTENT.maxX - VIEW_EXTENT.minX;
    const eh = VIEW_EXTENT.maxY - VIEW_EXTENT.minY;
    const fitScale = Math.min((size.w - pad * 2) / ew, (size.h - pad * 2) / eh);
    const cx = (VIEW_EXTENT.minX + VIEW_EXTENT.maxX) / 2;
    const cy = (VIEW_EXTENT.minY + VIEW_EXTENT.maxY) / 2;
    return { fitScale, cx, cy };
  }, [size]);
  // Default = minimum zoom: the airfield fills HOME_AIRFIELD_FILL (70%) of the map's
  // limiting dimension — the agreed reference view. You can zoom in from there but
  // never out past it. Zoom-in headroom is unchanged (same absolute max as before).
  const airfieldFit = Math.min(size.w / (AIRFIELD_EXTENT.maxX - AIRFIELD_EXTENT.minX),
                               size.h / (AIRFIELD_EXTENT.maxY - AIRFIELD_EXTENT.minY));
  const DEFAULT_SCALE = airfieldFit * HOME_AIRFIELD_FILL;
  const MIN_SCALE = DEFAULT_SCALE;
  const MAX_SCALE = Math.max(fitInfo.fitScale * ZOOM_RANGE, MIN_SCALE * 4);
  const initialView = useMemo(() => {
    // Open centred on the AIRFIELD itself (not the wider context extent, whose roads
    // and old airport pulled the centre left).
    const cx = (AIRFIELD_EXTENT.minX + AIRFIELD_EXTENT.maxX) / 2;
    const cy = (AIRFIELD_EXTENT.minY + AIRFIELD_EXTENT.maxY) / 2;
    const scale = DEFAULT_SCALE;
    return { scale, tx: size.w / 2 - cx * scale, ty: size.h / 2 - cy * scale };
  }, [size, DEFAULT_SCALE]);

  // Clamp a {scale,tx,ty} view so the airport can never be dragged into empty
  // space. Per axis: if the extent is SMALLER than the viewport (true at the
  // min/fit zoom), the extent is force-CENTRED (→ no panning possible at min
  // zoom). If it's LARGER (zoomed in), tx/ty are clamped so the extent always
  // covers the viewport — you can pan within the airport but not past its edges.
  const clampView = useCallback((v) => {
    const scale = Math.max(MIN_SCALE, Math.min(MAX_SCALE, v.scale));
    const E = PAN_EXTENT;
    const sw = (E.maxX - E.minX) * scale;
    const sh = (E.maxY - E.minY) * scale;
    let { tx, ty } = v;
    if (sw <= size.w) {
      tx = size.w / 2 - ((E.minX + E.maxX) / 2) * scale;  // centre
    } else {
      tx = Math.min(-E.minX * scale, Math.max(size.w - E.maxX * scale, tx));
    }
    if (sh <= size.h) {
      ty = size.h / 2 - ((E.minY + E.maxY) / 2) * scale;  // centre
    } else {
      ty = Math.min(-E.minY * scale, Math.max(size.h - E.maxY * scale, ty));
    }
    return { scale, tx, ty };
  }, [MIN_SCALE, MAX_SCALE, size]);

  const [view, setView] = useState(initialView);
  // Keep the view fitted+centred on every size change UNTIL the user first
  // interacts (pans or zooms). The container is initially the default 1000×700
  // and the real (wider) size arrives a frame later via the ResizeObserver — a
  // one-shot fit would lock in the default size and leave the map off-centre, so
  // we re-fit until the user takes over.
  const userMovedRef = useRef(false);
  useEffect(() => {
    if (!userMovedRef.current && size.w > 0) setView(initialView);
  }, [initialView, size]);

  // measure container. Only commit a NEW size when it actually changed (rounded
  // to whole px) — ResizeObserver can fire spuriously (sub-pixel/scroll/re-render)
  // and every new `size` object would re-run the fit effect and reset the user's
  // zoom. Skipping no-op fires keeps the zoom level persisted.
  useEffect(() => {
    const el = svgRef.current?.parentElement;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      const r = entries[0].contentRect;
      const w = Math.round(r.width), h = Math.round(r.height);
      setSize((prev) => (prev.w === w && prev.h === h ? prev : { w, h }));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // ── pan + zoom ────────────────────────────────────────────────────────────
  const drag = useRef(null);
  // `dragging` state (not just the ref) so we can drop the expensive heatmap
  // blur filter while panning — re-rasterizing an SVG filter every drag frame
  // can blank the whole SVG in some browsers. Blur restores on release.
  const [dragging, setDragging] = useState(false);
  const onPointerDown = (e) => {
    // Do NOT setPointerCapture here — capturing redirects the subsequent `click`
    // to the SVG, so a plane's onClick never fires (clicking a plane did nothing).
    // We only start capturing once a real drag begins (see onPointerMove), so a
    // plain click stays targeted at the plane.
    drag.current = { x: e.clientX, y: e.clientY, tx: view.tx, ty: view.ty,
                     pointerId: e.pointerId, moved: false };
  };
  const onPointerMove = (e) => {
    const d = drag.current;
    if (!d) return;
    const dx = e.clientX - d.x, dy = e.clientY - d.y;
    // Ignore sub-threshold jitter so a click isn't mistaken for a drag.
    if (!d.moved && Math.hypot(dx, dy) < 4) return;
    if (!d.moved) {
      d.moved = true;
      setDragging(true);
      // Now that it's a genuine drag, capture so we keep move/up even if the
      // pointer crosses a plane or leaves the svg.
      try { e.currentTarget.setPointerCapture(d.pointerId); } catch { /* noop */ }
    }
    userMovedRef.current = true;
    const nextTx = d.tx + dx;
    const nextTy = d.ty + dy;
    setView((v) => clampView({ ...v, tx: nextTx, ty: nextTy }));
  };
  const justDragged = useRef(false);
  const onPointerUp = (e) => {
    try { e.currentTarget.releasePointerCapture(e.pointerId); } catch { /* noop */ }
    justDragged.current = !!drag.current?.moved;  // remember if this gesture panned
    drag.current = null;
    setDragging(false);
  };
  // Background click clears the selection AND reports the clicked geo-coordinate
  // (like the 3D scene's ground click) — but NOT the click that ends a pan.
  const onBackgroundClick = (e) => {
    if (justDragged.current) { justDragged.current = false; return; }
    setSelectedEntity(null);
    // screen px → world units (undo the view transform) → lon/lat
    const rect = svgRef.current?.getBoundingClientRect();
    if (rect) {
      const wx = (e.clientX - rect.left - view.tx) / view.scale;
      const wy = (e.clientY - rect.top - view.ty) / view.scale;
      setMapClickCoord(unproject(wx, wy));
    }
  };
  const onWheel = useCallback((e) => {
    e.preventDefault();
    userMovedRef.current = true;
    setView((v) => {
      const rect = svgRef.current.getBoundingClientRect();
      const mx = e.clientX - rect.left, my = e.clientY - rect.top;
      const factor = e.deltaY < 0 ? 1.15 : 1 / 1.15;
      const scale = Math.max(MIN_SCALE, Math.min(MAX_SCALE, v.scale * factor));
      // zoom toward the cursor, then clamp so we can't reveal empty space
      const k = scale / v.scale;
      return clampView({ scale, tx: mx - (mx - v.tx) * k, ty: my - (my - v.ty) * k });
    });
  }, [MIN_SCALE, MAX_SCALE, clampView]);

  // Zoom to an explicit scale (from the slider), keeping the viewport centre fixed.
  const zoomTo = useCallback((target) => {
    userMovedRef.current = true;
    setView((v) => {
      const scale = Math.max(MIN_SCALE, Math.min(MAX_SCALE, target));
      const cx = size.w / 2, cy = size.h / 2;
      const k = scale / v.scale;
      return clampView({ scale, tx: cx - (cx - v.tx) * k, ty: cy - (cy - v.ty) * k });
    });
  }, [MIN_SCALE, MAX_SCALE, size, clampView]);

  // Bind a non-passive wheel listener so preventDefault works.
  useEffect(() => {
    const el = svgRef.current;
    if (!el) return;
    el.addEventListener('wheel', onWheel, { passive: false });
    return () => el.removeEventListener('wheel', onWheel);
  }, [onWheel]);

  const posList = useMemo(() => Object.values(positions), [positions]);
  const sensorList = useMemo(() => Object.values(sensorReadings), [sensorReadings]);
  // Gates whose number to HIDE — a flight is parked at / gate-parked / taxiing out
  // of the stand (mirrors Airport3D.occupiedGateIds). Empty stands keep their label.
  const occupiedGates = useMemo(() => {
    const s = new Set();
    for (const f of Object.values(flights)) {
      const phase = positions[f.id]?.phase ?? f.phase;
      if (f.assigned_gate && GATE_HIDE_PHASES.has(phase)) s.add(f.assigned_gate);
    }
    return s;
  }, [flights, positions]);

  const clockText = simTime
    ? new Date(simTime).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit', timeZone: tz })
    : '—';

  return (
    <div style={{ position: 'relative', width: '100%', height: '100%', background: PAGE_BG, overflow: 'hidden' }}>
      <svg
        ref={svgRef}
        width={size.w}
        height={size.h}
        style={{ display: 'block', cursor: drag.current ? 'grabbing' : 'grab', touchAction: 'none', userSelect: 'none' }}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerLeave={onPointerUp}
        onDragStart={(e) => e.preventDefault()}
        onClick={onBackgroundClick}
      >
        <g transform={`translate(${view.tx},${view.ty}) scale(${view.scale})`}>
          <defs>
            {/* soft blur that melds the per-sensor density blobs (in the scaled
                coordinate space, so the spread stays constant in world units) */}
            <filter id="heatBlur" x="-30%" y="-30%" width="160%" height="160%">
              <feGaussianBlur stdDeviation="4" />
            </filter>
            {/* clip the heatmap to the terminal footprint (like the 3D view) */}
            <clipPath id="terminalClip">
              {TERMINAL_CLIP_PATHS.map((d, i) => <path key={i} d={d} />)}
            </clipPath>
          </defs>
          {/* ── surrounding context from real OSM (behind the airfield) ── */}
          {/* sea backdrop — fills the viewport at any zoom */}
          <rect x={SEA_RECT.x} y={SEA_RECT.y} width={SEA_RECT.w} height={SEA_RECT.h}
                fill={SEA} />
          {/* Doha mainland (real OSM coastline, closed to a filled landmass) */}
          <path d={LAND_PATH} fill={GROUND.fill} stroke="#b8c3ce"
                strokeWidth={0.8 / view.scale} />
          {/* islands in the sea (closed coastline rings) */}
          {ISLAND_PATHS.map((d, i) => (
            <path key={i} d={d} fill={GROUND.fill} stroke="#b8c3ce"
                  strokeWidth={0.6 / view.scale} />
          ))}
          {/* inland water bodies (lagoons/marinas cut into the land) */}
          {WATER_PATHS.map((d, i) => (
            <path key={i} d={d} fill={SEA} stroke="#8fbccb" strokeWidth={0.4 / view.scale} />
          ))}
          {/* real OSM major roads, coloured by class (motorway/trunk → primary →
              secondary), each width kept constant on-screen via /view.scale */}
          {CONTEXT_ROADS_BY_CLASS.map(({ cls, paths }) => (
            <g key={cls} fill="none" stroke={ROAD_STYLE[cls].stroke}
               strokeLinecap="round" strokeLinejoin="round"
               strokeWidth={ROAD_STYLE[cls].w / view.scale}>
              {paths.map((d, i) => <path key={i} d={d} />)}
            </g>
          ))}
          {/* base airport geometry (surface polygons) */}
          {BASE_PATHS.map((p, i) => (
            <path key={i} d={p.d} fill={p.style.fill} stroke={p.style.stroke}
                  strokeWidth={0.6 / view.scale} />
          ))}
          {/* context orientation labels (DOHA / PERSIAN GULF / …) — counter-scaled
              so they stay a constant on-screen size at any zoom */}
          {CONTEXT_LABELS.map((l, i) => {
            const [x, y] = project(l.lon, l.lat);
            return (
              <text key={i} x={x.toFixed(1)} y={y.toFixed(1)}
                    fontSize={l.size / view.scale} fontWeight={700}
                    fontStyle={l.italic ? 'italic' : 'normal'}
                    fill={l.fill} textAnchor="middle"
                    style={{ letterSpacing: 0.5, pointerEvents: 'none' }}>
                {l.text}
              </text>
            );
          })}
          {/* density overlay (pax OR temperature) — soft blobs at each real
              sensor's lon/lat, exactly like the 3D HeatmapOverlay. Zone polygons
              were coarse boxes that didn't line up with the terminal; the sensors
              carry accurate positions. A Gaussian blur over the whole group melds
              the blobs into a smooth heatmap. */}
          {(radarLayers.pax || radarLayers.temp) && (() => {
            const isPax = radarLayers.pax;
            const R = 9; // blob radius in SVG units (~180 m — matches the 3D spread)
            const dotR = 3.2 / view.scale; // sensor origin dot — constant ~3px on screen
            return (
              <>
                {/* blurred heat blobs (clipped to the terminal). Blur is dropped
                    while dragging — re-rasterizing the filter each pan frame can
                    blank the SVG; it returns the moment the drag ends. */}
                <g clipPath="url(#terminalClip)" filter={dragging ? undefined : 'url(#heatBlur)'} opacity={0.8}>
                  {sensorList.map((s) => {
                    const [sx, sy] = project(s.lon, s.lat);
                    const t = isPax ? paxT(s.pax_density) : tempT(s.temperature);
                    const col = rampColor(isPax ? PAX_STOPS : TEMP_STOPS, t);
                    return <circle key={s.sensor_id} cx={sx} cy={sy} r={R} fill={col} />;
                  })}
                </g>
                {/* crisp origin dot at each sensor (NOT blurred, NOT clipped) —
                    marks where each heat area is centred. Coloured by the reading
                    with a white ring so it stands out on any heat colour. */}
                <g>
                  {sensorList.map((s) => {
                    const [sx, sy] = project(s.lon, s.lat);
                    const t = isPax ? paxT(s.pax_density) : tempT(s.temperature);
                    const col = rampColor(isPax ? PAX_STOPS : TEMP_STOPS, t);
                    return <circle key={s.sensor_id} cx={sx} cy={sy} r={dotR}
                                   fill={col} stroke="#ffffff"
                                   strokeWidth={dotR * 0.4} />;
                  })}
                </g>
              </>
            );
          })()}
          {/* surrounding roads (F-Ring Road, interchanges, terminal access) —
              real hiaRoads.json geometry, same shape-space as the airfield. Drawn
              here (behind the airfield network) so the operational HIA sits on top. */}
          <g fill="none" stroke="#b0942e" strokeOpacity={0.55} strokeLinecap="round" strokeLinejoin="round">
            {ROAD_LINES.map((r, i) => (
              <path key={i} d={r.d} strokeWidth={Math.max(r.w, 0.12)} />
            ))}
          </g>
          {/* decommissioned old Doha airport runways/taxiways to the west
              (decorative context — mirrors the 3D scene's oldAirportWays render) */}
          <g fill="none" stroke="#9aa4b0" strokeOpacity={0.5} strokeLinecap="round" strokeLinejoin="round">
            {OLD_TAXIWAY_LINES.map((d, i) => (
              <path key={i} d={d} strokeWidth={(2.3 / 3) * WAYS_K} />
            ))}
            {OLD_RUNWAY_LINES.map((d, i) => (
              <path key={i} d={d} strokeWidth={RWY_W * 0.7} stroke="#8a94a0" />
            ))}
          </g>
          {/* taxiway network — thin darker lines on the light ground.
              Width is 1/3 of the earlier size (was 2.3·K) so the gate lead-in
              fingers read as slender lines, not thick bars. */}
          <g fill="none" stroke="#8b96a4" strokeLinecap="round" strokeLinejoin="round">
            {TAXIWAY_LINES.map((d, i) => (
              <path key={i} d={d} strokeWidth={(2.3 / 3) * WAYS_K} />
            ))}
          </g>
          {/* runways — thick dark strips so they clearly read as runways */}
          {RUNWAY_LINES.map((d, i) => (
            <path key={i} d={d} fill="none" stroke="#5c6773" strokeLinecap="butt"
                  strokeWidth={RWY_W} />
          ))}
          {/* runway markings: dashed centreline + piano-key thresholds (white) */}
          {RUNWAY_MARKINGS.map((m, i) => (
            <g key={i}>
              <path d={m.centre} fill="none" stroke="#eef2f6" strokeWidth={0.22}
                    strokeDasharray="1.6 1.2" strokeLinecap="butt" opacity={0.9} />
              {m.keys.map((d, j) => (
                <path key={j} d={d} fill="#eef2f6" opacity={0.95} />
              ))}
            </g>
          ))}
          {/* runway number labels (e.g. 34R / 16L), rotated to read along the
              runway. Sized to fit within the runway width (RWY_W) — small. */}
          {RUNWAY_MARKINGS.map((m, i) =>
            m.labels.map((lb, j) => (
              <text key={`${i}-${j}`} x={0} y={0} fill="#eef2f6"
                    fontSize={RWY_W * 0.5} fontWeight={700} textAnchor="middle"
                    dominantBaseline="central"
                    transform={`translate(${lb.x.toFixed(1)},${lb.y.toFixed(1)}) rotate(${lb.rot.toFixed(1)})`}
                    style={{ letterSpacing: 0 }}>
                {lb.text}
              </text>
            ))
          )}
          {/* empty-stand gate numbers — painted on the apron (mirrors the 3D
              GateMarker). Hidden for a stand a flight is parked at / leaving, and
              zoom-gated so they don't clutter the fully-zoomed-out view. Sized in
              WORLD units (not counter-scaled), so the number scales WITH the ground
              — growing as you zoom to ground level — with a white halo so it reads on
              any ground colour. Drawn before the aircraft so a plane sits on top. */}
          {view.scale > MIN_SCALE * 2.2 && GATE_LABEL_POS.map((g) => {
            if (occupiedGates.has(g.id)) return null;
            // Grow with zoom but SUB-linearly (exponent 0.8, same as the plane
            // sprites' counter-scale) so the number scales with the ground yet
            // never balloons at ground level. On-screen size ∝ scale^0.2.
            const gs = 1 / Math.pow(view.scale, 0.8);
            return (
              <g key={g.id} transform={`translate(${g.x.toFixed(1)},${g.y.toFixed(1)}) scale(${gs.toFixed(4)})`}>
                <text x={0} y={0}
                      fontSize={8} fontWeight={700}
                      fill="#2b2417" stroke="#ffffff" strokeWidth={2.2}
                      paintOrder="stroke" strokeLinejoin="round"
                      textAnchor="middle" dominantBaseline="central"
                      style={{ pointerEvents: 'none', letterSpacing: 0 }}>
                  {g.id}
                </text>
              </g>
            );
          })}
          {/* live aircraft */}
          {posList.map((pos) => (
            <AircraftGlyph
              key={pos.flight_id}
              pos={pos}
              flight={flights[pos.flight_id]}
              scale={view.scale}
              selected={selectedEntity?.type === 'aircraft' && selectedEntity?.id === pos.flight_id}
              onSelect={(id) => setSelectedEntity(id ? { type: 'aircraft', id } : null)}
              onSpriteReady={onSpriteReady}
              showDetails={radarLayers.aircraft}
            />
          ))}
          {/* Selected-aircraft popup — drawn LAST so it sits on top of every
              plane (was previously nested in the plane's <g> and hidden by
              later-drawn aircraft). */}
          {(() => {
            const selId = selectedEntity?.type === 'aircraft' ? selectedEntity.id : null;
            if (!selId) return null;
            const sp = posList.find((p) => p.flight_id === selId);
            if (!sp) return null;
            return <SelectedAircraftPopup pos={sp} flight={flights[selId]} scale={view.scale}
                     tz={tz} tzAbbr={tzAbbr} homeIata={homeIata} />;
          })()}
        </g>
      </svg>

      {/* HUD header */}
      <div style={{
        position: 'absolute', top: 14, left: 14, display: 'flex', alignItems: 'center', gap: 10,
        background: 'rgba(10,16,28,0.82)', border: '1px solid rgba(56,189,248,0.3)', borderRadius: 8,
        padding: '8px 14px', backdropFilter: 'blur(6px)', pointerEvents: 'none',
      }}>
        <Radio size={16} color="#38bdf8" />
        <div>
          <div style={{ fontSize: 13, fontWeight: 700, color: '#e2e8f0', lineHeight: 1 }}>{airportLabel} · 2D Radar</div>
          <div style={{ fontSize: 10, color: '#94a3b8', marginTop: 2 }}>Live ground movements — Databricks realtime</div>
        </div>
      </div>

      {/* current-time ticker — centered at the top (ACTIVE ON GROUND removed per request) */}
      <div style={{
        position: 'absolute', top: 14, left: '50%', transform: 'translateX(-50%)',
        textAlign: 'center',
        background: 'rgba(10,16,28,0.82)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: 8,
        padding: '6px 18px', backdropFilter: 'blur(6px)', pointerEvents: 'none', fontFamily: 'monospace',
      }}>
        <div style={{ fontSize: 20, fontWeight: 700, color: '#e2e8f0', lineHeight: 1, letterSpacing: 1 }}>
          {clockText}<span style={{ fontSize: 11, color: '#94a3b8', marginLeft: 6 }}>{tzAbbr}</span>
        </div>
        {/* Positions come from a SEPARATE generator process via Lakebase. If it stops,
            its last rows persist and the map would silently show frozen aircraft — so
            say so explicitly rather than letting a stale map look live. */}
        {feed && !feed.alive && (
          <div title={`Last generator heartbeat: ${feed.age_seconds ?? '—'}s ago`}
               style={{
                 marginTop: 6, fontSize: 9, fontWeight: 700, letterSpacing: 0.5,
                 color: '#f87171', border: '1px solid #f8717155', borderRadius: 4,
                 padding: '2px 6px', display: 'inline-block',
               }}>
            ⚠ FEED STALE
          </div>
        )}
      </div>

      {/* live arrivals / departures board (like the 3D Home LiveGatesBoard) */}
      <FlightBoard posList={posList} flights={flights}
                   selectedId={selectedEntity?.type === 'aircraft' ? selectedEntity.id : null}
                   onSelect={(id) => setSelectedEntity(id ? { type: 'aircraft', id } : null)} />

      {/* zoom slider (2D map only) */}
      <div style={{
        position: 'absolute', bottom: 20, right: 20, display: 'flex', flexDirection: 'column',
        alignItems: 'center', gap: 8, background: 'rgba(10,16,28,0.82)',
        border: '1px solid rgba(255,255,255,0.12)', borderRadius: 10, padding: '10px 8px',
        backdropFilter: 'blur(6px)', pointerEvents: 'auto',
      }}>
        <button className="icon-btn" onClick={() => zoomTo(view.scale * 1.3)} style={ZOOM_BTN}
                title="Zoom in" aria-label="Zoom in">+</button>
        <input
          type="range" min={0} max={1000} value={scaleToSlider(view.scale, MIN_SCALE, MAX_SCALE)}
          onChange={(e) => zoomTo(sliderToScale(Number(e.target.value), MIN_SCALE, MAX_SCALE))}
          style={{
            writingMode: 'vertical-lr', direction: 'rtl', width: 6, height: 120,
            accentColor: '#38bdf8', cursor: 'pointer',
          }}
          title="Zoom"
        />
        <button className="icon-btn" onClick={() => zoomTo(view.scale / 1.3)} style={ZOOM_BTN}
                title="Zoom out" aria-label="Zoom out">−</button>
      </div>

      {/* layer switcher (bottom-left) — Aircraft details / Pax density / Temp */}
      <div style={{
        position: 'absolute', bottom: 56, left: 14, minWidth: 210,
        background: 'rgba(10,16,28,0.86)', border: '1px solid rgba(255,255,255,0.1)',
        borderRadius: 10, padding: '10px 14px', backdropFilter: 'blur(6px)', pointerEvents: 'auto',
      }}>
        <RadarToggle icon={Plane}       label="Aircraft"    on={radarLayers.aircraft} onClick={() => toggleRadar('aircraft')} />
        <RadarToggle icon={Users}       label="Pax Density" on={radarLayers.pax}      onClick={() => setDensity('pax')} />
        <RadarToggle icon={Thermometer} label="Temperature" on={radarLayers.temp}     onClick={() => setDensity('temp')} />
        {(radarLayers.pax || radarLayers.temp) && (
          <div style={{ marginTop: 6, paddingTop: 6, borderTop: '1px solid rgba(255,255,255,0.1)' }}>
            <div style={{ height: 5, borderRadius: 3,
              background: radarLayers.pax
                ? 'linear-gradient(to right,#0d47a1,#00ac82,#4caf50,#cddc39,#ffa000,#e53935,#b71c1c)'
                : 'linear-gradient(to right,#0d47a1,#1e88e5,#00ac82,#4caf50,#cddc39,#ffa000,#e53935,#b71c1c)' }} />
            <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 2 }}>
              <span style={{ fontSize: 9, color: '#94a3b8' }}>{radarLayers.pax ? '0' : '18°C'}</span>
              <span style={{ fontSize: 9, color: '#94a3b8' }}>{radarLayers.pax ? '4 pax/m²' : '40°C'}</span>
            </div>
          </div>
        )}
      </div>

      {/* clicked-coordinate readout (mirrors the 3D scene's CoordDisplay) +
          usage hint, stacked bottom-centre (bottom-left holds the layer switcher) */}
      <div style={{
        position: 'absolute', bottom: 14, left: '50%', transform: 'translateX(-50%)',
        display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6, pointerEvents: 'none',
      }}>
        <div style={{ pointerEvents: 'auto' }}>
          <CoordDisplay />
        </div>
        <div style={{
          fontSize: 10, color: '#94a3b8', background: 'rgba(10,16,28,0.7)',
          borderRadius: 6, padding: '4px 10px', pointerEvents: 'none',
        }}>
          drag to pan · scroll or slider to zoom · click a plane to select · click map for coords
        </div>
      </div>
    </div>
  );
}

// 32px visual box, but padding-free flex centring plus a 44px minimum hit area via
// the .icon-btn class (see index.css) — the skill's touch rules require 44x44 for
// tap targets, and these were 26x26, hard to hit on a touch screen or trackpad.
const ZOOM_BTN = {
  width: 32, height: 32, borderRadius: 'var(--radius-md)', cursor: 'pointer',
  background: 'rgba(56,189,248,0.12)', border: '1px solid rgba(56,189,248,0.4)',
  color: '#8fd6ff', fontSize: 17, fontWeight: 600, lineHeight: 1,
  display: 'flex', alignItems: 'center', justifyContent: 'center',
};

// One row of the 2D layer switcher — icon + label + iOS-style switch (reuses the
// shared .switch/.knob CSS the 3D LayerToggles uses).
function RadarToggle({ icon: Icon, label, on, onClick }) {
  return (
    // The whole row is now one <button role="switch">: previously only the ~34px
    // switch graphic was clickable (a bare div, so keyboard users couldn't toggle
    // layers at all and the on/off state was never announced). Making the row the
    // control also gives a comfortable 44px-tall target.
    <button
      type="button"
      role="switch"
      aria-checked={on}
      onClick={onClick}
      className="radar-toggle-row"
      style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 14,
        padding: '7px 4px', width: '100%', minHeight: 34,
        background: 'transparent', border: 'none', cursor: 'pointer', textAlign: 'left',
        borderRadius: 'var(--radius-sm)',
      }}
    >
      <span style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
        <Icon size={15} color="#93a3b8" aria-hidden="true" />
        <span style={{ color: '#e2e8f0', fontSize: 13 }}>{label}</span>
      </span>
      {/* Presentational now — the parent <button> owns the semantics, so this must
          not also be announced as a control. */}
      <span className={`switch ${on ? 'on' : ''}`} aria-hidden="true">
        <span className="knob" />
      </span>
    </button>
  );
}

// ── Live arrivals / departures board (top-right HUD) ────────────────────────────
// Mirrors the 3D Home LiveGatesBoard: two lists of the flights currently on the
// map, ARRIVING (→DOH) and DEPARTING (DOH→), each with callsign, gate and phase.
// Clicking a row selects that aircraft (same as clicking it on the map).
function FlightBoard({ posList, flights, selectedId, onSelect }) {
  const arriving = [], departing = [];
  for (const pos of posList) {
    const f = flights[pos.flight_id];
    if (!f) continue;
    const phase = pos.phase || f.phase;
    const isArr = f.destination === 'DOH';
    const schedIso = isArr ? f.scheduled_arr : f.scheduled_dep;
    (isArr ? arriving : departing).push({ ...f, phase, schedIso });
  }
  // Sort by scheduled time (soonest first); flights without a time go last.
  const byTime = (a, b) => (a.schedIso || '9999').localeCompare(b.schedIso || '9999');
  arriving.sort(byTime);
  departing.sort(byTime);

  const Row = (f) => (
    // Keyboard-selectable board row (was a bare <div onClick>). aria-pressed
    // communicates which flight is currently selected.
    <div key={f.id} role="button" tabIndex={0} aria-pressed={selectedId === f.id}
      aria-label={`${f.callsign}, gate ${f.assigned_gate || 'unassigned'}, ${_PHASE_LABEL[f.phase] || f.phase || 'unknown'}`}
      className="board-row"
      onClick={() => onSelect(selectedId === f.id ? null : f.id)}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          onSelect(selectedId === f.id ? null : f.id);
        }
      }}
      style={{
        display: 'flex', alignItems: 'center', gap: 6, padding: '4px 6px',
        borderRadius: 4, cursor: 'pointer',
        background: selectedId === f.id ? 'rgba(56,189,248,0.18)' : 'rgba(255,255,255,0.04)',
        borderLeft: `3px solid ${_PHASE_COLOR[f.phase] || '#8b9bb4'}`, marginBottom: 3,
      }}>
      <span className="mono" style={{ fontWeight: 600, fontSize: 11, color: '#e8edf4', width: 46 }}>{f.callsign}</span>
      {/* was 9.5px — under the 12px floor is bad enough without also being a
          fractional size that renders inconsistently across browsers */}
      <span className="mono" style={{ fontSize: 10, color: '#e8edf4', width: 34 }}>{_fmtSched(f.schedIso) || '—'}</span>
      <span style={{ fontSize: 10, color: '#a3b1c6', width: 30 }}>{f.assigned_gate || '—'}</span>
      <span style={{ fontSize: 9, color: _PHASE_COLOR[f.phase] || '#8b9bb4', fontWeight: 600, letterSpacing: 0.3, whiteSpace: 'nowrap', marginLeft: 'auto' }}>
        {_PHASE_LABEL[f.phase] || f.phase}
      </span>
    </div>
  );

  return (
    <div style={{
      position: 'absolute', top: 14, right: 14, width: 224, maxHeight: '62vh',
      overflowY: 'auto', background: 'rgba(10,16,28,0.86)',
      border: '1px solid rgba(255,255,255,0.1)', borderRadius: 8, padding: '10px 12px',
      backdropFilter: 'blur(6px)', pointerEvents: 'auto',
    }}>
      <div style={{ fontSize: 10, letterSpacing: 1, marginBottom: 6, color: '#4ade80' }}>
        ▼ ARRIVING · {arriving.length}
      </div>
      {arriving.length ? arriving.map(Row)
        : <div style={{ fontSize: 10, color: '#94a3b8', marginBottom: 4 }}>none on ground</div>}
      <div style={{ fontSize: 10, letterSpacing: 1, margin: '10px 0 6px', color: '#fbbf24' }}>
        ▲ DEPARTING · {departing.length}
      </div>
      {departing.length ? departing.map(Row)
        : <div style={{ fontSize: 10, color: '#94a3b8' }}>none on ground</div>}
    </div>
  );
}

// Map scale ↔ slider position on a LOG scale (so each slider step is a constant
// zoom ratio — feels natural). Slider 0..1000 spans [MIN_SCALE, MAX_SCALE].
function scaleToSlider(scale, min, max) {
  const t = Math.log(scale / min) / Math.log(max / min);
  return Math.max(0, Math.min(1000, Math.round(t * 1000)));
}
function sliderToScale(v, min, max) {
  return min * Math.pow(max / min, v / 1000);
}

// Error boundary — if anything in the radar view throws during render (e.g. a
// transient bad value mid-drag), show a small notice instead of blanking the whole
// screen, and log the error so we can see it. Recovers on the next tab visit.
class Radar2DBoundary extends Component {
  constructor(props) { super(props); this.state = { error: null }; }
  static getDerivedStateFromError(error) { return { error }; }
  componentDidCatch(error, info) { console.error('[Radar2D] render error:', error, info); }
  render() {
    if (this.state.error) {
      return (
        <div style={{
          position: 'absolute', inset: 0, display: 'flex', alignItems: 'center',
          justifyContent: 'center', flexDirection: 'column', gap: 10, background: '#0a0e17',
          color: '#e2e8f0', fontFamily: 'system-ui, sans-serif', padding: 24, textAlign: 'center',
        }}>
          <div style={{ fontSize: 15, fontWeight: 700 }}>2D Radar hit a render error</div>
          <div style={{ fontSize: 12, color: '#93a3b8', maxWidth: 520 }}>
            {String(this.state.error?.message || this.state.error)}
          </div>
          <button onClick={() => this.setState({ error: null })}
            style={{ marginTop: 8, padding: '6px 14px', borderRadius: 6, cursor: 'pointer',
              background: 'rgba(56,189,248,0.15)', border: '1px solid rgba(56,189,248,0.5)', color: '#8fd6ff' }}>
            Retry
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

export default function Radar2DPage() {
  return (
    <Radar2DBoundary>
      <Radar2DInner />
    </Radar2DBoundary>
  );
}
