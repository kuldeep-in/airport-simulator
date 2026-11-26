import { useCallback, useEffect, useMemo, useState } from 'react';
import { CalendarClock, PlaneLanding, PlaneTakeoff } from 'lucide-react';
import { useSimStore } from '../store/useSimStore';
import { API_STATUS_LABEL, departureDisplayStatus } from '../lib/flightStatus.js';
import { PAGE_BG, BOARD_PANEL, ROW_RULE, HEAD_COLOR, STATUS_COLOR, BoardLogo } from '../components/boardTheme.jsx';

const POLL_MS = 15000;

// Board-style column grids (same columns as before, in the same order).
const IN_COLS = '84px 72px minmax(70px, 1fr) 54px 66px 104px 96px 44px';
const OUT_COLS = '84px 72px minmax(70px, 1fr) 54px 66px 56px 104px';
const LOGO = { width: 76, height: 34 };
const ROW_PAD = '8px 16px';

const connectionColor = (min) => (min < 45 ? '#ef4444' : min < 90 ? '#f59e0b' : '#22c55e');

function ApiStatusBadge({ status }) {
  if (!status) return null;
  const label = API_STATUS_LABEL[status] ?? status.toUpperCase();
  return (
    <span title="Real-world status (AeroDataBox)"
          style={{ fontSize: 12, fontWeight: 700, letterSpacing: 0.5, whiteSpace: 'nowrap',
                   color: STATUS_COLOR[status] ?? '#ffffff' }}>
      {label}
    </span>
  );
}

async function fetchSchedule() {
  const res = await fetch('/api/schedule');
  if (!res.ok) throw new Error(`schedule fetch failed: ${res.status}`);
  return res.json();
}

function fmtClock(iso) {
  return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

// Fixed scheduled time as a clock time, plus a "(N min)" countdown suffix
// only once it's under an hour away — a "(187 min)" badge on a flight
// that's still far out isn't useful. Countdown is computed from the fixed
// scheduled_arr/scheduled_dep timestamp, not a live-fluctuating estimate.
function fmtScheduled(iso, now) {
  if (!iso) return { clock: '—', minutesLabel: null };
  const ms = new Date(iso).getTime();
  const clock = new Date(ms).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  const minutes = (ms - now) / 60000;
  return { clock, minutesLabel: minutes < 60 ? `${Math.max(0, Math.round(minutes))} min` : null };
}

// The single "when did/will this flight actually reach the gate" basis,
// shared by the visible arrival-time badge AND the connection-time-diff
// math below — so once a flight parks, connections immediately recompute
// against its real gate_arrival instead of staying pinned to the
// touchdown+5min estimate. scheduled_arr is only ever the TOUCHDOWN time.
function arrivalEtaIso(flight) {
  if (flight.phase === 'PARKED') return flight.gate_arrival ?? null;
  if (!flight.scheduled_arr) return null;
  return new Date(new Date(flight.scheduled_arr).getTime() + 5 * 60000).toISOString();
}

function SectionHeader({ icon, title, count, cols, headings }) {
  return (
    <>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '12px 16px', borderBottom: ROW_RULE }}>
        {icon}
        <span style={{ fontWeight: 800, fontSize: 20, color: '#fff', letterSpacing: 0.5 }}>{title}</span>
        <span style={{ fontSize: 13, color: HEAD_COLOR, fontWeight: 600 }}>({count})</span>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: cols, gap: 10, alignItems: 'center',
                    padding: '6px 16px', fontSize: 10, fontWeight: 700, letterSpacing: 1,
                    color: HEAD_COLOR, borderBottom: ROW_RULE }}>
        {headings.map((h, i) => <span key={i}>{h}</span>)}
      </div>
    </>
  );
}

function EmptyHint({ text }) {
  return (
    <div style={{ padding: '20px 16px', fontSize: 13, color: HEAD_COLOR }}>
      {text}
    </div>
  );
}

