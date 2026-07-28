import { useMemo } from 'react';
import * as THREE from 'three';
import { lonLatToShape } from '../../geo';

function buildShape(ring) {
  const pts = ring.map(([lon, lat]) => lonLatToShape(lon, lat));
  const shape = new THREE.Shape();
  shape.moveTo(pts[0][0], pts[0][1]);
  for (let i = 1; i < pts.length; i++) shape.lineTo(pts[i][0], pts[i][1]);
  return shape;
}

export default function TerminalBuilding({ coordinates, height }) {
  const geos = useMemo(() => {
    const ring = coordinates?.[0];
    if (!ring || ring.length < 3) return null;
    const shape = buildShape(ring);
    return {
      bodyGeo: new THREE.ExtrudeGeometry(shape, { depth: height, bevelEnabled: false }),
      roofGeo: new THREE.ExtrudeGeometry(shape, { depth: 0.8, bevelEnabled: false }),
    };
  }, [coordinates, height]);

  if (!geos) return null;

  return (
    <group rotation={[-Math.PI / 2, 0, 0]} receiveShadow castShadow>
      {/* Walls — warm sandy limestone, Qatar desert marble palette */}
      <mesh geometry={geos.bodyGeo} castShadow receiveShadow>
        <meshStandardMaterial
          color="#cfc8bc"
          roughness={0.80}
          metalness={0.03}
        />
      </mesh>
      {/* Roof — lighter cream, catches daylight like real HIA roof panels */}
      <mesh geometry={geos.roofGeo} position={[0, 0, height]} castShadow>
        <meshStandardMaterial
          color="#e8e2d6"
          roughness={0.52}
          metalness={0.05}
        />
      </mesh>
    </group>
  );
}
