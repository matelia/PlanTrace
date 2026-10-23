"""
Reading text off scanned plan pages (step 1 of automatic placement).

Uses Tesseract OCR, a free program installed separately (not a Python package).
We call it directly with subprocess, so nothing extra needs installing into
ArcGIS Pro's Python.

Main function: read_page(image_path, street_names) -> dict with
    streets   : street names found on the sheet (matched against real NYS names)
    scale     : scale text found, e.g. '1"=30\''
    dates     : date-like strings found
    sheet_kind: 'plan', 'correspondence', or 'unknown'
    word_count: how many words OCR could read (a rough quality signal)
"""

import difflib
import math
import os
import re
import subprocess
import tempfile

import numpy as np
from PIL import Image
from scipy import ndimage as ndi

Image.MAX_IMAGE_PIXELS = None  # plan scans are big; that's expected

# Common Windows install location. config.py can override this.
DEFAULT_TESSERACT = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

# Bigger images are shrunk to about this many pixels before reading.
# Keeps very large sheets from taking minutes each.
MAX_PIXELS = 45_000_000

# Words that are street "types", not the distinctive part of a name.
STREET_TYPES = {
    "ROAD", "RD", "DRIVE", "DR", "LANE", "LN", "STREET", "ST", "AVENUE", "AVE",
    "TRAIL", "TRL", "CIRCLE", "CIR", "COURT", "CT", "WAY", "PLACE", "PL",
    "RUN", "RISE", "RIDGE", "CRESCENT", "HOLLOW", "GATE", "PARK", "CHASE",
    "CROSSING", "COVE", "BOULEVARD", "BLVD", "PARKWAY", "PKWY", "TERRACE",
    "EASTBOUND", "WESTBOUND", "NORTHBOUND", "SOUTHBOUND",
}
# Names in the street data that aren't real streets.
IGNORE_NAMES = {"UNNAMED STREET", "DRIVEWAY", "PARKING LOT", "CEMETERY"}

# Highways are often written differently on plans than in street data.
# key = name in our output, value = patterns that mean it on a plan.
# Filled in from config.HIGHWAY_ALIASES by the step scripts.
HIGHWAY_ALIASES = {}

# Words that suggest a page is an email/letter rather than a drawing.
CORRESPONDENCE_HINTS = ["FROM:", "SENT:", "SUBJECT:", "TO:", "CC:", "DEAR", "REGARDS",
                        "TICKET", "@", "ATTACHMENT", "SINCERELY", "EMAIL"]


# ---------------------------------------------------------------------------
# Running Tesseract
# ---------------------------------------------------------------------------

def load_gray(image_path):
    """Open a page, flatten transparency onto white, grayscale, shrink if huge."""
    im = Image.open(image_path)
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        white = Image.new("RGBA", im.size, (255, 255, 255, 255))
        im = Image.alpha_composite(white, im)
    im = im.convert("L")
    pixels = im.width * im.height
    if pixels > MAX_PIXELS:
        f = (MAX_PIXELS / pixels) ** 0.5
        im = im.resize((int(im.width * f), int(im.height * f)), Image.LANCZOS)
    return im


def estimate_skew(im, max_angle=10.0, step=0.1):
    """How many degrees the scan is tilted.

    Tries small rotations and keeps the one where some row or column of the
    page is darkest. That happens when the sheet's long straight lines
    (border, title block, baselines) line up exactly with the image edges."""
    small = im.copy()
    small.thumbnail((2000, 2000))
    ink_im = Image.fromarray(((np.asarray(small) < 128) * 255).astype(np.uint8))
    best, best_score = 0.0, -1.0
    for ang in np.arange(-max_angle, max_angle + step / 2, step):
        r = np.asarray(ink_im.rotate(float(ang))).astype(np.float32)
        score = max(r.sum(axis=1).max(), r.sum(axis=0).max())
        if score > best_score:
            best, best_score = round(float(ang), 1), score
    return best


def remove_long_lines(im, min_len_px=60):
    """Erase long straight horizontal and vertical strokes (underlines,
    road lines, borders). Tesseract can't read words that touch a line."""
    ink = np.asarray(im) < 128
    horiz = ndi.binary_opening(ink, structure=np.ones((1, min_len_px), bool))
    vert = ndi.binary_opening(ink, structure=np.ones((min_len_px, 1), bool))
    clean = ink & ~horiz & ~vert
    return Image.fromarray(np.where(clean, 0, 255).astype(np.uint8))


