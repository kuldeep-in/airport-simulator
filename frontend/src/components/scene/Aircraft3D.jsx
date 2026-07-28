import { useRef, useMemo, useEffect } from 'react';
import { useFrame } from '@react-three/fiber';
import * as THREE from 'three';
import { clone as skeletonClone } from 'three/examples/jsm/utils/SkeletonUtils.js';
import { useGLTF, Text, Billboard } from '@react-three/drei';
import { useSimStore } from '../../store/useSimStore';
import { lonLatToWorld, headingToRotY } from '../../geo';

// ── Per-airline livery ────────────────────────────────────────────────────────
// Each livery = { body, tail, text, textOutline }. `body`/`tail` colour the
// fuselage/tail meshes; text/outline colour the two-line callsign label so it
// stays legible against the airline's colours. Known carriers seen in the DOH
// feed get hand-picked brand colours; anything else gets a stable colour derived
// from its IATA code (same code -> same colour every time), so a Fastjet or a
// one-off charter is still visually distinct without hardcoding every airline.
const AIRLINE_LIVERY = {
  QR: { body: '#800020', tail: '#5C0632', text: '#800020', textOutline: '#2b0011' }, // Qatar Airways (default maroon)
  EY: { body: '#c8a15a', tail: '#3a2f22', text: '#c8a15a', textOutline: '#1a140a' }, // Etihad (sand/graphite)
  FZ: { body: '#e9552a', tail: '#1c2b4a', text: '#e9552a', textOutline: '#0a1122' }, // flydubai (orange/navy)
  EK: { body: '#d71a21', tail: '#c8102e', text: '#d71a21', textOutline: '#2b0006' }, // Emirates (red)
  MS: { body: '#0a4a8f', tail: '#c8a02a', text: '#4a90d9', textOutline: '#04152b' }, // EgyptAir (blue/gold)
  SV: { body: '#0a6b3b', tail: '#0a6b3b', text: '#2ea56a', textOutline: '#04220f' }, // Saudia (green)
  KU: { body: '#0a6b3b', tail: '#1c2b4a', text: '#2ea56a', textOutline: '#04220f' }, // Kuwait Airways
  GF: { body: '#b48a2e', tail: '#5a2036', text: '#d4a94a', textOutline: '#1a0c14' }, // Gulf Air (gold/maroon)
  ME: { body: '#c8102e', tail: '#0a4a8f', text: '#e05a6e', textOutline: '#2b0006' }, // Middle East Airlines
  RJ: { body: '#3a4a5a', tail: '#7a2030', text: '#8fa3b8', textOutline: '#0a1118' }, // Royal Jordanian
  WY: { body: '#7a2036', tail: '#c8a15a', text: '#c17a8c', textOutline: '#1a0810' }, // Oman Air
  '6E': { body: '#0a3a7a', tail: '#0a3a7a', text: '#5a8fd9', textOutline: '#04152b' }, // IndiGo (deep blue)
  IX: { body: '#c8102e', tail: '#e07a1a', text: '#e8734a', textOutline: '#2b0006' }, // Air India Express
  G9: { body: '#5a2a7a', tail: '#e9552a', text: '#b07ad0', textOutline: '#150a1f' }, // Air Arabia (purple)
  QP: { body: '#1c6ba0', tail: '#e9552a', text: '#5aa0d0', textOutline: '#04152b' }, // (Akasa-style teal/orange)
  XY: { body: '#0a6b3b', tail: '#c8a02a', text: '#2ea56a', textOutline: '#04220f' }, // flynas (green/gold)
  WB: { body: '#0a6b5a', tail: '#c8a02a', text: '#2ea58f', textOutline: '#04221c' }, // RwandAir
};

// Deterministic fallback colour for airlines not in the table: hash the IATA
// code to a hue, keep saturation/lightness fixed so every unknown carrier gets a
// readable, distinct, stable colour.
function hashHue(code) {
  let h = 0;
  for (let i = 0; i < code.length; i++) h = (h * 31 + code.charCodeAt(i)) >>> 0;
  return h % 360;
}
function fallbackLivery(code) {
  const hue = hashHue(code || 'XX');
  const body = `hsl(${hue}, 55%, 45%)`;
  const tail = `hsl(${hue}, 60%, 30%)`;
  return { body, tail, text: `hsl(${hue}, 70%, 60%)`, textOutline: '#05080f' };
}
function liveryFor(code) {
  return AIRLINE_LIVERY[(code || '').toUpperCase()] || fallbackLivery((code || '').toUpperCase());
}

