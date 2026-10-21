r"""
Step 7: Place each plan sheet on the map automatically (scale + rotation).

For every sheet that step 06 located, it:
  - uses the sheet's own position if the PDF is georeferenced (exact)
  - otherwise skips web-map screenshots on aerial photos (nothing to fit)
  - finds the long lines on the scan (road edges, curbs, ROW lines)
  - turns, scales and slides them over the street centerlines around the
    suggested location until they line up best (see plantrace\place.py),
    using street-name labels to tell which way round the sheet goes and
    station labels (if any) for the exact scale
  - fine-tunes that against the aerial photo: the plan's curbs and edges
    onto the real ones (see plantrace\refine.py)
  - writes the position INSIDE the image and as a WORLD FILE next to it
    (<image>.tfw), identically, so the image opens in ArcGIS Pro already
    in place. You only check and fine-tune it.

Results:
  staged_plans\<image>.tfw            the world file (position, scale, rotation)
  staged_plans\<image>.plantrace.json what this step did (so it knows its own
                                      world files from yours)
  PlanFootprints   polygon layer: the outline of each placed sheet, with
                   place_status, match_score, scale and rotation. Add it to
                   your map to see at a glance where every sheet landed.
  placement_check\<plan_id>.png       picture per sheet: the real aerial photo
                   with the plan's ink (magenta) on top, street centerlines
                   (yellow) and the sheet outline (blue). Needs internet for
                   the aerial (grey background without). If the plan's curbs
                   sit on the real curbs, the placement is right.
  plan_manifest.csv  new columns: place_status, place_score, place_scale_ft_in,
                     place_rotation

place_status:
  good    lines fit well and clearly better than any other way of placing it
  check   a reasonable fit, but look at it (weak or a close second answer)
  poor    no convincing fit; place it by hand
  kept    the image already has YOUR georeferencing; left alone
  photo map  a web-map screenshot on an aerial photo: no drawn lines to
          fit, so no world file is written; place it by hand

YOUR GEOREFERENCING IS NEVER OVERWRITTEN. A world file is only replaced if
this step wrote it and it hasn't been changed since. If you georeference a
sheet in ArcGIS Pro, this step leaves it alone from then on.

Slow-ish: about 20-40 seconds per sheet. Close ArcGIS Pro first (memory), or
at least remove the plan images from your map.

Options:
  python scripts\07_place_plans.py              place every located sheet
  python scripts\07_place_plans.py --only GAS     only sheets whose plan_id contains "GAS"
  python scripts\07_place_plans.py --no-pictures  skip the check pictures (a bit faster)
  python scripts\07_place_plans.py --resume     skip sheets already placed (after a stopped run)
  python scripts\07_place_plans.py --footprints-only   just redraw PlanFootprints (quick)
  python scripts\07_place_plans.py --refine-only       fine-tune existing placements against the aerial
  python scripts\07_place_plans.py --pictures-only     just redraw the check pictures
"""

import csv
import difflib
import glob
import json
import math
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np

import config
import arcpy
from PIL import Image
from plantrace import georef, place, read, refine, review

FOOTPRINTS_FC_NAME = "PlanFootprints"
CHECK_DIR = os.path.join(config.PROJECT_DIR, "placement_check")
NEW_COLUMNS = ["place_status", "place_score", "place_scale_ft_in", "place_rotation"]

