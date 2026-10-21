"""
Fitting a plan sheet onto the street centerlines (step 3 of automatic
placement): how big it is (scale), which way it's turned (rotation), and
exactly where it sits (shift).

The idea in plain words:
  1. Find the long, thin lines on the scan: road edges, curbs, right-of-way
     lines, centerlines. Text, the sheet border and small marks are dropped.
     Each line pixel also remembers which way its line runs.
  2. Around the sheet's suggested location (step 06), draw the real street
     centerlines and mark everything within ~40-80 ft of them as "near a
     street". Plans show road EDGES and the street data is CENTERLINES, so
     we can't ask for an exact hit.
  3. Turn and scale the plan's lines every possible way, slide them over the
     streets, and count how many plan line points land near a street that
     runs the SAME direction. The best count wins.
     Sliding is done for all positions at once with a Fourier transform
     (numpy.fft), which is what makes trying every rotation affordable.
  4. Repeat on a finer grid around the best few answers.

Pure numpy/scipy (no ArcPy), so it can be tested on its own.

Main functions:
  plan_lines(image_path, ...)    -> PlanLines (points + directions)
  StreetSegments(array)          -> the centerlines as straight pieces
  fit_sheet(lines, streets, ...) -> Fit (scale, rotation, position, score)
"""

import math

import numpy as np
from PIL import Image
from scipy import ndimage as ndi

from . import read

Image.MAX_IMAGE_PIXELS = None

# Work on a copy of the page about this many pixels wide.
WORK_WIDTH = 2000
# Lines shorter than this (in inches on the paper) are treated as clutter
# (text, symbols, dashes).
MIN_LINE_IN = 0.8
# At most this many line points are used (a random, repeatable sample).
MAX_POINTS = 25000
# Line directions are grouped into bins this many degrees wide.
DIR_BIN_DEG = 15
# A plan line point "counts" if it's within NEAR_FT of a street centerline
# running the same way (full credit), fading to nothing at FAR_FT.
NEAR_FT = 40.0
FAR_FT = 80.0


# ---------------------------------------------------------------------------
# 1. Lines on the plan
# ---------------------------------------------------------------------------

class PlanLines:
    """Line points found on a page.

    u, v      position of each point in FULL-RESOLUTION pixels, measured from
              the page centre, v pointing UP (like map coordinates)
    angle     direction of the line through each point, degrees 0-180,
              counter-clockwise from the page's x axis
    width_px, height_px   size of the full-resolution image
    dpi       resolution of the full image (pixels per inch of paper)
    """

    def __init__(self, u, v, angle, width_px, height_px, dpi):
        self.u, self.v, self.angle = u, v, angle
        self.width_px, self.height_px, self.dpi = width_px, height_px, dpi

    def __len__(self):
        return len(self.u)


# A pixel is ink if it is this much darker than the paper around it.
INK_CONTRAST = 70


