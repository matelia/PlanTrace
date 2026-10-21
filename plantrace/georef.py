"""
Writing a placement into an image so ArcGIS Pro shows it exactly there.

Why this exists: a world file (.tfw) is only ONE place ArcGIS looks for an
image's position. If the TIFF itself also carries georeferencing inside the
file, ArcGIS uses that instead. arcpy's DefineProjection writes such internal
georeferencing, and for rotated sheets it did not match the world file (it
dropped the rotation), so sheets showed up in the wrong place in Pro even
though the world file was right.

So we write the SAME transform both ways: inside the TIFF (with GDAL, which
ships with ArcGIS Pro) and as the .tfw next to it. Then there is nothing for
ArcGIS to disagree about.

World file order:   A, D, B, E, C, F   (C, F = centre of the top-left pixel)
GDAL geotransform:  (x0, A, B, y0, D, E) with x0, y0 = top-left CORNER
"""

import arcpy
from osgeo import gdal

gdal.UseExceptions()


def geotransform_from_world_file(wf):
    a, d, b, e, c, f = wf
    return (c - a / 2.0 - b / 2.0, a, b, f - d / 2.0 - e / 2.0, d, e)


def world_file_from_geotransform(gt):
    x0, a, b, y0, d, e = gt
    return [a, d, b, e, x0 + a / 2.0 + b / 2.0, y0 + d / 2.0 + e / 2.0]


def write_georef(image_path, wf, wkid, nodata=None):
    """Put the transform (and coordinate system) inside the image file, and
    write the matching world file next to it. Returns the world file path."""
    wkt = arcpy.SpatialReference(wkid).exportToString()
    ds = gdal.Open(image_path, gdal.GA_Update)
    try:
        ds.SetGeoTransform(geotransform_from_world_file(wf))
        ds.SetProjection(wkt)
        if nodata is not None:
            for i in range(1, ds.RasterCount + 1):
                ds.GetRasterBand(i).SetNoDataValue(nodata)
        ds.FlushCache()
    finally:
        ds = None
    tfw = image_path.rsplit(".", 1)[0] + ".tfw"
    with open(tfw, "w", encoding="utf-8") as f:
        f.write("".join("{:.10f}\n".format(v) for v in wf))
    return tfw


def pdf_page_georef(pdf_path, page, width, height, wkid, dpi=300):
    """Real georeferencing stored in a PDF page (a "GeoPDF", e.g. exported
    from a utility's GIS), as world-file numbers for the page image of
    width x height pixels, in the project's coordinate system. None if the
    page has none. These are exact: no need to fit anything."""
    from osgeo import osr
    try:
        subs = gdal.Open(pdf_path).GetSubDatasets()
        name = subs[page - 1][0] if subs else pdf_path
        pds = gdal.OpenEx(name, open_options=["DPI={}".format(dpi)])
    except Exception:
        return None
    gt = pds.GetGeoTransform(can_return_null=True)
    wkt = pds.GetProjection()
    if not gt or not wkt:
        return None
    src = osr.SpatialReference(wkt=wkt)
    dst = osr.SpatialReference()
    dst.ImportFromWkt(arcpy.SpatialReference(wkid).exportToString())
    for s in (src, dst):
        s.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    ct = osr.CoordinateTransformation(src, dst)
    sx, sy = pds.RasterXSize / float(width), pds.RasterYSize / float(height)

    def corner(col, row):  # page image pixel corner -> project coordinates
        c, r = col * sx, row * sy
        x, y, _ = ct.TransformPoint(gt[0] + gt[1] * c + gt[2] * r, gt[3] + gt[4] * c + gt[5] * r)
        return x, y

    (x0, y0), (x1, y1), (x2, y2) = corner(0, 0), corner(width, 0), corner(0, height)
    out = (x0, (x1 - x0) / width, (x2 - x0) / height, y0, (y1 - y0) / width, (y2 - y0) / height)
    return world_file_from_geotransform(out)


def compare(wf_test, wf_true, width, height):
    """How far one placement is from another (e.g. automatic vs true):
    (centre error ft, worst corner error ft, rotation difference deg,
    scale ratio)."""
    import math

    def at(wf, col, row):
        a, d, b, e, c, f = wf
        return a * col + b * row + c, d * col + e * row + f

    pts = [(width / 2, height / 2), (0, 0), (width, 0), (width, height), (0, height)]
    errs = [math.dist(at(wf_test, *p), at(wf_true, *p)) for p in pts]
    rot = lambda wf: math.degrees(math.atan2(wf[1], wf[0]))
    drot = (rot(wf_test) - rot(wf_true) + 180) % 360 - 180
    scale = math.hypot(wf_test[0], wf_test[1]) / math.hypot(wf_true[0], wf_true[1])
    return errs[0], max(errs[1:]), drot, scale


def read_georef(image_path):
    """The transform GDAL (and ArcGIS) actually sees, as world-file numbers."""
    ds = gdal.Open(image_path)
    try:
        return world_file_from_geotransform(ds.GetGeoTransform())
    finally:
        ds = None