def _to_png(im):
    fd, tmp = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    im.save(tmp)
    return tmp


def _tesseract_lines(png, tesseract_exe, psm):
    """Run Tesseract and return a list of text lines (only confident words)."""
    cmd = [tesseract_exe, png, "stdout", "--psm", str(psm), "tsv"]
    out = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                         errors="replace", timeout=600).stdout
    lines = {}
    for row in out.splitlines()[1:]:
        parts = row.split("\t")
        if len(parts) < 12:
            continue
        text, conf = parts[11].strip(), float(parts[10])
        if not text or conf < 40:
            continue
        key = (parts[2], parts[3], parts[4])  # block, paragraph, line
        lines.setdefault(key, []).append(text)
    return [" ".join(words) for words in lines.values()]


def _unrotate(x, y, rot, w, h, w2, h2):
    """Where a point (x, y) in a copy of the page turned by `rot` degrees
    (PIL rotate with expand=True; original size w x h, turned size w2 x h2)
    sits on the original page."""
    t = math.radians(rot)
    dx, dy = x - w2 / 2.0, y - h2 / 2.0
    return (w / 2.0 + dx * math.cos(t) - dy * math.sin(t),
            h / 2.0 + dx * math.sin(t) + dy * math.cos(t))


# Pages are shrunk to about this size for finding word positions (labels
# are big text; full resolution only makes it slower).
WORDS_MAX_PIXELS = 20_000_000


def word_positions(image_path, tesseract_exe=DEFAULT_TESSERACT, max_pixels=None):
    """Every confidently read word on the page with WHERE it is.

    Reads the page at 0, 90 and 270 degrees (street names run along their
    roads, so they're printed at all angles), with long ruled lines erased.
    max_pixels: shrink bigger pages to about this size first (default
    WORDS_MAX_PIXELS; larger = slower but catches smaller text).
    Returns a list of [text, x, y] with x, y in pixels of the FULL-size
    image (x right, y down)."""
    max_pixels = max_pixels or WORDS_MAX_PIXELS
    gray = load_gray(image_path)
    with Image.open(image_path) as im:
        full_w, full_h = im.size
    if gray.width * gray.height > max_pixels:
        f = (max_pixels / (gray.width * gray.height)) ** 0.5
        gray = gray.resize((int(gray.width * f), int(gray.height * f)), Image.LANCZOS)
    back = full_w / gray.width
    gray = remove_long_lines(gray, max(40, int(min(gray.size) / 40)))
    words = []
    for rot in (0, 90, 270):
        im = gray.rotate(rot, expand=True, fillcolor=255) if rot else gray
        png = _to_png(im)
        try:
            cmd = [tesseract_exe, png, "stdout", "--psm", "11", "tsv"]
            out = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                                 errors="replace", timeout=900).stdout
        finally:
            os.remove(png)
        for row in out.splitlines()[1:]:
            parts = row.split("\t")
            if len(parts) < 12:
                continue
            text, conf = parts[11].strip(), float(parts[10])
            if len(text) < 2 or conf < 50:
                continue
            left, top, wd, ht = (int(p) for p in parts[6:10])
            x, y = _unrotate(left + wd / 2.0, top + ht / 2.0, rot,
                             gray.width, gray.height, im.width, im.height)
            words.append([text, round(x * back, 1), round(y * back, 1)])
    return words


