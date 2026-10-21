"""
Extra clues for sheets that street names alone can't place well.

Street crossings (locate.py) place most sheets. These clues help the rest:
sheets with only one usable street, sheets with no streets, and sheets
where two spots fit equally well. Each clue is LEARNED from the sheets
that were already located by street crossings, so nothing is typed in.

  grid        Utility sheets often carry grid/map numbers like 3021.09. A
              sheet lists its neighbours' numbers at the match lines (sheet
              3021.09 shows 3021.08 and 3021.10), so the middle number is the
              sheet's own. Within one series (3021.xx) the numbers run along the
              street, so known sheets let us fill in the gaps between them.
  stationing  Design stationing along a route (config.STATIONING_ROUTE;
              metric, e.g. 4+840) read by Tesseract. Position along the
              route = offset + station, where
              the offset (and direction) is learned from located sheets.
              The vision model's stations are NOT used: it tends to repeat
              the examples in its instructions (e.g. "STA 12+50").
  neighbours  Pages next to each other in the same PDF usually show
              neighbouring areas.

Main class: Hints(good_sheets, route_line).predict(sheet)
A "sheet" here is a dict with keys: id, source, page, grids, station, point
(point only for good sheets).
"""

import re
import statistics

import arcpy

FT_PER_M = 3.28083

GRID_RE = re.compile(r"(?<![\d.])(\d{4})\s?[.,]\s?(\d{2})(?!\d)")
# Metric stationing: 4+840 or 4+840.25. Not 'D68+58.77' (a sewer line's own
# stationing) or '62+54.0' (old English stationing on another baseline).
STATION_RE = re.compile(r"(?<![A-Za-z\d+])(\d{1,2})\s?\+\s?(\d{3})(?:\.\d+)?(?![\d+])")

# Grid numbers on one sheet that are far from the others are a different
# index (e.g. 3021.68 next to 3021.11-.13) and are ignored.
GRID_OUTLIER = 5
# How far beyond the known grid numbers / stations we dare to extend.
GRID_EXTEND = 2      # grid steps
STATION_EXTEND = 500  # metres
# The known sheets must agree on where the stationing starts within this
# many feet (typical). Their points are street crossings, not sheet
# centres, so a few hundred feet of disagreement is normal.
STATION_AGREE_FT = 750


# ---------------------------------------------------------------------------
# Reading the clues off a page (from step 05's JSON)
# ---------------------------------------------------------------------------

def grid_numbers(result):
    """{'3021': 9.0, ...}: each grid series on the sheet and the sheet's own
    position in it (middle of the numbers shown)."""
    v = result.get("vision") or {}
    text = list(result.get("lines", [])) + v.get("stations", []) + \
        v.get("house_numbers", []) + [v.get("date") or ""]
    found = {}
    for t in text:
        for m in GRID_RE.finditer(t):
            found.setdefault(m.group(1), set()).add(int(m.group(2)))
    out = {}
    for prefix, nums in found.items():
        # keep the biggest group of numbers close to each other
        centre = max(sorted(nums), key=lambda n: sum(abs(m - n) <= GRID_OUTLIER for m in nums))
        nums = [n for n in nums if abs(n - centre) <= GRID_OUTLIER]
        out[prefix] = (min(nums) + max(nums)) / 2
    return out


def raw_stations(result):
    """Metric stations (in metres) Tesseract read on the sheet."""
    out = []
    for line in result.get("lines", []):
        for m in STATION_RE.finditer(line):
            out.append(int(m.group(1)) * 1000 + int(m.group(2)))
    return out


def sheet_station(stations, main_range):
    """The sheet's typical station along the route: median of its stations that
    fall in the project's main stationing range (side-road stations such as
    0+077 are left out). None if there are none."""
    lo, hi = main_range
    keep = [s for s in stations if lo <= s <= hi]
    return statistics.median(keep) if keep else None


def main_station_range(all_stations, half_width=1500):
    """The project's mainline stationing: around the median of every station
    read on every sheet. Side roads restart at 0+000 and are less common."""
    if not all_stations:
        return None
    mid = statistics.median(all_stations)
    return (mid - half_width, mid + half_width)


def route_line(streets_fc, route):
    """One continuous line for a numbered route (e.g. '31'), for measuring
    distance along it. None if the route isn't in the street data."""
    layer = arcpy.management.MakeFeatureLayer(
        streets_fc, "route_" + route, "route = '{}'".format(route))[0]
    out = r"memory\route_" + route
    if arcpy.Exists(out):
        arcpy.management.Delete(out)
    arcpy.management.Dissolve(layer, out, None, None, "SINGLE_PART", "UNSPLIT_LINES")
    lines = [r[0] for r in arcpy.da.SearchCursor(out, ["SHAPE@"])]
    return max(lines, key=lambda g: g.length) if lines else None


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _point(x, y, sr):
    return arcpy.PointGeometry(arcpy.Point(x, y), sr)


