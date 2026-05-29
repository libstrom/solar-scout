"""
Solar panel scanner — building-based approach.

Flow:
1. OSM solar:    fetch buildings already tagged with solar panels (instant)
2. OSM buildings: fetch all building footprints in area
3. AI scan:      for each building, center satellite image on it → Claude YES/NO
4. Address:      OSM addr tags if present, else Google reverse geocode of centroid

Image source priority:
  1. Lantmäteriet ortofoto (free, CC-BY) — if LANTMATERIET_KEY provided
  2. Google Static Maps (~$5/scan)        — fallback
"""

import io
import math
import base64
import time
import threading
import logging
import httpx
try:
    import googlemaps as googlemaps
except ImportError:
    googlemaps = None  # type: ignore[assignment]
try:
    from known_installations import ENSPECTA_INSTALLATIONS as _BUNDLED_INSTALLATIONS  # type: ignore[import]
except ImportError:
    _BUNDLED_INSTALLATIONS = []
import anthropic
import concurrent.futures
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable

try:
    import numpy as np
    from PIL import Image as _PILImage
    _ENHANCE_AVAILABLE = True
except ImportError:
    _ENHANCE_AVAILABLE = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [scanner] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
_log = logging.getLogger("solar_scout")

# Limit concurrent Overpass calls — 4 parallel workers × 2 calls each can hit
# the overpass-api.de rate limiter. Cap at 2 simultaneous requests.
_OVERPASS_SEM = threading.Semaphore(2)


@dataclass
class Lead:
    lat: float
    lng: float
    address: str
    confidence: float       # 0–1
    source: str             # "osm" | "ai"
    tile_key: str = ""      # for dedup
    building_type: str = "" # OSM building tag value
    zoom: int = 20          # zoom used when fetching satellite image
    samtomt_solar_extra: bool = False  # extra solar found on same property
    solar_location: str = "roof"       # "roof" | "samtomt"
    needs_review: bool = False         # AI was unsure — human should verify
    ai_reasoning: str = ""             # AI's roof description (1-2 sentences)
    image_url: str = ""                # clickable LM WMS satellite URL
    scan_session_id: str | None = None # UUID string for grouping scan results


@dataclass
class ScanStats:
    """Aggregated statistics from a scan run."""
    yes: int = 0
    unsure: int = 0
    no: int = 0

    @property
    def false_positive_rate(self) -> float:
        """Estimate of false positive rate: unsure / (yes + unsure)."""
        denom = self.yes + self.unsure
        return self.unsure / denom if denom > 0 else 0.0


# ── Tile helpers (kept for UI bbox display) ────────────────────────────────────

ZOOM = 19
ZOOM_BUILDING = 20          # higher zoom when centred on one building


def _lat_lng_to_tile(lat: float, lng: float, zoom: int = ZOOM):
    n = 2 ** zoom
    x = int((lng + 180) / 360 * n)
    lat_rad = math.radians(lat)
    y = int((1 - math.log(math.tan(lat_rad) + 1 / math.cos(lat_rad)) / math.pi) / 2 * n)
    return x, y


def _bbox_tiles(south: float, west: float, north: float, east: float, zoom: int = ZOOM):
    x0, y0 = _lat_lng_to_tile(north, west, zoom)
    x1, y1 = _lat_lng_to_tile(south, east, zoom)
    tiles = []
    for x in range(x0, x1 + 1):
        for y in range(y0, y1 + 1):
            tiles.append((x, y))
    return tiles


def _center_bbox(lat: float, lng: float, radius_km: float = 1.0):
    delta_lat = radius_km / 111.0
    delta_lng = radius_km / (111.0 * math.cos(math.radians(lat)))
    return lat - delta_lat, lng - delta_lng, lat + delta_lat, lng + delta_lng


# ── OSM queries ────────────────────────────────────────────────────────────────

def _overpass(query: str, timeout: int = 90) -> list[dict]:
    _BACKOFF = [5, 20, 60]
    # Acquire with timeout so an aborted scan's lingering threads can't block
    # a new scan indefinitely (Streamlit reruns don't kill background threads).
    acquired = _OVERPASS_SEM.acquire(timeout=15)
    if not acquired:
        _log.warning("Overpass semaphore timeout — previous scan still running, proceeding unthrottled")
    try:
        for attempt in range(4):
            try:
                _log.info("Overpass query start (timeout=%ds attempt=%d)", timeout, attempt + 1)
                resp = httpx.post(
                    "https://overpass-api.de/api/interpreter",
                    data={"data": query},
                    timeout=timeout,
                    headers={
                        "User-Agent": "solar-scout/1.0 (https://github.com/libstrom/solar-scout)",
                    },
                )
                if resp.status_code == 429:
                    wait = max(60, _BACKOFF[min(attempt, len(_BACKOFF) - 1)])
                    _log.warning("Overpass rate limited (429) — waiting %ds", wait)
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                data = resp.json()
                remark = data.get("remark", "")
                if remark and ("too many" in remark.lower() or "rate" in remark.lower()):
                    wait = _BACKOFF[min(attempt, len(_BACKOFF) - 1)]
                    _log.warning("Overpass rate limit remark: %s — waiting %ds", remark, wait)
                    if attempt < 3:
                        time.sleep(wait)
                    continue
                elements = data.get("elements", [])
                _log.info("Overpass returned %d elements", len(elements))
                return elements
            except Exception as exc:
                _log.warning("Overpass attempt %d failed: %s", attempt + 1, exc)
                if attempt < 3:
                    time.sleep(_BACKOFF[min(attempt, len(_BACKOFF) - 1)])
        _log.error("Overpass failed after 4 attempts")
        return []
    finally:
        if acquired:
            _OVERPASS_SEM.release()


def _tags_to_address(tags: dict) -> str:
    street = tags.get("addr:street", "")
    number = tags.get("addr:housenumber", "")
    city   = tags.get("addr:city", "")
    parts  = []
    if street and number:
        parts.append(f"{street} {number}")
    elif street:
        parts.append(street)
    if city:
        parts.append(city)
    return ", ".join(parts) if parts else ""


def scan_area_osm(south: float, west: float, north: float, east: float) -> list[Lead]:
    """Buildings already tagged with solar panels in OSM — instant, no AI cost."""
    _log.info("scan_area_osm bbox=(%s,%s,%s,%s)", south, west, north, east)
    query = f"""
    [out:json][timeout:60];
    (
      node["roof:solar_panel"="yes"]({south},{west},{north},{east});
      way["roof:solar_panel"="yes"]({south},{west},{north},{east});
      node["generator:source"="solar"]["building"]({south},{west},{north},{east});
      way["generator:source"="solar"]["building"]({south},{west},{north},{east});
    );
    out center tags;
    """
    try:
        elements = _overpass(query)
    except Exception as exc:
        _log.error("scan_area_osm overpass failed: %s", exc)
        return []
    _log.info("scan_area_osm raw elements=%d", len(elements))
    leads = []
    seen: set[str] = set()
    skipped_no_solar_tag = 0
    skipped_building_type = 0
    skipped_amenity = 0
    skipped_flats = 0
    skipped_geom = 0
    for el in elements:
        tags = el.get("tags", {})
        has_roof_tag = tags.get("roof:solar_panel") == "yes"
        has_building = bool(tags.get("building"))
        has_generator_solar = tags.get("generator:source") == "solar"
        if not (has_roof_tag or (has_generator_solar and has_building)):
            skipped_no_solar_tag += 1
            continue
        btype = tags.get("building", "")
        if btype and btype not in _VILLA_TYPES_OSM:
            _log.debug("OSM skip building=%s addr=%s", btype, _tags_to_address(tags))
            skipped_building_type += 1
            continue
        amenity = tags.get("amenity", "")
        if amenity in _NON_RESIDENTIAL_AMENITIES:
            skipped_amenity += 1
            continue
        try:
            if int(tags.get("building:flats", "0") or "0") > 1:
                skipped_flats += 1
                continue
        except ValueError:
            pass
        if el["type"] == "node":
            lat, lng = el["lat"], el["lon"]
        elif el["type"] == "way" and "center" in el:
            lat, lng = el["center"]["lat"], el["center"]["lon"]
        else:
            skipped_geom += 1
            continue
        key = f"{lat:.5f},{lng:.5f}"
        if key in seen:
            continue
        seen.add(key)
        addr = _tags_to_address(tags)
        leads.append(Lead(lat=lat, lng=lng, address=addr or key,
                          confidence=1.0, source="osm", tile_key=key))
    _log.info(
        "scan_area_osm result=%d leads | skipped: no_solar_tag=%d building_type=%d "
        "amenity=%d flats=%d geom=%d",
        len(leads), skipped_no_solar_tag, skipped_building_type,
        skipped_amenity, skipped_flats, skipped_geom,
    )
    return leads


# Residential building types we want to KEEP. Note: `farm` here is a residential
# farm-house, not a lantbruksbyggnad (which would be `farm_auxiliary`/`barn`).
_NON_RESIDENTIAL_AMENITIES = {
    "school", "university", "college", "kindergarten",
    "hospital", "clinic", "doctors",
    "church", "place_of_worship",
    "community_centre", "social_facility",
    "fire_station", "police",
    "townhall", "courthouse", "post_office",
    "library", "theatre", "cinema",
    "restaurant", "cafe", "fast_food", "pub", "bar",
    "fuel", "car_wash", "parking",
    "waste_transfer_station", "recycling",
}

