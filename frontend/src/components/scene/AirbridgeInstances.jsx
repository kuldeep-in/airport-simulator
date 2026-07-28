import { useMemo } from 'react';
import * as THREE from 'three';
import { mergeGeometries } from 'three/examples/jsm/utils/BufferGeometryUtils.js';
import { GATE_DIRS } from '../../data/gateData.js';

// ── Jetway dimensions — 1 world unit = 10 m ──────────────────────────────────
// Reference image: gate lounge box at building face, slim arm at door level, cab at aircraft end
const ELEV  = 0.65;  // arm centre Y (6.5 m — widebody door height)
const ARM_H = 0.22;  // arm tunnel height (2.2 m)
const ARM_W = 0.20;  // arm tunnel width (2.0 m)
const ARM_L = 2.40;  // arm length beyond lounge face (24 m)
const CAB_D = 0.30;  // cab depth at aircraft end
const CAB_W = 0.28;  // cab width
const CAB_H = 0.34;  // cab height (slightly taller than arm)
const COL_R = 0.030; // support column radius
const COL_TOP_R = COL_R * 1.4; // flared foot

// Gate lounge: distinctive box protruding from building face at each gate
const LGE_D = 0.36;  // depth away from building (3.6 m)
const LGE_W = 0.44;  // width across gate slot (4.4 m)
const LGE_H = 1.05;  // height — slightly shorter than concourse wall

export default function AirbridgeInstances({ gatePositions }) {
  const { loungeGeo, armGeo, colGeo, cabGeo } = useMemo(() => {
    const lounges = [], arms = [], cols = [], cabs = [];

    for (const [id, pos] of Object.entries(gatePositions)) {
      const [gx, , gz] = pos;
      const dir = GATE_DIRS[id] ?? [0, 0, 1];
      const dx = dir[0], dz = dir[2];
      const isX = Math.abs(dx) > Math.abs(dz);

      // ── Gate lounge box (protrudes from building face) ──────────────────────
      const lg = new THREE.BoxGeometry(
        isX ? LGE_D : LGE_W,
        LGE_H,
        isX ? LGE_W : LGE_D,
      );
      lg.translate(gx + dx * (LGE_D / 2), LGE_H / 2, gz + dz * (LGE_D / 2));
      lounges.push(lg);

      // ── Jetway arm (elevated tunnel from lounge to cab) ─────────────────────
      const armOff = LGE_D + ARM_L / 2;   // centre of arm from gate
      const arm = new THREE.BoxGeometry(
        isX ? ARM_L : ARM_W,
        ARM_H,
        isX ? ARM_W : ARM_L,
      );
      arm.translate(gx + dx * armOff, ELEV, gz + dz * armOff);
      arms.push(arm);

      // ── Support column — near the cab end (70% along arm) ──────────────────
      const colT = LGE_D + ARM_L * 0.70;
      const colH = ELEV - ARM_H / 2;    // column top meets arm underside
      const col = new THREE.CylinderGeometry(COL_R, COL_TOP_R, colH, 6);
      col.translate(gx + dx * colT, colH / 2, gz + dz * colT);
      cols.push(col);

      // ── Passenger cab at aircraft end ───────────────────────────────────────
      const cabOff = LGE_D + ARM_L + CAB_D / 2;
      const cab = new THREE.BoxGeometry(
        isX ? CAB_D : CAB_W,
        CAB_H,
        isX ? CAB_W : CAB_D,
      );
      cab.translate(gx + dx * cabOff, ELEV, gz + dz * cabOff);
      cabs.push(cab);
    }

    return {
      loungeGeo: lounges.length ? mergeGeometries(lounges) : null,
      armGeo:    arms.length    ? mergeGeometries(arms)    : null,
      colGeo:    cols.length    ? mergeGeometries(cols)    : null,
      cabGeo:    cabs.length    ? mergeGeometries(cabs)    : null,
    };
  }, [gatePositions]);

  return (
    <>
      {/* Gate lounge boxes — slightly darker than building, distinct per gate */}
      {loungeGeo && (
        <mesh geometry={loungeGeo} castShadow receiveShadow>
          <meshStandardMaterial color="#b8bec8" roughness={0.70} metalness={0.06} />
        </mesh>
      )}
      {/* Jetway arms — slim elevated tunnels */}
      {armGeo && (
        <mesh geometry={armGeo} castShadow>
          <meshStandardMaterial color="#c8cdd8" roughness={0.50} metalness={0.12} />
        </mesh>
      )}
      {/* Support columns — slender metallic */}
      {colGeo && (
        <mesh geometry={colGeo} castShadow>
          <meshStandardMaterial color="#7a8494" roughness={0.45} metalness={0.45} />
        </mesh>
      )}
      {/* Passenger cab at aircraft end */}
      {cabGeo && (
        <mesh geometry={cabGeo} castShadow>
          <meshStandardMaterial color="#b0b8c4" roughness={0.55} metalness={0.14} />
        </mesh>
      )}
    </>
  );
}
