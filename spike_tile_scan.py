"""
Throwaway spike: tile-based scanning with numbered building labels.

Tests: can Claude correctly identify which numbered building has solar panels
when shown a single 300m-wide tile containing several houses?

Run:
    ANTHROPIC_API_KEY=sk-... python spike_tile_scan.py

Output:
    - spike_tile_labeled.jpg  (image Claude sees, with building numbers)
    - prints which buildings Claude says have solar
"""
import base64
import io
import math
import os
import sys

import httpx

sys.path.insert(0, os.path.dirname(__file__))

# ── Config ──────────────────────────────────────────────────────────────────
# Huskvarna centrum — known villa area with some solar
CENTER_LAT = 57.7900
CENTER_LNG = 14.2900
TILE_SIZE_M = 300        # side length of tile in metres
IMAGE_PX    = 640        # square image

ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
if not ANTHROPIC_KEY:
    sys.exit("Set ANTHROPIC_API_KEY")

# ── Geo helpers ──────────────────────────────────────────────────────────────
def _tile_bbox(lat: float, lng: float, size_m: float):
    """Return (S, W, N, E) for a square tile centred on lat/lng."""
    half = size_m / 2
    d_lat = half / 111_000
    d_lng = d_lat / math.cos(math.radians(lat))
    return lat - d_lat, lng - d_lng, lat + d_lat, lng + d_lng


def _geo_to_px(lat: float, lng: float, S: float, W: float, N: float, E: float, px: int):
    """Map (lat, lng) to pixel (x, y) within a WxH image covering S/W/N/E."""
    x = int((lng - W) / (E - W) * px)
    y = int((N - lat) / (N - S) * px)
    return x, y


# ── Fetch tile from Lantmäteriet WMS ────────────────────────────────────────
def fetch_tile(S: float, W: float, N: float, E: float, px: int = IMAGE_PX) -> bytes:
    bbox = f"{W},{S},{E},{N}"
    url = (
        "https://minkarta.lantmateriet.se/map/ortofoto"
        "?SERVICE=WMS&VERSION=1.1.1&REQUEST=GetMap"
        "&LAYERS=Ortofoto_0.25,Ortofoto_0.16"
        "&FORMAT=image/jpeg"
        f"&WIDTH={px}&HEIGHT={px}"
        f"&SRS=EPSG:4326&BBOX={bbox}"
    )
    print(f"  Fetching tile {px}x{px}px …")
    resp = httpx.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    assert "image" in resp.headers.get("content-type", ""), "Not an image response"
    return resp.content


# ── Fetch buildings from OSM ─────────────────────────────────────────────────
_RESIDENTIAL_TYPES = (
    "house|detached|semidetached_house|terrace|residential|"
    "bungalow|villa|farm|cottage|cabin|yes"
)

# Fallback: known villas near Huskvarna centrum for offline/firewalled runs
_HUSKVARNA_FALLBACK = [
    {"lat": 57.78980, "lng": 14.28820, "osm_id": 1, "building_type": "house"},
    {"lat": 57.79050, "lng": 14.28950, "osm_id": 2, "building_type": "house"},
    {"lat": 57.79100, "lng": 14.29050, "osm_id": 3, "building_type": "house"},
    {"lat": 57.79010, "lng": 14.29150, "osm_id": 4, "building_type": "detached"},
    {"lat": 57.78940, "lng": 14.29250, "osm_id": 5, "building_type": "house"},
    {"lat": 57.78880, "lng": 14.29080, "osm_id": 6, "building_type": "house"},
    {"lat": 57.78920, "lng": 14.28930, "osm_id": 7, "building_type": "house"},
]


