"""test_leads_metrics.py — mätvärdena i Leads-fliken.

Regression: `page_leads` räknade på en `has_solar`-kolumn som inte finns i
scout_leads. Resultatet blev "Med solceller: 0" för varje lead och ett filter
som tömde listan. Mätvärdena bygger nu på granskningsstatus istället
(user_confirmed / needs_review), vilket är det som faktiskt skiljer leads åt —
alla rader i listan ÄR soltak.
"""
import sys
import types
from unittest.mock import MagicMock

import pandas as pd

# Samma streamlit-stub som test_background_scan.py
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


def _df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def test_count_flag_counts_true_rows():
    df = _df([
        {"user_confirmed": True},
        {"user_confirmed": True},
        {"user_confirmed": False},
    ])
    assert app._count_flag(df, "user_confirmed") == 2


def test_count_flag_treats_null_as_false():
    """Supabase ger None för obesvarade flaggor → NaN i pandas.

    `bool(nan)` är True, så en rå .sum() skulle räknat granskade och
    ogranskade leads som samma sak.
    """
    df = _df([
        {"user_confirmed": True},
        {"user_confirmed": None},
        {"user_confirmed": None},
    ])
    assert app._count_flag(df, "user_confirmed") == 1


def test_count_flag_missing_column_is_zero():
    """En äldre databas utan kolumnen får inte krascha listan."""
    assert app._count_flag(_df([{"address": "Testgatan 1"}]), "needs_review") == 0


def test_count_flag_empty_dataframe():
    assert app._count_flag(pd.DataFrame(), "user_confirmed") == 0


def test_is_true_rejects_nan_and_none():
    """Statusikonen valde ✅ för ogranskade leads: bool(nan) är True."""
    assert app._is_true(True) is True
    assert app._is_true(False) is False
    assert app._is_true(None) is False
    assert app._is_true(float("nan")) is False


def test_filter_flag_missing_column_returns_empty():
    """Äldre schema utan kolumnen får inte visa HELA listan under ett filter
    som utger sig för att visa ett urval."""
    df = _df([{"address": "Testgatan 1"}, {"address": "Testgatan 2"}])
    assert len(app._filter_flag(df, "user_confirmed")) == 0


def test_filter_flag_keeps_only_true_rows():
    df = _df([
        {"address": "A", "user_confirmed": True},
        {"address": "B", "user_confirmed": None},
        {"address": "C", "user_confirmed": False},
    ])
    result = app._filter_flag(df, "user_confirmed")
    assert list(result["address"]) == ["A"]


def test_no_has_solar_column_reference_left():
    """`has_solar` finns inte i scout_leads — regression mot att den smyger
    tillbaka in i leads-vyn och nollar mätvärdena igen."""
    src = open(app.__file__, encoding="utf-8").read()
    # Manuellt sparade leads får fortfarande sätta fältet i sin egen dict;
    # det som är förbjudet är att LÄSA det ur en DataFrame från databasen.
    assert 'df["has_solar"]' not in src
    assert 'row.get("has_solar"' not in src
