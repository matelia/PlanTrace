r"""
Step 3 (run any time): Progress report + provenance check.

Read-only. It never changes your data. It reports:
  1. How many plans are staged / georeferenced / traced / skipped.
  2. Utility lines missing provenance (type, source plan, confidence, evidence).
  3. Lines whose source_plan doesn't match any plan_id in the manifest
     (usually a typo, which would break the link back to the plan).
  4. Plans marked "traced" that have no lines, and plans that have lines
     but aren't marked "traced" yet.

Tip: close ArcGIS Pro's edit session (save edits) before running, so the
report sees your latest lines.
"""

import csv
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
import arcpy

# Fields every line must have filled in. Edit this list if your rules change.
REQUIRED_FIELDS = ["utility_type", "source_plan", "confidence", "evidence_notes"]
MAX_LISTED = 15  # how many problem lines to print per check before summarizing


def load_manifest():
    if not os.path.exists(config.MANIFEST_PATH):
        return []
    with open(config.MANIFEST_PATH, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def is_blank(value):
    return value is None or str(value).strip() == ""


def print_list(title, items):
    print("\n" + title + ": " + (str(len(items)) if items else "none ✓"))
    for item in items[:MAX_LISTED]:
        print("   -", item)
    if len(items) > MAX_LISTED:
        print("   ... and {} more".format(len(items) - MAX_LISTED))


def main():
    fc = os.path.join(config.GDB_PATH, config.UTILITIES_FC_NAME)
    if not arcpy.Exists(fc):
        print("Utilities layer not found. Run step 01 first:", fc)
        sys.exit(1)

    print("PlanTrace check for project:", config.PROJECT_NAME)

    # --- 1. Manifest progress --------------------------------------------
    manifest = load_manifest()
    plan_ids = {r["plan_id"] for r in manifest}
    statuses = Counter((r.get("status") or "").strip().lower() or "(blank)" for r in manifest)
    print("\nPlans in manifest:", len(manifest))
    for status in ["staged", "georeferenced", "traced", "skip"]:
        print("   {:<14}{}".format(status, statuses.pop(status, 0)))
    for status, n in statuses.items():  # anything unexpected (typos etc.)
        print("   {:<14}{}   <- unexpected status".format(status, n))

    # --- 2. Read every line once -----------------------------------------
    missing = []
    unknown_plan = []
    lines_per_plan = Counter()
    total = 0
    fields = ["OID@"] + REQUIRED_FIELDS
    with arcpy.da.SearchCursor(fc, fields) as cursor:
        for row in cursor:
            total += 1
            oid, values = row[0], dict(zip(REQUIRED_FIELDS, row[1:]))
            blanks = [f for f in REQUIRED_FIELDS if is_blank(values[f])]
            if blanks:
                missing.append("OBJECTID {}: missing {}".format(oid, ", ".join(blanks)))
            plan = (values["source_plan"] or "").strip()
            if plan:
                lines_per_plan[plan] += 1
                if plan_ids and plan not in plan_ids:
                    unknown_plan.append("OBJECTID {}: source_plan '{}'".format(oid, plan))

    print("\nUtility lines:", total)
    print_list("Lines missing provenance", missing)
    print_list("Lines whose source_plan isn't in the manifest", unknown_plan)

    # --- 3. Manifest vs. lines -------------------------------------------
    traced_no_lines = [r["plan_id"] for r in manifest
                       if (r.get("status") or "").strip().lower() == "traced"
                       and lines_per_plan[r["plan_id"]] == 0]
    lines_not_traced = ["{} ({} lines, status '{}')".format(r["plan_id"], lines_per_plan[r["plan_id"]], r.get("status"))
                        for r in manifest
                        if lines_per_plan[r["plan_id"]] > 0
                        and (r.get("status") or "").strip().lower() != "traced"]
    print_list("Plans marked 'traced' but with no lines", traced_no_lines)
    print_list("Plans with lines but not marked 'traced'", lines_not_traced)

    print("\nDone.")


if __name__ == "__main__":
    main()
