"""
Enhetstester för lead-persistens-logiken i app.py.

Testar save_lead, load_leads, confirm_lead, delete_lead och get_accuracy_stats
med en mockad Supabase-klient — inga nätverksanrop görs.
"""

import sys
from unittest.mock import MagicMock
import pandas as pd
import pytest

# ── Stub externa paket INNAN app importeras ────────────────────────────────────
# st.cache_resource, stripe, googlemaps etc. ersätts med MagicMock så att
# import-tidens sidoeffekter (stripe.api_key = …, create_client …) inte kraschar.
for _mod in (
    "googlemaps",
    "streamlit",
    "supabase",
    "stripe",
    "folium",
    "streamlit_folium",
    "openpyxl",
):
    sys.modules.setdefault(_mod, MagicMock())

import app  # noqa: E402


# ── Hjälpfunktion ──────────────────────────────────────────────────────────────

def _make_supabase_mock(rows=None):
    """Returnera en MagicMock som simulerar Supabase-klientens flytande kedja.

    Kedjan: sb.table("scout_leads").select("*").eq(...).execute()
            sb.table("scout_leads").insert(data).execute()
            sb.table("scout_leads").update(...).eq(...).execute()
            sb.table("scout_leads").delete().eq(...).execute()
            sb.table("scout_leads").select(...).eq(...).eq(...).execute()

    Varje steg i kedjan returnerar samma mock-objekt (self-referential) utom
    .execute() som returnerar ett Response-liknande objekt med .data satt till
    ``rows``.

    Eftersom @st.cache_resource ersatts av MagicMock vid import-tid är
    app.get_supabase redan ett MagicMock-objekt.  Vi sätter därför
    app.get_supabase.return_value = <vår sb> direkt — inget patch() behövs.
    """
    execute_result = MagicMock()
    execute_result.data = rows if rows is not None else []

    # Skapa ett objekt som returnerar sig självt för alla kedjeoperationer
    # men returnerar execute_result för .execute()
    chain = MagicMock()
    chain.select.return_value = chain
    chain.insert.return_value = chain
    chain.update.return_value = chain
    chain.delete.return_value = chain
    chain.eq.return_value = chain
    chain.order.return_value = chain
    chain.execute.return_value = execute_result

    sb = MagicMock()
    sb.table.return_value = chain

    # Exponera chain och execute_result för assertions i testerna
    sb._chain = chain
    sb._execute_result = execute_result
    return sb


def _inject(sb):
    """Ersätt app.get_supabase med en funktion som returnerar sb."""
    app.get_supabase = lambda: sb
    return sb


# ── save_lead ──────────────────────────────────────────────────────────────────

def test_save_lead_inserts_correct_data():
    """save_lead ska sätta user_id på data-dikt och anropa insert på scout_leads."""
    sb = _inject(_make_supabase_mock())

    app.save_lead(
        user_id="uid-123",
        data={
            "address": "Storgatan 1, Malmö",
            "has_solar": False,
            "lat": 55.6,
            "lon": 13.0,
        },
        profile={},  # provide empty profile so get_profile is not called
    )

    # scout_leads tabell användes för insert
    sb.table.assert_any_call("scout_leads")

    # insert anropades med ett dict som innehåller user_id och address
    insert_args = sb._chain.insert.call_args[0][0]
    assert insert_args["user_id"] == "uid-123"
    assert insert_args["address"] == "Storgatan 1, Malmö"
    assert insert_args["has_solar"] is False

    # execute anropades minst en gång
    assert sb._chain.execute.call_count >= 1


def test_save_lead_overwrites_user_id_if_already_set():
    """Även om data redan har user_id ska save_lead skriva över med givet user_id."""
    sb = _inject(_make_supabase_mock())

    app.save_lead(
        user_id="correct-uid",
        data={"user_id": "wrong-uid", "address": "Byvägen 2"},
        profile={},  # provide empty profile so get_profile is not called
    )

    insert_args = sb._chain.insert.call_args[0][0]
    assert insert_args["user_id"] == "correct-uid"


# ── load_leads ─────────────────────────────────────────────────────────────────

def test_load_leads_returns_dataframe_with_correct_columns():
    """load_leads ska returnera en DataFrame med kolumnerna från Supabase-svaret."""
    rows = [
        {"id": 1, "user_id": "uid-1", "address": "Norra gatan 5", "has_solar": True, "created_at": "2024-01-01"},
        {"id": 2, "user_id": "uid-1", "address": "Södra gatan 3", "has_solar": False, "created_at": "2024-01-02"},
    ]
    sb = _inject(_make_supabase_mock(rows=rows))

    df = app.load_leads("uid-1")

    assert isinstance(df, pd.DataFrame)
    assert len(df) == 2
    assert list(df.columns) == ["id", "user_id", "address", "has_solar", "created_at"]
    assert df.iloc[0]["address"] == "Norra gatan 5"


