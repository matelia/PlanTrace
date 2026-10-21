"""
Step 0: Check that this computer is ready to run PlanTrace.

It confirms:
  - you are running ArcGIS Pro's Python (not some other Python)
  - ArcPy loads and a license is available
  - the coordinate system in config.py is valid

It does NOT change anything. Safe to run any time.
"""

import os
import sys

# Let this script find config.py, which sits one folder up.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

print("Python being used:")
print("   ", sys.executable)
print("   version", sys.version.split()[0])

if "arcgispro-py3" not in sys.executable.lower():
    print("\nWARNING: this does not look like ArcGIS Pro's Python environment.")
    print("Open 'Python Command Prompt' from the ArcGIS Pro Start menu folder and try again.")

try:
    import arcpy
except ImportError:
    print("\nFAILED: ArcPy could not be loaded. Use ArcGIS Pro's Python Command Prompt.")
    sys.exit(1)

info = arcpy.GetInstallInfo()
print("\nArcGIS Pro version:", info.get("Version"))
print("License level:      ", arcpy.ProductInfo())

try:
    sr = arcpy.SpatialReference(config.SPATIAL_REFERENCE_WKID)
    print("Coordinate system:  ", sr.name, "(WKID", str(config.SPATIAL_REFERENCE_WKID) + ")")
except Exception as e:
    print("\nFAILED: SPATIAL_REFERENCE_WKID in config.py is not valid:", e)
    sys.exit(1)

# --- Automatic placement (V1.5) needs -------------------------------------
print("\nAutomatic placement add-ons:")
for module in ("numpy", "scipy", "PIL"):
    try:
        __import__(module)
        print("   {:<10} OK".format(module))
    except ImportError:
        print("   {:<10} MISSING (tell Claude; this changes the plan)".format(module))
if os.path.exists(config.TESSERACT_EXE):
    print("   tesseract  OK  ", config.TESSERACT_EXE)
else:
    print("   tesseract  NOT INSTALLED (needed for step 05; see README)")
sys.path.insert(0, config.REPO_ROOT)
from plantrace import vision
if not config.USE_VISION:
    print("   ollama     turned off in config.py (USE_VISION = False)")
elif not vision.is_running(config.OLLAMA_URL):
    print("   ollama     NOT RUNNING (install it, or start it from the Start menu; see README)")
elif not vision.has_model(config.OLLAMA_MODEL, config.OLLAMA_URL):
    print("   ollama     running, but model missing. Run:  ollama pull " + config.OLLAMA_MODEL)
else:
    print("   ollama     OK   model " + config.OLLAMA_MODEL)
free_gb = __import__("shutil").disk_usage(config.REPO_ROOT).free / 1e9
print("   free disk   {:.1f} GB{}".format(free_gb, "   <- low, plan scans need space" if free_gb < 3 else ""))

print("\nProject name:       ", config.PROJECT_NAME)
print("Project folder:     ", config.PROJECT_DIR)
print("\nAll checks passed. You're ready for step 01.")
