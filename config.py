"""
PlanTrace settings.

This is the ONLY file you should normally need to edit.
Change the values below, save, and re-run the scripts.
"""

import os

# ---------------------------------------------------------------------------
# 1. Which project are you working on?
#    Use a short name with no spaces (letters, numbers, underscores).
#    Each project gets its own folder under "projects\".
# ---------------------------------------------------------------------------
PROJECT_NAME = "MY_PROJECT"

# ---------------------------------------------------------------------------
# 2. Coordinate system for your data (an EPSG / WKID number).
#    2262 = NAD83 / New York West (US feet). Use the State Plane zone (or
#    other system) your office uses for the project area.
#    (Changing it later means re-creating the geodatabase.)
# ---------------------------------------------------------------------------
SPATIAL_REFERENCE_WKID = 2262

# ---------------------------------------------------------------------------
# 3. Plan intake settings.
#    PDF_DPI: resolution when converting PDF pages to images.
#    300 is a good balance for reading small text vs. file size.
# ---------------------------------------------------------------------------
PDF_DPI = 300
MAX_PDF_PAGES = 200  # safety limit so a broken PDF can't loop forever

# File types the intake script will pick up from raw_plans\
IMAGE_EXTENSIONS = [".tif", ".tiff", ".jpg", ".jpeg", ".png"]
PDF_EXTENSIONS = [".pdf"]

# ---------------------------------------------------------------------------
# 4. Automatic placement settings
#
#    SEARCH_AREA: the box the tool searches for plan locations, in plain
#    longitude/latitude: (west, south, east, north), e.g.
#        SEARCH_AREA = (-76.20, 43.00, -76.10, 43.06)
#    Make it cover the project plus some margin.
#    Tip: in ArcGIS Pro, hover the map and read the coordinates at the bottom
#    of the map (switch the display to decimal degrees) to pick values.
# ---------------------------------------------------------------------------
SEARCH_AREA = None

# Where Tesseract (the free text-reading program) is installed.
TESSERACT_EXE = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

# Local vision AI model (Ollama). Set USE_VISION = False to use Tesseract only.
USE_VISION = True
OLLAMA_MODEL = "qwen3.5:4b"
OLLAMA_URL = "http://localhost:11434"

# Route whose design stationing (e.g. 4+840) appears on the plans, as its
# route number in the street data (e.g. "5"). Step 06 learns where those
# stations are along this route from sheets it has placed.
# None = don't use stationing.
STATIONING_ROUTE = None

# Public NYS street centerline service (free, no account needed). Outside
# New York, point this at a similar public street centerline layer (an
# ArcGIS REST layer with CompleteStreetName, HighwayNumber and
# LeftCityTownName fields), or adapt scripts\04_get_streets.py.
NYS_STREETS_URL = "https://gisservices.its.ny.gov/arcgis/rest/services/NYS_Streets/MapServer/4"

# Aerial photos used to check and fine-tune placements (public NYS orthoimagery,
# an ArcGIS MapServer that supports "export").
ORTHO_SERVICE_URL = "https://orthos.its.ny.gov/arcgis/rest/services/wms/Latest/MapServer"

# ---------------------------------------------------------------------------
# 5. Words that depend on where the project is
# ---------------------------------------------------------------------------
# Where the plans are, told to the vision model ("a utility plan in ...").
VISION_AREA = "New York State"

# Highways the plans name in ways that don't look like street names.
# key = name used in the results, value = text patterns (regular expressions)
# that mean it on a plan. The key's last word is the route number, which is
# looked up in the street data's route field. Example:
#     HIGHWAY_ALIASES = {
#         "Interstate 90": [r"\bI[\s\-]?90\b", r"\bTHRUWAY\b"],
#         "Route 5": [r"\b(?:RTE|ROUTE|RT|NY|S\.?R\.?)\.?\s*5\b"],
#     }
HIGHWAY_ALIASES = {}

# Single words that mark those highways' labels on a sheet (used to check
# which way round a sheet goes). Example:
#     HIGHWAY_LABEL_WORDS = {"Interstate 90": ["THRUWAY", "90"]}
HIGHWAY_LABEL_WORDS = {}

# Place names (besides the towns in the street data) that appear on plans
# and must never be read as a street name by themselves (e.g. the county
# name in title blocks). Example: ["ADAMS", "SPRINGFIELD"]
EXTRA_PLACE_WORDS = []

# Text removed from sheet names to make them shorter in the review map.
# Example: ["PROJECT_123_", "_Utility_Records"]
SHORT_NAME_REMOVE = []

# ---------------------------------------------------------------------------
# Everything below is calculated from the settings above. No need to edit.
# ---------------------------------------------------------------------------
REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.join(REPO_ROOT, "projects", PROJECT_NAME)

RAW_PLANS_DIR = os.path.join(PROJECT_DIR, "raw_plans")        # you drop scans here
STAGED_PLANS_DIR = os.path.join(PROJECT_DIR, "staged_plans")  # script writes images here
GDB_PATH = os.path.join(PROJECT_DIR, PROJECT_NAME + ".gdb")
MANIFEST_PATH = os.path.join(PROJECT_DIR, "plan_manifest.csv")

PLAN_TEXT_DIR = os.path.join(PROJECT_DIR, "plan_text")      # step 05 writes what it read here

UTILITIES_FC_NAME = "Utilities"
STREETS_FC_NAME = "RefStreets"          # street centerlines inside the search area
SEARCH_AREA_FC_NAME = "SearchArea"
