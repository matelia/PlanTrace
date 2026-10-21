r"""
Step 6: Suggest where each plan sheet goes on the map.

Uses what step 05 read off each page (plan_text\<plan_id>.json) and the
street centerlines from step 04. For each sheet it:
  - double-checks one-word street names (drops 'ADAMS' from 'ADAMS COUNTY',
    'GROUND' read as Round Trail Drive, and similar false matches)
  - finds where the sheet's streets cross each other
  - picks the spot where the most of the sheet's streets agree
  - for sheets the streets can't settle, uses extra clues learned from the
    sheets that WERE placed: utility grid/map numbers, route stationing, and
    neighbouring pages of the same PDF (see plantrace\hints.py). It first
    tests each clue on the placed sheets and prints how far off it lands.

Results:
  PlanLocations   point layer in the project geodatabase, one point per
                  sheet that could be located. Add it to your map and
                  compare against the imagery. Fields:
                    plan_id, source_file, page
                    method        crossing = streets on the sheet meet there
                                  one_road = only one usable street
                                  grid / stationing / neighbours = no usable
                                      streets; placed from that clue only
                    confidence    high / medium / low
                    streets_used  streets that agree with the point
                    streets_ignored  streets that didn't fit (misreads etc.)
                    hint          the extra clue used, if any
                    n_streets, n_crossings, note
  plan_manifest.csv  new columns: loc_method, loc_confidence, loc_streets,
                     loc_hint, loc_x, loc_y (state plane feet, EPSG 2262)

Takes about a minute. Nothing is read from the scans again.
SAFE TO RE-RUN: PlanLocations is replaced each time (it is a suggestion
layer, not hand-edited data). Your manifest edits are kept.
Close plan_manifest.csv in Excel first.
"""

import csv
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
import arcpy
from plantrace import hints, locate, read

LOCATIONS_FC_NAME = "PlanLocations"
NEW_COLUMNS = ["loc_method", "loc_confidence", "loc_streets", "loc_hint", "loc_x", "loc_y",
               "sta_x", "sta_y", "sta_dir"]


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
    os.replace(tmp, config.MANIFEST_PATH)  # only replace once fully written


def make_output_fc(sr):
    fc = os.path.join(config.GDB_PATH, LOCATIONS_FC_NAME)
    if arcpy.Exists(fc):
        arcpy.management.Delete(fc)
    arcpy.management.CreateFeatureclass(config.GDB_PATH, LOCATIONS_FC_NAME, "POINT",
                                        spatial_reference=sr)
    for name, length in [("plan_id", 150), ("source_file", 200), ("method", 20),
                         ("confidence", 10), ("streets_used", 500),
                         ("streets_ignored", 500), ("note", 200), ("hint", 200)]:
        arcpy.management.AddField(fc, name, "TEXT", field_length=length)
    arcpy.management.AddField(fc, "page", "SHORT")
    arcpy.management.AddField(fc, "n_streets", "SHORT")
    arcpy.management.AddField(fc, "n_crossings", "SHORT")
    return fc


def sheet_streets(result, towns):
    """Streets found on a page, after the stricter one-word check.
    Returns (kept names, dropped names)."""
    v = result.get("vision") or {}
    # Same text the names were matched in: Tesseract lines + the model's answers.
    # A separator keeps one answer's words from running into the next.
    lines = list(result.get("lines", []))
    for name in v.get("street_names", []):
        lines += ["|", name]
    kept, dropped = read.confirm_one_word_streets(result.get("streets", {}), lines, towns)
    order = sorted(kept, key=lambda n: -kept[n])
    return order, sorted(dropped)


# A clue is trusted (can raise a sheet to 'medium') if, on sheets we already
# know, it lands within this distance half the time or better.
TRUSTED_MEDIAN_MISS_FT = 1000
# A clue that disagrees with a street-crossing location by more than this
# gets a "check this" note on the sheet.
DISAGREE_FT = 2000


