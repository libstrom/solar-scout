"""
Regression: bildkälle-fallback när Google Static Maps når kvot/billing-väggen.

Rotorsak (observerad i produktion 2026-05): en scan av ~578 byggnader hamrade
Google Static Maps tills kvoten sprängdes. _fetch_google_static reste då
APIQuotaExceededError som propagerade upp och DÖDADE hela scanen — trots att
den gratis, lagringsbara LM WMS-fallbacken (minkarta) fanns kvar.

Önskat beteende: Google-kvotfel ska bryta kretsen och degradera till LM WMS
för resten av scanen, inte avbryta. Ägaren larmas separat via annan väg.
"""

import sys
from unittest.mock import MagicMock, patch

for _mod in ("googlemaps", "streamlit", "supabase", "stripe", "folium",
             "streamlit_folium", "openpyxl"):
    sys.modules.setdefault(_mod, MagicMock())

import scanner  # noqa: E402
from scanner import (  # noqa: E402
    _fetch_satellite,
    APIQuotaExceededError,
    reset_image_source_breakers,
)


def setup_function(_fn):
    reset_image_source_breakers()


def test_google_quota_falls_back_to_lm_wms():
    """När Google reser kvotfel ska _fetch_satellite returnera LM WMS-bilden
    i stället för att låta felet propagera."""
    with patch("scanner._fetch_google_static",
               side_effect=APIQuotaExceededError("Google Static Maps", "403 billing")), \
         patch("scanner._fetch_lm_wms", return_value=b"lm_image"):
        img = _fetch_satellite("gkey", 57.0, 14.0)
    assert img == b"lm_image"


def test_google_quota_sets_circuit_breaker():
    """Kvotfelet på Google ska sätta breakern så resten av scanen hoppar Google."""
    with patch("scanner._fetch_lm_wms", return_value=None), \
         patch("scanner._fetch_google_static",
               side_effect=APIQuotaExceededError("Google Static Maps", "429 rate limit")), \
         patch("scanner._fetch_mapbox", return_value=b"mapbox_image"):
        _fetch_satellite("gkey", 57.0, 14.0)
    assert scanner._google_exhausted.is_set()


def test_breaker_skips_google_on_subsequent_calls():
    """När breakern är satt ska Google INTE anropas igen — direkt till Mapbox."""
    scanner._google_exhausted.set()
    google_mock = MagicMock()
    with patch("scanner._fetch_lm_wms", return_value=None), \
         patch("scanner._fetch_google_static", google_mock), \
         patch("scanner._fetch_mapbox", return_value=b"mapbox_image"):
        img = _fetch_satellite("gkey", 57.0, 14.0, mapbox_key="mbkey")
    assert img == b"mapbox_image"
    google_mock.assert_not_called()


def test_reset_clears_breaker():
    """reset_image_source_breakers ska nollställa breakern inför ny scan."""
    scanner._google_exhausted.set()
    reset_image_source_breakers()
    assert not scanner._google_exhausted.is_set()


def test_lm_minkarta_is_primary():
    """LM minkarta ska vara primärkälla — gratis, ingen nyckel, samma 0.16m/px."""
    google_mock = MagicMock()
    with patch("scanner._fetch_lm_wms", return_value=b"lm_image"), \
         patch("scanner._fetch_google_static", google_mock):
        img = _fetch_satellite("gkey", 57.0, 14.0)
    assert img == b"lm_image"
    google_mock.assert_not_called()


def test_google_fallback_when_lm_fails():
    """Om LM minkarta misslyckas ska Google användas som fallback."""
    with patch("scanner._fetch_lm_wms", return_value=None), \
         patch("scanner._fetch_google_static", return_value=b"google_image"):
        img = _fetch_satellite("gkey", 57.0, 14.0)
    assert img == b"google_image"
