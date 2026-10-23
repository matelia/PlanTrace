r"""
Step 8: Make a "Placement Review" map in your ArcGIS Pro project.

Sets up everything you need to check step 07's automatic placements:
  - NYS orthoimagery underneath (falls back to Esri imagery if it can't load)
  - RefStreets (street centerlines, thin yellow)
  - PlanFootprints: the outline of each placed sheet, coloured by
    place_status (green = good, orange = check, red = poor)
  - every placed plan image, in three groups: Plans - good / check / poor.
    All are switched OFF; turn on one at a time (they're big).
  - PlanLocations (step 06's points)

It also:
  - tags each placed image with the project's coordinate system (EPSG 2262),
    so Pro doesn't ask about an unknown coordinate system
  - builds display pyramids for the images (one time; makes them draw fast)
  - adds the review fields to PlanFootprints if they're missing:
        review       dropdown: correct / close / wrong   <- you fill this in
        review_note  free text                           <- and this

Your own maps are not changed. If a "Placement Review" map already exists
it is rebuilt. A backup copy of the .aprx is saved first.

CLOSE ARCGIS PRO BEFORE RUNNING THIS (an open project can't be saved).

    python scripts\08_make_review_map.py
"""

import glob
import json
import os
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
import arcpy
from PIL import Image
from plantrace import georef, review

MAP_NAME = "Placement Review"
OVERLAY_DIR = os.path.join(config.PROJECT_DIR, "plan_overlays")
# Overlays are made at (nearly) full resolution so the lines stay thin.
OVERLAY_MAX_WIDTH = 12000
# Sheets at a smaller scale than this (feet per inch) are index maps.
INDEX_MAP_FT_PER_IN = 200
ORTHO_URL = config.ORTHO_SERVICE_URL
STATUS_COLOURS = {"from PDF": [0, 110, 255, 100], "from coordinates": [140, 70, 255, 100],
                  "good": [0, 170, 0, 100],
                  "check": [255, 140, 0, 100], "poor": [220, 0, 0, 100]}


def short_name(plan_id):
    for text in getattr(config, "SHORT_NAME_REMOVE", []):
        plan_id = plan_id.replace(text, "")
    return plan_id


def find_project():
    aprxs = [p for p in glob.glob(os.path.join(config.PROJECT_DIR, "*.aprx"))
             if "_backup_" not in os.path.basename(p)]
    if len(aprxs) != 1:
        print("Expected one .aprx in", config.PROJECT_DIR, "but found:", aprxs)
        sys.exit(1)
    return aprxs[0]


def pro_is_running():
    out = subprocess.run(["tasklist"], capture_output=True, text=True).stdout.lower()
    return "arcgispro.exe" in out


def prepare_images(placed):
    """Make sure each placed image carries its placement INSIDE the file
    (ArcGIS reads that before the world file), then build pyramids.

    An earlier version used arcpy's DefineProjection here. It left positions
    inside the TIFFs that didn't match the world files (rotation dropped, UTM
    numbers labelled as State Plane), so sheets showed up in the wrong place
    in Pro. Now the position from the step 07 record is written with GDAL,
    and only when it differs."""
    for n, (pid, image) in enumerate(placed, 1):
        try:
            with open(image + ".plantrace.json", encoding="utf-8") as f:
                wf = [float(v) for v in json.load(f)["world_file_text"].split()]
            with Image.open(image) as im:
                w, h = im.size
            off = georef.compare(georef.read_georef(image), wf, w, h)[1]
            if off > 0.5:
                print("   [{}/{}] fixing the position stored in the image ({:,.0f} ft off): {}".format(
                    n, len(placed), off, short_name(pid)))
                georef.write_georef(image, wf, config.SPATIAL_REFERENCE_WKID)
            base = os.path.splitext(image)[0]
            if not (os.path.exists(image + ".ovr") or os.path.exists(base + ".ovr")):
                print("   [{}/{}] building pyramids: {}".format(n, len(placed), short_name(pid)))
                arcpy.management.BuildPyramids(image)
        except Exception as e:
            print("   could not prepare {}: {}".format(short_name(pid), e))


