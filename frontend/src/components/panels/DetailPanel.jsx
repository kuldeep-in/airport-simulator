import { X } from 'lucide-react';
import { useSimStore } from '../../store/useSimStore';

function Row({ label, value }) {
  return (
    <div
      style={{
        display: 'flex',
        justifyContent: 'space-between',
        padding: '5px 0',
        borderBottom: '1px solid var(--border)',
      }}
    >
      <span className="muted">{label}</span>
      <span style={{ textAlign: 'right' }}>{value ?? '—'}</span>
    </div>
  );
}

function fmtTime(iso) {
  if (!iso) return '—';
  try {
    const d = new Date(iso);
    return d.toLocaleTimeString('en-GB', {
      hour: '2-digit',
      minute: '2-digit',
      timeZone: 'Asia/Qatar',
    });
  } catch {
    return iso;
  }
}

const STATUS_COLOR = {
  AVAILABLE: 'var(--status-green)',
  OCCUPIED:  'var(--status-yellow)',
  BOARDING:  'var(--status-yellow)',
  CLOSED:    'var(--status-red)',
};

const PHASE_DISPLAY = {
  ARRIVING: 'ON GROUND',
  PARKED:   'AT GATE',
  AIRBORNE: 'AIRBORNE',
  BOARDING: 'BOARDING',
};

export default function DetailPanel() {
  const selected = useSimStore((s) => s.selectedEntity);
  const setSelected = useSimStore((s) => s.setSelectedEntity);
  const gates = useSimStore((s) => s.gates);
  const flights = useSimStore((s) => s.flights);
  const positions = useSimStore((s) => s.positions);

  // Aircraft details are now shown in a popup attached to the plane on the map
  // (see Aircraft3D.jsx's <Html> popup), not in this top-left panel. Only gates
  // still use this panel.
  if (selected?.type === 'aircraft') return null;

  if (!selected) {
    return (
      <div
        className="panel"
        style={{ padding: 14, borderRadius: 6, width: 240, background: '#111827' }}
      >
        <div
          className="muted"
          style={{ fontSize: 10, letterSpacing: 1, marginBottom: 8 }}
        >
          DETAILS
        </div>
        <div className="muted" style={{ fontSize: 12, lineHeight: 1.6 }}>
          Select a gate or aircraft on the map to inspect it.
        </div>
      </div>
    );
  }

  let title = '';
  let rows = [];

  // Only gates reach here (aircraft return null above).
  {
    const flight = Object.values(flights).find(
      (f) => f.assigned_gate === selected.id && f.status !== 'DEPARTED'
    );
    title = `Gate ${selected.id}`;
    rows = [
      ['Flight', flight?.callsign],
      ['Airline', flight?.airline],
      ['Aircraft', flight?.aircraft_type],
      ['Origin', flight?.origin],
      ['Destination', flight?.destination],
      ['Pax', flight?.pax_count],
    ];
  }

  const isGate      = selected.type === 'gate';
  const accentColor = isGate ? '#38bdf8' : '#fbbf24';
  const bgTint      = isGate ? '#0d1e2e' : '#1e1508';

  return (
    <div
      className="panel"
      style={{
        padding: 14,
        borderRadius: 6,
        width: 240,
        overflowY: 'auto',
        background: bgTint,
        borderLeft: `3px solid ${accentColor}`,
      }}
    >
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: 10,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontSize: 15, fontWeight: 600 }}>{title}</span>
        </div>
        <X
          size={16}
          className="muted"
          style={{ cursor: 'pointer' }}
          onClick={() => setSelected(null)}
        />
      </div>
      <div style={{ fontSize: 12 }}>
        {rows.map(([label, value]) => (
          <Row key={label} label={label} value={value} />
        ))}
      </div>
    </div>
  );
}
