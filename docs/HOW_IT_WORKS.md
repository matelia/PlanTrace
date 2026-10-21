# How PlanTrace works, and what we learned building it

This is the story of the project: what each stage does, what went wrong along the way, and the numbers. It's written for engineers who place record plans by hand today.

## The problem

Utility record plans arrive as scanned PDFs: a mix of hand-drafted sheets from decades ago, printouts from a utility's GIS, contract plans with stationing, and screenshots of web maps. Before you can trace anything, each sheet has to be georeferenced by hand: find where it is, pick control points, fix the scale and rotation. With 60+ sheets per project, that's days of clicking.

The goal was a tool that does the first placement automatically ("roughly within a road width, roughly the right angle"), so the engineer only fine-tunes, using only free tools on an ordinary laptop, with nothing leaving the machine.

## 1. Read

Each page is turned into a 300-dpi image and read twice:

- **Tesseract OCR**, several passes: the page as-is at 0/90/270 degrees (street names are printed along their roads), and a cleaned copy that is straightened (scans are often tilted several degrees) and has long ruled lines erased (labels that touch an underline are otherwise unreadable).
- Optionally a **local vision model** (a small multimodal model through Ollama) for an overview of the page: sheet type, title block, firm, dates, scale. On hard pages it also reads zoomed-in tiles. On a laptop CPU this takes minutes per page, so it runs overnight.

The text is matched against the real street names in the search area. Lessons:

- **One-word street names cause most false matches.** "ADAMS" in "ADAMS COUNTY" becomes Adams Street, a person's name becomes a street, "GROUND" fuzzy-matches "Round Trail". A blocklist doesn't scale; the fix that works is a rule: a one-word name only counts if the text around it on the sheet matches the rest of the official name, including its type ("MAPLE RD" yes, "MAPLE" alone in a sentence no), or it stands alone as a map label and isn't a place name.
- **Don't trust what a vision model says it read in fields with examples in the prompt.** The model repeated the example stationing values from its own instructions on many sheets. Stationing is taken from OCR text only.

## 2. Locate

One street name says little (a road can be miles long). Two streets that **cross** say a lot. For each sheet:

1. Turn each street name into road segments in the centerline data, merging names of the same physical road (route numbers, shared segments).
2. Find every point where two of the sheet's roads meet.
3. Score each crossing by how many of the sheet's roads pass within 1,000 ft. The best one wins; names that don't agree spatially (a misread, a street from the title block) are simply outvoted.

For sheets the streets can't settle, three more clues are **learned from the sheets that were placed**, and each is tested leave-one-out before it's trusted:

| Clue | How | Typical miss when tested |
|---|---|---|
| Utility grid/map numbers | a sheet shows its neighbours' grid numbers at the match lines; positions are interpolated within a series | ~250 ft |
| Route stationing | position along the route = offset + station; offset and direction learned from placed sheets | ~550 ft |
| Neighbouring pages | pages next to each other in a PDF usually show neighbouring areas | ~800 ft |

Stationing also caught a sheet the streets had placed ~2 miles off: its stations said it belonged between two neighbouring sheets.

## 3. Place

**Finding the lines.** Ink that runs one consistent direction over ~0.15 in of paper is kept (road edges, curbs, right-of-way lines); text, the sheet border and short marks are dropped. Ink is measured against the paper colour around it, because photocopies have grey paper.

**The search.** Street centerlines around the suggested location are drawn as a "closeness" map, separately for each direction (a plan line only counts near a street running the same way). Every rotation in 3° steps, at the stated scale (and 2x for half-size prints), with all shifts tested at once by FFT cross-correlation, then refined. The score is the share of line points near a matching street **minus what random lines would score at the same spot**; without that correction, a sheet shrunk small enough fits anywhere.

**Which way round.** Two crossing roads look the same turned 180°. The positions of street-name labels on the page (read once with Tesseract) settle it: "SPRING HILL" has to land next to Spring Hill Road.

**Scale.** The stated scale is trusted (±8%): busy sheets pull a free scale 15–30% off. On contract plans with metric stationing along a baseline, the station labels give the scale exactly (20 m between labels) and the baseline must follow the stationed route in the direction the stations increase.

**Fine-tuning against the aerial.** The main search gives full credit anywhere within 40 ft of a centerline (plans show edges, the data is centerlines), so it settles anywhere on a flat plateau: right spot and angle, but 10–30 ft and ~1° off. The last step matches the plan's lines to edges in the leaf-off aerial photo instead (curbs, pavement edges) on a 2 ft grid, within ±2.5° and 60 ft, scale held. It's only kept if the match clearly improves.

## 4. Write and review

- The position is written **inside the image file and as a world file, identically**. Lesson learned the hard way: ArcGIS reads georeferencing stored inside a TIFF before the world file. Some converted PDF pages carried their own internal position, and a "define projection" step froze wrong positions into others, so sheets showed up miles away in ArcGIS Pro while every check picture (which read the world file) looked right. Verify what ArcGIS itself draws, not just your own files.
- **Georeferenced PDFs** (GIS exports) carry their real position. They're placed exactly and double as ground truth for measuring the automatic placement.
- **Check pictures** show the real aerial photo with the plan's ink in magenta; the **review map** has the same linework as see-through layers, grouped by status (from PDF / good / check / poor). The engineer marks each sheet correct / close / wrong; those verdicts survive re-runs.
- **Your own georeferencing is never overwritten**: a record of what the tool wrote tells its own files apart from yours.

## Results (one real project, 65 pages)

- 13 sheets placed exactly from their PDFs, 37 placed and fine-tuned automatically, 3 photo-based web maps left for hand placement.
- Against ground truth (12 sheets with GIS positions): the automatic fit was 3–30 ft off at the sheet centre with a +0.5° to +4° rotation bias; after fine-tuning against the aerial, 0–2 ft on 6 of 10 sheets and 7 ft on 2, rotation within ±0.2° on most.
- Engineer review: no sheet rated "good" was wrong; both rated "poor" were wrong. Half of the sheets placed from the utility's own GIS were only "close" to the aerial, so the utility's GIS itself isn't survey-grade either.

## Lessons

- **Measure against something real.** Eyeballing pictures is useful, but the numbers came from sheets with known positions, and they revealed a rotation bias no picture showed.
- **Look at what the user's software shows.** The worst bug was invisible in the tool's own output.
- **Some sheets can't be fitted.** Schematic sheets draw roads crossing at 90° that really cross at 55°; no world file can fit both. Saying "poor" is the right answer.
- **Tuning on one project overfits.** One rule (prefer the scale most sheets in a PDF use) helped one sheet and hurt three, and was removed. The honest test is the next project, run unchanged.

## Possible next steps

- Match photo-based web-map screenshots directly against the aerial.
- Toolbox tools for setup and reading, not just locate/place/review.
- With reviewed sheets from several projects, learn what a correct fit looks like instead of hand-tuned rules.