def make_overlays(placed):
    """A see-through magenta copy of each placed sheet's ink (no white
    background), so the plan's lines show on top of the aerial. Remade when
    the sheet's placement is newer than its overlay. Returns {plan_id: path}."""
    os.makedirs(OVERLAY_DIR, exist_ok=True)
    out = {}
    for n, (pid, image) in enumerate(placed, 1):
        marker = image + ".plantrace.json"
        tif = os.path.join(OVERLAY_DIR, pid + "_ink.tif")
        if not os.path.exists(marker):
            continue
        if not os.path.exists(tif) or os.path.getmtime(tif) < os.path.getmtime(marker):
            print("   [{}/{}] plan linework: {}".format(n, len(placed), short_name(pid)))
            with open(marker, encoding="utf-8") as f:
                wf = [float(v) for v in json.load(f)["world_file_text"].split()]
            for old in glob.glob(tif + "*") + glob.glob(tif[:-4] + ".tfw"):
                os.remove(old)  # our own derived files only
            review.ink_overlay(image, wf, tif, config.SPATIAL_REFERENCE_WKID, max_width=OVERLAY_MAX_WIDTH)
            arcpy.management.BuildPyramids(tif)
        out[pid] = tif
    return out


def style_footprints(layer):
    sym = layer.symbology
    sym.updateRenderer("UniqueValueRenderer")
    sym.renderer.fields = ["place_status"]
    for group in sym.renderer.groups:
        for item in group.items:
            colour = STATUS_COLOURS.get(item.values[0][0], [120, 120, 120, 100])
            item.symbol.color = {"RGB": [0, 0, 0, 0]}  # hollow
            item.symbol.outlineColor = {"RGB": colour}
            item.symbol.outlineWidth = 1.5
            item.label = item.values[0][0]
    layer.symbology = sym
    layer.showLabels = True
    for lc in layer.listLabelClasses():
        expr = "$feature.plan_id"
        for text in getattr(config, "SHORT_NAME_REMOVE", []):
            expr = "Replace({}, '{}', '')".format(expr, text)
        lc.expression = expr
        lc.expressionEngine = "Arcade"
        lc.visible = True


def style_streets(layer):
    sym = layer.symbology
    if hasattr(sym, "renderer") and hasattr(sym.renderer, "symbol"):
        sym.renderer.symbol.color = {"RGB": [255, 255, 0, 100]}
        sym.renderer.symbol.width = 1
        layer.symbology = sym


def remove_review_map(aprx):
    """Take the review map out of a project. Inside ArcGIS Pro this also
    lets go of the files it shows, so they can be rewritten."""
    for m in aprx.listMaps(MAP_NAME):
        aprx.deleteItem(m)


