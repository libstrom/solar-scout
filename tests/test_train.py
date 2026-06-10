"""Tests for train.py (självträningen) och prescreen.augment_prompt.

Offline: helpers är rena funktioner, DB-testerna repointar db.py:s
modulglobaler till tmp_path (samma mönster som övriga testfiler).
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

import db as dbmod  # noqa: E402
import prescreen  # noqa: E402
import train  # noqa: E402


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    monkeypatch.setattr(dbmod, "DATA_DIR", data_dir)
    monkeypatch.setattr(dbmod, "IMAGES_DIR", data_dir / "images")
    monkeypatch.setattr(dbmod, "DB_PATH", data_dir / "leads.db")
    dbmod.ensure_schema()
    return data_dir / "leads.db"


def _insert(db_path, place_id, status, ai_score, ai_reason="skäl"):
    with sqlite3.connect(db_path) as c:
        c.execute(
            """
            INSERT INTO leads (place_id, lat, lng, status, ai_score, ai_reason, created_at)
            VALUES (?, 56.0, 13.0, ?, ?, ?, '2026-06-10T00:00:00')
            """,
            (place_id, status, ai_score, ai_reason),
        )


# ---- pick_threshold ---------------------------------------------------------------

class TestPickThreshold:
    def test_none_when_too_few_confirmed(self):
        assert train.pick_threshold([60], [10, 20, 30, 40, 50]) is None

    def test_none_when_too_few_rejected(self):
        assert train.pick_threshold([60, 70, 80, 90, 55], [10]) is None

    def test_cutoff_is_min_confirmed_minus_margin(self):
        confirmed = [55, 70, 80, 90, 60]
        rejected = [10, 20, 30, 15, 25]
        assert train.pick_threshold(confirmed, rejected) == 45

    def test_none_when_cutoff_not_positive(self):
        confirmed = [5, 8, 9, 7, 10]   # min 5 - 10 = -5
        rejected = [1, 2, 3, 4, 2]
        assert train.pick_threshold(confirmed, rejected) is None


# ---- mine_few_shot ----------------------------------------------------------------

def _row(status, score, reason="skäl"):
    return {"status": status, "ai_score": score, "ai_reason": reason}


class TestMineFewShot:
    def test_picks_most_disagreeing_examples(self):
        confirmed = [_row("confirmed", s, f"c{s}") for s in (90, 40, 70)]
        rejected = [_row("rejected", s, f"r{s}") for s in (10, 60, 30)]
        lines = train.mine_few_shot(confirmed, rejected, k=1)
        assert len(lines) == 2
        assert '"c40"' in lines[0] and "BEKRÄFTAT" in lines[0]
        assert '"r60"' in lines[1] and "AVVISAT" in lines[1]

    def test_skips_rows_without_reason(self):
        lines = train.mine_few_shot([_row("confirmed", 50, "")], [])
        assert lines == []

    def test_empty_labels_give_no_lines(self):
        assert train.mine_few_shot([], []) == []


# ---- auto_reject_below --------------------------------------------------------------

class TestAutoReject:
    def test_rejects_only_pending_below_cutoff(self, tmp_db):
        _insert(tmp_db, "a", "pending", 10)
        _insert(tmp_db, "b", "pending", 80)
        _insert(tmp_db, "c", "confirmed", 5)   # redan validerad -- rörs ej
        n = train.auto_reject_below(45)
        assert n == 1
        with sqlite3.connect(tmp_db) as c:
            statuses = dict(c.execute("SELECT place_id, status FROM leads").fetchall())
        assert statuses == {"a": "auto_rejected", "b": "pending", "c": "confirmed"}

    def test_dry_run_changes_nothing(self, tmp_db):
        _insert(tmp_db, "a", "pending", 10)
        n = train.auto_reject_below(45, dry_run=True)
        assert n == 1
        with sqlite3.connect(tmp_db) as c:
            status = c.execute("SELECT status FROM leads WHERE place_id='a'").fetchone()[0]
        assert status == "pending"

    def test_ungraded_leads_never_touched(self, tmp_db):
        _insert(tmp_db, "a", "pending", None)
        assert train.auto_reject_below(45) == 0


# ---- fetch_labels --------------------------------------------------------------------

class TestFetchLabels:
    def test_splits_by_status_and_requires_score(self, tmp_db):
        _insert(tmp_db, "a", "confirmed", 70)
        _insert(tmp_db, "b", "rejected", 20)
        _insert(tmp_db, "c", "confirmed", None)  # ej AI-graderad -- ingen etikett
        _insert(tmp_db, "d", "pending", 50)
        confirmed, rejected = train.fetch_labels()
        assert [r["ai_score"] for r in confirmed] == [70]
        assert [r["ai_score"] for r in rejected] == [20]


# ---- prescreen.augment_prompt ----------------------------------------------------------

class TestAugmentPrompt:
    def test_no_calibration_returns_base(self):
        assert prescreen.augment_prompt("BAS", None) == "BAS"
        assert prescreen.augment_prompt("BAS", {"few_shot": []}) == "BAS"

    def test_few_shot_lines_appended(self):
        out = prescreen.augment_prompt("BAS", {"few_shot": ["- rad1", "- rad2"]})
        assert out.startswith("BAS")
        assert "manuella valideringar" in out
        assert "- rad1" in out and "- rad2" in out
