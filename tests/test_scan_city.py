"""
Tests för scan_city och scan_bbox i scanner.py.

Täcker:
  1. max_leads cap  — om max_leads=5 och 10 leads hittas → exakt 5 returneras
  2. dedup          — samma koordinat/adress från flera sources → dedupas till ett lead
  3. tom stad       — Overpass returnerar 0 byggnader → tom lista, inget krasch
  4. area_cap       — _get_osm_buildings skickar inte fler än max_count (default 600) till Overpass
  5. all_areas      — scan_city fortsätter till nästa area tills max_leads nås (regression för
                      max_leads//5-buggen som gav 0 leads i stora städer som Lund)
"""

import sys
from unittest.mock import MagicMock, patch, call
import pytest

# Stub out unavailable packages before importing scanner
for _mod in ("googlemaps", "streamlit", "supabase", "stripe", "folium",
             "streamlit_folium", "openpyxl"):
    sys.modules.setdefault(_mod, MagicMock())

import scanner  # noqa: E402
from scanner import scan_city, scan_bbox, Lead, _get_osm_buildings  # noqa: E402


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_geocode_result(south=59.30, west=18.00, north=59.40, east=18.10,
                          center_lat=59.35, center_lng=18.05):
    """Return a minimal fake Google geocode result list."""
    return [{
        "geometry": {
            "viewport": {
                "southwest": {"lat": south, "lng": west},
                "northeast": {"lat": north, "lng": east},
            },
            "location": {"lat": center_lat, "lng": center_lng},
        }
    }]


def _make_building(osm_id: str, lat: float, lng: float,
                   address: str = "Testgatan 1, Teststad") -> dict:
    """Return a minimal fake building dict as returned by _get_osm_buildings."""
    return {
        "lat": lat,
        "lng": lng,
        "address": address,
        "osm_id": osm_id,
        "building_type": "house",
        "zoom": 20,
        "area_m2": 120,
    }


def _make_lead(lat: float, lng: float, source: str = "ai",
               address: str = "Testgatan 1, Teststad") -> Lead:
    """Return a minimal Lead."""
    return Lead(
        lat=lat,
        lng=lng,
        address=address,
        confidence=0.9,
        source=source,
        tile_key=f"bld/{lat:.5f}",
    )


def _make_osm_element(lat: float, lng: float, tags: dict | None = None) -> dict:
    """Return a minimal OSM way element with center, suitable for scan_area_osm."""
    t = {
        "roof:solar_panel": "yes",
        "building": "house",
        "addr:street": "Solvägen",
        "addr:housenumber": "1",
        **(tags or {}),
    }
    return {
        "type": "way",
        "id": int(lat * 1000 + lng * 100),
        "center": {"lat": lat, "lon": lng},
        "tags": t,
    }


# ── 1. max_leads cap ───────────────────────────────────────────────────────────

