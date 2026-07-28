import { useMemo } from 'react';
import * as THREE from 'three';
import { Text } from '@react-three/drei';
import { mergeGeometries } from 'three/examples/jsm/utils/BufferGeometryUtils.js';
import hiaWays from '../../data/hiaWays.json';

// Draw at a Y just above the existing rwyMark layer (0.08)
const Y = 0.082;

// HIA has two parallel runways, both oriented ≈ 158 ° / 338 °.
// Runway 0 in hiaWays is the WESTERN one → 16R (NW approach) / 34L (SE approach)
// Runway 1 in hiaWays is the EASTERN one → 16L (NW approach) / 34R (SE approach)
const RWY_IDS = [
  { startLabel: '16R', endLabel: '34L' },
  { startLabel: '16L', endLabel: '34R' },
];

// ── Geometry helpers ──────────────────────────────────────────────────────────

function plane(w, h, ang, cx, cz) {
  const g = new THREE.PlaneGeometry(w, h);
  g.rotateX(-Math.PI / 2);
  g.rotateY(ang);
  g.translate(cx, Y, cz);
  return g;
}

// Piano-key threshold markings + threshold crossbar at each end of a runway
function buildThreshold(pts, hw, ang) {
  const STRIPE_L   = hw - 1.1;   // length of each stripe (across runway, per side)
  const STRIPE_W   = 0.45;       // width of each stripe (along runway)
  const GAP        = 0.42;       // gap between stripes
  const N_STRIPES  = 6;
  const FIRST_DIST = 3.0;        // distance from threshold to first stripe
  const CL_HALF    = 0.9;        // half the gap around the centreline

  // Perpendicular unit vector in XZ plane (across runway)
  const perpX = Math.cos(ang), perpZ = -Math.sin(ang);

  const geoms = [];

  for (const isEnd of [false, true]) {
    const [ex, ez] = isEnd ? pts[pts.length - 1] : pts[0];
    // Direction pointing into the runway from this threshold
    const inX = isEnd ? -Math.sin(ang) : Math.sin(ang);
    const inZ = isEnd ? -Math.cos(ang) : Math.cos(ang);

    // Full-width crossbar right at the threshold line
    geoms.push(plane(hw * 2 - 0.4, 0.55, ang, ex, ez));

    // Piano-key stripes (pairs each side of CL)
    for (let i = 0; i < N_STRIPES; i++) {
      const d  = FIRST_DIST + i * (STRIPE_W + GAP);
      const cx = ex + inX * d;
      const cz = ez + inZ * d;
      for (const side of [-1, 1]) {
        const offset = side * (CL_HALF + STRIPE_L / 2);
        geoms.push(plane(STRIPE_L, STRIPE_W, ang, cx + perpX * offset, cz + perpZ * offset));
      }
    }
  }
  return geoms;
}

// Touchdown zone bars: three pairs each side, offset from threshold
function buildTDZ(pts, hw, ang) {
  const BAR_L  = hw * 0.62;   // bar length across runway
  const BAR_W  = 0.5;         // bar width along runway
  const DISTS  = [14, 21, 28]; // wu from threshold (≈ 140, 210, 280 m)
  const OFFSET = hw * 0.3;    // distance from CL to bar centre (per side)

  const perpX = Math.cos(ang), perpZ = -Math.sin(ang);
  const geoms = [];

  for (const isEnd of [false, true]) {
    const [ex, ez] = isEnd ? pts[pts.length - 1] : pts[0];
    const inX = isEnd ? -Math.sin(ang) : Math.sin(ang);
    const inZ = isEnd ? -Math.cos(ang) : Math.cos(ang);

    for (const d of DISTS) {
      const cx = ex + inX * d, cz = ez + inZ * d;
      for (const side of [-1, 1]) {
        const offset = side * (OFFSET + BAR_L / 2);
        geoms.push(plane(BAR_L, BAR_W, ang, cx + perpX * offset, cz + perpZ * offset));
      }
    }
  }
  return geoms;
}