# Explicit single-family building types accepted by the AI scanner (broad —
# includes "yes" and "residential" since AI can see the building).
_RESIDENTIAL_TYPES = (
    "house|detached|semidetached_house|terrace|bungalow|cabin|residential|"
    "static_caravan|farm|yes"
)

# Strict allowlist used by the OSM scanner, which cannot see the building.
# Only accept tags that unambiguously mean "enfamiljshus". "yes" and
# "residential" are excluded — too many BRFs and schools use them.
_VILLA_TYPES_OSM = {
    "house", "detached", "semidetached_house", "terrace",
    "bungalow", "cabin", "static_caravan", "farm",
}

# Building tag values that are NEVER single-family homes — explicit deny-list
# because Swedish OSM heavily uses "building=yes" for everything. Includes
# flerfamiljshus (apartments/dormitory), commerce, industry, agriculture and
# civic/utility structures.
_NON_RESIDENTIAL_TYPES = {
    # Flerfamiljshus / collective housing
    "apartments", "dormitory", "hotel",
    # Commerce / office
    "office", "retail", "commercial", "supermarket", "kiosk",
    # Industry / utility
    "industrial", "warehouse", "shed", "garage", "garages", "carport",
    "hangar", "service", "transformer_tower", "construction",
    "train_station", "transportation", "fire_station",
    # Civic / public
    "church", "cathedral", "chapel", "mosque", "synagogue",
    "school", "university", "kindergarten", "hospital",
    "civic", "government", "public",
    # Agriculture (lantbruksbyggnader, NOT residential farm-houses)
    "barn", "cowshed", "stable", "farm_auxiliary", "greenhouse", "silo",
    # Misc non-home
    "hut", "roof",
}

# Reject buildings outside this footprint (m²). Excludes garden sheds, carports,
# industrial warehouses, school complexes etc. Upper bound tightened in V1.5 —
# most Swedish villor are 80-300 m²; 400+ m² is more likely a parhus-cluster or
# multi-unit residence we don't want.
MIN_BUILDING_AREA_M2 = 40
MAX_BUILDING_AREA_M2 = 350

# Max distance (m) to snap a building centroid to a nearby OSM address node.
ADDRESS_SNAP_RADIUS_M = 25


def _building_area_m2(bounds: dict, lat: float) -> float:
    h = (bounds["maxlat"] - bounds["minlat"]) * 111_000
    w = (bounds["maxlon"] - bounds["minlon"]) * 111_000 * math.cos(math.radians(lat))
    return h * w


def _nearest_addr_node(lat: float, lng: float, nodes: list[dict]) -> str:
    """Return formatted address of nearest addr-node within ADDRESS_SNAP_RADIUS_M."""
    best_d, best_addr = None, ""
    cos_lat = math.cos(math.radians(lat))
    for n in nodes:
        d_lat = (n["lat"] - lat) * 111_000
        d_lng = (n["lon"] - lng) * 111_000 * cos_lat
        d = math.sqrt(d_lat * d_lat + d_lng * d_lng)
        if d > ADDRESS_SNAP_RADIUS_M:
            continue
        if best_d is None or d < best_d:
            best_d = d
            best_addr = _tags_to_address(n.get("tags", {}))
    return best_addr


def _building_zoom(bounds: dict, lat: float) -> int:
    """Pick zoom so the building fills ~60 % of the 640 px frame."""
    h = (bounds["maxlat"] - bounds["minlat"]) * 111_000
    w = (bounds["maxlon"] - bounds["minlon"]) * 111_000 * math.cos(math.radians(lat))
    size_m = max(h, w, 5)
    cos_lat = math.cos(math.radians(lat))
    z = math.log2(640 * 156_543 * cos_lat / (size_m / 0.6))
    return max(17, min(21, round(z)))


def _get_osm_buildings(south: float, west: float, north: float,
                       east: float, max_count: int = 600) -> list[dict]:
    """
    Return building centroids from OSM, filtered for our use case:
    - building type is a residential class (not industrial/commercial/utility)
    - footprint area between MIN/MAX_BUILDING_AREA_M2
    - an OSM address can be resolved (tag on the building, or addr-node within
      ADDRESS_SNAP_RADIUS_M of its centroid)

    Each item: {lat, lng, address, osm_id, building_type, zoom, area_m2}
    """
    building_q = f"""
    [out:json][timeout:90];
    (
      way["building"~"^({_RESIDENTIAL_TYPES})$"]({south},{west},{north},{east});
    );
    out geom {max_count};
    """
    addr_q = f"""
    [out:json][timeout:60];
    (
      node["addr:street"]["addr:housenumber"]({south},{west},{north},{east});
    );
    out;
    """
    elements = _overpass(building_q, timeout=120)
    addr_nodes = _overpass(addr_q, timeout=120)

    buildings = []
    for el in elements:
        geom = el.get("geometry") or []
        if len(geom) < 3:
            continue
        lats = [p["lat"] for p in geom]
        lons = [p["lon"] for p in geom]
        lat = sum(lats) / len(lats)
        lng = sum(lons) / len(lons)
        bounds = {
            "minlat": min(lats), "maxlat": max(lats),
            "minlon": min(lons), "maxlon": max(lons),
        }

        tags = el.get("tags", {})
        btype = tags.get("building", "house")
        if btype in _NON_RESIDENTIAL_TYPES:
            continue

        # Skip buildings with commercial/public amenity tags even if building=yes
        amenity = tags.get("amenity", "")
        if amenity in _NON_RESIDENTIAL_AMENITIES:
            continue
        # Shop or office tags override the building type
        if tags.get("shop") or tags.get("office"):
            continue

        # Flerfamiljshus often tagged as residential but carry building:flats.
        # >1 flats means it's not a single-family villa.
        flats_raw = tags.get("building:flats", "")
        try:
            if flats_raw and int(flats_raw) > 1:
                continue
        except ValueError:
            pass

        area = _building_area_m2(bounds, lat)
        if area < MIN_BUILDING_AREA_M2 or area > MAX_BUILDING_AREA_M2:
            continue

        addr = _tags_to_address(tags) or _nearest_addr_node(lat, lng, addr_nodes)
        # Don't require an address — scan the building anyway and resolve via
        # reverse geocode after AI confirms solar. Use coords as placeholder.
        if not addr:
            addr = f"{lat:.5f},{lng:.5f}"

        buildings.append({
            "lat": lat, "lng": lng, "address": addr,
            "osm_id": str(el.get("id", "")),
            "building_type": btype,
            "zoom": _building_zoom(bounds, lat),
            "area_m2": round(area),
        })
    return buildings


# ── Samtomt-Sol-Flagga (V1.5 Slice 7) ─────────────────────────────────────────

def _has_extra_solar_nearby(lat: float, lng: float, radius_m: int = 30,
                             exclude_self_m: int = 8) -> dict:
    """
    Check whether the property around (lat, lng) carries additional OSM solar
    tags beyond the central building, and whether a villa-type building sits
    within ~30 m (confirming we're on a villa-tomt, not a lantbruk).

    Returns a dict:
      - extra_solar_found: bool — True if extra solar tags found beyond the centre
      - solar_locations:   list[dict] — [{lat, lng, type}, ...] for each extra solar
      - villa_nearby:      bool — True if a villa-type building exists within
                                  ~30 m (= exclude_self_m * 4)
    """
    # Bounding box for the solar tag scan (radius_m around the centre)
    d_lat = radius_m / 111_000
    d_lng = d_lat / max(math.cos(math.radians(lat)), 1e-6)
    south, west = lat - d_lat, lng - d_lng
    north, east = lat + d_lat, lng + d_lng

    solar_q = f"""
    [out:json][timeout:60];
    (
      node["generator:source"="solar"]({south},{west},{north},{east});
      way["generator:source"="solar"]({south},{west},{north},{east});
      node["power"="generator"]["generator:source"="solar"]({south},{west},{north},{east});
      way["power"="generator"]["generator:source"="solar"]({south},{west},{north},{east});
      node["roof:solar_panel"="yes"]({south},{west},{north},{east});
      way["roof:solar_panel"="yes"]({south},{west},{north},{east});
    );
    out center;
    """

    # Villa-confirmation bbox — exclude_self_m * 4 (default 32 m)
    villa_radius_m = exclude_self_m * 4
    vd_lat = villa_radius_m / 111_000
    vd_lng = vd_lat / max(math.cos(math.radians(lat)), 1e-6)
    v_south, v_west = lat - vd_lat, lng - vd_lng
    v_north, v_east = lat + vd_lat, lng + vd_lng

    building_q = f"""
    [out:json][timeout:60];
    (
      way["building"~"^({_RESIDENTIAL_TYPES})$"]({v_south},{v_west},{v_north},{v_east});
    );
    out center;
    """

    solar_elements = _overpass(solar_q)
    building_elements = _overpass(building_q)

    cos_lat = math.cos(math.radians(lat))

    solar_locations: list[dict] = []
    for el in solar_elements:
        if el.get("type") == "node":
            e_lat, e_lng = el.get("lat"), el.get("lon")
        elif el.get("type") == "way" and "center" in el:
            e_lat, e_lng = el["center"]["lat"], el["center"]["lon"]
        else:
            continue
        if e_lat is None or e_lng is None:
            continue
        d_lat_m = (e_lat - lat) * 111_000
        d_lng_m = (e_lng - lng) * 111_000 * cos_lat
        dist = math.sqrt(d_lat_m * d_lat_m + d_lng_m * d_lng_m)
        if dist <= exclude_self_m:
            # That's the central building itself — skip
            continue
        if dist > radius_m:
            continue
        tags = el.get("tags", {}) or {}
        if tags.get("roof:solar_panel") == "yes":
            stype = "roof"
        elif tags.get("generator:source") == "solar":
            stype = "generator"
        else:
            stype = "solar"
        solar_locations.append({"lat": e_lat, "lng": e_lng, "type": stype})

    villa_nearby = False
    for el in building_elements:
        btype = (el.get("tags") or {}).get("building", "")
        if btype in _NON_RESIDENTIAL_TYPES:
            continue
        if el.get("type") == "way" and "center" in el:
            e_lat, e_lng = el["center"]["lat"], el["center"]["lon"]
        elif el.get("type") == "node":
            e_lat, e_lng = el.get("lat"), el.get("lon")
        else:
            continue
        if e_lat is None or e_lng is None:
            continue
        d_lat_m = (e_lat - lat) * 111_000
        d_lng_m = (e_lng - lng) * 111_000 * cos_lat
        dist = math.sqrt(d_lat_m * d_lat_m + d_lng_m * d_lng_m)
        if dist <= villa_radius_m:
            villa_nearby = True
            break

    return {
        "extra_solar_found": len(solar_locations) > 0,
        "solar_locations": solar_locations,
        "villa_nearby": villa_nearby,
    }


