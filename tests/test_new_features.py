"""Tests for the new modules: db.py, harvester.py (ETA/Progress), prescreen.py.

The real data/leads.db is never opened:

* All db.py tests monkeypatch db.DB_PATH / db.DATA_DIR / db.IMAGES_DIR to
  tmp_path before any DB function is called (the helpers read the module
  globals at call time, so setattr on the module works).
* Progress is created with run_id=None so nothing is mirrored to scan_runs,
  and tick() (which renders to stdout) is never called -- fields and the
  _samples deque are set directly.
* prescreen tests only touch the pure helpers parse_verdict / pick_backend.
  app.py is never imported.
"""
from __future__ import annotations

import sqlite3
import sys
from collections import deque
from pathlib import Path

import pytest

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

import db as dbmod  # noqa: E402
import harvester  # noqa: E402
import prescreen  # noqa: E402


# ---- db.py -------------------------------------------------------------------

@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Repoint db.py's module-level paths at a temp dir."""
    data_dir = tmp_path / "data"
    monkeypatch.setattr(dbmod, "DATA_DIR", data_dir)
    monkeypatch.setattr(dbmod, "IMAGES_DIR", data_dir / "images")
    monkeypatch.setattr(dbmod, "DB_PATH", data_dir / "leads.db")
    return data_dir / "leads.db"


def _table_names(db_path):
    with sqlite3.connect(db_path) as c:
        rows = c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    return {r[0] for r in rows}


def _lead_columns(db_path):
    with sqlite3.connect(db_path) as c:
        return {r[1] for r in c.execute("PRAGMA table_info(leads)").fetchall()}


class TestEnsureSchema:
    def test_creates_tables(self, tmp_db):
        dbmod.ensure_schema()
        assert tmp_db.exists()
        names = _table_names(tmp_db)
        assert "leads" in names
        assert "scan_runs" in names

    def test_idempotent(self, tmp_db):
        dbmod.ensure_schema()
        dbmod.ensure_schema()  # must not raise on existing tables/columns
        assert "leads" in _table_names(tmp_db)

    def test_migration_columns_present(self, tmp_db):
        dbmod.ensure_schema()
        cols = _lead_columns(tmp_db)
        for col in ("ai_score", "ai_has_panels", "ai_reason",
                    "phone", "owner_name", "call_status", "call_notes"):
            assert col in cols, f"missing migrated column {col}"

    def test_migrations_applied_to_pre_existing_db(self, tmp_db):
        # Simulate an old DB created before the migrations shipped.
        tmp_db.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(tmp_db) as c:
            c.execute(
                "CREATE TABLE leads (id INTEGER PRIMARY KEY, "
                "place_id TEXT UNIQUE NOT NULL, lat REAL NOT NULL, "
                "lng REAL NOT NULL, status TEXT NOT NULL DEFAULT 'pending', "
                "created_at TEXT NOT NULL)"
            )
        dbmod.ensure_schema()
        assert "ai_score" in _lead_columns(tmp_db)

    def test_creates_images_dir(self, tmp_db):
        dbmod.ensure_schema()
        assert dbmod.IMAGES_DIR.is_dir()


