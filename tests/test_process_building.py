"""
Enhetstester för två kritiska fixar i scanner.py:

1. addr-fallback i _get_osm_buildings:
   - Bugg: byggnader utan addr-nod droppades (0 leads).
   - Fix: använd f"{lat:.5f},{lng:.5f}" som placeholder-adress när ingen
     adress hittas via OSM-tagg eller närliggande addr-nod.

2. SOLAR=UNSURE → needs_review i _process_building:
   - När _analyze_building returnerar (True, False, True, "reasoning")
     ska lead skapas med needs_review=True och ai_reasoning satt.
   - SOLAR=YES → needs_review=False, normal lead.
   - SOLAR=NO  → ingen lead returneras (return None).
"""

import sys
from unittest.mock import MagicMock, patch

# Stub out packages unavailable in test environment before importing scanner
for _mod in ("googlemaps", "streamlit", "supabase", "stripe", "folium",
             "streamlit_folium", "openpyxl"):
    sys.modules.setdefault(_mod, MagicMock())

from scanner import _get_osm_buildings, _process_building  # noqa: E402


# ── Helpers ────────────────────────────────────────────────────────────────────

_BBOX = (55.5, 13.0, 55.6, 13.1)


def _make_way(lat=55.55, lon=13.05, tags=None, osm_id=1):
    """Return a minimal OSM way element with geometry and center.

    The polygon is sized to produce ~120 m² — well within the
    MIN_BUILDING_AREA_M2=40 / MAX_BUILDING_AREA_M2=400 filter.
    """
    # d chosen so that (2*d*111000)*(2*d*111000*cos(lat)) ≈ 120 m²
    d = 0.0000656
    geom = [
        {"lat": lat - d, "lon": lon - d},
        {"lat": lat + d, "lon": lon - d},
        {"lat": lat + d, "lon": lon + d},
        {"lat": lat - d, "lon": lon + d},
        {"lat": lat - d, "lon": lon - d},
    ]
    return {
        "type": "way",
        "id": osm_id,
        "geometry": geom,
        "tags": tags or {"building": "house"},
    }


def _make_building_dict(lat=55.55, lng=13.05, address="Testvagen 1", osm_id="42"):
    """Return a minimal building dict as returned by _get_osm_buildings."""
    return {
        "lat": lat,
        "lng": lng,
        "address": address,
        "osm_id": osm_id,
        "building_type": "house",
        "zoom": 20,
        "area_m2": 120,
    }


# ── addr-fallback tests ────────────────────────────────────────────────────────

def test_addr_fallback_when_no_addr_nodes():
    """Byggnad utan addr-nod i närheten ska inte droppas — koordinater används som adress."""
    fake_buildings = [_make_way(lat=55.55, lon=13.05, osm_id=1)]
    # No addr nodes at all in second _overpass call
    with patch("scanner._overpass", side_effect=[fake_buildings, []]):
        result = _get_osm_buildings(*_BBOX)

    assert len(result) == 1, "Byggnad utan adress ska inkluderas med koordinat-fallback"
    addr = result[0]["address"]
    # Placeholder should be "lat,lng" formatted to 5 decimal places
    assert "," in addr
    assert not any(c.isalpha() for c in addr), (
        f"Placeholder-adress ska bara innehålla siffror och komma, fick: {addr!r}"
    )
    # Verify format matches f"{lat:.5f},{lng:.5f}"
    lat_str, lng_str = addr.split(",")
    assert float(lat_str) == pytest_approx(55.55, abs=0.001)
    assert float(lng_str) == pytest_approx(13.05, abs=0.001)