# ── Lantmäteriet ortofoto ──────────────────────────────────────────────────────

# Layer candidates in priority order — the debug script probes which one works.
_LM_SERVICE  = "ortofoto-ccby"
_LM_LAYERS   = ["Ortofoto_0.25", "orto", "ortofoto"]
_LM_ZOOM     = 19   # max zoom for LM open ortofoto (3x3 tiles → ~95m × 95m view)

# Few-shot ground-truth buildings (verified by user).
# Malmö (SE4) + Nässjö/Småland (SE3) for geographic diversity.
# Loaded once per scan session from LM WMS and sent as multi-turn examples.
_FEW_SHOT_COORDS = [
    (55.5705978, 13.0378985, "solar_yes"),   # Risholmsgatan 8, Malmö — SE4 positive
    (57.64119,   14.70581,   "solar_yes_3"), # Queckfeldtsgatan 17, Nässjö — SE3 positive
    (55.5764531, 13.0743366, "solar_no"),    # Remontgatan 41, Malmö — SE4 negative
    (57.6349444, 14.7103611, "solar_no_3"),  # Smålandsgatan 48, Nässjö — SE3 negative
]
_FEW_SHOT_VERDICTS = {
    "solar_yes": (
        "The roof shows a rectangular section of smooth, uniform dark panels that clearly "
        "contrast against the surrounding coarser tile texture — typical PV array from above.\n\n"
        "HOUSE=YES\nSOLAR=YES"
    ),
    "solar_yes_2": (
        "A distinct rectangular patch with a smoother, more uniform surface is visible on "
        "part of the roof, set apart from the surrounding textured tile material — "
        "consistent with a photovoltaic array.\n\n"
        "HOUSE=YES\nSOLAR=YES"
    ),
    "solar_no": (
        "Uniform roof surface with consistent texture throughout — no smooth rectangular "
        "patches or contrast areas visible.\n\n"
        "HOUSE=YES\nSOLAR=NO"
    ),
    "solar_yes_3": (
        "Swedish inland villa (Småland). A clearly defined rectangular area on the roof "
        "surface appears noticeably smoother and more uniform than the surrounding pitched "
        "tile material — the smoothness contrast and regular geometry indicate a PV array.\n\n"
        "HOUSE=YES\nSOLAR=YES"
    ),
    "solar_yes_4": (
        "Single-family home with a south-facing roof slope. A flat, dark rectangular patch "
        "of uniform texture is visible against the coarser surrounding roof surface — "
        "characteristic smoothness contrast of mounted solar modules.\n\n"
        "HOUSE=YES\nSOLAR=YES"
    ),
    "solar_yes_5": (
        "Residential villa, Nässjö area. The roof has a distinctly smoother rectangular "
        "section that stands out from the surrounding textured tiles — consistent with a "
        "photovoltaic installation on the main roof slope.\n\n"
        "HOUSE=YES\nSOLAR=YES"
    ),
    "solar_no_3": (
        "Swedish inland villa (Småland/Nässjö). Roof surface is uniformly textured throughout "
        "— no smooth rectangular patch or contrast area distinguishable from the surrounding "
        "tile material. No photovoltaic installation visible.\n\n"
        "HOUSE=YES\nSOLAR=NO"
    ),
}


def _lm_tile_url(token: str, layer: str, z: int, x: int, y: int) -> str:
    # WMTS order: TileMatrix / TileRow / TileCol  →  z / y / x
    return (
        f"https://api.lantmateriet.se/open/{_LM_SERVICE}/v1/wmts"
        f"/token/{token}/1.0.0/{layer}/default/3857/{z}/{y}/{x}.png"
    )


def _fetch_lantmateriet(lm_key: str, lat: float, lng: float, layer: str = _LM_LAYERS[0]) -> bytes | None:
    """Fetch 3×3 tile grid from Lantmäteriet ortofoto and return a 640×640 PNG."""
    if not _ENHANCE_AVAILABLE:
        return None
    cx, cy = _lat_lng_to_tile(lat, lng, _LM_ZOOM)
    canvas = _PILImage.new("RGB", (768, 768), (80, 80, 80))
    got_any = False
    for dx in range(-1, 2):
        for dy in range(-1, 2):
            url = _lm_tile_url(lm_key, layer, _LM_ZOOM, cx + dx, cy + dy)
            try:
                resp = httpx.get(url, timeout=20)
                if resp.status_code == 200:
                    tile = _PILImage.open(io.BytesIO(resp.content)).convert("RGB")
                    canvas.paste(tile, ((dx + 1) * 256, (dy + 1) * 256))
                    got_any = True
            except Exception:
                pass
    if not got_any:
        return None
    # Crop centre 640×640
    cropped = canvas.crop((64, 64, 704, 704))
    buf = io.BytesIO()
    cropped.save(buf, format="PNG")
    return buf.getvalue()


def _probe_lm_layer(lm_key: str, lat: float = 59.33, lng: float = 18.07) -> str | None:
    """Return the first working layer name, or None if all fail."""
    cx, cy = _lat_lng_to_tile(lat, lng, _LM_ZOOM)
    for layer in _LM_LAYERS:
        url = _lm_tile_url(lm_key, layer, _LM_ZOOM, cx, cy)
        try:
            r = httpx.get(url, timeout=15)
            if r.status_code == 200 and r.headers.get("content-type", "").startswith("image"):
                return layer
        except Exception:
            pass
    return None


# ── Claude Vision ──────────────────────────────────────────────────────────────

def _fetch_mapbox(mapbox_key: str, lat: float, lng: float, zoom: int = ZOOM_BUILDING) -> bytes | None:
    # Mapbox uses lng,lat order (not lat,lng)
    url = (
        f"https://api.mapbox.com/styles/v1/mapbox/satellite-v9/static"
        f"/{lng},{lat},{zoom}/640x640"
        f"?access_token={mapbox_key}"
    )
    try:
        resp = httpx.get(url, timeout=20)
        resp.raise_for_status()
        return resp.content
    except Exception:
        return None


def lm_wms_url(lat: float, lng: float, size_m: float = 80, width: int = 640, height: int = 480) -> str:
    """Return a clickable Lantmäteriet WMS URL centred on (lat, lng). No API key required."""
    d_lat = (size_m / 2) / 111_000
    d_lng = d_lat / math.cos(math.radians(lat))
    d_lat_h = d_lat * height / width
    bbox = f"{lng - d_lng},{lat - d_lat_h},{lng + d_lng},{lat + d_lat_h}"
    return (
        "https://minkarta.lantmateriet.se/map/ortofoto"
        "?SERVICE=WMS&VERSION=1.1.1&REQUEST=GetMap"
        "&LAYERS=Ortofoto_0.25,Ortofoto_0.16"
        "&FORMAT=image/jpeg"
        f"&WIDTH={width}&HEIGHT={height}"
        f"&SRS=EPSG:4326&BBOX={bbox}"
    )


def _fetch_lm_wms(lat: float, lng: float, size_m: float = 18) -> bytes | None:
    """Lantmäteriet minkarta WMS — free, no key, high-res Swedish orthophoto."""
    d_lat = (size_m / 2) / 111_000
    d_lng = d_lat / math.cos(math.radians(lat))
    bbox = f"{lng-d_lng},{lat-d_lat},{lng+d_lng},{lat+d_lat}"
    url = (
        "https://minkarta.lantmateriet.se/map/ortofoto"
        "?SERVICE=WMS&VERSION=1.1.1&REQUEST=GetMap"
        "&LAYERS=Ortofoto_0.25,Ortofoto_0.16"
        "&FORMAT=image/jpeg&WIDTH=640&HEIGHT=640"
        f"&SRS=EPSG:4326&BBOX={bbox}"
    )
    try:
        resp = httpx.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code == 200 and "image" in resp.headers.get("content-type", ""):
            return resp.content
    except Exception:
        pass
    return None


