# PlanTrace

**Reconstructing where buried utilities are from old scanned record plans is slow, manual work on every highway project. PlanTrace automates the first, most repetitive part: getting each plan sheet onto the map.**

![PlanTrace placing scanned plan sheets in ArcGIS Pro](docs/images/demo.gif)
<!-- Placeholder: add a screenshot or GIF of plan sheets landing in ArcGIS Pro (docs/images/demo.gif). -->

## What it does

Before design starts, a highway engineer collects record plans from every utility in the area: gas, water, sewer, electric, telecom. They arrive as dozens of scanned PDF sheets at different scales, from different decades, hand-drafted or printed from GIS, often tilted, and almost never georeferenced. Each one has to be placed on the map by hand before anything can be traced.

PlanTrace does that first placement:

1. **Reads each sheet locally**: street names, scale, stationing, dates, map numbers. It uses **Tesseract OCR**, plus an optional local vision model through **Ollama** for hand-lettered sheets.
   It also reads **datum notes** (NAD 27 / NAD 83, State Plane, UTM, NGVD 29 / NAVD 88) and each PDF's **file properties** (scan, CAD plot, GIS export, email print). If a sheet has **grid-tick coordinates**, it's placed from those, and the datum is worked out even when the sheet doesn't name it: each candidate system is tried (NAD 27 converted with NADCON), and the one that lands in the project area wins.
2. **Finds where the sheet is** from the streets named on it that cross each other, using public street centerline data.
3. **Places it in ArcGIS Pro**: a Python script fits the sheet's road lines onto the streets (scale, rotation, position), fine-tunes them against the aerial photo, and writes the position into the image so it opens already in place.
4. **Builds a review map**: aerial photo, streets, and every sheet's linework in see-through magenta, each labelled good / check / poor.

**It assists, it doesn't replace.** The engineer still checks every placement, adjusts it with control points where needed, and traces the utilities. PlanTrace never overwrites georeferencing the engineer has done, and it keeps a record of where every placement came from.

**It runs fully locally.** No plan leaves the computer. It uses only what ships with ArcGIS Pro (arcpy, numpy, scipy, Pillow, GDAL), plus two free stand-alone programs. **Nothing is installed into ArcGIS Pro's Python environment.**

## Requirements

- Windows with **ArcGIS Pro** (tested with 3.7) and a Standard or Advanced license.
- **Tesseract OCR** (free): <https://github.com/UB-Mannheim/tesseract/wiki>
- Optional: **Ollama** (free) with a small vision model, e.g. `qwen3.5:4b`: <https://ollama.com>. It runs on the CPU, so it's slow (minutes per page), but it reads hand-lettering much better.
- Internet access only for public map data: street centerlines and aerial photos. The defaults are New York State's free services; elsewhere, point `config.py` at equivalent public layers.

## Setup

1. Download this repository and put the folder somewhere **outside OneDrive/Dropbox** (they lock geodatabases), e.g. `C:\Users\<you>\Documents\PlanTrace`.
2. Install Tesseract with its default options. Optionally install Ollama, then in a Command Prompt run `ollama pull qwen3.5:4b`.
3. Open **Start → ArcGIS → Python Command Prompt**. This is ArcGIS Pro's own Python; no other Python or virtual environment is needed.
4. Go to the folder:
   ```
   cd C:\Users\<you>\Documents\PlanTrace
   ```
5. Check that everything is found:
   ```
   python scripts\00_check_environment.py
   ```
6. Open `config.py` in Notepad. Set at least `PROJECT_NAME`, `SPATIAL_REFERENCE_WKID` (your State Plane zone) and `SEARCH_AREA` (a box around the project). Each setting is explained in the file.

## Running it

In the Python Command Prompt, from the PlanTrace folder:

