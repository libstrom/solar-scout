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
import logging
import httpx
import googlemaps
import anthropic
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [scanner] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
_log = logging.getLogger("solar_scout")

try:
    from PIL import Image as _PILImage
    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False


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
    try:
        _log.info("Overpass query start (timeout=%ds)", timeout)
        resp = httpx.post(
            "https://overpass-api.de/api/interpreter",
            data={"data": query},
            timeout=timeout,
            headers={
                "User-Agent": "solar-scout/1.0 (https://github.com/libstrom/solar-scout)",
                "Accept": "application/json",
            },
        )
        resp.raise_for_status()
        elements = resp.json().get("elements", [])
        _log.info("Overpass returned %d elements", len(elements))
        return elements
    except Exception as exc:
        _log.error("Overpass error: %s", exc)
        return []


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
MAX_BUILDING_AREA_M2 = 400

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


def _lm_tile_url(token: str, layer: str, z: int, x: int, y: int) -> str:
    # WMTS order: TileMatrix / TileRow / TileCol  →  z / y / x
    return (
        f"https://api.lantmateriet.se/open/{_LM_SERVICE}/v1/wmts"
        f"/token/{token}/1.0.0/{layer}/default/3857/{z}/{y}/{x}.png"
    )


def _fetch_lantmateriet(lm_key: str, lat: float, lng: float, layer: str = _LM_LAYERS[0]) -> bytes | None:
    """Fetch 3×3 tile grid from Lantmäteriet ortofoto and return a 640×640 PNG."""
    if not _PIL_AVAILABLE:
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


def lm_wms_url(lat: float, lng: float, size_m: float = 80, width: int = 640, height: int = 480) -> str:
    """Return a directly clickable Lantmäteriet WMS URL for the given coordinates.

    Opens a 640x480 satellite image centred on (lat, lng) in a web browser --
    no API key required, free CC-BY ortofoto from Lantmäteriet minkarta.

    Args:
        lat: Latitude in decimal degrees (WGS-84).
        lng: Longitude in decimal degrees (WGS-84).
        size_m: Half-width of the bounding box in metres (default 80 m gives
                ~160 m x 120 m coverage at a 4:3 aspect ratio).
        width: Image pixel width (default 640).
        height: Image pixel height (default 480).

    Returns:
        A plain URL string that can be opened directly in a browser.
    """
    d_lat = (size_m / 2) / 111_000
    d_lng = d_lat / math.cos(math.radians(lat))
    # Adjust vertical half-span to match the requested aspect ratio
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


def _fetch_satellite(
    google_key: str,
    lat: float,
    lng: float,
    zoom: int = ZOOM_BUILDING,
    mapbox_key: str | None = None,
    lm_key: str | None = None,
    lm_layer: str = _LM_LAYERS[0],
) -> bytes | None:
    # Priority: Lantmäteriet WMS (free, best for Sweden) → Mapbox → Google
    img = _fetch_lm_wms(lat, lng)
    if img:
        _log.debug("_fetch_satellite source=lm_wms lat=%s lng=%s", lat, lng)
        return img
    if mapbox_key:
        img = _fetch_mapbox(mapbox_key, lat, lng, zoom)
        if img:
            _log.debug("_fetch_satellite source=mapbox lat=%s lng=%s", lat, lng)
            return img
    if lm_key and _PIL_AVAILABLE:
        img = _fetch_lantmateriet(lm_key, lat, lng, layer=lm_layer)
        if img:
            _log.debug("_fetch_satellite source=lm_tile lat=%s lng=%s", lat, lng)
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


