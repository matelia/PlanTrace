# Manual workflow: georeference → trace → tag

This is the part only you can do. The scripts get everything ready; this checklist keeps each plan consistent so the provenance is trustworthy later.

Work one plan image at a time. Use `plan_manifest.csv` (open it in Excel) as your to-do list.

---

## A. One-time map setup (per project)

1. Open ArcGIS Pro and create a new project (Map template). Save it inside `projects\<PROJECT_NAME>\`.
2. **Map** tab → **Add Data** → add `projects\<PROJECT_NAME>\<PROJECT_NAME>.gdb\Utilities`.
3. Add your reference imagery (current ortho, plus any historical imagery you have).
4. Optional but useful: add the project limits / alignment so you can see what's in scope.
5. In the map's properties, confirm the coordinate system matches the Utilities layer.

---

## A2. Checking the automatic placements (after steps 07 and 08)

Goal: for each placed sheet, decide **correct / close / wrong** and record it. This takes ~1 minute per sheet. These verdicts are the first accuracy numbers for the tool.

**Setup (once):**
1. Close ArcGIS Pro. In the Python Command Prompt run `python scripts\08_make_review_map.py`.
2. Open your project's `.aprx` (in the project folder). In the **Catalog** pane → **Maps** → double-click **Placement Review**.
3. You'll see the orthoimagery, yellow street centerlines, the sheet outlines (**green = good, orange = check, red = poor**, labelled with the sheet name), and every sheet's **linework in magenta**: the plan's own lines with no white background, so the aerial shows through.
4. Right-click **PlanFootprints** → **Attribute Table**. Dock it at the bottom. Sort by `place_status`.
5. Where many sheets overlap (around the bridge) the magenta gets busy. In Contents, untick the **Plan linework - …** groups you're not looking at.
6. Groups: **from PDF** (blue outlines) = the PDF carried its own exact position from the utility's GIS, nothing to check except that it's the right sheet. **good / check / poor** = placed automatically. **index maps** (1"=200' or smaller) are switched off: their lettering is huge at street level.

**For each sheet (start with the green ones):**
1. In the attribute table, click the row's grey box on the left, then right-click it → **Zoom To**.
2. In Contents, open the matching **Plan linework - good / check / poor** group. Untick the other sheets in it so only this one's magenta shows. (If you want the original scan with its colours and text, tick it in **Scans - …**; it's 25% see-through.)
3. Check, in this order:
   - **Right place?** Do the plan's roads sit on the real roads in the imagery? Look at an intersection or a curve, not a straight stretch (a straight road can slide).
   - **Right way round?** Street names on the plan should sit next to the roads they name. A sheet turned 180° can still look aligned.
   - **Right size?** Curb lines and edges should match the road width in the imagery, along the whole sheet, not just in the middle. If it fits in the middle but drifts toward the ends, the scale is off.
   - For a closer look: select the image in Contents → **Raster Layer** tab → **Swipe**, and drag across the map.
4. Record your verdict in the attribute table row:
   - `review` (dropdown): **correct** = within about a road width, ready to trace on; **close** = right area and roughly right angle, needs a few control points; **wrong** = wrong place, flipped, or badly off in size/rotation.
   - `review_note`: anything useful, e.g. "~40 ft east", "scale too small", "schematic, not to scale".
5. Untick the image before moving to the next sheet (they're big; keeping many on slows Pro down).
6. Every few sheets: **Edit** tab → **Save** (saves your review entries).

Your verdicts are kept if step 07 is run again. If a sheet gets re-placed, its note says "placed again ... re-check".

**Answer-key sheets:** pick a handful of sheets (easy and hard ones) and, after reviewing them, georeference them properly (section C). Comparing those against the automatic placement gives exact error numbers in feet and degrees.

---

## B. Before georeferencing a plan

1. Pick the next row in `plan_manifest.csv` with status `staged`.
2. Skim the plan and fill in **plan_date** and **plan_author_firm** in the manifest now, while you're looking at the title block.
3. Decide if it's worth placing. Not useful (cover sheet, details only, out of limits)? Set status to `skip`, add a note, move on.
4. Note the references you'll match: pole IDs, structures, house numbers, curb lines, stationing, monuments.

---

## C. Georeference (ArcGIS Pro)

1. **Map** tab → **Add Data** → add the staged image from `staged_plans\`. If Pro asks to build pyramids, say yes.
   - If step 07 placed it (`place_status` in the manifest is `good` or `check`), it opens **already roughly in place**. Right-click it → **Zoom To Layer**. Check it against the imagery. If it's close, go to step 4 and just fine-tune with control points. If it's clearly wrong (`poor`, or you can see it is), do step 3 as usual.
   - Pro may say the image has an unknown coordinate system. That's fine: the world file is in the project's coordinate system (EPSG 2262).
2. Select the image in Contents → **Imagery** tab → **Georeference**.
3. Click **Fit to Display** to bring the plan roughly into the map view (skip this if it's already placed).
4. **Add Control Points**: click a point on the plan, then the matching point on the imagery. Repeat.
   - Use features spread across the whole plan, not clustered in one corner.
   - Prefer things that don't move: pole bases, manholes, building corners, curb returns.
   - Aim for 4+ points. Start with 1st Order Polynomial (Affine); only use a higher order if you have many good points and the plan is clearly distorted.
5. Open the **Control Point Table** and check residuals. Delete or re-place any point that's way off.
6. Click **Save** (keeps it linked) or **Save as New** (writes a new georeferenced image). Pick one approach and stick with it for the whole project.
7. Close Georeference. Update the manifest: status → `georeferenced`, and note which references you matched (e.g. "poles 1234, 1240; MH at Sta 12+50").

Once you've saved your own georeferencing, step 07 never touches that image again. What step 07 originally suggested stays in `<image>.plantrace.json`, so your result can later be compared against it (that comparison is how we get accuracy numbers).

---

## D. Trace utilities

1. **Edit** tab → **Create** → pick the Utilities layer → **Line**.
2. Trace each utility shown on the plan. One line per run between clear breaks (e.g. structure to structure) is easier to fix later than one giant line.
3. Only trace what this plan actually shows. If two plans show the same line, trace it from the better plan and mention the other in evidence notes.
4. **Save** edits often (Edit tab → Save).

---

## E. Tag every line (provenance — never skip this)

With the new line(s) selected, fill in the attributes:

| Field | What to enter |
|---|---|
| utility_type | from the dropdown |
| source_plan | the `plan_id` from the manifest (exact) |
| plan_date | date from the title block, if known |
| plan_author_firm | from the title block |
| confidence | see guide below |
| evidence_notes | *why* it's here, e.g. "matched pole #1234 & #1240; trench scar in 2008 aerial" |
| traced_by | *automatic* (your Windows user name) |
| traced_date | *automatic* |

`last_edited_by` / `last_edited_date` also fill in automatically whenever you change a line later.

Tip: select all lines from the same plan and fill the shared fields (source_plan, plan_date, firm) once in the Attributes pane.

**Confidence guide** (adjust to your judgment, but keep it consistent):
- **high** — plan matched to 3+ well-spread fixed references with small residuals, *and* the line agrees with a field/imagery clue (scar, valve boxes, manholes, markings).
- **medium** — good georeference but no independent confirmation of the line itself, or only 2 solid references.
- **low** — loose fit, schematic or "approximate" plan, or the line was inferred.

---

## F. Close out the plan

1. Save edits.
2. Manifest: status → `traced`. Add anything odd in notes.
3. Remove the plan image from the map (keeps Pro fast).
4. Next plan.

Every few plans (and always before sharing results), run `python scripts\03_check_progress.py`. It flags lines with missing provenance, mistyped plan names, and manifest statuses that don't match the map.