_installations_cache: list[tuple[float, float, str]] | None = None


def _load_installations() -> list[tuple[float, float, str]]:
    """Load known Enspecta installations from Supabase, fall back to bundled list."""
    global _installations_cache
    if _installations_cache is not None:
        return _installations_cache
    try:
        from supabase import create_client
        import os
        url = os.environ.get("SUPABASE_URL", "")
        key = os.environ.get("SUPABASE_ANON_KEY", "")
        if url and key:
            sb = create_client(url, key)
            rows = sb.table("enspecta_installations").select("lat,lng,address").execute().data or []
            if rows:
                _installations_cache = [(r["lat"], r["lng"], r["address"]) for r in rows]
                return _installations_cache
    except Exception:
        pass
    _installations_cache = list(_BUNDLED_INSTALLATIONS)
    return _installations_cache


def _is_existing_customer(
    lat: float,
    lng: float,
    radius_m: float = 30.0,
    installations: list[tuple[float, float, str]] | None = None,
) -> bool:
    """Return True if (lat, lng) is within radius_m metres of a known Enspecta installation."""
    if installations is None:
        try:
            installations = _load_installations()
        except Exception:
            return False
    R = 6_371_000
    lat_r = math.radians(lat)
    for inst_lat, inst_lng, _ in installations:
        dlat = math.radians(inst_lat - lat)
        dlng = math.radians(inst_lng - lng)
        a = math.sin(dlat / 2) ** 2 + math.cos(lat_r) * math.cos(math.radians(inst_lat)) * math.sin(dlng / 2) ** 2
        if R * 2 * math.asin(math.sqrt(a)) <= radius_m:
            return True
    return False


def _fetch_street_view(google_key: str, lat: float, lng: float) -> bytes | None:
    """Fetch a Google Street View image for the given location.

    Returns JPEG bytes, or None if no panorama exists nearby or the request fails.
    pitch=20 tilts the camera slightly upward to reveal roof slopes.
    source=outdoor avoids indoor panoramas.
    """
    url = (
        "https://maps.googleapis.com/maps/api/streetview"
        f"?size=400x400&location={lat},{lng}"
        f"&pitch=20&source=outdoor&return_error_code=true"
        f"&key={google_key}"
    )
    try:
        resp = httpx.get(url, timeout=10)
        if resp.status_code == 200 and "image" in resp.headers.get("content-type", ""):
            return resp.content
    except Exception:
        pass
    return None


def _load_dynamic_few_shot(user_id: str | None = None, max_each: int = 4) -> list[tuple[str, str]]:
    """Load confirmed leads from Supabase as additional few-shot examples.

    Fetches up to max_each YES and max_each NO examples that David has reviewed.
    Uses confirmed_image_url when available, falls back to image_url so that
    leads marked via the "❌ Fel" button (which sets false_positive but not
    confirmed_image_url) are still used as negative calibration examples.
    Falls back silently — never blocks the scan.
    """
    try:
        import os, httpx as _httpx
        from supabase import create_client as _sc
        url = os.environ.get("SUPABASE_URL", "")
        key = os.environ.get("SUPABASE_ANON_KEY", "")
        if not (url and key and user_id):
            return []
        sb = _sc(url, key)
        yes_rows = (
            sb.table("scout_leads")
            .select("confirmed_image_url,image_url,lat,lng")
            .eq("user_id", user_id)
            .eq("user_confirmed", True)
            .eq("false_positive", False)
            .order("created_at", desc=True)
            .limit(max_each)
            .execute()
            .data or []
        )
        # Negative examples: review-rejected UNSURE leads (user_confirmed=false)
        # OR explicitly "❌ Fel"-marked leads (false_positive=true).
        # The comment above used to match the code, but the filter only checked
        # false_positive — so every "❌ Nej" click on an UNSURE lead was silently
        # dropped from the few-shot pool. PostgREST .or_ restores both signals
        # and retroactively unlocks rows already marked in the review queue.
        no_rows = (
            sb.table("scout_leads")
            .select("confirmed_image_url,image_url,lat,lng")
            .eq("user_id", user_id)
            .or_("false_positive.eq.true,user_confirmed.eq.false")
            .order("created_at", desc=True)
            .limit(max_each)
            .execute()
            .data or []
        )
        examples = []
        yes_verdict = (
            "Roof shows a rectangular section with smooth, uniform panel texture — "
            "clearly different from surrounding roof material. User-confirmed solar installation.\n\n"
            "HOUSE=YES\nSOLAR=YES"
        )
        no_verdict = (
            "Roof surface is uniform throughout — no panel contrast, no distinct rectangular patches. "
            "User-confirmed: no solar panels. This is a typical SE3 Swedish villa without PV.\n\n"
            "HOUSE=YES\nSOLAR=NO"
        )

        def _fetch_img(row: dict) -> bytes | None:
            for key in ("confirmed_image_url", "image_url"):
                img_url = row.get(key)
                if img_url:
                    try:
                        resp = _httpx.get(img_url, timeout=8)
                        if resp.status_code == 200 and resp.content:
                            return resp.content
                    except Exception:
                        pass
            return None

        for row in yes_rows:
            content = _fetch_img(row)
            if content:
                examples.append((base64.standard_b64encode(content).decode(), yes_verdict))
        for row in no_rows:
            content = _fetch_img(row)
            if content:
                examples.append((base64.standard_b64encode(content).decode(), no_verdict))
        if examples:
            _log.info("dynamic few-shot: %d examples from Supabase", len(examples))
        return examples
    except Exception as _e:
        _log.debug("dynamic few-shot load failed (non-fatal): %s", _e)
        return []


def _load_few_shot_images(user_id: str | None = None) -> list[tuple[str, str]]:
    """Download few-shot examples from LM WMS once per session.

    Loads hardcoded verified examples first, then appends dynamic examples
    from Supabase (user-confirmed leads). Returns [] if hardcoded downloads
    fail so callers degrade gracefully to text-only prompts.
    """
    examples = []
    for lat, lng, label in _FEW_SHOT_COORDS:
        img = _fetch_lm_wms(lat, lng)
        if img is None:
            _log.warning("few-shot download failed for %s — disabling few-shot", label)
            return []
        b64 = base64.standard_b64encode(img).decode()
        examples.append((b64, _FEW_SHOT_VERDICTS[label]))
    dynamic = _load_dynamic_few_shot(user_id=user_id)
    examples.extend(dynamic)
    _log.info("few-shot examples loaded: %d (%d dynamic)", len(examples), len(dynamic))
    return examples


def _fetch_satellite(
    google_key: str,
    lat: float,
    lng: float,
    zoom: int = ZOOM_BUILDING,
    mapbox_key: str | None = None,
    lm_key: str | None = None,
    lm_layer: str = _LM_LAYERS[0],
) -> bytes | None:
    # Priority: official LM API → free minkarta WMS → Mapbox → Google
    if lm_key and _ENHANCE_AVAILABLE:
        img = _fetch_lantmateriet(lm_key, lat, lng, layer=lm_layer)
        if img:
            _log.debug("_fetch_satellite source=lm_tile lat=%s lng=%s", lat, lng)
            return img
    img = _fetch_lm_wms(lat, lng)
    if img:
        _log.debug("_fetch_satellite source=lm_wms lat=%s lng=%s", lat, lng)
        return img
    if mapbox_key:
        img = _fetch_mapbox(mapbox_key, lat, lng, zoom)
        if img:
            _log.debug("_fetch_satellite source=mapbox lat=%s lng=%s", lat, lng)
            return img
    url = (
        f"https://maps.googleapis.com/maps/api/staticmap"
        f"?center={lat},{lng}&zoom={zoom}&size=640x640"
        f"&maptype=satellite&key={google_key}"
    )
    try:
        resp = httpx.get(url, timeout=20)
        resp.raise_for_status()
        _log.debug("_fetch_satellite source=google lat=%s lng=%s", lat, lng)
        return resp.content
    except Exception as exc:
        _log.warning("_fetch_satellite failed lat=%s lng=%s: %s", lat, lng, exc)
        return None


def _enhance_contrast(img_bytes: bytes) -> bytes:
    """Boost image contrast via tile-based CLAHE on the Y channel (YCbCr).

    Helps Claude distinguish smooth PV patches on overcast/flat Scandinavian
    orthophotos. Falls back silently to original bytes if PIL/numpy absent.
    """
    if not _ENHANCE_AVAILABLE:
        return img_bytes
    try:
        img = _PILImage.open(io.BytesIO(img_bytes)).convert("YCbCr")
        y, cb, cr = img.split()
        y_arr = np.array(y, dtype=np.uint8)

        # Tile-based equalization — 4×4 grid, each tile equalised independently
        h, w = y_arr.shape
        tile_h, tile_w = h // 4, w // 4
        out = np.empty_like(y_arr)
        for ti in range(4):
            for tj in range(4):
                r0, r1 = ti * tile_h, (ti + 1) * tile_h if ti < 3 else h
                c0, c1 = tj * tile_w, (tj + 1) * tile_w if tj < 3 else w
                tile = y_arr[r0:r1, c0:c1]
                hist, _ = np.histogram(tile.flatten(), 256, (0, 256))
                cdf = hist.cumsum()
                cdf_min = int(cdf[cdf > 0][0])
                n = tile.size
                lut = np.round(
                    (cdf - cdf_min) / max(n - cdf_min, 1) * 255
                ).clip(0, 255).astype(np.uint8)
                out[r0:r1, c0:c1] = lut[tile]

        # Blend: 60% CLAHE + 40% original to avoid over-sharpening
        blended = (0.6 * out + 0.4 * y_arr).clip(0, 255).astype(np.uint8)
        y_new = _PILImage.fromarray(blended, mode="L")
        enhanced = _PILImage.merge("YCbCr", (y_new, cb, cr)).convert("RGB")
        buf = io.BytesIO()
        enhanced.save(buf, format="JPEG", quality=85)
        return buf.getvalue()
    except Exception:
        return img_bytes


