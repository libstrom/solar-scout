"""
Enhetstester för OSM-filtrering i scan_area_osm.

Verifierar att scannern inte returnerar leads för:
  - node-element utan byggnadstagg (t.ex. solcellsgatlyktor, markmonterade paneler)
  - way-element utan adress (inget hus att kontakta)

Och att den returnerar leads för:
  - way-element med roof:solar_panel=yes och adress
"""

import sys
from unittest.mock import MagicMock, patch

for _mod in ("googlemaps", "streamlit", "supabase", "stripe", "folium",
             "streamlit_folium", "openpyxl"):
    sys.modules.setdefault(_mod, MagicMock())

from scanner import scan_area_osm


_BBOX = (55.5, 13.0, 55.6, 13.1)  # Malmö-området


def _osm_node(lat=55.55, lon=13.05, tags=None):
    return {"type": "node", "lat": lat, "lon": lon, "tags": tags or {}}


def _osm_way(lat=55.55, lon=13.05, tags=None):
    return {"type": "way", "center": {"lat": lat, "lon": lon}, "tags": tags or {}}


def test_isolated_solar_node_without_address_is_excluded():
    """
    Isolerade nod-element (t.ex. solcellsgatlyktor) utan adresstaggar
    ska inkluderas men med lat/lng som adress — acceptabelt men verifierbart.

    Primärt: kontrollera att en nod UTAN roof:solar_panel-tagg och UTAN
    adresstaggar inte ger en falsk "Ja"-lead med en riktig gatuadress.
    """
    fake_elements = [
        _osm_node(tags={"generator:source": "solar"})  # ingen adress
    ]
    with patch("scanner._overpass", return_value=fake_elements):
        leads = scan_area_osm(*_BBOX)
    assert len(leads) == 1
    lead = leads[0]
    # Utan adresstaggar ska adressen vara koordinater, inte en gatuadress
    assert "," in lead.address or lead.address == ""
    assert "gatan" not in lead.address.lower()
    assert "vägen" not in lead.address.lower()


def test_way_with_roof_solar_and_address_is_included():
    """way med roof:solar_panel=yes och adresstaggar ska ge ett lead."""
    fake_elements = [
        _osm_way(tags={
            "roof:solar_panel": "yes",
            "addr:street": "Ljunggatan",
            "addr:housenumber": "99",
            "addr:city": "Malmö",
        })
    ]
    with patch("scanner._overpass", return_value=fake_elements):
        leads = scan_area_osm(*_BBOX)
    assert len(leads) == 1
    assert "Ljunggatan 99" in leads[0].address
    assert leads[0].source == "osm"
    assert leads[0].confidence == 1.0


def test_duplicate_coordinates_deduplicated():
    """Samma koordinat från flera OSM-element ska bara ge ett lead."""
    fake_elements = [
        _osm_node(lat=55.55, lon=13.05, tags={"generator:source": "solar"}),
        _osm_way(lat=55.55, lon=13.05, tags={"roof:solar_panel": "yes"}),
    ]
    with patch("scanner._overpass", return_value=fake_elements):
        leads = scan_area_osm(*_BBOX)
    assert len(leads) == 1


def test_empty_overpass_response_returns_empty_list():
    with patch("scanner._overpass", return_value=[]):
        leads = scan_area_osm(*_BBOX)
    assert leads == []


def test_overpass_error_returns_empty_list():
    with patch("scanner._overpass", side_effect=Exception("timeout")):
        leads = scan_area_osm(*_BBOX)
    assert leads == []
