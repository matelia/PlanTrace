r"""
Step 5b: Datum, coordinates and file properties for every page.

Uses what step 05 read (plan_text\<plan_id>.json) and the PDFs themselves:
  - FILE PROPERTIES: which program made each PDF (scanner, CAD, GIS, email
    print...), when, and by whom. Tells you what kind of document it is.
  - DATUM NOTES written on the sheet: NAD 27 / NAD 83, State Plane or UTM,
    NGVD 29 / NAVD 88, benchmarks.
  - COORDINATES: if a page's text has coordinate-like numbers (grid ticks
    such as "N 1,050,000" / "E 655,250"), it reads where they are on the
    page and works out the sheet's position directly from them. The
    coordinate system is often not written, so every system in
    config.COORDINATE_SYSTEMS is tried (old datums are converted, NAD 27 ->
    NAD 83 with NADCON); the one that lands in the project area wins. That
    is how the datum is found when the sheet doesn't say.

Results:
  plan_text\<plan_id>.datum.json   everything found, with the text it came from
  plan_manifest.csv  new columns: file_kind, file_program, file_created,
                     datum_written, vertical_datum, coords_found, coords_system,
                     coords_note
Step 07 then uses a coordinate placement (when there is one) instead of
fitting the sheet to the streets.

Takes a few minutes (pages with coordinates are re-read at higher
resolution, about a minute each). Safe to re-run.

    python scripts\05b_find_datum.py
    python scripts\05b_find_datum.py --only SEWER     only some pages
"""

import csv
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
import arcpy
from PIL import Image

from plantrace import datum, read

Image.MAX_IMAGE_PIXELS = None
NEW_COLUMNS = ["file_kind", "file_program", "file_created", "datum_written", "vertical_datum",
               "coords_found", "coords_system", "coords_note"]
# A coordinate placement is trusted only if the labels agree this well (ft)
# and enough labels were read.
MAX_FIT_RMS_FT = 30
MIN_LABELS = 4


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


def sharp_words(pid, image):
    """Word positions read at higher resolution (cached), for small labels."""
    path = os.path.join(config.PLAN_TEXT_DIR, pid + ".words_hi.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    print("     reading label positions at higher resolution (about a minute)...")
    words = read.word_positions(image, config.TESSERACT_EXE, max_pixels=read.MAX_PIXELS)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(words, f)
    os.replace(path + ".tmp", path)
    return words


def main():
    only = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else None
    rows, columns = load_manifest()
    streets = os.path.join(config.GDB_PATH, config.STREETS_FC_NAME)
    ext = arcpy.Describe(streets).extent  # the project area (search box)
    box = (ext.XMin, ext.YMin, ext.XMax, ext.YMax)
    systems = getattr(config, "COORDINATE_SYSTEMS", [])
    props_cache = {}
    tally = {"pages": 0, "datum written": 0, "coordinates found": 0, "placed from coordinates": 0}

    for row in rows:
        pid = row["plan_id"]
        if only and only.lower() not in pid.lower():
            continue
        json_path = os.path.join(config.PLAN_TEXT_DIR, pid + ".json")
        if not os.path.exists(json_path):
            continue
        with open(json_path, encoding="utf-8") as f:
            result = json.load(f)
        tally["pages"] += 1
        image = os.path.join(config.STAGED_PLANS_DIR, row["staged_image"])
        out = {"plan_id": pid}

        # 1. File properties (one look per PDF)
        src = row.get("source_file", "")
        if src not in props_cache:
            hits = glob.glob(os.path.join(config.RAW_PLANS_DIR, "**", src), recursive=True)
            props_cache[src] = datum.file_properties(hits[0]) if hits and src.lower().endswith(".pdf") else {}
        props = props_cache[src]
        out["file"] = props
        row["file_kind"] = props.get("kind", "")
        row["file_program"] = props.get("program", "")
        row["file_created"] = props.get("created", "")

        # 2. Datum notes on the sheet
        v = result.get("vision") or {}
        lines = list(result.get("lines", [])) + [str(x) for x in v.get("stations", [])]
        notes = datum.datum_statements(lines)
        out["written"] = notes
        row["datum_written"] = "; ".join(notes["horizontal"] + notes["system"])
        row["vertical_datum"] = "; ".join(notes["vertical"])
        if notes["horizontal"] or notes["system"]:
            tally["datum written"] += 1

        # 3. Coordinates
        row["coords_found"] = row["coords_system"] = row["coords_note"] = ""
        if datum.has_coordinate_numbers(result.get("lines", [])) and os.path.exists(image):
            print(pid)
            with Image.open(image) as im:
                w, h = im.size
            labels = datum.coordinate_labels(sharp_words(pid, image), w, h)
            row["coords_found"] = str(len(labels))
            out["labels"] = labels
            if labels:
                tally["coordinates found"] += 1
            near = (float(row["loc_x"]), float(row["loc_y"])) if row.get("loc_x") else None
            tries = datum.place_from_coordinates(labels, w, h, systems, config.SPATIAL_REFERENCE_WKID,
                                                 box, near, config.PDF_DPI) if len(labels) >= MIN_LABELS else []
            out["tries"] = [{k: t[k] for k in ("system", "inside_area", "dist_to_suggested_ft",
                                                "ft_per_in", "rotation_deg", "fit_rms")} for t in tries]
            best = tries[0] if tries else None
            inside = [t for t in tries if t["inside_area"]]
            if best and best["inside_area"] and best["fit_rms"] <= MAX_FIT_RMS_FT:
                out["placement"] = best
                row["coords_system"] = best["system"]
                note = "placed from {} labels; fit {:.0f} ft; 1\"={:.0f}'".format(
                    best["labels_used"], best["fit_rms"], best["ft_per_in"])
                if len({t["system"] for t in inside}) > 1:
                    note += "; other systems also land in the area: check"
                if notes["horizontal"] and not any(h in best["system"] for h in notes["horizontal"]):
                    note += "; sheet says {}: check".format("/".join(notes["horizontal"]))
                row["coords_note"] = note
                tally["placed from coordinates"] += 1
                print("     coordinates are {} ({})".format(best["system"], note))
            else:
                row["coords_note"] = ("too few coordinate labels read" if len(labels) < MIN_LABELS
                                      else "no coordinate system puts this sheet in the project area")
                print("     " + row["coords_note"])

        with open(os.path.join(config.PLAN_TEXT_DIR, pid + ".datum.json"), "w", encoding="utf-8") as f:
            json.dump(out, f, indent=1)

    save_manifest(rows, columns)
    print("\n{pages} pages: datum written on {datum written}, coordinate labels on "
          "{coordinates found}, placed from coordinates: {placed from coordinates}.".format(**tally))
    kinds = {}
    for p in props_cache.values():
        kinds[p.get("kind") or "?"] = kinds.get(p.get("kind") or "?", 0) + 1
    print("PDFs by how they were made:", ", ".join("{} {}".format(n, k) for k, n in sorted(kinds.items())))


if __name__ == "__main__":
    main()
