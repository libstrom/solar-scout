"""test_background_scan.py — tests for the background scan engine in app.py.

Covers the daemon-thread refactor that keeps Streamlit's WebSocket alive on long
scans: _ScanState, _StateLogHandler (dev console), and _run_scan_worker error
classification + progressive AI-lead persistence.
"""
import logging
import sys
import types
from unittest.mock import MagicMock

import pytest

# ── Stub heavy deps so app.py imports without a real Streamlit runtime ─────────
_st = types.ModuleType("streamlit")
for _attr in [
    "cache_resource", "cache_data", "session_state", "secrets", "error",
    "warning", "info", "success", "rerun", "expander", "code", "text_input",
    "selectbox", "button", "metric", "spinner", "tabs", "columns", "divider",
    "caption", "subheader", "header", "markdown", "dataframe", "image",
    "sidebar", "checkbox", "radio", "empty", "progress", "stop", "toast",
]:
    setattr(_st, _attr, MagicMock())
_st.secrets = {}
_st.session_state = MagicMock()
sys.modules.setdefault("streamlit", _st)
for _mod in ["extra_streamlit_components", "stripe", "googlemaps"]:
    sys.modules.setdefault(_mod, types.ModuleType(_mod))

import app  # noqa: E402
import scanner  # noqa: E402


def _fake_lead(source="ai", address="Testgatan 1", lat=57.0, lng=14.0):
    return scanner.Lead(lat=lat, lng=lng, address=address, confidence=0.9, source=source)


# ── _ScanState ─────────────────────────────────────────────────────────────────

def test_scanstate_defaults():
    s = app._ScanState()
    assert s.done == 0
    assert s.total == 0
    assert s.finished is False
    assert s.error_kind == ""
    assert s.leads_live == [] and s.result_leads == []
    assert s.logs == []


# ── _StateLogHandler ───────────────────────────────────────────────────────────

def test_state_log_handler_mirrors_records():
    s = app._ScanState()
    handler = app._StateLogHandler(s)
    log = logging.getLogger("test_bgscan")
    log.addHandler(handler)
    log.setLevel(logging.DEBUG)
    log.info("hello world")
    log.warning("careful")
    log.removeHandler(handler)
    msgs = [m for _ts, _lvl, m in s.logs]
    assert "hello world" in msgs
    assert "careful" in msgs


def test_state_log_handler_caps_buffer():
    s = app._ScanState()
    handler = app._StateLogHandler(s)
    for i in range(1000):
        handler.emit(logging.LogRecord("x", logging.INFO, "f", 0, f"msg{i}", None, None))
    assert len(s.logs) <= 800
    # Newest entries retained
    assert s.logs[-1][2] == "msg999"


# ── _run_scan_worker ───────────────────────────────────────────────────────────

def _run_worker(state, sb_client, **overrides):
    kwargs = dict(
        state=state, use_bbox=False, city_name="Nässjö",
        south=None, west=None, north=None, east=None,
        google_key="g", anthr_key="a", mapbox_key=None, max_leads=10,
        skip_tile_keys=frozenset(), user_id="u1", sb_client=sb_client,
    )
    kwargs.update(overrides)
    app._run_scan_worker(**kwargs)


def test_worker_success_populates_state(monkeypatch):
    lead = _fake_lead(source="ai")

    def fake_scan_city(city, gk, ak, on_progress, **kw):
        on_progress(1, 1, lead)
        return [lead], scanner.ScanStats(yes=1)

    monkeypatch.setattr(scanner, "scan_city", fake_scan_city)
    sb = MagicMock()
    state = app._ScanState(budget=None)
    _run_worker(state, sb)

    assert state.finished is True
    assert state.error_kind == ""
    assert state.result_leads == [lead]
    assert state.done == 1
    assert state.leads_live == [lead]
    # AI lead persisted progressively
    sb.table.assert_called_with("scout_leads")
    sb.table.return_value.insert.return_value.execute.assert_called()


def test_worker_skips_db_for_osm_lead(monkeypatch):
    lead = _fake_lead(source="osm")

    def fake_scan_city(city, gk, ak, on_progress, **kw):
        on_progress(1, 1, lead)
        return [lead], scanner.ScanStats()

    monkeypatch.setattr(scanner, "scan_city", fake_scan_city)
    sb = MagicMock()
    state = app._ScanState()
    _run_worker(state, sb)

    # OSM leads are NOT saved by the worker (main thread persists them)
    sb.table.return_value.insert.assert_not_called()
    assert state.finished is True


def test_worker_db_failure_recorded(monkeypatch):
    lead = _fake_lead(source="ai")
    sb = MagicMock()
    sb.table.return_value.insert.return_value.execute.side_effect = RuntimeError("db down")

    def fake_scan_city(city, gk, ak, on_progress, **kw):
        on_progress(1, 1, lead)
        return [lead], scanner.ScanStats(yes=1)

    monkeypatch.setattr(scanner, "scan_city", fake_scan_city)
    state = app._ScanState()
    _run_worker(state, sb)

    assert state.finished is True
    assert any("DB-sparning misslyckades" in e for e in state.scan_errors)


def test_worker_value_error(monkeypatch):
    def fake_scan_city(*a, **k):
        raise ValueError("Hittade inte orten: Foo")

    monkeypatch.setattr(scanner, "scan_city", fake_scan_city)
    state = app._ScanState()
    _run_worker(state, MagicMock())

    assert state.finished is True
    assert state.error_kind == "value"
    assert "Hittade inte orten" in state.error_msg


def test_worker_quota_error(monkeypatch):
    def fake_scan_city(*a, **k):
        raise scanner.APIQuotaExceededError("Anthropic", "credit balance")

    monkeypatch.setattr(scanner, "scan_city", fake_scan_city)
    state = app._ScanState()
    _run_worker(state, MagicMock())

    assert state.finished is True
    assert state.error_kind == "quota"
    assert state.quota_api == "Anthropic"


def test_worker_generic_crash(monkeypatch):
    def fake_scan_city(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(scanner, "scan_city", fake_scan_city)
    state = app._ScanState()
    _run_worker(state, MagicMock())

    assert state.finished is True
    assert state.error_kind == "crash"
    assert any("avbröts oväntat" in e for e in state.scan_errors)


def test_worker_bbox_mode(monkeypatch):
    calls = {}

    def fake_scan_bbox(s, w, n, e, gk, ak, on_progress, **kw):
        calls["bbox"] = (s, w, n, e)
        return [], scanner.ScanStats()

    monkeypatch.setattr(scanner, "scan_bbox", fake_scan_bbox)
    state = app._ScanState()
    _run_worker(state, MagicMock(), use_bbox=True,
                south=57.0, west=14.0, north=57.1, east=14.1, city_name="")

    assert calls["bbox"] == (57.0, 14.0, 57.1, 14.1)
    assert state.finished is True


# ── Cancellation (BudgetTracker) ────────────────────────────────────────────────

def test_budget_cancel_flag():
    from scan_cost import BudgetTracker
    bt = BudgetTracker()
    assert bt.cancelled is False
    bt.request_cancel()
    assert bt.cancelled is True