def ocr_page(image_path, tesseract_exe=DEFAULT_TESSERACT):
    """Read all text on a page, combining two kinds of passes:

    A. the page as-is, at 0, 90 and 270 degrees (plans have text at several
       angles: street names along roads, stationing turned sideways).
       Small pages (web-map screenshots etc.) also at +/-45 degrees.
    B. a "cleaned" copy: straightened (most scans are a few degrees crooked)
       and with long ruled lines erased, at 0 and 90 degrees. This catches
       underlined or line-touching labels that pass A misses.

    Each pass catches things the other misses, so all text is kept.
    Returns (lines, skew_degrees)."""
    gray = load_gray(image_path)
    skew = estimate_skew(gray)
    line_px = max(40, int(min(gray.size) / 40))  # longer than any letter stroke

    passes = [(0, False), (90, False), (270, False)]
    if gray.width * gray.height < 4_000_000:
        passes += [(45, False), (-45, False)]
    passes += [(0, True), (90, True)]

    all_lines = []
    for rot, cleaned in passes:
        if cleaned:
            im = gray.rotate(skew + rot, expand=True, fillcolor=255, resample=Image.BILINEAR)
            im = remove_long_lines(im, line_px)
        else:
            im = gray.rotate(rot, expand=True, fillcolor=255) if rot else gray
        png = _to_png(im)
        try:
            all_lines += _tesseract_lines(png, tesseract_exe, psm=11)
        finally:
            os.remove(png)
    return all_lines, skew


# ---------------------------------------------------------------------------
# Making sense of the text
# ---------------------------------------------------------------------------

