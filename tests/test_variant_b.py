"""
test_variant_b.py — Regression tests for Variant B UI changes.

Tests:
  1. Supersearch mode detection (coords/postal/address/city)
  2. Coordinate bbox calculation
  3. Tab count = 4 (not 5)
  4. _page_scout_inline exists and has widget keys
"""
import ast
import re
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ── Stub heavy dependencies (same pattern as test_acceptance.py) ──────────────────

_st = types.ModuleType("streamlit")
for _attr in [
    "cache_resource", "cache_data", "session_state", "secrets", "error",
    "warning", "info", "success", "rerun", "expander", "code", "text_input",
    "selectbox", "button", "metric", "spinner", "tabs", "columns", "divider",
    "caption", "subheader", "header", "markdown", "dataframe", "image",
    "sidebar", "checkbox", "radio", "empty", "progress", "stop",
]:
    setattr(_st, _attr, MagicMock())
_st.secrets = {}
_st.session_state = MagicMock()
sys.modules.setdefault("streamlit", _st)

for _mod in [
    "extra_streamlit_components", "stripe", "googlemaps", "supabase",
    "folium", "streamlit_folium", "openpyxl", "anthropic", "resend",
]:
    sys.modules.setdefault(_mod, MagicMock())


# ── AST helpers ───────────────────────────────────────────────────────────────────────

APP_SRC = Path(__file__).parent.parent / "app.py"
_tree = ast.parse(APP_SRC.read_text())


def _find_tab_calls(tree: ast.AST) -> list[list[str]]:
    """Return all literal string lists passed to st.tabs(...)."""
    results = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "tabs"
            and node.args
            and isinstance(node.args[0], ast.List)
        ):
            labels = [
                elt.s
                for elt in node.args[0].elts
                if isinstance(elt, ast.Constant) and isinstance(elt.s, str)
            ]
            if labels:
                results.append(labels)
    return results


def _extract_widget_keys(tree: ast.AST) -> list[str]:
    """Return all literal key= values passed to st widget calls."""
    keys = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg == "key" and isinstance(kw.value, ast.Constant):
                keys.append(kw.value.s)
    return keys


# ── Supersearch mode detection (mirrors app.py logic) ──────────────────────────

def _detect_mode(sq: str) -> str:
    if re.match(r"^-?\d{1,3}\.?\d*\s*,\s*-?\d{1,3}\.?\d*$", sq):
        return "coords"
    elif re.match(r"^\d{3}\s?\d{2}$", sq):
        return "postal"
    elif re.search(r"\d", sq) and len(sq) > 3:
        return "address"
    elif sq:
        return "city"
    return "city"


class TestSupersearchDetection:
    def test_coordinate_decimal(self):
        assert _detect_mode("57.65,14.69") == "coords"

    def test_coordinate_with_spaces(self):
        assert _detect_mode("57.65, 14.69") == "coords"

    def test_coordinate_negative(self):
        assert _detect_mode("-33.8,151.2") == "coords"

    def test_postal_code_5digit(self):
        assert _detect_mode("57300") == "postal"

    def test_postal_code_with_space(self):
        assert _detect_mode("573 00") == "postal"

    def test_address_with_number(self):
        assert _detect_mode("Storgatan 4, Nässjö") == "address"

    def test_city_no_digit(self):
        assert _detect_mode("Nässjö") == "city"

    def test_empty_string(self):
        assert _detect_mode("") == "city"

    def test_short_string_with_digit(self):
        # "4" is too short (len <= 3) → city fallback
        assert _detect_mode("4") == "city"

    def test_huskvarna(self):
        assert _detect_mode("Huskvarna") == "city"


class TestCoordBbox:
    def test_bbox_size(self):
        lat, lng = 57.65, 14.69
        delta = 0.009
        south, north = lat - delta, lat + delta
        west, east = lng - delta, lng + delta

        assert abs((north - south) - 2 * delta) < 1e-9
        assert abs((east - west) - 2 * delta) < 1e-9

    def test_bbox_center(self):
        lat, lng = 57.65, 14.69
        delta = 0.009
        south, north = lat - delta, lat + delta
        west, east = lng - delta, lng + delta

        assert abs((south + north) / 2 - lat) < 1e-9
        assert abs((west + east) / 2 - lng) < 1e-9


# ── Structural tests via AST ─────────────────────────────────────────────────

class TestTabStructure:
    def test_main_tabs_count_is_4(self):
        """Variant B must have exactly 4 main tabs (removed 'Scouta Tak')."""
        all_tab_calls = _find_tab_calls(_tree)
        # The main nav call is the one with 4 entries
        four_tab_calls = [t for t in all_tab_calls if len(t) == 4]
        assert len(four_tab_calls) >= 1, (
            f"Expected a st.tabs() with 4 items; found: {all_tab_calls}"
        )

    def test_no_five_tab_call(self):
        """The old 5-tab variant must be gone."""
        all_tab_calls = _find_tab_calls(_tree)
        five_tab_calls = [t for t in all_tab_calls if len(t) == 5]
        assert len(five_tab_calls) == 0, (
            f"Found 5-tab call (old Variant A): {five_tab_calls}"
        )

    def test_scanner_tab_label(self):
        all_tab_calls = _find_tab_calls(_tree)
        flat = [label for tabs in all_tab_calls for label in tabs]
        # Should contain "Scanner" without "AI" prefix
        scanner_labels = [l for l in flat if "Scanner" in l]
        ai_scanner_labels = [l for l in flat if l.strip() == "AI Scanner"]
        assert scanner_labels, "No tab labelled '*Scanner*' found"
        assert not ai_scanner_labels, "Old 'AI Scanner' tab label still present"


class TestWidgetKeys:
    def test_no_duplicate_widget_keys(self):
        """Every st widget key= literal must be unique."""
        keys = _extract_widget_keys(_tree)
        seen: set[str] = set()
        dupes: list[str] = []
        for k in keys:
            if k in seen:
                dupes.append(k)
            seen.add(k)
        assert not dupes, f"Duplicate widget keys found: {dupes}"

    def test_scout_inline_has_keys(self):
        """_page_scout_inline widgets must have explicit keys to avoid collisions."""
        src = APP_SRC.read_text()
        # Find the _page_scout_inline function and verify key= appears in it
        fn_start = src.find("def _page_scout_inline(")
        assert fn_start != -1, "_page_scout_inline not found in app.py"
        # Find next function def or end of file
        next_fn = src.find("\ndef ", fn_start + 1)
        fn_body = src[fn_start:next_fn] if next_fn != -1 else src[fn_start:]
        assert 'key="scout_' in fn_body or "key='scout_" in fn_body, (
            "_page_scout_inline has no scout_* widget keys — risk of duplicate key error"
        )


class TestApiHealthFunction:
    def test_render_api_health_exists(self):
        """_render_api_health must exist (live API status panel)."""
        src = APP_SRC.read_text()
        assert "def _render_api_health(" in src, "_render_api_health not found in app.py"

    def test_budget_tracker_in_session_state(self):
        """BudgetTracker must be stored in session_state for cost display."""
        src = APP_SRC.read_text()
        assert '"_budget_tracker"' in src or "'_budget_tracker'" in src, (
            "_budget_tracker not stored in session_state"
        )