const MODEL = {
  JUMBO:  '/models/a380.glb',
  WIDE:   '/models/b773.glb',
  NARROW: '/models/a320.glb',
};

const MODEL_CFG = {
  JUMBO:  { scale: 0.108, rotY: -Math.PI / 2, pathRotOffset: -Math.PI / 2 }, // 380 — +20%
  WIDE:   { scale: 0.096, rotY: -Math.PI / 2, pathRotOffset: -Math.PI / 2 }, // 777 — +20%
  NARROW: { scale: 0.115, rotY: -Math.PI / 2, pathRotOffset: -Math.PI / 2 }, // 320 — boosted for visibility, not true-to-scale
};

function resolveModel(aircraftType) {
  const t = (aircraftType || '').toUpperCase();
  if (/A38[08]/.test(t)) return 'JUMBO';
  if (/B77|A35[09]|A35K|B78[789]|A33[023]/.test(t)) return 'WIDE';
  return 'NARROW';
}

// Shared, airline-agnostic materials (engines/gear/glass look the same on every
// carrier).
const MAT_ENGINE = new THREE.MeshStandardMaterial({ color: '#1C1C1C', roughness: 0.42, metalness: 0.65 });
const MAT_GEAR   = new THREE.MeshStandardMaterial({ color: '#252525', roughness: 0.55, metalness: 0.40 });
const MAT_GLASS  = new THREE.MeshStandardMaterial({ color: '#1a3a5c', roughness: 0.06, metalness: 0.10, transparent: true, opacity: 0.75 });

// Body/tail materials are per-airline. Cache one material pair per livery colour
// so all of an airline's aircraft share materials (cheap) without ever mutating
// another airline's — the old single shared MAT_BODY/MAT_TAIL couldn't do this.
const _bodyTailCache = new Map();
function bodyTailMaterials(livery) {
  const key = `${livery.body}|${livery.tail}`;
  let pair = _bodyTailCache.get(key);
  if (!pair) {
    pair = {
      body: new THREE.MeshStandardMaterial({ color: new THREE.Color(livery.body), roughness: 0.22, metalness: 0.18, side: THREE.DoubleSide }),
      tail: new THREE.MeshStandardMaterial({ color: new THREE.Color(livery.tail), roughness: 0.32, metalness: 0.10, side: THREE.DoubleSide }),
    };
    _bodyTailCache.set(key, pair);
  }
  return pair;
}

const RE_ENGINE = /engine|nacelle|fan|inlet|exhaust|pod|spinner/i;
const RE_TAIL   = /tail|fin|vert|stab|rudder|elevator|htail|vtail/i;
const RE_GLASS  = /window|glass|canopy|transp/i;
const RE_GEAR   = /gear|wheel|tire|strut|bogie/i;

function applyLivery(root, livery) {
  const { body, tail } = bodyTailMaterials(livery);
  root.traverse((node) => {
    if (!node.isMesh) return;
    const tag = ((node.name || '') + ' ' + (node.material?.name || '')).toLowerCase();
    if      (RE_GLASS.test(tag))  node.material = MAT_GLASS;
    else if (RE_ENGINE.test(tag)) node.material = MAT_ENGINE;
    else if (RE_GEAR.test(tag))   node.material = MAT_GEAR;
    else if (RE_TAIL.test(tag))   node.material = tail;
    else                          node.material = body;
    node.castShadow = true;
    node.renderOrder = 10;
  });
}

Object.values(MODEL).forEach((url) => useGLTF.preload(url));

const AIRBORNE_PHASES = new Set(['AIRBORNE']);
// Stationary-at-a-stand phases — position/heading come straight from the
// server and are set once, then left alone (see the useFrame guard below).
// Anything NOT in this set falls through to the moving-object dead-reckoning
// path, which derives heading from *observed velocity between ticks* — for a
// phase where the position never changes (zero velocity), that path never
// sets rotation.y at all, leaving whatever Three.js default it started at.
// That's exactly what BOARDING hit before being added here: the backend's
// heading was computed correctly but silently never reached the model.
const STATIONARY_PHASES = new Set(['PARKED', 'GATE_PARKED', 'BOARDING']);

