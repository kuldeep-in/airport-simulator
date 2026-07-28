import { Plane } from 'lucide-react';
import { useSimStore } from '../../store/useSimStore';

const SPEEDS = [1, 2, 4, 8];

// Hidden for now — the app always runs at 1x (arrival/departure scheduling
// assumes a fixed real-time-matching clock; see project notes on the
// sim-time-vs-wall-clock drift this caused when speed was ever changed).
// Flip back to true to restore the UI once that's revisited.
const SHOW_SPEED_CONTROLS = false;

async function postSim(body) {
  try {
    await fetch('/api/sim', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
  } catch (e) {
    // WS sim_tick will still reflect authoritative state
  }
}

export default function SimControls() {
  const visualSpeed    = useSimStore((s) => s.visualSpeed);
  const setVisualSpeed = useSimStore((s) => s.setVisualSpeed);

  return (
    <div
      className="panel"
      style={{
        height: 56,
        display: 'flex',
        alignItems: 'center',
        padding: '0 16px',
        borderRadius: 0,
        borderLeft: 'none',
        borderRight: 'none',
        borderTop: 'none',
      }}
    >
      {/* title */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <Plane size={20} className="accent" />
        <div>
          <div style={{ fontSize: 15, fontWeight: 700, lineHeight: 1 }}>
            HIA Simulator
          </div>
          <div className="muted" style={{ fontSize: 10 }}>
            Hamad International · OTHH
          </div>
        </div>
      </div>

      <div style={{ flex: 1 }} />

      {/* speed controls */}
      {SHOW_SPEED_CONTROLS && (
        <div style={{ display: 'flex', gap: 4 }}>
          {SPEEDS.map((sp) => (
            <button
              key={sp}
              onClick={() => { postSim({ speed: sp }); setVisualSpeed(sp); }}
              className="card"
              style={{
                minWidth: 38,
                height: 30,
                cursor: 'pointer',
                background:
                  visualSpeed === sp ? 'var(--accent-blue)' : 'var(--bg-card)',
                color: visualSpeed === sp ? '#0a0e1a' : 'var(--text-primary)',
                fontWeight: visualSpeed === sp ? 700 : 400,
              }}
            >
              {sp}×
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
