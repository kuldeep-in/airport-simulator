// Flight status labels/colours + the departure display-status rule, shared by the
// Schedule page and the Departures board so both always show the same status.

// The single, real-world flight status from AeroDataBox (flight.api_status).
// Full observed vocabulary from the live DOH feed: CheckIn, Boarding, GateClosed,
// Departed, Expected, Delayed, Arrived, Canceled, Unknown (+ Diverted per the docs).
export const API_STATUS_LABEL = {
  CheckIn: 'CHECK-IN', Boarding: 'BOARDING', GateClosed: 'GATE CLOSED',
  Departed: 'DEPARTED', Expected: 'EXPECTED', Delayed: 'DELAYED',
  Arrived: 'LANDED', Approaching: 'APPROACHING', Diverted: 'DIVERTED',
  Canceled: 'CANCELLED', Cancelled: 'CANCELLED', Unknown: 'SCHEDULED',
};
// Badge text renders at 10px (normal text), so every colour here must clear the
// WCAG AA 4.5:1 ratio against --bg-card (#1c2535). Five of these previously did
// not: CheckIn/Unknown/fallback #6070a0 (3.17:1), Departed #64748b (3.23:1) and
// Canceled/Diverted #ef4444 (4.09:1). Raised to the nearest accessible tone in the
// same hue family so the semantic meaning is unchanged.
// Display-status progression for a departure (all display-only, never written
// to the DB), in precedence order:
//   already departed (RUNWAY / real Departed)      → keep real api_status
//   GATE CLOSED  — within 2 min of dep, OR taxiing out / holding short
//   BOARDING     — otherwise, once it's on the map (has a sim phase, e.g.
//                  GATE_PARKED sitting at its stand)
//   else         → the raw api_status (still SCHEDULED, not yet on the map)
export function departureDisplayStatus(flight, now) {
  const depMs = new Date(flight.scheduled_dep).getTime();
  const taxiing = ['TAXIING_OUT', 'HOLDING_SHORT'].includes(flight.phase);
  const within2min = depMs - now <= 2 * 60 * 1000 && depMs - now >= 0;
  const alreadyGone = flight.phase === 'RUNWAY' || (flight.api_status || '').toLowerCase() === 'departed';
  const onMap = !!flight.phase;  // has a sim phase → placed on the map
  if (alreadyGone) return flight.api_status;
  if (taxiing || within2min) return 'GateClosed';
  if (onMap) return 'Boarding';
  return flight.api_status;
}
