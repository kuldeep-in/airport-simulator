// Airline logo helper, shared across pages.

// Per-IATA logo PNG (served from frontend/public/airline-logos/). Missing files
// should be handled by the caller via <img onError>.
export function airlineLogo(code) {
  return `/airline-logos/${(code || '').toUpperCase()}.png`;
}
