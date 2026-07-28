/**
 * Canvas-based smooth heatmap overlay for sensor point readings.
 * Two-pass algorithm: intensity blobs → LUT colorization → CanvasTexture on a flat plane.
 */
import { useRef, useEffect } from 'react';
import * as THREE from 'three';
import { useSimStore } from '../../store/useSimStore';
import { lonLatToWorld } from '../../geo';

// ── Coverage bounds in world units (includes sensor area + padding) ──────────
// S01-S15: z ~ +34..+67 (south tarmac). S16-S26: z ~ -47..-1 (north terminal).
const BOUNDS = { minX: -80, maxX: 80, minZ: -65, maxZ: 85 };
const PLANE_W = BOUNDS.maxX - BOUNDS.minX; // 160 wu
const PLANE_D = BOUNDS.maxZ - BOUNDS.minZ; // 150 wu
const PLANE_CX = (BOUNDS.minX + BOUNDS.maxX) / 2; // 0
const PLANE_CZ = (BOUNDS.minZ + BOUNDS.maxZ) / 2; // 10

const CANVAS_W = 512;
const CANVAS_H = Math.round(CANVAS_W * PLANE_D / PLANE_W); // ≈480px

// Blob radius in canvas pixels (~13 wu ≈ 130m spread per sensor point)
const BLOB_R = Math.round(CANVAS_W * 0.08);

// ── LUT builder ───────────────────────────────────────────────────────────────
function buildLUT(stops) {
  const lut = new Uint8Array(256 * 3);
  for (let i = 0; i < 256; i++) {
    const t = i / 255;
    let si = 0;
    while (si < stops.length - 2 && t > stops[si + 1][0]) si++;
    const [t0, c0] = stops[si];
    const [t1, c1] = stops[si + 1];
    const f = Math.max(0, Math.min(1, (t - t0) / (t1 - t0)));
    lut[i * 3]     = Math.round(c0[0] + f * (c1[0] - c0[0]));
    lut[i * 3 + 1] = Math.round(c0[1] + f * (c1[1] - c0[1]));
    lut[i * 3 + 2] = Math.round(c0[2] + f * (c1[2] - c0[2]));
  }
  return lut;
}

// PAX scale: 0 → 4 pax/m²  (t = v / 4)
// Synthetic data always 0-2.5 → t ≤ 0.625 → stays in green/amber band
// Full scale shown so legend is meaningful; red only appears above 3.2 pax/m²
const PAX_LUT = buildLUT([
  [0.00, [ 13,  71, 161]],  // 0.0  deep blue  (empty)
  [0.13, [  0, 172, 130]],  // 0.5  teal
  [0.25, [ 76, 175,  80]],  // 1.0  green
  [0.45, [205, 220,  57]],  // 1.8  yellow-green
  [0.63, [255, 160,   0]],  // 2.5  amber/orange  ← synthetic data max
  [0.80, [229,  57,  53]],  // 3.2  orange-red
  [1.00, [183,  28,  28]],  // 4.0  deep red
]);

// Temperature scale: 18°C → 40°C  (t = (v-18) / 22)
// Synthetic data always 18-28°C → t ≤ 0.45 → stays in blue/teal/green band
// Full scale shown so legend is meaningful; orange/red only above 32°C
const TEMP_LUT = buildLUT([
  [0.00, [ 13,  71, 161]],  // 18°C  deep blue
  [0.18, [ 30, 136, 229]],  // 22°C  blue
  [0.32, [  0, 172, 130]],  // 25°C  teal
  [0.45, [ 76, 175,  80]],  // 28°C  green  ← synthetic data max
  [0.60, [205, 220,  57]],  // 31°C  yellow-green
  [0.75, [255, 160,   0]],  // 34.5°C amber
  [0.90, [229,  57,  53]],  // 38°C  orange-red
  [1.00, [183,  28,  28]],  // 40°C  deep red
]);