def test_addr_fallback_format_matches_spec():
    """Placeholder-adressen ska ha formatet f'{lat:.5f},{lng:.5f}'.

    Centroiden beräknas som medelvärdet av geometripunkterna, vilket kan
    avvika marginellt från det nominella mitten beroende på d-värdet.
    Testet verifierar formatet (5 decimaler, komma-separator) snarare än
    exakt koordinatmatchning.
    """
    lat, lon = 55.12345, 13.98765
    way = _make_way(lat=lat, lon=lon, osm_id=2)
    # Recompute what the centroid will actually be (same logic as scanner)
    geom = way["geometry"]
    lats = [p["lat"] for p in geom]
    lons = [p["lon"] for p in geom]
    centroid_lat = sum(lats) / len(lats)
    centroid_lng = sum(lons) / len(lons)
    expected = f"{centroid_lat:.5f},{centroid_lng:.5f}"

    fake_buildings = [way]
    with patch("scanner._overpass", side_effect=[fake_buildings, []]):
        result = _get_osm_buildings(*_BBOX)

    assert len(result) == 1
    addr = result[0]["address"]
    # Verify the format: only digits, dots, and a comma separator
    assert "," in addr
    assert not any(c.isalpha() for c in addr), f"Adress ska bara ha siffror, fick: {addr!r}"
    # Verify it matches the expected f"{lat:.5f},{lng:.5f}" pattern for the centroid
    assert addr == expected, f"Förväntade {expected!r}, fick {addr!r}"


def test_addr_from_building_tags_used_when_present():
    """När building-taggen innehåller adress ska den användas (ingen fallback)."""
    fake_buildings = [
        _make_way(lat=55.55, lon=13.05, osm_id=3, tags={
            "building": "house",
            "addr:street": "Solgatan",
            "addr:housenumber": "7",
            "addr:city": "Malmö",
        })
    ]
    with patch("scanner._overpass", side_effect=[fake_buildings, []]):
        result = _get_osm_buildings(*_BBOX)

    assert len(result) == 1
    assert "Solgatan 7" in result[0]["address"]


def test_addr_from_nearby_addr_node_used_when_present():
    """Addr-nod nära byggnaden ska ge adress (ingen koordinat-fallback)."""
    lat, lon = 55.55, 13.05
    fake_buildings = [_make_way(lat=lat, lon=lon, osm_id=4)]
    # An addr node very close to the building centroid
    addr_node = {
        "type": "node",
        "lat": lat + 0.00005,  # ~5.5 m away — within ADDRESS_SNAP_RADIUS_M=25
        "lon": lon,
        "tags": {"addr:street": "Rosengatan", "addr:housenumber": "3"},
    }
    with patch("scanner._overpass", side_effect=[fake_buildings, [addr_node]]):
        result = _get_osm_buildings(*_BBOX)

    assert len(result) == 1
    assert "Rosengatan 3" in result[0]["address"]


def test_multiple_buildings_all_returned_without_addr_nodes():
    """Flera byggnader utan adress ska alla returneras med koordinat-placeholders."""
    fake_buildings = [
        _make_way(lat=55.55, lon=13.05, osm_id=10),
        _make_way(lat=55.56, lon=13.06, osm_id=11),
        _make_way(lat=55.57, lon=13.07, osm_id=12),
    ]
    with patch("scanner._overpass", side_effect=[fake_buildings, []]):
        result = _get_osm_buildings(*_BBOX)

    assert len(result) == 3, "Alla tre byggnader ska returneras trots avsaknad av adress"
    for b in result:
        addr = b["address"]
        assert "," in addr
        assert not any(c.isalpha() for c in addr)


# ── SOLAR=UNSURE → needs_review tests ─────────────────────────────────────────

def _make_process_building_patches(analyze_return, has_extra_solar=False, img_bytes=b"fake_image"):
    """Return a context-manager stack that stubs out _fetch_satellite,
    _analyze_building, and _has_extra_solar_nearby for _process_building tests."""
    from contextlib import ExitStack
    stack = ExitStack()
    stack.enter_context(patch("scanner._fetch_satellite", return_value=img_bytes))
    stack.enter_context(patch("scanner._analyze_building", return_value=analyze_return))
    stack.enter_context(patch("scanner._has_extra_solar_nearby", return_value={
        "extra_solar_found": has_extra_solar,
        "solar_locations": [],
        "villa_nearby": False,
    }))
    return stack


