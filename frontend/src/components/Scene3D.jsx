import { Suspense, useRef, useState, useCallback } from 'react';
import { Canvas, useFrame } from '@react-three/fiber';
import { MapControls } from '@react-three/drei';
import { Spherical, Vector3 } from 'three';
import Airport3D from './scene/Airport3D';
import SimControls from './panels/SimControls';
import LiveGatesBoard from './panels/LiveGatesBoard';
import LayerToggles from './panels/LayerToggles';
import DetailPanel from './panels/DetailPanel';
import CoordDisplay from './panels/CoordDisplay';
import GateEditor from './panels/GateEditor';

const BOUNDS = { minX: -200, maxX: 200, minZ: -180, maxZ: 150 };
// Camera tilt (polar angle from straight down). Shared by MapControls' limits and the
// tilt buttons, so mouse and buttons agree on the range.
const TILT_MAX = Math.PI * 0.42;          // ~75° — stops short of the horizon
const TILT_3D = Math.PI * 0.3;            // the "3D" preset (~54°)
const TILT_STEP = Math.PI / 12;           // 15° per tilt-button press

// Shows a lat/lon label once the user clicks the ground/apron (see
// AirportGround.jsx's onClick -> setMapClickCoord).
const SHOW_COORDS = true;

function PanLimiter({ controlsRef }) {
  useFrame(() => {
    const controls = controlsRef.current;
    if (!controls) return;
    const t = controls.target;
    const cx = Math.max(BOUNDS.minX, Math.min(BOUNDS.maxX, t.x));
    const cz = Math.max(BOUNDS.minZ, Math.min(BOUNDS.maxZ, t.z));
    const dx = cx - t.x;
    const dz = cz - t.z;
    if (dx !== 0 || dz !== 0) {
      // Shift the camera by the same delta as the target so the
      // position-target offset (and therefore the view direction
      // MapControls derives from it) never changes — clamping only
      // the target here desynced it from camera.position, which made
      // MapControls recompute a distorted azimuth/tilt on the next
      // drag, visible as the view spinning whenever you hit an edge.
      t.x = cx;
      t.z = cz;
      controls.object.position.x += dx;
      controls.object.position.z += dz;
      controls.update();
    }
  });
  return null;
}

function AzimuthSync({ controlsRef, onAzimuth, onTilt }) {
  const last = useRef(0);
  const lastTilt = useRef(0);
  useFrame(() => {
    if (!controlsRef.current) return;
    const a = controlsRef.current.getAzimuthalAngle();
    if (Math.abs(a - last.current) > 0.002) {
      last.current = a;
      onAzimuth(a);
    }
    const p = controlsRef.current.getPolarAngle();
    if (onTilt && Math.abs(p - lastTilt.current) > 0.01) {
      lastTilt.current = p;
      onTilt(p);
    }
  });
  return null;
}

// ── North compass with rotate buttons ────────────────────────────────────────
const BTN = {
  width: 28,
  height: 28,
  borderRadius: 14,
  background: 'rgba(8,14,28,0.82)',
  border: '1.5px solid rgba(255,160,64,0.4)',
  color: '#e2e8f0',
  fontSize: 16,
  cursor: 'pointer',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  userSelect: 'none',
  lineHeight: 1,
};

