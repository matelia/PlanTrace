# Using PlanTrace results in ArcMap

PlanTrace runs in **ArcGIS Pro** on the computer that has it. Its **results** are ordinary GIS files that **ArcMap 10.x** can open and edit:

- **plans\\**: the placed plan sheets (GeoTIFF + world file). They open already in place.
- **linework\\**: each sheet's lines in see-through magenta, for checking placements against the aerial.
- **the geodatabase** (`<PROJECT>.gdb`): `Utilities` (where you trace, with dropdown lists), `PlanFootprints` (sheet outlines, status and your review verdicts), `PlanLocations`, `RefStreets`. It's an ArcGIS 10.x-compatible file geodatabase.

On the Pro computer, step 09 puts all of that in one folder (`python scripts\09_export_for_arcmap.py`, or `--test` for a small trial package). Copy that whole folder to the ArcMap computer, e.g. to `C:\PlanTrace_review\`.

---

## Part 1: Office test (about 15 minutes, do this first)

Use the **test package** (3 sheets). For each check, write **pass** or **fail** in the table at the end. Take a screenshot of anything that fails (Windows key + Shift + S).

**Before you start:** note the ArcMap version (**Help → About ArcMap**).

### T1. Open the geodatabase
1. Start **ArcMap** with a blank map.
2. Click **Add Data** (the yellow plus button). Click **Connect To Folder** (the folder with a plus) and pick the package folder.
3. Open the `.gdb` and add **RefStreets**, **PlanFootprints** and **Utilities**. Hold Ctrl to pick several.
   - **Pass:** the three layers appear with no error message, and the streets draw.

### T2. Aerial underneath
1. **File → Add Data → Add Basemap… → Imagery**.
   - If that's blocked, use the NYS orthoimagery: **Catalog** window → **GIS Servers** → **Add ArcGIS Server** → **Use GIS services** → Server URL `https://orthos.its.ny.gov/arcgis/services` → **Finish**. Open the new connection → **wms** → drag **Latest** onto the map.
   - **Pass:** the aerial shows and the yellow/grey street lines sit on the roads.

### T3. Placed plan sheets open in place
1. **Add Data** → package folder → **plans** → add all three `.tif` files. If ArcMap asks to build pyramids, click **No** (they're included). If it warns about an unknown coordinate system, click **OK**.
2. Right-click each plan → **Zoom To Layer**.
   - **Pass:** each sheet is on the right streets, turned and sized like the roads in the aerial. It doesn't have to be perfect; "close" is fine.
   - **Fail:** a sheet appears far away, upside down, or not rotated (sitting square to the screen when its roads are diagonal).

### T4. See-through linework
1. **Add Data** → **linework** → add the three `_ink.tif` files.
   - **Pass:** only magenta lines show, with the aerial visible between them.
   - **Fail:** a solid white or black rectangle covers the map. Then try the fix: right-click the layer → **Properties** → **Symbology** → set value **0** to **No Color** (or, under *Stretched*, tick **Display Background Value 0** as no color) → **OK**. Note in the table whether the fix worked.

### T5. Review verdicts can be edited
1. Turn on the **Editor** toolbar: **Customize → Toolbars → Editor**.
2. **Editor → Start Editing** → pick the package's geodatabase if asked.
3. Right-click **PlanFootprints** → **Open Attribute Table**. Click a cell in the **review** column.
   - **Pass:** a dropdown with *correct / close / wrong* appears, and you can pick one.
4. **Editor → Save Edits → Stop Editing.**

### T6. Tracing a utility line
1. **Editor → Start Editing**. Open **Create Features** (Editor → Editing Windows → Create Features) and pick **Utilities**.
2. Draw a short test line anywhere and double-click to finish.
3. In the attribute table, click **utility_type** and **confidence**.
   - **Pass:** both show dropdown lists.
4. **Save Edits**, then look at **traced_by** / **traced_date**.
   - **Pass:** they filled in automatically (your Windows user name and today's date).
5. Delete the test line, **Save Edits**, **Stop Editing**.

### T7 (optional). Georeferencing a placed sheet
1. **Customize → Toolbars → Georeferencing**. In its layer list, pick one of the plans.
2. **Add Control Points**: click a point on the plan, then the matching point on the aerial. Add two or three.
3. **Georeferencing → Update Georeferencing**.
   - **Pass:** the sheet moves and stays there after you remove and re-add it.

### Results to send back

| Check | Pass / fail | Notes |
|---|---|---|
| ArcMap version | | |
| T1 Geodatabase opens | | |
| T2 Aerial underneath | | |
| T3 Plans open in place | | which sheet failed, how |
| T4 Linework see-through | | did the fix work? |
| T5 Review dropdown editable | | |
| T6 Tracing: dropdowns + auto names/dates | | |
| T7 Georeferencing (optional) | | |

---

## Part 2: Everyday checklist in ArcMap

### Set up the map (once per project)
1. Copy the full package (not the test one) to the ArcMap computer.
2. New blank map. **Add Data**: from the `.gdb` add **RefStreets**, **PlanFootprints**, **Utilities**. Add the aerial (T2).
3. Colour the outlines by status: right-click **PlanFootprints** → **Properties** → **Symbology** → **Categories → Unique values** → Value Field **place_status** → **Add All Values**. Make each symbol hollow with a coloured outline: *from PDF* blue, *from coordinates* purple, *good* green, *check* orange, *poor* red.
4. **Labels** tab → tick **Label features in this layer** → Label Field **plan_id**.
5. Add the **linework** folder's `_ink.tif` files. Select them all in the Table of Contents, then right-click → **Group**. Turn the group on and off as needed.
6. **File → Save As** to keep the map document (.mxd) with the project.

### Check each sheet
1. Open the PlanFootprints attribute table. Select a row → right-click → **Zoom To Selected**.
2. Turn on that sheet's linework only (or add the scan from **plans**).
3. Check against the aerial: **right place** (look at intersections and curves), **right way round** (street names next to their roads), **right size** (both ends of the sheet fit).
4. With **Start Editing** on, set **review** to *correct / close / wrong* and add a **review_note** (e.g. "~20 ft north"). **Save Edits**.

### Fine-tune, trace and tag
Follow WORKFLOW.md sections C–F. The ArcMap equivalents:
- Georeference with the **Georeferencing toolbar** (Add Control Points → Update Georeferencing).
- Trace with **Editor → Create Features → Utilities**.
- Fill in `source_plan`, `plan_date`, `plan_author_firm`, `confidence` and `evidence_notes` for every line. `traced_by`/`traced_date` fill in automatically.

### Bringing the work back
The review verdicts and traced lines are in the package's geodatabase. **Only one copy should be edited at a time.** When the office is done, copy the package's `.gdb` back to the Pro computer and tell whoever runs PlanTrace there. Don't overwrite the Pro computer's geodatabase without checking which one has the newer edits.

Sheets you georeferenced in ArcMap keep your position: PlanTrace never overwrites georeferencing it didn't write.

## Good to know
- ArcMap may say a layer has an **unknown coordinate system**; click OK. The positions are in the project's coordinate system (see config.py).
- If ArcMap asks to **build pyramids**, you can say No: they're included with the images.
- The **Placement Review** map and the **PlanTrace toolbox** are ArcGIS Pro only; this checklist replaces them in ArcMap.
