r"""
Step 5: Read the text on every staged plan page.

For each page in plan_manifest.csv it:
  - straightens the scan and reads all the text with Tesseract (fast)
  - if Ollama is installed (see README): asks the local vision model to read
    the page too; on hard pages (Tesseract found < 2 streets) it also reads
    zoomed-in pieces of the page (slow, a few minutes per page)
  - recognizes real street names (from RefStreets, made by step 04)
  - finds scale notes, dates, stationing, and who made the drawing
  - sorts the page into: plan / correspondence (emails, tickets) / unknown

Results:
  plan_text\<plan_id>.json        everything it read, per page
  plan_manifest.csv               new columns filled in:
        sheet_kind, streets_found, scale_found, dates_found,
        stations_found, firm_found, read_by

SLOW ON PURPOSE: big sheets take 1-4 minutes each. A full set of ~65 pages
can take an hour or more. You can stop it any time (Ctrl+C) and run it
again: pages already read are skipped. Close ArcGIS Pro while it runs if
your computer feels slow.

Options:
  python scripts\05_read_plans.py            read pages not yet read
  python scripts\05_read_plans.py --redo     read every page again
  python scripts\05_read_plans.py --only GAS     only pages whose plan_id contains "GAS"
  python scripts\05_read_plans.py --no-vision    Tesseract only (fast), even if Ollama is installed

Pages already read by Tesseract alone get the vision pass added the next
time you run this with Ollama available (Tesseract is not repeated).
"""

import csv
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from plantrace import read, vision

NEW_COLUMNS = ["sheet_kind", "streets_found", "scale_found", "dates_found",
               "stations_found", "firm_found", "read_by"]


def load_street_names():
    import arcpy
    fc = os.path.join(config.GDB_PATH, config.STREETS_FC_NAME)
    if not arcpy.Exists(fc):
        print("RefStreets not found. Run step 04 first.")
        sys.exit(1)
    return sorted({r[0] for r in arcpy.da.SearchCursor(fc, ["street_name"]) if r[0]})


def load_manifest():
    with open(config.MANIFEST_PATH, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        return list(reader), list(reader.fieldnames)


def save_manifest(rows, columns):
    for c in NEW_COLUMNS:
        if c not in columns:
            columns.append(c)
    tmp = config.MANIFEST_PATH + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, config.MANIFEST_PATH)  # only replace once fully written


def summarize(result):
    """Turn a read result into the short manifest columns."""
    streets = sorted(result["streets"], key=lambda n: -result["streets"][n])
    v = result.get("vision") or {}
    scales = set(result["scale"]) | ({v["scale"]} if v.get("scale") else set())
    return {
        "sheet_kind": result["sheet_kind"],
        "streets_found": "; ".join(streets),
        "scale_found": "; ".join(sorted(scales)),
        "dates_found": "; ".join(result["dates"][:5]),
        "stations_found": "; ".join(v.get("stations", [])[:6]),
        "firm_found": v.get("firm_or_owner", ""),
        "read_by": "tesseract+vision" if v else "tesseract",
    }


def add_vision(result, image, street_index, log):
    """Run the vision model on a page and fold its findings into result."""
    tess_streets = len(result["streets"])
    hard = tess_streets < 2
    log("     vision model reading{}...".format(" (hard page: zooming in)" if hard else ""))
    v = vision.read_with_vision(image, config.OLLAMA_MODEL, config.OLLAMA_URL,
                                use_tiles=hard, log=log)
    result["vision"] = v
    # Check the model's street names against the real NYS names, the same way
    # Tesseract's text is checked. Unrecognized names are kept in the JSON only.
    lines = v["street_names"] + ["ROUTE " + r for r in v["route_numbers"]]
    for name, score in read.match_streets(lines, street_index).items():
        if name not in result["streets"]:
            result["streets"][name] = score
            result.setdefault("streets_from_vision", []).append(name)
    if v.get("date") and v["date"] not in result["dates"]:
        result["dates"].insert(0, v["date"])
    # Sheet kind: streets win; otherwise trust the model's judgment of the page.
    if result["streets"]:
        result["sheet_kind"] = "plan"
    elif v.get("sheet_type") == "correspondence":
        result["sheet_kind"] = "correspondence"
    return result