def _analyze_building(
    client: anthropic.Anthropic,
    img_bytes: bytes,
    few_shot: list[tuple[str, str]] | None = None,
    street_view_bytes: bytes | None = None,
) -> tuple[bool, bool, bool, str]:
    """
    Returns (is_residential_house, has_solar_panels, is_unsure, reasoning).

    is_unsure=True means SOLAR=UNSURE — AI sees possible panels but can't
    confirm. These become needs_review=True leads surfaced in the review queue.

    street_view_bytes: optional JPEG from Google Street View, included as a
    second reference image to resolve ambiguous cases.
    """
    img_bytes = _enhance_contrast(img_bytes)
    b64 = base64.standard_b64encode(img_bytes).decode()
    sv_clause = (
        "\n\nA street-level photo of the SAME property follows the aerial image. "
        "Use it as a secondary reference — solar panels on south-facing roof slopes "
        "are often clearly visible from the street. If the street-level view confirms "
        "or refutes panels, weight it heavily. If the view is obstructed or unclear, "
        "rely on the aerial image."
        if street_view_bytes else ""
    )
    instruction = (
        "Swedish aerial orthophoto, ~50m wide, top-down view. "
        "SE3/SE4 grid zone (Småland, Skåne, Jönköping). Dominant roof materials: "
        "red/brown clay tiles (Skandiategel, tegelpannor), grey fibre cement, "
        "black bitumen/EPDM, corrugated cement, standing-seam metal (plåttak).\n\n"

        "STEP 1 — Is the CENTRAL structure a single-family home?\n"
        "(villa, parhus, radhus, fritidshus — NOT garage, carport, barn, shed, "
        "warehouse, industrial, church, school, kiosk, construction site, "
        "apartment block/BRF/flerfamiljshus, office building, retail/commercial)\n"
        "→ If NO: HOUSE=NO, SOLAR=NO. Stop here.\n\n"

        "STEP 2 — Describe the roof in one sentence:\n"
        "roof shape, dominant texture/colour, and whether you can see any area "
        "that looks distinctly different from the surrounding roof material.\n\n"

        "STEP 3 — Look for PV evidence. EITHER signal counts:\n"
        "  (a) SMOOTHNESS CONTRAST — a clearly smoother, more uniform area against "
        "      the bumpy/ribbed texture of adjacent tiles or shingles. This is the "
        "      primary signal on tegelpannor/Skandiategel roofs.\n"
        "  (b) RECTANGULAR BOUNDARY — a discrete rectangular sub-area with a visible "
        "      edge against the rest of the roof. Panels can also cover a full roof "
        "      slope; in that case look for the bottom/side edge against the roof "
        "      gutter or ridge line, plus visible module seams.\n"
        "Module-grid lines, mirror-bright reflections, and uniform dark-blue/black "
        "panel colour are supporting signals.\n\n"

        "COMMON TRAPS that are NOT solar (default SOLAR=NO):\n"
        "• Skandiategel / tegelpannor (clay tiles) — bumpy ridge texture; ubiquitous in SE3\n"
        "• Corrugated grey fibre cement — ribbed surface, no flat patch contrast; "
        "common on 1960–1980s Swedish housing\n"
        "• Standing-seam metal (plåttak) — long parallel ribs ridge-to-eave with "
        "uniform colour — no smoother sub-area\n"
        "• Eternite / smooth grey fibre cement — uniform grey, no module grid\n"
        "• Copper or green-patina metal — uniform colour across the whole roof\n"
        "• Whole-roof dark bitumen / asphalt / EPDM with no smoother patch\n"
        "• Shadows, skylights, snow patches, dormer windows — irregular shape\n"
        "• Solar thermal collectors (solfångare) — narrow tube strips, not flat panels\n\n"

        "CALIBRATION: Only ~5–10% of Swedish villas have solar. "
        "When the signal is subtle, uncertain, or ambiguous → SOLAR=UNSURE, not YES. "
        "Reserve SOLAR=NO for clear cases where no smoother patch and no rectangular "
        "sub-area is visible. A missed panel costs less than a false alarm, but "
        "SOLAR=NO on a panel that's clearly visible is also a loss.\n\n"

        "End with exactly two lines, nothing after:\n"
        "HOUSE=YES or HOUSE=NO\n"
        "SOLAR=YES or SOLAR=UNSURE or SOLAR=NO\n"
        "(SOLAR=NO whenever HOUSE=NO)"
        + sv_clause
    )
    try:
        system_blocks: list[dict] = [
            {"type": "text", "text": instruction, "cache_control": {"type": "ephemeral"}}
        ]
        if few_shot:
            msgs: list[dict] = []
            last_idx = len(few_shot) - 1
            for i, (ex_b64, verdict) in enumerate(few_shot):
                img_block: dict = {
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/jpeg", "data": ex_b64},
                }
                if i == last_idx:
                    img_block["cache_control"] = {"type": "ephemeral"}
                msgs.append({
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Analyze this Swedish aerial roof image:"},
                        img_block,
                    ],
                })
                msgs.append({"role": "assistant", "content": verdict})
            final_content: list[dict] = [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
            ]
            if street_view_bytes:
                sv_b64 = base64.standard_b64encode(street_view_bytes).decode()
                final_content.append({"type": "text", "text": "Street-level view of the same property:"})
                final_content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": sv_b64}})
            msgs.append({"role": "user", "content": final_content})
        else:
            final_content = [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
            ]
            if street_view_bytes:
                sv_b64 = base64.standard_b64encode(street_view_bytes).decode()
                final_content.append({"type": "text", "text": "Street-level view of the same property:"})
                final_content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": sv_b64}})
            msgs = [{"role": "user", "content": final_content}]
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=220,
            system=system_blocks,
            messages=msgs,
        )
        raw = msg.content[0].text
        text = raw.upper()
        is_house = "HOUSE=YES" in text
        has_solar = is_house and "SOLAR=YES" in text and "SOLAR=UNSURE" not in text
        is_unsure = is_house and "SOLAR=UNSURE" in text
        reasoning = ""
        for line in raw.splitlines():
            if line.strip().upper().startswith(("HOUSE=", "SOLAR=")):
                break
            if line.strip():
                reasoning = line.strip()
                break
        _log.debug("_analyze_building HOUSE=%s SOLAR=%s UNSURE=%s few_shot=%s sv=%s",
                   is_house, has_solar, is_unsure, bool(few_shot), bool(street_view_bytes))
        return is_house, has_solar, is_unsure, reasoning
    except Exception as exc:
        _log.error("_analyze_building API error: %s", exc)
        return False, False, False, ""