def test_load_leads_returns_empty_dataframe_when_no_leads():
    """load_leads ska returnera en tom DataFrame om inga leads finns."""
    sb = _inject(_make_supabase_mock(rows=[]))

    df = app.load_leads("uid-no-leads")

    assert isinstance(df, pd.DataFrame)
    assert df.empty


def test_load_leads_filters_by_user_id():
    """load_leads ska anropa .eq('user_id', …) för att filtrera per användare."""
    sb = _inject(_make_supabase_mock(rows=[]))

    app.load_leads("uid-42")

    sb._chain.eq.assert_any_call("user_id", "uid-42")


# ── confirm_lead ───────────────────────────────────────────────────────────────

def test_confirm_lead_sets_confirmed_true():
    """confirm_lead(id, True) ska anropa update med user_confirmed=True."""
    sb = _inject(_make_supabase_mock())

    app.confirm_lead(lead_id=7, confirmed=True)

    sb._chain.update.assert_called_once_with({"user_confirmed": True})
    sb._chain.eq.assert_any_call("id", 7)
    sb._chain.execute.assert_called_once()


def test_confirm_lead_sets_confirmed_false():
    """confirm_lead(id, False) ska anropa update med user_confirmed=False."""
    sb = _inject(_make_supabase_mock())

    app.confirm_lead(lead_id=99, confirmed=False)

    sb._chain.update.assert_called_once_with({"user_confirmed": False})
    sb._chain.eq.assert_any_call("id", 99)


# ── delete_lead ────────────────────────────────────────────────────────────────

def test_delete_lead_calls_delete_with_correct_id():
    """delete_lead ska anropa .delete().eq('id', lead_id).execute()."""
    sb = _inject(_make_supabase_mock())

    app.delete_lead(lead_id=42)

    sb.table.assert_called_once_with("scout_leads")
    sb._chain.delete.assert_called_once()
    sb._chain.eq.assert_any_call("id", 42)
    sb._chain.execute.assert_called_once()


# ── get_accuracy_stats ─────────────────────────────────────────────────────────

def test_get_accuracy_stats_counts_correctly():
    """get_accuracy_stats ska räkna reviewed/confirmed/denied korrekt."""
    rows = [
        {"user_confirmed": True},   # reviewed + confirmed
        {"user_confirmed": True},   # reviewed + confirmed
        {"user_confirmed": False},  # reviewed + denied
        {"user_confirmed": None},   # ej reviewed
    ]
    sb = _inject(_make_supabase_mock(rows=rows))

    stats = app.get_accuracy_stats("uid-stats")

    assert stats["total_ai"] == 4
    assert stats["reviewed"] == 3
    assert stats["confirmed"] == 2
    assert stats["denied"] == 1
    assert stats["pct"] == 67  # round(2/3 * 100) = 67


def test_get_accuracy_stats_no_rows():
    """get_accuracy_stats med inga AI-leads ska returnera None för pct."""
    sb = _inject(_make_supabase_mock(rows=[]))

    stats = app.get_accuracy_stats("uid-empty")

    assert stats["total_ai"] == 0
    assert stats["reviewed"] == 0
    assert stats["confirmed"] == 0
    assert stats["denied"] == 0
    assert stats["pct"] is None


def test_get_accuracy_stats_all_unreviewed():
    """Alla leads None → reviewed=0, pct=None."""
    rows = [{"user_confirmed": None}, {"user_confirmed": None}]
    sb = _inject(_make_supabase_mock(rows=rows))

    stats = app.get_accuracy_stats("uid-unreviewed")

    assert stats["total_ai"] == 2
    assert stats["reviewed"] == 0
    assert stats["pct"] is None


def test_get_accuracy_stats_filters_by_user_id_and_scan_source():
    """get_accuracy_stats ska filtrera på både user_id och scan_source='ai'."""
    sb = _inject(_make_supabase_mock(rows=[]))

    app.get_accuracy_stats("uid-filter-test")

    eq_calls = [c.args for c in sb._chain.eq.call_args_list]
    assert ("user_id", "uid-filter-test") in eq_calls
    assert ("scan_source", "ai") in eq_calls