def report_clue_accuracy(clues):
    """Test each clue on the sheets already placed by street crossings
    (each sheet left out of its own prediction). Prints the results and
    returns the set of clue names good enough to trust."""
    print("\nHow well each clue finds sheets we already located (leave-one-out):")
    trusted = set()
    typical = {}
    for name, misses in clues.check_accuracy().items():
        if not misses:
            print("  {:<11} not enough sheets to learn from".format(name))
            continue
        ft = sorted(m for _, m in misses)
        median = ft[len(ft) // 2]
        typical[name] = median
        ok = len(ft) >= 3 and median <= TRUSTED_MEDIAN_MISS_FT
        if ok:
            trusted.add(name)
        print("  {:<11} {:>2} sheets   typical miss {:>6,.0f} ft   worst {:>6,.0f} ft   {}".format(
            name, len(ft), median, ft[-1], "trusted" if ok else "used only as a last resort"))
    print()
    # Best-tested trusted clue first; untrusted ones only as a last resort.
    clues.set_order(sorted(clues.ORDER, key=lambda n: (n not in trusted, typical.get(n, 1e9))))
    return trusted


def improve_with_clues(sh, clues, trusted, index, anchor):
    """Use grid / stationing / neighbouring-page clues on a weak sheet.
    Updates sh["loc"] in place."""
    loc = sh["loc"]
    hint = clues.predict(sh)
    point, desc, name = hint if hint else (None, "", "")
    level = "medium" if name in trusted else "low"

    if loc["method"] == "crossing" and loc["confidence"] in ("high", "medium"):
        # Already placed by streets. If a precise, trusted clue (grid number
        # or stationing, not neighbouring pages) strongly disagrees, flag it
        # and lower the confidence; you decide which is right.
        for clue in ("grid", "stationing"):
            got = getattr(clues, clue)(sh) if clue in trusted else None
            if got and got[0].distanceTo(loc["point"]) > DISAGREE_FT:
                loc["note"] = (loc["note"] + "; " if loc["note"] else "") + \
                    "CHECK: {} suggests a spot {:,.0f} ft away".format(got[1], got[0].distanceTo(loc["point"]))
                loc["confidence"] = "low"
                loc["hint"] = got[1]
                break
        return

    if loc["method"] == "crossing":  # two spots fit equally well
        if point:
            new = locate.locate_sheet(sh["names"], index, hint=point)
            if new.get("tie_broken"):
                new["confidence"] = level
                new["note"] += " ({})".format(desc)
                new["hint"] = desc
                sh["loc"] = new
        return

    if loc["method"] == "one_road":  # position along the road from the clue
        new = locate.locate_sheet(sh["names"], index, anchor=point or anchor)
        if point:
            off = new["point"].distanceTo(point)
            new["hint"] = desc
            if off > DISAGREE_FT:
                new["note"] = "{} is {:,.0f} ft from this road; placed on the road nearest it".format(desc, off)
            else:
                new["confidence"] = level
                new["note"] = "position along the road from " + desc
        sh["loc"] = new
        return

    # No usable streets at all: the clue is all we have.
    if point:
        loc.update(point=point, method=name, confidence="low", hint=desc,
                   note="no usable streets; placed from " + desc)


def main():
    streets_fc = os.path.join(config.GDB_PATH, config.STREETS_FC_NAME)
    if not arcpy.Exists(streets_fc):
        print("RefStreets not found. Run step 04 first.")
        sys.exit(1)
    if not os.path.exists(config.MANIFEST_PATH):
        print("No plan_manifest.csv yet. Run step 02 first.")
        sys.exit(1)

    print("Loading street centerlines...")
    read.HIGHWAY_ALIASES.update(getattr(config, "HIGHWAY_ALIASES", {}))
    index = locate.RoadIndex(streets_fc)
    rows, columns = load_manifest()
    fc = make_output_fc(index.spatial_reference)
    fields = ["SHAPE@", "plan_id", "source_file", "page", "method", "confidence",
              "streets_used", "streets_ignored", "n_streets", "n_crossings", "note", "hint"]

    # Pass 1: locate every sheet from its streets, and collect its other clues.
    tally = {}
    sheets = []
    for row in rows:
        for c in NEW_COLUMNS:
            row[c] = ""
        json_path = os.path.join(config.PLAN_TEXT_DIR, row["plan_id"] + ".json")
        if (row.get("status") or "").lower() == "skip" or not os.path.exists(json_path):
            continue
        with open(json_path, encoding="utf-8") as f:
            result = json.load(f)
        if result.get("sheet_kind") == "correspondence":
            row["loc_method"] = "skipped (correspondence)"
            tally["skipped (correspondence)"] = tally.get("skipped (correspondence)", 0) + 1
            continue
        names, dropped = sheet_streets(result, index.towns | set(getattr(config, "EXTRA_PLACE_WORDS", [])))
        loc = locate.locate_sheet(names, index)
        sheets.append({"id": row["plan_id"], "row": row, "names": names, "dropped": dropped,
                       "loc": loc, "source": row.get("source_file", ""),
                       "page": int(row.get("page") or 0),
                       "grids": hints.grid_numbers(result),
                       "raw_stations": hints.raw_stations(result)})

    # Sheets placed by street crossings (high/medium) teach the other clues.
    main_range = hints.main_station_range([s for sh in sheets for s in sh["raw_stations"]])
    for sh in sheets:
        sh["station"] = hints.sheet_station(sh["raw_stations"], main_range) if main_range else None
    good = [sh for sh in sheets if sh["loc"]["method"] == "crossing"
            and sh["loc"]["confidence"] in ("high", "medium")]
    for sh in good:
        sh["point"] = sh["loc"]["point"]
    route = (hints.route_line(streets_fc, config.STATIONING_ROUTE)
             if getattr(config, "STATIONING_ROUTE", None) else None)
    clues = hints.Hints(good, route, index.spatial_reference)
    trusted = report_clue_accuracy(clues)

    # Pass 2: improve the weak sheets with the clues.
    anchor = locate.project_anchor(
        [sh["point"] for sh in good if sh["loc"]["confidence"] == "high"],
        index.spatial_reference)
    # Placed sheets first: any a precise clue disagrees with get flagged, and
    # stop teaching the clues, so they can't mislead the weak sheets.
    for sh in good:
        improve_with_clues(sh, clues, trusted, index, anchor)
    clues.good = [sh for sh in good if sh["loc"]["confidence"] != "low"]
    good_ids = {sh["id"] for sh in good}
    for sh in sheets:
        if sh["id"] not in good_ids:
            improve_with_clues(sh, clues, trusted, index, anchor)

    with arcpy.da.InsertCursor(fc, fields) as cur:
        for sh in sheets:
            row, loc = sh["row"], sh["loc"]
            ignored = loc["ignored"] + ["{} (text check)".format(d) for d in sh["dropped"]]
            row["loc_hint"] = loc.get("hint", "")
            # Where the sheet's own stationing puts it on the route (if it
            # has stationing and that clue is trusted). Step 07 searches
            # around this spot, which is tighter than the street location.
            sta = clues.stationing(sh) if "stationing" in trusted else None
            if sta:
                p = sta[0].firstPoint
                row["sta_x"], row["sta_y"] = round(p.X, 1), round(p.Y, 1)
                # which way stations increase along the route here (degrees,
                # counter-clockwise from east): where station +20 m lands
                ahead = clues.stationing(dict(sh, station=sh["station"] + 20))
                if ahead:
                    q = ahead[0].firstPoint
                    row["sta_dir"] = round(math.degrees(math.atan2(q.Y - p.Y, q.X - p.X)) % 360, 1)

            key = loc["method"] + (" " + loc["confidence"] if loc["confidence"] else "")
            tally[key] = tally.get(key, 0) + 1
            row["loc_method"] = loc["method"]
            row["loc_confidence"] = loc["confidence"]
            row["loc_streets"] = "; ".join(loc["used"])
            print("{:<62} {:<9} {:<6} {}".format(
                row["plan_id"][-62:], loc["method"], loc["confidence"],
                "; ".join(loc["used"]) or loc["note"]))
            if loc["point"] is None:
                continue
            p = loc["point"].firstPoint
            row["loc_x"], row["loc_y"] = round(p.X, 1), round(p.Y, 1)
            cur.insertRow([loc["point"], row["plan_id"], row.get("source_file", ""),
                           int(row.get("page") or 0), loc["method"], loc["confidence"],
                           "; ".join(loc["used"])[:500], "; ".join(ignored)[:500],
                           len(loc["used"]), loc["n_crossings"], loc["note"][:200],
                           loc.get("hint", "")[:200]])

    save_manifest(rows, columns)
    print("\nSummary:", ", ".join("{} {}".format(n, k) for k, n in sorted(tally.items())))
    print("Saved points to", fc)
    print("Next: add PlanLocations to your map and check the points against the imagery.")


if __name__ == "__main__":
    main()