def fetch_buildings(S: float, W: float, N: float, E: float) -> list[dict]:
    query = (
        f"[out:json][timeout:30]; "
        f"way[\"building\"~\"^({_RESIDENTIAL_TYPES})$\"]({S},{W},{N},{E}); out geom 50;"
    )
    print("  Querying Overpass for buildings …")
    try:
        resp = httpx.post(
            "https://overpass-api.de/api/interpreter",
            data={"data": query},
            timeout=20,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        if resp.status_code == 200:
            elements = resp.json().get("elements", [])
            buildings = []
            for el in elements:
                geom = el.get("geometry") or []
                if len(geom) < 3:
                    continue
                lats = [p["lat"] for p in geom]
                lons = [p["lon"] for p in geom]
                buildings.append({
                    "lat": sum(lats) / len(lats),
                    "lng": sum(lons) / len(lons),
                    "osm_id": el.get("id"),
                    "building_type": el.get("tags", {}).get("building", "yes"),
                })
            if buildings:
                print(f"  Found {len(buildings)} buildings from OSM")
                return buildings
    except Exception as e:
        print(f"  Overpass error: {e}")

    print("  ⚠ Overpass unavailable — using hardcoded Huskvarna fallback buildings")
    return [b for b in _HUSKVARNA_FALLBACK if S <= b["lat"] <= N and W <= b["lng"] <= E]


# ── Draw numbered labels on image ────────────────────────────────────────────
def label_image(
    img_bytes: bytes,
    buildings: list[dict],
    S: float, W: float, N: float, E: float,
) -> bytes:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        sys.exit("pip install pillow")

    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    draw = ImageDraw.Draw(img)
    px = img.width  # square

    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 18)
        font_sm = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 14)
    except Exception:
        font = font_sm = ImageFont.load_default()

    for i, bld in enumerate(buildings, start=1):
        x, y = _geo_to_px(bld["lat"], bld["lng"], S, W, N, E, px)
        # white circle background
        r = 14
        draw.ellipse([x - r, y - r, x + r, y + r], fill="white", outline="red", width=2)
        label = str(i)
        bbox_txt = draw.textbbox((0, 0), label, font=font)
        tw, th = bbox_txt[2] - bbox_txt[0], bbox_txt[3] - bbox_txt[1]
        draw.text((x - tw // 2, y - th // 2), label, fill="red", font=font)

    out = io.BytesIO()
    img.save(out, format="JPEG", quality=85)
    return out.getvalue()


# ── Ask Claude ───────────────────────────────────────────────────────────────
def ask_claude(img_bytes: bytes, n_buildings: int) -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

    b64 = base64.standard_b64encode(img_bytes).decode()
    prompt = (
        f"Swedish aerial orthophoto, top-down view, ~{TILE_SIZE_M}m wide. "
        "Numbered labels (1 to {n}) mark residential building centroids.\n\n"
        "Which numbered buildings have photovoltaic solar panels on their roofs? "
        "Solar PV appears as smooth, uniform rectangular patches clearly contrasting "
        "with surrounding clay tiles, asphalt, or metal roofing.\n\n"
        "Reply with ONLY a comma-separated list of building numbers that have solar, "
        "or the single word NONE if no buildings have solar panels. "
        "Example: '3, 7' or 'NONE'. No other text."
    ).format(n=n_buildings)

    print(f"  Asking Claude about {n_buildings} buildings …")
    msg = client.messages.create(
        model="claude-opus-4-7",
        max_tokens=64,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
                {"type": "text", "text": prompt},
            ],
        }],
    )
    return msg.content[0].text.strip()


# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    S, W, N, E = _tile_bbox(CENTER_LAT, CENTER_LNG, TILE_SIZE_M)
    print(f"\nTile bbox: S={S:.5f} W={W:.5f} N={N:.5f} E={E:.5f}")

    print("\n1. Fetching satellite tile …")
    tile_img = fetch_tile(S, W, N, E)
    print(f"   {len(tile_img):,} bytes")

    print("\n2. Fetching OSM buildings …")
    buildings = fetch_buildings(S, W, N, E)
    if not buildings:
        print("   No buildings found — try a different center or larger tile")
        return

    print("\n3. Drawing labels …")
    labeled = label_image(tile_img, buildings, S, W, N, E)
    out_path = os.path.join(os.path.dirname(__file__), "spike_tile_labeled.jpg")
    with open(out_path, "wb") as f:
        f.write(labeled)
    print(f"   Saved → {out_path}")
    print(f"   Buildings numbered 1–{len(buildings)}:")
    for i, b in enumerate(buildings, 1):
        print(f"     {i:2d}. lat={b['lat']:.5f} lng={b['lng']:.5f}  type={b['building_type']}")

    print("\n4. Asking Claude …")
    answer = ask_claude(labeled, len(buildings))
    print(f"\n   Claude's answer: '{answer}'")

    print("\n5. Parsing result …")
    if answer.upper() == "NONE":
        print("   → No solar panels found in this tile")
    else:
        try:
            indices = [int(x.strip()) for x in answer.split(",") if x.strip().isdigit()]
            print(f"   → Solar panels on building(s): {indices}")
            for idx in indices:
                if 1 <= idx <= len(buildings):
                    b = buildings[idx - 1]
                    print(f"      #{idx}: lat={b['lat']:.5f} lng={b['lng']:.5f}")
                else:
                    print(f"      #{idx}: OUT OF RANGE (n_buildings={len(buildings)})")
        except Exception as e:
            print(f"   → Parse error: {e}  (raw: '{answer}')")

    print(f"\nDone. Open spike_tile_labeled.jpg to verify label placement.")


if __name__ == "__main__":
    main()
