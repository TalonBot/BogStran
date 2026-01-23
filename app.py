from flask import Flask, render_template, jsonify
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

            # NEW:
            "credits": colF.strip(),
            "moves_link": colG.strip(),
        }

        mons.append(mon)

    return mons

CHECK_SECONDS = 5 * 60  # how often to check Google Sheet for changes

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

def _sheet_link_to_csv_url(url: str) -> str | None:
    if not url:
        return None

    url = url.strip()

    # Extract gid if present
    gid_match = re.search(r"[#&?]gid=(\d+)", url)
    gid = gid_match.group(1) if gid_match else "0"

    # ✅ NEW: googleusercontent export links often contain the real sheet id near the end
    # Example:
    # https://doc-...googleusercontent.com/export/.../*/<SHEET_ID>?format=csv&gid=0
    m = re.search(r"googleusercontent\.com/.*/([a-zA-Z0-9-_]{20,})\?", url)
    if m:
        sheet_id = m.group(1)
        return f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}"

    # Standard sheets link: /spreadsheets/d/<ID>
    m = re.search(r"docs\.google\.com/spreadsheets/d/([a-zA-Z0-9-_]+)", url)
    if m:
        sheet_id = m.group(1)
        return f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?tqx=out:csv&gid={gid}"

    # Published sheets link: /spreadsheets/d/e/<ID>/pubhtml
    m = re.search(r"docs\.google\.com/spreadsheets/d/e/([a-zA-Z0-9-_]+)", url)
    if m:
        pub_id = m.group(1)
        return f"https://docs.google.com/spreadsheets/d/e/{pub_id}/pub?output=csv&gid={gid}"

    # Fallback: follow redirects (with User-Agent)
    try:
        r = requests.get(
            url,
            allow_redirects=True,
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        final = r.url

        gid_match = re.search(r"[#&?]gid=(\d+)", final)
        gid2 = gid_match.group(1) if gid_match else gid

        m = re.search(r"docs\.google\.com/spreadsheets/d/([a-zA-Z0-9-_]+)", final)
        if m:
            sheet_id = m.group(1)
            return f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid2}"

        m = re.search(r"docs\.google\.com/spreadsheets/d/e/([a-zA-Z0-9-_]+)", final)
        if m:
            pub_id = m.group(1)
            return f"https://docs.google.com/spreadsheets/d/e/{pub_id}/pub?output=csv&gid={gid2}"
    except Exception:
        pass

    return None




# cache learnsets per CSV url
_learnset_cache: dict[str, dict] = {}
_learnset_cache_ts: dict[str, float] = {}

LEARNSET_CACHE_SECONDS = 8 * 60  # keep in sync with main sheet check


def _parse_learnset_csv(csv_text: str) -> dict:
    """
    Parses a learnset sheet where the FIRST ROW contains category headers
    like: Level Up, TMs, Tutor, Egg (and maybe more columns).
    Returns: { "Level Up": [...], "TMs": [...], ... }
    """
    f = io.StringIO(csv_text)
    rows = list(csv.reader(f))

    if not rows:
        return {}

    headers = [h.strip() for h in rows[0]]
    out = {h: [] for h in headers if h}

    for r in rows[1:]:
        # pad row to headers length
        r = r + [""] * (len(headers) - len(r))
        for i, h in enumerate(headers):
            if not h:
                continue
            cell = (r[i] or "").strip()
            if cell:
                out[h].append(cell)

    # remove empty categories
    out = {k: v for k, v in out.items() if v}
    return out


@app.route("/api/learnset/<mon_id>")
def learnset(mon_id):
    mons = get_mons_smart()
    mon = next((m for m in mons if m["id"] == mon_id), None)
    if not mon:
        return jsonify({})

    # TEMP HARD-CODE for testing:
    link = (mon.get("moves_link") or "").strip()
    csv_url = _sheet_link_to_csv_url(link)

    print("MOVES LINK:", link)
    print("CSV URL:", csv_url)

    if not csv_url:
        return jsonify({})

    now = time.time()
    if csv_url in _learnset_cache and (now - _learnset_cache_ts.get(csv_url, 0)) < LEARNSET_CACHE_SECONDS:
        return jsonify(_learnset_cache[csv_url])

    try:
        resp = requests.get(
            csv_url,
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0"},
        )
    except Exception as e:
        print("LEARNSET REQUEST ERROR:", repr(e))
        return jsonify({})

    if resp.status_code != 200:
        print("LEARNSET FETCH FAILED:", resp.status_code)
        print("BODY:", resp.text[:300])
        return jsonify({})

    parsed = _parse_learnset_csv(resp.text)

    _learnset_cache[csv_url] = parsed
    _learnset_cache_ts[csv_url] = now
    return jsonify(parsed)






@app.route("/")
def dex():
    # Single page app: send all mons at once
    return render_template("dex.html", mons=get_mons_smart())



if __name__ == "__main__":
    app.run(debug=True)