# How far (ft) the page centre may be from step 06's point, by its confidence.
SEARCH_RADIUS_FT = {"high": 1500, "medium": 2500, "low": 3500}
# ...or this close to where the sheet's own stationing puts it on the route.
STATION_RADIUS_FT = 600
# A page is a web-map screenshot on an aerial photo if more than this share
# of it is coloured (plans are black/grey lines on white or grey paper).
PHOTO_SHARE = 0.25
# Common engineering scales (feet per inch), tried when no scale was read.
COMMON_SCALES = [20, 30, 40, 50, 60, 80, 100, 150, 200, 300, 400]
# With a stated scale, try it as is and for a half-size print (2x). The fine
# search then adjusts it by up to +-8%. These PDFs are full-size prints, so
# the stated scale is trusted: the lines alone tend to pull the scale off
# on busy sheets (lettering, title blocks).
SCALE_FACTORS = [1.0, 2.0]
# Thresholds for place_status. The line score is how much better the sheet's
# lines sit on matching streets than random lines would (0 = no better).
# The margin is how far ahead of the next-best, different placement it is.
GOOD_SCORE, GOOD_MARGIN = 0.35, 0.08
CHECK_SCORE = 0.20
# Words in street names that say nothing about WHICH street it is.
PLAIN_WORDS = {"ROAD", "DRIVE", "LANE", "STREET", "AVENUE", "COURT", "CIRCLE",
               "TRAIL", "PLACE", "BOULEVARD", "PARKWAY", "TERRACE", "INTERSTATE",
               "ROUTE", "EASTBOUND", "WESTBOUND", "NORTH", "SOUTH", "EAST", "WEST"}
# How highways are labelled on plans, besides their names.
HIGHWAY_WORDS = getattr(config, "HIGHWAY_LABEL_WORDS", {})
# Place names that must not count as street labels (e.g. the county).
EXTRA_PLACES = {w.upper() for w in getattr(config, "EXTRA_PLACE_WORDS", [])}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def load_manifest():
    with open(config.MANIFEST_PATH, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        return list(reader), list(reader.fieldnames)


def save_manifest(rows, columns):
    for c in NEW_COLUMNS:
        if c not in columns:
            columns.append(c)
    tmp = config.MANIFEST_PATH + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, config.MANIFEST_PATH)


def street_segments():
    """Every straight piece of every RefStreets centerline: all of them
    together, plus grouped by street name and by route number."""
    fc = os.path.join(config.GDB_PATH, config.STREETS_FC_NAME)
    segs, by_name, by_route, towns = [], {}, {}, set()
    for shape, name, route, town in arcpy.da.SearchCursor(
            fc, ["SHAPE@", "street_name", "route", "town"]):
        if town:
            towns.add(town.upper())
        if shape is None:
            continue
        for part in shape:
            pts = [(p.X, p.Y) for p in part if p]
            pieces = [(a[0], a[1], b[0], b[1]) for a, b in zip(pts, pts[1:])]
            segs += pieces
            by_name.setdefault(name or "", []).extend(pieces)
            if route:
                by_route.setdefault(route.strip(), []).extend(pieces)
    return place.StreetSegments(segs), by_name, by_route, towns


def sheet_labels(row, words, lines, by_name, by_route, towns):
    """Street names printed on the sheet (from the word positions), for the
    streets step 06 used to locate it. Returns place.Labels."""
    cx, cy = (lines.width_px - 1) / 2.0, (lines.height_px - 1) / 2.0
    items = []
    for group in (row.get("loc_streets") or "").split("; "):
        if not group:
            continue
        names = group.split(" / ")
        segs = []
        keywords = set()
        for name in names:
            if name.startswith("Route ") or name.startswith("Interstate "):
                segs += by_route.get(name.split()[-1], [])
            else:
                segs += by_name.get(name, [])
            for w in re.sub(r"[^A-Z0-9 ]", " ", name.upper()).split():
                if len(w) >= 4 and w not in PLAIN_WORDS and w not in towns and w not in EXTRA_PLACES:
                    keywords.add(w)
            keywords.update(HIGHWAY_WORDS.get(name, []))
        if not segs or not keywords:
            continue
        segs = np.array(segs)
        for text, x, y in words:
            t = re.sub(r"[^A-Z0-9]", "", text.upper())
            if len(t) < 3:
                continue
            if any(t == k or (len(k) >= 5 and difflib.SequenceMatcher(None, t, k).ratio() >= 0.85)
                   for k in keywords):
                items.append((x - cx, -(y - cy), group, segs))
    return place.Labels(items)