function NorthCompass({ azimuth, tilt, onRotateLeft, onRotateRight, onTilt, onToggleView }) {
  const deg = -(azimuth * 180) / Math.PI;
  return (
    <div style={{
      position: 'absolute',
      bottom: 56,  // above CoordDisplay
      right: 16,
      display: 'flex',
      alignItems: 'center',
      gap: 6,
      zIndex: 20,
    }}>
      <button style={BTN} onClick={onRotateLeft} title="Rotate left 45°">↺</button>

      <div style={{ width: 48, height: 48, userSelect: 'none', pointerEvents: 'none' }}>
        <svg viewBox="0 0 48 48" width="48" height="48"
          style={{ transform: `rotate(${deg}deg)`, transition: 'transform 0.08s linear' }}>
          <circle cx="24" cy="24" r="22"
            fill="rgba(8,14,28,0.85)"
            stroke="rgba(255,160,64,0.40)"
            strokeWidth="1.5" />
          <polygon points="24,5 27,23 24,20 21,23" fill="#ef4444" />
          <polygon points="24,43 27,25 24,28 21,25" fill="#64748b" />
          <circle cx="24" cy="24" r="2.5" fill="#e2e8f0" />
          <text x="24" y="13" textAnchor="middle" fontSize="7" fontWeight="bold"
            fill="#ef4444" fontFamily="system-ui,sans-serif">N</text>
        </svg>
      </div>

      <button style={BTN} onClick={onRotateRight} title="Rotate right 45°">↻</button>

      {/* Tilt: buttons for trackpads (mouse: right-drag, or Shift/⌘ + drag) */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4, marginLeft: 4 }}>
        <button style={BTN} onClick={() => onTilt(TILT_STEP)} title="Tilt more (towards the horizon)">⤒</button>
        <button style={BTN} onClick={() => onTilt(-TILT_STEP)} title="Tilt less (towards top-down)">⤓</button>
      </div>
      <button style={{ ...BTN, width: 'auto', padding: '0 10px', fontSize: 11, fontWeight: 700, letterSpacing: '0.06em' }}
              onClick={onToggleView} title="Switch between top-down and 3D view">
        {tilt > 0.05 ? 'TOP' : '3D'}
      </button>
    </div>
  );
}