def _analyze_building(client: anthropic.Anthropic, img_bytes: bytes) -> tuple[bool, bool, bool, str]:
    """
    Returns (is_residential_house, has_solar_panels, is_unsure, reasoning).

    is_unsure=True means SOLAR=UNSURE — AI sees possible panels but can't
    confirm. These become needs_review=True leads surfaced in the review queue.
    """
    b64 = base64.standard_b64encode(img_bytes).decode()
    try:
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=220,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": "image/jpeg", "data": b64},
                    },
                    {
                        "type": "text",
                        "text": (
                            "Swedish aerial orthophoto, ~50m wide, top-down view.\n\n"
                            "Look at the structure occupying the CENTRE of the image. Answer:\n\n"
                            "Q1 — Is the central structure a single-family residential home "
                            "(villa, parhus, radhus, fritidshus)? It must NOT be: carport, "
                            "parking shed, garage, barn, industrial building, warehouse, "
                            "church, school, kiosk, construction site, or bare ground.\n\n"
                            "Q2 — Only if Q1=YES: does its roof have photovoltaic solar panels?\n\n"
                            "Solar PV from directly above appears as:\n"
                            "- RECTANGULAR FLAT PATCHES on the roof that are SMOOTHER and more "
                            "UNIFORM than the bumpy texture of surrounding clay tiles or asphalt "
                            "shingles. This is the primary signal — even when grid lines between "
                            "individual modules are too thin to see in compressed imagery, the "
                            "smoothness contrast remains visible.\n"
                            "- Colour can vary with sun angle: dark blue, black, charcoal, "
                            "lighter blue-grey, brownish, or mirror-bright reflections.\n"
                            "- Installation may be a partial array (one or several rectangles "
                            "on part of the roof) OR cover an entire south-facing slope.\n"
                            "- Supporting signal when visible: regular grid lines / module seams.\n\n"
                            "First, in ONE sentence describe the roof: shape, texture, and any "
                            "rectangular patches or unusual smooth areas you notice.\n\n"
                            "Then commit to a verdict:\n"
                            "- SOLAR=YES   — clearly visible rectangular smooth panels, distinctly "
                            "different from surrounding roof, plausibly photovoltaic.\n"
                            "- SOLAR=UNSURE — something that COULD be panels but image quality, "
                            "shadow, or angle makes you uncertain. Use this instead of guessing.\n"
                            "- SOLAR=NO    — no panels visible, or surface is EPDM/felt/metal/"
                            "asphalt/skylights/ambiguous. Default when uncertain.\n\n"
                            "End with exactly two lines, nothing after:\n"
                            "HOUSE=YES or HOUSE=NO\n"
                            "SOLAR=YES or SOLAR=UNSURE or SOLAR=NO\n\n"
                            "If HOUSE=NO, set SOLAR=NO."
                        ),
                    },
                ],
            }],
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
        _log.debug("_analyze_building HOUSE=%s SOLAR=%s UNSURE=%s", is_house, has_solar, is_unsure)
        return is_house, has_solar, is_unsure, reasoning
    except Exception as exc:
        _log.error("_analyze_building API error: %s", exc)
        return False, False, False, ""


def _process_building(
    building: dict,
    google_key: str,
    anthropic_client: anthropic.Anthropic,
    mapbox_key: str | None = None,
    lm_key: str | None = None,
    lm_layer: str = _LM_LAYERS[0],
) -> Lead | None:
    lat, lng = building["lat"], building["lng"]
    zoom = building.get("zoom", ZOOM_BUILDING)
    img = _fetch_satellite(google_key, lat, lng, zoom=zoom, mapbox_key=mapbox_key, lm_key=lm_key, lm_layer=lm_layer)
    if img is None:
        return None
    is_house, has_solar, is_unsure, reasoning = _analyze_building(anthropic_client, img)
    if not is_house:
        return None

    samtomt = _has_extra_solar_nearby(lat, lng)
    extra_solar = samtomt.get("extra_solar_found", False)
    villa_nearby = samtomt.get("villa_nearby", False)

    samtomt_solar_extra = False
    solar_location = "roof"
    needs_review = False
    confidence = 0.90

    if has_solar:
        if extra_solar:
            samtomt_solar_extra = True
    elif is_unsure:
        # AI not certain — save for human review instead of dropping
        needs_review = True
        confidence = 0.50
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
        confidence=confidence,
        source="ai",
        tile_key=f"bld/{building['osm_id']}",
        building_type=building.get("building_type", ""),
        zoom=zoom,
        samtomt_solar_extra=samtomt_solar_extra,
        solar_location=solar_location,
        needs_review=needs_review,
        ai_reasoning=reasoning,
        image_url=lm_wms_url(lat, lng),
    )


def scan_buildings_ai(
    buildings: list[dict],
    google_key: str,
    anthropic_key: str,
    on_progress: Callable[[int, int, "Lead | None"], None] | None = None,
    max_workers: int = 8,
    mapbox_key: str | None = None,
    lm_key: str | None = None,
    max_leads: int | None = None,
) -> list[Lead]:
    """Run Claude Vision on each OSM building centroid.

    Args:
        max_leads: Stop processing once this many confirmed leads are found.
                   None means no limit.
    """
    if not buildings or not anthropic_key:
        return []

    client = anthropic.Anthropic(api_key=anthropic_key)
    leads: list[Lead] = []
    total = len(buildings)

    # Probe which LM layer works once up front to avoid per-tile probing
    lm_layer = _LM_LAYERS[0]
    if lm_key and _PIL_AVAILABLE:
        probed = _probe_lm_layer(lm_key)
        if probed:
            lm_layer = probed
        else:
            lm_key = None

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(_process_building, b, google_key, client, mapbox_key, lm_key, lm_layer): b
            for b in buildings
        }
        done = 0
        for future in as_completed(futures):
            done += 1
            try:
                result = future.result()
            except Exception:
                result = None
            if result:
                leads.append(result)
            if on_progress:
                on_progress(done, total, result)
            # Short-circuit if we've hit the lead cap
            if max_leads is not None and len(leads) >= max_leads:
                # Cancel remaining futures that haven't started yet
                for f in futures:
                    f.cancel()
                break

    return leads


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
            else:
                area_deg2 = 0.0
            areas.append({"lat": lat, "lng": lng, "area_deg2": area_deg2})
        elif el.get("type") == "relation" and "center" in el:
            lat = el["center"]["lat"]
            lng = el["center"]["lon"]
            areas.append({"lat": lat, "lng": lng, "area_deg2": 0.0})
    # Largest residential area first
    areas.sort(key=lambda a: a["area_deg2"], reverse=True)
    return areas


