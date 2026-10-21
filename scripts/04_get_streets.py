r"""
Step 4: Download real street centerlines for the search area.

Gets every street segment inside SEARCH_AREA (config.py) from the free NYS
Streets service and saves it in the project geodatabase:
    RefStreets  - street centerlines with name, route number and town
    SearchArea  - the search box, so you can see it on the map

Later steps use these to (a) recognize street names written on plans and
(b) find where those streets are.

Needs an internet connection. Takes under a minute.
SAFE TO RE-RUN: it replaces RefStreets / SearchArea with a fresh download
(they are reference data, never edited by hand).
"""

import json
import os
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
import arcpy

OUT_FIELDS = ["CompleteStreetName", "HighwayNumber", "LeftCityTownName"]
CHUNK = 400  # segments per request (the service allows up to 1000)


def query(params):
    """Send one query to the street service (POST, so long ID lists fit)."""
    data = urllib.parse.urlencode(dict(params, f="json")).encode()
    req = urllib.request.Request(config.NYS_STREETS_URL + "/query", data=data)
    with urllib.request.urlopen(req, timeout=120) as resp:
        result = json.load(resp)
    if "error" in result:
        raise RuntimeError("Street service error: {}".format(result["error"]))
    return result


def main():
    if not config.SEARCH_AREA:
        print("Set SEARCH_AREA in config.py first (the box around your project, in")
        print("longitude/latitude: west, south, east, north).")
        sys.exit(1)
    west, south, east, north = config.SEARCH_AREA
    box = {"geometry": "{},{},{},{}".format(west, south, east, north),
           "geometryType": "esriGeometryEnvelope", "inSR": 4326,
           "spatialRel": "esriSpatialRelIntersects"}

    print("Asking the NYS Streets service which segments are in the search area...")
    ids = query(dict(box, where="1=1", returnIdsOnly="true")).get("objectIds") or []
    print("   {} street segments found.".format(len(ids)))
    if not ids:
        print("Nothing found. Check SEARCH_AREA in config.py.")
        sys.exit(1)

    sr = arcpy.SpatialReference(config.SPATIAL_REFERENCE_WKID)
    streets_fc = os.path.join(config.GDB_PATH, config.STREETS_FC_NAME)
    if arcpy.Exists(streets_fc):
        arcpy.management.Delete(streets_fc)
    arcpy.management.CreateFeatureclass(config.GDB_PATH, config.STREETS_FC_NAME,
                                        "POLYLINE", spatial_reference=sr)
    arcpy.management.AddField(streets_fc, "street_name", "TEXT", field_length=120)
    arcpy.management.AddField(streets_fc, "route", "TEXT", field_length=20)
    arcpy.management.AddField(streets_fc, "town", "TEXT", field_length=60)

    written = 0
    with arcpy.da.InsertCursor(streets_fc, ["SHAPE@", "street_name", "route", "town"]) as cur:
        for i in range(0, len(ids), CHUNK):
            chunk = ids[i:i + CHUNK]
            result = query({"objectIds": ",".join(str(x) for x in chunk),
                            "outFields": ",".join(OUT_FIELDS),
                            "outSR": config.SPATIAL_REFERENCE_WKID,
                            "returnGeometry": "true"})
            for feat in result.get("features", []):
                geom = feat.get("geometry")
                if not geom or not geom.get("paths"):
                    continue
                geom["spatialReference"] = {"wkid": config.SPATIAL_REFERENCE_WKID}
                attrs = feat.get("attributes", {})
                cur.insertRow([arcpy.AsShape(geom, True),
                               str(attrs.get("CompleteStreetName") or ""),
                               str(attrs.get("HighwayNumber") or ""),
                               str(attrs.get("LeftCityTownName") or "")])
                written += 1
            print("   downloaded {} / {}".format(min(i + CHUNK, len(ids)), len(ids)))
    print("Saved {} segments to {}".format(written, streets_fc))

    # The search box itself, as a polygon you can show on the map.
    area_fc = os.path.join(config.GDB_PATH, config.SEARCH_AREA_FC_NAME)
    if arcpy.Exists(area_fc):
        arcpy.management.Delete(area_fc)
    arcpy.management.CreateFeatureclass(config.GDB_PATH, config.SEARCH_AREA_FC_NAME,
                                        "POLYGON", spatial_reference=sr)
    wgs84 = arcpy.SpatialReference(4326)
    corners = arcpy.Array([arcpy.Point(west, south), arcpy.Point(west, north),
                           arcpy.Point(east, north), arcpy.Point(east, south)])
    box_poly = arcpy.Polygon(corners, wgs84).projectAs(sr)
    with arcpy.da.InsertCursor(area_fc, ["SHAPE@"]) as cur:
        cur.insertRow([box_poly])
    print("Saved search box to", area_fc)

    names = sorted({r[0] for r in arcpy.da.SearchCursor(streets_fc, ["street_name"]) if r[0]})
    print("\n{} distinct street names, e.g.: {}".format(len(names), ", ".join(names[:8])))
    print("\nDone. Next: run step 05 to read the plans.")


if __name__ == "__main__":
    main()
