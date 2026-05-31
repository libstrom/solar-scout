"""
test_db_robustness.py — Stresstest för DB-hantering.

Testar att:
  1. _sb_retry försöker igen på transient httpx-fel
  2. _sb_retry hanterar TimeoutException (ny i iter-2)
  3. get_accuracy_stats returnerar tomma stats vid DB-fel
  4. load_leads returnerar tom DataFrame vid DB-fel
  5. delete_lead och confirm_lead sväljer undantag utan krasch
  6. Retry-backoff ökar med varje försök

Inga riktiga Supabase-anrop — allt är mockat.
"""

import time
import threading
import httpx
import pandas as pd
import pytest
from unittest.mock import MagicMock, patch, call


# ── Importera funktionerna vi testar ──────────────────────────────────────────

# app.py importerar streamlit — vi behöver patcha det
import sys
import types

# Minimal streamlit stub so app.py can be imported without a real Streamlit
_st_stub = types.ModuleType("streamlit")
for _attr in [
    "cache_resource", "session_state", "secrets", "error", "warning",
    "info", "success", "rerun", "expander", "code",
]:
    setattr(_st_stub, _attr, MagicMock())
_st_stub.secrets = {}
sys.modules.setdefault("streamlit", _st_stub)

# Patch heavy optional imports before importing app
for _mod in ["extra_streamlit_components", "stripe", "googlemaps"]:
    sys.modules.setdefault(_mod, types.ModuleType(_mod))

import app as _app  # noqa: E402


# ── Helper: produce a real httpx exception ───────────────────────────────────

def _remote_protocol_error():
    return httpx.RemoteProtocolError("peer closed connection without sending a response")


def _timeout_error():
    return httpx.TimeoutException("timed out")


def _connect_error():
    return httpx.ConnectError("connection refused")


# ── _sb_retry ─────────────────────────────────────────────────────────────────

def test_sb_retry_succeeds_first_try():
    calls = []
    def fn():
        calls.append(1)
        return "ok"

    result = _app._sb_retry(fn)
    assert result == "ok"
    assert len(calls) == 1


def test_sb_retry_retries_remote_protocol_error():
    attempt = [0]
    def fn():
        attempt[0] += 1
        if attempt[0] < 3:
            raise _remote_protocol_error()
        return "recovered"

    with patch("time.sleep"):  # don't actually sleep in tests
        result = _app._sb_retry(fn, attempts=3)
    assert result == "recovered"
    assert attempt[0] == 3


def test_sb_retry_retries_timeout():
    attempt = [0]
    def fn():
        attempt[0] += 1
        if attempt[0] == 1:
            raise _timeout_error()
        return "ok"

    with patch("time.sleep"):
        result = _app._sb_retry(fn, attempts=3)
    assert result == "ok"
    assert attempt[0] == 2


def test_sb_retry_retries_connect_error():
    attempt = [0]
    def fn():
        attempt[0] += 1
        if attempt[0] < 2:
            raise _connect_error()
        return "ok"

    with patch("time.sleep"):
        result = _app._sb_retry(fn, attempts=3)
    assert result == "ok"


def test_sb_retry_raises_after_all_attempts():
    def fn():
        raise _remote_protocol_error()

    with patch("time.sleep"):
        with pytest.raises(httpx.RemoteProtocolError):
            _app._sb_retry(fn, attempts=3)


def test_sb_retry_does_not_catch_non_transient():
    def fn():
        raise ValueError("not a transient error")

    with pytest.raises(ValueError):
        _app._sb_retry(fn)


def test_sb_retry_backoff_increases():
    """sleep() is called with increasing delays, but NOT after the last attempt."""
    sleeps = []
    def fn():
        raise _remote_protocol_error()

    with patch("time.sleep", side_effect=sleeps.append):
        with pytest.raises(httpx.RemoteProtocolError):
            _app._sb_retry(fn, attempts=3)

    # attempts=3: sleep after attempt 1 and 2, NOT after attempt 3
    assert len(sleeps) == 2
    assert sleeps[1] > sleeps[0]


# ── get_accuracy_stats ────────────────────────────────────────────────────────

def test_get_accuracy_stats_returns_empty_on_db_error():
    """If Supabase throws, get_accuracy_stats must return safe defaults, not raise."""
    with patch.object(_app, "get_supabase") as mock_sb:
        mock_sb.return_value.table.return_value.select.return_value \
            .eq.return_value.eq.return_value.execute.side_effect = _connect_error()

        with patch("time.sleep"):
            stats = _app.get_accuracy_stats("some-user-id")

    assert stats["total_ai"] == 0
    assert stats["reviewed"] == 0
    assert stats["confirmed"] == 0
    assert stats["pct"] is None


def test_get_accuracy_stats_counts_correctly():
    """Happy path: correct counts from real-looking data."""
    fake_rows = [
        {"user_confirmed": True},
        {"user_confirmed": True},
        {"user_confirmed": False},
        {"user_confirmed": None},   # unreviewed
    ]
    mock_resp = MagicMock()
    mock_resp.data = fake_rows

    with patch.object(_app, "get_supabase") as mock_sb:
        (mock_sb.return_value.table.return_value.select.return_value
         .eq.return_value.eq.return_value.execute.return_value) = mock_resp

        stats = _app.get_accuracy_stats("uid")

    assert stats["total_ai"] == 4
    assert stats["reviewed"] == 3
    assert stats["confirmed"] == 2
    assert stats["denied"] == 1
    assert stats["pct"] == 67


# ── load_leads ────────────────────────────────────────────────────────────────

def test_load_leads_returns_empty_dataframe_on_error():
    with patch.object(_app, "get_supabase") as mock_sb:
        # load_leads: .table().select().eq(user_id).eq(false_positive).order().execute()
        chain = mock_sb.return_value.table.return_value.select.return_value
        chain.eq.return_value.eq.return_value.order.return_value.execute.side_effect = _timeout_error()

        with patch("time.sleep"):
            df = _app.load_leads("uid")

    assert isinstance(df, pd.DataFrame)
    assert df.empty


# ── delete_lead / confirm_lead ────────────────────────────────────────────────

def test_delete_lead_swallows_exception():
    with patch.object(_app, "get_supabase") as mock_sb:
        (mock_sb.return_value.table.return_value.delete.return_value
         .eq.return_value.execute.side_effect) = _remote_protocol_error()

        with patch("time.sleep"):
            _app.delete_lead(999)  # must not raise


def test_confirm_lead_swallows_exception():
    with patch.object(_app, "get_supabase") as mock_sb:
        (mock_sb.return_value.table.return_value.update.return_value
         .eq.return_value.execute.side_effect) = _connect_error()

        with patch("time.sleep"):
            _app.confirm_lead(999, True)  # must not raise
