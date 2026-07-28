import { useMemo } from 'react';
import * as THREE from 'three';
import { mergeGeometries } from 'three/examples/jsm/utils/BufferGeometryUtils.js';
import { lonLatToShape, CENTER_LON, CENTER_LAT, LON_SCALE, LAT_SCALE } from '../../geo';
import hiaWays  from '../../data/hiaWays.json';
import hiaRoads from '../../data/hiaRoads.json';
// Decorative-only: the old Doha International Airport's taxiway/runway pavement,
// kept out of hiaWays.json on purpose so it never enters taxiway_graph.py's
// routing graph (that file is loaded verbatim for real flight-path Dijkstra —
// see its docstring re: "single connected component, 0 disconnected islands").
import oldAirportWays from '../../data/oldAirportWays.json';
import { useSimStore } from '../../store/useSimStore';

// ── Draw-order Y stack (1 wu = 10 m; these are 5–80 cm — invisible at scale) ──
const Y = {
  ground:    0,
  water:     0.005,
  military:  0.006,
  parking:   0.007,
  road:      0.009,
  apronOut:  0.01,
  apron:     0.02,
  mosque:    0.022,
  twyOut:    0.03,
  twy:       0.04,
  twyCL:     0.05,
  rwyOut:    0.06,
  rwy:       0.07,
  rwyMark:   0.08,
};

// ── Qatar desert colour palette ───────────────────────────────────────────────
const COLORS = {
  ground:     '#c9a96e',
  sea:        '#5ac8d8',
  water:      '#1db8cc',
  parking:    '#7c7872',
  road:       '#6e6b65',
  mosque:     '#e8dfd0',
  apron:      '#b0a494',
  apronOut:   '#7a7060',
  taxiway:    '#8a8278',
  taxiOut:    '#5a5450',
  runway:     '#606068',
  rwyOut:     '#3a3840',
  centerline: '#d4a820',
  rwyMark:    '#e8e8e8',
  military:   '#a8a498',
};

// ── Ribbon geometry built in XZ plane ────────────────────────────────────────
function buildRibbon(pts, halfW, y = 0) {
  if (pts.length < 2) return null;
  const pos = [];
  const idx = [];

  for (let i = 0; i < pts.length; i++) {
    const [x, z] = pts[i];
    let dx, dz;
    if (i === 0) {
      dx = pts[1][0] - pts[0][0]; dz = pts[1][1] - pts[0][1];
    } else if (i === pts.length - 1) {
      dx = pts[i][0] - pts[i - 1][0]; dz = pts[i][1] - pts[i - 1][1];
    } else {
      dx = pts[i + 1][0] - pts[i - 1][0]; dz = pts[i + 1][1] - pts[i - 1][1];
    }
    const len = Math.sqrt(dx * dx + dz * dz) || 1;
    const px = -dz / len, pz = dx / len;

    pos.push(x - px * halfW, y, z - pz * halfW);
    pos.push(x + px * halfW, y, z + pz * halfW);

    if (i > 0) {
      const b = (i - 1) * 2;
      idx.push(b, b + 1, b + 2, b + 1, b + 3, b + 2);
    }
  }

  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
  geo.setIndex(idx);
  geo.computeVertexNormals();
  return geo;
}

// ── Apron area polygons ───────────────────────────────────────────────────────
function buildShapeGeo(ring) {
  const pts = ring.map(([lon, lat]) => lonLatToShape(lon, lat));
  const shape = new THREE.Shape();
  shape.moveTo(pts[0][0], pts[0][1]);
  for (let i = 1; i < pts.length; i++) shape.lineTo(pts[i][0], pts[i][1]);
  return new THREE.ShapeGeometry(shape);
}


function ApronAreas({ features }) {
  const surfGeo = useMemo(() => {
    const aprons = features.filter(f => f.properties?.type === 'apron');
    const surfs = [];
    for (const f of aprons) {
      const ring = f.geometry.type === 'Polygon'
        ? f.geometry.coordinates[0]
        : f.geometry.coordinates[0][0];
      surfs.push(buildShapeGeo(ring));
    }
    return surfs.length ? mergeGeometries(surfs) : null;
  }, [features]);

  return surfGeo ? (
    <mesh geometry={surfGeo} rotation={[-Math.PI / 2, 0, 0]} position={[0, Y.apron, 0]}>
      <meshStandardMaterial color={COLORS.apron} roughness={0.92} metalness={0} />
    </mesh>
  ) : null;
}

