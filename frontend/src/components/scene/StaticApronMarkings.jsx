import { useMemo } from 'react';
import * as THREE from 'three';
import { mergeGeometries } from 'three/examples/jsm/utils/BufferGeometryUtils.js';
import { GATE_POSITIONS, GATE_DIRS } from '../../data/gateData.js';

const DASH_D = [3, 7, 11, 15, 19, 23];

export default function StaticApronMarkings() {
  const geo = useMemo(() => {
    const geos = [];

    for (const [id, pos] of Object.entries(GATE_POSITIONS)) {
      const [gx, , gz] = pos;
      const dir = GATE_DIRS[id] ?? [0, 0, 1];
      const dx = dir[0]; const dz = dir[2];
      const isX = dx !== 0;

      for (const d of DASH_D) {
        const g = new THREE.PlaneGeometry(isX ? 2.8 : 0.18, isX ? 0.18 : 2.8);
        g.rotateX(-Math.PI / 2);
        g.translate(gx + dx * d, 0.09, gz + dz * d);
        geos.push(g);
      }

      const tb = new THREE.PlaneGeometry(isX ? 0.22 : 4.0, isX ? 4.0 : 0.22);
      tb.rotateX(-Math.PI / 2);
      tb.translate(gx, 0.09, gz);
      geos.push(tb);

      const st = new THREE.PlaneGeometry(isX ? 1.8 : 0.22, isX ? 0.22 : 1.8);
      st.rotateX(-Math.PI / 2);
      st.translate(gx - dx * 0.9, 0.09, gz - dz * 0.9);
      geos.push(st);
    }

    return mergeGeometries(geos);
  }, []);

  return (
    <mesh geometry={geo}>
      <meshBasicMaterial color="#d4a810" transparent opacity={0.75} />
    </mesh>
  );
}
