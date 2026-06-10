"""Enspecta Solar Lead Machine -- Harvester.

Overnight scan: walks a 20m grid over the configured area, queries the
Google Solar API for buildings, downloads a 600x600 satellite crop for
each unique building, reverse-geocodes the address, and stores the lot
in SQLite. Computed export-time fields (Plus Code, Estimated Fuse, links)
are NOT persisted -- they're derived in app.py.

A live progress bar with ETA and cost-so-far renders in the terminal,
and the same numbers are written to the scan_runs table every couple of
seconds so the Streamlit app can mirror the progress while you watch.

Run:
    python harvester.py                              # default area Kågeröd, cap 200
    python harvester.py --max-buildings 500
    python harvester.py --town Svalöv
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import sys
import time
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

import requests
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import db as shared_db

load_dotenv()

# ---- Config ----------------------------------------------------------------

GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY", "").strip()

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
IMAGES_DIR = DATA_DIR / "images"
DB_PATH = DATA_DIR / "leads.db"

GRID_SPACING_M = 20
STATIC_MAP_SIZE = 600
STATIC_MAP_ZOOM = 20

# (south, west, north, east). Verify on a map before unleashing the full grid.
BBOXES = {
    "Kågeröd": (55.985, 13.065, 56.005, 13.105),
    "Svalöv":  (55.900, 13.090, 55.920, 13.135),
}

# Per-call USD costs (approximate; for the cost preview and live meter).
COST_SOLAR_API = 0.01
COST_STATIC_MAP = 0.002
COST_GEOCODE = 0.005

DEFAULT_MAX_BUILDINGS = 200

SOLAR_ENDPOINT = "https://solar.googleapis.com/v1/buildingInsights:findClosest"
STATIC_ENDPOINT = "https://maps.googleapis.com/maps/api/staticmap"
GEOCODE_ENDPOINT = "https://maps.googleapis.com/maps/api/geocode/json"


# ---- HTTP session with retries ---------------------------------------------

def _build_session() -> requests.Session:
    s = requests.Session()
    retry = Retry(
        total=4,
        backoff_factor=1.5,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=["GET"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


SESSION = _build_session()


# ---- Database ----------------------------------------------------------------
# Connection helpers stay local (and read the module-level DB_PATH) so tests
# can repoint DB_PATH/DATA_DIR/IMAGES_DIR at a temp dir. The schema itself is
# owned by db.py and shared with app.py / prescreen.py.

@contextmanager
def db() -> Iterator[sqlite3.Connection]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    with db() as c:
        c.executescript(shared_db.SCHEMA)
        for stmt in shared_db.LEADS_MIGRATIONS:
            try:
                c.execute(stmt)
            except sqlite3.OperationalError:
                pass  # column already exists


def lead_exists(place_id: str) -> bool:
    with db() as c:
        return c.execute(
            "SELECT 1 FROM leads WHERE place_id = ?", (place_id,)
        ).fetchone() is not None


def insert_lead(row: dict) -> None:
    with db() as c:
        c.execute(
            """
            INSERT INTO leads (
                place_id, address, lat, lng, coordinates,
                solar_confidence, roof_area_m2, image_path,
                status, raw_solar_data, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["place_id"], row["address"], row["lat"], row["lng"],
                row["coordinates"], row["solar_confidence"],
                row["roof_area_m2"], row["image_path"],
                "pending", json.dumps(row["raw"]),
                datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds"),
            ),
        )


# ---- scan_runs (live progress for the Streamlit app) -------------------------

def _scan_run_start(town: str, grid_total: int, max_buildings: int) -> int:
    with db() as c:
        cur = c.execute(
            """
            INSERT INTO scan_runs (town, status, grid_total, max_buildings, started_at)
            VALUES (?, 'running', ?, ?, ?)
            """,
            (town, grid_total, max_buildings, shared_db.utcnow()),
        )
        return cur.lastrowid


def _scan_run_update(run_id: int, grid_done: int, new_leads: int, skipped: int,
                     errors: int, cost_usd: float, eta_seconds: Optional[float]) -> None:
    with db() as c:
        c.execute(
            """
            UPDATE scan_runs
            SET grid_done = ?, new_leads = ?, skipped = ?, errors = ?,
                cost_usd = ?, eta_seconds = ?, updated_at = ?
            WHERE id = ?
            """,
            (grid_done, new_leads, skipped, errors, cost_usd, eta_seconds,
             shared_db.utcnow(), run_id),
        )


