function getDir(gateId) {
  const letter = gateId[0];
  const num = parseInt(gateId.slice(1));
  if (letter === 'A' || letter === 'C') return [-1, 0, 0];
  if (letter === 'B') return num <= 7 ? [0, 0, -1] : [0, 0, 1];
  if (letter === 'D') return [0, 0, -1];
  if (letter === 'E') return [-1, 0, 0];
  return [-1, 0, 0];
}

export default function Airbridge({ gateId, gatePosition }) {
  const [gx, , gz] = gatePosition;
  const [dx, , dz] = getDir(gateId);

  const LENGTH = 9;
  const ELEV = 3.4;
  const W = 1.9;
  const H = 1.5;

  const mx = gx + dx * (LENGTH / 2);
  const mz = gz + dz * (LENGTH / 2);
  const isX = dx !== 0;

  const col1x = gx + dx * 1.8;
  const col1z = gz + dz * 1.8;
  const col2x = gx + dx * (LENGTH - 1.5);
  const col2z = gz + dz * (LENGTH - 1.5);

  return (
    <group>
      {/* Inner support column */}
      <mesh position={[col1x, ELEV / 2, col1z]}>
        <cylinderGeometry args={[0.11, 0.16, ELEV, 6]} />
        <meshStandardMaterial color="#3a4352" metalness={0.82} roughness={0.18} />
      </mesh>
      {/* Outer support column */}
      <mesh position={[col2x, ELEV / 2, col2z]}>
        <cylinderGeometry args={[0.11, 0.16, ELEV, 6]} />
        <meshStandardMaterial color="#3a4352" metalness={0.82} roughness={0.18} />
      </mesh>
      {/* Bridge tube body */}
      <mesh position={[mx, ELEV, mz]}>
        <boxGeometry args={[isX ? LENGTH : W, H, isX ? W : LENGTH]} />
        <meshStandardMaterial
          color="#28333e"
          metalness={0.7}
          roughness={0.3}
          emissive="#ffa040"
          emissiveIntensity={0.03}
        />
      </mesh>
      {/* Amber docking light at aircraft end */}
      <pointLight
        position={[gx + dx * (LENGTH + 0.6), ELEV + 0.2, gz + dz * (LENGTH + 0.6)]}
        intensity={1.2}
        distance={7}
        color="#ffaa40"
      />
    </group>
  );
}
