"""
Finding WHERE a plan sheet goes (step 2 of automatic placement).

Idea: one street name on a sheet says little (Main St may be two miles long).
Two streets that CROSS say a lot: there is usually only one place where
Main St meets Oak Ave. So for each sheet:

  1. Turn each street name found on the sheet into a "road": the matching
     segments in RefStreets. Names that are the same physical road are
     merged (e.g. 'Route 5', 'East Main Road' and 'Main Road' may all be
     Route 5), so one road isn't counted twice.
  2. Find every point where two of those roads cross or meet.
  3. Score each crossing by how many of the sheet's roads pass within
     NEAR_FT of it. The best-scoring crossing wins. Names that don't agree
     with the rest (a street from a title block, a misread) simply don't add
     to the score, so they're ignored.
  4. The suggested location is the average of the winning crossings.

Uses ArcPy geometry only (no extra packages).

Main function: locate_sheet(streets, roads_index, ...) -> dict
"""

import arcpy

from .read import street_core

# A plan sheet typically covers a few hundred to ~2,000 ft of road.
# Roads within this distance of a candidate point count as "on the sheet".
NEAR_FT = 1000

# Two candidate spots farther apart than this are different places
# (used to warn when a sheet could be in two places).
RIVAL_FT = 2500


class RoadIndex:
    """All RefStreets segments, grouped so we can look roads up by name."""

    def __init__(self, streets_fc):
        self.segments = {}        # OID -> polyline
        self.by_name = {}         # street_name -> set of OIDs
        self.by_route = {}        # route number -> set of OIDs
        self.route_of = {}        # OID -> route number ('' if none)
        self.towns = set()        # town names, e.g. 'Springfield'
        self.spatial_reference = arcpy.Describe(streets_fc).spatialReference
        fields = ["OID@", "SHAPE@", "street_name", "route", "town"]
        for oid, shape, name, route, town in arcpy.da.SearchCursor(streets_fc, fields):
            if town:
                self.towns.add(town)
            if shape is None:
                continue
            self.segments[oid] = shape
            self.by_name.setdefault(name or "", set()).add(oid)
            route = (route or "").strip()
            self.route_of[oid] = route
            if route:
                self.by_route.setdefault(route, set()).add(oid)
        self._geometry_cache = {}
        extent = arcpy.Describe(streets_fc).extent
        self.center = arcpy.PointGeometry(
            arcpy.Point((extent.XMin + extent.XMax) / 2, (extent.YMin + extent.YMax) / 2),
            self.spatial_reference)

    def oids_for(self, street_name):
        """Segments for a name found on a sheet. The highway names our reader
        produces ('Route 5', 'Interstate 90') are looked up by route number,
        which covers every local name of that highway."""
        if street_name.startswith("Route ") or street_name.startswith("Interstate "):
            return set(self.by_route.get(street_name.split()[-1], set()))
        return set(self.by_name.get(street_name, set()))

    def routes_of(self, oids):
        """Route numbers carried by most of these segments (a road that is
        a numbered route along most of its length)."""
        counts = {}
        for oid in oids:
            r = self.route_of.get(oid)
            if r:
                counts[r] = counts.get(r, 0) + 1
        return {r for r, n in counts.items() if n >= len(oids) / 2}

    def geometry(self, oids):
        """One (multi-part) polyline made of these segments, cached."""
        key = frozenset(oids)
        if key not in self._geometry_cache:
            parts = arcpy.Array()
            for oid in oids:
                for part in self.segments[oid]:
                    parts.add(part)
            self._geometry_cache[key] = arcpy.Polyline(parts, self.spatial_reference)
        return self._geometry_cache[key]


def group_roads(street_names, index):
    """Turn a list of street names into a list of distinct roads.

    Returns a list of dicts: {"names": [...], "oids": set(...)}.
    Names are merged when they share segments or a route number, or the same
    distinctive part ('Oakwood Road' / 'Oakwood Lane' come from one word
    'OAKWOOD' on the sheet, so they count as one road)."""
    roads = []
    for name in street_names:
        oids = index.oids_for(name)
        if not oids:
            continue
        roads.append({"names": [name], "oids": oids, "routes": index.routes_of(oids),
                      "cores": {street_core(name)}})
    merged = True
    while merged:  # keep merging until nothing changes
        merged = False
        for i in range(len(roads)):
            for j in range(i + 1, len(roads)):
                a, b = roads[i], roads[j]
                if a["oids"] & b["oids"] or a["routes"] & b["routes"] or a["cores"] & b["cores"]:
                    a["names"] += b["names"]
                    a["cores"] |= b["cores"]
                    a["oids"] |= b["oids"]
                    a["routes"] |= b["routes"]
                    del roads[j]
                    merged = True
                    break
            if merged:
                break
    for r in roads:
        r["label"] = " / ".join(r["names"])
    return roads


def crossings(road_a, road_b, index):
    """Points where two roads cross or meet."""
    ga, gb = index.geometry(road_a["oids"]), index.geometry(road_b["oids"])
    if ga.disjoint(gb):
        return []
    hit = ga.intersect(gb, 1)  # 1 = return points
    pts = [arcpy.PointGeometry(p, index.spatial_reference) for p in hit] if hit else []
    # A road crossing at a divided highway or a bent segment gives several
    # points a few feet apart. Keep one per spot.
    kept = []
    for p in pts:
        if all(p.distanceTo(q) > 150 for q in kept):
            kept.append(p)
    return kept


