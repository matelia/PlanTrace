r"""
Step 9: Package the results for an office that uses ArcMap.

PlanTrace itself runs in ArcGIS Pro (on the computer that has it). Its
RESULTS are ordinary GIS files that ArcMap 10.x can open and edit:
  - the placed plan images (GeoTIFF + world file): they open in place
  - the see-through plan linework (GeoTIFF + world file)
  - the project geodatabase: Utilities (for tracing, with its dropdown
    lists), PlanFootprints (sheet outlines + your review verdicts),
    PlanLocations, RefStreets. It is a 10.x-compatible file geodatabase.

This step copies all of that into ONE folder you can put on a USB stick or
a shared drive, plus the ArcMap instructions (ARCMAP.md):

  projects\<PROJECT_NAME>\for_arcmap\<date>\
      <PROJECT_NAME>.gdb\        copy of the geodatabase
      plans\                     placed plan images (+ .tfw, pyramids)
      linework\                  see-through magenta linework
      PlanFootprints.shp         sheet outlines also as a shapefile (spare)
      ARCMAP.md                  how to use it in ArcMap, and the office test

Close ArcGIS Pro first (it can lock the geodatabase while copying).

    python scripts\09_export_for_arcmap.py           everything placed
    python scripts\09_export_for_arcmap.py --test    a small 3-sheet package for
                                                     trying ArcMap out first
"""

import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
import arcpy

FOOTPRINTS = "PlanFootprints"
OVERLAY_DIR = os.path.join(config.PROJECT_DIR, "plan_overlays")


def pick_test_sheets(status, scale):
    """One sheet of each kind of placement, for the office test (skipping
    index maps: they cover a mile and make a poor test)."""
    picks = []
    for wanted in ("from PDF", "good", "from coordinates", "check"):
        for pid in sorted(status):
            if status[pid] == wanted and pid not in picks and (scale.get(pid) or 0) <= 200:
                picks.append(pid)
                break
        if len(picks) == 3:
            break
    return picks


def copy_with_sidecars(base_path, out_dir):
    """Copy an image and the files that travel with it (.tfw, .aux.xml,
    pyramids .ovr). Never copies anything else."""
    n = 0
    stem = os.path.splitext(base_path)[0]
    for path in (base_path, stem + ".tfw", base_path + ".aux.xml", base_path + ".ovr", stem + ".ovr"):
        if os.path.exists(path):
            shutil.copy2(path, out_dir)
            n += os.path.getsize(path)
    return n


def main():
    test = "--test" in sys.argv
    fp_fc = os.path.join(config.GDB_PATH, FOOTPRINTS)
    if not arcpy.Exists(fp_fc):
        print("No PlanFootprints yet. Run step 07 first.")
        sys.exit(1)
    status, scale = {}, {}
    for pid, st, ft_in in arcpy.da.SearchCursor(fp_fc, ["plan_id", "place_status", "scale_ft_per_in"]):
        status[pid], scale[pid] = st, ft_in
    sheets = pick_test_sheets(status, scale) if test else sorted(status)

    out = os.path.join(config.PROJECT_DIR, "for_arcmap",
                       time.strftime("%Y-%m-%d") + ("_test" if test else ""))
    if os.path.exists(out):
        print("Already exists:", out)
        print("Delete or rename that folder first if you want a fresh package.")
        sys.exit(1)
    os.makedirs(os.path.join(out, "plans"))
    os.makedirs(os.path.join(out, "linework"))

    print("Copying the geodatabase...")
    arcpy.management.Copy(config.GDB_PATH, os.path.join(out, os.path.basename(config.GDB_PATH)))
    arcpy.conversion.FeatureClassToShapefile([fp_fc], out)

    size = 0
    for n, pid in enumerate(sheets, 1):
        scans = [f for f in os.listdir(config.STAGED_PLANS_DIR)
                 if os.path.splitext(f)[0] == pid and os.path.splitext(f)[1].lower() in (".tif", ".tiff", ".jpg", ".png")]
        if scans:
            size += copy_with_sidecars(os.path.join(config.STAGED_PLANS_DIR, scans[0]), os.path.join(out, "plans"))
        ink = os.path.join(OVERLAY_DIR, pid + "_ink.tif")
        if os.path.exists(ink):
            size += copy_with_sidecars(ink, os.path.join(out, "linework"))
        print("   [{}/{}] {} ({})".format(n, len(sheets), pid, status[pid]))

    guide = os.path.join(config.REPO_ROOT, "ARCMAP.md")
    if os.path.exists(guide):
        shutil.copy2(guide, out)
    print("\nDone: {} sheet(s), {:.0f} MB of images, in\n   {}".format(len(sheets), size / 1e6, out))
    print("Copy that whole folder to the ArcMap computer and follow ARCMAP.md.")
    if test:
        print("Test sheets: " + "; ".join("{} = {}".format(p, status[p]) for p in sheets))


if __name__ == "__main__":
    main()
