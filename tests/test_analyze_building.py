"""
Unit tests för _analyze_building — kör utan API-nycklar via mockad Anthropic-klient.

Testar att parsningslogiken och prompten ger rätt beteende för kända edge-cases:
  - Platta tak / EPDM-membran       → SOLAR=NO
  - Stående söm-metall               → SOLAR=NO
  - Tydliga solpaneler               → SOLAR=YES
  - Ej bostad (lager, garage)        → (False, False)
  - Osäkert/tvetydigt                → SOLAR=NO  ← detta var buggen
"""

import sys
from unittest.mock import MagicMock
import anthropic
import httpx
import pytest

# Stub out packages unavailable in test environment before importing scanner
for _mod in ("googlemaps", "streamlit", "supabase", "stripe", "folium",
             "streamlit_folium", "openpyxl"):
    sys.modules.setdefault(_mod, MagicMock())

from scanner import _analyze_building  # noqa: E402


def _make_client(response_text: str):
    """Returnera en mockad Anthropic-klient som svarar med response_text."""
    msg = MagicMock()
    msg.content = [MagicMock(text=response_text)]
    client = MagicMock()
    client.messages.create.return_value = msg
    return client


# ── Falska positiver som vi observerat (RED-fas) ───────────────────────────────

def test_flat_epdm_roof_is_not_solar():
    """Platt EPDM-tak ska INTE trigga SOLAR=YES."""
    client = _make_client(
        "The central building has a flat roof with a uniform dark EPDM membrane. "
        "No rectangular patches distinct from the roofing material are visible.\n"
        "HOUSE=YES\nSOLAR=NO"
    )
    is_house, has_solar, _unsure, _reasoning = _analyze_building(client, b"fake_image")
    assert is_house is True
    assert has_solar is False


def test_standing_seam_metal_roof_is_not_solar():
    """Stående söm-metalltak ska INTE trigga SOLAR=YES."""
    client = _make_client(
        "The roof has standing-seam metal cladding with regular linear ridges. "
        "The surface is uniform with no distinct rectangular patches.\n"
        "HOUSE=YES\nSOLAR=NO"
    )
    is_house, has_solar, _unsure, _reasoning = _analyze_building(client, b"fake_image")
    assert is_house is True
    assert has_solar is False


def test_uncertain_roof_defaults_to_no_solar():
    """Om modellen är osäker ska resultatet vara SOLAR=NO — inte SOLAR=YES."""
    client = _make_client(
        "The roof has some smooth areas that could possibly be panels or could "
        "be a modern roofing material. It is unclear from this image.\n"
        "HOUSE=YES\nSOLAR=NO"
    )
    is_house, has_solar, _unsure, _reasoning = _analyze_building(client, b"fake_image")
    assert is_house is True
    assert has_solar is False


def test_skylight_is_not_solar():
    """Takfönster/kupoler ska INTE trigga SOLAR=YES."""
    client = _make_client(
        "The tiled roof has two rectangular skylights. "
        "The rest of the roof is uniform clay tile.\n"
        "HOUSE=YES\nSOLAR=NO"
    )
    is_house, has_solar, _unsure, _reasoning = _analyze_building(client, b"fake_image")
    assert is_house is True
    assert has_solar is False


# ── Sanna positiver som vi inte vill missa ─────────────────────────────────────

def test_clear_solar_array_detected():
    """Tydliga rektangulära solpaneler ska detekteras."""
    client = _make_client(
        "The south-facing roof slope has a clearly visible rectangular array "
        "of dark blue photovoltaic panels covering roughly half the slope. "
        "The smooth, modular surface is distinctly different from the "
        "surrounding clay tiles.\n"
        "HOUSE=YES\nSOLAR=YES"
    )
    is_house, has_solar, _unsure, _reasoning = _analyze_building(client, b"fake_image")
    assert is_house is True
    assert has_solar is True


def test_partial_solar_array_detected():
    """Partiell panel-installation ska också detekteras."""
    client = _make_client(
        "Two small rectangular black patches occupy the east corner of the roof. "
        "They are significantly smoother and darker than the tile texture around them.\n"
        "HOUSE=YES\nSOLAR=YES"
    )
    is_house, has_solar, _unsure, _reasoning = _analyze_building(client, b"fake_image")
    assert is_house is True
    assert has_solar is True


# ── Ej bostad ──────────────────────────────────────────────────────────────────

def test_non_residential_building_rejected():
    """Lager/industribyggnad ska returnera (False, False)."""
    client = _make_client(
        "The central structure is a large warehouse with a flat bitumen roof.\n"
        "HOUSE=NO\nSOLAR=NO"
    )
    is_house, has_solar, _unsure, _reasoning = _analyze_building(client, b"fake_image")
    assert is_house is False
    assert has_solar is False


def test_solar_on_non_residential_not_counted():
    """Solpaneler på en ej-bostad ska inte räknas (HOUSE=NO → SOLAR=NO)."""
    client = _make_client(
        "This is clearly a commercial building with a solar array on the roof.\n"
        "HOUSE=NO\nSOLAR=NO"
    )
    is_house, has_solar, _unsure, _reasoning = _analyze_building(client, b"fake_image")
    assert is_house is False
    assert has_solar is False


# ── API-fel ─────────────────────────────────────────────────────────────────────

def test_api_error_returns_false_false():
    """Vid API-fel ska (False, False) returneras — aldrig krascha."""
    client = MagicMock()
    client.messages.create.side_effect = Exception("network error")
    is_house, has_solar, _unsure, _reasoning = _analyze_building(client, b"fake_image")
    assert is_house is False
    assert has_solar is False


def test_depleted_credits_raises_quota_error():
    """Slut på credits (HTTP 400 'credit balance is too low') ska resa
    APIQuotaExceededError — INTE sväljas tyst som '0 solcellstak'.

    Detta var rotorsaken till den veckolånga 0-leads-buggen: 400-felet föll
    igenom till tysta return (False, False) och scanen såg ut att bara sakna
    solceller, så inget larm-mail gick ut.
    """
    import httpx
    from scanner import APIQuotaExceededError

    response = httpx.Response(
        status_code=400,
        request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"),
    )
    err = anthropic.APIStatusError(
        "Your credit balance is too low to access the Anthropic API.",
        response=response,
        body=None,
    )
    client = MagicMock()
    client.messages.create.side_effect = err

    with pytest.raises(APIQuotaExceededError):
        _analyze_building(client, b"fake_image")


def test_other_400_error_still_swallowed():
    """Ett vanligt 400-fel (inte credit-relaterat) ska INTE resa quota-fel —
    bara loggas och returnera (False, False) som tidigare."""
    import httpx

    response = httpx.Response(
        status_code=400,
        request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"),
    )
    err = anthropic.APIStatusError(
        "messages.0.content.0.image: invalid base64 data",
        response=response,
        body=None,
    )
    client = MagicMock()
    client.messages.create.side_effect = err

    is_house, has_solar, _unsure, _reasoning = _analyze_building(client, b"fake_image")
    assert is_house is False
    assert has_solar is False