function ConnectionDots({ connections }) {
  if (connections.length === 0) {
    return (
      <span
        style={{
          width: 10, height: 10, borderRadius: '50%',
          background: 'rgba(255,255,255,0.10)', border: '1px solid rgba(190,210,255,0.35)',
          flexShrink: 0, display: 'inline-block',
        }}
      />
    );
  }
  return (
    <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', maxWidth: 120 }}>
      {connections.map((c) => (
        <span
          key={c.id}
          title={`${c.callsign} → ${c.destination} (${Math.round(c.connectionMinutes)} min)`}
          style={{
            width: 10,
            height: 10,
            borderRadius: '50%',
            background: connectionColor(c.connectionMinutes),
            border: '1px solid rgba(255,255,255,0.15)',
            flexShrink: 0,
          }}
        />
      ))}
    </div>
  );
}

function InboundRow({ flight, now, connections, expanded, onToggle }) {
  // scheduled_arr is the flight's TOUCHDOWN time, not gate arrival — before
  // parking, show an estimate (touchdown + 5 min) instead. Once parked,
  // switch to the real time it actually reached the gate (gate_arrival, set
  // once in seed_runner.advance()), with no countdown suffix since it's
  // already happened.
  const isParked = flight.phase === 'PARKED';
  const hasLanded = !!flight.phase;  // ARRIVING or PARKED — it has touched down
  let eta;
  if (isParked) {
    // Parked: the real time it reached the gate, no countdown.
    eta = { clock: flight.gate_arrival ? fmtClock(flight.gate_arrival) : '—', minutesLabel: null };
  } else if (hasLanded) {
    // Landed and taxiing in: show the (already-past) TOUCHDOWN time with NO
    // future countdown. Otherwise the row reads "LANDED" next to a future
    // gate-arrival estimate (touchdown+5min) — the inconsistency that looks
    // like the plane landed before its arrival time.
    eta = { clock: flight.scheduled_arr ? fmtClock(flight.scheduled_arr) : '—', minutesLabel: null };
  } else {
    // Still approaching (SCHEDULED, not yet on the map): estimated gate arrival
    // (touchdown + 5 min) plus a countdown.
    eta = fmtScheduled(arrivalEtaIso(flight), now);
  }

  // Once an arrival is on the map (it has a sim phase — ARRIVING/PARKED — i.e. it
  // has "touched down"), show it as LANDED regardless of the raw AeroDataBox
  // status, which can still read Expected/Approaching. `phase` is null while the
  // flight is still SCHEDULED (not yet on the map).
  const displayStatus = flight.phase ? 'Arrived' : flight.api_status;

  return (
    // Keyboard-operable disclosure row: it was a bare <div onClick>, so it was
    // unreachable by Tab and invisible to assistive tech. role="button" +
    // tabIndex + Enter/Space + aria-expanded make it behave like the control it
    // already looked like.
    <div
      className="row-interactive fids-row"
      role="button"
      tabIndex={0}
      aria-expanded={expanded}
      aria-label={`Flight ${flight.callsign} from ${flight.origin}, ${connections.length} onward connections`}
      style={{
        padding: ROW_PAD, borderBottom: ROW_RULE, color: '#fff', cursor: 'pointer',
        background: expanded ? 'rgba(255,255,255,0.12)' : 'transparent',
      }}
      onClick={onToggle}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onToggle(); }
      }}
    >
      <div style={{ display: 'grid', gridTemplateColumns: IN_COLS, gap: 10, alignItems: 'center', minWidth: 0 }}>
        <BoardLogo code={flight.airline} {...LOGO} />
        <div style={{ fontSize: 16, fontWeight: 700 }}>{flight.callsign}</div>
        <div style={{ fontSize: 15, fontWeight: 600, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{flight.origin_name || flight.origin}</div>
        <div style={{ fontSize: 13, color: HEAD_COLOR }}>{flight.aircraft_type}</div>
        <div style={{ fontSize: 14, fontWeight: 600 }}>{flight.assigned_gate ? `Gate ${flight.assigned_gate}` : ''}</div>
        <div>
          <ApiStatusBadge status={displayStatus} />
        </div>
        <div className="mono" style={{ fontSize: 15, fontWeight: 600, whiteSpace: 'nowrap' }}>
          {eta.clock}
          {eta.minutesLabel && (
            <span style={{ marginLeft: 5, fontSize: 11, color: HEAD_COLOR }}>({eta.minutesLabel})</span>
          )}
        </div>
        <ConnectionDots connections={connections} />
      </div>

      {expanded && (
        <div style={{ marginTop: 8, borderTop: ROW_RULE, paddingTop: 8 }}>
          {connections.length === 0 && (
            <div style={{ fontSize: 12, color: HEAD_COLOR }}>
              No outbound connections for this flight.
            </div>
          )}
          {connections.map((c) => (
            <div
              key={c.id}
              style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '4px 0', fontSize: 12, flexWrap: 'wrap' }}
            >
              <span
                style={{
                  width: 9, height: 9, borderRadius: '50%',
                  background: connectionColor(c.connectionMinutes), flexShrink: 0,
                }}
              />
              <span style={{ fontWeight: 700, width: 64 }}>{c.callsign}</span>
              <span style={{ color: HEAD_COLOR, width: 46 }}>→ {c.destination}</span>
              <span style={{ color: HEAD_COLOR, width: 60 }}>Gate {c.assigned_gate}</span>
              <span style={{ color: HEAD_COLOR, width: 60 }}>{fmtClock(c.scheduled_dep)}</span>
              <span style={{ color: connectionColor(c.connectionMinutes), fontWeight: 600 }}>
                {Math.round(c.connectionMinutes)} min
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function OutboundRow({ flight, now, highlighted, faded }) {
  const displayStatus = departureDisplayStatus(flight, now);

  return (
    <div
      style={{
        padding: ROW_PAD, display: 'grid', gridTemplateColumns: OUT_COLS, gap: 10, alignItems: 'center', minWidth: 0,
        borderBottom: ROW_RULE, color: '#fff',
        background: highlighted ? 'rgba(255,255,255,0.12)' : 'transparent',
        opacity: faded ? 0.35 : 1,
        transition: 'opacity 0.15s ease',
      }}
    >
      <BoardLogo code={flight.airline} {...LOGO} />
      <div style={{ fontSize: 16, fontWeight: 700 }}>{flight.callsign}</div>
      <div style={{ fontSize: 15, fontWeight: 600, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>→ {flight.destination_name || flight.destination}</div>
      <div style={{ fontSize: 13, color: HEAD_COLOR }}>{flight.aircraft_type}</div>
      <div style={{ fontSize: 14, fontWeight: 600 }}>{flight.assigned_gate ? `Gate ${flight.assigned_gate}` : '—'}</div>
      <div className="mono" style={{ fontSize: 15, fontWeight: 600 }}>{fmtClock(flight.scheduled_dep)}</div>
      <div style={{ textAlign: 'right' }}>
        <ApiStatusBadge status={displayStatus} />
      </div>
    </div>
  );
}

export default function ConnectionsPage() {
  const [inbound, setInbound] = useState([]);
  const [outbound, setOutbound] = useState([]);
  const [connectionsMap, setConnectionsMap] = useState({});
  const [expandedId, setExpandedId] = useState(null);

  // The simulated clock, not wall-clock time: scheduled_arr/scheduled_dep
  // are computed from SIM["sim_time"] on the backend, which only advances
  // when unpaused and races ahead of real time at 2x/4x/8x speed. Comparing
  // them against a local Date.now() would silently drift as soon as the
  // Home page's speed/pause controls are ever touched. simTime is pushed by
  // the websocket every tick (see useWebSocket/useSimStore), so this alone
  // is enough to re-render the countdowns — no separate local clock needed.
  const simTimeIso = useSimStore((s) => s.simTime);
  const now = simTimeIso ? new Date(simTimeIso).getTime() : Date.now();

  const refresh = useCallback(() => {
    fetchSchedule()
      .then((data) => {
        setInbound(data.inbound || []);
        setOutbound(data.outbound || []);
        setConnectionsMap(data.connections || {});
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    refresh();
    const poll = setInterval(refresh, POLL_MS);
    return () => clearInterval(poll);
  }, [refresh]);

  // Flights come live from AeroDataBox and auto-activate at their real
  // scheduled times — no manual "Schedule" trigger. The 15s poll below keeps
  // the lists fresh.

  // Connections have no source in the live feed (see
  // server/simulator/connections.py), so GET /api/schedule returns an empty
  // connections map and this resolves to no connection rows. connectionMinutes
  // is still computed live for any that ever appear (red/orange/green coding).
  const connectionsByInbound = useMemo(() => {
    const outboundById = {};
    for (const o of outbound) outboundById[o.id] = o;

    const map = {};
    for (const inb of inbound) {
      const etaIso = arrivalEtaIso(inb);
      if (!etaIso) {
        map[inb.id] = [];
        continue;
      }
      const etaMs = new Date(etaIso).getTime();
      map[inb.id] = (connectionsMap[inb.id] || [])
        .map((depId) => outboundById[depId])
        .filter((o) => o && o.status !== 'DEPARTED')
        .map((o) => ({
          ...o,
          connectionMinutes: (new Date(o.scheduled_dep).getTime() - etaMs) / 60000,
        }))
        .sort((a, b) => a.connectionMinutes - b.connectionMinutes);
    }
    return map;
  }, [inbound, outbound, connectionsMap]);

  // Selecting (clicking) an inbound flight highlights it and its real
  // connections in the Outgoing column too, not just its own expanded panel.
  const highlightedOutboundIds = useMemo(
    () => new Set((connectionsByInbound[expandedId] || []).map((c) => c.id)),
    [connectionsByInbound, expandedId],
  );

  return (
    <div style={{ height: '100%', width: '100%', overflow: 'auto', background: PAGE_BG }}>
      <div
        style={{
          height: 56, display: 'flex', alignItems: 'center', padding: '0 16px',
          position: 'sticky', top: 0, zIndex: 1, background: PAGE_BG, borderBottom: ROW_RULE, color: '#fff',
        }}
      >
        <CalendarClock size={22} color="#fff" />
        <div style={{ marginLeft: 10 }}>
          <div style={{ fontSize: 17, fontWeight: 800, lineHeight: 1 }}>Schedule</div>
          <div style={{ fontSize: 11, color: HEAD_COLOR }}>
            Incoming arrivals &amp; outbound connections
          </div>
        </div>
        <div style={{ flex: 1 }} />
        <div
          title="Live flights from AeroDataBox — refreshes hourly; each flight activates at its real scheduled time."
          style={{
            display: 'flex', alignItems: 'center', gap: 8, height: 30, padding: '0 14px',
            fontWeight: 700, fontSize: 12, color: '#fff', borderRadius: 6,
            border: '1px solid rgba(160,190,255,0.35)', background: 'rgba(255,255,255,0.06)',
          }}
        >
          <span
            style={{
              width: 8, height: 8, borderRadius: '50%',
              background: 'var(--accent-green, #34d399)',
              boxShadow: '0 0 6px var(--accent-green, #34d399)',
            }}
          />
          Live · auto-updating
        </div>
      </div>

      <div
        style={{
          padding: 16, display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16,
          maxWidth: 1600, margin: '0 auto', alignItems: 'start',
        }}
      >
        <div style={BOARD_PANEL}>
          <SectionHeader icon={<PlaneLanding size={22} color="#fff" />} title="Incoming" count={inbound.length}
                         cols={IN_COLS} headings={['', 'FLIGHT', 'FROM', 'TYPE', 'GATE', 'STATUS', 'TIME', 'CONN']} />
          {inbound.length === 0 && <EmptyHint text="Waiting for live arrivals from AeroDataBox…" />}
          <div>
            {inbound.map((f) => (
              <InboundRow
                key={f.id}
                flight={f}
                now={now}
                connections={connectionsByInbound[f.id] || []}
                expanded={expandedId === f.id}
                onToggle={() => setExpandedId(expandedId === f.id ? null : f.id)}
              />
            ))}
          </div>
        </div>

        <div style={BOARD_PANEL}>
          <SectionHeader icon={<PlaneTakeoff size={22} color="#fff" />} title="Outgoing" count={outbound.length}
                         cols={OUT_COLS} headings={['', 'FLIGHT', 'TO', 'TYPE', 'GATE', 'TIME', 'STATUS']} />
          {outbound.length === 0 && <EmptyHint text="Waiting for live departures from AeroDataBox…" />}
          <div>
            {outbound.map((f) => (
              <OutboundRow
                key={f.id}
                flight={f}
                now={now}
                highlighted={highlightedOutboundIds.has(f.id)}
                faded={expandedId != null && !highlightedOutboundIds.has(f.id)}
              />
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