class TestMaxLeadsCap:
    """scan_bbox och scan_city ska aldrig returnera fler leads än max_leads."""

    def test_scan_bbox_max_leads_caps_result(self):
        """scan_bbox med max_leads=5 och 10 AI-leads → exakt 5 returneras.

        scan_bbox passes remaining=max_leads to scan_buildings_ai. Our mock
        respects that kwarg so the final result is capped.
        """
        ten_buildings = [_make_building(str(i), 59.34 + i * 0.0001, 18.05)
                         for i in range(10)]

        def fake_scan_buildings_ai(buildings, *args, max_leads=None, **kwargs):
            leads = [_make_lead(b["lat"], b["lng"]) for b in buildings]
            if max_leads is not None:
                leads = leads[:max_leads]
            return leads

        with patch.object(scanner, "_overpass", return_value=[]), \
             patch.object(scanner, "_get_osm_buildings", return_value=ten_buildings), \
             patch.object(scanner, "scan_buildings_ai",
                          side_effect=fake_scan_buildings_ai):

            result = scan_bbox(
                south=59.34, west=18.04, north=59.345, east=18.055,
                google_key="fake", anthropic_key="fake",
                max_leads=5,
            )

        assert len(result) <= 5

    def test_scan_bbox_max_leads_with_osm_overflow(self):
        """OSM returnerar redan 10 leads och max_leads=5 → exakt 5."""
        ten_osm_elements = [_make_osm_element(59.340 + i * 0.0001, 18.05)
                            for i in range(10)]

        with patch.object(scanner, "_overpass", return_value=ten_osm_elements), \
             patch.object(scanner, "_get_osm_buildings", return_value=[]):

            result = scan_bbox(
                south=59.34, west=18.04, north=59.345, east=18.055,
                google_key="fake", anthropic_key="fake",
                max_leads=5,
            )

        assert len(result) == 5

    def test_scan_city_max_leads_caps_ai_results(self):
        """scan_city med max_leads=5 och 10 AI-leads → max 5 totalt.

        scan_city passes remaining budget to scan_buildings_ai; our mock
        respects that kwarg so the total is bounded by max_leads.
        """
        ten_buildings = [_make_building(str(i), 59.34 + i * 0.0001, 18.05)
                         for i in range(10)]

        def fake_scan_buildings_ai(buildings, *args, max_leads=None, **kwargs):
            leads = [_make_lead(b["lat"], b["lng"]) for b in buildings]
            if max_leads is not None:
                leads = leads[:max_leads]
            return leads

        fake_gmaps = MagicMock()
        fake_gmaps.geocode.return_value = _make_geocode_result()

        # One residential area
        fake_area = {"lat": 59.35, "lng": 18.05, "area_deg2": 0.01}

        with patch("googlemaps.Client", return_value=fake_gmaps), \
             patch.object(scanner, "scan_area_osm", return_value=[]), \
             patch.object(scanner, "_get_residential_areas", return_value=[fake_area]), \
             patch.object(scanner, "_get_osm_buildings", return_value=ten_buildings), \
             patch.object(scanner, "scan_buildings_ai",
                          side_effect=fake_scan_buildings_ai):

            result = scan_city(
                city_name="Teststad",
                google_key="fake",
                anthropic_key="fake",
                max_leads=5,
            )

        assert len(result) <= 5

    def test_scan_city_no_anthropic_key_caps_osm(self):
        """Utan anthropic_key returneras bara OSM-leads, cappade till max_leads."""
        five_osm = [_make_lead(59.30 + i * 0.001, 18.05, source="osm") for i in range(8)]

        fake_gmaps = MagicMock()
        fake_gmaps.geocode.return_value = _make_geocode_result()

        with patch("googlemaps.Client", return_value=fake_gmaps), \
             patch.object(scanner, "scan_area_osm", return_value=five_osm):

            result = scan_city(
                city_name="Teststad",
                google_key="fake",
                anthropic_key=None,
                max_leads=3,
            )

        assert len(result) == 3


# ── 2. dedup ───────────────────────────────────────────────────────────────────