def main():
    redo = "--redo" in sys.argv
    only = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else None

    if not os.path.exists(config.TESSERACT_EXE):
        print("Tesseract not found at:", config.TESSERACT_EXE)
        print("Install it (see README, 'Automatic placement setup') or fix TESSERACT_EXE in config.py.")
        sys.exit(1)
    if not os.path.exists(config.MANIFEST_PATH):
        print("No plan_manifest.csv yet. Run step 02 first.")
        sys.exit(1)

    use_vision = config.USE_VISION and "--no-vision" not in sys.argv
    if use_vision:
        if not vision.is_running(config.OLLAMA_URL):
            print("Ollama isn't running, so this run uses Tesseract only.")
            print("(Start Ollama from the Start menu, or run with --no-vision to hide this.)\n")
            use_vision = False
        elif not vision.has_model(config.OLLAMA_MODEL, config.OLLAMA_URL):
            print("Ollama is running but the model '{}' isn't downloaded.".format(config.OLLAMA_MODEL))
            print("In a command prompt run:  ollama pull {}\n".format(config.OLLAMA_MODEL))
            use_vision = False
        else:
            print("Vision model: {} (local)".format(config.OLLAMA_MODEL))

    os.makedirs(config.PLAN_TEXT_DIR, exist_ok=True)
    street_names = load_street_names()
    read.HIGHWAY_ALIASES.update(getattr(config, "HIGHWAY_ALIASES", {}))
    vision.AREA = getattr(config, "VISION_AREA", vision.AREA)
    street_index = read.build_street_index(street_names)
    rows, columns = load_manifest()
    print("Recognizing {} street names. {} pages in manifest.\n".format(len(street_names), len(rows)))

    todo = [r for r in rows if (r.get("status") or "").lower() != "skip"
            and (only is None or only.lower() in r["plan_id"].lower())]
    done = 0
    started = time.time()
    try:
        for n, row in enumerate(todo, 1):
            out_json = os.path.join(config.PLAN_TEXT_DIR, row["plan_id"] + ".json")
            image = os.path.join(config.STAGED_PLANS_DIR, row["staged_image"])
            result = None
            if os.path.exists(out_json) and not redo:
                with open(out_json, encoding="utf-8") as f:
                    result = json.load(f)
                needs_vision = (use_vision and "vision" not in result
                                and result["sheet_kind"] != "correspondence")
                if not needs_vision:
                    row.update(summarize(result))
                    continue
            print("[{}/{}] {}".format(n, len(todo), row["plan_id"]))
            if not os.path.exists(image):
                print("     image missing, skipped:", image)
                continue
            t = time.time()
            if result is None:
                result = read.read_page(image, street_names, config.TESSERACT_EXE, config.PDF_DPI)
                print("     tesseract: {}".format("; ".join(result["streets"]) or "-"))
            if use_vision and result["sheet_kind"] != "correspondence":
                try:
                    add_vision(result, image, street_index, print)
                except Exception as e:  # never lose the Tesseract result over a vision hiccup
                    print("     vision model failed on this page ({}); kept Tesseract result".format(e))
            with open(out_json, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=1)
            row.update(summarize(result))
            print("     {:<15} {:.0f}s  streets: {}".format(
                row["sheet_kind"], time.time() - t, row["streets_found"] or "-"))
            done += 1
            save_manifest(rows, columns)  # save after every page, so nothing is lost
    except KeyboardInterrupt:
        print("\nStopped. Progress so far is saved; run again to continue.")
    save_manifest(rows, columns)

    kinds = {}
    for r in todo:
        kinds[r.get("sheet_kind") or "(not read)"] = kinds.get(r.get("sheet_kind") or "(not read)", 0) + 1
    print("\nRead {} page(s) this run in {:.0f} min.".format(done, (time.time() - started) / 60))
    print("Sheet types:", ", ".join("{} {}".format(v, k) for k, v in sorted(kinds.items())))
    print("Open plan_manifest.csv to see what was found for each page.")


if __name__ == "__main__":
    main()
