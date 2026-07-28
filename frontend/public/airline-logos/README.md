# Airline logos

Logos for the airlines that arrive into Hamad International (DOH), captured for
future development (e.g. showing a carrier logo in the flight-detail popup or
schedule board — not yet wired into the UI).

- **Source:** `https://pics.avs.io/200/60/<IATA>.png` (Kiwi/Tequila airline-logo CDN).
- **Format:** 200×60 PNG with transparent background, keyed by 2-char IATA code.
- **Coverage:** the airlines seen arriving in the live AeroDataBox DOH feed, plus
  the carriers the 2D/3D views already have brand liveries for. 28 airlines.
- **`manifest.json`** — `{ "<IATA>": { "name": "...", "file": "/airline-logos/<IATA>.png" } }`
  for lookup by code.

Served statically at `/airline-logos/<IATA>.png` (FastAPI serves `frontend/dist`;
Vite copies `public/` into `dist/`). To use in the app, look up by
`flight.airline` (IATA) — fall back gracefully when a code has no logo file.

To refresh / add carriers: re-run the download against `pics.avs.io` for the
IATA codes in `flight_schedule`, or just drop a `<IATA>.png` in here and add a
manifest entry.
