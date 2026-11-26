import { Plane } from 'lucide-react';
import { useSimStore } from '../../store/useSimStore';

const SPEEDS = [1, 2, 4, 8];

// Speed/pause control is GONE, not just hidden. Positions are computed by a separate
// process (the generator job) that this app cannot pause or re-clock — the old
// POST /api/sim endpoint was removed with the split. A control that silently did
// nothing would be worse than none. The array + flag stay only so the layout below is
// easy to restore if the generator ever exposes its own control channel.
const SHOW_SPEED_CONTROLS = false;

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
            Airport Operations
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
              onClick={() => setVisualSpeed(sp)}
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