1. `python scripts\01_create_geodatabase.py`: creates the project folders, a geodatabase, and a Utilities line layer with provenance fields.
2. Copy the plan PDFs or scans into `projects\<PROJECT_NAME>\raw_plans\`, then run `python scripts\02_intake_plans.py`. It converts every page to an image and lists them in `plan_manifest.csv`.
3. `python scripts\04_get_streets.py`: downloads street centerlines for the search area.
4. `python scripts\05_read_plans.py`: reads every page. With the vision model this is best run overnight; it can be stopped and resumed.
   Then `python scripts\05b_find_datum.py`: reads each PDF's file properties, datum notes on the sheets, and coordinate labels. A sheet with grid ticks is placed directly from its own coordinates.
5. `python scripts\06_locate_plans.py`: suggests where each sheet goes.
6. `python scripts\07_place_plans.py`: places every sheet and fine-tunes it against the aerial.
7. `python scripts\08_make_review_map.py` (with ArcGIS Pro closed): adds a **Placement Review** map to your ArcGIS project.

Steps 5–7 are also available **inside ArcGIS Pro** as a toolbox: **Catalog pane → right-click Toolboxes → Add Toolbox → `PlanTrace.pyt`**.

Then review the placements in ArcGIS Pro: [WORKFLOW.md](WORKFLOW.md), section A2, explains how to mark each sheet correct / close / wrong, georeference, trace and tag.

## Results

Tested on one real highway project: 65 scanned pages from 15 PDFs from about ten utilities and agencies.

**Time per sheet** (placing a sheet on the map, before tracing):

| | Minutes per sheet |
|---|---|
| By hand (before) | _TBD_ |
| With PlanTrace, including review and fine-tuning (after) | _TBD_ |

**Accuracy**, measured on sheets whose true position was known (12 sheets exported from a utility's GIS, which carry their real coordinates):

| | Fit to street centerlines | + fine-tuned against the aerial photo |
|---|---|---|
| Error at sheet centre | 3–30 ft | **0–2 ft on 6 of 10 sheets**, 7 ft on 2 |
| Rotation error | +0.5° to +4° | within ±0.2° on most |

**Engineer review** of 19 placements in ArcGIS Pro: none of the sheets the tool rated "good" was wrong, and both sheets it rated "poor" really were wrong.

## Status and limitations

PlanTrace is a working prototype (v0.1), tested on one project so far. The honest test is the next project, run without changes.

What it can't do (yet):
- **Schematic sheets**, drawn not to scale, can't be fitted. Roads drawn crossing at 90° that really cross at 55° have no single correct scale and rotation. These come out "poor".
- **Web-map screenshots on aerial photos** have no drawn lines to fit. They're detected and left for hand placement.
- **Sheets with no readable street names** and no other clues (map numbers, stationing, neighbouring pages) are left unplaced.
- **Busy sheets**, or sheets with no stated scale, are often only "close" and need a few control points.
- **Reading with the vision model is slow** on a laptop CPU (minutes per page).
- **Defaults are set up for New York State**: State Plane coordinates, NYS street centerlines and NYS orthoimagery. Other areas need equivalent public layers in `config.py`.
- **Only steps 06–08 are in the ArcGIS Pro toolbox**; setup, intake and reading still run from the command prompt.

The method, the full results and lessons learned are in [docs/HOW_IT_WORKS.md](docs/HOW_IT_WORKS.md). Changes are listed in [CHANGELOG.md](CHANGELOG.md).

## Sample data

This repository contains **no plan sheets**. Any sample data used with it is public or synthetic only. See [sample_data/README.md](sample_data/README.md) for where to find public plans to try it on. Real utility record plans should never be committed: the `.gitignore` excludes plans, scans, project folders and everything made from them.

## Repository layout

```
config.py              settings (the only file you normally edit)
PlanTrace.pyt          ArcGIS Pro toolbox (steps 06-08)
scripts\               the steps, 00-08, run in order
plantrace\             the shared code:
  read.py                page loading, straightening, OCR, street-name matching
  vision.py              local vision model client (Ollama)
  locate.py              location from street crossings
  hints.py               map numbers, stationing, neighbouring pages
  datum.py               file properties, datum notes, placement from coordinate labels
  place.py               line finding; scale/rotation/position search
  refine.py              fine-tuning against the aerial photo
  georef.py              writing positions into images; georeferenced PDFs
  review.py              check pictures and see-through linework overlays
WORKFLOW.md            the engineer's review -> georeference -> trace -> tag checklist
docs\HOW_IT_WORKS.md   method, results and lessons
sample_data\           where to find public sample plans (no plans included)
CHANGELOG.md           what changed in each version
```

## License

MIT. See [LICENSE](LICENSE).