// Aiming point markings: two large rectangular blocks on each side, ~300 m from threshold
function buildAimingPoint(pts, hw, ang) {
  const BLOCK_L = hw * 0.75;
  const BLOCK_W = 4.5;           // elongated block along runway
  const DIST    = 32;             // wu from threshold ≈ 320 m
  const OFFSET  = hw * 0.25;

  const perpX = Math.cos(ang), perpZ = -Math.sin(ang);
  const geoms = [];

  for (const isEnd of [false, true]) {
    const [ex, ez] = isEnd ? pts[pts.length - 1] : pts[0];
    const inX = isEnd ? -Math.sin(ang) : Math.sin(ang);
    const inZ = isEnd ? -Math.cos(ang) : Math.cos(ang);
    const cx  = ex + inX * DIST, cz = ez + inZ * DIST;

    for (const side of [-1, 1]) {
      const offset = side * (OFFSET + BLOCK_L / 2);
      geoms.push(plane(BLOCK_L, BLOCK_W, ang, cx + perpX * offset, cz + perpZ * offset));
    }
  }
  return geoms;
}

// ── Main component ────────────────────────────────────────────────────────────
export default function RunwayMarkings() {
  const { markingGeo, labels } = useMemo(() => {
    const runways = hiaWays.filter(w => w.t === 'runway');
    const allGeoms  = [];
    const allLabels = [];

    runways.forEach((rw, idx) => {
      const pts = rw.p;
      const hw  = (rw.w ?? 6) / 2;
      const [x0, z0] = pts[0];
      const [xN, zN] = pts[pts.length - 1];
      const ang = Math.atan2(xN - x0, zN - z0);

      allGeoms.push(
        ...buildThreshold(pts, hw, ang),
        ...buildTDZ(pts, hw, ang),
        ...buildAimingPoint(pts, hw, ang),
      );

      // Runway number labels — placed TEXT_DIST wu inward from each threshold
      const TEXT_DIST = 10;
      const sinA = Math.sin(ang), cosA = Math.cos(ang);
      const { startLabel, endLabel } = RWY_IDS[idx] ?? { startLabel: '??', endLabel: '??' };

      // NW threshold: "16R" or "16L"
      allLabels.push({
        id:   `rwy-${idx}-start`,
        x:     x0 + sinA * TEXT_DIST,
        z:     z0 + cosA * TEXT_DIST,
        rotY:  ang + Math.PI, // top of digits faces into runway (SE)
        text:  startLabel,
      });

      // SE threshold: "34L" or "34R"
      allLabels.push({
        id:   `rwy-${idx}-end`,
        x:     xN - sinA * TEXT_DIST,
        z:     zN - cosA * TEXT_DIST,
        rotY:  ang,           // top of digits faces into runway (NW)
        text:  endLabel,
      });
    });

    return {
      markingGeo: allGeoms.length ? mergeGeometries(allGeoms) : null,
      labels:     allLabels,
    };
  }, []);

  return (
    <group>
      {/* All white surface markings in one draw call */}
      {markingGeo && (
        <mesh geometry={markingGeo}>
          <meshBasicMaterial color="#e0e0e0" />
        </mesh>
      )}

      {/* Runway number labels */}
      {labels.map(({ id, x, z, rotY, text }) => (
        // Outer group: Y-rotation only — keeps text flat in the XZ ground plane
        <group key={id} position={[x, Y + 0.005, z]} rotation={[0, rotY, 0]}>
          <Text
            rotation={[-Math.PI / 2, 0, 0]}
            fontSize={3.2}
            fontWeight={900}
            color="#e2e2e2"
            anchorX="center"
            anchorY="middle"
            depthOffset={-4}
          >
            {text}
          </Text>
        </group>
      ))}
    </group>
  );
}