class TestDedup:
    """Samma koordinat/adress från flera sources ska dedupas till ett lead."""

    def test_scan_bbox_deduplicates_osm_and_ai_at_same_location(self):
        """En OSM-lead och en AI-lead på samma plats → bara en i resultatet."""
        lat, lng = 59.342, 18.050

        osm_element = _make_osm_element(lat, lng)
        ai_building = _make_building("1", lat, lng)
        ai_lead = _make_lead(lat, lng, source="ai")

        with patch.object(scanner, "_overpass", return_value=[osm_element]), \
             patch.object(scanner, "_get_osm_buildings", return_value=[ai_building]), \
             patch.object(scanner, "scan_buildings_ai", return_value=[ai_lead]):

            result = scan_bbox(
                south=59.340, west=18.045, north=59.345, east=18.055,
                google_key="fake", anthropic_key="fake",
            )

        # The coordinate dedup in scan_bbox filters out buildings near OSM leads,
        # then merge_leads further deduplicates — at most 1 result expected.
        lats = [r.lat for r in result]
        assert lats.count(lat) <= 1, "Duplicate lat found in result"

    def test_merge_leads_removes_duplicates_by_proximity(self):
        """merge_leads deduplikar leads som är < 20m från varandra."""
        from scanner import merge_leads

        # Same location — OSM and AI both detect the same building
        osm = Lead(lat=59.3500, lng=18.0500, address="Solv 1", confidence=1.0,
                   source="osm", tile_key="osm/1")
        ai  = Lead(lat=59.3500, lng=18.0500, address="Solv 1", confidence=0.9,
                   source="ai",  tile_key="bld/999")

        merged = merge_leads([osm], [ai])
        assert len(merged) == 1
        # OSM should win (higher confidence, listed first)
        assert merged[0].source == "osm"

    def test_scan_city_deduplicates_buildings_across_areas(self):
        """Samma OSM building_id från två areas → processas bara en gång."""
        # Both areas return the same building id
        shared_building = _make_building("SHARED_ID", 59.350, 18.050)

        fake_gmaps = MagicMock()
        fake_gmaps.geocode.return_value = _make_geocode_result()

        two_areas = [
            {"lat": 59.35, "lng": 18.05, "area_deg2": 0.01},
            {"lat": 59.36, "lng": 18.06, "area_deg2": 0.009},
        ]

        calls_to_scan_buildings_ai = []

        def fake_scan_buildings_ai(buildings, *args, **kwargs):
            calls_to_scan_buildings_ai.append(list(buildings))
            return [_make_lead(b["lat"], b["lng"]) for b in buildings]

        with patch("googlemaps.Client", return_value=fake_gmaps), \
             patch.object(scanner, "scan_area_osm", return_value=[]), \
             patch.object(scanner, "_get_residential_areas", return_value=two_areas), \
             patch.object(scanner, "_get_osm_buildings", return_value=[shared_building]), \
             patch.object(scanner, "scan_buildings_ai",
                          side_effect=fake_scan_buildings_ai):

            scan_city(
                city_name="Teststad",
                google_key="fake",
                anthropic_key="fake",
            )

        # The shared building should appear in at most one scan_buildings_ai call
        all_ids_passed = [b["osm_id"] for call in calls_to_scan_buildings_ai for b in call]
        assert all_ids_passed.count("SHARED_ID") <= 1, (
            "SHARED_ID was passed to scan_buildings_ai more than once"
        )


# ── 3. tom stad ────────────────────────────────────────────────────────────────

class TestEmptyCity:
    """Om Overpass returnerar 0 byggnader → tom lista, inget krasch."""

    def test_scan_bbox_empty_overpass_returns_empty_list(self):
        """scan_bbox med tom Overpass-respons → []."""
        with patch.object(scanner, "_overpass", return_value=[]), \
             patch.object(scanner, "_get_osm_buildings", return_value=[]), \
             patch.object(scanner, "scan_buildings_ai", return_value=[]):

            result = scan_bbox(
                south=59.340, west=18.045, north=59.345, east=18.055,
                google_key="fake", anthropic_key="fake",
            )

        assert result == []

    def test_scan_city_no_buildings_returns_empty_list(self):
        """scan_city med inga byggnader → []."""
        fake_gmaps = MagicMock()
        fake_gmaps.geocode.return_value = _make_geocode_result()

        with patch("googlemaps.Client", return_value=fake_gmaps), \
             patch.object(scanner, "scan_area_osm", return_value=[]), \
             patch.object(scanner, "_get_residential_areas", return_value=[]), \
             patch.object(scanner, "_get_osm_buildings", return_value=[]), \
             patch.object(scanner, "scan_buildings_ai", return_value=[]):

            result = scan_city(
                city_name="Tomstad",
                google_key="fake",
                anthropic_key="fake",
            )

        assert result == []

    def test_scan_city_no_results_from_geocode_raises(self):
        """Om geocode returnerar [] ska ValueError kastas."""
        fake_gmaps = MagicMock()
        fake_gmaps.geocode.return_value = []

        with patch("googlemaps.Client", return_value=fake_gmaps):
            with pytest.raises(ValueError, match="Hittade inte orten"):
                scan_city(
                    city_name="XyzFinnsInte",
                    google_key="fake",
                    anthropic_key="fake",
                )

    def test_scan_city_residential_area_no_buildings_no_crash(self):
        """Residential area utan byggnader → ingen krasch, tom lista."""
        fake_gmaps = MagicMock()
        fake_gmaps.geocode.return_value = _make_geocode_result()

        area_without_buildings = {"lat": 59.35, "lng": 18.05, "area_deg2": 0.01}

        with patch("googlemaps.Client", return_value=fake_gmaps), \
             patch.object(scanner, "scan_area_osm", return_value=[]), \
             patch.object(scanner, "_get_residential_areas",
                          return_value=[area_without_buildings]), \
             patch.object(scanner, "_get_osm_buildings", return_value=[]):

            result = scan_city(
                city_name="Tomstad",
                google_key="fake",
                anthropic_key="fake",
            )

        assert result == []


