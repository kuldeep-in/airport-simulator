import { useMemo } from 'react';
import { useSimStore } from '../../store/useSimStore';
import AirportGround from './AirportGround';
import TerminalBuilding from './TerminalBuilding';
import Aircraft3D from './Aircraft3D';
import GateMarker from './GateMarker';
import RunwayMarkings from './RunwayMarkings';
import HeatmapOverlay from './HeatmapOverlay';
import hiaBase from '../../data/hiaBase.json';
import { GATE_POSITIONS } from '../../data/gateData.js';
import { lonLatToWorld } from '../../geo.js';

// ─── Static airport data ──────────────────────────────────────────────────────
const ALL_FEATURES = hiaBase.features;
// Apron + OSM-sourced extra features; AirportGround filters by type internally
const GROUND_TYPES = new Set(['apron', 'water', 'parking', 'mosque', 'sea', 'military']);
const GROUND_FEATURES = ALL_FEATURES.filter((f) => GROUND_TYPES.has(f.properties?.type));
const BUILDING_FEATURES = ALL_FEATURES.filter((f) => f.properties?.type === 'terminal');

// Phases where a flight is parked at / still leaving its stand — the gate number
// is hidden for its gate (redundant clutter under/near the plane).
const HIDE_NUM_PHASES = new Set(['PARKED', 'GATE_PARKED', 'TAXIING_OUT']);

// Real HIA heights in world units (1 unit = 10 m):
// Main terminal ~25-30 m, concourses ~12-15 m
const HEIGHTS = {
  terminal_hall: 3.0,
  concourse_a: 1.4,
  concourse_b: 1.4,
  concourse_c: 2.0,
  concourse_d: 1.4,
  concourse_e: 1.4,
};

function getHeight(props) {
  const sub = props?.subtype || '';
  return HEIGHTS[sub] || 10;
}


export default function Airport3D() {
  const flights = useSimStore((s) => s.flights);
  const positions = useSimStore((s) => s.positions);
  const gates = useSimStore((s) => s.gates);
  const gateEdits = useSimStore((s) => s.gateEdits);
  const layers = useSimStore((s) => s.layers);

  // Return live-edited position when gate editor has overrides, else static GATE_POSITIONS
  const getGatePos = (id) => {
    const e = gateEdits[id];
    return e ? lonLatToWorld(e.lon, e.lat) : GATE_POSITIONS[id];
  };

  // Gates whose number should be hidden — an aircraft is parked on the stand or
  // still on/leaving it, so the number would be redundant clutter under/near the
  // plane. Covers parked arrivals (PARKED), gate-parked departures (GATE_PARKED),
  // and departures still taxiing out from the stand (TAXIING_OUT). Derived live
  // from the flights themselves. Once a plane reaches the hold point / runway
  // (HOLDING_SHORT/RUNWAY) it's well clear of the gate, so the number returns.
  const occupiedGateIds = useMemo(() => {
    const s = new Set();
    for (const f of Object.values(flights)) {
      const phase = positions[f.id]?.phase ?? f.phase;
      if (f.assigned_gate && HIDE_NUM_PHASES.has(phase)) {
        s.add(f.assigned_gate);
      }
    }
    return s;
  }, [flights, positions]);

  return (
    <>
      <AirportGround features={GROUND_FEATURES} />
      <RunwayMarkings />

      {BUILDING_FEATURES.map((feature, i) => {
        const coords =
          feature.geometry.type === 'Polygon'
            ? feature.geometry.coordinates
            : feature.geometry.coordinates[0];
        return (
          <TerminalBuilding
            key={i}
            coordinates={coords}
            height={getHeight(feature.properties)}
          />
        );
      })}

      {layers.paxHeatmap && <HeatmapOverlay metric="pax_density" />}
      {layers.tempZones  && <HeatmapOverlay metric="temperature" />}

      {Object.values(gates).map((gate) => (
        <GateMarker key={gate.id} gate={gate} gatePosition={getGatePos(gate.id)} occupied={occupiedGateIds.has(gate.id)} />
      ))}
      {Object.keys(GATE_POSITIONS)
        .filter((id) => !gates[id])
        .map((id) => (
          <GateMarker
            key={id}
            gate={{ id, status: 'available' }}
            gatePosition={getGatePos(id)}
            occupied={occupiedGateIds.has(id)}
          />
        ))}

      {/* Lakebase-only aircraft — source='lakebase' flag set by seed_runner */}
      {Object.values(flights)
        .filter((flight) => flight.source === 'lakebase' && positions[flight.id]?.lat)
        .map((flight) => (
          <Aircraft3D key={flight.id} flight={flight} position={positions[flight.id]} />
        ))}
    </>
  );
}