def _scan_run_finish(run_id: int, status: str) -> None:
    with db() as c:
        c.execute(
            "UPDATE scan_runs SET status = ?, eta_seconds = 0, finished_at = ? WHERE id = ?",
            (status, shared_db.utcnow(), run_id),
        )


# ---- Grid ------------------------------------------------------------------

def metres_to_degrees(metres: float, at_lat: float) -> tuple[float, float]:
    d_lat = metres / 111_320.0
    d_lng = metres / (111_320.0 * math.cos(math.radians(at_lat)))
    return d_lat, d_lng


def grid_points(bbox: tuple[float, float, float, float], spacing_m: float) -> Iterator[tuple[float, float]]:
    south, west, north, east = bbox
    d_lat, d_lng = metres_to_degrees(spacing_m, (south + north) / 2)
    lat = south
    while lat <= north:
        lng = west
        while lng <= east:
            yield lat, lng
            lng += d_lng
        lat += d_lat


def grid_size(bbox, spacing_m) -> int:
    south, west, north, east = bbox
    d_lat, d_lng = metres_to_degrees(spacing_m, (south + north) / 2)
    return (int((north - south) / d_lat) + 1) * (int((east - west) / d_lng) + 1)


# ---- Progress bar with ETA ---------------------------------------------------

def format_eta(seconds: Optional[float]) -> str:
    """Render seconds as H:MM:SS / MM:SS. None -> '--:--'."""
    if seconds is None or seconds != seconds or seconds < 0 or math.isinf(seconds):
        return "--:--"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


class Progress:
    """Single-line terminal progress bar + ETA, mirrored to scan_runs.

    ETA is the *sooner* of two finish conditions: the grid running out, or
    the --max-buildings cap being hit at the current find rate. Rates come
    from a sliding window so they track the recent pace, not the average
    since start.
    """

    BAR_WIDTH = 26
    RENDER_EVERY_S = 0.5
    DB_EVERY_S = 2.0
    WINDOW = 300  # samples

    def __init__(self, grid_total: int, max_buildings: int, run_id: Optional[int]):
        self.grid_total = max(grid_total, 1)
        self.max_buildings = max_buildings
        self.run_id = run_id
        self.done = 0
        self.found = 0
        self.skipped = 0
        self.errors = 0
        self.cost = 0.0
        self._samples: deque[tuple[float, int, int]] = deque(maxlen=self.WINDOW)
        self._last_render = 0.0
        self._last_db = 0.0
        self._line_len = 0

    def add_cost(self, usd: float) -> None:
        self.cost += usd

    def eta_seconds(self) -> Optional[float]:
        if len(self._samples) < 2:
            return None
        t0, d0, f0 = self._samples[0]
        t1, d1, f1 = self._samples[-1]
        dt = t1 - t0
        if dt <= 0:
            return None
        grid_rate = (d1 - d0) / dt
        candidates = []
        if grid_rate > 0:
            candidates.append((self.grid_total - self.done) / grid_rate)
        find_rate = (f1 - f0) / dt
        if find_rate > 0:
            candidates.append((self.max_buildings - self.found) / find_rate)
        if not candidates:
            return None
        return max(0.0, min(candidates))

    def tick(self, *, found: bool = False, skipped: bool = False, error: bool = False) -> None:
        self.done += 1
        if found:
            self.found += 1
        if skipped:
            self.skipped += 1
        if error:
            self.errors += 1
        now = time.monotonic()
        self._samples.append((now, self.done, self.found))
        if now - self._last_render >= self.RENDER_EVERY_S:
            self._render()
            self._last_render = now
        if self.run_id is not None and now - self._last_db >= self.DB_EVERY_S:
            self._push_db()
            self._last_db = now

    def _rate(self) -> Optional[float]:
        if len(self._samples) < 2:
            return None
        t0, d0, _ = self._samples[0]
        t1, d1, _ = self._samples[-1]
        if t1 <= t0:
            return None
        return (d1 - d0) / (t1 - t0)

    def _render(self) -> None:
        pct = self.done / self.grid_total
        filled = int(pct * self.BAR_WIDTH)
        bar = "#" * filled + "-" * (self.BAR_WIDTH - filled)
        rate = self._rate()
        rate_s = f"{rate:.1f} pkt/s" if rate else "-- pkt/s"
        line = (
            f"[{bar}] {pct:6.1%} | {self.done:,}/{self.grid_total:,} pkt"
            f" | {self.found}/{self.max_buildings} leads"
            f" | ${self.cost:.2f} | {rate_s} | ETA {format_eta(self.eta_seconds())}"
        )
        pad = " " * max(0, self._line_len - len(line))
        sys.stdout.write("\r" + line + pad)
        sys.stdout.flush()
        self._line_len = len(line)

    def _push_db(self) -> None:
        _scan_run_update(self.run_id, self.done, self.found, self.skipped,
                         self.errors, round(self.cost, 4), self.eta_seconds())

    def log(self, msg: str) -> None:
        """Print a normal line without corrupting the progress bar."""
        sys.stdout.write("\r" + " " * self._line_len + "\r")
        print(msg)
        self._line_len = 0
        self._render()

    def close(self) -> None:
        self._render()
        sys.stdout.write("\n")
        sys.stdout.flush()
        if self.run_id is not None:
            self._push_db()


