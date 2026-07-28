export const CENTER_LON = 51.611;
export const CENTER_LAT = 25.266;  // real HIA terminal complex center (from OSM)
export const LON_SCALE = (111320 * Math.cos((CENTER_LAT * Math.PI) / 180)) / 10;
export const LAT_SCALE = 111320 / 10;

export function lonLatToShape(lon, lat) {
  return [(lon - CENTER_LON) * LON_SCALE, (lat - CENTER_LAT) * LAT_SCALE];
}

export function lonLatToWorld(lon, lat) {
  const x = (lon - CENTER_LON) * LON_SCALE;
  const y = (lat - CENTER_LAT) * LAT_SCALE;
  return [x, 0, -y];
}

export function headingToRotY(deg) {
  return Math.PI - (deg * Math.PI) / 180;
}