class TestScanRuns:
    def test_start_update_finish_flow(self, tmp_db):
        dbmod.ensure_schema()
        run_id = dbmod.start_scan_run("Kågeröd", grid_total=100, max_buildings=10)
        assert isinstance(run_id, int)

        row = dbmod.latest_scan_run()
        assert row["id"] == run_id
        assert row["town"] == "Kågeröd"
        assert row["status"] == "running"
        assert row["grid_total"] == 100
        assert row["max_buildings"] == 10
        assert row["grid_done"] == 0
        assert row["started_at"]
        assert row["finished_at"] is None

        dbmod.update_scan_run(run_id, grid_done=50, new_leads=5, skipped=2,
                              errors=1, cost_usd=0.55, eta_seconds=12.5)
        row = dbmod.latest_scan_run()
        assert row["grid_done"] == 50
        assert row["new_leads"] == 5
        assert row["skipped"] == 2
        assert row["errors"] == 1
        assert row["cost_usd"] == pytest.approx(0.55)
        assert row["eta_seconds"] == pytest.approx(12.5)
        assert row["updated_at"]

        dbmod.finish_scan_run(run_id, "done")
        row = dbmod.latest_scan_run()
        assert row["status"] == "done"
        assert row["eta_seconds"] == 0
        assert row["finished_at"]

    @pytest.mark.parametrize("status", ["done", "aborted", "error"])
    def test_finish_accepts_valid_statuses(self, tmp_db, status):
        dbmod.ensure_schema()
        run_id = dbmod.start_scan_run("Svalöv", 10, 5)
        dbmod.finish_scan_run(run_id, status)
        assert dbmod.latest_scan_run()["status"] == status

    def test_finish_invalid_status_raises(self, tmp_db):
        dbmod.ensure_schema()
        run_id = dbmod.start_scan_run("Svalöv", 10, 5)
        with pytest.raises(ValueError):
            dbmod.finish_scan_run(run_id, "banana")
        # and the row is untouched
        assert dbmod.latest_scan_run()["status"] == "running"

    def test_latest_returns_most_recent(self, tmp_db):
        dbmod.ensure_schema()
        dbmod.start_scan_run("Kågeröd", 100, 10)
        second = dbmod.start_scan_run("Svalöv", 200, 20)
        row = dbmod.latest_scan_run()
        assert row["id"] == second
        assert row["town"] == "Svalöv"

    def test_latest_none_when_db_missing(self, tmp_db):
        assert not tmp_db.exists()
        assert dbmod.latest_scan_run() is None

    def test_latest_none_when_table_missing(self, tmp_db):
        # DB file exists but scan_runs was never created.
        tmp_db.parent.mkdir(parents=True, exist_ok=True)
        sqlite3.connect(tmp_db).close()
        assert dbmod.latest_scan_run() is None


# ---- harvester.format_eta ------------------------------------------------------

class TestFormatEta:
    @pytest.mark.parametrize("seconds, expected", [
        (None, "--:--"),
        (0, "00:00"),
        (65, "01:05"),
        (3725, "1:02:05"),
        (-1, "--:--"),
        (float("inf"), "--:--"),
        (float("-inf"), "--:--"),
        (float("nan"), "--:--"),
        (59, "00:59"),
        (3600, "1:00:00"),
        (61.9, "01:01"),  # truncates fractional seconds
    ])
    def test_format_eta(self, seconds, expected):
        assert harvester.format_eta(seconds) == expected


# ---- harvester.Progress --------------------------------------------------------

def _progress(grid_total=100, max_buildings=10):
    # run_id=None -> no DB writes; tick() is never called -> no stdout render.
    return harvester.Progress(grid_total=grid_total, max_buildings=max_buildings,
                              run_id=None)


class TestProgress:
    def test_eta_none_with_fewer_than_two_samples(self):
        p = _progress()
        assert p.eta_seconds() is None
        p._samples.append((0.0, 1, 0))
        assert p.eta_seconds() is None

    def test_eta_none_when_no_time_elapsed(self):
        p = _progress()
        p._samples = deque([(5.0, 0, 0), (5.0, 10, 1)])
        assert p.eta_seconds() is None

    def test_eta_grid_and_find_rate_agree(self):
        p = _progress(grid_total=100, max_buildings=10)
        p.done, p.found = 50, 5
        p._samples = deque([(0.0, 0, 0), (10.0, 50, 5)])
        # grid: 5 pkt/s -> 50 left -> 10 s; find: 0.5/s -> 5 left -> 10 s
        assert p.eta_seconds() == pytest.approx(10.0)

    def test_eta_capped_by_max_buildings(self):
        p = _progress(grid_total=100, max_buildings=10)
        p.done, p.found = 50, 9
        p._samples = deque([(0.0, 0, 0), (10.0, 50, 9)])
        # grid would say 10 s, but find rate 0.9/s with 1 lead left -> ~1.11 s
        eta = p.eta_seconds()
        assert eta == pytest.approx((10 - 9) / 0.9)
        assert eta < 10.0

    def test_eta_uses_grid_when_no_finds(self):
        p = _progress(grid_total=100, max_buildings=10)
        p.done, p.found = 50, 0
        p._samples = deque([(0.0, 0, 0), (10.0, 50, 0)])
        assert p.eta_seconds() == pytest.approx(10.0)

    def test_eta_never_negative(self):
        p = _progress(grid_total=100, max_buildings=10)
        p.done, p.found = 100, 10  # already past both limits
        p._samples = deque([(0.0, 0, 0), (10.0, 100, 10)])
        assert p.eta_seconds() == 0.0

    def test_add_cost_accumulates(self):
        p = _progress()
        p.add_cost(0.01)
        p.add_cost(0.002)
        p.add_cost(0.005)
        assert p.cost == pytest.approx(0.017)

    def test_grid_total_floor_of_one(self):
        # Guards against division by zero in _render's pct computation.
        p = harvester.Progress(grid_total=0, max_buildings=5, run_id=None)
        assert p.grid_total == 1


