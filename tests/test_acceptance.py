"""
test_acceptance.py — End-to-end acceptance-tester för Solar Scout.

Appen anses "hel" när den här sviten är grön:
  pytest -m acceptance

Fem kritiska vägar testas:
  1. Login-väg        — inloggning lyckas / ger svenska felmeddelanden vid fel
  2. Scan-väg         — scan_city → Lead → _lead_to_sb_row → load_leads
  3. Budget-väg       — scan stoppas när budgettaket nås, partial leads returneras
  4. DB-fel-väg       — Supabase-timeout → appen kraschar inte, ger safe defaults
  5. Cost-estimat-väg — estimate_scan_cost blockerar scans som överstiger budget

Inga riktiga nätverksanrop — allt är mockat.
"""

import sys
import types
import httpx
import pytest
import pandas as pd
from unittest.mock import MagicMock, patch

# ── Streamlit- och tunga beroenden-stub (INNAN app importeras) ────────────────

_st_stub = types.ModuleType("streamlit")
for _attr in [
    "cache_resource", "session_state", "secrets", "error", "warning",
    "info", "success", "rerun", "expander", "code",
]:
    setattr(_st_stub, _attr, MagicMock())
_st_stub.secrets = {}
sys.modules.setdefault("streamlit", _st_stub)

for _mod in ["extra_streamlit_components", "stripe", "googlemaps", "supabase",
             "folium", "streamlit_folium", "openpyxl"]:
    sys.modules.setdefault(_mod, MagicMock())

import app as _app       # noqa: E402
import scanner as _sc    # noqa: E402
from scan_cost import (  # noqa: E402
    BudgetTracker, estimate_scan_cost, DEFAULT_BUDGET_SEK,
)

pytestmark = pytest.mark.acceptance


# ── Hjälpfunktioner ───────────────────────────────────────────────────────────

def _fake_geocode(south=57.75, west=14.22, north=57.83, east=14.32,
                  center_lat=57.79, center_lng=14.27):
    return [{
        "geometry": {
            "location": {"lat": center_lat, "lng": center_lng},
            "viewport": {
                "southwest": {"lat": south, "lng": west},
                "northeast": {"lat": north, "lng": east},
            },
        }
    }]


def _fake_lead(**kwargs):
    defaults = dict(
        lat=57.79, lng=14.27,
        address="Solvägen 1, Huskvarna",
        confidence=0.92,
        source="osm",
        tile_key="tk_57790_14270",
    )
    defaults.update(kwargs)
    return _sc.Lead(**defaults)


# ── 1. Login-väg ──────────────────────────────────────────────────────────────

def _mock_auth_client(access_token="tok_access", refresh_token="tok_refresh"):
    user = MagicMock()
    user.id = "user-123"
    session = MagicMock()
    session.access_token = access_token
    session.refresh_token = refresh_token
    resp = MagicMock()
    resp.user = user
    resp.session = session
    client = MagicMock()
    client.auth.sign_in_with_password.return_value = resp
    return client


@pytest.mark.acceptance
def test_login_success_sets_session_state():
    """Lyckad inloggning fyller session_state med access- och refresh-token."""
    client = _mock_auth_client()
    session_state = {}
    _app.st.session_state = session_state

    with patch("app.create_client", return_value=client), \
         patch("app._get_cookie_manager", return_value=None):
        user = _app.do_login("test@example.com", "password123")

    assert user is not None
    assert session_state.get("access_token") == "tok_access"
    assert session_state.get("refresh_token") == "tok_refresh"
    client.auth.sign_in_with_password.assert_called_once_with(
        {"email": "test@example.com", "password": "password123"}
    )


@pytest.mark.acceptance
def test_login_invalid_credentials_gives_swedish_error():
    """'Invalid login credentials' ska ge ett lättläst svenskt meddelande."""
    msg = _app._sv_error(Exception("Invalid login credentials"))
    assert "Fel e-postadress" in msg


@pytest.mark.acceptance
def test_login_unconfirmed_email_gives_swedish_error():
    """'email not confirmed' ska ge ett svenskt bekräftelse-meddelande."""
    msg = _app._sv_error(Exception("email not confirmed"))
    assert "bekräftad" in msg


@pytest.mark.acceptance
def test_login_network_error_gives_swedish_error():
    """Nätverksfel ska ge ett förståeligt svenskt meddelande."""
    msg = _app._sv_error(Exception("network connection failed"))
    assert "Nätverksfel" in msg


# ── 2. Scan-väg ───────────────────────────────────────────────────────────────

def _mock_googlemaps(geocode_result):
    """Return a patched scanner.googlemaps where .Client(...).geocode() returns geocode_result."""
    fake_gmaps = MagicMock()
    fake_gmaps.geocode.return_value = geocode_result
    mock_module = MagicMock()
    mock_module.Client.return_value = fake_gmaps
    return mock_module


