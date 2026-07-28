// Renders the SAME 3D aircraft models (a380/b773/a320 .glb) to top-down PNG
// sprites, so the 2D radar shows the exact plane shape as the 3D scene.
//
// Orientation: from Aircraft3D.jsx the model's authored orientation (Y-rot 0)
// points NORTH (−z). We capture with an orthographic camera looking straight
// down (−Y) and camera.up = −z, so north maps to the TOP of the sprite (nose
// up). The 2D map then rotates the <image> by `heading°` clockwise — identical
// to the old glyph convention. Flip SPRITE_FLIP_DEG to 180 if a model turns out
// nose-down.
import * as THREE from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { clone as skeletonClone } from 'three/examples/jsm/utils/SkeletonUtils.js';

const MODEL_URL = {
  JUMBO: '/models/a380.glb',
  WIDE: '/models/b773.glb',
  NARROW: '/models/a320.glb',
};

const SIZE = 256;             // sprite pixel resolution (square)
const SPRITE_FLIP_DEG = 0;    // set 180 if a model renders nose-down

// Shared airframe materials (mirror Aircraft3D.jsx look).
const MAT_ENGINE = new THREE.MeshStandardMaterial({ color: '#1C1C1C', roughness: 0.42, metalness: 0.65 });
const MAT_GEAR   = new THREE.MeshStandardMaterial({ color: '#252525', roughness: 0.55, metalness: 0.40 });
const MAT_GLASS  = new THREE.MeshStandardMaterial({ color: '#1a3a5c', roughness: 0.06, metalness: 0.10, transparent: true, opacity: 0.75 });
const RE_ENGINE = /engine|nacelle|fan|inlet|exhaust|pod|spinner/i;
const RE_TAIL   = /tail|fin|vert|stab|rudder|elevator|htail|vtail/i;
const RE_GLASS  = /window|glass|canopy|transp/i;
const RE_GEAR   = /gear|wheel|tire|strut|bogie/i;

function applyLivery(root, body, tail) {
  const bodyMat = new THREE.MeshStandardMaterial({ color: new THREE.Color(body), roughness: 0.22, metalness: 0.18, side: THREE.DoubleSide });
  const tailMat = new THREE.MeshStandardMaterial({ color: new THREE.Color(tail), roughness: 0.32, metalness: 0.10, side: THREE.DoubleSide });
  root.traverse((node) => {
    if (!node.isMesh) return;
    const tag = ((node.name || '') + ' ' + (node.material?.name || '')).toLowerCase();
    if      (RE_GLASS.test(tag))  node.material = MAT_GLASS;
    else if (RE_ENGINE.test(tag)) node.material = MAT_ENGINE;
    else if (RE_GEAR.test(tag))   node.material = MAT_GEAR;
    else if (RE_TAIL.test(tag))   node.material = tailMat;
    else                          node.material = bodyMat;
  });
}

// Lazily-created singletons.
let _renderer = null;
const _loader = new GLTFLoader();
const _gltfScene = {};                 // modelKey -> loaded THREE.Scene (raw)
const _spriteCache = new Map();        // key -> dataURL (or Promise while pending)

function getRenderer() {
  if (!_renderer) {
    _renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true });
    _renderer.setSize(SIZE, SIZE);
    _renderer.setPixelRatio(1);
    _renderer.setClearColor(0x000000, 0);
  }
  return _renderer;
}

function loadModel(modelKey) {
  if (_gltfScene[modelKey]) return Promise.resolve(_gltfScene[modelKey]);
  return new Promise((resolve, reject) => {
    _loader.load(
      MODEL_URL[modelKey],
      (gltf) => { _gltfScene[modelKey] = gltf.scene; resolve(gltf.scene); },
      undefined,
      reject
    );
  });
}

function renderSprite(modelKey, body, tail) {
  const model = skeletonClone(_gltfScene[modelKey]);
  applyLivery(model, body, tail);

  const scene = new THREE.Scene();
  scene.add(new THREE.HemisphereLight(0xffffff, 0x35404f, 1.15));
  const dir = new THREE.DirectionalLight(0xffffff, 1.1);
  dir.position.set(-3, 8, -2); // from the north-west above → soft form shading
  scene.add(dir);

  // Center the model at origin and fit it to the frame.
  const box = new THREE.Box3().setFromObject(model);
  const center = box.getCenter(new THREE.Vector3());
  const size = box.getSize(new THREE.Vector3());
  model.position.sub(center);            // recenter
  scene.add(model);

  // Fit the larger of the horizontal (x) / longitudinal (z) extents.
  const half = (Math.max(size.x, size.z) / 2) * 1.08;
  const cam = new THREE.OrthographicCamera(-half, half, half, -half, 0.01, 10000);
  cam.position.set(0, Math.max(size.y, 1) * 4 + 100, 0);
  cam.up.set(0, 0, -1);                  // world −z (north) → top of sprite
  cam.lookAt(0, 0, 0);

  const r = getRenderer();
  r.render(scene, cam);
  const url = r.domElement.toDataURL('image/png');

  // Free the clone's geometries/materials we created per-render.
  model.traverse((n) => { if (n.isMesh && n.geometry?.dispose) { /* geometry shared via clone; skip */ } });
  return url;
}

// Public: get a sprite data URL for (modelKey, livery). Returns the cached URL
// synchronously if ready; otherwise kicks off async generation and calls
// onReady(url) when done. `livery` = { body, tail }.
export function getPlaneSprite(modelKey, livery, onReady) {
  const key = `${modelKey}|${livery.body}|${livery.tail}`;
  const hit = _spriteCache.get(key);
  if (typeof hit === 'string') return hit;
  if (hit) return null; // pending
  const pending = loadModel(modelKey)
    .then(() => {
      const url = renderSprite(modelKey, livery.body, livery.tail);
      _spriteCache.set(key, url);
      onReady && onReady(url);
      return url;
    })
    .catch((e) => { _spriteCache.delete(key); console.warn('sprite render failed', key, e); });
  _spriteCache.set(key, pending);
  return null;
}

export { SPRITE_FLIP_DEG };