# ---- prescreen.parse_verdict ----------------------------------------------------

class TestParseVerdict:
    def test_valid_json(self):
        v = prescreen.parse_verdict(
            '{"has_panels": false, "score": 85, "reason": "Stort rent sadeltak"}'
        )
        assert v == {"has_panels": False, "score": 85,
                     "reason": "Stort rent sadeltak"}

    def test_text_around_json_ignored(self):
        v = prescreen.parse_verdict(
            'Här är min bedömning: {"has_panels": true, "score": 0, '
            '"reason": "Paneler finns"} hoppas det hjälper'
        )
        assert v is not None
        assert v["has_panels"] is True
        assert v["score"] == 0

    def test_score_clamped_low(self):
        v = prescreen.parse_verdict('{"has_panels": false, "score": -5, "reason": "x"}')
        assert v["score"] == 0

    def test_score_clamped_high(self):
        v = prescreen.parse_verdict('{"has_panels": false, "score": 150, "reason": "x"}')
        assert v["score"] == 100

    def test_score_as_string(self):
        v = prescreen.parse_verdict('{"has_panels": false, "score": "85", "reason": "x"}')
        assert v["score"] == 85

    def test_score_non_numeric_string_returns_none(self):
        assert prescreen.parse_verdict(
            '{"has_panels": false, "score": "hög", "reason": "x"}'
        ) is None

    def test_score_none_returns_none(self):
        assert prescreen.parse_verdict(
            '{"has_panels": false, "score": null, "reason": "x"}'
        ) is None

    def test_invalid_json_returns_none(self):
        assert prescreen.parse_verdict('{"score": 85,,}') is None

    def test_no_json_returns_none(self):
        assert prescreen.parse_verdict("Jag kan inte bedöma denna bild.") is None

    def test_missing_score_returns_none(self):
        assert prescreen.parse_verdict('{"has_panels": true, "reason": "x"}') is None

    def test_non_dict_json_returns_none(self):
        # No {...} match at all for a bare array.
        assert prescreen.parse_verdict("[1, 2, 3]") is None

    @pytest.mark.parametrize("raw, expected", [
        (True, True), (False, False), (1, True), (0, False),
        ("yes", True), (None, False),
    ])
    def test_has_panels_coerced_to_bool(self, raw, expected):
        import json as _json
        text = _json.dumps({"has_panels": raw, "score": 50, "reason": "x"})
        assert prescreen.parse_verdict(text)["has_panels"] is expected

    def test_has_panels_missing_defaults_false(self):
        v = prescreen.parse_verdict('{"score": 50, "reason": "x"}')
        assert v["has_panels"] is False

    def test_reason_truncated_to_200(self):
        long_reason = "a" * 500
        v = prescreen.parse_verdict(
            f'{{"has_panels": false, "score": 50, "reason": "{long_reason}"}}'
        )
        assert len(v["reason"]) == 200
        assert v["reason"] == "a" * 200

    def test_reason_missing_defaults_empty(self):
        v = prescreen.parse_verdict('{"has_panels": false, "score": 50}')
        assert v["reason"] == ""


# ---- prescreen.pick_backend ------------------------------------------------------

class TestPickBackend:
    def test_forced_api(self, monkeypatch):
        monkeypatch.setattr(prescreen, "ANTHROPIC_API_KEY", "")
        assert prescreen.pick_backend("api") == "api"

    def test_forced_cli(self, monkeypatch):
        monkeypatch.setattr(prescreen, "ANTHROPIC_API_KEY", "sk-ant-xyz")
        assert prescreen.pick_backend("cli") == "cli"

    def test_auto_api_when_key_set(self, monkeypatch):
        monkeypatch.setattr(prescreen, "ANTHROPIC_API_KEY", "sk-ant-xyz")
        assert prescreen.pick_backend(None) == "api"

    def test_auto_cli_when_no_key(self, monkeypatch):
        monkeypatch.setattr(prescreen, "ANTHROPIC_API_KEY", "")
        assert prescreen.pick_backend(None) == "cli"