// ── Direct pixel renderer (no canvas compositing) ────────────────────────────
// Maintains a Float32Array of per-pixel intensity = max(t * falloff) across all sensors.
// No canvas blending involved → pixel values are deterministic and browser-independent.
// A sensor at t=0.36 yields exactly LUT[92] (teal/green); overlapping sensors can never
// push a pixel above the highest individual sensor's value at that location.
function drawHeatmap(oCtx, W, H, points, lut) {
  const intensity = new Float32Array(W * H); // 0..1, one float per pixel

  for (const [cx, cy, t] of points) {
    const r  = BLOB_R;
    const x0 = Math.max(0, Math.floor(cx - r));
    const x1 = Math.min(W - 1, Math.ceil(cx + r));
    const y0 = Math.max(0, Math.floor(cy - r));
    const y1 = Math.min(H - 1, Math.ceil(cy + r));
    for (let py = y0; py <= y1; py++) {
      for (let px = x0; px <= x1; px++) {
        const dx = px - cx, dy = py - cy;
        const d  = Math.sqrt(dx * dx + dy * dy);
        if (d >= r) continue;
        const falloff = 1 - d / r; // linear fade — keeps colour strong across the blob
        const val = t * falloff;
        const idx = py * W + px;
        if (val > intensity[idx]) intensity[idx] = val;
      }
    }
  }

  const out = oCtx.createImageData(W, H);
  for (let i = 0; i < W * H; i++) {
    const v = intensity[i];
    if (v < 0.008) { out.data[i * 4 + 3] = 0; continue; }
    const li = Math.min(Math.round(v * 255), 255);
    out.data[i * 4]     = lut[li * 3];
    out.data[i * 4 + 1] = lut[li * 3 + 1];
    out.data[i * 4 + 2] = lut[li * 3 + 2];
    out.data[i * 4 + 3] = Math.min(Math.round(v * 700), 230);
  }
  oCtx.putImageData(out, 0, 0);
}

// ── Component ─────────────────────────────────────────────────────────────────
export default function HeatmapOverlay({ metric }) {
  const sensorReadings = useSimStore((s) => s.sensorReadings);

  // Single output canvas + texture (created once, never re-allocated)
  const oCanvasRef = useRef(null);
  const oCtxRef    = useRef(null);
  const texRef     = useRef(null);

  if (!oCanvasRef.current) {
    oCanvasRef.current = document.createElement('canvas');
    oCanvasRef.current.width  = CANVAS_W;
    oCanvasRef.current.height = CANVAS_H;
    oCtxRef.current = oCanvasRef.current.getContext('2d');
    texRef.current  = new THREE.CanvasTexture(oCanvasRef.current);
  }

  useEffect(() => {
    const sensors = Object.values(sensorReadings);
    if (!sensors.length) return;

    const isPax = metric === 'pax_density';
    const lut   = isPax ? PAX_LUT : TEMP_LUT;
    const norm  = isPax
      ? (v) => Math.min(v / 4.0, 1)                       // 0..4 pax/m²
      : (v) => Math.min(Math.max((v - 18) / 22, 0), 1);   // 18..40°C

    const points = sensors.map((s) => {
      const [wx, , wz] = lonLatToWorld(s.lon, s.lat);
      const cx = ((wx - BOUNDS.minX) / PLANE_W) * CANVAS_W;
      const cy = ((wz - BOUNDS.minZ) / PLANE_D) * CANVAS_H;
      const v  = isPax ? (s.pax_density ?? 0.5) : (s.temperature ?? 30);
      return [cx, cy, norm(v)];
    });

    drawHeatmap(oCtxRef.current, CANVAS_W, CANVAS_H, points, lut);
    texRef.current.needsUpdate = true;
  }, [sensorReadings, metric]);

  return (
    <mesh
      position={[PLANE_CX, 0.6, PLANE_CZ]}
      rotation={[-Math.PI / 2, 0, 0]}
      renderOrder={2}
    >
      <planeGeometry args={[PLANE_W, PLANE_D]} />
      <meshBasicMaterial
        map={texRef.current}
        transparent
        depthTest={false}
        depthWrite={false}
      />
    </mesh>
  );
}
