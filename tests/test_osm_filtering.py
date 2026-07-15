"""
Enhetstester för OSM-filtrering i scan_area_osm.

Verifierar att scannern INTE returnerar leads för:
  - generator:source=solar utan building-tagg (solcellsparker, gatlyktor)
  - power=generator + generator:source=solar (utility-scale, t.ex. Sturup)

Och att den RETURNERAR leads för:
  - roof:solar_panel=yes (taksolceller, mest tillförlitlig tagg)
  - generator:source=solar med building-tagg (tak på byggnad med solar)
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


def test_solar_park_without_building_tag_excluded():
    """Solcellspark (Sturup-fallet) ska inte ge ett lead.

    generator:source=solar utan building-tagg = markmonterad anläggning.
    """
    fake_elements = [
        _osm_way(tags={"generator:source": "solar", "landuse": "industrial"}),
        _osm_node(tags={"generator:source": "solar"}),
    ]
    with patch("scanner._overpass", return_value=fake_elements):
        leads = scan_area_osm(*_BBOX)
    assert leads == [], "Solcellspark utan building-tagg ska filtreras bort"


def test_utility_generator_excluded():
    """power=generator + generator:source=solar = storskalig anläggning, ska filtreras."""
    fake_elements = [
        _osm_way(tags={"power": "generator", "generator:source": "solar"}),
    ]
    with patch("scanner._overpass", return_value=fake_elements):
        leads = scan_area_osm(*_BBOX)
    assert leads == [], "Utility-scale generator ska filtreras bort"


def test_roof_solar_panel_yes_included():
    """roof:solar_panel=yes är den tillförlitligaste taggen — ska alltid inkluderas."""
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


def test_generator_solar_with_building_tag_included():
    """generator:source=solar på en byggnad (building=*) = takinstallation → inkludera."""
    fake_elements = [
        _osm_way(tags={
            "generator:source": "solar",
            "building": "house",
            "addr:street": "Myrtengatan",
            "addr:housenumber": "11",
            "addr:city": "Malmö",
        })
    ]
    with patch("scanner._overpass", return_value=fake_elements):
        leads = scan_area_osm(*_BBOX)
    assert len(leads) == 1
    assert "Myrtengatan 11" in leads[0].address


def test_non_residential_building_with_solar_excluded():
    """Reningsverk, lager, kyrkor etc. med solpaneler ska inte bli leads.

    VA SYD Sjölunda (Spillepengsgatan 15) är taggat roof:solar_panel=yes
    men building=industrial — ska filtreras bort.
    """
    fake_elements = [
        _osm_way(tags={
            "roof:solar_panel": "yes",
            "building": "industrial",
            "name": "Sjölunda avloppsreningsverk",
        }),
        _osm_way(tags={
            "roof:solar_panel": "yes",
            "building": "church",
        }),
    ]
    with patch("scanner._overpass", return_value=fake_elements):
        leads = scan_area_osm(*_BBOX)
    assert leads == [], "Industribyggnader och kyrkor ska filtreras bort"


def test_duplicate_coordinates_deduplicated():
    """Samma koordinat från flera OSM-element ska bara ge ett lead."""
    fake_elements = [
        _osm_node(lat=55.55, lon=13.05, tags={"roof:solar_panel": "yes"}),
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


def test_roof_solar_without_building_tag_needs_review():
    """roof:solar_panel=yes UTAN building-tagg kan inte villa-verifieras —
    kan sitta på en industrihall (B2B). Ska levereras, men flaggad till
    Granska-fliken istället för rakt till säljlistan."""
    fake_elements = [
        _osm_way(tags={"roof:solar_panel": "yes"}),  # ingen building-tagg
    ]
    with patch("scanner._overpass", return_value=fake_elements):
        leads = scan_area_osm(*_BBOX)
    assert len(leads) == 1
    assert leads[0].needs_review is True
    assert "villa" in leads[0].ai_reasoning.lower()


def test_roof_solar_with_villa_building_tag_not_flagged():
    """roof:solar_panel=yes PÅ verifierad villa (building=house) → ren lead,
    ingen granskning behövs."""
    fake_elements = [
        _osm_way(tags={"roof:solar_panel": "yes", "building": "house"}),
    ]
    with patch("scanner._overpass", return_value=fake_elements):
        leads = scan_area_osm(*_BBOX)
    assert len(leads) == 1
    assert leads[0].needs_review is False
    assert leads[0].building_type == "house"