// ── Main scene ────────────────────────────────────────────────────────────────
export default function Scene3D() {
  const controlsRef = useRef();
  const [azimuth, setAzimuth] = useState(0);
  const [tilt, setTilt] = useState(0);
  const [showGateEditor, setShowGateEditor] = useState(false);

  const rotateMap = useCallback((delta) => {
    const controls = controlsRef.current;
    if (!controls) return;
    const cam = controls.object;
    const tgt = controls.target;
    const dx = cam.position.x - tgt.x;
    const dz = cam.position.z - tgt.z;
    const c = Math.cos(delta), s = Math.sin(delta);
    cam.position.x = tgt.x + dx * c - dz * s;
    cam.position.z = tgt.z + dx * s + dz * c;
    controls.update();
  }, []);

  // Tilt the camera around the target (keeps azimuth and distance), clamped to the
  // same range MapControls enforces. `absolute` jumps straight to a polar angle.
  const tiltMap = useCallback((delta, absolute = null) => {
    const controls = controlsRef.current;
    if (!controls) return;
    const cam = controls.object;
    const off = new Vector3().subVectors(cam.position, controls.target);
    const sph = new Spherical().setFromVector3(off);
    sph.phi = Math.max(0.001, Math.min(TILT_MAX, absolute ?? sph.phi + delta));
    off.setFromSpherical(sph);
    cam.position.copy(controls.target).add(off);
    controls.update();
  }, []);

  return (
    <div style={{ width: '100%', height: '100%', position: 'relative' }}>
      <Canvas
        orthographic
        camera={{ position: [0, 200, 1], zoom: 2.4, near: 0.1, far: 1000 }}
        gl={{ antialias: true, logarithmicDepthBuffer: true }}
        style={{ background: '#9cb8d0', width: '100%', height: '100%' }}
      >
        <ambientLight intensity={2.2} color="#dce8f8" />
        <directionalLight
          position={[-80, 220, 100]} intensity={2.2} color="#fff8f0"
          castShadow shadow-mapSize={[1024, 1024]}
          shadow-camera-far={900} shadow-camera-left={-200}
          shadow-camera-right={200} shadow-camera-top={200} shadow-camera-bottom={-200}
        />
        <directionalLight position={[120, 100, -50]} intensity={0.6} color="#e8f0ff" />

        <Suspense fallback={null}>
          <Airport3D />
        </Suspense>

        <MapControls
          ref={controlsRef}
          target={[0, 0, 0]}
          minZoom={0.5}
          maxZoom={30}
          // Tilt is free between straight-down and ~75° (right-drag / two-finger drag);
          // azimuth rotates freely and with the compass ↺/↻ buttons. Was pinned to
          // 0.001 rad (top-down only), which locked the view direction.
          minPolarAngle={0}
          maxPolarAngle={TILT_MAX}
          dampingFactor={0.08}
          enableDamping
          screenSpacePanning={false}
        />
        <PanLimiter controlsRef={controlsRef} />
        <AzimuthSync controlsRef={controlsRef} onAzimuth={setAzimuth} onTilt={setTilt} />
      </Canvas>

      {/* ── HUD overlays ── */}
      <div style={{ position: 'absolute', top: 0, left: 0, right: 0, pointerEvents: 'none' }}>
        <div style={{ pointerEvents: 'auto' }}><SimControls /></div>
      </div>
      <div style={{ position: 'absolute', top: 60, right: 72, pointerEvents: 'auto', zIndex: 10 }}>
        <LiveGatesBoard />
      </div>
      {/* Layer toggles sit directly above the north compass, bottom-right */}
      <div style={{ position: 'absolute', bottom: 112, right: 16, pointerEvents: 'auto', zIndex: 10 }}>
        <LayerToggles />
      </div>
      <div style={{ position: 'absolute', top: 60, left: 16, pointerEvents: 'auto', zIndex: 10 }}>
        <DetailPanel />
      </div>
      {SHOW_COORDS && (
        <div style={{ position: 'absolute', bottom: 16, left: 16, pointerEvents: 'none', zIndex: 10 }}>
          <CoordDisplay />
        </div>
      )}

      {/* Gate editor toggle */}
      <div style={{ position: 'absolute', bottom: 16, right: 16, pointerEvents: 'auto', zIndex: 30 }}>
        <button
          onClick={() => setShowGateEditor((v) => !v)}
          style={{
            background: showGateEditor ? 'rgba(255,160,64,0.2)' : 'rgba(8,14,28,0.82)',
            border: `1.5px solid ${showGateEditor ? 'rgba(255,160,64,0.7)' : 'rgba(255,160,64,0.35)'}`,
            borderRadius: 6,
            color: showGateEditor ? '#ffa040' : '#94a3b8',
            cursor: 'pointer',
            fontFamily: 'monospace',
            fontSize: 11,
            fontWeight: showGateEditor ? 700 : 400,
            padding: '5px 10px',
            backdropFilter: 'blur(6px)',
          }}
          title="Toggle gate label editor"
        >
          ⚙ Gate Editor
        </button>
      </div>

      {/* Gate editor panel */}
      {showGateEditor && (
        <div style={{
          position: 'absolute',
          top: 60,
          right: 16,
          pointerEvents: 'auto',
          zIndex: 25,
        }}>
          <GateEditor onClose={() => setShowGateEditor(false)} />
        </div>
      )}

      <NorthCompass
        azimuth={azimuth}
        onRotateLeft={() => rotateMap(Math.PI / 4)}
        onRotateRight={() => rotateMap(-Math.PI / 4)}
        tilt={tilt}
        onTilt={(d) => tiltMap(d)}
        onToggleView={() => tiltMap(0, tilt > 0.05 ? 0.001 : TILT_3D)}
      />
      <div style={{ position: 'absolute', bottom: 16, left: '50%', transform: 'translateX(-50%)', zIndex: 20, fontSize: 10.5, color: '#94a3b8',
                    background: 'rgba(8,14,28,0.7)', padding: '3px 8px', borderRadius: 6, pointerEvents: 'none' }}>
        drag = pan · right-drag or Shift/⌘ + drag = rotate &amp; tilt · scroll = zoom
      </div>
    </div>
  );
}