// ── OSM-sourced extra features (parking, water, mosque) ──────────────────────
function ExtraFeatures({ features }) {
  const geos = useMemo(() => {
    const water = [], parking = [], mosque = [], military = [];
    for (const f of features) {
      const t = f.properties?.type;
      if (t === 'water')    water.push(f);
      if (t === 'parking')  parking.push(f);
      if (t === 'mosque')   mosque.push(f);
      if (t === 'military') military.push(f);
    }
    function merge(feats) {
      const surfs = feats.map(f => buildShapeGeo(f.geometry.coordinates[0]));
      return surfs.length ? mergeGeometries(surfs) : null;
    }
    return { waterGeo: merge(water), parkingGeo: merge(parking), mosqueGeo: merge(mosque), militaryGeo: merge(military) };
  }, [features]);

  const rot = [-Math.PI / 2, 0, 0];
  return (
    <>
      {geos.waterGeo    && <mesh geometry={geos.waterGeo}    rotation={rot} position={[0, Y.water,    0]}><meshStandardMaterial color={COLORS.water}    roughness={0.10} metalness={0.05} /></mesh>}
      {geos.militaryGeo && <mesh geometry={geos.militaryGeo} rotation={rot} position={[0, Y.military, 0]}><meshStandardMaterial color={COLORS.military} roughness={0.90} metalness={0} /></mesh>}
      {geos.parkingGeo  && <mesh geometry={geos.parkingGeo}  rotation={rot} position={[0, Y.parking,  0]}><meshStandardMaterial color={COLORS.parking}  roughness={0.88} metalness={0} /></mesh>}
      {geos.mosqueGeo   && <mesh geometry={geos.mosqueGeo}   rotation={rot} position={[0, Y.mosque,   0]}><meshStandardMaterial color={COLORS.mosque}   roughness={0.75} metalness={0} /></mesh>}
    </>
  );
}

// ── Ribbon meshes from OSM way centerlines ────────────────────────────────────
// `ways` defaults to the live HIA network (hiaWays.json). Pass a different
// source (e.g. oldAirportWays.json) to render purely decorative pavement
// elsewhere without it ever touching the routing graph in taxiway_graph.py.
function WayRibbons({ ways = hiaWays }) {
  const {
    rwyOutGeo, rwySurfGeo, rwyMarkGeo,
    twySurfGeo, twyCLGeo,
  } = useMemo(() => {
    const taxiways = ways.filter(w => w.t === 'taxiway');
    const runways  = ways.filter(w => w.t === 'runway');

    const twySurfs = [], twyCLs = [];
    for (const tw of taxiways) {
      const hw = (tw.w || 2.3) / 2;
      const s = buildRibbon(tw.p, hw, Y.twy);
      if (s) twySurfs.push(s);
      const cl = buildRibbon(tw.p, 0.15, Y.twyCL);
      if (cl) twyCLs.push(cl);
    }

    const rwySurfs = [], rwyOuts = [], rwyMarks = [];
    for (const rw of runways) {
      const hw = (rw.w || 6.0) / 2;
      const o = buildRibbon(rw.p, hw + 0.5, Y.rwyOut);
      if (o) rwyOuts.push(o);
      const s = buildRibbon(rw.p, hw, Y.rwy);
      if (s) rwySurfs.push(s);

      // Centerline dashes + edge stripes baked at rwyMark Y
      const pts = rw.p;
      for (let i = 1; i < pts.length; i++) {
        const [x1, z1] = pts[i - 1], [x2, z2] = pts[i];
        const dx = x2 - x1, dz = z2 - z1;
        const segLen = Math.sqrt(dx * dx + dz * dz);
        if (segLen < 0.1) continue;
        const ang = Math.atan2(dx, dz);
        let t = 0;
        while (t + 6 < segLen) {
          const midT = t + 3;
          const mx = x1 + (dx / segLen) * midT, mz = z1 + (dz / segLen) * midT;
          const g = new THREE.PlaneGeometry(0.5, 6);
          g.rotateX(-Math.PI / 2); g.rotateY(ang);
          g.translate(mx, Y.rwyMark, mz);
          rwyMarks.push(g);
          t += 14;
        }
        for (const side of [-hw + 0.5, hw - 0.5]) {
          const ex  = x1 + (dz / segLen) * side, ez  = z1 - (dx / segLen) * side;
          const ex2 = x2 + (dz / segLen) * side, ez2 = z2 - (dx / segLen) * side;
          const sdx = ex2 - ex, sdz = ez2 - ez;
          const sl = Math.sqrt(sdx * sdx + sdz * sdz);
          if (sl < 0.1) continue;
          const sg = new THREE.PlaneGeometry(0.3, sl);
          sg.rotateX(-Math.PI / 2); sg.rotateY(Math.atan2(sdx, sdz));
          sg.translate((ex + ex2) / 2, Y.rwyMark, (ez + ez2) / 2);
          rwyMarks.push(sg);
        }
      }
    }

    return {
      twySurfGeo: twySurfs.length ? mergeGeometries(twySurfs) : null,
      twyCLGeo:   twyCLs.length   ? mergeGeometries(twyCLs)   : null,
      rwyOutGeo:  rwyOuts.length  ? mergeGeometries(rwyOuts)  : null,
      rwySurfGeo: rwySurfs.length ? mergeGeometries(rwySurfs) : null,
      rwyMarkGeo: rwyMarks.length ? mergeGeometries(rwyMarks) : null,
    };
  }, [ways]);

  return (
    <>
      {twySurfGeo && <mesh geometry={twySurfGeo}><meshStandardMaterial color={COLORS.taxiway}   roughness={0.9}  metalness={0} /></mesh>}
      {twyCLGeo   && <mesh geometry={twyCLGeo}>  <meshBasicMaterial   color={COLORS.centerline} /></mesh>}
      {rwyOutGeo  && <mesh geometry={rwyOutGeo}> <meshBasicMaterial   color={COLORS.rwyOut}  /></mesh>}
      {rwySurfGeo && <mesh geometry={rwySurfGeo}><meshStandardMaterial color={COLORS.runway}  roughness={0.88} metalness={0} /></mesh>}
      {rwyMarkGeo && <mesh geometry={rwyMarkGeo}><meshBasicMaterial   color={COLORS.rwyMark} /></mesh>}
    </>
  );
}

