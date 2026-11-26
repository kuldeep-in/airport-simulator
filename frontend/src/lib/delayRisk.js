// Delay-risk (ML) presentation helpers, shared by the board, dashboard and alert banner.
// Bands come from the scoring job (ml/score_delay_risk.py): high ≥ 50 %, medium ≥ 25 %.
export const RISK_COLOR = { high: '#f87171', medium: '#fbbf24', low: '#4ade80' };
export const riskPct = (r) => (r == null ? '—' : `${Math.round(r * 100)}%`);
export const riskColor = (band) => RISK_COLOR[band] || '#94a3b8';

export async function fetchDelayRisk(hours = 6) {
  const res = await fetch(`/api/delay-risk?hours=${hours}`);
  if (!res.ok) throw new Error(`delay-risk ${res.status}`);
  return res.json();
}
