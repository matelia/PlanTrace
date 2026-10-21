"""
Final fine-tuning of a placement against the AERIAL PHOTO (step 3b).

The main fit (place.py) lines the plan up with street CENTERLINES, with a
generous tolerance (plans show road edges, the data is centerlines). That
finds the right spot and angle, but leaves sheets "close" rather than
"correct": a degree or so off, or shifted 10-30 ft.

Here the plan's lines are matched to what they actually show: curbs and
pavement edges, which are visible in the NYS leaf-off orthoimagery. Only a
small adjustment is searched (a couple of degrees, a few percent of scale,
up to MAX_SHIFT_FT), starting from the existing placement, and it is only
kept if the plan's lines clearly sit better on edges in the photo.

Pure numpy/scipy; the photo comes from the public NYS ortho service.
"""

import math

import numpy as np
from scipy import fft as sfft
from scipy import ndimage as ndi

from . import place, review

MAX_SHIFT_FT = 60          # how far the sheet may move
ROT_RANGE_DEG = 2.0        # +- this much rotation (coarse pass in 0.5 deg steps)
ROT_STEP_DEG = 0.25        # (the fine pass uses half this)
DIR_BIN_DEG = 30           # edge direction bins
MIN_GAIN = 0.10            # keep the result only if 10 % better than before
MAX_PHOTO_PX = 4000        # biggest photo requested from the service
GRID_FT = 2.0              # matching grid (ft); edges are found on the sharper photo


def _edges(photo, cell):
    """Edge strength in the photo, split by edge direction. Returns an
    array (direction bins x rows x cols), each blurred a little so that a
    plan line within a couple of feet of an edge still counts."""
    g = np.asarray(photo.convert("L"), np.float32)
    g = ndi.gaussian_filter(g, 1.0)
    gx = ndi.sobel(g, axis=1)
    gy = ndi.sobel(g, axis=0)
    mag = np.hypot(gx, gy)
    mag = np.clip(mag / (np.percentile(mag, 97) + 1e-6), 0, 1)
    # direction the edge RUNS, in map terms (y up): 90 deg off the gradient
    run = (np.degrees(np.arctan2(-gy, gx)) + 90.0) % 180.0
    nb = 180 // DIR_BIN_DEG
    out = np.zeros((nb,) + mag.shape, np.float32)
    for k in range(nb):
        centre = (k + 0.5) * DIR_BIN_DEG
        diff = np.abs((run - centre + 90.0) % 180.0 - 90.0)
        weight = np.clip(1.0 - diff / DIR_BIN_DEG, 0, 1)   # soft: neighbours count partly
        out[k] = ndi.gaussian_filter(mag * weight, max(1.0, 2.0 / cell))
    return out


def _params(wf):
    a, d, b, e, c, f = wf
    return math.hypot(a, d), math.degrees(math.atan2(d, a))


def refine(lines, wf, wkid, scale_range=0.0, log=None, passes=2):
    """refine_to_aerial, run again if the first pass turned the sheet by
    nearly the full search range (it may need to turn further)."""
    new, info = refine_to_aerial(lines, wf, wkid, scale_range, log)
    for _ in range(passes - 1):
        if new is None or abs(info["rotation_change_deg"]) < ROT_RANGE_DEG - 0.25:
            break
        again, info2 = refine_to_aerial(lines, new, wkid, scale_range, log)
        if again is None:
            break
        info = dict(info2, shift_ft=info["shift_ft"] + info2["shift_ft"],
                    rotation_change_deg=info["rotation_change_deg"] + info2["rotation_change_deg"],
                    edge_score_before=info["edge_score_before"])
        new = again
    return new, info