def _normalize(text):
    text = text.upper().replace("-", " ").replace("_", " ")
    text = re.sub(r"[^A-Z0-9 ]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def street_core(name):
    """'North Spring Road' -> 'NORTH SPRING' (the distinctive part)."""
    words = _normalize(name).split()
    core = [w for w in words if w not in STREET_TYPES]
    return " ".join(core) if core else " ".join(words)


def build_street_index(street_names):
    """Map each distinctive core -> official street name(s)."""
    index = {}
    for name in street_names:
        if name.upper() in IGNORE_NAMES or name.upper().startswith("EXIT "):
            continue
        core = street_core(name)
        if len(core) < 4 or core.isdigit():
            continue
        index.setdefault(core, set()).add(name)
    return index


def match_streets(lines, street_index, min_ratio=0.86):
    """Find which real streets are named on the sheet.

    For each street core (e.g. 'SPRING HILL'), slide a window of the same
    number of words across every OCR line and accept close matches, so small
    OCR mistakes ('SPRIMG') still count. Short cores (<5 letters) must match
    exactly AND be followed by a street-type word, to avoid false hits like
    'MILL' inside a note."""
    found = {}
    norm_lines = [_normalize(l) for l in lines]
    for core, names in street_index.items():
        n = len(core.split())
        short = len(core.replace(" ", "")) < 5
        for line in norm_lines:
            words = line.split()
            for i in range(len(words) - n + 1):
                window = " ".join(words[i:i + n])
                if short:
                    nxt = words[i + n] if i + n < len(words) else ""
                    ok = window == core and nxt in STREET_TYPES
                    score = 1.0
                else:
                    score = difflib.SequenceMatcher(None, window, core).ratio()
                    ok = score >= min_ratio
                if ok:
                    for name in names:
                        found[name] = max(found.get(name, 0), round(score, 2))
    # highways by their common plan spellings
    joined = "\n".join(l.upper() for l in lines)
    for name, patterns in HIGHWAY_ALIASES.items():
        if any(re.search(p, joined) for p in patterns):
            found.setdefault(name, 1.0)
    return found


# Short forms, so 'MAPLE ROAD' and 'MAPLE RD' compare equal.
TYPE_ABBREVIATIONS = {
    "ROAD": "RD", "DRIVE": "DR", "LANE": "LN", "STREET": "ST", "AVENUE": "AVE",
    "TRAIL": "TRL", "CIRCLE": "CIR", "COURT": "CT", "PLACE": "PL",
    "BOULEVARD": "BLVD", "PARKWAY": "PKWY", "TERRACE": "TER",
}


def _abbr(word):
    return TYPE_ABBREVIATIONS.get(word, word)


# Words that appear on plans by themselves without meaning a street.
# (Place names such as the county are passed in as place_words.)
GENERIC_WORDS = {"CREEK", "CENTER", "NORTH", "SOUTH", "EAST", "WEST", "GROUND", "COUNTY"}


def confirm_one_word_streets(found, lines, place_words=()):
    """Second, stricter look at streets whose distinctive part is ONE word.

    match_streets() accepts 'ADAMS' as Adams Street, but on a plan sheet
    'ADAMS' may well mean 'ADAMS COUNTY' in the title block. Same for
    'GROUND' -> Round Trail Drive, 'MILL CREEK' (a stream) -> Creek Ridge,
    'JAMES SMITH' (a person) -> James Street. So a one-word name only counts
    if the words around it on the sheet match the rest of the official name,
    including its street type: 'MAPLE RD' -> Maple Road yes; 'ADAMS COUNTY'
    -> Adams Street no; 'CROSSING CREEK DR' -> Crossing Creek Drive yes;
    'FALLING CREEK RD' -> Creek Ridge no. If the name has a word BEFORE the
    distinctive one, those two words are enough ('FROM SPRING HILL TO' ->
    Spring Hill Road).

    Exception: a label that stands alone on its own line ('Harvest' on a web
    map, 'MAPLE' on a drawing) also counts, unless it's a place name like a
    town or county (place_words) or a plain word like 'CREEK'.

    found : {street name: score} from match_streets()
    lines : the text the match came from. Lines are joined into one stream,
            because a label is often split over two lines ('MAPLE' / 'RD').
    place_words : town/county names to never accept on their own.
    Returns (kept, dropped), both {name: score}.
    """
    stream = " ".join(_normalize(l) for l in lines).split()
    stream = [_abbr(w) for w in stream]
    lone_labels = {_normalize(l) for l in lines}
    never_alone = {_normalize(p) for p in place_words} | GENERIC_WORDS
    kept, dropped = {}, {}
    for name, score in found.items():
        core = street_core(name).split()
        if len(core) != 1 or name in HIGHWAY_ALIASES:
            kept[name] = score  # multi-word names are distinctive enough already
            continue
        words = [_abbr(w) for w in _normalize(name).split()]
        k = words.index(core[0])          # where the distinctive word sits in the name
        before, after = words[:k], words[k + 1:k + 2]  # type word(s) around it
        ok = (len(core[0]) >= 5 and core[0] not in never_alone
              and core[0] in lone_labels)
        for i, w in enumerate(stream):
            if ok:
                break
            close = (w == core[0] if len(core[0]) < 5
                     else difflib.SequenceMatcher(None, w, core[0]).ratio() >= 0.86)
            if not close or i < len(before):
                continue
            if stream[i - len(before):i] == before and (
                    before or stream[i + 1:i + 1 + len(after)] == after):
                ok = True
        (kept if ok else dropped)[name] = score
    return kept, dropped


SCALE_RE = re.compile(r"""(1\s*["”'′]?\s*(?:=|:|INCH\s*=)\s*\d{1,4}\s*(?:'|FT|FEET|′)?)""", re.I)
DATE_RE = re.compile(r"\b(\d{1,2}[/\-.]\d{1,2}[/\-.](?:19|20)?\d{2})\b")


def find_scale(lines):
    hits = []
    for l in lines:
        for m in SCALE_RE.findall(l.replace(" ", "")):
            hits.append(m)
    return hits


def find_dates(lines):
    return sorted({d for l in lines for d in DATE_RE.findall(l)})


def sheet_kind(lines, streets, page_size_in):
    """'correspondence' (email/letter/ticket, no use for placement), 'plan'
    (has street names we recognized), or 'unknown' (a drawing we couldn't
    read a location from)."""
    text = " ".join(lines).upper()
    hints = sum(text.count(h) for h in CORRESPONDENCE_HINTS)
    words = len(text.split())
    w, h = page_size_in
    letter_portrait = w <= 9 and h <= 14.5  # emails/letters are 8.5x11 or 8.5x14
    if letter_portrait and hints >= 3 and words > 80:
        return "correspondence"
    if streets:
        return "plan"
    return "unknown"


def page_size_inches(image_path, dpi):
    with Image.open(image_path) as im:
        return im.width / dpi, im.height / dpi


def read_page(image_path, street_names, tesseract_exe=DEFAULT_TESSERACT, dpi=300):
    lines, skew = ocr_page(image_path, tesseract_exe)
    index = build_street_index(street_names)
    streets = match_streets(lines, index)
    size = page_size_inches(image_path, dpi)
    return {
        "page_size_in": [round(size[0], 1), round(size[1], 1)],
        "skew": skew,
        "streets": streets,
        "scale": find_scale(lines),
        "dates": find_dates(lines),
        "sheet_kind": sheet_kind(lines, streets, size),
        "word_count": sum(len(l.split()) for l in lines),
        "lines": lines,
    }
