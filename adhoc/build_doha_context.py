"""Build frontend/src/data/dohaContext.json from raw OSM Overpass pulls.

Produces a light-context backdrop for the 2D radar page:
  - land:      one polygon (mainland) = coastline chain closed off along the map's
               west/south edges (land is west of the coast here).
  - islands:   closed coastline rings (land surrounded by sea) drawn on the sea.
  - water:     natural=water polygons (lagoons/marinas) drawn on the land.
  - roads:     major arterials (motorway/trunk/primary/secondary), simplified.
All coords are raw [lon,lat] so they project identically to the airport geometry.
"""
import json, math

# Clip bbox (matches the fetch) — used to close the land polygon off-screen.
S, W, N, E = 25.20, 51.40, 25.42, 51.75

def rdp(pts, eps):
    """Ramer–Douglas–Peucker polyline simplification (lon/lat degrees)."""
    if len(pts) < 3: return pts
    def d(p, a, b):
        (x, y), (x1, y1), (x2, y2) = p, a, b
        dx, dy = x2 - x1, y2 - y1
        if dx == 0 and dy == 0: return math.hypot(x - x1, y - y1)
        t = ((x - x1) * dx + (y - y1) * dy) / (dx * dx + dy * dy)
        t = max(0, min(1, t))
        return math.hypot(x - (x1 + t * dx), y - (y1 + t * dy))
    dmax, idx = 0, 0
    for i in range(1, len(pts) - 1):
        dd = d(pts[i], pts[0], pts[-1])
        if dd > dmax: dmax, idx = dd, i
    if dmax > eps:
        left = rdp(pts[:idx + 1], eps); right = rdp(pts[idx:], eps)
        return left[:-1] + right
    return [pts[0], pts[-1]]

def rnd(pts, nd=5):
    return [[round(x, nd), round(y, nd)] for x, y in pts]

# ── coastline → chains ─────────────────────────────────────────────────────────
cl = json.load(open('adhoc/osm_coastline.json'))['elements']
from collections import defaultdict
segs = {}
for w in cl:
    n, g = w.get('nodes'), w.get('geometry')
    if n and g: segs[w['id']] = {'nodes': n, 'geom': [(p['lon'], p['lat']) for p in g]}
start_map = defaultdict(list)
for wid, s in segs.items():
    start_map[s['nodes'][0]].append(wid)
used, chains = set(), []
for wid, s in segs.items():
    if wid in used: continue
    chain, cn = list(s['geom']), list(s['nodes']); used.add(wid)
    while True:
        nxt = [x for x in start_map.get(cn[-1], []) if x not in used]
        if not nxt: break
        m = segs[nxt[0]]; used.add(nxt[0]); chain += m['geom'][1:]; cn += m['nodes'][1:]
    chains.append({'pts': chain, 'closed': cn[0] == cn[-1]})
chains.sort(key=lambda c: len(c['pts']), reverse=True)

EPS = 0.00018  # ~20 m simplification tolerance
mainland = None
islands = []
for c in chains:
    simp = rdp(c['pts'], EPS)
    if c['closed'] and len(simp) >= 4:
        islands.append(rnd(simp))
    elif mainland is None:
        mainland = rnd(simp)   # the big open coast chain = mainland boundary

# Close the mainland into a filled polygon by walking off the west/south edges.
# The open chain runs south→NW; land lies to the WEST, so append far-west corners.
land_poly = list(mainland) + [[W - 0.05, mainland[-1][1]], [W - 0.05, S - 0.05],
                              [mainland[0][0], S - 0.05]]

# ── water bodies ────────────────────────────────────────────────────────────────
wt = json.load(open('adhoc/osm_water.json'))['elements']
water = []
for w in wt:
    g = w.get('geometry')
    if not g or len(g) < 4: continue
    ring = rdp([(p['lon'], p['lat']) for p in g], EPS)
    if len(ring) >= 4: water.append(rnd(ring))

# ── roads: major arterials only, simplified ─────────────────────────────────────
rd = json.load(open('adhoc/osm_roads.json'))['elements']
# Simplified per request: only the PRIMARY arterial network (motorway/trunk/
# primary) — the dense secondary grid is dropped for a cleaner backdrop.
KEEP = {'motorway', 'trunk', 'primary'}
CLASS = {'motorway': 'mtr', 'trunk': 'mtr', 'primary': 'pri'}
roads = []
for w in rd:
    hw = w['tags'].get('highway')
    if hw not in KEEP: continue
    g = w.get('geometry')
    if not g or len(g) < 2: continue
    pts = rdp([(p['lon'], p['lat']) for p in g], 0.00012)  # ~13 m
    if len(pts) >= 2:
        roads.append({'c': CLASS[hw], 'p': rnd(pts)})

out = {'land': land_poly, 'islands': islands, 'water': water, 'roads': roads}
json.dump(out, open('frontend/src/data/dohaContext.json', 'w'), separators=(',', ':'))

import os
sz = os.path.getsize('frontend/src/data/dohaContext.json')
print(f"land pts: {len(land_poly)}  islands: {len(islands)}  water: {len(water)}  roads: {len(roads)}")
print(f"road pts total: {sum(len(r['p']) for r in roads)}")
print(f"file size: {sz/1024:.1f} KB")