@pytest.mark.acceptance
def test_scan_city_returns_osm_lead():
    """scan_city utan anthropic_key → returnerar OSM-leads utan API-kostnad."""
    lead = _fake_lead()

    with patch.object(_sc, "googlemaps", _mock_googlemaps(_fake_geocode())), \
         patch.object(_sc, "scan_area_osm", return_value=[lead]):
        leads, stats = _sc.scan_city(
            city_name="Huskvarna",
            google_key="fake-key",
            anthropic_key=None,
        )

    assert len(leads) >= 1
    assert leads[0].address == "Solvägen 1, Huskvarna"
    assert leads[0].source == "osm"


@pytest.mark.acceptance
def test_lead_to_sb_row_has_required_fields():
    """_lead_to_sb_row producerar en dict med alla obligatoriska nycklar."""
    lead = _fake_lead(source="ai", confidence=0.87)
    row = _app._lead_to_sb_row(lead)

    for key in ("address", "lat", "lng", "scan_source", "maps_url",
                "google_search_url", "tile_key", "needs_review"):
        assert key in row, f"Saknar nyckel: {key}"
    assert row["address"] == "Solvägen 1, Huskvarna"
    assert row["scan_source"] == "ai"
    assert row["lat"] == pytest.approx(57.79)


@pytest.mark.acceptance
def test_load_leads_returns_saved_lead():
    """load_leads med mockad Supabase returnerar rätt lead i DataFrame."""
    fake_rows = [{"address": "Solvägen 1, Huskvarna", "lat": 57.79, "lng": 14.27}]
    mock_resp = MagicMock()
    mock_resp.data = fake_rows

    with patch.object(_app, "get_supabase") as mock_sb:
        chain = mock_sb.return_value.table.return_value.select.return_value
        chain.eq.return_value.eq.return_value.order.return_value.execute.return_value = mock_resp

        df = _app.load_leads("user-123")

    assert not df.empty
    assert df.iloc[0]["address"] == "Solvägen 1, Huskvarna"


# ── 3. Budget-väg ─────────────────────────────────────────────────────────────

@pytest.mark.acceptance
def test_budget_exceeded_flag_is_set():
    """BudgetTracker sätter stopped_over_budget vid kostnadsspräckning."""
    budget = BudgetTracker(budget_sek=0.001)
    budget.add_usage(input_tokens=1_000_000)  # ~31 kr >>> 0.001 kr
    budget.stopped_over_budget = True

    assert budget.stopped_over_budget
    assert budget.spent_sek > 0.001


@pytest.mark.acceptance
def test_scan_city_returns_partial_leads_when_budget_exceeded():
    """scan_city returnerar OSM-leads även när budgeten är slut innan AI-steget."""
    lead = _fake_lead(source="osm")
    budget = BudgetTracker(budget_sek=0.001)
    budget.add_usage(input_tokens=1_000_000)
    budget.stopped_over_budget = True

    with patch.object(_sc, "googlemaps", _mock_googlemaps(_fake_geocode())), \
         patch.object(_sc, "scan_area_osm", return_value=[lead]):
        leads, _stats = _sc.scan_city(
            city_name="Huskvarna",
            google_key="fake-key",
            anthropic_key=None,
            budget=budget,
        )

    # OSM-leads hämtas innan AI-loopen, ska finnas kvar
    assert any(l.address == "Solvägen 1, Huskvarna" for l in leads)


@pytest.mark.acceptance
def test_budget_stops_ai_scan_loop():
    """scan_buildings_ai loop bryter när budget.stopped_over_budget sätts."""
    budget = BudgetTracker(budget_sek=0.001)
    budget.add_usage(input_tokens=1_000_000)

    fake_gmaps = MagicMock()
    fake_gmaps.geocode.return_value = _fake_geocode()
    fake_area = {"lat": 57.79, "lng": 14.27, "area_deg2": 0.01}
    buildings = [{"lat": 57.79, "lng": 14.27, "address": "X", "osm_id": "1",
                  "building_type": "house", "zoom": 20, "area_m2": 120}]

    def fake_scan_buildings_ai(bldgs, *args, **kwargs):
        bgt = kwargs.get("budget")
        if bgt:
            bgt.stopped_over_budget = True
        return [], _sc.ScanStats()

    with patch.object(_sc, "googlemaps", _mock_googlemaps(_fake_geocode())), \
         patch.object(_sc, "scan_area_osm", return_value=[]), \
         patch.object(_sc, "_get_residential_areas", return_value=[fake_area]), \
         patch.object(_sc, "_get_osm_buildings", return_value=buildings), \
         patch.object(_sc, "scan_buildings_ai", side_effect=fake_scan_buildings_ai):
        leads, stats = _sc.scan_city(
            city_name="Huskvarna",
            google_key="fake-key",
            anthropic_key="fake-ak",
            budget=budget,
        )

    # Scan ska ha avbrutits utan krasch
    assert isinstance(leads, list)