def _analyze_building_opus(
    client: anthropic.Anthropic,
    img_bytes: bytes,
    street_view_bytes: bytes | None = None,
) -> tuple[bool, bool, bool, str]:
    """Opus 4.8 + extended thinking for borderline UNSURE buildings.

    Forces a definitive YES/NO verdict — is_unsure is always False on success.
    On API error, returns (False, False, True, "") so the caller falls through
    to needs_review=True rather than silently dropping the lead.
    """
    img_bytes = _enhance_contrast(img_bytes)
    b64 = base64.standard_b64encode(img_bytes).decode()

    sv_clause = (
        "\n\nA street-level photo of the same property follows the aerial image. "
        "Solar panels on south-facing slopes are often clearly visible from the street. "
        "Weight it heavily if it confirms or refutes panels."
        if street_view_bytes
        else ""
    )
    instruction = (
        "You are an expert solar panel detection system. "
        "A faster model already flagged this image as uncertain — use your extended "
        "reasoning to reach a definitive answer.\n\n"
        "Swedish aerial orthophoto, ~50 m wide, top-down view. SE3/SE4 grid zone "
        "(Småland, Skåne, Jönköping). Dominant roof materials: red/brown clay tiles "
        "(Skandiategel, tegelpannor), grey fibre cement, black bitumen/EPDM, "
        "corrugated cement, standing-seam metal (plåttak).\n\n"
        "STEP 1 — Is the CENTRAL structure a single-family home?\n"
        "(villa, parhus, radhus, fritidshus — NOT garage, barn, warehouse, industrial, "
        "church, school, apartment block/BRF/flerfamiljshus, office, retail)\n"
        "→ If NO: HOUSE=NO, SOLAR=NO\n\n"
        "STEP 2 — Look for PV evidence:\n"
        "  (a) SMOOTHNESS CONTRAST — a clearly smoother, more uniform area against "
        "      the bumpy/ribbed texture of adjacent tiles\n"
        "  (b) RECTANGULAR BOUNDARY — a discrete rectangular sub-area with a visible edge\n"
        "  (c) MODULE GRID — parallel seam lines; uniform dark-blue/black colour\n\n"
        "NOT solar: Skandiategel clay tiles (bumpy ridges), corrugated fibre cement "
        "(ribbed, uniform grey), standing-seam metal (parallel ribs ridge-to-eave, "
        "no smoother sub-area), bitumen/EPDM (whole-roof uniform dark, no patch), "
        "copper/green-patina metal (uniform across the whole roof).\n\n"
        "Think deeply. Commit to a verdict — do not hedge."
        + sv_clause
        + "\n\nEnd with exactly two lines:\nHOUSE=YES or HOUSE=NO\nSOLAR=YES or SOLAR=NO"
    )

    final_content: list[dict] = [
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
    ]
    if street_view_bytes:
        sv_b64 = base64.standard_b64encode(street_view_bytes).decode()
        final_content.append({"type": "text", "text": "Street-level view of the same property:"})
        final_content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg", "data": sv_b64},
        })

    try:
        resp = client.messages.create(
            model="claude-opus-4-8",
            max_tokens=4096,
            thinking={"type": "enabled", "budget_tokens": 1024},
            system=instruction,
            messages=[{"role": "user", "content": final_content}],
        )
        raw = ""
        think_excerpt = ""
        for block in resp.content:
            if block.type == "thinking":
                think_excerpt = (block.thinking or "")[:300]
            elif block.type == "text":
                raw = block.text

        text = raw.upper()
        is_house = "HOUSE=YES" in text
        has_solar = is_house and "SOLAR=YES" in text
        reasoning = think_excerpt
        for line in raw.splitlines():
            if line.strip().upper().startswith(("HOUSE=", "SOLAR=")):
                break
            if line.strip():
                reasoning = line.strip()
                break
        _log.debug(
            "_analyze_building_opus HOUSE=%s SOLAR=%s sv=%s",
            is_house, has_solar, bool(street_view_bytes),
        )
        return is_house, has_solar, False, reasoning
    except Exception as exc:
        _log.error("_analyze_building_opus error: %s", exc)
        return True, False, True, ""  # preserve as needs_review on API error


def _process_building(
    building: dict,
    google_key: str,
    anthropic_client: anthropic.Anthropic,
    mapbox_key: str | None = None,
    lm_key: str | None = None,
    lm_layer: str = _LM_LAYERS[0],
    few_shot: list[tuple[str, str]] | None = None,
    skip_tile_keys: frozenset[str] = frozenset(),
) -> Lead | None:
    lat, lng = building["lat"], building["lng"]
    zoom = building.get("zoom", ZOOM_BUILDING)
    tile_key = f"bld/{building['osm_id']}"
    if tile_key in skip_tile_keys:
        _log.debug("_process_building skip duplicate tile_key=%s", tile_key)
        return None
    if _is_existing_customer(lat, lng):
        _log.debug("_process_building skip existing Enspecta customer lat=%s lng=%s", lat, lng)
        return None
    img = _fetch_satellite(google_key, lat, lng, zoom=zoom, mapbox_key=mapbox_key, lm_key=lm_key, lm_layer=lm_layer)
    if img is None:
        return None
    is_house, has_solar, is_unsure, reasoning = _analyze_building(anthropic_client, img, few_shot=few_shot)
    if not is_house:
        return None

    # Second pass: if AI is unsure, use Opus 4.8 + extended thinking.
    # Fetches Street View first (south-facing slope evidence) then hands both
    # images to Opus which reasons deeply and commits to a definitive verdict.
    if is_unsure:
        sv_bytes: bytes | None = None
        if google_key:
            sv_bytes = _fetch_street_view(google_key, lat, lng)
        _log.debug(
            "_process_building: UNSURE → Opus 4.8 extended thinking lat=%s lng=%s sv=%s",
            lat, lng, bool(sv_bytes),
        )
        is_house, has_solar, is_unsure, reasoning = _analyze_building_opus(
            anthropic_client, img, street_view_bytes=sv_bytes
        )
        if not is_house:
            return None

    if is_unsure:
        # AI not certain — skip samtomt check (result unused for UNSURE), save for human review
        address = building["address"]
        if address and "," in address and not any(c.isalpha() for c in address):
            try:
                gmaps = googlemaps.Client(key=google_key)
                rev = gmaps.reverse_geocode((lat, lng))
                if rev:
                    address = rev[0].get("formatted_address", address)
            except Exception:
                pass
        return Lead(
            lat=lat,
            lng=lng,
            address=address,
            confidence=0.50,
            source="ai",
            tile_key=f"bld/{building['osm_id']}",
            building_type=building.get("building_type", ""),
            zoom=zoom,
            samtomt_solar_extra=False,
            solar_location="roof",
            needs_review=True,
            ai_reasoning=reasoning,
            image_url=lm_wms_url(lat, lng),
        )

    samtomt = _has_extra_solar_nearby(lat, lng)
    extra_solar = samtomt.get("extra_solar_found", False)
    villa_nearby = samtomt.get("villa_nearby", False)

    samtomt_solar_extra = False
    solar_location = "roof"

    if has_solar:
        if extra_solar:
            samtomt_solar_extra = True
    else:
        if extra_solar and villa_nearby:
            samtomt_solar_extra = True
            solar_location = "samtomt"
        else:
            return None

    address = building["address"]
    if address and "," in address and not any(c.isalpha() for c in address):
        try:
            gmaps = googlemaps.Client(key=google_key)
            rev = gmaps.reverse_geocode((lat, lng))
            if rev:
                address = rev[0].get("formatted_address", address)
        except Exception:
            pass
    return Lead(
        lat=lat,
        lng=lng,
        address=address,
        confidence=0.90,
        source="ai",
        tile_key=f"bld/{building['osm_id']}",
        building_type=building.get("building_type", ""),
        zoom=zoom,
        samtomt_solar_extra=samtomt_solar_extra,
        solar_location=solar_location,
        needs_review=False,
        ai_reasoning=reasoning,
        image_url=lm_wms_url(lat, lng),
    )


def scan_buildings_ai(
    buildings: list[dict],
    google_key: str,
    anthropic_key: str,
    on_progress: Callable[[int, int, "Lead | None"], None] | None = None,
    max_workers: int = 4,
    mapbox_key: str | None = None,
    lm_key: str | None = None,
    max_leads: int | None = None,
    few_shot: list[tuple[str, str]] | None = None,
    skip_tile_keys: frozenset[str] = frozenset(),
) -> tuple[list[Lead], ScanStats]:
    """Run Claude Vision on each OSM building centroid.

    Args:
        max_leads: Stop processing once this many confirmed leads are found.
                   None means no limit.
        few_shot: Pre-loaded few-shot examples as (b64_jpeg, verdict_text) pairs.
                  If None, examples are loaded from LM WMS on first call.
                  Pass a pre-loaded list to avoid redundant downloads when
                  scan_buildings_ai is called multiple times per scan (e.g.
                  once per residential area in scan_city).

    Returns:
        A tuple of (leads, stats) where stats tracks yes/unsure/no counts for
        all buildings processed by the AI.
    """
    stats = ScanStats()
    if not buildings or not anthropic_key:
        return [], stats

    client = anthropic.Anthropic(api_key=anthropic_key)
    leads: list[Lead] = []
    total = len(buildings)

    # Probe which LM layer works once up front to avoid per-tile probing
    lm_layer = _LM_LAYERS[0]
    if lm_key and _ENHANCE_AVAILABLE:
        probed = _probe_lm_layer(lm_key)
        if probed:
            lm_layer = probed
        else:
            lm_key = None

    # Load few-shot examples if not provided by caller.
    # Callers that invoke scan_buildings_ai multiple times per scan (scan_city)
    # should load once and pass the result here to avoid redundant WMS downloads.
    if few_shot is None:
        few_shot = _load_few_shot_images()

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(_process_building, b, google_key, client, mapbox_key, lm_key, lm_layer, few_shot, skip_tile_keys): b
            for b in buildings
        }
        done = 0
        for future in as_completed(futures):
            done += 1
            try:
                result = future.result(timeout=60)
            except concurrent.futures.TimeoutError:
                _log.warning("_process_building timed out after 60s, skipping")
                result = None
            except Exception:
                result = None
            if result is not None:
                leads.append(result)
                if result.needs_review:
                    stats.unsure += 1
                else:
                    stats.yes += 1
            else:
                stats.no += 1
            if on_progress:
                try:
                    on_progress(done, total, result)
                except Exception as _cb_err:
                    _log.warning("on_progress callback error: %s", _cb_err)
            # Short-circuit if we've hit the lead cap
            if max_leads is not None and len(leads) >= max_leads:
                # Cancel remaining futures that haven't started yet
                for f in futures:
                    f.cancel()
                break

    return leads, stats


# ── Merge & deduplicate ────────────────────────────────────────────────────────

