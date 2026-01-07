from flask import Flask, render_template
import csv
import io
import re
import requests
import time
import hashlib


app = Flask(__name__)

# === Your Google Sheet as CSV ===
CSV_URL = (
    "https://docs.google.com/spreadsheets/d/"
    "1qa13OkWHd2Am1QPsy6UC41OMDwjp6E_bMQ5usTaylFs/export?format=csv&gid=0"
)


def slugify(name: str) -> str:
    name = name.strip().lower()
    name = re.sub(r"[^a-z0-9]+", "-", name)
    return name.strip("-") or "mon"


def parse_stats(stats_text: str) -> dict:
    """Turn the stats cell text into a dict like { hp: 60, attack: 75, ... }."""
    stats = {}
    for line in stats_text.splitlines():
        line = line.strip().strip(",").strip('"')
        if not line or ":" not in line:
            continue
        key, val = line.split(":", 1)
        key = key.strip().strip('"').replace("_", " ")
        m = re.search(r"\d+", val)
        stats[key] = int(m.group(0)) if m else val.strip()
    return stats


def extract_types(description: str):
    """
    Finds lines starting with 'Type:' and extracts one or more types.

    Examples the parser can handle:
      Type: Fighting
      Type: Grass/Ghost
      Type: Fighting, Normal
    """
    types = []
    for line in description.splitlines():
        if line.strip().lower().startswith("type:"):
            raw = line.split(":", 1)[1].strip()
            # allow both '/' and ',' separators
            parts = [t.strip() for t in raw.replace(",", "/").split("/") if t.strip()]
            types.extend(parts)
    return types


def remove_type_lines(description: str) -> str:
    """Return description with any 'Type:' lines removed (we show icons instead)."""
    clean = []
    for line in description.splitlines():
        if line.strip().lower().startswith("type:"):
            continue
        clean.append(line)
    return "\n".join(clean).strip()

def normalize_image_url(url: str) -> str:
    """Convert common Google Drive share links into an <img>-friendly URL."""
    if not url:
        return ""

    url = url.strip().strip('"').strip("'")

    # file view link: https://drive.google.com/file/d/<id>/view?...
    m = re.search(r"drive\.google\.com/file/d/([a-zA-Z0-9_-]+)", url)
    if m:
        file_id = m.group(1)
        return f"https://drive.google.com/thumbnail?id={file_id}&sz=w1000"

    # open?id=<id>
    m = re.search(r"drive\.google\.com/open\?id=([a-zA-Z0-9_-]+)", url)
    if m:
        file_id = m.group(1)
        return f"https://drive.google.com/thumbnail?id={file_id}&sz=w1000"

    # uc?...id=<id>
    m = re.search(r"drive\.google\.com/uc\?.*id=([a-zA-Z0-9_-]+)", url)
    if m:
        file_id = m.group(1)
        return f"https://drive.google.com/thumbnail?id={file_id}&sz=w1000"

    # already-direct image URL
    return url



def parse_mons_from_csv_text(text: str):
    """Parse CSV text and convert to a list of mon dicts."""
    f = io.StringIO(text)
    reader = list(csv.reader(f))

    if not reader:
        return []

    mons = []

    # Assuming columns:
    # A = general info (name on first line, then description, including 'Type:' line)
    # B = custom moves
    # C = abilities
    # D = stats text
    # E = image URL
    for row in reader[1:]:
        row = row + [""] * (7 - len(row))  # pad safety
        colA, colB, colC, colD, colE, colF, colG = row[:7]

        lines = [l for l in colA.splitlines() if l.strip()]
        if not lines:
            continue

        name = lines[0].strip()
        raw_description = "\n".join(lines[1:]).strip()

        types = extract_types(raw_description)
        description = remove_type_lines(raw_description)

        mon = {
            "id": slugify(name),
            "name": name,
            "description": description,
            "types": types,
            "moves_raw": colB,
            "abilities_raw": colC,
            "stats_raw": colD,
            "stats": parse_stats(colD),
            "image_url": normalize_image_url(colE),
        }
        mons.append(mon)

    return mons

CHECK_SECONDS = 30  # how often to check Google Sheet for changes

_last_check = 0.0
_last_hash = None
_cached_mons = []


def get_mons_smart():
    """
    Check the sheet occasionally. Only re-parse if the CSV content changed.
    """
    global _last_check, _last_hash, _cached_mons

    now = time.time()

    # If we've checked recently, return cached data
    if _cached_mons and (now - _last_check) < CHECK_SECONDS:
        return _cached_mons

    resp = requests.get(CSV_URL, timeout=15)
    resp.raise_for_status()
    csv_text = resp.text

    # fingerprint the CSV text
    h = hashlib.sha256(csv_text.encode("utf-8")).hexdigest()

    _last_check = now

    # If unchanged, keep cached parsed result
    if h == _last_hash and _cached_mons:
        return _cached_mons

    # Changed -> parse and update cache
    _last_hash = h
    _cached_mons = parse_mons_from_csv_text(csv_text)
    return _cached_mons



@app.route("/")
def dex():
    # Single page app: send all mons at once
    return render_template("dex.html", mons=get_mons_smart())



if __name__ == "__main__":
    app.run(debug=True)
