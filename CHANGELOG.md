# Changelog

All notable changes to PlanTrace. A new entry is added whenever a working step is finished.

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
