r"""
Step 1: Create the project folders, geodatabase, and Utilities layer.

Creates:
  projects\<PROJECT_NAME>\raw_plans\        (put your scanned plans here)
  projects\<PROJECT_NAME>\staged_plans\     (step 02 fills this)
  projects\<PROJECT_NAME>\<PROJECT_NAME>.gdb
      - domain "UtilityType"  (gas, water, sewer, ...)
      - domain "Confidence"   (high, medium, low)
      - feature class "Utilities" (polyline) with all provenance fields

SAFE TO RE-RUN: it never deletes or overwrites anything. If the geodatabase
or the Utilities layer already exists, it leaves them alone and only adds
anything that is missing (e.g. a field you added to this script later).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
import arcpy

# ---------------------------------------------------------------------------
# Data model. Edit here if the fields ever change.
# ---------------------------------------------------------------------------

# Coded-value domains: {domain name: (description, [(code, description), ...])}
DOMAINS = {
    "UtilityType": ("Type of utility", [
        ("gas", "Gas"),
        ("water", "Water"),
        ("sewer", "Sanitary sewer"),
        ("storm", "Storm"),
        ("electric", "Electric"),
        ("telecom", "Telecom"),
        ("unknown", "Unknown"),
    ]),
    "Confidence": ("Placement confidence", [
        ("high", "High"),
        ("medium", "Medium"),
        ("low", "Low"),
    ]),
}

# Fields: (name, type, length, alias, domain)
FIELDS = [
    ("utility_type",     "TEXT", 20,   "Utility Type",     "UtilityType"),
    ("source_plan",      "TEXT", 255,  "Source Plan",      None),
    ("plan_date",        "DATE", None, "Plan Date",        None),
    ("plan_author_firm", "TEXT", 150,  "Plan Author Firm", None),
    ("confidence",       "TEXT", 10,   "Confidence",       "Confidence"),
    ("evidence_notes",   "TEXT", 1000, "Evidence Notes",   None),
    ("traced_by",        "TEXT", 100,  "Traced By",        None),
    ("traced_date",      "DATE", None, "Traced Date",      None),
    # Filled automatically by editor tracking whenever a line is changed
    # (e.g. you nudge it after a better georeference). Useful audit trail.
    ("last_edited_by",   "TEXT", 100,  "Last Edited By",   None),
    ("last_edited_date", "DATE", None, "Last Edited Date", None),
]


def make_folders():
    for folder in (config.PROJECT_DIR, config.RAW_PLANS_DIR, config.STAGED_PLANS_DIR):
        if not os.path.isdir(folder):
            os.makedirs(folder)
            print("Created folder:", folder)
        else:
            print("Folder exists: ", folder)


def make_geodatabase():
    if arcpy.Exists(config.GDB_PATH):
        print("Geodatabase exists:", config.GDB_PATH)
        return
    arcpy.management.CreateFileGDB(config.PROJECT_DIR, config.PROJECT_NAME + ".gdb")
    print("Created geodatabase:", config.GDB_PATH)


def make_domains():
    existing = {d.name for d in arcpy.da.ListDomains(config.GDB_PATH)}
    for name, (description, codes) in DOMAINS.items():
        if name not in existing:
            arcpy.management.CreateDomain(config.GDB_PATH, name, description, "TEXT", "CODED")
            print("Created domain:", name)
        # Add any codes that are missing (adding the same code twice would error).
        current_codes = {}
        for d in arcpy.da.ListDomains(config.GDB_PATH):
            if d.name == name:
                current_codes = d.codedValues or {}
        for code, code_desc in codes:
            if code not in current_codes:
                arcpy.management.AddCodedValueToDomain(config.GDB_PATH, name, code, code_desc)
                print("   added code:", name, "->", code)


def make_feature_class():
    fc = os.path.join(config.GDB_PATH, config.UTILITIES_FC_NAME)
    if not arcpy.Exists(fc):
        sr = arcpy.SpatialReference(config.SPATIAL_REFERENCE_WKID)
        arcpy.management.CreateFeatureclass(
            config.GDB_PATH, config.UTILITIES_FC_NAME, "POLYLINE", spatial_reference=sr
        )
        print("Created feature class:", fc, "in", sr.name)
    else:
        print("Feature class exists:", fc)

    existing_fields = {f.name.lower() for f in arcpy.ListFields(fc)}
    for name, ftype, length, alias, domain in FIELDS:
        if name.lower() in existing_fields:
            continue
        arcpy.management.AddField(
            fc, name, ftype,
            field_length=length,
            field_alias=alias,
            field_domain=domain or "",
        )
        print("   added field:", name)
    return fc


def enable_editor_tracking(fc):
    """Have ArcGIS fill in who/when automatically:
       - traced_by / traced_date        when a line is first drawn
       - last_edited_by / last_edited_date  every time it is changed
    The "who" is your Windows user name."""
    if arcpy.Describe(fc).editorTrackingEnabled:
        print("Editor tracking already on.")
        return
    arcpy.management.EnableEditorTracking(
        fc,
        creator_field="traced_by",
        creation_date_field="traced_date",
        last_editor_field="last_edited_by",
        last_edit_date_field="last_edited_date",
        add_fields="NO_ADD_FIELDS",        # the fields already exist (made above)
        record_dates_in="DATABASE_TIME",   # local time, so dates read naturally
    )
    print("Turned on editor tracking (traced_by/traced_date fill in automatically).")


if __name__ == "__main__":
    print("PlanTrace setup for project:", config.PROJECT_NAME, "\n")
    make_folders()
    make_geodatabase()
    make_domains()
    fc = make_feature_class()
    enable_editor_tracking(fc)
    print("\nDone. Add this layer to your ArcGIS Pro map:")
    print("   ", fc)
    print("Next: copy your scanned plans into", config.RAW_PLANS_DIR)
    print("      then run step 02.")