def refine_to_aerial(lines, wf, wkid, scale_range=0.0, log=None):
    """Fine-tune a placement against the aerial photo.

    lines  place.PlanLines of the sheet
    wf     current placement (world-file numbers)
    scale_range  allowed scale change (0.01 = +-1 %). Default 0: keep the
           scale. With the scale free, the photo match tends to stretch the
           sheet toward unrelated edges (houses, tree lines).
    Returns (new world file or None, info dict). None means "no clear
    improvement; keep the current placement"."""
    w, h = lines.width_px, lines.height_px
    s0, rot0 = _params(wf)
    a, d, b, e, c, f = wf
    cx, cy = (w - 1) / 2.0, (h - 1) / 2.0
    X0, Y0 = a * cx + b * cy + c, d * cx + e * cy + f      # map spot of the page centre

    corners = [(a * col + b * row + c, d * col + e * row + f)
               for col, row in [(0, 0), (w, 0), (w, h), (0, h)]]
    xs, ys = [p[0] for p in corners], [p[1] for p in corners]
    pad = MAX_SHIFT_FT + 40
    x0, x1, y0, y1 = min(xs) - pad, max(xs) + pad, min(ys) - pad, max(ys) + pad
    cell = max(1.0, (x1 - x0) / MAX_PHOTO_PX, (y1 - y0) / MAX_PHOTO_PX)
    photo = review.fetch_ortho(x0, y0, x1, y1, wkid, int((x1 - x0) / cell))
    edges = _edges(photo, cell)
    # Edges are found on the sharp photo, then matched on a coarser grid
    # (GRID_FT): plan lines aren't drawn more precisely than that anyway,
    # and it makes the search many times faster.
    k = max(1, int(round(GRID_FT / cell)))
    nb, nr, nc = edges.shape
    nr, nc = nr // k, nc // k
    edges = edges[:, :nr * k, :nc * k].reshape(nb, nr, k, nc, k).mean(axis=(2, 4))
    cell_y = (y1 - y0) / (photo.size[1] / k)
    cell_x = (x1 - x0) / (photo.size[0] / k)
    edges_f = sfft.rfft2(edges, workers=-1)
    # where the page centre sits now, in photo cells (row 0 = top)
    r0, c0 = (y1 - Y0) / cell_y - 0.5, (X0 - x0) / cell_x - 0.5
    rr, cc = np.mgrid[0:nr, 0:nc]
    allowed = np.hypot((rr - r0) * cell_y, (cc - c0) * cell_x) <= MAX_SHIFT_FT
    npts = float(len(lines))

    def score_all(scale, rot):
        th = math.radians(rot)
        dx = scale * (lines.u * math.cos(th) - lines.v * math.sin(th))
        dy = scale * (lines.u * math.sin(th) + lines.v * math.cos(th))
        oc = np.round(dx / cell_x).astype(int) % nc
        orow = np.round(-dy / cell_y).astype(int) % nr
        k = (((lines.angle + rot) % 180.0) // DIR_BIN_DEG).astype(int) % nb
        total = None
        for b_ in range(nb):
            sel = k == b_
            if not sel.any():
                continue
            pk = np.bincount(orow[sel] * nc + oc[sel], minlength=nr * nc).reshape(nr, nc)
            term = edges_f[b_] * np.conj(sfft.rfft2(pk.astype(np.float32), workers=-1))
            total = term if total is None else total + term
        corr = sfft.irfft2(total, s=(nr, nc), workers=-1) / npts
        return corr

    def best_of(scales, rots, best):
        for scale in scales:
            for rot in rots:
                corr = score_all(scale, rot)
                corr[~allowed] = -1
                r, c_ = np.unravel_index(np.argmax(corr), corr.shape)
                if corr[r, c_] > best[0]:
                    best = (float(corr[r, c_]), scale, rot, r, c_)
        return best

    # score of the current placement (no shift, same rotation and scale)
    now = score_all(s0, rot0)
    before = float(now[int(round(r0)) % nr, int(round(c0)) % nc])

    # Coarse pass, then a fine pass around the best of it.
    best = (before, s0, rot0, r0, c0)
    coarse_scales = [1.0] if not scale_range else [1 - scale_range, 1.0, 1 + scale_range]
    best = best_of(s0 * np.array(coarse_scales),
                   rot0 + np.arange(-ROT_RANGE_DEG, ROT_RANGE_DEG + 1e-9, 0.5), best)
    sb, rb = best[1], best[2]
    fine_scales = [1.0] if not scale_range else 1 + np.arange(-scale_range / 2, scale_range / 2 + 1e-9, scale_range / 4)
    best = best_of(sb * np.asarray(fine_scales),
                   rb + np.arange(-0.5, 0.501, ROT_STEP_DEG / 2), best)
    after, scale, rot, r, c_ = best
    Xn, Yn = x0 + (c_ + 0.5) * cell_x, y1 - (r + 0.5) * cell_y
    info = {"edge_score_before": before, "edge_score_after": after,
            "shift_ft": math.hypot(Xn - X0, Yn - Y0), "rotation_change_deg": rot - rot0,
            "scale_change": scale / s0, "photo_ft_per_px": cell}
    if log:
        log("     aerial fine-tune: edge match {:.3f} -> {:.3f}, moved {:.0f} ft, turned {:+.2f} deg, "
            "scale x{:.3f}".format(before, after, info["shift_ft"], rot - rot0, scale / s0))
    if after < before * (1 + MIN_GAIN):
        return None, info
    new = place.Fit(scale, rot, Xn, Yn, after)
    return new.world_file(w, h), info