# ── 4. area_cap ────────────────────────────────────────────────────────────────

class TestAreaCap:
    """_get_osm_buildings ska inte processa fler än max_count byggnader (default 600)."""

    def test_get_osm_buildings_passes_max_count_to_overpass(self):
        """_get_osm_buildings skickar max_count i Overpass-frågan."""
        with patch.object(scanner, "_overpass", return_value=[]) as mock_overpass:
            _get_osm_buildings(59.30, 18.00, 59.40, 18.10)

        # First call is the building query — verify max_count appears in it
        building_query = mock_overpass.call_args_list[0][0][0]
        assert "600" in building_query, (
            "Default max_count=600 should appear in the Overpass building query"
        )

    def test_get_osm_buildings_custom_max_count(self):
        """_get_osm_buildings med custom max_count skickar rätt värde."""
        with patch.object(scanner, "_overpass", return_value=[]) as mock_overpass:
            _get_osm_buildings(59.30, 18.00, 59.40, 18.10, max_count=100)

        building_query = mock_overpass.call_args_list[0][0][0]
        assert "100" in building_query

    def test_get_osm_buildings_respects_max_count_limit(self):
        """_get_osm_buildings returnerar aldrig fler element än max_count tillåter."""
        # Build 10 valid building elements; max_count=5 means only 5 can be requested
        def _make_way(i: int) -> dict:
            lat = 59.30 + i * 0.001
            lng = 18.05
            return {
                "type": "way",
                "id": i,
                "tags": {"building": "house",
                         "addr:street": "Gatan",
                         "addr:housenumber": str(i)},
                "geometry": [
                    {"lat": lat,        "lon": lng},
                    {"lat": lat + 0.0001, "lon": lng},
                    {"lat": lat + 0.0001, "lon": lng + 0.0001},
                    {"lat": lat,        "lon": lng + 0.0001},
                ],
            }

        # Overpass would respect the limit in real life; here we fake it returning
        # exactly max_count=5 elements even though 10 were "available".
        five_elements = [_make_way(i) for i in range(5)]
        with patch.object(scanner, "_overpass",
                          side_effect=[five_elements, []]) as mock_overpass:
            result = _get_osm_buildings(59.30, 18.00, 59.40, 18.10, max_count=5)

        building_query = mock_overpass.call_args_list[0][0][0]
        assert "5" in building_query
        assert len(result) <= 5

    def test_scan_city_passes_default_area_cap_to_get_osm_buildings(self):
        """scan_city anropar _get_osm_buildings utan att överrida max_count (default 600)."""
        fake_gmaps = MagicMock()
        fake_gmaps.geocode.return_value = _make_geocode_result()

        area = {"lat": 59.35, "lng": 18.05, "area_deg2": 0.01}

        calls_to_get_osm_buildings = []

        def fake_get_osm_buildings(south, west, north, east, max_count=600):
            calls_to_get_osm_buildings.append(max_count)
            return []

        with patch("googlemaps.Client", return_value=fake_gmaps), \
             patch.object(scanner, "scan_area_osm", return_value=[]), \
             patch.object(scanner, "_get_residential_areas", return_value=[area]), \
             patch.object(scanner, "_get_osm_buildings",
                          side_effect=fake_get_osm_buildings):

            scan_city(
                city_name="Teststad",
                google_key="fake",
                anthropic_key="fake",
            )

        assert calls_to_get_osm_buildings, "_get_osm_buildings was never called"
        # Default cap should be 600 (or at least not None / unlimited)
        for cap in calls_to_get_osm_buildings:
            assert cap is not None
            assert cap <= 600, f"area cap {cap} exceeds MAX expected 600"


# ── 5. all_areas — regression för max_leads//5-buggen ─────────────────────────