const PHASE_LABEL = {
  ARRIVING:    'ON GROUND',
  PARKED:      'AT GATE',
  GATE_PARKED: 'AT GATE',
  BOARDING:    'BOARDING',
};
const PHASE_COLOR = {
  ARRIVING:    '#4ade80',
  BOARDING:    '#fbbf24',
  PARKED:      '#38bdf8',
  GATE_PARKED: '#38bdf8',
};

// Popup that floats above a selected aircraft, built from scene-native
// primitives (same as the callsign-at-tail label) inside a <Billboard> so it
// always faces the camera. Rendered via <Html> previously, which broke click
// handling — this stays in the WebGL layer and never touches the DOM.
// Line 1: flight number + (aircraft type) + an airline roundel (the app has no
// logo image assets, so the "company icon" is a livery-coloured disc with the
// IATA code — same convention the plane's own colours use).
// Line 2: origin → destination.
function AircraftPopup({ flight, livery }) {
  const callsign = flight?.callsign ?? flight?.id ?? '';
  const type = flight?.aircraft_type ?? '';
  const airline = (flight?.airline || '').toUpperCase();
  const origin = flight?.origin || '—';
  const dest = flight?.destination || '—';
  const line1 = type ? `${callsign}  (${type})` : callsign;
  const line2 = `${origin}  →  ${dest}`;

  return (
    <Billboard position={[0, 22, 0]} scale={4}>
      {/* card background */}
      <mesh position={[0, 0, -0.02]} renderOrder={20}>
        <planeGeometry args={[9, 3.4]} />
        <meshBasicMaterial color="#0a101c" transparent opacity={0.92} depthTest={false} depthWrite={false} />
      </mesh>
      {/* thin accent border (slightly larger plane behind) */}
      <mesh position={[0, 0, -0.03]} renderOrder={19}>
        <planeGeometry args={[9.3, 3.7]} />
        <meshBasicMaterial color={livery.text} transparent opacity={0.9} depthTest={false} depthWrite={false} />
      </mesh>
      {/* airline roundel (livery-coloured disc) */}
      <mesh position={[-3.5, 0.7, 0.01]} renderOrder={21}>
        <circleGeometry args={[0.85, 32]} />
        <meshBasicMaterial color={livery.body} depthTest={false} depthWrite={false} />
      </mesh>
      <Text position={[-3.5, 0.7, 0.02]} fontSize={0.62} fontWeight={700} color="#ffffff"
            anchorX="center" anchorY="middle" renderOrder={22} depthOffset={-10}>
        {airline || '✈'}
      </Text>
      {/* line 1: flight number (type) */}
      <Text position={[-2.3, 0.7, 0.02]} fontSize={0.9} fontWeight={700} color="#f1f5f9"
            anchorX="left" anchorY="middle" renderOrder={22} depthOffset={-10}
            outlineWidth={0.02} outlineColor="#050a14">
        {line1}
      </Text>
      {/* line 2: origin -> destination */}
      <Text position={[-3.5, -0.85, 0.02]} fontSize={0.85} fontWeight={400} color={livery.text}
            anchorX="left" anchorY="middle" renderOrder={22} depthOffset={-10}
            outlineWidth={0.02} outlineColor="#050a14">
        {line2}
      </Text>
    </Billboard>
  );
}