# ---- Google API clients ----------------------------------------------------

class GoogleApiError(Exception):
    pass


def solar_find_closest(lat: float, lng: float) -> Optional[dict]:
    """Return the raw Solar API JSON or None if no building / no coverage."""
    if not GOOGLE_MAPS_API_KEY:
        raise GoogleApiError("GOOGLE_MAPS_API_KEY not set; check .env")
    params = {
        "location.latitude": f"{lat:.7f}",
        "location.longitude": f"{lng:.7f}",
        "requiredQuality": "LOW",
        "key": GOOGLE_MAPS_API_KEY,
    }
    try:
        r = SESSION.get(SOLAR_ENDPOINT, params=params, timeout=15)
    except requests.exceptions.RequestException as e:
        raise GoogleApiError(f"network error: {e}") from e
    if r.status_code == 404:
        return None
    if r.status_code != 200:
        raise GoogleApiError(f"Solar API HTTP {r.status_code}: {r.text[:300]}")
    data = r.json()
    return data if data.get("name") else None


def fetch_satellite_crop(lat: float, lng: float, out_path: Path) -> None:
    params = {
        "center": f"{lat:.7f},{lng:.7f}",
        "zoom": str(STATIC_MAP_ZOOM),
        "size": f"{STATIC_MAP_SIZE}x{STATIC_MAP_SIZE}",
        "maptype": "satellite",
        "scale": "1",
        "format": "png",
        "key": GOOGLE_MAPS_API_KEY,
    }
    try:
        r = SESSION.get(STATIC_ENDPOINT, params=params, timeout=15)
    except requests.exceptions.RequestException as e:
        raise GoogleApiError(f"static-maps network error: {e}") from e
    if r.status_code != 200:
        raise GoogleApiError(f"Static Maps HTTP {r.status_code}: {r.text[:300]}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(r.content)


def reverse_geocode(lat: float, lng: float) -> Optional[str]:
    try:
        r = SESSION.get(
            GEOCODE_ENDPOINT,
            params={"latlng": f"{lat},{lng}", "key": GOOGLE_MAPS_API_KEY},
            timeout=15,
        )
    except requests.exceptions.RequestException:
        return None
    if r.status_code != 200:
        return None
    results = r.json().get("results") or []
    return results[0].get("formatted_address") if results else None


# ---- Harvest loop ----------------------------------------------------------

def cost_preview(grid_pts: int, max_buildings: int) -> None:
    bounded = min(grid_pts, max_buildings)
    s = grid_pts * COST_SOLAR_API
    m = bounded * COST_STATIC_MAP
    g = bounded * COST_GEOCODE
    print(f"  Grid points (~{GRID_SPACING_M}m): {grid_pts:,}")
    print(f"  Max NEW buildings:    {max_buildings}")
    print(f"  Solar API (max):      {grid_pts:,}  ~${s:.2f}")
    print(f"  Static Maps:          {bounded:,}  ~${m:.2f}")
    print(f"  Geocode:              {bounded:,}  ~${g:.2f}")
    print(f"  TOTAL (worst case):   ~${s+m+g:.2f}")


def harvest(town: str, max_buildings: int) -> None:
    if not GOOGLE_MAPS_API_KEY:
        print("ERROR: GOOGLE_MAPS_API_KEY missing in .env", file=sys.stderr)
        sys.exit(2)
    if town not in BBOXES:
        print(f"ERROR: unknown town {town!r}. Known: {list(BBOXES)}", file=sys.stderr)
        sys.exit(2)

    init_db()
    bbox = BBOXES[town]
    total_pts = grid_size(bbox, GRID_SPACING_M)
    print("=" * 60)
    print(f"Enspecta Solar Lead Machine -- harvester")
    print(f"Area: {town}  bbox={bbox}")
    cost_preview(total_pts, max_buildings)
    print("=" * 60)

    run_id = _scan_run_start(town, total_pts, max_buildings)
    progress = Progress(total_pts, max_buildings, run_id)
    final_status = "done"

    try:
        for lat, lng in grid_points(bbox, GRID_SPACING_M):
            if progress.found >= max_buildings:
                progress.log(f"Hit --max-buildings={max_buildings}; stopping.")
                break
            try:
                progress.add_cost(COST_SOLAR_API)
                data = solar_find_closest(lat, lng)
            except GoogleApiError as e:
                msg = str(e)
                progress.log(f"  Solar API err at ({lat:.5f},{lng:.5f}): {msg}")
                if "403" in msg or "429" in msg:
                    progress.log("  Auth/rate-limit -- aborting.")
                    final_status = "error"
                    break
                progress.tick(error=True)
                continue
            if not data:
                progress.tick()
                continue
            place_id = data["name"]
            if lead_exists(place_id):
                progress.tick(skipped=True)
                continue

            center = data.get("center") or {}
            c_lat = float(center.get("latitude", lat))
            c_lng = float(center.get("longitude", lng))
            sp = data.get("solarPotential") or {}
            whole_roof = sp.get("wholeRoofStats") or {}
            roof_area = whole_roof.get("areaMeters2")
            confidence = data.get("imageryQuality")  # 'HIGH'|'MEDIUM'|'LOW'

            safe_id = place_id.replace("/", "_")
            img_path = IMAGES_DIR / f"{safe_id}.png"
            try:
                progress.add_cost(COST_STATIC_MAP)
                fetch_satellite_crop(c_lat, c_lng, img_path)
            except GoogleApiError as e:
                progress.log(f"  Static map fail for {place_id}: {e}")
                progress.tick(error=True)
                continue

            progress.add_cost(COST_GEOCODE)
            address = reverse_geocode(c_lat, c_lng)

            insert_lead({
                "place_id": place_id,
                "address": address,
                "lat": c_lat,
                "lng": c_lng,
                "coordinates": f"{c_lat:.6f},{c_lng:.6f}",
                "solar_confidence": confidence,
                "roof_area_m2": float(roof_area) if roof_area else None,
                "image_path": str(img_path),
                "raw": data,
            })
            progress.tick(found=True)
            progress.log(
                f"  + [{progress.found:>3}/{max_buildings}] {place_id}  "
                f"area={roof_area or '?'}m²  q={confidence}  {address or '(no address)'}"
            )
    except KeyboardInterrupt:
        final_status = "aborted"
        progress.log("Avbruten (Ctrl+C) -- läget är sparat, kör igen för att fortsätta.")
    finally:
        progress.close()
        _scan_run_finish(run_id, final_status)

    print(f"\nDone ({final_status}). New: {progress.found}  "
          f"Skipped (already in DB): {progress.skipped}  Errors: {progress.errors}  "
          f"Cost: ~${progress.cost:.2f}")
    print(f"Next: `python prescreen.py` to AI-grade the roofs, then `streamlit run app.py`.")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Enspecta Solar Lead Machine -- harvester")
    p.add_argument("--town", default="Kågeröd", choices=list(BBOXES.keys()))
    p.add_argument("--max-buildings", type=int, default=DEFAULT_MAX_BUILDINGS,
                   help=f"Cap NEW buildings per run (default {DEFAULT_MAX_BUILDINGS})")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    harvest(args.town, args.max_buildings)