def scan_city(
    city_name: str,
    google_key: str,
    anthropic_key: str | None,
    on_progress: Callable[[int, int, Lead | None], None] | None = None,
    lm_key: str | None = None,
    mapbox_key: str | None = None,
    max_leads: int | None = None,
) -> list[Lead]:
    """Scan a city for buildings with solar panels.

    Args:
        max_leads: Stop scanning once this many confirmed leads are found.
                   None means no limit.
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

    if not anthropic_key:
        _log.info("scan_city no anthropic_key — returning OSM only")
        return osm_leads[:max_leads] if max_leads else osm_leads

    # Check if we've already hit max_leads from OSM alone
    if max_leads is not None and len(osm_leads) >= max_leads:
        _log.info("scan_city max_leads reached by OSM alone")
        return osm_leads[:max_leads]

    osm_keys = {f"{l.lat:.4f},{l.lng:.4f}" for l in osm_leads}
    all_ai_leads: list[Lead] = []
    seen_building_ids: set[str] = set()

    # Query landuse=residential polygons within the city viewport
    residential_areas = _get_residential_areas(south, west, north, east)
    _log.info("scan_city residential_areas=%d", len(residential_areas))

    if residential_areas:
        max_areas = max(1, (max_leads // 5)) if max_leads else len(residential_areas)
        _log.info("scan_city scanning %d/%d areas", max_areas, len(residential_areas))
        for area in residential_areas[:max_areas]:
            # Remaining lead budget for AI scan
            remaining = None
            if max_leads is not None:
                already_found = len(osm_leads) + len(all_ai_leads)
                remaining = max_leads - already_found
                if remaining <= 0:
                    break

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
                continue

            _log.info("scan_city area lat=%s lng=%s buildings=%d", area["lat"], area["lng"], len(buildings))
            area_leads = scan_buildings_ai(
                buildings, google_key, anthropic_key, on_progress,
                mapbox_key=mapbox_key, lm_key=lm_key,
                max_leads=remaining,
            )
            _log.info("scan_city area_leads=%d", len(area_leads))
            all_ai_leads.extend(area_leads)

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
        all_ai_leads = scan_buildings_ai(
            buildings, google_key, anthropic_key, on_progress,
            mapbox_key=mapbox_key, lm_key=lm_key,
            max_leads=remaining,
        )

    merged = merge_leads(osm_leads, all_ai_leads)
    _log.info("scan_city done city=%s total_leads=%d (osm=%d ai=%d)",
              city_name, len(merged), len(osm_leads), len(all_ai_leads))
    return merged


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
) -> list[Lead]:
    """Scan a bounding box for buildings with solar panels.

    Args:
        max_leads: Stop scanning once this many confirmed leads are found.
                   None means no limit.
    """
    tile_count = len(_bbox_tiles(south, west, north, east))
    if tile_count > 1000:
        raise ValueError(
            f"Området är för stort ({tile_count} brickor ≈ {tile_count * 107 // 1000:.1f} km²). "
            "Rita en mindre ruta — max ~2 km²."
        )

    osm_leads = scan_area_osm(south, west, north, east)

    if not anthropic_key:
        return osm_leads[:max_leads] if max_leads else osm_leads

    # Check if we've already hit max_leads from OSM alone
    if max_leads is not None and len(osm_leads) >= max_leads:
        return osm_leads[:max_leads]

    buildings = _get_osm_buildings(south, west, north, east)
    osm_keys  = {f"{l.lat:.4f},{l.lng:.4f}" for l in osm_leads}
    buildings = [b for b in buildings
                 if f"{b['lat']:.4f},{b['lng']:.4f}" not in osm_keys]

    remaining = None
    if max_leads is not None:
        remaining = max_leads - len(osm_leads)

    ai_leads = scan_buildings_ai(
        buildings, google_key, anthropic_key, on_progress,
        mapbox_key=mapbox_key, lm_key=lm_key,
        max_leads=remaining,
    )
    return merge_leads(osm_leads, ai_leads)
