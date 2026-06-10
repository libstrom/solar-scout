"""Tests for harvester_lm.py (gratis-harvestern: OSM + Lantmäteriet).

Allt körs offline: geometri- och filterhelpers är rena funktioner,
DB-testerna repointar db.py:s modulglobaler till tmp_path (samma mönster
som test_new_features.py), och Overpass-parsning testas mot ett inbäddat
exempelsvar. Inga HTTP-anrop görs.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

import db as dbmod  # noqa: E402
import harvester_lm as hlm  # noqa: E402


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    monkeypatch.setattr(dbmod, "DATA_DIR", data_dir)
    monkeypatch.setattr(dbmod, "IMAGES_DIR", data_dir / "images")
    monkeypatch.setattr(dbmod, "DB_PATH", data_dir / "leads.db")
    dbmod.ensure_schema()
    return data_dir / "leads.db"


# ---- Geometri ------------------------------------------------------------------

# ~10x10 m kvadrat vid lat 56: 10 m i lat ≈ 0.0000898°, i lng ≈ 0.0001606°.
SQUARE_10M = [
    (56.0000000, 13.0000000),
    (56.0000898, 13.0000000),
    (56.0000898, 13.0001606),
    (56.0000000, 13.0001606),
]


class TestGeometry:
    def test_area_of_10m_square(self):
        area = hlm.polygon_area_m2(SQUARE_10M)
        assert 90 < area < 110

    def test_area_degenerate_polygon_is_zero(self):
        assert hlm.polygon_area_m2(SQUARE_10M[:2]) == 0.0

    def test_centroid_is_middle(self):
        lat, lng = hlm.polygon_centroid(SQUARE_10M)
        assert abs(lat - 56.0000449) < 1e-6
        assert abs(lng - 13.0000803) < 1e-6

    def test_wms_bbox_spans_size_m(self):
        bbox = hlm.wms_bbox(56.0, 13.0, size_m=50)
        w, s, e, n = (float(x) for x in bbox.split(","))
        assert 49 < (n - s) * 111_320 < 51   # höjd ≈ 50 m
        assert w < 13.0 < e and s < 56.0 < n


# ---- Kandidatfilter --------------------------------------------------------------

def _building(btype: str, coords=None):
    return {"id": 1, "tags": {"building": btype}, "coords": coords or SQUARE_10M}


class TestIsCandidate:
    def test_house_100m2_accepted(self):
        assert hlm.is_candidate(_building("house"), min_area=80, max_area=400)

    def test_too_small_rejected(self):
        assert not hlm.is_candidate(_building("house"), min_area=200, max_area=400)

    def test_too_big_rejected(self):
        assert not hlm.is_candidate(_building("house"), min_area=10, max_area=50)

    def test_excluded_type_rejected_despite_area(self):
        for btype in ("garage", "shed", "church", "apartments"):
            assert not hlm.is_candidate(_building(btype), min_area=80, max_area=400)

    def test_untagged_yes_passes_on_area(self):
        assert hlm.is_candidate(_building("yes"), min_area=80, max_area=400)


# ---- Adress ----------------------------------------------------------------------

class TestAddressFromTags:
    def test_full_address(self):
        tags = {"addr:street": "Storgatan", "addr:housenumber": "5",
                "addr:postcode": "268 78", "addr:city": "Kågeröd"}
        assert hlm.address_from_tags(tags) == "Storgatan 5, 268 78 Kågeröd"

    def test_street_only(self):
        assert hlm.address_from_tags({"addr:street": "Storgatan"}) == "Storgatan"

    def test_no_street_returns_none(self):
        assert hlm.address_from_tags({"addr:city": "Kågeröd"}) is None

    def test_street_and_number_without_locality(self):
        tags = {"addr:street": "Storgatan", "addr:housenumber": "5"}
        assert hlm.address_from_tags(tags) == "Storgatan 5"


# ---- Overpass-parsning ------------------------------------------------------------

OVERPASS_SAMPLE = {
    "elements": [
        {
            "type": "way", "id": 111,
            "tags": {"building": "house", "addr:street": "Storgatan"},
            "geometry": [{"lat": la, "lon": lo} for la, lo in SQUARE_10M],
        },
        {"type": "node", "id": 222},  # ej way -- ska ignoreras
        {"type": "way", "id": 333, "tags": {"building": "garage"}},  # saknar geometry
    ]
}


class TestOverpassParsing:
    def test_parses_ways_with_geometry_only(self, monkeypatch):
        class FakeResp:
            status_code = 200
            def json(self):
                return OVERPASS_SAMPLE
            def raise_for_status(self):
                pass

        monkeypatch.setattr(hlm.SESSION, "post", lambda *a, **k: FakeResp())
        buildings = hlm.fetch_buildings_overpass((55.9, 13.0, 56.0, 13.1))
        assert len(buildings) == 1
        b = buildings[0]
        assert b["id"] == 111
        assert b["tags"]["building"] == "house"
        assert b["coords"][0] == SQUARE_10M[0]


# ---- DB: insert + dedupe -----------------------------------------------------------

class TestDbInsert:
    def test_insert_and_exists(self, tmp_db):
        assert not hlm.lead_exists("osm/way/111")
        hlm.insert_lead_lm({
            "place_id": "osm/way/111", "address": "Storgatan 5, Kågeröd",
            "lat": 56.0, "lng": 13.0, "roof_area_m2": 123.4,
            "image_path": "data/images/osm_way_111.png",
            "tags": {"building": "house"},
        })
        assert hlm.lead_exists("osm/way/111")
        with sqlite3.connect(tmp_db) as c:
            c.row_factory = sqlite3.Row
            row = c.execute("SELECT * FROM leads WHERE place_id='osm/way/111'").fetchone()
        assert row["status"] == "pending"
        assert row["solar_confidence"] == "OSM"
        assert row["roof_area_m2"] == 123.4
        assert row["ai_score"] is None  # prescreen ska plocka upp den
        assert '"source": "osm"' in row["raw_solar_data"]

    def test_duplicate_place_id_raises(self, tmp_db):
        lead = {
            "place_id": "osm/way/111", "address": None,
            "lat": 56.0, "lng": 13.0, "roof_area_m2": 100.0,
            "image_path": "x.png", "tags": {},
        }
        hlm.insert_lead_lm(lead)
        with pytest.raises(sqlite3.IntegrityError):
            hlm.insert_lead_lm(lead)
