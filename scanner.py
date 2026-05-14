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
import httpx
import googlemaps
import anthropic
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable

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
        resp = httpx.post(
            "https://overpass-api.de/api/interpreter",
            data={"data": query},
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json().get("elements", [])
    except Exception:
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
    query = f"""
    [out:json][timeout:60];
    (
      node["generator:source"="solar"]({south},{west},{north},{east});
      way["generator:source"="solar"]({south},{west},{north},{east});
      node["roof:solar_panel"="yes"]({south},{west},{north},{east});
      way["roof:solar_panel"="yes"]({south},{west},{north},{east});
      node["power"="generator"]["generator:source"="solar"]({south},{west},{north},{east});
      way["power"="generator"]["generator:source"="solar"]({south},{west},{north},{east});
    );
    out center;
    """
    elements = _overpass(query)
    leads = []
    seen: set[str] = set()
    for el in elements:
        if el["type"] == "node":
            lat, lng = el["lat"], el["lon"]
        elif el["type"] == "way" and "center" in el:
            lat, lng = el["center"]["lat"], el["center"]["lon"]
        else:
            continue
        key = f"{lat:.5f},{lng:.5f}"
        if key in seen:
            continue
        seen.add(key)
        addr = _tags_to_address(el.get("tags", {}))
        leads.append(Lead(lat=lat, lng=lng, address=addr or key,
                          confidence=1.0, source="osm", tile_key=key))
    return leads


_RESIDENTIAL_TYPES = (
    "yes|house|detached|residential|apartments|terrace|"
    "semidetached_house|bungalow|farm|dormitory|block_of_flats"
)


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
    Return building centroids with addresses from OSM.
    Only residential types. Each item: {lat, lng, address, osm_id, building_type, zoom}
    """
    query = f"""
    [out:json][timeout:90];
    (
      way["building"~"^({_RESIDENTIAL_TYPES})$"]({south},{west},{north},{east});
    );
    out bb center {max_count};
    """
    elements = _overpass(query, timeout=120)
    buildings = []
    for el in elements:
        if "center" not in el:
            continue
        lat = el["center"]["lat"]
        lng = el["center"]["lon"]
        tags = el.get("tags", {})
        addr = _tags_to_address(tags)
        osm_id = str(el.get("id", ""))
        btype = tags.get("building", "yes")
        zoom = _building_zoom(el["bounds"], lat) if "bounds" in el else ZOOM_BUILDING
        buildings.append({
            "lat": lat, "lng": lng, "address": addr,
            "osm_id": osm_id, "building_type": btype, "zoom": zoom,
        })
    return buildings


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


def _fetch_lm_wms(lat: float, lng: float, size_m: float = 50) -> bytes | None:
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
        return img
    if mapbox_key:
        img = _fetch_mapbox(mapbox_key, lat, lng, zoom)
        if img:
            return img
    if lm_key and _PIL_AVAILABLE:
        img = _fetch_lantmateriet(lm_key, lat, lng, layer=lm_layer)
        if img:
            return img
    url = (
        f"https://maps.googleapis.com/maps/api/staticmap"
        f"?center={lat},{lng}&zoom={zoom}&size=640x640"
        f"&maptype=satellite&key={google_key}"
    )
    try:
        resp = httpx.get(url, timeout=20)
        resp.raise_for_status()
        return resp.content
    except Exception:
        return None


def _analyze_building(client: anthropic.Anthropic, img_bytes: bytes) -> bool:
    """Return True only if THIS building clearly has PV solar panels on its roof."""
    b64 = base64.standard_b64encode(img_bytes).decode()
    try:
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=120,
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
                            "You are looking at a Swedish aerial orthophoto (top-down, ~50m wide) "
                            "centred on a residential building.\n\n"
                            "From directly above, PV solar panels appear as:\n"
                            "- Uniformly flat, very dark (dark blue/black/charcoal) rectangular patches\n"
                            "- Distinctly flatter and more uniform than surrounding roof tiles\n"
                            "- Arranged in a rectangular array on part of the roof\n"
                            "- Often slightly shinier or more reflective than the rest of the roof\n\n"
                            "Does any part of the central building's roof look like solar panels? "
                            "End your answer with exactly YES or NO."
                        ),
                    },
                ],
            }],
        )
        return msg.content[0].text.strip().lstrip("*_ ").upper().startswith("YES")
    except Exception:
        return False


def _reverse_geocode(gmaps: googlemaps.Client, lat: float, lng: float) -> str:
    try:
        results = gmaps.reverse_geocode((lat, lng))
        if results:
            return results[0].get("formatted_address", f"{lat:.5f}, {lng:.5f}")
    except Exception:
        pass
    return f"{lat:.5f}, {lng:.5f}"


def _process_building(
    building: dict,
    google_key: str,
    anthropic_client: anthropic.Anthropic,
    gmaps: googlemaps.Client,
    mapbox_key: str | None = None,
    lm_key: str | None = None,
    lm_layer: str = _LM_LAYERS[0],
) -> Lead | None:
    lat, lng = building["lat"], building["lng"]
    zoom = building.get("zoom", ZOOM_BUILDING)
    img = _fetch_satellite(google_key, lat, lng, zoom=zoom, mapbox_key=mapbox_key, lm_key=lm_key, lm_layer=lm_layer)
    if img is None:
        return None
    if not _analyze_building(anthropic_client, img):
        return None
    address = building["address"] or _reverse_geocode(gmaps, lat, lng)
    return Lead(
        lat=lat,
        lng=lng,
        address=address,
        confidence=0.90,
        source="ai",
        tile_key=f"bld/{building['osm_id']}",
        building_type=building.get("building_type", ""),
        zoom=zoom,
    )


def scan_buildings_ai(
    buildings: list[dict],
    google_key: str,
    anthropic_key: str,
    on_progress: Callable[[int, int, "Lead | None"], None] | None = None,
    max_workers: int = 8,
    mapbox_key: str | None = None,
    lm_key: str | None = None,
) -> list[Lead]:
    """Run Claude Vision on each OSM building centroid."""
    if not buildings or not anthropic_key:
        return []

    client = anthropic.Anthropic(api_key=anthropic_key)
    gmaps  = googlemaps.Client(key=google_key)
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
            pool.submit(_process_building, b, google_key, client, gmaps, mapbox_key, lm_key, lm_layer): b
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

    return leads


# ── Merge & deduplicate ────────────────────────────────────────────────────────

def merge_leads(osm_leads: list[Lead], ai_leads: list[Lead]) -> list[Lead]:
    seen: set[str] = set()
    merged: list[Lead] = []
    for lead in osm_leads + ai_leads:
        key = lead.tile_key or f"{lead.lat:.4f},{lead.lng:.4f}"
        if key not in seen:
            seen.add(key)
            merged.append(lead)
    merged.sort(key=lambda l: (0 if l.source == "osm" else 1, -l.confidence))
    return merged


# ── Public entry points ────────────────────────────────────────────────────────

def scan_city(
    city_name: str,
    google_key: str,
    anthropic_key: str | None,
    on_progress: Callable[[int, int, Lead | None], None] | None = None,
    lm_key: str | None = None,
    mapbox_key: str | None = None,
) -> list[Lead]:
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

    # OSM solar tags cover the full city viewport (free, instant)
    osm_leads = scan_area_osm(south, west, north, east)

    if not anthropic_key:
        return osm_leads

    # AI building scan: 1 km radius around city centre
    ai_south, ai_west, ai_north, ai_east = _center_bbox(
        center["lat"], center["lng"], radius_km=1.0
    )
    buildings = _get_osm_buildings(ai_south, ai_west, ai_north, ai_east)

    # Skip buildings already confirmed by OSM solar tags
    osm_keys = {f"{l.lat:.4f},{l.lng:.4f}" for l in osm_leads}
    buildings = [b for b in buildings
                 if f"{b['lat']:.4f},{b['lng']:.4f}" not in osm_keys]

    ai_leads = scan_buildings_ai(buildings, google_key, anthropic_key, on_progress, mapbox_key=mapbox_key, lm_key=lm_key)
    return merge_leads(osm_leads, ai_leads)


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
) -> list[Lead]:
    tile_count = len(_bbox_tiles(south, west, north, east))
    if tile_count > 1000:
        raise ValueError(
            f"Området är för stort ({tile_count} brickor ≈ {tile_count * 107 // 1000:.1f} km²). "
            "Rita en mindre ruta — max ~2 km²."
        )

    osm_leads = scan_area_osm(south, west, north, east)

    if not anthropic_key:
        return osm_leads

    buildings = _get_osm_buildings(south, west, north, east)
    osm_keys  = {f"{l.lat:.4f},{l.lng:.4f}" for l in osm_leads}
    buildings = [b for b in buildings
                 if f"{b['lat']:.4f},{b['lng']:.4f}" not in osm_keys]

    ai_leads = scan_buildings_ai(buildings, google_key, anthropic_key, on_progress, mapbox_key=mapbox_key, lm_key=lm_key)
    return merge_leads(osm_leads, ai_leads)