def _average(points, sr):
    x = sum(p.firstPoint.X for p in points) / len(points)
    y = sum(p.firstPoint.Y for p in points) / len(points)
    return arcpy.PointGeometry(arcpy.Point(x, y), sr)


def locate_sheet(street_names, index, anchor=None, hint=None, near_ft=NEAR_FT):
    """Suggest a location for one sheet from the streets found on it.

    anchor: a PointGeometry for the project's focus (e.g. the bridge). Only
            used when a sheet has just one usable road: the point goes on
            that road where it comes closest to the anchor. Without an
            anchor, the middle of the search area is used.
    hint:   a rough PointGeometry from another clue (see hints.py). Used to
            pick between equally good crossings. (For one-road sheets, pass
            the hint as the anchor instead.)

    Returns a dict:
      point        arcpy PointGeometry, or None if no location
      method       'crossing' (two or more roads meet there), 'one_road'
                   (only one usable road), or 'none'
      confidence   'high' / 'medium' / 'low' / ''
      used         road labels that agree with the location
      ignored      road labels that don't (too far away)
      n_crossings  how many crossings were averaged
      note         plain-language explanation, e.g. a possible second spot
    """
    roads = group_roads(street_names, index)
    result = {"point": None, "method": "none", "confidence": "", "used": [],
              "ignored": [r["label"] for r in roads], "n_crossings": 0,
              "note": "no recognized streets" if not roads else ""}
    if not roads:
        return result

    # Every crossing between two of the sheet's roads is a candidate spot.
    candidates = []  # (point, i, j)
    for i in range(len(roads)):
        for j in range(i + 1, len(roads)):
            for p in crossings(roads[i], roads[j], index):
                candidates.append((p, i, j))

    if not candidates:
        # Only one road (or roads that never meet): weak. Put the point on the
        # longest road, at its spot nearest the project's focus (anchor).
        road = max(roads, key=lambda r: index.geometry(r["oids"]).length)
        geom = index.geometry(road["oids"])
        snapped = geom.queryPointAndDistance(anchor or index.center)[0]
        result.update(point=snapped, method="one_road", confidence="low",
                      used=[road["label"]],
                      ignored=[r["label"] for r in roads if r is not road],
                      note="no two streets on this sheet meet; placed on the road nearest the project center (a guess)")
        return result

    # Score each candidate: how many of the sheet's roads pass near it.
    road_geoms = [index.geometry(r["oids"]) for r in roads]

    def near_roads(p):
        return {k for k, g in enumerate(road_geoms) if g.distanceTo(p) <= near_ft}

    scored = []
    for p, i, j in candidates:
        near = near_roads(p)
        # crossing roads near this spot (stronger evidence than just passing by)
        crossing_roads = {k for q, a, b in candidates if q.distanceTo(p) <= near_ft for k in (a, b)}
        scored.append((len(near), len(crossing_roads), p, near))
    scored.sort(key=lambda s: (s[0], s[1]), reverse=True)
    tied = [s for s in scored if s[0] == scored[0][0] and s[1] == scored[0][1]]
    # If several spots score the same, a hint (grid number, stationing,
    # neighbouring pages) picks between them.
    best = min(tied, key=lambda s: s[2].distanceTo(hint)) if hint else tied[0]
    best_score, best_cross, best_p, best_near = best

    # Average all crossings near the winner whose two roads both agree.
    cluster = [p for p, i, j in candidates
               if p.distanceTo(best_p) <= near_ft and i in best_near and j in best_near]
    point = _average(cluster, index.spatial_reference)

    # Is there another, equally good spot somewhere else?
    rivals = [s for s in tied if s[2].distanceTo(best_p) > RIVAL_FT]

    used = [roads[k]["label"] for k in sorted(best_near)]
    ignored = [roads[k]["label"] for k in range(len(roads)) if k not in best_near]
    if rivals and hint:
        confidence = "medium"
        note = "{} equally good spots; picked the one nearest the hint".format(len(rivals) + 1)
        result["tie_broken"] = True
    elif rivals:
        confidence = "low"
        far = rivals[0][2].distanceTo(best_p)
        note = "another spot {:,.0f} ft away fits equally well".format(far)
    elif best_score >= 3:
        confidence, note = "high", ""
    else:
        confidence, note = "medium", "only two streets agree"
    result.update(point=point, method="crossing", confidence=confidence,
                  used=used, ignored=ignored, n_crossings=len(cluster), note=note)
    return result


def project_anchor(points, sr):
    """The project's focus: the median of the confidently located sheets
    (median, so a few far-off sheets don't drag it). None if there are none."""
    if not points:
        return None
    xs = sorted(p.firstPoint.X for p in points)
    ys = sorted(p.firstPoint.Y for p in points)
    mid = len(xs) // 2
    return arcpy.PointGeometry(arcpy.Point(xs[mid], ys[mid]), sr)
