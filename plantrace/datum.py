"""
Datum, coordinates and file properties: what a plan sheet says about WHERE
it is in coordinates, and what the PDF says about how it was made.

  file_properties()    the PDF's own properties: which program made it
                       (scanner, CAD, GIS, email print...), when, and by whom.
  datum_statements()   datum notes written on the sheet: NAD 27 / NAD 83,
                       State Plane or UTM, NGVD 29 / NAVD 88, benchmarks.
  coordinate_labels()  grid-tick and coordinate labels ("N 1,050,000",
                       "E 655,250") with where they are on the page.
  place_from_coordinates()
                       if a sheet has enough coordinate labels, work out its
                       position directly from them. The labels' coordinate
                       system is often not written, so each candidate system
                       (e.g. NAD 27 and NAD 83 State Plane zones) is tried;
                       ArcGIS converts between datums (NAD 27 -> NAD 83 uses
                       the official NADCON grids). The one that puts the
                       sheet where its streets are is the datum.

Nothing here guesses history: a datum is only reported if it is written on
the sheet, stored in the PDF, or proven by the coordinates landing in the
right place.
"""

import math
import re

import arcpy
import numpy as np

# ---------------------------------------------------------------------------
# File properties
# ---------------------------------------------------------------------------

# (pattern in the PDF's Creator/Producer, what kind of document that means)
MAKERS = [
    (r"paper capture|image conversion|imagemill|scan|xerox|canon|ricoh|kodak|fujitsu|epson|sharp|konica|"
     r"ocr", "scan"),
    (r"autocad|microstation|civil ?3d|pdfplot|\.hdi|bentley|openroads|carlson", "CAD plot"),
    (r"arcgis|arcmap|arcsoc|esri|qgis|geopdf|global mapper|mapinfo", "GIS export"),
    (r"outlook|word|excel|print to pdf|mozilla|chrome|skia|edge|firefox", "email or office print"),
    (r"bluebeam|acrobat|distiller|pscript", "PDF tool (unknown source)"),
]


def _pdf_date(text):
    """'D:20251223143219-05'00'' -> '2025-12-23'."""
    m = re.match(r"D?:?(\d{4})(\d{2})?(\d{2})?", text or "")
    if not m:
        return ""
    return "-".join(p for p in m.groups() if p)


def file_properties(pdf_path):
    """The PDF's own properties. Returns a dict with program, kind, created,
    author, title. Empty values when the PDF doesn't say."""
    from osgeo import gdal
    try:
        md = gdal.Open(pdf_path).GetMetadata() or {}
    except Exception:
        md = {}
    program = " / ".join(v for v in (md.get("CREATOR", ""), md.get("PRODUCER", "")) if v)
    kind = ""
    for pattern, label in MAKERS:
        if re.search(pattern, program, re.I):
            kind = label
            break
    return {"program": program[:120], "kind": kind or ("unknown" if program else ""),
            "created": _pdf_date(md.get("CREATION_DATE", "")),
            "author": md.get("AUTHOR", "")[:80], "title": md.get("TITLE", "")[:80]}


# ---------------------------------------------------------------------------
# Datum notes written on the sheet
# ---------------------------------------------------------------------------

HORIZONTAL = [
    (r"\bNAD\s*-?\s*(?:19)?27\b|NORTH AMERICAN DATUM (?:OF )?1927", "NAD 27"),
    (r"\bNAD\s*-?\s*(?:19)?83\b|NORTH AMERICAN DATUM (?:OF )?1983", "NAD 83"),
    (r"\bWGS\s*-?\s*84\b", "WGS 84"),
]
VERTICAL = [
    (r"\bNGVD\s*-?\s*(?:19)?29\b|\bNGVD\b|SEA LEVEL DATUM (?:OF )?1929", "NGVD 29"),
    (r"\bNAVD\s*-?\s*(?:19)?88\b|\bNAVD\b", "NAVD 88"),
    (r"ASSUMED (?:ELEV|DATUM)|LOCAL DATUM", "assumed / local"),
]
SYSTEM = [
    (r"UTM\s*ZONE\s*(\d{1,2})\s*N?", "UTM zone {0}N"),
    (r"STATE\s*PLANE", "State Plane"),
    (r"NEW YORK\s*(WEST|CENTRAL|EAST|LONG ISLAND)\s*(?:ZONE)?", "New York {0} zone"),
    (r"\b(US|U\.S\.)\s*(SURVEY\s*)?FEET|\bUS\s*FT\b", "US feet"),
]
BENCHMARK = r"\bB\.?\s?M\.?\s*(?:NO\.?\s*)?[A-Z]?-?\d+|\bBENCH\s*MARK\b"