def load_words(pid, image, log):
    """Word positions on the page (cached in plan_text\\<plan_id>.words.json)."""
    path = os.path.join(config.PLAN_TEXT_DIR, pid + ".words.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    log("     reading where the street names are on the sheet (one time, ~1 min)...")
    words = read.word_positions(image, config.TESSERACT_EXE)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(words, f)
    os.replace(path + ".tmp", path)
    return words


SCALE_RES = [
    re.compile(r"1\s*(?:\"|”|''|IN(?:CH)?)\s*=\s*(\d{1,4})\s*(?:'|FT|FEET|’|-)", re.I),
    re.compile(r"1\s*(?:\"|”|IN(?:CH)?)\s*=\s*(\d{1,4})\b", re.I),
]


def stated_scales(row, result):
    """Feet-per-inch values written on the sheet ('1"=30'', '1 inch = 50 feet')."""
    texts = [row.get("scale_found", "")] + list(result.get("scale", []))
    v = result.get("vision") or {}
    if v.get("scale"):
        texts.append(v["scale"])
    found = []
    for t in texts:
        for rx in SCALE_RES:
            for m in rx.findall(t.replace(" ", " ")):
                ft = int(m)
                if 5 <= ft <= 1000 and ft not in found:
                    found.append(ft)
    return found


FT_PER_M = 3.28083
STATION_WORD = re.compile(r"(?:STA\.?)?(\d)\+(\d{3})(?:[.,]\d+)?")


def station_labels(words, lines):
    """Metric station labels on the page (e.g. '4+560') with their position,
    as (metres, u, v) in full-resolution pixels from the page centre, v up.
    Side-road stations (0+xxx) are left out."""
    cx, cy = (lines.width_px - 1) / 2.0, (lines.height_px - 1) / 2.0
    out = []
    for text, x, y in words:
        m = STATION_WORD.fullmatch(text.strip().upper())
        if m:
            metres = int(m.group(1)) * 1000 + int(m.group(2))
            if metres >= 1000:
                out.append((metres, x - cx, -(y - cy)))
    return out


def route_direction(by_route, x, y, max_ft=1000):
    """Direction (degrees, 0-180) of the stationed route nearest (x, y), or
    None if the route isn't within max_ft."""
    segs = by_route.get(config.STATIONING_ROUTE)
    if not segs:
        return None
    s = np.asarray(segs, float)
    a, b = s[:, :2], s[:, 2:]
    ab = b - a
    p = np.array([x, y])
    t = np.clip(((p - a) * ab).sum(1) / np.maximum((ab ** 2).sum(1), 1e-9), 0, 1)
    dist = np.hypot(*(a + t[:, None] * ab - p).T)
    k = int(np.argmin(dist))
    if dist[k] > max_ft:
        return None
    return math.degrees(math.atan2(ab[k, 1], ab[k, 0])) % 180


def is_photo_map(image_path):
    """True for web-map screenshots on top of aerial photos: lots of
    coloured, textured pixels instead of lines on paper. There are no
    drawn lines to fit on those, so they are left for hand placement."""
    with Image.open(image_path) as im:
        im = im.convert("RGB")
        im.thumbnail((600, 600))
        hsv = np.asarray(im.convert("HSV")).astype(np.int16)
    coloured = (hsv[..., 1] > 50) & (hsv[..., 2] > 40) & (hsv[..., 2] < 235)
    return coloured.mean() > PHOTO_SHARE


def candidate_scales(ft_per_in_list, dpi):
    """Feet per full-resolution pixel to try."""
    if ft_per_in_list:
        vals = {ft * f for ft in ft_per_in_list for f in SCALE_FACTORS}
    else:
        vals = set(COMMON_SCALES)
    return sorted(v / dpi for v in vals)


def own_world_file(image):
    """(path of the .tfw, True if it's safe for this step to write it)."""
    base, ext = os.path.splitext(image)
    tfw = base + (".tfw" if ext.lower() in (".tif", ".tiff") else ext[:2] + ext[-1] + "w")
    marker = image + ".plantrace.json"
    aux = image + ".aux.xml"
    # Georeferencing saved in ArcGIS Pro lives in the .aux.xml file.
    if os.path.exists(aux):
        with open(aux, encoding="utf-8", errors="ignore") as f:
            if "GeodataXform" in f.read():
                return tfw, False
    if not os.path.exists(tfw):
        return tfw, True
    if not os.path.exists(marker):
        return tfw, False  # a world file we didn't write
    try:
        with open(marker, encoding="utf-8") as f:
            ours = json.load(f).get("world_file_text")
    except ValueError:
        return tfw, False  # unreadable record: play safe, don't touch it
    with open(tfw, encoding="utf-8") as f:
        return tfw, f.read() == ours


def pdf_georef(row, image):
    """Exact position stored in the source PDF page, if it has one (as
    world-file numbers for the staged image), else None."""
    source = row.get("source_file", "")
    if not source.lower().endswith(".pdf"):
        return None
    hits = glob.glob(os.path.join(config.RAW_PLANS_DIR, "**", source), recursive=True)
    if not hits:
        return None
    with Image.open(image) as im:
        w, h = im.size
    return georef.pdf_page_georef(hits[0], int(row.get("page") or 1), w, h,
                                  config.SPATIAL_REFERENCE_WKID, config.PDF_DPI)


def fine_tune(lines, wf, info, free_scale=False):
    """Fine-tune a placement against the aerial photo (plantrace/refine.py).
    Returns the improved placement, or the same one if there's no clear
    improvement or the photo can't be downloaded. Notes what happened in info."""
    try:
        new, r = refine.refine(lines, wf, config.SPATIAL_REFERENCE_WKID,
                               scale_range=0.01 if free_scale else 0.0, log=print)
    except Exception as e:  # offline, service down...
        print("     aerial fine-tune skipped ({})".format(e))
        info["aerial_refine"] = "skipped: {}".format(e)
        return wf
    info["aerial_refine"] = dict(r, applied=new is not None, before_world_file=wf)
    if new is None:
        print("     aerial fine-tune: no clear improvement; kept the fit")
        return wf
    return new


def refine_existing(rows, columns, streets, sr, only, pictures):
    """Fine-tune the automatic placements already made, against the aerial,
    without redoing the main fit. Sheets placed from the PDF's own position
    and sheets you georeferenced yourself are left alone."""
    done = []
    for row in rows:
        pid = row["plan_id"]
        if only and only.lower() not in pid.lower():
            continue
        if row.get("place_status") not in ("good", "check", "poor"):
            continue
        image = os.path.join(config.STAGED_PLANS_DIR, row["staged_image"])
        tfw, ours = own_world_file(image)
        if not ours or not os.path.exists(image + ".plantrace.json"):
            continue
        with open(image + ".plantrace.json", encoding="utf-8") as f:
            rec = json.load(f)
        if isinstance(rec.get("aerial_refine"), dict) and rec["aerial_refine"].get("applied"):
            continue  # already fine-tuned
        with open(os.path.join(config.PLAN_TEXT_DIR, pid + ".json"), encoding="utf-8") as f:
            result = json.load(f)
        print(pid)
        lines = place.plan_lines(image, dpi=config.PDF_DPI, skew=result.get("skew"))
        wf = [float(v) for v in rec["world_file_text"].split()]
        new = fine_tune(lines, wf, rec, free_scale=not stated_scales(row, result))
        if new is wf:
            write_safely(image + ".plantrace.json", json.dumps(rec, indent=1))
            continue
        a, d = new[0], new[1]
        rec.update({"scale_ft_per_in": math.hypot(a, d) * lines.dpi,
                    "rotation_deg": math.degrees(math.atan2(d, a))})
        row["place_scale_ft_in"] = round(rec["scale_ft_per_in"], 1)
        row["place_rotation"] = round(rec["rotation_deg"], 1)
        write_placement(image, tfw, new, rec)
        done.append(pid)
        if pictures:
            draw_check(image, pid, new, streets, row["place_status"], rec["scale_ft_per_in"],
                       rec["rotation_deg"])
        save_manifest(rows, columns)
    rebuild_footprints(rows, sr, placed_now=set(done))
    print("\nFine-tuned {} sheet(s) against the aerial.".format(len(done)))


def write_placement(image, tfw, wf, info):
    """Save a placement: the record first (what we meant to write), then the
    position INSIDE the image and as the world file, identical, so ArcGIS
    can't pick a different one."""
    text = "".join("{:.10f}\n".format(v) for v in wf)
    info = dict(info, world_file_text=text, written=time.strftime("%Y-%m-%d %H:%M"))
    write_safely(image + ".plantrace.json", json.dumps(info, indent=1))
    georef.write_georef(image, wf, config.SPATIAL_REFERENCE_WKID)


def write_safely(path, text):
    """Write a file completely or not at all (a crash can't leave half a file)."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def status_of(best, runner):
    if best is None:
        return "poor"
    margin = best.total - runner.total if runner else 1.0
    labels = best.labels_ok
    if labels is not None and labels < 0.25:
        return "poor"  # the street names land on the wrong streets
    if best.score >= GOOD_SCORE and margin >= GOOD_MARGIN and (labels is None or labels >= 0.5):
        return "good"
    if best.score >= CHECK_SCORE:
        return "check"
    return "poor"


def short_name(plan_id):
    """Sheet name with the parts in config.SHORT_NAME_REMOVE taken out."""
    for text in getattr(config, "SHORT_NAME_REMOVE", []):
        plan_id = plan_id.replace(text, "")
    return plan_id


def draw_check(image, pid, wf, streets, status, ft_per_in, rotation):
    """Picture: the real aerial photo with the plan's ink (magenta) on top,
    street centerlines (yellow) and the sheet outline (blue)."""
    title = "{}   [{}]   1\"={:.0f}', rotated {:.1f} deg".format(
        short_name(pid), status, ft_per_in, rotation)
    review.review_picture(image, wf, streets, os.path.join(CHECK_DIR, pid + ".png"), title,
                          config.SPATIAL_REFERENCE_WKID)


def redraw_pictures(rows, streets, only=None):
    """Redraw the check pictures for every placed sheet, from the records."""
    os.makedirs(CHECK_DIR, exist_ok=True)
    for row in rows:
        if only and only.lower() not in row["plan_id"].lower():
            continue
        image = os.path.join(config.STAGED_PLANS_DIR, row.get("staged_image", ""))
        marker = image + ".plantrace.json"
        if not os.path.exists(marker):
            continue
        with open(marker, encoding="utf-8") as f:
            rec = json.load(f)
        print("   picture:", row["plan_id"])
        draw_check(image, row["plan_id"], [float(v) for v in rec["world_file_text"].split()],
                   streets, rec.get("status", ""), rec.get("scale_ft_per_in") or 0,
                   rec.get("rotation_deg") or 0)


REVIEW_DOMAIN = "PlacementReview"
REVIEW_CHOICES = [("correct", "correct: on the right spot"),
                  ("close", "close: right area, needs fine-tuning"),
                  ("wrong", "wrong: wrong place, size or rotation")]


def add_review_fields(fc):
    """Fields YOU fill in while checking: review (a dropdown) and review_note.
    Step 07 carries them over when it runs again, so your verdicts are kept."""
    gdb = os.path.dirname(fc)
    if REVIEW_DOMAIN not in [d.name for d in arcpy.da.ListDomains(gdb)]:
        arcpy.management.CreateDomain(gdb, REVIEW_DOMAIN, "Your verdict on an automatic placement",
                                      "TEXT", "CODED")
        for code, desc in REVIEW_CHOICES:
            arcpy.management.AddCodedValueToDomain(gdb, REVIEW_DOMAIN, code, desc)
    existing = [f.name for f in arcpy.ListFields(fc)]
    if "review" not in existing:
        arcpy.management.AddField(fc, "review", "TEXT", field_length=10, field_domain=REVIEW_DOMAIN)
    if "review_note" not in existing:
        arcpy.management.AddField(fc, "review_note", "TEXT", field_length=250)


def rebuild_footprints(rows, sr, placed_now):
    """Redraw PlanFootprints from the per-sheet records (<image>.plantrace.json),
    which are saved as each sheet is placed, so a stopped run loses nothing.
    Your review verdicts are carried over. Sheets you've georeferenced
    yourself are left out (their record no longer matches the world file).
    Returns the feature class path."""
    fc = os.path.join(config.GDB_PATH, FOOTPRINTS_FC_NAME)
    reviews = {}
    if arcpy.Exists(fc) and "review" in [f.name for f in arcpy.ListFields(fc)]:
        reviews = {r[0]: (r[1] or "", r[2] or "")
                   for r in arcpy.da.SearchCursor(fc, ["plan_id", "review", "review_note"])
                   if r[1] or r[2]}
    make_footprints_fc(sr)
    fields = ["SHAPE@", "plan_id", "source_file", "place_status", "match_score",
              "scale_ft_per_in", "rotation_deg", "review", "review_note"]
    with arcpy.da.InsertCursor(fc, fields) as cur:
        for row in rows:
            image = os.path.join(config.STAGED_PLANS_DIR, row.get("staged_image", ""))
            marker = image + ".plantrace.json"
            if not os.path.exists(marker):
                continue
            tfw, ours = own_world_file(image)
            if not ours or not os.path.exists(tfw):
                continue
            with open(marker, encoding="utf-8") as f:
                rec = json.load(f)
            a, d, b, e, c, f_ = [float(v) for v in rec["world_file_text"].split()]
            with Image.open(image) as im:
                w, h = im.size
            corners = [(a * col + b * r + c, d * col + e * r + f_)
                       for col, r in [(-0.5, -0.5), (w - 0.5, -0.5), (w - 0.5, h - 0.5), (-0.5, h - 0.5)]]
            poly = arcpy.Polygon(arcpy.Array([arcpy.Point(x, y) for x, y in corners]), sr)
            pid = row["plan_id"]
            review, note = reviews.get(pid, ("", ""))
            if review and pid in placed_now:  # placed again since you reviewed it
                note = ("placed again {}; re-check. ".format(time.strftime("%Y-%m-%d")) + note)[:250]
            cur.insertRow([poly, pid, row.get("source_file", ""), rec.get("status", ""),
                           round(rec.get("score") or 0, 3), round(rec.get("scale_ft_per_in") or 0, 1),
                           round(rec.get("rotation_deg") or 0, 1), review, note])
    return fc


def make_footprints_fc(sr):
    fc = os.path.join(config.GDB_PATH, FOOTPRINTS_FC_NAME)
    if arcpy.Exists(fc):
        arcpy.management.Delete(fc)
    arcpy.management.CreateFeatureclass(config.GDB_PATH, FOOTPRINTS_FC_NAME, "POLYGON",
                                        spatial_reference=sr)
    for name, length in [("plan_id", 150), ("source_file", 200), ("place_status", 10)]:
        arcpy.management.AddField(fc, name, "TEXT", field_length=length)
    for name in ["match_score", "scale_ft_per_in", "rotation_deg"]:
        arcpy.management.AddField(fc, name, "DOUBLE")
    add_review_fields(fc)
    return fc


# ---------------------------------------------------------------------------

def main():
    only = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else None
    pictures = "--no-pictures" not in sys.argv
    if not os.path.exists(config.MANIFEST_PATH):
        print("No plan_manifest.csv yet. Run steps 02-06 first.")
        sys.exit(1)
    rows, columns = load_manifest()
    if "loc_x" not in columns:
        print("No locations yet. Run step 06 first.")
        sys.exit(1)

    print("Loading street centerlines...")
    review.ORTHO_EXPORT = config.ORTHO_SERVICE_URL.rstrip("/") + "/export"
    streets, by_name, by_route, towns = street_segments()
    sr = arcpy.SpatialReference(config.SPATIAL_REFERENCE_WKID)
    if pictures:
        os.makedirs(CHECK_DIR, exist_ok=True)

    if "--refine-only" in sys.argv:  # fine-tune existing automatic placements
        refine_existing(rows, columns, streets, sr, only, pictures)
        return
    if "--pictures-only" in sys.argv:  # just redraw the check pictures
        redraw_pictures(rows, streets, only)
        return
    if "--footprints-only" in sys.argv:  # just redraw PlanFootprints from the records
        rebuild_footprints(rows, sr, placed_now=set())
        return
    placed_now = set()

    todo = [r for r in rows if r.get("loc_x") and (only is None or only.lower() in r["plan_id"].lower())]
    if "--resume" in sys.argv:  # a stopped run saved its progress in the manifest
        todo = [r for r in todo if not r.get("place_status")]
    print("{} located sheet(s) to place.\n".format(len(todo)))
    tally = {}
    started = time.time()
    for n, row in enumerate(todo, 1):
        pid = row["plan_id"]
        image = os.path.join(config.STAGED_PLANS_DIR, row["staged_image"])
        print("[{}/{}] {}".format(n, len(todo), pid))
        tfw, may_write = own_world_file(image)
        if not may_write:
            print("     already georeferenced by you; left alone")
            row["place_status"] = "kept"
            tally["kept"] = tally.get("kept", 0) + 1
            continue
        with open(os.path.join(config.PLAN_TEXT_DIR, pid + ".json"), encoding="utf-8") as f:
            result = json.load(f)
        # Some PDFs (exports from a utility's GIS) carry their real position.
        # That's exact, so it's used as is. The automatic fit still runs on
        # those sheets, to measure how accurate it is against the truth.
        pdf_wf = pdf_georef(row, image)
        if pdf_wf:
            print("     the PDF itself is georeferenced: using its exact position")
        if pdf_wf and is_photo_map(image):
            write_placement(image, tfw, pdf_wf, {"plan_id": pid, "status": "from PDF",
                                                 "source": "georeferenced PDF"})
            row["place_status"] = "from PDF"
            tally["from PDF"] = tally.get("from PDF", 0) + 1
            placed_now.add(pid)
            save_manifest(rows, columns)
            continue
        if is_photo_map(image):
            print("     web map on an aerial photo: no drawn lines to fit; place it by hand")
            row["place_status"] = "photo map"
            tally["photo map"] = tally.get("photo map", 0) + 1
            for path in (tfw, image + ".plantrace.json"):  # our own earlier attempt, if any
                if os.path.exists(path):
                    os.remove(path)
            save_manifest(rows, columns)
            continue
        t = time.time()
        lines = place.plan_lines(image, dpi=config.PDF_DPI, skew=result.get("skew"))
        stated = stated_scales(row, result)
        prior = stated[0] if stated else None
        scales = candidate_scales(stated, lines.dpi)
        # Search around the spot step 06 suggested; if the sheet's own
        # stationing pins it down on the route, search tightly around that.
        if row.get("sta_x"):
            cx, cy, radius = float(row["sta_x"]), float(row["sta_y"]), STATION_RADIUS_FT
            where = "its stationing"
        else:
            cx, cy = float(row["loc_x"]), float(row["loc_y"])
            radius = SEARCH_RADIUS_FT.get(row.get("loc_confidence") or "low", 3500)
            where = "the suggested spot"
        words = load_words(pid, image, print)
        labels = sheet_labels(row, words, lines, by_name, by_route, towns)
        # Station labels along a baseline (4+560, 4+580...) give the scale
        # exactly, and the baseline's direction must follow the route.
        rotations = None
        base = place.baseline_from_stations(station_labels(words, lines))
        route_dir = route_direction(by_route, cx, cy)
        if base:
            per_m, page_angle, n_lab = base
            ft_px = FT_PER_M / per_m
            scales, prior = [ft_px], ft_px * lines.dpi
            print("     {} station labels along the baseline: scale 1\"={:.1f}'".format(n_lab, prior))
            if row.get("sta_dir"):
                # we know which way stations increase along the route here
                turn = float(row["sta_dir"]) - page_angle
                rotations = (turn + np.arange(-6, 6.1, 1.5)) % 360
                print("     baseline must follow Route {} with stations increasing the same way: "
                      "trying {:.0f} deg (+-6)".format(config.STATIONING_ROUTE, turn % 360))
            elif route_dir is not None:
                turn = route_dir - page_angle
                rotations = np.concatenate([(turn + np.arange(-6, 6.1, 1.5)) % 360,
                                            (turn + 180 + np.arange(-6, 6.1, 1.5)) % 360])
                print("     baseline must follow Route {}: trying {:.0f} or {:.0f} deg (+-6)".format(
                    config.STATIONING_ROUTE, turn % 360, (turn + 180) % 360))
        print("     {} line points, {} street labels; scale on sheet: {}; searching {:,} ft around {}".format(
            len(lines), len(labels), ", ".join('1"={}\''.format(s) for s in stated) or "not found",
            radius, where))
        best, runner = place.fit_sheet(lines, streets, cx, cy, radius, scales,
                                       prior_scale=prior / lines.dpi if prior else None,
                                       labels=labels, log=print, rotations=rotations)
        status = status_of(best, runner)
        if best is None and not pdf_wf:
            print("     not enough lines on the sheet to place it")
            row["place_status"] = status
            tally[status] = tally.get(status, 0) + 1
            continue

        info = {"plan_id": pid, "status": status,
                "located_from": [row.get("loc_method"), row.get("loc_confidence")],
                "stated_scales": stated}
        if best is not None:
            auto_wf = best.world_file(lines.width_px, lines.height_px)
            info.update({"score": best.score, "labels_agree": best.labels_ok, "total": best.total,
                         "runner_up_total": runner.total if runner else None,
                         "scale_ft_per_in": best.scale * lines.dpi, "rotation_deg": best.rotation,
                         "centre_xy": [best.x, best.y]})
            print("     {}: lines {:.2f}, labels {}, total {:.2f} (next best {:.2f}), 1\"={:.0f}', rotated {:.1f} deg  [{:.0f}s]".format(
                status.upper(), best.score,
                "{:.0%}".format(best.labels_ok) if best.labels_ok is not None else "none",
                best.total, runner.total if runner else 0, best.scale * lines.dpi, best.rotation,
                time.time() - t))
            # Final fine-tuning against the aerial photo (curbs, pavement
            # edges). Scale is held unless the sheet had no stated scale.
            auto_wf = fine_tune(lines, auto_wf, info, free_scale=not stated)
        if pdf_wf:
            # Use the exact position; keep the automatic one to measure accuracy.
            info.update({"status": "from PDF", "source": "georeferenced PDF",
                         "auto_status": status})
            if best is not None:
                centre, corner, drot, ratio = georef.compare(auto_wf, pdf_wf, lines.width_px,
                                                             lines.height_px)
                info["auto_vs_true"] = {"centre_ft": centre, "worst_corner_ft": corner,
                                        "rotation_deg": drot, "scale_ratio": ratio,
                                        "auto_world_file": auto_wf}
                print("     automatic fit vs the PDF's true position: centre {:.0f} ft, worst corner "
                      "{:.0f} ft, rotation {:+.1f} deg, scale x{:.3f}".format(centre, corner, drot, ratio))
            status, wf = "from PDF", pdf_wf
        else:
            wf = auto_wf
        a, d = wf[0], wf[1]
        ft_per_in = math.hypot(a, d) * lines.dpi
        rotation = math.degrees(math.atan2(d, a))
        info.update({"scale_ft_per_in": ft_per_in, "rotation_deg": rotation})
        row["place_status"] = status
        tally[status] = tally.get(status, 0) + 1
        row["place_score"] = round(best.score, 3) if best is not None else ""
        row["place_scale_ft_in"] = round(ft_per_in, 1)
        row["place_rotation"] = round(rotation, 1)
        write_placement(image, tfw, wf, info)
        placed_now.add(pid)
        if pictures:
            draw_check(image, pid, wf, streets, status, ft_per_in, rotation)
        save_manifest(rows, columns)  # save as we go

    save_manifest(rows, columns)
    fp_fc = rebuild_footprints(rows, sr, placed_now)

    print("\nPlaced {} sheet(s) in {:.0f} min: {}".format(
        len(todo), (time.time() - started) / 60,
        ", ".join("{} {}".format(v, k) for k, v in sorted(tally.items()))))
    print("World files written next to the images in", config.STAGED_PLANS_DIR)
    print("Footprints saved to", fp_fc)
    if pictures:
        print("Check pictures in", CHECK_DIR)


if __name__ == "__main__":
    main()