def merge_leads(osm_leads: list[Lead], ai_leads: list[Lead], dedup_radius_m: int = 20) -> list[Lead]:
    """Merge OSM and AI leads, deduplicating by proximity.

    Two leads within dedup_radius_m metres of each other are treated as the
    same building. OSM leads take priority over AI leads (higher confidence).
    """
    merged: list[Lead] = []
    cos_lat_avg = math.cos(math.radians(
        sum(l.lat for l in osm_leads + ai_leads) /
        max(len(osm_leads) + len(ai_leads), 1)
    ))

    seen_keys: set[str] = set()

    def _too_close_cross_source(lead: Lead) -> bool:
        for existing in merged:
            if existing.source == lead.source:
                continue
            d_lat = (lead.lat - existing.lat) * 111_000
            d_lng = (lead.lng - existing.lng) * 111_000 * cos_lat_avg
            if math.sqrt(d_lat * d_lat + d_lng * d_lng) < dedup_radius_m:
                return True
        return False

    # OSM first (confidence=1.0), then AI — so OSM wins ties
    for lead in osm_leads + ai_leads:
        key = lead.tile_key or f"{lead.lat:.5f},{lead.lng:.5f}"
        if key in seen_keys:
            continue
        if _too_close_cross_source(lead):
            continue
        seen_keys.add(key)
        merged.append(lead)

    merged.sort(key=lambda l: (0 if l.source == "osm" else 1, -l.confidence))
    return merged


# ── Public entry points ────────────────────────────────────────────────────────

def _get_residential_areas(south: float, west: float, north: float, east: float) -> list[dict]:
    """
    Return OSM landuse=residential area centroids within the viewport, sorted by
    area (largest first — most houses = most leads).

    Each item: {lat, lng, area_deg2}
    """
    query = f"""
    [out:json][timeout:90];
    (
      way["landuse"="residential"]({south},{west},{north},{east});
      relation["landuse"="residential"]({south},{west},{north},{east});
    );
    out center;
    """
    elements = _overpass(query, timeout=90)
    areas: list[dict] = []
    for el in elements:
        if el.get("type") == "way" and "center" in el:
            lat = el["center"]["lat"]
            lng = el["center"]["lon"]
            # Estimate area from bounds if available (rough proxy for sorting)
            bounds = el.get("bounds", {})
            if bounds:
                area_deg2 = (
                    (bounds.get("maxlat", lat) - bounds.get("minlat", lat)) *
                    (bounds.get("maxlon", lng) - bounds.get("minlon", lng))
                )
                s = bounds.get("minlat", lat - 0.005)
                w = bounds.get("minlon", lng - 0.005)
                n = bounds.get("maxlat", lat + 0.005)
                e = bounds.get("maxlon", lng + 0.005)
            else:
                area_deg2 = 0.0
                s, w, n, e = lat - 0.005, lng - 0.005, lat + 0.005, lng + 0.005
            areas.append({"lat": lat, "lng": lng, "area_deg2": area_deg2,
                          "south": s, "west": w, "north": n, "east": e})
        elif el.get("type") == "relation" and "center" in el:
            lat = el["center"]["lat"]
            lng = el["center"]["lon"]
            areas.append({"lat": lat, "lng": lng, "area_deg2": 0.0,
                          "south": lat - 0.005, "west": lng - 0.005,
                          "north": lat + 0.005, "east": lng + 0.005})
    # Largest residential area first
    areas.sort(key=lambda a: a["area_deg2"], reverse=True)
    return areas


def scan_nearby_buildings(
    lat: float,
    lng: float,
    google_key: str,
    anthropic_key: str | None,
    exclude_tile_key: str = "",
    skip_tile_keys: frozenset[str] = frozenset(),
    radius_m: int = 60,
    mapbox_key: str | None = None,
) -> list[Lead]:
    """Scan buildings within radius_m metres of lat/lng for solar panels."""
    dlat = radius_m / 111_000
    dlng = radius_m / (111_000 * math.cos(math.radians(lat)))
    south, west = lat - dlat, lng - dlng
    north, east = lat + dlat, lng + dlng
    buildings = _get_osm_buildings(south, west, north, east, max_count=20)
    if not buildings or not anthropic_key:
        return []
    client = anthropic.Anthropic(api_key=anthropic_key)
    few_shot = _load_few_shot_images()
    all_skip = skip_tile_keys | ({exclude_tile_key} if exclude_tile_key else set())
    leads: list[Lead] = []
    for bld in buildings:
        tile_key = f"bld/{bld['osm_id']}"
        if tile_key in all_skip:
            continue
        lead = _process_building(
            bld, google_key, client,
            mapbox_key=mapbox_key,
            few_shot=few_shot,
            skip_tile_keys=all_skip,
        )
        if lead:
            leads.append(lead)
            all_skip = all_skip | {tile_key}
    return leads


def scan_city(
    city_name: str,
    google_key: str,
    anthropic_key: str | None,
    on_progress: Callable[[int, int, Lead | None], None] | None = None,
    lm_key: str | None = None,
    mapbox_key: str | None = None,
    max_leads: int | None = None,
    phase_callback: Callable[[str, int], None] | None = None,
    skip_tile_keys: frozenset[str] = frozenset(),
    user_id: str | None = None,
    on_area_start: Callable[[dict], None] | None = None,
    on_area_done: Callable[[dict, int], None] | None = None,
) -> tuple[list[Lead], ScanStats]:
    """Scan a city for buildings with solar panels.

    Args:
        max_leads: Stop scanning once this many confirmed leads are found.
                   None means no limit.

    Returns:
        A tuple of (leads, stats) where stats aggregates yes/unsure/no counts.
    """
    _log.info("scan_city city=%s max_leads=%s", city_name, max_leads)
    gmaps = googlemaps.Client(key=google_key)
    results = gmaps.geocode(city_name)
    if not results:
        raise ValueError(f"Hittade inte orten: {city_name}")

    geom     = results[0]["geometry"]
    viewport = geom["viewport"]
    south = viewport["southwest"]["lat"]
    west  = viewport["southwest"]["lng"]
    north = viewport["northeast"]["lat"]
    east  = viewport["northeast"]["lng"]
    center = geom["location"]
    _log.info("scan_city geocoded bbox=(%s,%s,%s,%s)", south, west, north, east)

    # OSM solar tags cover the full city viewport (free, instant)
    osm_leads = scan_area_osm(south, west, north, east)
    _log.info("scan_city osm_leads=%d", len(osm_leads))
    if phase_callback:
        phase_callback("osm_leads", len(osm_leads))

    if not anthropic_key:
        _log.info("scan_city no anthropic_key — returning OSM only")
        leads = osm_leads[:max_leads] if max_leads else osm_leads
        return leads, ScanStats()

    # Check if we've already hit max_leads from OSM alone
    if max_leads is not None and len(osm_leads) >= max_leads:
        _log.info("scan_city max_leads reached by OSM alone")
        return osm_leads[:max_leads], ScanStats()

    osm_keys = {f"{l.lat:.4f},{l.lng:.4f}" for l in osm_leads}
    all_ai_leads: list[Lead] = []
    merged_stats = ScanStats()
    seen_building_ids: set[str] = set()

    # Load few-shot examples once per full scan — shared across all residential
    # areas so LM WMS is not hit 6× per area (N_areas × 6 images otherwise).
    few_shot = _load_few_shot_images(user_id=user_id)
    _log.info("scan_city few_shot=%d examples loaded", len(few_shot))

    # Query landuse=residential polygons within the city viewport
    residential_areas = _get_residential_areas(south, west, north, east)
    _log.info("scan_city residential_areas=%d", len(residential_areas))

    if residential_areas:
        # Scan all areas — inner loop breaks early when max_leads is reached.
        # The old max_leads//5 cap caused 0 leads in large cities (e.g. Lund:
        # 465 areas but only 2 were scanned, missing all villa suburbs).
        max_areas = len(residential_areas)
        _log.info("scan_city scanning %d/%d areas", max_areas, len(residential_areas))
        for area in residential_areas[:max_areas]:
            # Remaining lead budget for AI scan
            remaining = None
            if max_leads is not None:
                already_found = len(osm_leads) + len(all_ai_leads)
                remaining = max_leads - already_found
                if remaining <= 0:
                    break

            if on_area_start:
                on_area_start(area)

            a_south, a_west, a_north, a_east = _center_bbox(
                area["lat"], area["lng"], radius_km=1.0
            )
            buildings = _get_osm_buildings(a_south, a_west, a_north, a_east)

            # Deduplicate across areas and against OSM leads
            buildings = [
                b for b in buildings
                if b["osm_id"] not in seen_building_ids
                and f"{b['lat']:.4f},{b['lng']:.4f}" not in osm_keys
            ]
            for b in buildings:
                seen_building_ids.add(b["osm_id"])

            if not buildings:
                _log.info("scan_city area lat=%s lng=%s no new buildings", area["lat"], area["lng"])
                if on_area_done:
                    on_area_done(area, 0)
                continue

            _log.info("scan_city area lat=%s lng=%s buildings=%d", area["lat"], area["lng"], len(buildings))
            if phase_callback:
                phase_callback("area_buildings", len(buildings))
            area_leads, area_stats = scan_buildings_ai(
                buildings, google_key, anthropic_key, on_progress,
                mapbox_key=mapbox_key, lm_key=lm_key,
                max_leads=remaining, few_shot=few_shot,
                skip_tile_keys=skip_tile_keys,
            )
            _log.info("scan_city area_leads=%d", len(area_leads))
            if on_area_done:
                on_area_done(area, len(area_leads))
            all_ai_leads.extend(area_leads)
            merged_stats.yes += area_stats.yes
            merged_stats.unsure += area_stats.unsure
            merged_stats.no += area_stats.no

            if max_leads is not None and len(osm_leads) + len(all_ai_leads) >= max_leads:
                break
    else:
        # Fallback: 1 km radius around city centre
        _log.info("scan_city no residential areas — fallback to 1km radius")
        ai_south, ai_west, ai_north, ai_east = _center_bbox(
            center["lat"], center["lng"], radius_km=1.0
        )
        buildings = _get_osm_buildings(ai_south, ai_west, ai_north, ai_east)
        buildings = [b for b in buildings
                     if f"{b['lat']:.4f},{b['lng']:.4f}" not in osm_keys]
        _log.info("scan_city fallback buildings=%d", len(buildings))

        remaining = None
        if max_leads is not None:
            remaining = max_leads - len(osm_leads)
        all_ai_leads, merged_stats = scan_buildings_ai(
            buildings, google_key, anthropic_key, on_progress,
            mapbox_key=mapbox_key, lm_key=lm_key,
            max_leads=remaining, few_shot=few_shot,
            skip_tile_keys=skip_tile_keys,
        )

    merged = merge_leads(osm_leads, all_ai_leads)
    _log.info("scan_city done city=%s total_leads=%d (osm=%d ai=%d)",
              city_name, len(merged), len(osm_leads), len(all_ai_leads))
    return merged, merged_stats


