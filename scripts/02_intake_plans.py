r"""
Step 2: Stage scanned record plans for georeferencing.

What it does:
  1. Looks in projects\<PROJECT_NAME>\raw_plans\ (and its sub-folders).
  2. PDFs  -> converts every page to a TIFF (one TIFF per page).
     Images (TIF/JPG/PNG) -> copies them as-is.
     Results go in projects\<PROJECT_NAME>\staged_plans\.
  3. Writes/updates plan_manifest.csv: one row per staged image, with blank
     columns for you to fill in (plan date, firm, status, notes).

Your originals in raw_plans\ are never modified.

SAFE TO RE-RUN: images already staged are skipped, and rows you have already
edited in plan_manifest.csv are kept exactly as they are. New plans are added
as new rows at the bottom.
"""

import csv
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

MANIFEST_COLUMNS = [
    "plan_id",           # unique name, also the staged image's file name (no extension)
    "source_file",       # original file, relative to raw_plans\
    "page",              # page number (PDFs), blank for single images
    "staged_image",      # staged image, relative to staged_plans\
    "plan_date",         # YOU fill in: date on the plan, e.g. 1987-05-12
    "plan_author_firm",  # YOU fill in: who drew it
    "status",            # staged -> georeferenced -> traced  (or: skip)
    "notes",             # YOU fill in: anything useful
]


def safe_name(text):
    """Turn a file name into something safe for ArcGIS (no spaces/odd characters)."""
    return re.sub(r"[^A-Za-z0-9_-]+", "_", text).strip("_")


def find_raw_plans():
    """Return a sorted list of plan files under raw_plans\\ (including sub-folders)."""
    wanted = set(config.IMAGE_EXTENSIONS + config.PDF_EXTENSIONS)
    found = []
    for folder, _subfolders, files in os.walk(config.RAW_PLANS_DIR):
        for f in files:
            if os.path.splitext(f)[1].lower() in wanted:
                found.append(os.path.join(folder, f))
    return sorted(found)


def plan_id_for(raw_path):
    """Build an ID from the path inside raw_plans\\, so same-named files in
    different sub-folders don't collide."""
    rel = os.path.relpath(raw_path, config.RAW_PLANS_DIR)
    return safe_name(os.path.splitext(rel)[0])


def load_manifest():
    """Read the existing manifest (if any) so we never lose your edits."""
    rows = []
    if os.path.exists(config.MANIFEST_PATH):
        with open(config.MANIFEST_PATH, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
    return rows


def save_manifest(rows):
    # Keep any extra columns added later (by step 05 or by you in Excel).
    columns = list(MANIFEST_COLUMNS)
    for r in rows:
        for c in r:
            if c and c not in columns:
                columns.append(c)
    with open(config.MANIFEST_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def stage_image(raw_path, pid):
    """Copy an image file into staged_plans\\. Returns the new row (or None if it already exists)."""
    ext = os.path.splitext(raw_path)[1].lower()
    out_name = pid + ext
    out_path = os.path.join(config.STAGED_PLANS_DIR, out_name)
    if not os.path.exists(out_path):
        shutil.copy2(raw_path, out_path)
        print("   copied   ->", out_name)
    return [(pid, "", out_name)]


def stage_pdf(raw_path, pid):
    """Convert each PDF page to a TIFF. Returns a list of (plan_id, page, file name)."""
    import arcpy  # only needed for PDFs; imported here so image-only runs are fast

    results = []
    for page in range(1, config.MAX_PDF_PAGES + 1):
        page_id = "{}_p{:02d}".format(pid, page)
        out_name = page_id + ".tif"
        out_path = os.path.join(config.STAGED_PLANS_DIR, out_name)

        if os.path.exists(out_path):
            results.append((page_id, str(page), out_name))
            continue

        try:
            # Arguments: input, output, password, page, map, clip, resolution
            arcpy.conversion.PDFToTIFF(raw_path, out_path, "", page, "", "", config.PDF_DPI)
        except arcpy.ExecuteError:
            if page == 1:
                print("   ERROR converting", os.path.basename(raw_path))
                print("  ", arcpy.GetMessages(2))
            # Asking for a page past the end of the PDF errors out: that's
            # how we know we've converted every page.
            break
        print("   converted -> {} ({} dpi)".format(out_name, config.PDF_DPI))
        results.append((page_id, str(page), out_name))
    return results


def main():
    if not os.path.isdir(config.RAW_PLANS_DIR):
        print("raw_plans folder not found. Run step 01 first:", config.RAW_PLANS_DIR)
        sys.exit(1)
    os.makedirs(config.STAGED_PLANS_DIR, exist_ok=True)

    raw_files = find_raw_plans()
    if not raw_files:
        print("No plans found in", config.RAW_PLANS_DIR)
        print("Copy your scanned PDFs/TIFFs/JPGs there and run this again.")
        return

    rows = load_manifest()
    known_ids = {r["plan_id"] for r in rows}
    added = 0

    print("Staging {} plan file(s) for project {}\n".format(len(raw_files), config.PROJECT_NAME))
    for raw_path in raw_files:
        rel = os.path.relpath(raw_path, config.RAW_PLANS_DIR)
        print(rel)
        pid = plan_id_for(raw_path)
        ext = os.path.splitext(raw_path)[1].lower()

        if ext in config.PDF_EXTENSIONS:
            staged = stage_pdf(raw_path, pid)
        else:
            staged = stage_image(raw_path, pid)

        for page_id, page, out_name in staged:
            if page_id in known_ids:
                continue  # already in the manifest; keep the user's edits
            rows.append({
                "plan_id": page_id,
                "source_file": rel,
                "page": page,
                "staged_image": out_name,
                "plan_date": "",
                "plan_author_firm": "",
                "status": "staged",
                "notes": "",
            })
            known_ids.add(page_id)
            added += 1

    save_manifest(rows)
    print("\nDone. {} new image(s) added to the manifest ({} total).".format(added, len(rows)))
    print("Manifest:     ", config.MANIFEST_PATH)
    print("Staged images:", config.STAGED_PLANS_DIR)
    print("Next: follow WORKFLOW.md to georeference each staged image in ArcGIS Pro.")


if __name__ == "__main__":
    main()
