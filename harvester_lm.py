"""Enspecta Solar Lead Machine -- gratis-harvester (Lantmäteriet + OSM).

Noll-kostnadsalternativet till harvester.py. I stället för att grid-scanna
Google Solar API ($0.01/punkt) hämtas allt från öppna källor:

    1. Byggnadspolygoner från OpenStreetMap (Overpass API, gratis, ingen nyckel).
       Filtreras på byggnadstyp och takyta (default 80-400 m2 = villor/gårdar).
    2. Ortofoto-crop från Lantmäteriets Min Karta-WMS (0.16/0.25 m, gratis).
       Throttlas och cachas -- en bild hämtas aldrig två gånger.
    3. Adress från OSM:s addr-taggar; saknas de används Nominatim
       (gratis, hård gräns 1 anrop/s) som fallback.

Leads hamnar i samma SQLite-tabell som harvester.py med place_id
"osm/way/<id>", så prescreen.py och app.py fungerar oförändrade.
Google Solar API anropas ALDRIG här -- offertdata (kWh-potential m.m.)
hämtas separat och enbart för bekräftade leads.

Multipolygon-relationer (innergårdar m.m.) hoppas över; för villor är
det försumbart.

Run:
    python harvester_lm.py                       # default Kågeröd, cap 200
    python harvester_lm.py --town Svalöv --max-buildings 500
    python harvester_lm.py --min-area 100 --max-area 300 --no-geocode
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from typing import Optional

import requests
from dotenv import load_dotenv

import db as shared_db
from harvester import BBOXES, Progress, _build_session

load_dotenv()

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ---- Config ----------------------------------------------------------------

OVERPASS_ENDPOINT = "https://overpass-api.de/api/interpreter"
LM_WMS_ENDPOINT = "https://minkarta.lantmateriet.se/map/ortofoto"
NOMINATIM_ENDPOINT = "https://nominatim.openstreetmap.org/reverse"

USER_AGENT = "EnspectaSolarLeadMachine/1.0 (Enspecta Energi)"

LM_THROTTLE_S = 0.4        # var snäll mot Min Karta -- inte ett bulk-API
NOMINATIM_THROTTLE_S = 1.1  # Nominatims usage policy: max 1 req/s

IMAGE_SIZE_PX = 640
CROP_SIZE_M = 50

DEFAULT_MIN_AREA_M2 = 80
DEFAULT_MAX_AREA_M2 = 400
DEFAULT_MAX_BUILDINGS = 200

# Byggnadstyper som aldrig är villaleads, oavsett yta.
EXCLUDED_BUILDING_TYPES = {
    "garage", "garages", "shed", "carport", "greenhouse", "roof",
    "ruins", "construction", "church", "chapel", "industrial",
    "warehouse", "retail", "commercial", "office", "school",
    "kindergarten", "hospital", "apartments", "service", "hut", "cabin",
}

SESSION = _build_session()


# ---- Geometri ----------------------------------------------------------------

def polygon_area_m2(coords: list) -> float:
    """Shoelace på ekvirektangulär projektion. coords = [(lat, lng), ...]."""
    if len(coords) < 3:
        return 0.0
    lat0 = sum(c[0] for c in coords) / len(coords)
    k_lat = 111_320.0
    k_lng = 111_320.0 * math.cos(math.radians(lat0))
    pts = [(c[1] * k_lng, c[0] * k_lat) for c in coords]
    area = 0.0
    for i in range(len(pts)):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % len(pts)]
        area += x1 * y2 - x2 * y1
    return abs(area) / 2.0


def polygon_centroid(coords: list) -> tuple:
    """Aritmetiskt mittpunktssnitt av hörnen -- gott nog för hustak."""
    n = len(coords)
    return (sum(c[0] for c in coords) / n, sum(c[1] for c in coords) / n)


def wms_bbox(lat: float, lng: float, size_m: float = CROP_SIZE_M) -> str:
    d_lat = (size_m / 2) / 111_320.0
    d_lng = d_lat / math.cos(math.radians(lat))
    return f"{lng - d_lng},{lat - d_lat},{lng + d_lng},{lat + d_lat}"


# ---- Datakällor ----------------------------------------------------------------

def fetch_buildings_overpass(bbox: tuple) -> list[dict]:
    """Alla byggnads-ways i bbox. Returnerar [{id, tags, coords}, ...]."""
    south, west, north, east = bbox
    query = (
        "[out:json][timeout:120];"
        f'way["building"]({south},{west},{north},{east});'
        "out tags geom;"
    )
    r = SESSION.post(
        OVERPASS_ENDPOINT, data={"data": query},
        headers={"User-Agent": USER_AGENT}, timeout=180,
    )
    r.raise_for_status()
    out = []
    for el in r.json().get("elements", []):
        if el.get("type") != "way" or not el.get("geometry"):
            continue
        out.append({
            "id": el["id"],
            "tags": el.get("tags") or {},
            "coords": [(g["lat"], g["lon"]) for g in el["geometry"]],
        })
    return out


def is_candidate(building: dict, min_area: float, max_area: float) -> bool:
    btype = building["tags"].get("building", "yes")
    if btype in EXCLUDED_BUILDING_TYPES:
        return False
    return min_area <= polygon_area_m2(building["coords"]) <= max_area


def address_from_tags(tags: dict) -> Optional[str]:
    street = tags.get("addr:street")
    number = tags.get("addr:housenumber")
    if not street:
        return None
    first = f"{street} {number}" if number else street
    locality = " ".join(p for p in (tags.get("addr:postcode"), tags.get("addr:city")) if p)
    return f"{first}, {locality}" if locality else first


_last_nominatim = 0.0


def nominatim_reverse(lat: float, lng: float) -> Optional[str]:
    global _last_nominatim
    wait = NOMINATIM_THROTTLE_S - (time.monotonic() - _last_nominatim)
    if wait > 0:
        time.sleep(wait)
    _last_nominatim = time.monotonic()
    try:
        r = SESSION.get(
            NOMINATIM_ENDPOINT,
            params={"lat": f"{lat:.7f}", "lon": f"{lng:.7f}", "format": "jsonv2"},
            headers={"User-Agent": USER_AGENT}, timeout=15,
        )
        if r.status_code != 200:
            return None
        a = r.json().get("address") or {}
    except requests.exceptions.RequestException:
        return None
    street = a.get("road")
    if not street:
        return None
    first = f"{street} {a['house_number']}" if a.get("house_number") else street
    city = a.get("village") or a.get("town") or a.get("city") or ""
    locality = " ".join(p for p in (a.get("postcode"), city) if p)
    return f"{first}, {locality}" if locality else first


_last_wms = 0.0


def fetch_lm_ortho(lat: float, lng: float, out_path) -> None:
    """Hämta ortofoto-crop från Min Karta-WMS. Kastar RuntimeError vid fel."""
    global _last_wms
    wait = LM_THROTTLE_S - (time.monotonic() - _last_wms)
    if wait > 0:
        time.sleep(wait)
    _last_wms = time.monotonic()
    params = {
        "SERVICE": "WMS", "VERSION": "1.1.1", "REQUEST": "GetMap",
        "LAYERS": "Ortofoto_0.25,Ortofoto_0.16",
        "FORMAT": "image/png",
        "WIDTH": str(IMAGE_SIZE_PX), "HEIGHT": str(IMAGE_SIZE_PX),
        "SRS": "EPSG:4326", "BBOX": wms_bbox(lat, lng),
    }
    r = SESSION.get(LM_WMS_ENDPOINT, params=params,
                    headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    if r.status_code != 200 or "image" not in r.headers.get("content-type", ""):
        raise RuntimeError(f"LM WMS HTTP {r.status_code}: {r.text[:200]}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(r.content)


# ---- DB ------------------------------------------------------------------------

def lead_exists(place_id: str) -> bool:
    with shared_db.db() as c:
        return c.execute(
            "SELECT 1 FROM leads WHERE place_id = ?", (place_id,)
        ).fetchone() is not None


def insert_lead_lm(row: dict) -> None:
    with shared_db.db() as c:
        c.execute(
            """
            INSERT INTO leads (
                place_id, address, lat, lng, coordinates,
                solar_confidence, roof_area_m2, image_path,
                status, raw_solar_data, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
            """,
            (
                row["place_id"], row["address"], row["lat"], row["lng"],
                f"{row['lat']:.6f},{row['lng']:.6f}",
                "OSM", row["roof_area_m2"], row["image_path"],
                json.dumps({"source": "osm", "tags": row["tags"]}),
                shared_db.utcnow(),
            ),
        )


# ---- Harvest -------------------------------------------------------------------

def harvest_lm(town: str, max_buildings: int, min_area: float, max_area: float,
               use_geocode: bool = True) -> None:
    if town not in BBOXES:
        print(f"ERROR: okänd ort {town!r}. Kända: {list(BBOXES)}", file=sys.stderr)
        sys.exit(2)

    shared_db.ensure_schema()
    bbox = BBOXES[town]

    print("=" * 60)
    print("Enspecta Solar Lead Machine -- gratis-harvester (LM + OSM)")
    print(f"Area: {town}  bbox={bbox}")
    print("Hämtar byggnadspolygoner från Overpass...")
    buildings = fetch_buildings_overpass(bbox)
    candidates = [b for b in buildings if is_candidate(b, min_area, max_area)]
    print(f"  Byggnader i OSM:      {len(buildings):,}")
    print(f"  Kandidater ({min_area:.0f}-{max_area:.0f} m2): {len(candidates):,}")
    print(f"  Max NYA denna körning: {max_buildings}")
    print("  Kostnad:               $0.00 (alla källor gratis)")
    print("=" * 60)

    run_id = shared_db.start_scan_run(f"{town} (LM)", len(candidates), max_buildings)
    progress = Progress(len(candidates), max_buildings, None)
    final_status = "done"

    try:
        for b in candidates:
            if progress.found >= max_buildings:
                progress.log(f"Nådde --max-buildings={max_buildings}; stannar.")
                break
            place_id = f"osm/way/{b['id']}"
            if lead_exists(place_id):
                progress.tick(skipped=True)
                continue

            lat, lng = polygon_centroid(b["coords"])
            img_path = shared_db.IMAGES_DIR / f"{place_id.replace('/', '_')}.png"
            if not img_path.exists():
                try:
                    fetch_lm_ortho(lat, lng, img_path)
                except RuntimeError as e:
                    progress.log(f"  Ortofoto-fel för {place_id}: {e}")
                    progress.tick(error=True)
                    continue

            address = address_from_tags(b["tags"])
            if address is None and use_geocode:
                address = nominatim_reverse(lat, lng)

            area = polygon_area_m2(b["coords"])
            insert_lead_lm({
                "place_id": place_id,
                "address": address,
                "lat": lat,
                "lng": lng,
                "roof_area_m2": round(area, 1),
                "image_path": str(img_path),
                "tags": b["tags"],
            })
            progress.tick(found=True)
            progress.log(
                f"  + [{progress.found:>3}/{max_buildings}] {place_id}  "
                f"area={area:.0f}m2  {address or '(adress saknas)'}"
            )
            if progress.run_id is None:
                shared_db.update_scan_run(
                    run_id, grid_done=progress.done, new_leads=progress.found,
                    skipped=progress.skipped, errors=progress.errors,
                    cost_usd=0.0, eta_seconds=progress.eta_seconds(),
                )
    except KeyboardInterrupt:
        final_status = "aborted"
        progress.log("Avbruten (Ctrl+C) -- läget är sparat, kör igen för att fortsätta.")
    finally:
        progress.close()
        shared_db.finish_scan_run(run_id, final_status)

    print(f"\nKlart ({final_status}). Nya: {progress.found}  "
          f"Skippade (fanns i DB): {progress.skipped}  Fel: {progress.errors}  "
          f"Kostnad: $0.00")
    print("Nästa: `python prescreen.py` AI-graderar taken, sen `streamlit run app.py`.")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Gratis-harvester: OSM-byggnader + LM-ortofoton")
    p.add_argument("--town", default="Kågeröd", choices=list(BBOXES.keys()))
    p.add_argument("--max-buildings", type=int, default=DEFAULT_MAX_BUILDINGS)
    p.add_argument("--min-area", type=float, default=DEFAULT_MIN_AREA_M2,
                   help=f"Minsta takyta m2 (default {DEFAULT_MIN_AREA_M2})")
    p.add_argument("--max-area", type=float, default=DEFAULT_MAX_AREA_M2,
                   help=f"Största takyta m2 (default {DEFAULT_MAX_AREA_M2})")
    p.add_argument("--no-geocode", action="store_true",
                   help="Hoppa över Nominatim-fallback när OSM saknar adress")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    harvest_lm(args.town, args.max_buildings, args.min_area, args.max_area,
               use_geocode=not args.no_geocode)