export default function Aircraft3D({ flight, position }) {
  const groupRef  = useRef();
  const smoothPos = useRef(null);

  // Dead-reckoning state (backend position mode)
  const prevPosKey        = useRef(null);
  const prevServerWorld   = useRef(null);
  const currServerWorld   = useRef(null);
  const serverVelocity    = useRef(new THREE.Vector3(0, 0, 0));
  const lastServerTime    = useRef(null);
  const lastServerInterval = useRef(1);
  const parkedRef         = useRef(false);

  const setSelectedEntity = useSimStore((s) => s.setSelectedEntity);
  const layers            = useSimStore((s) => s.layers);
  const selectedEntity    = useSimStore((s) => s.selectedEntity);
  const isSelected = selectedEntity?.type === 'aircraft' && selectedEntity?.id === flight?.id;

  // Safety net: don't leave the cursor stuck as 'pointer' if this aircraft
  // unmounts (e.g. it goes AIRBORNE and disappears) while still hovered.
  useEffect(() => () => { document.body.style.cursor = 'auto'; }, []);

  const modelKey = resolveModel(flight?.aircraft_type);
  const { scale, rotY: modelRotY, pathRotOffset } = MODEL_CFG[modelKey];

  const livery = useMemo(() => liveryFor(flight?.airline), [flight?.airline]);

  const { scene } = useGLTF(MODEL[modelKey]);
  const aircraftScene = useMemo(() => {
    const cloned = skeletonClone(scene);
    applyLivery(cloned, livery);
    return cloned;
  }, [scene, livery]);

  useFrame(() => {
    if (!groupRef.current) return;
    if (!position) return;

    // Full stop while stationary at a stand (PARKED after taxi-in, or
    // BOARDING before pushback): sync exactly once to the server's position
    // and heading, then never touch position or rotation again — no
    // re-snapping to a separately looked-up gate coordinate/orientation, and
    // no falling through to the moving-object path below (which derives
    // heading from velocity between ticks and would never set it at all for
    // something that never moves — see STATIONARY_PHASES above).
    if (STATIONARY_PHASES.has(position.phase)) {
      if (!parkedRef.current) {
        const [px, , pz] = lonLatToWorld(position.lon, position.lat);
        groupRef.current.position.set(px, 0.12, pz);
        groupRef.current.rotation.y = headingToRotY(position.heading || 0) + pathRotOffset;
        parkedRef.current = true;
      }
      return;
    }
    parkedRef.current = false;

    const now        = performance.now() / 1000;
    const posKey     = `${position.lat},${position.lon}`;
    const isAirborne = AIRBORNE_PHASES.has(position.phase);
    const [tx, , tz] = lonLatToWorld(position.lon, position.lat);
    const ty         = isAirborne ? 30 : 0.12;

    if (posKey !== prevPosKey.current) {
      const newWorld = new THREE.Vector3(tx, ty, tz);

      if (currServerWorld.current) {
        const jumpDist = newWorld.distanceTo(currServerWorld.current);
        if (jumpDist > 50) {
          // Large jump (cycle restart) — snap cleanly, no velocity/interpolation artefact
          smoothPos.current       = newWorld.clone();
          prevServerWorld.current = newWorld.clone();
          currServerWorld.current = newWorld;
          serverVelocity.current.set(0, 0, 0);
          lastServerTime.current  = now;
          lastServerInterval.current = 1;
          prevPosKey.current      = posKey;
          groupRef.current.position.copy(smoothPos.current);
          return;
        }
        const dt = Math.max(now - lastServerTime.current, 0.05);
        serverVelocity.current
          .subVectors(newWorld, currServerWorld.current)
          .divideScalar(dt);
        if (serverVelocity.current.length() > 400) serverVelocity.current.setLength(400);
        // Ground motion interpolates from the previous confirmed point to this
        // one over roughly the same real-time gap as the last update — this
        // reconstructs the seed data's per-second deceleration curve smoothly
        // instead of snapping-then-freezing every tick.
        prevServerWorld.current    = currServerWorld.current.clone();
        lastServerInterval.current = Math.min(Math.max(dt, 0.2), 3);
      } else {
        prevServerWorld.current = newWorld.clone();
      }

      currServerWorld.current = newWorld;
      lastServerTime.current  = now;
      prevPosKey.current      = posKey;

      if (!smoothPos.current) {
        smoothPos.current = newWorld.clone();
        groupRef.current.position.copy(smoothPos.current);
        return;
      }
    }

    if (!smoothPos.current || !currServerWorld.current) return;

    if (isAirborne) {
      // Airborne: dead reckoning with extrapolation for smooth fast motion
      const elapsed      = now - (lastServerTime.current ?? now);
      const extrapolated = currServerWorld.current.clone()
        .addScaledVector(serverVelocity.current, elapsed);
      smoothPos.current.lerp(extrapolated, 0.07);
    } else {
      // Ground (ARRIVING): linear interpolation between the last two
      // confirmed server positions over the observed update interval. Exact
      // reconstruction of the true per-second motion (never overshoots past
      // the latest confirmed point), so it can't cut through building corners.
      const frac = Math.min(
        (now - lastServerTime.current) / lastServerInterval.current,
        1,
      );
      smoothPos.current.copy(prevServerWorld.current).lerp(currServerWorld.current, frac);
    }
    groupRef.current.position.copy(smoothPos.current);

    // Heading follows direction of travel while moving (PARKED already
    // returned above, so this only ever runs pre-parking).
    const vx = serverVelocity.current.x;
    const vz = serverVelocity.current.z;
    if (Math.sqrt(vx * vx + vz * vz) > 0.01) {
      groupRef.current.rotation.y = Math.atan2(vx, vz) + pathRotOffset;
    }
  });

  if (!layers.aircraft) return null;
  if (!position?.lat || !position?.lon) return null;

  const callsign   = flight?.callsign ?? '';
  // Split the callsign onto two lines: airline prefix on top, flight number
  // below (e.g. "QR151" -> "QR" / "151"). Prefer the known airline IATA code;
  // fall back to splitting where letters meet digits.
  const airlineCode = (flight?.airline || '').toUpperCase();
  const twoLineCallsign = (() => {
    if (!callsign) return '';
    if (airlineCode && callsign.toUpperCase().startsWith(airlineCode)) {
      return `${airlineCode}\n${callsign.slice(airlineCode.length)}`;
    }
    const m = callsign.match(/^([A-Za-z]+)(\d.*)$/);
    return m ? `${m[1]}\n${m[2]}` : callsign;
  })();
  const phase      = position?.phase ?? null;
  const phaseLabel = PHASE_LABEL[phase] ?? null;
  const phaseColor = PHASE_COLOR[phase] ?? '#e2e8f0';

  return (
    <group
      ref={groupRef}
      onClick={(e) => {
        e.stopPropagation();
        // Toggle: clicking the selected plane again clears the popup.
        setSelectedEntity(isSelected ? null : { type: 'aircraft', id: flight.id });
      }}
      onPointerOver={(e) => { e.stopPropagation(); document.body.style.cursor = 'pointer'; }}
      onPointerOut={(e) => { e.stopPropagation(); document.body.style.cursor = 'auto'; }}
    >
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.02, 0]} scale={[3.8, 2.4, 1]} renderOrder={10}>
        <circleGeometry args={[1, 24]} />
        <meshBasicMaterial color={livery.tail} transparent opacity={0.18} depthWrite={false} />
      </mesh>

      <group scale={[scale, scale, scale]} rotation={[0, modelRotY, 0]}>
        <primitive object={aircraftScene} />
      </group>

      {/* Callsign — positioned clear of the airframe entirely, past the
          tail (local -X is the back — see reference screenshot confirming
          direction), not just behind the tail fin but outside the halo's
          footprint (halo's X semi-axis is 3.8, see the mesh below) so it
          never overlaps the model. Sized for the biggest aircraft (A380)
          and kept that size for every model — the model mesh itself is
          what's scaled per-type, not this text — so the callsign reads
          consistently large regardless of plane size. Only shown while
          still moving — once parked there's no need to keep labeling it. */}
      {phase !== 'PARKED' && (
        <Text
          position={[-6.5, 2.0, 0]}
          rotation={[-Math.PI / 2, 0, Math.PI / 2]}
          fontSize={2.4}
          lineHeight={1.0}
          fontWeight={400}
          color={livery.text}
          anchorX="center"
          anchorY="middle"
          textAlign="center"
          outlineWidth={0.04}
          outlineColor={livery.textOutline}
          depthOffset={-6}
          renderOrder={10}
        >
          {twoLineCallsign}
        </Text>
      )}

      {/* Live phase status — only for Lakebase-driven aircraft */}
      {phaseLabel && (
        <Text
          position={[0, 1.4, 0]}
          rotation={[-Math.PI / 2, 0, Math.PI / 2]}
          fontSize={0.22}
          fontWeight={700}
          color={phaseColor}
          anchorX="center"
          anchorY="middle"
          outlineWidth={0.018}
          outlineColor="#050a14"
          depthOffset={-6}
        >
          {phaseLabel}
        </Text>
      )}

      {/* Click popup — attached above the plane (replaces the old top-left
          DetailPanel for aircraft), built from scene primitives in a Billboard
          so it always faces the camera. */}
      {isSelected && <AircraftPopup flight={flight} livery={livery} />}
    </group>
  );
}
