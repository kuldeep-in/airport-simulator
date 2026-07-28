function getDir(gateId) {
  const letter = gateId[0];
  const num = parseInt(gateId.slice(1));
  if (letter === 'A' || letter === 'C') return [-1, 0, 0];
  if (letter === 'B') return num <= 7 ? [0, 0, -1] : [0, 0, 1];
  if (letter === 'D') return [0, 0, -1];
  if (letter === 'E') return [-1, 0, 0];
  return [-1, 0, 0];
}

export default function GateMarkings({ gateId, gatePosition }) {
  const [gx, , gz] = gatePosition;
  const [dx, , dz] = getDir(gateId);
  const isX = dx !== 0;

  const DASH_OFFSETS = [3, 7, 11, 15, 19, 23];

  return (
    <group>
      {/* Lead-in yellow dashes */}
      {DASH_OFFSETS.map((d, i) => (
        <mesh
          key={i}
          position={[gx + dx * d, 0.08, gz + dz * d]}
          rotation={[-Math.PI / 2, 0, 0]}
        >
          <planeGeometry args={isX ? [2.8, 0.18] : [0.18, 2.8]} />
          <meshBasicMaterial color="#d4a810" opacity={0.72} transparent />
        </mesh>
      ))}

      {/* T-bar — transverse stop line */}
      <mesh position={[gx, 0.09, gz]} rotation={[-Math.PI / 2, 0, 0]}>
        <planeGeometry args={isX ? [0.22, 4] : [4, 0.22]} />
        <meshBasicMaterial color="#d4a810" opacity={0.85} transparent />
      </mesh>

      {/* T-bar stem (short inline piece toward terminal) */}
      <mesh
        position={[gx - dx * 0.9, 0.09, gz - dz * 0.9]}
        rotation={[-Math.PI / 2, 0, 0]}
      >
        <planeGeometry args={isX ? [1.8, 0.22] : [0.22, 1.8]} />
        <meshBasicMaterial color="#d4a810" opacity={0.85} transparent />
      </mesh>
    </group>
  );
}