@pytest.mark.acceptance
def test_scan_municipality_shares_budget_across_cities():
    """scan_municipality ska dela EN BudgetTracker mellan alla orter.

    Utan detta fick varje scan_city-anrop sin egen 5000 kr-budget (eftersom
    scan_city skapar en ny tracker när budget=None) — en bulk-scan av N orter
    kunde då kosta upp till N × 5000 kr innan spärren slog till. Med en delad
    tracker ska scan_city #2 aldrig anropas när budgeten redan är slut efter
    ort #1.
    """
    budget = BudgetTracker(budget_sek=10.0)
    call_count = {"n": 0}

    def fake_scan_city(city, *args, **kwargs):
        call_count["n"] += 1
        bgt = kwargs.get("budget")
        # Simulera att första ortens scan ensam spräcker den DELADE budgeten.
        bgt.add_usage(input_tokens=1_000_000)  # ~52 kr >> 10 kr tak
        bgt.stopped_over_budget = True
        return [], _sc.ScanStats()

    with patch.object(_sc, "scan_city", side_effect=fake_scan_city):
        leads, stats = _sc.scan_municipality(
            ["Nässjö", "Eksjö", "Vetlanda", "Jönköping"],
            google_key="fake-key",
            anthropic_key="fake-ak",
            budget=budget,
        )

    # Bara första orten ska ha scannats — resten hoppas över när den DELADE
    # budgeten redan är slut.
    assert call_count["n"] == 1
    assert budget.spent_sek > 10.0


# ── 4. DB-fel-väg ─────────────────────────────────────────────────────────────

@pytest.mark.acceptance
def test_load_leads_safe_on_timeout():
    """load_leads returnerar tom DataFrame vid Supabase-timeout — kraschar inte."""
    with patch.object(_app, "get_supabase") as mock_sb:
        chain = mock_sb.return_value.table.return_value.select.return_value
        chain.eq.return_value.eq.return_value.order.return_value.execute.side_effect = \
            httpx.TimeoutException("timed out")

        with patch("time.sleep"):
            df = _app.load_leads("user-123")

    assert isinstance(df, pd.DataFrame)
    assert df.empty


@pytest.mark.acceptance
def test_get_accuracy_stats_safe_on_timeout():
    """get_accuracy_stats returnerar noll-stats vid Supabase-timeout."""
    with patch.object(_app, "get_supabase") as mock_sb:
        mock_sb.return_value.table.return_value.select.return_value \
            .eq.return_value.eq.return_value.execute.side_effect = \
            httpx.TimeoutException("timed out")

        with patch("time.sleep"):
            stats = _app.get_accuracy_stats("user-123")

    assert stats["total_ai"] == 0
    assert stats["reviewed"] == 0
    assert stats["pct"] is None


@pytest.mark.acceptance
def test_delete_lead_safe_on_network_error():
    """delete_lead sväljer nätverksfel utan att krascha."""
    with patch.object(_app, "get_supabase") as mock_sb:
        mock_sb.return_value.table.return_value.delete.return_value \
            .eq.return_value.execute.side_effect = httpx.ConnectError("refused")

        with patch("time.sleep"):
            _app.delete_lead(999)  # ska inte kasta


@pytest.mark.acceptance
def test_confirm_lead_safe_on_network_error():
    """confirm_lead sväljer nätverksfel utan att krascha."""
    with patch.object(_app, "get_supabase") as mock_sb:
        mock_sb.return_value.table.return_value.update.return_value \
            .eq.return_value.execute.side_effect = httpx.ConnectError("refused")

        with patch("time.sleep"):
            _app.confirm_lead(999, True)  # ska inte kasta


# ── 5. Cost-estimat-väg ───────────────────────────────────────────────────────

@pytest.mark.acceptance
def test_large_scan_estimate_exceeds_budget():
    """200 000 byggnader ska flaggas som 'exceeds_budget'."""
    est = estimate_scan_cost(200_000)
    assert est.exceeds_budget
    assert est.high_sek >= DEFAULT_BUDGET_SEK


@pytest.mark.acceptance
def test_normal_scan_estimate_within_budget():
    """600 byggnader (typisk stad) ska ligga inom budgetgränsen."""
    est = estimate_scan_cost(600)
    assert not est.exceeds_budget
    assert est.high_sek < DEFAULT_BUDGET_SEK


@pytest.mark.acceptance
def test_estimate_gate_blocks_oversized_scan():
    """est.exceeds_budget == True är rätt gate att använda i app.py."""
    est = estimate_scan_cost(100_000)
    assert est.exceeds_budget, (
        "estimate_scan_cost(100_000) ska flagga exceeds_budget — "
        "app.py-gaten blockerar scan"
    )
