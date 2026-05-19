"""
Unit tests for the two-pass detection pipeline.

Two-pass design:
  Pass 1 — wide 60m×60m image, classify building type only (cheap, max_tokens=50).
            HOUSE=NO  → return None immediately, pass 2 never called.
  Pass 2 — tight 18m×18m image, detect solar panels only.
            Returns (True, has_solar, is_unsure, reasoning).

Tests verify:
  1. HOUSE=NO in pass 1  → None returned, pass 2 never called.
  2. HOUSE=YES + SOLAR=YES → Lead with needs_review=False.
  3. HOUSE=YES + SOLAR=UNSURE → Lead with needs_review=True.
  4. Feature flag TWO_PASS_MODE=True routes _process_building through new path.
  5. Feature flag TWO_PASS_MODE=False keeps original path (pass 2 not called).
"""

import sys
from contextlib import ExitStack
from unittest.mock import MagicMock, call, patch

import pytest

# Stub out packages unavailable in test environment before importing scanner
for _mod in ("googlemaps", "streamlit", "supabase", "stripe", "folium",
             "streamlit_folium", "openpyxl"):
    sys.modules.setdefault(_mod, MagicMock())

import scanner  # noqa: E402  (imported after stubs)
from scanner import (  # noqa: E402
    Lead,
    _analyze_building_two_pass,
    _detect_solar_panels,
    _process_building,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_building_dict(lat=55.55, lng=13.05, address="Testvagen 1", osm_id="42"):
    return {
        "lat": lat,
        "lng": lng,
        "address": address,
        "osm_id": osm_id,
        "building_type": "house",
        "zoom": 20,
        "area_m2": 120,
    }


def _make_client_two_call(pass1_text: str, pass2_text: str):
    """Return a mock Anthropic client whose messages.create returns pass1_text
    on the first call and pass2_text on the second call."""
    msg1 = MagicMock()
    msg1.content = [MagicMock(text=pass1_text)]
    msg2 = MagicMock()
    msg2.content = [MagicMock(text=pass2_text)]
    client = MagicMock()
    client.messages.create.side_effect = [msg1, msg2]
    return client


def _make_client_one_call(text: str):
    """Return a mock client that answers once (for pass 1 only, when pass 2 must not run)."""
    msg = MagicMock()
    msg.content = [MagicMock(text=text)]
    client = MagicMock()
    client.messages.create.return_value = msg
    return client


# ── _analyze_building_two_pass unit tests ─────────────────────────────────────

class TestTwoPassHouseFilter:
    """Pass 1 correctly gates access to pass 2."""

    def test_house_no_returns_none_pass2_never_called(self):
        """HOUSE=NO in pass 1 → None, messages.create called exactly once."""
        client = _make_client_one_call("HOUSE=NO")
        with patch("scanner._fetch_lm_wms", return_value=b"fake_wide_image"):
            result = _analyze_building_two_pass(
                client, google_key="fake", lat=55.55, lng=13.05
            )
        assert result is None
        # Only pass 1 should have been called
        assert client.messages.create.call_count == 1

    def test_house_yes_solar_yes_returns_tuple(self):
        """HOUSE=YES pass 1 + SOLAR=YES pass 2 → (True, True, False, reasoning)."""
        client = _make_client_two_call(
            pass1_text="HOUSE=YES",
            pass2_text=(
                "Clearly visible rectangular array of dark PV panels on the south slope.\n"
                "SOLAR=YES"
            ),
        )
        with patch("scanner._fetch_lm_wms", return_value=b"fake_image"):
            result = _analyze_building_two_pass(
                client, google_key="fake", lat=55.55, lng=13.05
            )
        assert result is not None
        is_house, has_solar, is_unsure, reasoning = result
        assert is_house is True
        assert has_solar is True
        assert is_unsure is False
        assert "PV panels" in reasoning

    def test_house_yes_solar_no_returns_tuple(self):
        """HOUSE=YES pass 1 + SOLAR=NO pass 2 → (True, False, False, reasoning)."""
        client = _make_client_two_call(
            pass1_text="HOUSE=YES",
            pass2_text=(
                "Uniform clay tile roof with no smooth rectangular patches.\n"
                "SOLAR=NO"
            ),
        )
        with patch("scanner._fetch_lm_wms", return_value=b"fake_image"):
            result = _analyze_building_two_pass(
                client, google_key="fake", lat=55.55, lng=13.05
            )
        assert result is not None
        is_house, has_solar, is_unsure, reasoning = result
        assert is_house is True
        assert has_solar is False
        assert is_unsure is False

    def test_house_yes_solar_unsure_returns_tuple(self):
        """HOUSE=YES pass 1 + SOLAR=UNSURE pass 2 → (True, False, True, reasoning)."""
        client = _make_client_two_call(
            pass1_text="HOUSE=YES",
            pass2_text=(
                "Some smooth patches that could be panels but image quality is poor.\n"
                "SOLAR=UNSURE"
            ),
        )
        with patch("scanner._fetch_lm_wms", return_value=b"fake_image"):
            result = _analyze_building_two_pass(
                client, google_key="fake", lat=55.55, lng=13.05
            )
        assert result is not None
        is_house, has_solar, is_unsure, reasoning = result
        assert is_house is True
        assert has_solar is False
        assert is_unsure is True

    def test_pass1_image_fetch_failure_returns_none(self):
        """If the wide image cannot be fetched, returns None without API call."""
        client = MagicMock()
        with patch("scanner._fetch_lm_wms", return_value=None), \
             patch("scanner._fetch_satellite", return_value=None):
            result = _analyze_building_two_pass(
                client, google_key="fake", lat=55.55, lng=13.05
            )
        assert result is None
        client.messages.create.assert_not_called()

    def test_pass1_api_error_returns_none(self):
        """API error in pass 1 → None, no crash."""
        client = MagicMock()
        client.messages.create.side_effect = Exception("network timeout")
        with patch("scanner._fetch_lm_wms", return_value=b"fake_image"):
            result = _analyze_building_two_pass(
                client, google_key="fake", lat=55.55, lng=13.05
            )
        assert result is None

    def test_pass2_image_fetch_failure_returns_none(self):
        """If the tight image cannot be fetched in pass 2, returns None."""
        client = _make_client_one_call("HOUSE=YES")
        call_count = {"n": 0}

        def fake_fetch_lm_wms(lat, lng, size_m=18):
            call_count["n"] += 1
            if size_m == 60:
                return b"fake_wide"
            return None  # pass 2 tight image fails

        with patch("scanner._fetch_lm_wms", side_effect=fake_fetch_lm_wms), \
             patch("scanner._fetch_satellite", return_value=None):
            result = _analyze_building_two_pass(
                client, google_key="fake", lat=55.55, lng=13.05
            )
        assert result is None
        # Pass 1 API call happened but pass 2 did not
        assert client.messages.create.call_count == 1

    def test_pass2_api_error_returns_no_solar(self):
        """API error in pass 2 → (True, False, False, '') — house confirmed but panels unknown."""
        msg1 = MagicMock()
        msg1.content = [MagicMock(text="HOUSE=YES")]
        client = MagicMock()
        # First call (pass 1) succeeds; second call (pass 2) raises
        client.messages.create.side_effect = [msg1, Exception("rate limit")]
        with patch("scanner._fetch_lm_wms", return_value=b"fake_image"):
            result = _analyze_building_two_pass(
                client, google_key="fake", lat=55.55, lng=13.05
            )
        # _detect_solar_panels catches the exception and returns (False, False, "")
        assert result is not None
        is_house, has_solar, is_unsure, reasoning = result
        assert is_house is True
        assert has_solar is False
        assert is_unsure is False


# ── _detect_solar_panels unit tests ───────────────────────────────────────────

class TestDetectSolarPanels:
    """_detect_solar_panels parses SOLAR= verdict correctly."""

    def _client(self, text):
        msg = MagicMock()
        msg.content = [MagicMock(text=text)]
        c = MagicMock()
        c.messages.create.return_value = msg
        return c

    def test_solar_yes(self):
        client = self._client("Rectangular dark patches on south slope.\nSOLAR=YES")
        has_solar, is_unsure, reasoning = _detect_solar_panels(client, b"img")
        assert has_solar is True
        assert is_unsure is False

    def test_solar_no(self):
        client = self._client("Uniform clay tiles, no patches.\nSOLAR=NO")
        has_solar, is_unsure, reasoning = _detect_solar_panels(client, b"img")
        assert has_solar is False
        assert is_unsure is False

    def test_solar_unsure(self):
        client = self._client("Some ambiguous patches.\nSOLAR=UNSURE")
        has_solar, is_unsure, reasoning = _detect_solar_panels(client, b"img")
        assert has_solar is False
        assert is_unsure is True

    def test_api_error_returns_false_false(self):
        client = MagicMock()
        client.messages.create.side_effect = Exception("network error")
        has_solar, is_unsure, reasoning = _detect_solar_panels(client, b"img")
        assert has_solar is False
        assert is_unsure is False
        assert reasoning == ""


# ── _process_building integration with TWO_PASS_MODE ─────────────────────────

class TestProcessBuildingTwoPassMode:
    """_process_building routes through two-pass path when TWO_PASS_MODE=True."""

    def _patches(self, two_pass_return):
        """Context manager that patches TWO_PASS_MODE=True and
        _analyze_building_two_pass to return two_pass_return."""
        stack = ExitStack()
        stack.enter_context(patch.object(scanner, "TWO_PASS_MODE", True))
        stack.enter_context(
            patch("scanner._analyze_building_two_pass", return_value=two_pass_return)
        )
        stack.enter_context(patch("scanner._has_extra_solar_nearby", return_value={
            "extra_solar_found": False,
            "solar_locations": [],
            "villa_nearby": False,
        }))
        return stack

    def test_house_no_returns_none_pass2_not_called(self):
        """When two-pass returns None (HOUSE=NO), _process_building returns None.
        _analyze_building must NOT be called."""
        building = _make_building_dict()
        with self._patches(two_pass_return=None):
            with patch("scanner._analyze_building") as mock_analyze:
                lead = _process_building(building, google_key="fake",
                                         anthropic_client=MagicMock())
        assert lead is None
        mock_analyze.assert_not_called()

    def test_house_yes_solar_yes_returns_lead(self):
        """HOUSE=YES + SOLAR=YES in two-pass → Lead with needs_review=False."""
        building = _make_building_dict(address="Solvägen 1, Malmö")
        two_pass_result = (True, True, False, "Clear rectangular PV array.")
        with self._patches(two_pass_return=two_pass_result):
            lead = _process_building(building, google_key="fake",
                                     anthropic_client=MagicMock())
        assert lead is not None
        assert isinstance(lead, Lead)
        assert lead.needs_review is False
        assert lead.confidence == 0.90
        assert lead.ai_reasoning == "Clear rectangular PV array."

    def test_house_yes_solar_unsure_returns_review_lead(self):
        """HOUSE=YES + SOLAR=UNSURE in two-pass → Lead with needs_review=True."""
        building = _make_building_dict(address="Pannvägen 3, Lund")
        two_pass_result = (True, False, True, "Ambiguous patches, uncertain.")
        with self._patches(two_pass_return=two_pass_result):
            lead = _process_building(building, google_key="fake",
                                     anthropic_client=MagicMock())
        assert lead is not None
        assert lead.needs_review is True
        assert lead.confidence == 0.50
        assert lead.ai_reasoning == "Ambiguous patches, uncertain."

    def test_house_yes_solar_no_returns_none(self):
        """HOUSE=YES + SOLAR=NO in two-pass → None (no lead)."""
        building = _make_building_dict(address="Skuggvägen 5, Vellinge")
        two_pass_result = (True, False, False, "No panels visible.")
        with self._patches(two_pass_return=two_pass_result):
            lead = _process_building(building, google_key="fake",
                                     anthropic_client=MagicMock())
        assert lead is None

    def test_single_pass_mode_does_not_call_two_pass(self):
        """When TWO_PASS_MODE=False, _analyze_building_two_pass is never called."""
        building = _make_building_dict()
        with patch.object(scanner, "TWO_PASS_MODE", False), \
             patch("scanner._fetch_satellite", return_value=b"fake_img"), \
             patch("scanner._analyze_building",
                   return_value=(True, True, False, "solar yes")) as mock_analyze, \
             patch("scanner._analyze_building_two_pass") as mock_two_pass, \
             patch("scanner._has_extra_solar_nearby", return_value={
                 "extra_solar_found": False,
                 "solar_locations": [],
                 "villa_nearby": False,
             }):
            lead = _process_building(building, google_key="fake",
                                     anthropic_client=MagicMock())
        mock_two_pass.assert_not_called()
        mock_analyze.assert_called_once()
        assert lead is not None
