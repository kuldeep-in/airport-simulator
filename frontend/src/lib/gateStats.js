// Shared gate/flight statistics helpers used by GatesPage and DashboardPage.

export const DAY_MS = 24 * 60 * 60 * 1000;

// Utilization % over the trailing 24h window [now-24h, now]: sum of each stay's
// overlap with the window (history stays + the current, still-open occupancy),
// divided by the window length. Clamped to 0–100.
export function utilization24h(gate, now) {
  const winStart = now - DAY_MS;
  let occ = 0;
  const add = (from, until) => {
    if (from == null || until == null) return;
    const a = Math.max(from, winStart);
    const b = Math.min(until, now);
    if (b > a) occ += b - a;
  };
  for (const h of gate.history || []) {
    add(new Date(h.occupied_from).getTime(), new Date(h.occupied_until).getTime());
  }
  if (gate.status === 'OCCUPIED' && gate.current?.occupied_from) {
    add(new Date(gate.current.occupied_from).getTime(), now);
  }
  return Math.max(0, Math.min(100, (occ / DAY_MS) * 100));
}

// Widebody vs narrowbody, mirroring the server's gates.capacity_class_of()
// regexes (A388/A35x/B77x/B78x/A33x = wide; everything else narrow).
export function isWidebody(aircraftType) {
  const t = (aircraftType || '').toUpperCase();
  return /A38[08]|B77|A35[09]|A35K|B78[789]|A33[023]/.test(t);
}