def datum_statements(lines):
    """Datum and coordinate-system notes in the text read off a sheet.
    Returns {"horizontal": [...], "vertical": [...], "system": [...],
    "benchmarks": n, "evidence": [the lines they came from]}."""
    out = {"horizontal": [], "vertical": [], "system": [], "benchmarks": 0, "evidence": []}
    for line in lines:
        text = line.upper()
        hit = False
        for key, table in (("horizontal", HORIZONTAL), ("vertical", VERTICAL)):
            for pattern, label in table:
                if re.search(pattern, text) and label not in out[key]:
                    out[key].append(label)
                    hit = True
        for pattern, label in SYSTEM:
            m = re.search(pattern, text)
            if m:
                name = label.format(*(g.title() if g else "" for g in m.groups()))
                if name not in out["system"]:
                    out["system"].append(name)
                hit = True
        if re.search(BENCHMARK, text):
            out["benchmarks"] += 1
            hit = True
        if hit and line.strip() not in out["evidence"]:
            out["evidence"].append(line.strip()[:100])
    return out


# ---------------------------------------------------------------------------
# Coordinate labels on the page
# ---------------------------------------------------------------------------

# A big number written with thousands separators: 655,250 / 1,050,000.00
BIG_NUMBER = re.compile(r"^[NE]?\.?:?(\d{1,3}(?:,\d{3}){1,2}(?:\.\d+)?)$")
PREFIX = re.compile(r"^(N|E|NORTH|EAST|N\.|E\.|N:|E:)$")


def has_coordinate_numbers(lines):
    """Quick check on a page's text: any coordinate-like numbers at all?"""
    return sum(1 for l in lines for t in l.replace("=", " ").split()
               if BIG_NUMBER.match(t.strip().upper()) and _value(t) >= 100_000) >= 2


def _value(token):
    m = BIG_NUMBER.match(token.strip().upper())
    return float(m.group(1).replace(",", "")) if m else 0.0


def coordinate_labels(words, width, height, min_value=100_000):
    """Coordinate labels with their position on the page.

    words: [text, x, y] (full-size pixels, y down), from read.word_positions.
    Returns a list of (kind, value, u, v): kind is 'N', 'E' or '?' (a bare
    number with no N/E next to it); u, v are pixels from the page centre,
    v up (like place.PlanLines)."""
    cx, cy = (width - 1) / 2.0, (height - 1) / 2.0
    out = []
    for i, (text, x, y) in enumerate(words):
        t = text.strip().upper()
        if not BIG_NUMBER.match(t):
            continue
        value = _value(t)
        if value < min_value:
            continue
        kind = t[0] if t[0] in "NE" else "?"
        if kind == "?" and i > 0:  # 'N' / 'E' written as its own word just before
            prev, px, py = words[i - 1]
            if PREFIX.match(prev.strip().upper()) and math.hypot(px - x, py - y) < 400:
                kind = prev.strip().upper()[0]
        out.append((kind, value, x - cx, -(y - cy)))
    # the same label read in two passes: keep one
    unique = []
    for lab in out:
        if not any(lab[1] == o[1] and math.hypot(lab[2] - o[2], lab[3] - o[3]) < 60 for o in unique):
            unique.append(lab)
    return unique


def _assignments(labels):
    """Ways to read the labels as northings and eastings. Labelled ones are
    fixed; bare numbers are split into two groups by size (northings and
    eastings in one system are usually far apart), both ways round."""
    bare = [l for l in labels if l[0] == "?"]
    fixed = [l for l in labels if l[0] != "?"]
    if not bare:
        return [labels]
    vals = sorted(l[1] for l in bare)
    gaps = [(vals[i + 1] - vals[i], i) for i in range(len(vals) - 1)]
    if not gaps:
        return [fixed + [("N",) + bare[0][1:]], fixed + [("E",) + bare[0][1:]]]
    split = vals[max(gaps)[1]]
    low = [l for l in bare if l[1] <= split]
    high = [l for l in bare if l[1] > split]
    a = fixed + [("E",) + l[1:] for l in low] + [("N",) + l[1:] for l in high]
    b = fixed + [("N",) + l[1:] for l in low] + [("E",) + l[1:] for l in high]
    return [a, b]


