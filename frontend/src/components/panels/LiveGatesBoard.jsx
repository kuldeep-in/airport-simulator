import { useMemo } from 'react';
import { Radio } from 'lucide-react';
import { useSimStore } from '../../store/useSimStore';
import { GATE_LONLAT } from '../../data/gateData.js';

// Same natural concourse+number sort used by the Gate Editor, so gate order
// is consistent everywhere in the app (A1, A3, ... A11, B1, ... C26, ...).
const ALL_GATE_IDS = Object.keys(GATE_LONLAT).sort((a, b) => {
  const [, ac, an] = a.match(/([A-Z])(\d+)/);
  const [, bc, bn] = b.match(/([A-Z])(\d+)/);
  return ac < bc ? -1 : ac > bc ? 1 : parseInt(an) - parseInt(bn);
});

const PHASE_LABEL = {
  ARRIVING:       'ARRIVING',
  TAXIING_IN:     'TAXIING IN',
  PARKED:         'AT GATE',
  GATE_PARKED:    'AT GATE',
  GROUND_SERVICE: 'GROUND SVC',
  BOARDING:       'BOARDING',
  TAXIING_OUT:    'TAXIING OUT',
  HOLDING_SHORT:  'HOLDING SHORT',
  RUNWAY:         'ON RUNWAY',
};
const PHASE_COLOR = {
  ARRIVING:       '#4ade80',
  TAXIING_IN:     '#4ade80',
  PARKED:         '#38bdf8',
  GATE_PARKED:    '#38bdf8',
  GROUND_SERVICE: '#fbbf24',
  BOARDING:       '#fbbf24',
  TAXIING_OUT:    '#fbbf24',
  HOLDING_SHORT:  '#f97316',
  RUNWAY:         '#ef4444',
};

// Departure-side phases (a flight leaving DOH). Everything else live at a gate
// is an arrival. Direction is also confirmed by origin/destination === 'DOH'.
const DEPARTURE_PHASES = new Set(['GATE_PARKED', 'BOARDING', 'TAXIING_OUT', 'HOLDING_SHORT', 'RUNWAY']);

function FlightRow({ id, occupant }) {
  return (
    <div
      style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '5px 8px', marginBottom: 4, borderRadius: 4,
        background: 'rgba(255,255,255,0.04)',
        borderLeft: `3px solid ${PHASE_COLOR[occupant.phase] ?? '#6070a0'}`,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{ fontWeight: 700, fontSize: 12, width: 30 }}>{id}</span>
        <span className="muted" style={{ fontSize: 11 }}>{occupant.callsign}</span>
      </div>
      <span
        style={{
          fontSize: 10, color: PHASE_COLOR[occupant.phase] ?? '#6070a0',
          fontWeight: 600, letterSpacing: 0.5, whiteSpace: 'nowrap',
        }}
      >
        {PHASE_LABEL[occupant.phase] ?? occupant.phase}
      </span>
    </div>
  );
}

export default function LiveGatesBoard() {
  const layers      = useSimStore((s) => s.layers);
  const wsConnected = useSimStore((s) => s.wsConnected);
  const flights     = useSimStore((s) => s.flights);
  const positions   = useSimStore((s) => s.positions);

  const { arriving, departing } = useMemo(() => {
    const arr = {}, dep = {};
    for (const f of Object.values(flights)) {
      if (f.source !== 'lakebase' || !f.assigned_gate) continue;
      const phase = positions[f.id]?.phase ?? f.phase;
      const isDeparture = DEPARTURE_PHASES.has(phase) || f.origin === 'DOH';
      (isDeparture ? dep : arr)[f.assigned_gate] = { callsign: f.callsign, phase };
    }
    const rows = (byGate) => ALL_GATE_IDS.filter((id) => byGate[id]).map((id) => ({ id, occupant: byGate[id] }));
    return { arriving: rows(arr), departing: rows(dep) };
  }, [flights, positions]);

  const total = arriving.length + departing.length;

  return (
    <div
      className="panel"
      style={{
        width: 230, padding: 14, borderRadius: 0,
        display: 'flex', flexDirection: 'column', maxHeight: '70vh',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12, flexShrink: 0 }}>
        <Radio size={14} className={wsConnected ? 'accent' : ''} />
        <span className="dot" style={{ background: wsConnected ? 'var(--status-green)' : 'var(--status-red)' }} />
        <span className="muted" style={{ fontSize: 12 }}>
          {wsConnected ? 'Live feed connected' : 'Reconnecting…'}
        </span>
      </div>

      {layers.gates && total > 0 && (
        <div style={{ overflowY: 'auto' }}>
          {arriving.length > 0 && (
            <>
              <div className="muted" style={{ fontSize: 10, letterSpacing: 1, marginBottom: 6, color: '#4ade80' }}>
                ▼ ARRIVING · {arriving.length}
              </div>
              {arriving.map(({ id, occupant }) => <FlightRow key={id} id={id} occupant={occupant} />)}
            </>
          )}
          {departing.length > 0 && (
            <>
              <div className="muted" style={{ fontSize: 10, letterSpacing: 1, margin: '10px 0 6px', color: '#fbbf24' }}>
                ▲ DEPARTING · {departing.length}
              </div>
              {departing.map(({ id, occupant }) => <FlightRow key={id} id={id} occupant={occupant} />)}
            </>
          )}
        </div>
      )}
    </div>
  );
}
