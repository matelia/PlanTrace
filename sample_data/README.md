# Sample data

**This folder contains no plan sheets, on purpose.** Utility record plans show where buried infrastructure is, and the plans used to develop PlanTrace are not public. They must never be committed. The repository's `.gitignore` blocks PDFs, scans and images here and everywhere else.

To try PlanTrace, use plans that are already public, or make synthetic ones.

## Where to find public plans

- **State DOT contract plans.** Many state departments of transportation publish plan sets for advertised or past construction contracts (bid documents) on their websites. These often include highway plan sheets with stationing and existing utilities.
- **City and county open-data portals.** Some municipalities publish as-built drawings, water or sewer maps, or engineering plans as PDFs.
- **Planning board and permit records.** Site plans submitted for public review are often posted online as PDFs.
- **Georeferenced PDF maps.** GIS exports with real coordinates are handy because PlanTrace reads their true position. That gives you ground truth to measure the automatic placement against.

Check the terms of use of whatever you download, and don't redistribute it here.

## Synthetic sheets

You can also draw a test sheet yourself: print or export a few streets from public centerline data at a known scale (e.g. 1"=50'), add street-name labels and a title block, and save it as a PDF. Because you know exactly where it belongs, it's a good accuracy test.

## Using a sample

1. Set `PROJECT_NAME` and `SEARCH_AREA` in `config.py` to match the area the plans show.
2. Run steps 01 and 02, then copy the PDFs into `projects\<PROJECT_NAME>\raw_plans\` (that folder is ignored by git).
3. Continue with the steps in the main README.