def _fit(labels):
    """Solve the sheet's position from N/E labels (least squares).
    Map = scale * rotation * page + shift:
        E = a*u - b*v + tx      N = b*u + a*v + ty
    Returns ((a, b, tx, ty), rms error in map units) or None."""
    n = [l for l in labels if l[0] == "N"]
    e = [l for l in labels if l[0] == "E"]
    if len(n) < 1 or len(e) < 1 or len(labels) < 4:
        return None
    rows, rhs = [], []
    for kind, value, u, v in labels:
        if kind == "E":
            rows.append([u, -v, 1, 0])
        else:
            rows.append([v, u, 0, 1])
        rhs.append(value)
    A, y = np.array(rows, float), np.array(rhs, float)
    sol, *_ = np.linalg.lstsq(A, y, rcond=None)
    rms = float(np.sqrt(np.mean((A @ sol - y) ** 2)))
    return tuple(sol), rms


def _world_file(points_from, points_to):
    """World-file numbers from 3 page points (col, row) and their map spots."""
    (c0, r0), (c1, r1), (c2, r2) = points_from
    (x0, y0), (x1, y1), (x2, y2) = points_to
    M = np.array([[c0, r0, 1], [c1, r1, 1], [c2, r2, 1]], float)
    ax, bx, cx = np.linalg.solve(M, [x0, x1, x2])
    ay, by, cy = np.linalg.solve(M, [y0, y1, y2])
    return [ax, ay, bx, by, cx, cy]


def place_from_coordinates(labels, width, height, candidates, project_wkid, search_box,
                           near_xy=None, dpi=300):
    """Try to place a sheet from its coordinate labels.

    candidates : [(wkid, name), ...] coordinate systems the labels might be in
    search_box : (xmin, ymin, xmax, ymax) of the project area, project units
    near_xy    : where step 06 thinks the sheet is (optional, for ranking)
    Returns a list of results, best first. Each: {"system", "wkid",
    "world_file" (project system), "ft_per_in", "rotation_deg",
    "fit_rms", "inside_area", "dist_to_suggested_ft"}."""
    project_sr = arcpy.SpatialReference(project_wkid)
    results = []
    for labs in _assignments(labels):
        fitted = _fit(labs)
        if not fitted:
            continue
        (a, b, tx, ty), rms = fitted
        scale = math.hypot(a, b)
        if scale <= 0:
            continue
        cx, cy = (width - 1) / 2.0, (height - 1) / 2.0
        page_pts = [(0, 0), (width - 1, 0), (0, height - 1), (cx, cy)]
        for wkid, name in candidates:
            sr = arcpy.SpatialReference(wkid)
            unit = sr.metersPerUnit / project_sr.metersPerUnit  # candidate units -> project units
            transforms = arcpy.ListTransformations(sr, project_sr)
            mapped = []
            for col, row in page_pts:
                u, v = col - cx, -(row - cy)
                pt = arcpy.PointGeometry(arcpy.Point(a * u - b * v + tx, b * u + a * v + ty), sr)
                pt = pt.projectAs(project_sr, transforms[0]) if transforms else pt.projectAs(project_sr)
                mapped.append((pt.firstPoint.X, pt.firstPoint.Y))
            wf = _world_file(page_pts[:3], mapped[:3])
            x, y = mapped[3]
            inside = search_box[0] <= x <= search_box[2] and search_box[1] <= y <= search_box[3]
            dist = math.hypot(x - near_xy[0], y - near_xy[1]) if near_xy else None
            ft_px = math.hypot(wf[0], wf[1]) * (project_sr.metersPerUnit / 0.3048006096)
            results.append({"system": name, "wkid": wkid, "world_file": wf,
                            "ft_per_in": ft_px * dpi, "rotation_deg": math.degrees(math.atan2(wf[1], wf[0])),
                            "fit_rms": rms * unit, "inside_area": inside,
                            "dist_to_suggested_ft": dist, "labels_used": len(labs)})
    results.sort(key=lambda r: (not r["inside_area"],
                                r["dist_to_suggested_ft"] if r["dist_to_suggested_ft"] is not None else 0,
                                r["fit_rms"]))
    return results