def test_solar_unsure_creates_lead_with_needs_review_true():
    """SOLAR=UNSURE: _analyze_building returnerar (True, False, True, 'reasoning')
    → lead ska skapas med needs_review=True."""
    building = _make_building_dict(address="Solvägen 5, Malmö")
    analyze_return = (True, False, True, "The roof has some ambiguous smooth patches.")

    with _make_process_building_patches(analyze_return):
        lead = _process_building(building, google_key="fake", anthropic_client=MagicMock())

    assert lead is not None, "SOLAR=UNSURE ska ge ett lead, inte None"
    assert lead.needs_review is True
    assert lead.ai_reasoning == "The roof has some ambiguous smooth patches."
    assert lead.confidence == 0.50


def test_solar_yes_creates_lead_with_needs_review_false():
    """SOLAR=YES: _analyze_building returnerar (True, True, False, 'reasoning')
    → lead ska skapas med needs_review=False."""
    building = _make_building_dict(address="Pannvägen 2, Lund")
    analyze_return = (True, True, False, "Clearly visible rectangular solar array.")

    with _make_process_building_patches(analyze_return):
        lead = _process_building(building, google_key="fake", anthropic_client=MagicMock())

    assert lead is not None, "SOLAR=YES ska ge ett lead"
    assert lead.needs_review is False
    assert lead.confidence == 0.90


def test_solar_no_returns_none():
    """SOLAR=NO: _analyze_building returnerar (True, False, False, 'reasoning')
    → _process_building ska returnera None (ingen lead)."""
    building = _make_building_dict(address="Skuggvägen 8, Vellinge")
    analyze_return = (True, False, False, "Flat EPDM roof, no panels visible.")

    with _make_process_building_patches(analyze_return):
        lead = _process_building(building, google_key="fake", anthropic_client=MagicMock())

    assert lead is None, "SOLAR=NO ska inte ge ett lead"


def test_house_no_returns_none():
    """HOUSE=NO: _analyze_building returnerar (False, False, False, '')
    → ingen lead oavsett."""
    building = _make_building_dict(address="Industrigatan 1, Malmö")
    analyze_return = (False, False, False, "Large warehouse, not a residential home.")

    with _make_process_building_patches(analyze_return):
        lead = _process_building(building, google_key="fake", anthropic_client=MagicMock())

    assert lead is None, "HOUSE=NO ska inte ge ett lead"


def test_solar_unsure_reasoning_stored_in_lead():
    """ai_reasoning från _analyze_building ska sparas på lead-objektet."""
    building = _make_building_dict()
    reasoning_text = "Possibly smooth patches on the south slope, hard to tell."
    analyze_return = (True, False, True, reasoning_text)

    with _make_process_building_patches(analyze_return):
        lead = _process_building(building, google_key="fake", anthropic_client=MagicMock())

    assert lead is not None
    assert lead.ai_reasoning == reasoning_text


def test_solar_unsure_address_kept_as_is_if_already_readable():
    """SOLAR=UNSURE med läsbar adress ska inte triggera reverse geocoding."""
    building = _make_building_dict(address="Björkvägen 12, Malmö")
    analyze_return = (True, False, True, "Ambiguous roof texture.")

    with _make_process_building_patches(analyze_return):
        lead = _process_building(building, google_key="fake", anthropic_client=MagicMock())

    assert lead is not None
    # The address contains alphabetical chars so no reverse geocoding should happen
    assert lead.address == "Björkvägen 12, Malmö"


# ── Import guard ───────────────────────────────────────────────────────────────

try:
    from pytest import approx as pytest_approx
except ImportError:
    # Fallback in case pytest.approx is not found at module level during collection
    def pytest_approx(val, abs=1e-6):  # noqa: A002
        return val