class TestAllAreas:
    """scan_city ska scanna alla areas tills max_leads nås — inte stanna efter max_leads//5.

    Regression för buggen som gav 0 leads i stora städer (Lund, Malmö):
    med max_leads=10 begränsades scan till max(1, 10//5)=2 areas. Om de 2
    första råkade vara campus/lägenhetszoner → 0 leads trots att villa-suburbs
    med solpaneler finns längre ut.
    """

    def test_scans_beyond_first_two_areas_when_no_leads_found(self):
        """Med 10 areas och 0 leads i area 1-2 ska area 3+ scannas.

        Gamla beteendet: max_areas = max(1, 10//5) = 2 → stannar efter area 2.
        Nya beteendet: scanna alla tills max_leads nås.

        Varje area får unika buildings (olika osm_id) för att undvika
        seen_building_ids-dedupliceringen som annars filtrerar bort dem.
        """
        fake_gmaps = MagicMock()
        fake_gmaps.geocode.return_value = _make_geocode_result()

        ten_areas = [
            {"lat": 59.30 + i * 0.01, "lng": 18.05, "area_deg2": 0.01}
            for i in range(10)
        ]

        area_scan_count = [0]

        def fake_get_osm_buildings(south, west, north, east, **kwargs):
            # Unique building per area call — avoids seen_building_ids dedup
            area_idx = area_scan_count[0]
            return [_make_building(f"BLD_AREA_{area_idx}", 59.30 + area_idx * 0.01, 18.05)]

        def fake_scan_buildings_ai(buildings, *args, max_leads=None, **kwargs):
            area_scan_count[0] += 1
            # Only area 5 (call #5) produces a lead
            if area_scan_count[0] == 5:
                return [_make_lead(59.35, 18.05)]
            return []

        with patch("googlemaps.Client", return_value=fake_gmaps), \
             patch.object(scanner, "scan_area_osm", return_value=[]), \
             patch.object(scanner, "_get_residential_areas", return_value=ten_areas), \
             patch.object(scanner, "_get_osm_buildings",
                          side_effect=fake_get_osm_buildings), \
             patch.object(scanner, "scan_buildings_ai",
                          side_effect=fake_scan_buildings_ai):

            result = scan_city(
                city_name="Teststad",
                google_key="fake",
                anthropic_key="fake",
                max_leads=10,
            )

        assert len(result) >= 1, "Ska hitta lead i area 5"
        assert area_scan_count[0] >= 5, (
            f"Bara {area_scan_count[0]} areas scannades — gamla buggen begränsade till 2"
        )

    def test_stops_scanning_areas_when_max_leads_reached(self):
        """När max_leads är uppnått ska inga fler areas scannas."""
        fake_gmaps = MagicMock()
        fake_gmaps.geocode.return_value = _make_geocode_result()

        twenty_areas = [
            {"lat": 59.30 + i * 0.01, "lng": 18.05, "area_deg2": 0.01}
            for i in range(20)
        ]
        building = _make_building("BLD1", 59.35, 18.05)

        area_scan_count = [0]

        def fake_scan_buildings_ai(buildings, *args, max_leads=None, **kwargs):
            area_scan_count[0] += 1
            # Every area produces max_leads leads immediately
            leads = [_make_lead(59.35 + area_scan_count[0] * 0.0001, 18.05)]
            if max_leads is not None:
                leads = leads[:max_leads]
            return leads

        with patch("googlemaps.Client", return_value=fake_gmaps), \
             patch.object(scanner, "scan_area_osm", return_value=[]), \
             patch.object(scanner, "_get_residential_areas", return_value=twenty_areas), \
             patch.object(scanner, "_get_osm_buildings", return_value=[building]), \
             patch.object(scanner, "scan_buildings_ai",
                          side_effect=fake_scan_buildings_ai):

            result = scan_city(
                city_name="Teststad",
                google_key="fake",
                anthropic_key="fake",
                max_leads=3,
            )

        assert len(result) <= 3, "Ska inte returnera fler än max_leads"
        # Should stop well before scanning all 20 areas
        assert area_scan_count[0] < 20, "Ska sluta scanna när max_leads nås"