def main(aprx=None):
    """aprx: the project to put the map in. The PlanTrace toolbox passes the
    project open in ArcGIS Pro ("CURRENT"). Run from the command prompt,
    Pro must be closed and the project file is found, backed up and saved."""
    in_pro = aprx is not None
    review.ORTHO_EXPORT = config.ORTHO_SERVICE_URL.rstrip("/") + "/export"
    if in_pro:
        remove_review_map(aprx)  # before touching the images it shows
    else:
        if pro_is_running():
            print("ArcGIS Pro is open. Close it (save your work first), then run this again,")
            print("or use the PlanTrace toolbox inside Pro.")
            sys.exit(1)
        aprx_path = find_project()
        backup = aprx_path.replace(".aprx", "_backup_{}.aprx".format(time.strftime("%Y%m%d_%H%M")))
        shutil.copy2(aprx_path, backup)
        print("Backed up your project to", os.path.basename(backup))

    fp_fc = os.path.join(config.GDB_PATH, "PlanFootprints")
    if not arcpy.Exists(fp_fc):
        print("No PlanFootprints yet. Run step 07 first.")
        sys.exit(1)
    # step 07 knows how to add the review fields; reuse it
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    place_step = __import__("07_place_plans")
    place_step.add_review_fields(fp_fc)

    status, scale = {}, {}
    for pid, st, ft_in in arcpy.da.SearchCursor(fp_fc, ["plan_id", "place_status", "scale_ft_per_in"]):
        status[pid] = st
        scale[pid] = ft_in or 0
    placed = []
    for pid in sorted(status):
        hits = [p for p in glob.glob(os.path.join(config.STAGED_PLANS_DIR, pid + ".*"))
                if os.path.splitext(p)[1].lower() in (".tif", ".tiff", ".jpg", ".jpeg", ".png")]
        if hits:
            placed.append((pid, hits[0]))
    print("Preparing {} placed images (first time takes a while)...".format(len(placed)))
    prepare_images(placed)
    print("Making the see-through plan linework...")
    overlays = make_overlays(placed)

    if not in_pro:
        aprx = arcpy.mp.ArcGISProject(aprx_path)
        remove_review_map(aprx)
    m = aprx.createMap(MAP_NAME, "MAP")
    m.spatialReference = arcpy.SpatialReference(config.SPATIAL_REFERENCE_WKID)

    # Imagery underneath.
    try:
        ortho = m.addDataFromPath(ORTHO_URL)
        ortho.name = "NYS Orthoimagery (latest)"
    except Exception as e:  # no internet, service down...
        print("Couldn't add NYS orthoimagery ({}); using Esri imagery instead.".format(e))
        m.addBasemap("Imagery")

    def add_grouped(paths, prefix, visible, transparency):
        """One group per place_status, each sheet a layer inside it."""
        groups = {}
        for st in ("index maps", "poor", "check", "good", "from coordinates", "from PDF"):
            groups[st] = m.createGroupLayer("{} - {}".format(prefix, st))
            # index maps cover a mile; their lettering swamps everything else
            groups[st].visible = st != "index maps"
        for pid, path in paths:
            st = "index maps" if scale.get(pid, 0) > INDEX_MAP_FT_PER_IN else status.get(pid, "check")
            lyr = m.addDataFromPath(path)
            lyr.name = short_name(pid)
            lyr.visible = visible
            lyr.transparency = transparency
            m.addLayerToGroup(groups.get(st, groups["check"]), lyr)
            m.removeLayer(lyr)

    # Original scans (white background): all OFF, turn on one at a time.
    add_grouped(placed, "Scans", visible=False, transparency=25)
    # The plans' linework in magenta, see-through: all ON.
    add_grouped(sorted(overlays.items()), "Plan linework", visible=True, transparency=0)

    streets = m.addDataFromPath(os.path.join(config.GDB_PATH, config.STREETS_FC_NAME))
    style_streets(streets)
    locations = os.path.join(config.GDB_PATH, "PlanLocations")
    if arcpy.Exists(locations):
        m.addDataFromPath(locations).visible = False
    footprints = m.addDataFromPath(fp_fc)
    style_footprints(footprints)

    try:  # open the map zoomed to the sheets
        m.defaultCamera.setExtent(arcpy.Describe(fp_fc).extent)
    except Exception:
        pass
    if in_pro:
        try:
            m.openView()  # show it
        except Exception:
            pass
        print("\nDone. The '{}' map is open (Catalog pane > Maps). Save the project to keep it.".format(MAP_NAME))
    else:
        aprx.save()
        print("\nDone. Open {} in ArcGIS Pro and open the '{}' map".format(
            os.path.basename(aprx_path), MAP_NAME))
        print("(Catalog pane > Maps > {}).".format(MAP_NAME))


if __name__ == "__main__":
    main()