def _interp(s, known):
    """Straight-line interpolation of (x, y) at position s from a sorted
    list of (s, x, y). Beyond the ends, extends the end segment."""
    if s <= known[0][0]:
        a, b = known[0], known[1]
    elif s >= known[-1][0]:
        a, b = known[-2], known[-1]
    else:
        for a, b in zip(known, known[1:]):
            if a[0] <= s <= b[0]:
                break
    t = (s - a[0]) / (b[0] - a[0])
    return a[1] + t * (b[1] - a[1]), a[2] + t * (b[2] - a[2])


# ---------------------------------------------------------------------------
# The clues
# ---------------------------------------------------------------------------

class Hints:
    """Learns the three clues from well-located sheets and predicts a rough
    location for any sheet. Every prediction leaves the sheet itself out of
    the learning, so the same code can honestly test accuracy on sheets
    whose location we already know."""

    ORDER = ["grid", "stationing", "neighbours"]

    def __init__(self, good_sheets, route, sr):
        self.good = good_sheets
        self.route = route
        self.sr = sr
        self.order = list(self.ORDER)  # set_order() changes this after testing

    def _others(self, sheet):
        return [g for g in self.good if g["id"] != sheet["id"]]

    def grid(self, sheet):
        votes = []
        for prefix, s in sheet["grids"].items():
            by_pos = {}
            for g in self._others(sheet):
                if prefix in g["grids"]:
                    by_pos.setdefault(g["grids"][prefix], []).append(g["point"].firstPoint)
            if len(by_pos) < 2:
                continue  # need two known sheets in this series
            known = sorted((pos, statistics.mean(p.X for p in pts), statistics.mean(p.Y for p in pts))
                           for pos, pts in by_pos.items())
            if not known[0][0] - GRID_EXTEND <= s <= known[-1][0] + GRID_EXTEND:
                continue
            x, y = _interp(s, known)
            votes.append((x, y, "{}.{:02d}".format(prefix, int(s))))
        if not votes:
            return None
        x = statistics.mean(v[0] for v in votes)
        y = statistics.mean(v[1] for v in votes)
        return _point(x, y, self.sr), "grid " + ", ".join(v[2] for v in votes)

    def stationing(self, sheet):
        if self.route is None or sheet["station"] is None:
            return None
        known = [(g["station"], self.route.queryPointAndDistance(g["point"])[1])
                 for g in self._others(sheet) if g["station"] is not None]
        if len(known) < 2 or max(k[0] for k in known) - min(k[0] for k in known) < 100:
            return None  # too few (or too close together) to learn from
        # Which way do stations run along the line? Look at every pair of
        # sheets: does the higher station sit farther along? A real pair is
        # about 3.3 ft along the road per metre of station; pairs far from
        # that involve a misplaced sheet and don't get a vote.
        slopes = [(mb - ma) / (sb - sa) for i, (sa, ma) in enumerate(known)
                  for sb, mb in known[i + 1:] if abs(sb - sa) >= 100]
        slopes = [k for k in slopes if 0.5 * FT_PER_M <= abs(k) <= 2 * FT_PER_M]
        if not slopes:
            return None
        sign = 1 if statistics.median(slopes) > 0 else -1
        offset = statistics.median(m - sign * s * FT_PER_M for s, m in known)
        miss = statistics.median(abs(m - offset - sign * s * FT_PER_M) for s, m in known)
        if miss > STATION_AGREE_FT:
            return None  # the known sheets don't agree; don't trust it
        lo, hi = min(k[0] for k in known), max(k[0] for k in known)
        if not lo - STATION_EXTEND <= sheet["station"] <= hi + STATION_EXTEND:
            return None
        along = offset + sign * sheet["station"] * FT_PER_M
        along = min(max(along, 0), self.route.length)
        km, m = divmod(int(round(sheet["station"])), 1000)
        return self.route.positionAlongLine(along), "station {}+{:03d}".format(km, m)

    def neighbours(self, sheet):
        near = [(abs(g["page"] - sheet["page"]), g) for g in self._others(sheet)
                if g["source"] == sheet["source"] and 0 < abs(g["page"] - sheet["page"]) <= 2]
        if not near:
            return None
        w = [1.0 / d for d, _ in near]
        x = sum(wi * g["point"].firstPoint.X for wi, (_, g) in zip(w, near)) / sum(w)
        y = sum(wi * g["point"].firstPoint.Y for wi, (_, g) in zip(w, near)) / sum(w)
        pages = ", ".join(str(g["page"]) for _, g in sorted(near, key=lambda n: n[1]["page"]))
        return _point(x, y, self.sr), "neighbouring page(s) " + pages

    def set_order(self, names):
        """Try clues in this order (best-tested first)."""
        self.order = list(names)

    def predict(self, sheet):
        """Best available clue: (point, description, clue name), or None."""
        for name in self.order:
            got = getattr(self, name)(sheet)
            if got:
                return got[0], got[1], name
        return None

    def check_accuracy(self):
        """For every well-located sheet, predict it from each clue separately
        (leaving it out) and measure the miss in feet.
        Returns {clue name: [(sheet id, miss in ft), ...]}."""
        out = {name: [] for name in self.ORDER}
        for g in self.good:
            for name in self.ORDER:
                got = getattr(self, name)(g)
                if got:
                    out[name].append((g["id"], got[0].distanceTo(g["point"])))
        return out
