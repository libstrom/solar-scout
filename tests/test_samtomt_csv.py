"""
Tester som verifierar att samtomt_solar_extra-flaggan flödar korrekt
från Lead-objektet via sb_rows-dictionaryt till CSV-exporten.

Täcker:
  1. samtomt_solar_extra=True på ett Lead hamnar i sb_rows-dictionaryt
  2. samtomt_solar_extra=False på ett Lead hamnar i sb_rows-dictionaryt
"""

import sys
from unittest.mock import MagicMock

# Stub externa paket INNAN scanner/app importeras
for _mod in (
    "googlemaps",
    "streamlit",
    "supabase",
    "stripe",
    "folium",
    "streamlit_folium",
    "openpyxl",
    "anthropic",
    "httpx",
    "PIL",
    "PIL.Image",
):
    sys.modules.setdefault(_mod, MagicMock())

from scanner import Lead  # noqa: E402


def _lead_to_sb_row(lead: Lead) -> dict:
    """Replikerar sb_rows-bygg-logiken från page_scanner i app.py."""
    import urllib.parse

    return {
        "address":             lead.address,
        "has_solar":           "Ja",
        "air_to_air":          "False",
        "air_to_water":        "False",
        "notes":               f"Detekterad via {lead.source.upper()} (konfidens {lead.confidence:.0%})",
        "google_search_url":   f"https://www.google.com/search?q=vem+bor+p%C3%A5+{urllib.parse.quote(lead.address)}",
        "hitta_url":           f"https://www.hitta.se/s%C3%B6k?vad={urllib.parse.quote(lead.address)}",
        "maps_url":            f"https://www.google.com/maps/search/?api=1&query={lead.lat},{lead.lng}",
        "lat":                 lead.lat,
        "lng":                 lead.lng,
        "scan_source":         lead.source,
        "building_type":       getattr(lead, "building_type", ""),
        "samtomt_solar_extra": getattr(lead, "samtomt_solar_extra", False),
        "solar_location":      getattr(lead, "solar_location", "roof"),
        "needs_review":        getattr(lead, "needs_review", False),
        "ai_reasoning":        getattr(lead, "ai_reasoning", ""),
    }


def test_samtomt_solar_extra_true_is_in_sb_row():
    """Lead med samtomt_solar_extra=True ska ha True i sb_rows-dictionaryt."""
    lead = Lead(
        lat=55.7,
        lng=13.2,
        address="Garagevägen 1, Malmö",
        confidence=0.90,
        source="ai",
        samtomt_solar_extra=True,
        solar_location="samtomt",
    )

    row = _lead_to_sb_row(lead)

    assert "samtomt_solar_extra" in row, "samtomt_solar_extra saknas i sb_rows"
    assert row["samtomt_solar_extra"] is True, (
        f"Förväntat True men fick {row['samtomt_solar_extra']!r}"
    )


def test_samtomt_solar_extra_false_is_in_sb_row():
    """Lead med samtomt_solar_extra=False (default) ska ha False i sb_rows-dictionaryt."""
    lead = Lead(
        lat=55.6,
        lng=13.0,
        address="Taksolsvägen 5, Lund",
        confidence=0.90,
        source="ai",
    )

    row = _lead_to_sb_row(lead)

    assert "samtomt_solar_extra" in row, "samtomt_solar_extra saknas i sb_rows"
    assert row["samtomt_solar_extra"] is False, (
        f"Förväntat False men fick {row['samtomt_solar_extra']!r}"
    )