def scan_municipality(
    cities: list[str],
    google_key: str,
    anthropic_key: str | None,
    on_progress: Callable[[int, int, Lead | None], None] | None = None,
    on_city_done: Callable[[str, int, ScanStats], None] | None = None,
    lm_key: str | None = None,
    mapbox_key: str | None = None,
    max_leads_per_city: int | None = None,
    max_leads_total: int | None = None,
    skip_tile_keys: frozenset[str] = frozenset(),
    user_id: str | None = None,
) -> tuple[list[Lead], ScanStats]:
    """Scan multiple cities/municipalities sequentially to generate large lead batches.

    Designed for bulk runs (hundreds–thousands of leads across a region).
    Deduplicates across cities so the same building is never scanned twice.
    Calls on_city_done after each city so the caller can persist leads incrementally.

    Args:
        cities: List of city/municipality names to scan in order.
        on_city_done: Called after each city with (city_name, leads_found, stats).
        max_leads_per_city: Lead cap per city (None = unlimited).
        max_leads_total: Stop scanning new cities once this total is reached.
        skip_tile_keys: Tile keys already in the database — skip these buildings.

    Returns:
        Tuple of (all_leads, merged_stats).
    """
    all_leads: list[Lead] = []
    merged_stats = ScanStats()
    seen_tile_keys: set[str] = set(skip_tile_keys)

    for city in cities:
        if max_leads_total is not None and len(all_leads) >= max_leads_total:
            _log.info("scan_municipality max_leads_total=%d reached after %d leads", max_leads_total, len(all_leads))
            break

        remaining_total = None
        if max_leads_total is not None:
            remaining_total = max_leads_total - len(all_leads)

        per_city_cap = max_leads_per_city
        if remaining_total is not None:
            per_city_cap = min(per_city_cap, remaining_total) if per_city_cap else remaining_total

        _log.info("scan_municipality city=%s cap=%s", city, per_city_cap)
        try:
            city_leads, city_stats = scan_city(
                city, google_key, anthropic_key, on_progress,
                lm_key=lm_key, mapbox_key=mapbox_key,
                max_leads=per_city_cap,
                skip_tile_keys=frozenset(seen_tile_keys),
                user_id=user_id,
            )
        except Exception as exc:
            _log.warning("scan_municipality city=%s failed: %s", city, exc)
            if on_city_done:
                on_city_done(city, 0, ScanStats())
            continue

        for lead in city_leads:
            seen_tile_keys.add(lead.tile_key)

        all_leads.extend(city_leads)
        merged_stats.yes    += city_stats.yes
        merged_stats.unsure += city_stats.unsure
        merged_stats.no     += city_stats.no

        _log.info("scan_municipality city=%s leads=%d total_so_far=%d", city, len(city_leads), len(all_leads))
        if on_city_done:
            on_city_done(city, len(city_leads), city_stats)

    return all_leads, merged_stats

def scan_bbox(
    south: float,
    west: float,
    north: float,
    east: float,
    google_key: str,
    anthropic_key: str | None,
    on_progress: Callable[[int, int, Lead | None], None] | None = None,
    lm_key: str | None = None,
    mapbox_key: str | None = None,
    max_leads: int | None = None,
    phase_callback: Callable[[str, int], None] | None = None,
    skip_tile_keys: frozenset[str] = frozenset(),
    user_id: str | None = None,
    on_area_start: Callable[[dict], None] | None = None,
    on_area_done: Callable[[dict, int], None] | None = None,
) -> tuple[list[Lead], ScanStats]:
    """Scan a bounding box for buildings with solar panels.

    Returns:
        A tuple of (leads, stats) where stats aggregates yes/unsure/no counts.
    """
    tile_count = len(_bbox_tiles(south, west, north, east))

    # OSM solar tags: instant, works for any bbox size
    osm_leads = scan_area_osm(south, west, north, east)
    _log.info("scan_bbox tile_count=%d osm_leads=%d", tile_count, len(osm_leads))
    if phase_callback:
        phase_callback("osm_leads", len(osm_leads))

    if not anthropic_key:
        leads = osm_leads[:max_leads] if max_leads else osm_leads
        return leads, ScanStats()

    if max_leads is not None and len(osm_leads) >= max_leads:
        return osm_leads[:max_leads], ScanStats()

    osm_keys  = {f"{l.lat:.4f},{l.lng:.4f}" for l in osm_leads}
    few_shot  = _load_few_shot_images(user_id=user_id)
    all_ai_leads: list[Lead] = []
    merged_stats = ScanStats()

    if tile_count > 1000:
        # Large bbox: chunk by OSM residential polygons — same strategy as scan_city.
        # This lets users draw whole city districts or municipalities without limits.
        residential_areas = _get_residential_areas(south, west, north, east)
        _log.info("scan_bbox large area: residential_areas=%d", len(residential_areas))

        if residential_areas:
            seen_building_ids: set[str] = set()
            for area in residential_areas:
                remaining = None
                if max_leads is not None:
                    remaining = max_leads - len(osm_leads) - len(all_ai_leads)
                    if remaining <= 0:
                        break

                if on_area_start:
                    on_area_start(area)

                a_south, a_west, a_north, a_east = _center_bbox(
                    area["lat"], area["lng"], radius_km=1.0
                )
                # Clamp to drawn bbox so we don't spill outside the user's selection
                a_south = max(a_south, south)
                a_west  = max(a_west,  west)
                a_north = min(a_north, north)
                a_east  = min(a_east,  east)

                buildings = _get_osm_buildings(a_south, a_west, a_north, a_east)
                buildings = [
                    b for b in buildings
                    if b["osm_id"] not in seen_building_ids
                    and f"{b['lat']:.4f},{b['lng']:.4f}" not in osm_keys
                ]
                for b in buildings:
                    seen_building_ids.add(b["osm_id"])

                if not buildings:
                    if on_area_done:
                        on_area_done(area, 0)
                    continue

                if phase_callback:
                    phase_callback("area_buildings", len(buildings))
                area_leads, area_stats = scan_buildings_ai(
                    buildings, google_key, anthropic_key, on_progress,
                    mapbox_key=mapbox_key, lm_key=lm_key,
                    max_leads=remaining, few_shot=few_shot,
                    skip_tile_keys=skip_tile_keys,
                )
                if on_area_done:
                    on_area_done(area, len(area_leads))
                all_ai_leads.extend(area_leads)
                merged_stats.yes    += area_stats.yes
                merged_stats.unsure += area_stats.unsure
                merged_stats.no     += area_stats.no

                if max_leads is not None and len(osm_leads) + len(all_ai_leads) >= max_leads:
                    break
        else:
            # No residential polygons (rural/industrial): scan full bbox directly.
            buildings = _get_osm_buildings(south, west, north, east)
            buildings = [b for b in buildings
                         if f"{b['lat']:.4f},{b['lng']:.4f}" not in osm_keys]
            if phase_callback:
                phase_callback("buildings_found", len(buildings))
            remaining = (max_leads - len(osm_leads)) if max_leads is not None else None
            all_ai_leads, merged_stats = scan_buildings_ai(
                buildings, google_key, anthropic_key, on_progress,
                mapbox_key=mapbox_key, lm_key=lm_key,
                max_leads=remaining, few_shot=few_shot,
                skip_tile_keys=skip_tile_keys,
            )
    else:
        # Small bbox (≤ ~2 km²): direct scan, no chunking needed
        buildings = _get_osm_buildings(south, west, north, east)
        buildings = [b for b in buildings
                     if f"{b['lat']:.4f},{b['lng']:.4f}" not in osm_keys]
        if phase_callback:
            phase_callback("buildings_found", len(buildings))
        remaining = (max_leads - len(osm_leads)) if max_leads is not None else None
        all_ai_leads, merged_stats = scan_buildings_ai(
            buildings, google_key, anthropic_key, on_progress,
            mapbox_key=mapbox_key, lm_key=lm_key,
            max_leads=remaining, few_shot=few_shot,
            skip_tile_keys=skip_tile_keys,
        )

    if phase_callback:
        phase_callback("ai_done", len(all_ai_leads))
    return merge_leads(osm_leads, all_ai_leads), merged_stats
