import { useEffect } from 'react';
import { Text } from '@react-three/drei';
import { useSimStore } from '../../store/useSimStore';

const VISIBLE_GATES = new Set([
  'A1',  'A3',  'A4',  'A5',  'A6',  'A7',  'A8',  'A9',  'A10', 'A11',
  'B1',  'B2',  'B3',  'B4',  'B5',  'B6',  'B7',  'B8',  'B9',  'B10',
  'C1',  'C2',  'C3',  'C4',  'C5',  'C6',  'C7',  'C8',  'C9',
  'C10', 'C11', 'C12', 'C13', 'C14', 'C15', 'C16', 'C18', 'C19',
  'C20', 'C22', 'C23', 'C24', 'C25', 'C26',
  'D1',  'D2',  'D3',  'D4',  'D5',  'D6',  'D7',  'D8',  'D9',
  'E1',  'E2',  'E3',  'E4',  'E5',  'E6',  'E7',  'E8',
]);

// Gate number sits just above the apron (like a painted stand marking) rather
// than floating high, and uses normal depth testing at a low render order — so a
// parked aircraft physically occludes it (the label is "below" the aircraft
// layer). When the stand is empty the number reads clearly on the apron.
const LABEL_Y = 0.14;
const LABEL_COLOR = '#0a0a0a';

export default function GateMarker({ gate, gatePosition, occupied = false }) {
  const layers            = useSimStore((s) => s.layers);
  const setSelectedEntity = useSimStore((s) => s.setSelectedEntity);

  // Safety net: don't leave the cursor stuck as 'pointer' if this marker
  // unmounts (e.g. gates layer toggled off) while the pointer is still over it.
  useEffect(() => () => { document.body.style.cursor = 'auto'; }, []);

  // Hide the gate number while an aircraft is parked on the stand — the plane
  // sits on the number and it's just redundant clutter. Shown when the gate is free.
  if (!layers.gates || !gatePosition || !VISIBLE_GATES.has(gate.id) || occupied) return null;

  const [gx, , gz] = gatePosition;

  return (
    <group
      onClick={(e) => { e.stopPropagation(); setSelectedEntity({ type: 'gate', id: gate.id }); }}
      onPointerOver={(e) => { e.stopPropagation(); document.body.style.cursor = 'pointer'; }}
      onPointerOut={(e) => { e.stopPropagation(); document.body.style.cursor = 'auto'; }}
    >
      <Text
        position={[gx, LABEL_Y, gz]}
        rotation={[-Math.PI / 2, 0, 0]}
        fontSize={2.6}
        fontWeight={700}
        color={LABEL_COLOR}
        anchorX="center"
        anchorY="middle"
        outlineWidth={0.06}
        outlineColor="#ffffff"
        renderOrder={1}
      >
        {gate.id}
      </Text>
    </group>
  );
}
