import { useCallback, useEffect, useMemo, useState } from 'react';
import { CalendarClock, PlaneLanding, PlaneTakeoff } from 'lucide-react';
import { useSimStore } from '../store/useSimStore';

const POLL_MS = 15000;

const connectionColor = (min) => (min < 45 ? '#ef4444' : min < 90 ? '#f59e0b' : '#22c55e');

// The single, real-world flight status from AeroDataBox (flight.api_status) —
// this is what the schedule shows (the sim's own animation phase drives only the
// 3D map, not this list). Full observed vocabulary from the live DOH feed:
// CheckIn, Boarding, GateClosed, Departed, Expected, Delayed, Arrived, Canceled,
// Unknown (+ Diverted per the docs). Each maps to a readable label + colour.
const API_STATUS_LABEL = {
  CheckIn: 'CHECK-IN', Boarding: 'BOARDING', GateClosed: 'GATE CLOSED',
  Departed: 'DEPARTED', Expected: 'EXPECTED', Delayed: 'DELAYED',
  Arrived: 'LANDED', Approaching: 'APPROACHING', Diverted: 'DIVERTED',
  Canceled: 'CANCELLED', Cancelled: 'CANCELLED', Unknown: 'SCHEDULED',
};
const API_STATUS_COLOR = {
  CheckIn: '#6070a0', Boarding: '#4ade80', GateClosed: '#f59e0b',
  Departed: '#64748b', Expected: '#38bdf8', Delayed: '#f97316',
  Arrived: '#22c55e', Approaching: '#38bdf8', Diverted: '#ef4444',
  Canceled: '#ef4444', Cancelled: '#ef4444', Unknown: '#6070a0',
};
function ApiStatusBadge({ status }) {
  if (!status) return null;
  const color = API_STATUS_COLOR[status] ?? '#6070a0';
  const label = API_STATUS_LABEL[status] ?? status.toUpperCase();
  return (
    <span
      title="Real-world status (AeroDataBox)"
      style={{
        fontSize: 10, fontWeight: 700, letterSpacing: 0.4, textTransform: 'uppercase',
        color, border: `1px solid ${color}55`, borderRadius: 4, padding: '2px 7px',
      }}
    >
      {label}
    </span>
  );
}

async function fetchSchedule() {
  const res = await fetch('/api/schedule');
  if (!res.ok) throw new Error(`schedule fetch failed: ${res.status}`);
  return res.json();
}

function fmtCountdown(seconds) {
  if (seconds == null) return '—';
  if (seconds <= 0) return 'now';
  const totalMin = Math.max(1, Math.round(seconds / 60));
  const h = Math.floor(totalMin / 60);
  const m = totalMin % 60;
  if (h === 0) return `${m} min${m === 1 ? '' : 's'}`;
  if (m === 0) return `${h} hour${h === 1 ? '' : 's'}`;
  return `${h} hour${h === 1 ? '' : 's'} ${m} min${m === 1 ? '' : 's'}`;
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

function SectionHeader({ icon, title, count }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 10 }}>
      {icon}
      <span style={{ fontWeight: 700, fontSize: 13 }}>{title}</span>
      <span className="muted" style={{ fontSize: 11 }}>({count})</span>
    </div>
  );
}

