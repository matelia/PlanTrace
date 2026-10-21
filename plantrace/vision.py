"""
Reading plan pages with a local vision AI model (through Ollama).

Ollama is a free program that runs AI models on this computer. Nothing is
sent to the internet. We talk to it over its local web address
(http://localhost:11434) using only Python's built-in libraries.

The model is slower than Tesseract (minutes per page on a computer without a
graphics card) but much better at hand lettering, crooked scans and crowded
sheets. So step 05 uses it:
  - once per page on a shrunken overview  -> sheet type, title block info
  - on zoomed-in tiles only when Tesseract found fewer than 2 streets

Main function: read_with_vision(image_path, ...) -> dict (see SCHEMA below)
"""

import base64
import io
import json
import urllib.request

from PIL import Image

from . import read  # reuse page loading (flatten, grayscale, shrink)

DEFAULT_URL = "http://localhost:11434"

# What we ask the model to return. Ollama forces the answer into this shape.
SCHEMA = {
    "type": "object",
    "properties": {
        "sheet_type": {"type": "string",
                       "enum": ["plan", "profile", "detail", "cover_or_index",
                                "correspondence", "web_map_screenshot", "other"]},
        "street_names": {"type": "array", "items": {"type": "string"}},
        "route_numbers": {"type": "array", "items": {"type": "string"}},
        "town": {"type": "string"},
        "stations": {"type": "array", "items": {"type": "string"}},
        "house_numbers": {"type": "array", "items": {"type": "string"}},
        "scale": {"type": "string"},
        "date": {"type": "string"},
        "firm_or_owner": {"type": "string"},
    },
    "required": ["sheet_type", "street_names"],
}

# Where the plans are, mentioned to the model. Set from config.VISION_AREA.
AREA = "New York State"

OVERVIEW_PROMPT = """This is a scanned page from a set of utility record plans
(gas, water, sewer, electric, telecom) in {area}.
Read it carefully and report:
- sheet_type: what kind of page this is
- street_names: every road/street name written on the page, exactly as written
  (include expressways and highways, e.g. "NORTHERN EXPWY", "MAIN ST")
- route_numbers: route shields or route numbers (e.g. "90", "5")
- town: town or village named on the page, if any
- stations: any stationing values (e.g. "10+250", "STA 12+50")
- house_numbers: any house/building numbers next to buildings or lots
- scale: the drawing scale if written (e.g. 1"=30')
- date: the drawing or revision date if written
- firm_or_owner: the company, utility or agency that made the drawing
Only report text you can actually see. Use empty values when not present."""

TILE_PROMPT = """This is a zoomed-in piece of a scanned utility record plan in
{area}. List every road or street name you can read
(street_names), any route numbers, stationing and house numbers.
Only report text you can actually see."""


def _image_b64(im, max_side):
    im = im.copy()
    im.thumbnail((max_side, max_side), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def ask(im, prompt, model, url=DEFAULT_URL, max_side=1600, timeout=900):
    """Send one image + question to the local model; return its JSON answer."""
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt, "images": [_image_b64(im, max_side)]}],
        "format": SCHEMA,
        "stream": False,
        "think": False,                       # we want the answer, not the reasoning
        "keep_alive": "10m",                  # stay loaded between pages, then free the RAM
        "options": {"temperature": 0, "num_ctx": 8192},
    }
    req = urllib.request.Request(url + "/api/chat", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        reply = json.load(resp)
    text = reply.get("message", {}).get("content", "") or "{}"
    try:
        return json.loads(text)
    except ValueError:
        return {"sheet_type": "other", "street_names": [], "_unparsed": text[:500]}


def tiles(im, grid=(3, 2), overlap=0.12):
    """Cut a page into overlapping pieces so small lettering stays readable."""
    cols, rows = grid if im.width >= im.height else (grid[1], grid[0])
    tw, th = im.width / cols, im.height / rows
    ox, oy = tw * overlap, th * overlap
    for r in range(rows):
        for c in range(cols):
            box = (int(max(0, c * tw - ox)), int(max(0, r * th - oy)),
                   int(min(im.width, (c + 1) * tw + ox)), int(min(im.height, (r + 1) * th + oy)))
            yield im.crop(box)


def is_running(url=DEFAULT_URL):
    """True if Ollama answers on this computer."""
    try:
        with urllib.request.urlopen(url + "/api/tags", timeout=5) as resp:
            return json.load(resp)
    except Exception:
        return None


def has_model(model, url=DEFAULT_URL):
    tags = is_running(url) or {}
    names = [m.get("name", "") for m in tags.get("models", [])]
    return any(n == model or n.split(":")[0] == model or n.startswith(model + ":") or n == model + ":latest"
               for n in names)


def merge(answers):
    """Combine overview + tile answers into one result."""
    out = {"sheet_type": "", "street_names": [], "route_numbers": [], "town": "",
           "stations": [], "house_numbers": [], "scale": "", "date": "", "firm_or_owner": ""}
    for a in answers:
        for k, v in a.items():
            if k not in out:
                continue
            if isinstance(out[k], list):
                for item in v or []:
                    item = str(item).strip()
                    if item and item not in out[k]:
                        out[k].append(item)
            elif not out[k] and v:
                out[k] = str(v).strip()
    return out


def read_with_vision(image_path, model, url=DEFAULT_URL, use_tiles=False, log=print):
    """Overview pass (always) + tile passes (when use_tiles)."""
    gray = read.load_gray(image_path)
    answers = [ask(gray, OVERVIEW_PROMPT.format(area=AREA), model, url)]
    if use_tiles:
        pieces = list(tiles(gray))
        for i, piece in enumerate(pieces, 1):
            log("        vision tile {}/{}".format(i, len(pieces)))
            answers.append(ask(piece, TILE_PROMPT.format(area=AREA), model, url))
    return merge(answers)
