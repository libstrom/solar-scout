"""
Enhetstester för lm_wms_url i scanner.py.

Verifierar att funktionen returnerar en icke-tom URL-sträng som innehåller
lat/lng-koordinaterna i BBOX-parametern.
"""

import sys
from unittest.mock import MagicMock

# Stub out heavy packages unavailable in the test environment
for _mod in ("googlemaps", "streamlit", "supabase", "stripe", "folium",
             "streamlit_folium", "openpyxl", "anthropic"):
    sys.modules.setdefault(_mod, MagicMock())

from scanner import lm_wms_url  # noqa: E402


def test_lm_wms_url_returns_nonempty_string():
    """lm_wms_url ska returnera en icke-tom sträng för givna koordinater."""
    url = lm_wms_url(lat=59.3293, lng=18.0686)
    assert isinstance(url, str), "Returnvärdet ska vara en sträng"
    assert len(url) > 0, "Returnvärdet ska inte vara tomt"


def test_lm_wms_url_contains_lat_lng():
    """URL:en ska innehålla lat- och lng-koordinaterna i BBOX-parametern."""
    lat, lng = 55.6050, 13.0038
    url = lm_wms_url(lat=lat, lng=lng)
    # The BBOX parameter encodes coordinates as floats — verify both values appear
    assert str(lng)[:6] in url, f"Longitude {lng} ska finnas i URL:en: {url}"
    assert str(lat)[:6] in url, f"Latitude {lat} ska finnas i URL:en: {url}"


def test_lm_wms_url_points_to_lantmateriet():
    """URL:en ska peka mot Lantmäteriet minkarta WMS-endpoint."""
    url = lm_wms_url(lat=57.7089, lng=11.9746)
    assert "minkarta.lantmateriet.se" in url, (
        f"URL ska peka mot minkarta.lantmateriet.se, fick: {url}"
    )
    assert "REQUEST=GetMap" in url, "URL ska innehålla REQUEST=GetMap"


def test_lm_wms_url_contains_image_dimensions():
    """URL:en ska ange 640x480 som bildstorlek (standardvärden)."""
    url = lm_wms_url(lat=59.0, lng=17.0)
    assert "WIDTH=640" in url, f"URL ska innehålla WIDTH=640, fick: {url}"
    assert "HEIGHT=480" in url, f"URL ska innehålla HEIGHT=480, fick: {url}"


def test_lm_wms_url_custom_dimensions():
    """Anpassade dimensioner ska reflekteras i URL:en."""
    url = lm_wms_url(lat=59.0, lng=17.0, width=800, height=600)
    assert "WIDTH=800" in url
    assert "HEIGHT=600" in url


def test_lm_wms_url_different_coordinates_give_different_urls():
    """Olika koordinater ska ge olika URL:er (BBOX ska variera)."""
    url1 = lm_wms_url(lat=55.0, lng=13.0)
    url2 = lm_wms_url(lat=59.0, lng=18.0)
    assert url1 != url2, "Olika koordinater ska producera olika URL:er"
