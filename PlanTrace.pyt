r"""
PlanTrace toolbox for ArcGIS Pro.

Runs the PlanTrace steps as normal ArcGIS tools (a window with fields and a
Run button, messages in Pro's message pane), so nothing needs the command
prompt. Each tool calls the same step script you'd run by hand
(scripts\0X_....py); nothing about how sheets are located or placed changes.

How to add it (once): Catalog pane -> right-click Toolboxes -> Add Toolbox
-> pick PlanTrace.pyt in the PlanTrace folder.

The project the tools work on is the one set in config.py (PROJECT_NAME).
"""

import contextlib
import importlib.util
import os
import sys

import arcpy

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "scripts")


# ---------------------------------------------------------------------------
# Running a step script inside ArcGIS Pro
# ---------------------------------------------------------------------------

class _ToMessages:
    """Sends what a step script prints to ArcGIS's message pane, line by line."""

    def __init__(self):
        self.buffer = ""

    def write(self, text):
        self.buffer += text
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            arcpy.AddMessage(line)

    def flush(self):
        if self.buffer:
            arcpy.AddMessage(self.buffer)
            self.buffer = ""


def _fresh_import(script_name):
    """Load a step script (and fresh copies of config.py and plantrace), so
    edits to the code take effect without restarting ArcGIS Pro."""
    for path in (HERE, SCRIPTS):
        if path not in sys.path:
            sys.path.insert(0, path)
    for name in list(sys.modules):
        if name == "config" or name == "plantrace" or name.startswith("plantrace.") \
                or name[:2].isdigit():
            del sys.modules[name]
    path = os.path.join(SCRIPTS, script_name)
    spec = importlib.util.spec_from_file_location(script_name[:-3], path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[script_name[:-3]] = module
    spec.loader.exec_module(module)
    return module, path


def _open_project():
    """The project open in ArcGIS Pro, or None when not run from inside Pro."""
    try:
        return arcpy.mp.ArcGISProject("CURRENT")
    except Exception:
        return None


def _let_go_of_review_map():
    """Inside Pro, the Placement Review map holds on to the data these steps
    rewrite. Remove it first; 'Build review map' makes a fresh one."""
    aprx = _open_project()
    if aprx is None:
        return
    review, _ = _fresh_import("08_make_review_map.py")
    if aprx.listMaps(review.MAP_NAME):
        review.remove_review_map(aprx)
        arcpy.AddMessage("Removed the '{}' map while this runs (it shows the files being "
                         "rewritten). Run 'Build review map' afterwards.".format(review.MAP_NAME))


def run_step(script_name, args=(), pass_project=False):
    """Run a step script's main() with the given command-line options."""
    module, path = _fresh_import(script_name)
    old_argv, out = sys.argv, _ToMessages()
    sys.argv = [path] + list(args)
    try:
        with contextlib.redirect_stdout(out):
            if pass_project:
                module.main(_open_project())
            else:
                module.main()
    except SystemExit as stop:
        out.flush()
        if stop.code not in (None, 0):
            arcpy.AddError("Stopped (see the messages above).")
            raise arcpy.ExecuteError("PlanTrace step stopped") from None
    finally:
        out.flush()
        sys.argv = old_argv


# ---------------------------------------------------------------------------
# The toolbox and its tools
# ---------------------------------------------------------------------------

class Toolbox(object):
    def __init__(self):
        self.label = "PlanTrace"
        self.alias = "plantrace"
        self.tools = [LocateSheets, PlaceSheets, BuildReviewMap]


class LocateSheets(object):
    def __init__(self):
        self.label = "1. Locate sheets"
        self.description = ("Suggests where each plan sheet goes, from the street names read off "
                            "it (crossings), utility grid numbers, route stationing and neighbouring "
                            "pages. Writes the PlanLocations layer. About a minute. (Step 06)")
        self.canRunInBackground = False

    def getParameterInfo(self):
        return []

    def execute(self, parameters, messages):
        _let_go_of_review_map()
        run_step("06_locate_plans.py")


MODES = {
    "Place all located sheets (about an hour)": [],
    "Continue a stopped run": ["--resume"],
    "Fine-tune existing placements against the aerial (quick)": ["--refine-only"],
    "Redraw the check pictures only": ["--pictures-only"],
}


class PlaceSheets(object):
    def __init__(self):
        self.label = "2. Place sheets"
        self.description = ("Scales, rotates and positions each located sheet onto the streets, "
                            "then fine-tunes it against the aerial photo, and writes its position "
                            "into the image so it opens in place. Uses a PDF's own position when it "
                            "has one. Never overwrites georeferencing you did yourself. (Step 07)")
        self.canRunInBackground = False

    def getParameterInfo(self):
        mode = arcpy.Parameter(displayName="What to do", name="mode", datatype="GPString",
                               parameterType="Required", direction="Input")
        mode.filter.type = "ValueList"
        mode.filter.list = list(MODES)
        mode.value = list(MODES)[2]
        only = arcpy.Parameter(displayName="Only sheets whose name contains (optional)",
                               name="only", datatype="GPString", parameterType="Optional",
                               direction="Input")
        pictures = arcpy.Parameter(displayName="Make check pictures (aerial + plan lines)",
                                   name="pictures", datatype="GPBoolean", parameterType="Optional",
                                   direction="Input")
        pictures.value = True
        return [mode, only, pictures]

    def execute(self, parameters, messages):
        mode, only, pictures = (p.valueAsText for p in parameters)
        args = list(MODES[mode])
        if only:
            args += ["--only", only.strip()]
        if pictures == "false":
            args.append("--no-pictures")
        _let_go_of_review_map()
        run_step("07_place_plans.py", args)


class BuildReviewMap(object):
    def __init__(self):
        self.label = "3. Build review map"
        self.description = ("Makes (or remakes) the 'Placement Review' map in this project: aerial, "
                            "streets, sheet outlines coloured by status, and every placed sheet's "
                            "linework in see-through magenta. (Step 08)")
        self.canRunInBackground = False

    def getParameterInfo(self):
        return []

    def execute(self, parameters, messages):
        run_step("08_make_review_map.py", pass_project=True)