// ── OSM trunk road ribbons (F Ring Road + terminal access) ───────────────────
function RoadRibbons() {
  const { roadGeo, roadCLGeo } = useMemo(() => {
    const surfs = [], cls = [];
    for (const r of hiaRoads) {
      const s = buildRibbon(r.p, r.w, Y.road);
      if (s) surfs.push(s);
      const cl = buildRibbon(r.p, 0.05, Y.road + 0.001);
      if (cl) cls.push(cl);
    }
    return {
      roadGeo:   surfs.length ? mergeGeometries(surfs) : null,
      roadCLGeo: cls.length   ? mergeGeometries(cls)   : null,
    };
  }, []);

  return (
    <>
      {roadGeo   && <mesh geometry={roadGeo}>  <meshStandardMaterial color={COLORS.road} roughness={0.90} metalness={0} /></mesh>}
      {roadCLGeo && <mesh geometry={roadCLGeo}><meshBasicMaterial   color="#ffffff" opacity={0.35} transparent /></mesh>}
    </>
  );
}

// ── Main export ───────────────────────────────────────────────────────────────
export default function AirportGround({ features = [] }) {
  const setMapClickCoord = useSimStore((s) => s.setMapClickCoord);

  // Separate sea polygon (used for ground cutout) from other renderable features
  const seaFeature  = features.find(f => f.properties?.type === 'sea');
  const landFeatures = features.filter(f => f.properties?.type !== 'sea');

  // Land shape = 1600×1600 bounding box with the sea polygon cut out as a hole.
  // BigBox is CCW in shape space; sea polygon is CW (OSM coastline convention) → valid hole.
  const landGeo = useMemo(() => {
    const shape = new THREE.Shape();
    shape.moveTo(-500, -500);
    shape.lineTo( 500, -500);
    shape.lineTo( 500,  500);
    shape.lineTo(-500,  500);
    shape.closePath();

    if (seaFeature) {
      const ring = seaFeature.geometry.coordinates[0];
      const pts  = ring.map(([lon, lat]) => lonLatToShape(lon, lat));
      const hole = new THREE.Path();
      hole.moveTo(pts[0][0], pts[0][1]);
      for (let i = 1; i < pts.length; i++) hole.lineTo(pts[i][0], pts[i][1]);
      hole.closePath();
      shape.holes.push(hole);
    }
    return new THREE.ShapeGeometry(shape);
  }, [seaFeature]);

  function handleGroundClick(e) {
    e.stopPropagation();
    const lon = (e.point.x / LON_SCALE + CENTER_LON).toFixed(6);
    const lat = (-e.point.z / LAT_SCALE + CENTER_LAT).toFixed(6);
    setMapClickCoord({ lon, lat });
  }

  return (
    <group>
      {/* Persian Gulf — large background plane below the land shape */}
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -0.01, 0]}>
        <planeGeometry args={[3000, 3000]} />
        <meshStandardMaterial color={COLORS.sea} roughness={0.05} metalness={0.1} />
      </mesh>

      {/* Desert sand land — sea polygon cut out via hole */}
      <mesh geometry={landGeo} rotation={[-Math.PI / 2, 0, 0]} position={[0, Y.ground, 0]} receiveShadow onClick={handleGroundClick}>
        <meshStandardMaterial color={COLORS.ground} roughness={0.97} metalness={0} />
      </mesh>

      <ExtraFeatures features={landFeatures} />
      <ApronAreas    features={landFeatures} />
      <RoadRibbons />
      <WayRibbons />
      <WayRibbons ways={oldAirportWays} />
    </group>
  );
}