def _ink_full(image_path):
    """Dark pixels of the page at full resolution (True = ink).

    "Dark" is measured against the paper AROUND each pixel, not a fixed
    level: photocopies often have grey paper (sometimes darker in places),
    and with a fixed level the whole page counted as ink."""
    im = Image.open(image_path)
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        white = Image.new("RGBA", im.size, (255, 255, 255, 255))
        im = Image.alpha_composite(white, im)
    gray = np.asarray(im.convert("L"))
    # Paper brightness: the brightest value in each ~0.25 in neighbourhood,
    # worked out on an 8x smaller copy (fast), then blown back up.
    k = 8
    h, w = gray.shape
    hh, ww = -(-h // k) * k, -(-w // k) * k
    padded = np.pad(gray, ((0, hh - h), (0, ww - w)), mode="edge")
    small = padded.reshape(hh // k, k, ww // k, k).max(axis=(1, 3))
    paper = ndi.maximum_filter(small, size=9)
    paper = ndi.uniform_filter(paper.astype(np.float32), size=9)  # smooth it
    paper = np.repeat(np.repeat(paper, k, axis=0), k, axis=1)[:h, :w]
    return gray.astype(np.int16) < (paper - INK_CONTRAST)


def _shrink(ink, k):
    """Shrink by a whole factor k, keeping any ink in each k x k block
    (so thin lines don't fade away)."""
    h, w = (ink.shape[0] // k) * k, (ink.shape[1] // k) * k
    return ink[:h, :w].reshape(h // k, k, w // k, k).any(axis=(1, 3))


def _long_runs(a, axis, min_len):
    """Pixels that are part of a straight run of ink at least min_len long
    along the given axis (0 = vertical, 1 = horizontal)."""
    frac = ndi.uniform_filter1d(a.astype(np.float32), min_len, axis=axis)
    centres = frac >= 0.9
    return ndi.maximum_filter1d(centres, min_len, axis=axis) & a


def _frame_mask(ink, skew):
    """The sheet border (and anything outside it), so it can be erased.

    The border is found as long straight lines near the page edges. Long
    lines in the middle of the sheet are kept: those are usually roads."""
    h, w = ink.shape
    turned = np.asarray(Image.fromarray(ink.astype(np.uint8) * 255).rotate(
        skew, resample=Image.NEAREST, fillcolor=0)) > 0
    # Allow a pixel or two of leftover tilt.
    thick_h = ndi.maximum_filter1d(turned, 3, axis=0)
    thick_v = ndi.maximum_filter1d(turned, 3, axis=1)
    horiz = _long_runs(thick_h, 1, int(0.5 * w))
    vert = _long_runs(thick_v, 0, int(0.5 * h))
    rows = np.where(horiz.any(axis=1))[0]
    cols = np.where(vert.any(axis=0))[0]
    band_r, band_c = int(0.15 * h), int(0.15 * w)
    mask = np.zeros_like(turned)
    top = [r for r in rows if r < band_r]
    bottom = [r for r in rows if r > h - band_r]
    left = [c for c in cols if c < band_c]
    right = [c for c in cols if c > w - band_c]
    for r in top + bottom:
        mask[r, :] |= horiz[r, :]
    for c in left + right:
        mask[:, c] |= vert[:, c]
    # Everything outside the border (drafter names, scanner edges) goes too.
    if top:
        mask[:max(top) + 1, :] = True
    if bottom:
        mask[min(bottom):, :] = True
    if left:
        mask[:, :max(left) + 1] = True
    if right:
        mask[:, min(right):] = True
    mask = ndi.binary_dilation(mask, iterations=2)
    back = np.asarray(Image.fromarray(mask.astype(np.uint8) * 255).rotate(
        -skew, resample=Image.NEAREST, fillcolor=0)) > 0
    return back


def _directions(lines_mask, sigma=2.0):
    """Direction of the line through each pixel (degrees, 0-180, counter-
    clockwise from x with y pointing UP), plus how clearly line-like the
    neighbourhood is (0 = blob/corner, 1 = clean straight line)."""
    f = lines_mask.astype(np.float32)
    gy = ndi.sobel(f, axis=0)  # rows go down the page
    gx = ndi.sobel(f, axis=1)
    jxx = ndi.gaussian_filter(gx * gx, sigma)
    jyy = ndi.gaussian_filter(gy * gy, sigma)
    jxy = ndi.gaussian_filter(gx * gy, sigma)
    # Gradient direction (image coords, y down); the line runs at 90 deg to it.
    grad = 0.5 * np.degrees(np.arctan2(2 * jxy, jxx - jyy))
    line_img = grad + 90.0
    angle = (-line_img) % 180.0  # flip to y-up
    coherence = np.sqrt((jxx - jyy) ** 2 + 4 * jxy ** 2) / (jxx + jyy + 1e-9)
    return angle, coherence


def plan_lines(image_path, dpi=300, skew=None, seed=0):
    """Find the long thin lines on a page. Returns PlanLines."""
    ink = _ink_full(image_path)
    full_h, full_w = ink.shape
    k = max(1, int(math.ceil(full_w / WORK_WIDTH)))
    small = _shrink(ink, k)
    del ink
    work_dpi = dpi / k

    if skew is None:
        skew = read.estimate_skew(Image.fromarray(np.where(small, 0, 255).astype(np.uint8)))
    small &= ~_frame_mask(small, skew)

    # Keep ink that runs one consistent way over a wider neighbourhood
    # (~0.15 in): straight or gently curving lines. Text has strokes going
    # every which way, so it drops out. Roads that touch each other get
    # split at the junctions, which is fine.
    angle, coherence = _directions(small, sigma=max(2.0, 0.15 * work_dpi / 2))
    straight = small & (coherence > 0.75)
    # Then drop what's left that is short (bits of letters, symbols).
    labels, n = ndi.label(straight, structure=np.ones((3, 3), bool))
    if n == 0:
        return PlanLines(*(np.zeros(0),) * 3, full_w, full_h, dpi)
    min_len = MIN_LINE_IN * work_dpi
    keep = np.zeros(n + 1, bool)
    for i, sl in enumerate(ndi.find_objects(labels), start=1):
        if sl is not None and max(sl[0].stop - sl[0].start, sl[1].stop - sl[1].start) >= min_len:
            keep[i] = True
    lines_mask = keep[labels]

    rows, cols = np.nonzero(lines_mask)
    if len(rows) > MAX_POINTS:
        pick = np.random.default_rng(seed).choice(len(rows), MAX_POINTS, replace=False)
        rows, cols = rows[pick], cols[pick]
    # Back to full-resolution pixels, from the page centre, v up.
    cx, cy = (full_w - 1) / 2.0, (full_h - 1) / 2.0
    u = cols * k + (k - 1) / 2.0 - cx
    v = -(rows * k + (k - 1) / 2.0 - cy)
    return PlanLines(u, v, angle[rows, cols], full_w, full_h, dpi)


# ---------------------------------------------------------------------------
# 2. Streets as a "closeness" picture
# ---------------------------------------------------------------------------

class StreetSegments:
    """Street centerlines as straight pieces: array of (x1, y1, x2, y2) in
    map feet. Built once by the calling script from RefStreets."""

    def __init__(self, segs):
        self.segs = np.asarray(segs, float).reshape(-1, 4)
        d = self.segs[:, 2:] - self.segs[:, :2]
        self.angle = np.degrees(np.arctan2(d[:, 1], d[:, 0])) % 180.0
        self.length = np.hypot(d[:, 0], d[:, 1])

    def closeness(self, x0, y0, n, cell):
        """For each direction bin, an n x n grid (row = y, col = x, starting
        at x0, y0) with 1 near streets running in or next to that direction,
        fading to 0 at FAR_FT."""
        nbins = 180 // DIR_BIN_DEG
        x1, y1 = x0 + n * cell, y0 + n * cell
        s = self.segs
        inside = ~((np.maximum(s[:, 0], s[:, 2]) < x0 - FAR_FT) | (np.minimum(s[:, 0], s[:, 2]) > x1 + FAR_FT) |
                   (np.maximum(s[:, 1], s[:, 3]) < y0 - FAR_FT) | (np.minimum(s[:, 1], s[:, 3]) > y1 + FAR_FT))
        s, ang, length = s[inside], self.angle[inside], self.length[inside]
        # sample points along every piece, half a cell apart
        steps = np.maximum(1, np.ceil(length / (cell / 2))).astype(int)
        idx = np.repeat(np.arange(len(s)), steps + 1)
        t = np.concatenate([np.linspace(0, 1, k + 1) for k in steps]) if len(s) else np.zeros(0)
        px = s[idx, 0] + t * (s[idx, 2] - s[idx, 0])
        py = s[idx, 1] + t * (s[idx, 3] - s[idx, 1])
        pbin = (ang[idx] // DIR_BIN_DEG).astype(int) % nbins
        ci = np.floor((px - x0) / cell).astype(int)
        ri = np.floor((py - y0) / cell).astype(int)
        ok = (ci >= 0) & (ci < n) & (ri >= 0) & (ri < n)
        ci, ri, pbin = ci[ok], ri[ok], pbin[ok]
        out = np.zeros((nbins, n, n), np.float32)
        for b in range(nbins):
            # a street counts for its own direction and the two next to it
            sel = (pbin == b) | (pbin == (b - 1) % nbins) | (pbin == (b + 1) % nbins)
            if not sel.any():
                continue
            grid = np.ones((n, n), bool)
            grid[ri[sel], ci[sel]] = False
            dist = ndi.distance_transform_edt(grid) * cell
            out[b] = np.clip((FAR_FT - dist) / (FAR_FT - NEAR_FT), 0, 1)
        return out


# ---------------------------------------------------------------------------
# Street-name labels: which road is which
# ---------------------------------------------------------------------------

# A label counts as "next to its street" within LABEL_NEAR_FT, fading to
# nothing at LABEL_FAR_FT.
LABEL_NEAR_FT = 300.0
LABEL_FAR_FT = 600.0
# How much the labels count, next to the line score.
LABEL_WEIGHT = 0.3


def _dist_to_segments(px, py, segs):
    """Distance from each point to the nearest of the segments."""
    if len(segs) == 0:
        return np.full(len(px), np.inf)
    a, b = segs[:, :2], segs[:, 2:]
    ab = b - a
    ab2 = np.maximum((ab ** 2).sum(1), 1e-9)
    p = np.stack([px, py], 1)[:, None, :]          # points x 1 x 2
    t = np.clip(((p - a) * ab).sum(2) / ab2, 0, 1)  # points x segments
    closest = a + t[..., None] * ab
    return np.sqrt(((p - closest) ** 2).sum(2)).min(1)


class Labels:
    """Street names printed on the sheet, with where they are.

    items: list of (u, v, street label, segments array) with u, v in
    full-resolution pixels from the page centre (v up), like PlanLines."""

    def __init__(self, items):
        self.items = items

    def __len__(self):
        return len(self.items)

    def agreement(self, fit):
        """Share of the sheet's streets whose labels land next to that
        street when the sheet is placed this way (0-1). None if no labels."""
        if not self.items:
            return None
        th = math.radians(fit.rotation)
        per_street = {}
        for u, v, street, segs in self.items:
            x = fit.x + fit.scale * (u * math.cos(th) - v * math.sin(th))
            y = fit.y + fit.scale * (u * math.sin(th) + v * math.cos(th))
            d = _dist_to_segments(np.array([x]), np.array([y]), segs)[0]
            ok = min(1.0, max(0.0, (LABEL_FAR_FT - d) / (LABEL_FAR_FT - LABEL_NEAR_FT)))
            per_street.setdefault(street, []).append(ok)
        # a street counts as agreeing if most of its labels do
        return float(np.mean([np.mean(v) for v in per_street.values()]))


# ---------------------------------------------------------------------------
# 3. The search
# ---------------------------------------------------------------------------

class Fit:
    """One way of laying the sheet on the map.
    scale    feet per full-resolution pixel
    rotation degrees the sheet is turned, counter-clockwise
    x, y     map position of the page centre (feet)
    score    share of the plan's line points that land near a matching street
    """

    def __init__(self, scale, rotation, x, y, score):
        self.scale, self.rotation, self.x, self.y, self.score = scale, rotation, x, y, score

    def differs_from(self, other, dist_ft=300, angle_deg=10):
        dr = abs((self.rotation - other.rotation + 180) % 360 - 180)
        return (math.hypot(self.x - other.x, self.y - other.y) > dist_ft or dr > angle_deg
                or abs(math.log(self.scale / other.scale)) > 0.15)

    def world_file(self, width_px, height_px):
        """The six numbers of an ESRI world file (.tfw) for the full image."""
        th = math.radians(self.rotation)
        a = self.scale * math.cos(th)
        b = self.scale * math.sin(th)
        d = self.scale * math.sin(th)
        e = -self.scale * math.cos(th)
        cx, cy = (width_px - 1) / 2.0, (height_px - 1) / 2.0
        c = self.x - a * cx - b * cy
        f = self.y - d * cx - e * cy
        return [a, d, b, e, c, f]

    def corners(self, width_px, height_px):
        """Map coordinates of the four image corners."""
        a, d, b, e, c, f = self.world_file(width_px, height_px)
        out = []
        for col, row in [(-0.5, -0.5), (width_px - 0.5, -0.5),
                         (width_px - 0.5, height_px - 0.5), (-0.5, height_px - 0.5)]:
            out.append((a * col + b * row + c, d * col + e * row + f))
        return out


# Score lost per doubling/halving of the scale away from the stated scale.
# The stated scale is usually right; this stops a smaller sheet (which fits
# anywhere more easily) from winning by a hair.
SCALE_PENALTY_PER_DOUBLING = 0.12


def scale_penalty(scale, prior_scale):
    if not prior_scale:
        return 0.0
    return SCALE_PENALTY_PER_DOUBLING * abs(math.log2(scale / prior_scale))


def _search(lines, streets, cx, cy, radius, scales, rotations, cell, keep=5, prior_scale=None):
    """Try every scale x rotation, every shift within `radius` of (cx, cy).
    Returns the best few Fits that differ from each other."""
    nbins = 180 // DIR_BIN_DEG
    half_extent = 0.5 * math.hypot(lines.width_px, lines.height_px) * max(scales)
    n = int(2 * (radius + half_extent) / cell) + 8
    n = int(2 ** math.ceil(math.log2(n))) if n < 64 else n + (-n % 16)
    x0, y0 = cx - n * cell / 2.0, cy - n * cell / 2.0
    near = streets.closeness(x0, y0, n, cell)
    near_f = np.fft.rfft2(near)
    # only shifts that keep the page centre within `radius` of the location
    rr, cc = np.mgrid[0:n, 0:n]
    centre = n // 2
    allowed = np.hypot(rr - centre, cc - centre) * cell <= radius
    # Balance the directions: one long road drawn with many lines must not
    # outvote the cross street. Each direction's share of the score grows
    # only with the square root of how many points it has.
    page_bin = (lines.angle // DIR_BIN_DEG).astype(int) % nbins
    counts = np.bincount(page_bin, minlength=nbins).astype(float)
    weight = 1.0 / np.sqrt(np.maximum(counts[page_bin], 1))
    weight /= weight.sum()
    # "Chance" points: the same directions, but scattered at random over the
    # area the plan's lines cover. Where even random lines score well (a
    # small sheet sitting on a street), a high score means little.
    rng = np.random.default_rng(0)
    m = 3000
    lo_u, hi_u = np.percentile(lines.u, [2, 98])
    lo_v, hi_v = np.percentile(lines.v, [2, 98])
    rand_u = rng.uniform(lo_u, hi_u, m)
    rand_v = rng.uniform(lo_v, hi_v, m)
    rand_angle = rng.choice(lines.angle, m, p=weight)

    found = []
    for s in scales:
        penalty = scale_penalty(s, prior_scale)
        for rot in rotations:
            th = math.radians(rot)
            dx = s * (lines.u * math.cos(th) - lines.v * math.sin(th))
            dy = s * (lines.u * math.sin(th) + lines.v * math.cos(th))
            ci = np.round(dx / cell).astype(int) % n
            ri = np.round(dy / cell).astype(int) % n
            b = ((lines.angle + rot) % 180.0 // DIR_BIN_DEG).astype(int) % nbins
            total = None
            for k in range(nbins):
                sel = b == k
                if not sel.any():
                    continue
                pk = np.bincount(ri[sel] * n + ci[sel], weights=weight[sel],
                                 minlength=n * n).reshape(n, n).astype(np.float32)
                term = near_f[k] * np.conj(np.fft.rfft2(pk))
                total = term if total is None else total + term
            # corr[t] = sum over line points of near[point offset + t], so t
            # is the grid cell where the page centre sits.
            corr = np.fft.irfft2(total, s=(n, n))
            corr[~allowed] = -1
            row, col = np.unravel_index(np.argmax(corr), corr.shape)
            raw = float(corr[row, col])
            # how random lines would score at this same spot
            rc = np.round(s * (rand_u * math.cos(th) - rand_v * math.sin(th)) / cell).astype(int) + col
            rr_ = np.round(s * (rand_u * math.sin(th) + rand_v * math.cos(th)) / cell).astype(int) + row
            rb = ((rand_angle + rot) % 180.0 // DIR_BIN_DEG).astype(int) % nbins
            ok = (rc >= 0) & (rc < n) & (rr_ >= 0) & (rr_ < n)
            chance = float(near[rb[ok], rr_[ok], rc[ok]].sum()) / m
            # How much better than random lines at the same spot: 0 = no
            # better; near 1 = every line on a matching street where random
            # lines would almost never be.
            score = raw - chance
            fit = Fit(float(s), float(rot), x0 + (col + 0.5) * cell,
                      y0 + (row + 0.5) * cell, score - penalty)
            fit.raw, fit.chance = raw, chance
            found.append(fit)
    found.sort(key=lambda f: -f.score)
    distinct = []
    for f in found:
        if all(f.differs_from(g) for g in distinct):
            distinct.append(f)
        if len(distinct) >= keep:
            break
    return distinct


def _with_labels(fits, labels):
    """Add the street-label check to each fit's score (fit.total)."""
    for f in fits:
        f.labels_ok = labels.agreement(f) if labels else None
        f.total = f.score + (LABEL_WEIGHT * f.labels_ok if f.labels_ok is not None else 0.0)
    return sorted(fits, key=lambda f: -f.total)


def baseline_from_stations(stations, min_labels=3):
    """Scale and direction of a plan's stationed baseline, from the station
    labels printed along it (e.g. 4+560, 4+580, 4+680).

    stations: list of (station in metres, u, v) with u, v in full-resolution
    page pixels from the centre, v up (like PlanLines).
    Labels sit a little off the line, and some are strays (title block
    "STA. 10+000 TO STA. 10+160", side-road stations), so the largest group of
    labels that agree with each other wins (tried for every pair).
    Returns (pixels per metre, page angle in degrees of increasing
    stations, number of agreeing labels) or None."""
    pts = [(s, np.array([u, v])) for s, u, v in stations]
    best = None
    for i in range(len(pts)):
        for j in range(len(pts)):
            si, pi = pts[i]
            sj, pj = pts[j]
            if sj - si < 40:  # want labels at least 40 m apart, in station order
                continue
            d = (pj - pi) / (sj - si)          # pixels per metre, as a vector
            per_m = float(np.hypot(*d))
            if not 5 <= per_m <= 200:
                continue
            unit = d / per_m
            agree = 0
            for sk, pk in pts:
                along = float((pk - pi) @ unit) / per_m   # metres along the line
                across = abs(float((pk - pi) @ np.array([-unit[1], unit[0]]))) / per_m
                if abs(along - (sk - si)) < 4 and across < 25:
                    agree += 1
            key = (agree, sj - si)
            if agree >= min_labels and (best is None or key > best[0]):
                best = (key, per_m, math.degrees(math.atan2(d[1], d[0])))
    if best is None:
        return None
    return best[1], best[2], best[0][0]


def fit_sheet(lines, streets, x, y, radius, scales, prior_scale=None, labels=None, log=None,
              rotations=None):
    """Find how the sheet lies on the map.

    lines   PlanLines of the page
    streets StreetSegments
    x, y    suggested location (step 06)
    radius  how far (ft) the page centre may be from that location
    scales  candidate feet-per-pixel values to try
    prior_scale  the stated scale (feet per pixel), if one was read
    labels  Labels (street names printed on the sheet), optional. They
            settle which way round the sheet goes: two crossing roads look
            the same turned 180 degrees, but their labels don't.
    rotations  rotations (degrees) to try in the coarse search; default every
               3 degrees all the way round
    Returns (best Fit, runner-up Fit or None). fit.total is the final score
    (line score + label bonus); fit.labels_ok the label agreement."""
    if len(lines) < 200:
        return None, None
    # Coarse: every 3 degrees, on a grid of 20 ft, or coarser for sheets that
    # cover a big area (a 1"=400' sheet doesn't need 20 ft detail, and the
    # work grows with the square of the grid size).
    coarse = []
    for s in scales:
        ground_ft = s * max(lines.width_px, lines.height_px)
        cell = max(20.0, ground_ft / 150.0)
        coarse += _search(lines, streets, x, y, radius, [s],
                          np.arange(0, 360, 3.0) if rotations is None else rotations, cell=cell,
                          keep=6, prior_scale=prior_scale)
    coarse.sort(key=lambda f: -f.score)
    distinct = []
    for f in coarse:
        if all(f.differs_from(g) for g in distinct):
            distinct.append(f)
    coarse = _with_labels(distinct[:8], labels)
    if log:
        log("     coarse best: " + "; ".join(
            "{:.0f} deg, {:.0f}'/in, lines {:.2f}{}".format(
                f.rotation, f.scale * lines.dpi, f.score,
                ", labels {:.0%}".format(f.labels_ok) if f.labels_ok is not None else "")
            for f in coarse[:3]))
    # Fine: around each of the best coarse answers, on a 5 ft grid (coarser
    # for sheets that cover a big area, for the same reason as above).
    fine = []
    for f in coarse[:3]:
        rots = f.rotation + np.arange(-3, 3.01, 0.5)
        scs = f.scale * np.exp(np.linspace(-0.08, 0.08, 9))
        cell = max(5.0, f.scale * max(lines.width_px, lines.height_px) / 600.0)
        fine += _search(lines, streets, f.x, f.y, max(120.0, 6 * cell), scs, rots, cell=cell,
                        keep=1, prior_scale=prior_scale)
    fine = _with_labels(fine, labels)
    best = fine[0]
    runner = next((f for f in fine[1:] + coarse if f.differs_from(best)), None)
    return best, runner
