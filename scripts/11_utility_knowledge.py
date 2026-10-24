r"""
Step 11: Utility knowledge table: what each owner's records are like.

Gathers what PlanTrace and you found on every sheet, per utility owner and
per decade (by the sheet's latest date), so the next project starts with it:
  - how many sheets, which years
  - what kind of file (scan, CAD plot, GIS export, GeoPDF with its own position)
  - datums WRITTEN on the sheets, and coordinate systems PROVEN by
    coordinates landing in the right place
  - scales used, drafting firms named
  - how placement went, and your review verdicts

It only counts what was seen. Nothing is guessed or filled in from outside:
every number lists the sheets it came from (the "evidence" column), so each
fact can be checked on the sheet itself.

The table is shared by all projects: re-running replaces this project's rows
and keeps the others.

  projects\_knowledge\utility_knowledge.csv   one row per project + owner + decade
  projects\_knowledge\utility_knowledge.md    readable summary, all projects

    python scripts\11_utility_knowledge.py
"""

import csv
import os
import re
import sys
import time
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from plantrace import records

KNOW_DIR = os.path.join(config.REPO_ROOT, "projects", "_knowledge")
KNOW_CSV = os.path.join(KNOW_DIR, "utility_knowledge.csv")
KNOW_MD = os.path.join(KNOW_DIR, "utility_knowledge.md")
FOOTPRINTS_FC = os.path.join(config.GDB_PATH, "PlanFootprints")
REVIEW_BACKUP = os.path.join(config.PROJECT_DIR, "review_backup.csv")
COLUMNS = ["project", "owner", "decade", "n_sheets", "years", "file_kinds",
           "datum_written", "coords_proven", "scales", "firms", "placement",
           "reviews", "evidence", "updated"]


def scale_text(raw):
    """Stated scale in one form: 1"=30', or '' if none could be read."""
    m = re.search(r"1\s*(?:\"|''|inch|in\.?)\s*=\s*(\d+)\s*(?:'|feet|ft)", raw or "", re.I)
    return '1"={}\''.format(m.group(1)) if m else ""


def counted(values):
    """Counter -> 'a (3); b (1)', most common first; '' if empty."""
    c = Counter(v for v in values if v)
    return "; ".join("{} ({})".format(v, n) for v, n in c.most_common())


def short(pid):
    for text in config.SHORT_NAME_REMOVE:
        pid = pid.replace(text, "")
    return pid


def project_rows():
    """This project's knowledge rows, from the manifest and your reviews."""
    with open(config.MANIFEST_PATH, newline="", encoding="utf-8-sig") as f:
        manifest = list(csv.DictReader(f))
    reviews = records.read_reviews(FOOTPRINTS_FC, REVIEW_BACKUP)
    groups = {}
    for r in manifest:
        if r.get("sheet_kind") == "correspondence":
            continue
        owner, _ = records.owner_of(r.get("source_file"), config.UTILITY_OWNERS)
        if owner == "811 design ticket":
            continue
        years = records.years_in(r.get("dates_found"))
        decade = "{}s".format(years[-1] // 10 * 10) if years else "no date"
        groups.setdefault((owner, decade), []).append((r, years))

    rows = []
    for (owner, decade), sheets in sorted(groups.items()):
        all_years = sorted({y for _, ys in sheets for y in ys})
        status = []
        for r, _ in sheets:
            s = r.get("place_status") or "not placed"
            status.append("GeoPDF (own position)" if s == "from PDF" else s)
        rows.append({
            "project": config.PROJECT_NAME,
            "owner": owner,
            "decade": decade,
            "n_sheets": len(sheets),
            "years": "{}-{}".format(all_years[0], all_years[-1]) if len(all_years) > 1
                     else (str(all_years[0]) if all_years else ""),
            "file_kinds": counted(r.get("file_kind") for r, _ in sheets),
            "datum_written": counted(r.get("datum_written") for r, _ in sheets),
            "coords_proven": counted(r.get("coords_system") for r, _ in sheets),
            "scales": counted(scale_text(r.get("scale_found")) for r, _ in sheets),
            "firms": counted((r.get("firm_found") or "").strip()[:60] for r, _ in sheets),
            "placement": counted(status),
            "reviews": counted(reviews.get(r["plan_id"], ("",))[0] for r, _ in sheets),
            "evidence": "; ".join(short(r["plan_id"]) for r, _ in sheets),
            "updated": time.strftime("%Y-%m-%d"),
        })
    return rows


def write_summary(rows):
    """utility_knowledge.md: per owner, across all projects."""
    by_owner = {}
    for r in rows:
        by_owner.setdefault(r["owner"], []).append(r)
    out = ["# Utility knowledge", "",
           "What PlanTrace has seen on each owner's record plans, counted from the sheets "
           "(updated {}). Only observations: every line says how many sheets it is based on, "
           "and utility_knowledge.csv lists which sheets.".format(time.strftime("%Y-%m-%d")), ""]
    for owner in sorted(by_owner):
        rs = by_owner[owner]
        n = sum(int(r["n_sheets"]) for r in rs)
        projects = sorted({r["project"] for r in rs})
        out.append("## {}".format(owner))
        out.append("{} sheet(s), {} project(s): {}".format(n, len(projects), ", ".join(projects)))
        out.append("")
        out.append("| Period | Sheets | Years | File kind | Datum written | Datum proven by coordinates | Scales | Placement | Your reviews |")
        out.append("|---|---|---|---|---|---|---|---|---|")
        for r in sorted(rs, key=lambda r: (r["decade"] == "no date", r["decade"], r["project"])):
            out.append("| {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
                r["decade"], r["n_sheets"], r["years"] or "-", r["file_kinds"] or "-",
                r["datum_written"] or "none", r["coords_proven"] or "-", r["scales"] or "-",
                r["placement"] or "-", r["reviews"] or "not reviewed"))
        out.append("")
    with open(KNOW_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")


def main():
    os.makedirs(KNOW_DIR, exist_ok=True)
    kept = []
    if os.path.exists(KNOW_CSV):
        with open(KNOW_CSV, newline="", encoding="utf-8") as f:
            kept = [r for r in csv.DictReader(f) if r["project"] != config.PROJECT_NAME]
    mine = project_rows()
    rows = kept + mine
    tmp = KNOW_CSV + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, KNOW_CSV)
    write_summary(rows)
    print("{}: {} row(s) (owner x decade) from {} sheet(s). Other projects kept: {} row(s).".format(
        config.PROJECT_NAME, len(mine), sum(r["n_sheets"] for r in mine), len(kept)))
    print("Wrote\n   {}\n   {}".format(KNOW_CSV, KNOW_MD))


if __name__ == "__main__":
    main()