function EmptyHint({ text }) {
  return (
    <div className="muted" style={{ padding: '20px 8px', fontSize: 12 }}>
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
          background: 'rgba(255,255,255,0.08)', border: '1px solid rgba(255,255,255,0.15)',
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
  const eta = isParked
    ? { clock: flight.gate_arrival ? fmtClock(flight.gate_arrival) : '—', minutesLabel: null }
    : fmtScheduled(arrivalEtaIso(flight), now);

  // Once an arrival is on the map (it has a sim phase — ARRIVING/PARKED — i.e. it
  // has "touched down"), show it as LANDED regardless of the raw AeroDataBox
  // status, which can still read Expected/Approaching. `phase` is null while the
  // flight is still SCHEDULED (not yet on the map).
  const displayStatus = flight.phase ? 'Arrived' : flight.api_status;

  return (
    <div
      className="card"
      style={{
        padding: '10px 14px',
        cursor: 'pointer',
        borderLeft: expanded ? '3px solid var(--accent-blue)' : '3px solid transparent',
        background: expanded ? 'rgba(56,189,248,0.08)' : undefined,
      }}
      onClick={onToggle}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap', rowGap: 6 }}>
        <div style={{ width: 60, fontWeight: 700 }}>{flight.callsign}</div>
        <div className="muted" style={{ width: 42 }}>{flight.origin}</div>
        <div className="muted" style={{ width: 64 }}>{flight.aircraft_type}</div>
        <div className="muted" style={{ width: 64 }}>{flight.assigned_gate ? `Gate ${flight.assigned_gate}` : ''}</div>
        <div style={{ width: 92 }}>
          <ApiStatusBadge status={displayStatus} />
        </div>
        <div style={{ width: 110, fontFamily: 'monospace', fontSize: 12 }}>
          {eta.clock}
          {eta.minutesLabel && (
            <span className="muted" style={{ marginLeft: 5, fontSize: 10 }}>({eta.minutesLabel})</span>
          )}
        </div>
        <div style={{ flex: 1 }} />
        <ConnectionDots connections={connections} />
      </div>

      {expanded && (
        <div style={{ marginTop: 10, borderTop: '1px solid var(--border)', paddingTop: 10 }}>
          {connections.length === 0 && (
            <div className="muted" style={{ fontSize: 11 }}>
              No outbound connections for this flight.
            </div>
          )}
          {connections.map((c) => (
            <div
              key={c.id}
              style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '4px 0', fontSize: 11, flexWrap: 'wrap' }}
            >
              <span
                style={{
                  width: 9, height: 9, borderRadius: '50%',
                  background: connectionColor(c.connectionMinutes), flexShrink: 0,
                }}
              />
              <span style={{ fontWeight: 700, width: 64 }}>{c.callsign}</span>
              <span className="muted" style={{ width: 46 }}>→ {c.destination}</span>
              <span className="muted" style={{ width: 60 }}>Gate {c.assigned_gate}</span>
              <span className="muted" style={{ width: 60 }}>{fmtClock(c.scheduled_dep)}</span>
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
  const depMs = new Date(flight.scheduled_dep).getTime();
  const countdownSec = flight.status === 'SCHEDULED' ? Math.max(0, (depMs - now) / 1000) : null;

  // Display-status progression for a departure (all display-only, never written
  // to the DB), in precedence order:
  //   already departed (RUNWAY / real Departed)      → keep real api_status
  //   GATE CLOSED  — within 2 min of dep, OR taxiing out / holding short
  //   BOARDING     — otherwise, once it's on the map (has a sim phase, e.g.
  //                  GATE_PARKED sitting at its stand)
  //   else         → the raw api_status (still SCHEDULED, not yet on the map)
  const taxiing = ['TAXIING_OUT', 'HOLDING_SHORT'].includes(flight.phase);
  const within2min = depMs - now <= 2 * 60 * 1000 && depMs - now >= 0;
  const alreadyGone = flight.phase === 'RUNWAY' || (flight.api_status || '').toLowerCase() === 'departed';
  const onMap = !!flight.phase;  // has a sim phase → placed on the map
  let displayStatus = flight.api_status;
  if (!alreadyGone) {
    if (taxiing || within2min) displayStatus = 'GateClosed';
    else if (onMap) displayStatus = 'Boarding';
  }

  return (
    <div
      className="card"
      style={{
        padding: '10px 14px', display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap', rowGap: 6,
        borderLeft: highlighted ? '3px solid var(--accent-blue)' : '3px solid transparent',
        background: highlighted ? 'rgba(56,189,248,0.08)' : undefined,
        opacity: faded ? 0.35 : 1,
        transition: 'opacity 0.15s ease',
      }}
    >
      <div style={{ width: 70, fontWeight: 700 }}>{flight.callsign}</div>
      <div className="muted" style={{ width: 46 }}>→ {flight.destination}</div>
      <div className="muted" style={{ width: 64 }}>{flight.aircraft_type}</div>
      <div className="muted" style={{ width: 64 }}>{flight.assigned_gate ? `Gate ${flight.assigned_gate}` : '—'}</div>
      <div style={{ width: 70, fontFamily: 'monospace', fontSize: 12 }}>{fmtClock(flight.scheduled_dep)}</div>
      <div style={{ flex: 1 }} />
      {countdownSec != null && (
        <span className="muted" style={{ fontSize: 11, fontFamily: 'monospace' }}>
          in {fmtCountdown(countdownSec)}
        </span>
      )}
      <ApiStatusBadge status={displayStatus} />
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
    <div style={{ height: '100%', width: '100%', overflow: 'auto', background: 'var(--bg-base)' }}>
      <div
        className="panel"
        style={{
          height: 56, display: 'flex', alignItems: 'center', padding: '0 16px',
          borderRadius: 0, borderLeft: 'none', borderRight: 'none', borderTop: 'none',
          position: 'sticky', top: 0, zIndex: 1,
        }}
      >
        <CalendarClock size={20} className="accent" />
        <div style={{ marginLeft: 10 }}>
          <div style={{ fontSize: 15, fontWeight: 700, lineHeight: 1 }}>Schedule</div>
          <div className="muted" style={{ fontSize: 10 }}>
            Incoming arrivals &amp; outbound connections
          </div>
        </div>
        <div style={{ flex: 1 }} />
        <div
          className="card"
          title="Live flights from AeroDataBox — refreshes hourly; each flight activates at its real scheduled time."
          style={{
            display: 'flex', alignItems: 'center', gap: 8, height: 30, padding: '0 14px',
            fontWeight: 700, fontSize: 12,
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
          maxWidth: 1400, margin: '0 auto',
        }}
      >
        <div>
          <SectionHeader icon={<PlaneLanding size={14} />} title="Incoming" count={inbound.length} />
          {inbound.length === 0 && <EmptyHint text="Waiting for live arrivals from AeroDataBox…" />}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
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

        <div>
          <SectionHeader icon={<PlaneTakeoff size={14} />} title="Outgoing" count={outbound.length} />
          {outbound.length === 0 && <EmptyHint text="Waiting for live departures from AeroDataBox…" />}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
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
