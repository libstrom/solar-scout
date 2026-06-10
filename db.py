"""Solar Scout -- shared DB layer.

Single SQLite file (data/leads.db) used by harvester.py, prescreen.py
and app.py. Owns the schema, lightweight column migrations, and the
scan_runs progress table that the Streamlit UI reads to render a live
progress bar + ETA while a harvest is running.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
IMAGES_DIR = DATA_DIR / "images"
DB_PATH = DATA_DIR / "leads.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    place_id TEXT UNIQUE NOT NULL,
    address TEXT,
    lat REAL NOT NULL,
    lng REAL NOT NULL,
    coordinates TEXT,
    solar_confidence TEXT,
    roof_area_m2 REAL,
    image_path TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    raw_solar_data TEXT,
    created_at TEXT NOT NULL,
    verified_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status);

CREATE TABLE IF NOT EXISTS scan_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    town TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    grid_total INTEGER NOT NULL,
    grid_done INTEGER NOT NULL DEFAULT 0,
    new_leads INTEGER NOT NULL DEFAULT 0,
    skipped INTEGER NOT NULL DEFAULT 0,
    errors INTEGER NOT NULL DEFAULT 0,
    max_buildings INTEGER,
    cost_usd REAL NOT NULL DEFAULT 0,
    eta_seconds REAL,
    started_at TEXT NOT NULL,
    updated_at TEXT,
    finished_at TEXT
);
"""

# Columns added after the original schema shipped. ALTER TABLE ADD COLUMN
# is idempotent-by-try: existing DBs gain them on first ensure_schema().
LEADS_MIGRATIONS = [
    "ALTER TABLE leads ADD COLUMN ai_score INTEGER",
    "ALTER TABLE leads ADD COLUMN ai_has_panels INTEGER",
    "ALTER TABLE leads ADD COLUMN ai_reason TEXT",
    "ALTER TABLE leads ADD COLUMN phone TEXT",
    "ALTER TABLE leads ADD COLUMN owner_name TEXT",
    "ALTER TABLE leads ADD COLUMN call_status TEXT",
    "ALTER TABLE leads ADD COLUMN call_notes TEXT",
]


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


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


def ensure_schema() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    with db() as c:
        c.executescript(SCHEMA)
        for stmt in LEADS_MIGRATIONS:
            try:
                c.execute(stmt)
            except sqlite3.OperationalError:
                pass  # column already exists


# ---- scan_runs ---------------------------------------------------------------

def start_scan_run(town: str, grid_total: int, max_buildings: int) -> int:
    with db() as c:
        cur = c.execute(
            """
            INSERT INTO scan_runs (town, status, grid_total, max_buildings, started_at)
            VALUES (?, 'running', ?, ?, ?)
            """,
            (town, grid_total, max_buildings, utcnow()),
        )
        return cur.lastrowid


def update_scan_run(run_id: int, *, grid_done: int, new_leads: int, skipped: int,
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
             utcnow(), run_id),
        )


def finish_scan_run(run_id: int, status: str) -> None:
    if status not in ("done", "aborted", "error"):
        raise ValueError(status)
    with db() as c:
        c.execute(
            "UPDATE scan_runs SET status = ?, eta_seconds = 0, finished_at = ? WHERE id = ?",
            (status, utcnow(), run_id),
        )


def latest_scan_run() -> Optional[sqlite3.Row]:
    if not DB_PATH.exists():
        return None
    with db() as c:
        try:
            return c.execute(
                "SELECT * FROM scan_runs ORDER BY id DESC LIMIT 1"
            ).fetchone()
        except sqlite3.OperationalError:
            return None  # table not created yet
