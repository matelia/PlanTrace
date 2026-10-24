# Changelog

All notable changes to PlanTrace. A new entry is added whenever a working step is finished.

## Unreleased (v0.2.0)

- **Datum, coordinates and file properties** (new step 05b, `plantrace/datum.py`):
  - Reads each PDF's file properties: which program made it (scan, CAD plot, GIS export, email or office print) and when.
  - Reads datum notes on the sheet: NAD 27 / NAD 83, State Plane or UTM, NGVD 29 / NAVD 88, benchmarks.
  - Reads grid-tick and coordinate labels with their position on the page. With enough labels, the sheet is placed from its own coordinates: each system in `config.COORDINATE_SYSTEMS` is tried (NAD 27 converted to NAD 83 with NADCON), and the one that lands in the project area is the datum, even when the sheet doesn't name it.
  - On the test project, a sewer plan with no datum written was found to be in NAD 27 State Plane and placed from its coordinates (it had been placed wrong by line fitting).
- Placement priority: the PDF's own georeferencing, then the sheet's coordinates, then fitting to the streets. New review-map group "from coordinates".
- **ArcMap offices** (new step 09, `ARCMAP.md`): packages the geodatabase, placed sheets and see-through linework in one folder that ArcMap 10.x opens directly, with a checklist and a 15-minute office test (`--test` makes a 3-sheet trial package).
- **Contacts and request drafts** (new step 10, `plantrace/records.py`): reads 811 design tickets (member utilities, contact names, phones) and printed emails, flags automatic senders that nobody reads, and works out per owner what to ask for: a GIS/CAD file for sheets that couldn't be placed or were reviewed wrong, the data behind GIS screenshots, missing dates or scales, the datum, and records from ticket members who sent nothing. Writes a contacts list (with the page each contact came from), a summary and one draft per owner. Nothing is sent.
- **Utility knowledge table** (new step 11): per owner and decade, what was seen on the sheets: file kinds, datums written or proven by coordinates, scales, firms, placement results and review verdicts. Observations only, each listing its evidence sheets; shared across projects.
- Dates: map and grid numbers (e.g. 1945.11) are no longer read as years.
- Safer review data: the footprint layer is rebuilt under a temporary name and swapped in only when complete, and review verdicts are backed up to `review_backup.csv` before every rebuild.

## v0.1.0 — first public version

What works today:

- **Project setup and intake** (steps 00–02): environment check, project folders and geodatabase with a provenance-tracked Utilities layer, PDF and scan intake to 300-dpi page images with a manifest.
- **Reference data** (step 04): downloads public street centerlines for the project area.
- **Reading** (step 05): Tesseract OCR with straightening and ruled-line removal, plus an optional local vision model (Ollama). Extracts street names, scale, dates, stationing, map numbers and the drafting firm, and sorts pages into plans and correspondence. Resumable.
- **Locating** (step 06): a suggested location for every sheet, from where the streets named on it cross. Weak sheets get help from map/grid numbers, route stationing and neighbouring pages, each tested leave-one-out before it's trusted. A stricter check removes false street matches from one-word names.
- **Placing** (step 07): finds the sheet's road lines and fits them onto the street centerlines (rotation, scale, position) with an FFT search. Street-name labels settle 180° ambiguity, and station labels along a baseline give the scale exactly. A final fine-tune against the aerial photo fixed a ~1° rotation bias. Georeferenced PDFs are placed exactly from their own position. Positions are written into each image and as a world file, identically. The engineer's own georeferencing is never overwritten.
- **Review** (step 08): a Placement Review map in ArcGIS Pro (aerial, streets, footprints by status, see-through linework), and aerial check pictures per sheet. Review verdicts (correct / close / wrong) survive re-runs.
- **ArcGIS Pro toolbox** (`PlanTrace.pyt`): Locate sheets, Place sheets, Build review map, run inside Pro.

Measured on one real project (65 pages): sheet centres within 0–2 ft of the true position on 6 of 10 ground-truth sheets after fine-tuning (3–30 ft before); rotation within ±0.2° on most.

Known limitations: schematic (not-to-scale) sheets and web-map screenshots can't be fitted; tested on one project only; defaults are for New York State data.
