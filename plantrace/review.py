"""
Making automatic placements easy to check by eye.

  ink_overlay()     a see-through copy of a placed plan sheet: only the ink,
                    in magenta, with no white background, positioned by the
                    same world file. In ArcGIS Pro it sits on top of the
                    aerial so you can see whether the plan's curbs and edges
                    land on the real ones.
  review_picture()  a PNG of the real aerial photo (NYS orthoimagery) with the
                    plan's ink drawn on top, for flipping through without
                    ArcGIS.

Pure numpy/Pillow + urllib (no extra packages).
"""

import io
import math
import urllib.request

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import georef, place

Image.MAX_IMAGE_PIXELS = None

INK_RGB = (255, 0, 255)       # magenta: stands out on grass, trees and pavement
STREET_RGB = (255, 230, 0)    # street centerlines (yellow), like the review map
# The aerial photo service (public NYS orthoimagery). Step scripts set it
# from config.ORTHO_SERVICE_URL.
ORTHO_EXPORT = "https://orthos.its.ny.gov/arcgis/rest/services/wms/Latest/MapServer/export"


def _ink_small(image_path, max_width):
    """Ink of the page, shrunk by a whole factor so lines stay visible.
    Returns (ink array, factor)."""
    ink = place._ink_full(image_path)
    k = max(1, int(math.ceil(ink.shape[1] / max_width)))
    return place._shrink(ink, k), k


def _scaled_world_file(wf, k):
    """World file for a copy of the image shrunk k times."""
    a, d, b, e, c, f = wf
    half = (k - 1) / 2.0  # centre of shrunk pixel (0,0) in full-size pixels
    return [a * k, d * k, b * k, e * k, c + a * half + b * half, f + d * half + e * half]


def ink_overlay(image_path, wf, out_tif, wkid, max_width=4000):
    """Write a paletted TIFF (+ .tfw) of the plan's ink: value 1 = ink
    (magenta), value 0 = background (NoData, so it's see-through)."""
    ink, k = _ink_small(image_path, max_width)
    im = Image.fromarray(ink.astype(np.uint8), mode="L").convert("P")
    palette = [255, 255, 255] + list(INK_RGB) + [0, 0, 0] * 254
    im.putpalette(palette)
    im.save(out_tif, compression="tiff_lzw")
    # position inside the file AND as a world file, identical; 0 = see-through
    georef.write_georef(out_tif, _scaled_world_file(wf, k), wkid, nodata=0)
    return out_tif


def fetch_ortho(xmin, ymin, xmax, ymax, wkid, width):
    """Aerial photo for a box (map feet), from the NYS ortho service."""
    height = max(1, int(round(width * (ymax - ymin) / (xmax - xmin))))
    url = ("{}?bbox={},{},{},{}&bboxSR={w}&imageSR={w}&size={},{}&format=jpg&f=image".format(
        ORTHO_EXPORT, xmin, ymin, xmax, ymax, width, height, w=wkid))
    with urllib.request.urlopen(url, timeout=120) as resp:
        return Image.open(io.BytesIO(resp.read())).convert("RGB")


def review_picture(image_path, wf, streets, out_png, title, wkid, width=1600):
    """Aerial + plan ink (magenta) + street centerlines (thin yellow), with
    the sheet outline. streets: place.StreetSegments."""
    with Image.open(image_path) as im:
        w, h = im.size
    a, d, b, e, c, f = wf
    corners = [(a * col + b * row + c, d * col + e * row + f)
               for col, row in [(-0.5, -0.5), (w - 0.5, -0.5), (w - 0.5, h - 0.5), (-0.5, h - 0.5)]]
    xs, ys = [p[0] for p in corners], [p[1] for p in corners]
    pad = 0.08 * max(max(xs) - min(xs), max(ys) - min(ys))
    x0, x1, y0, y1 = min(xs) - pad, max(xs) + pad, min(ys) - pad, max(ys) + pad
    try:
        photo = fetch_ortho(x0, y0, x1, y1, wkid, width)
        # dim the photo a little so the magenta lines stand out
        photo = Image.blend(photo, Image.new("RGB", photo.size, (40, 40, 40)), 0.25)
    except Exception as err:  # offline or service down: plain grey background
        print("     (no aerial photo: {})".format(err))
        photo = Image.new("RGB", (width, int(width * (y1 - y0) / (x1 - x0))), (90, 90, 90))
    pw, ph = photo.size
    sx, sy = pw / (x1 - x0), ph / (y1 - y0)

    def to_px(x, y):
        return (x - x0) * sx, (y1 - y) * sy

    draw = ImageDraw.Draw(photo)
    s = streets.segs
    inside = (s[:, [0, 2]].max(1) > x0) & (s[:, [0, 2]].min(1) < x1) & \
             (s[:, [1, 3]].max(1) > y0) & (s[:, [1, 3]].min(1) < y1)
    for xa, ya, xb, yb in s[inside]:
        draw.line([to_px(xa, ya), to_px(xb, yb)], fill=STREET_RGB, width=1)
    draw.line([to_px(*p) for p in corners + corners[:1]], fill=(0, 200, 255), width=2)

    # plan ink, at about the picture's resolution
    ft_per_full_px = math.hypot(a, d)
    ink, k = _ink_small(image_path, max_width=max(500, int(w * ft_per_full_px * sx)))
    rows, cols = np.nonzero(ink)
    fc = cols * k + (k - 1) / 2.0
    fr = rows * k + (k - 1) / 2.0
    px = ((a * fc + b * fr + c) - x0) * sx
    py = (y1 - (d * fc + e * fr + f)) * sy
    ok = (px >= 0) & (px < pw) & (py >= 0) & (py < ph)
    arr = np.asarray(photo).copy()
    arr[py[ok].astype(int), px[ok].astype(int)] = INK_RGB
    out = Image.fromarray(arr)

    bar = Image.new("RGB", (pw, 34), (255, 255, 255))
    try:
        font = ImageFont.load_default(size=18)
    except TypeError:  # older Pillow
        font = ImageFont.load_default()
    ImageDraw.Draw(bar).text((8, 7), title, fill=(0, 0, 0), font=font)
    final = Image.new("RGB", (pw, ph + 34), (255, 255, 255))
    final.paste(bar, (0, 0))
    final.paste(out, (0, 34))
    final.save(out_png)
    return out_png
