"""
F1 regression harness for the solar-panel AI detection pipeline.

DUAL-MODE design:
  - CI (no ANTHROPIC_API_KEY): validates TEST_CASES structure and runs the
    scoring machinery against a deterministic mock — always passes.
  - Real API (ANTHROPIC_API_KEY set): fetches live Lantmäteriet WMS images,
    calls Claude Vision, measures precision/recall/F1, asserts F1 >= 0.80.

Verified addresses (user-confirmed Malmö, 2026-05-19):
  Risholmsgatan 8   — solar_yes
  Skimmelgatan 22   — solar_yes
  Remontgatan 41    — solar_no
"""

import os
import sys
from unittest.mock import MagicMock
import inspect
import pytest

# Stub unavailable packages before importing scanner
for _mod in ("googlemaps", "streamlit", "supabase", "stripe", "folium",
             "streamlit_folium", "openpyxl"):
    sys.modules.setdefault(_mod, MagicMock())

import scanner  # noqa: E402
from scanner import _analyze_building, _fetch_lm_wms  # noqa: E402


# ── Ground-truth fixtures ──────────────────────────────────────────────────────

# Each entry: (lat, lng, has_solar: bool, label: str)
# Verified by user against Lantmäteriet orthophoto, Malmö 2026-05-19.
TEST_CASES: list[tuple[float, float, bool, str]] = [
    (55.5705978, 13.0378985, True,  "Risholmsgatan 8 Malmö"),   # solar_yes
    (55.5750780, 13.0707577, True,  "Skimmelgatan 22 Malmö"),   # solar_yes
    (55.5764531, 13.0743366, False, "Remontgatan 41 Malmö"),    # solar_no
]

_HAS_API_KEY = bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())


# ── Scoring helpers ────────────────────────────────────────────────────────────

def _compute_f1(
    results: list[tuple[bool, bool]],
) -> tuple[float, float, float]:
    """Compute precision, recall, F1 from (predicted, actual) pairs.

    Returns (precision, recall, f1).  All values are 0.0 when the denominator
    would be zero (avoids ZeroDivisionError on degenerate inputs).
    """
    tp = sum(1 for pred, actual in results if pred and actual)
    fp = sum(1 for pred, actual in results if pred and not actual)
    fn = sum(1 for pred, actual in results if not pred and actual)

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall    = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1        = (
        2 * precision * recall / (precision + recall)
        if (precision + recall) > 0
        else 0.0
    )
    return precision, recall, f1


# ── Mock client for CI mode ────────────────────────────────────────────────────

def _mock_client_for(has_solar: bool) -> MagicMock:
    """Return a mock Anthropic client whose response matches the ground truth."""
    if has_solar:
        response_text = (
            "A distinct rectangular patch with uniform smoother surface is "
            "visible on part of the roof, clearly different from surrounding "
            "coarser tile texture — consistent with a photovoltaic array.\n"
            "HOUSE=YES\nSOLAR=YES"
        )
    else:
        response_text = (
            "Uniform roof surface with consistent texture throughout — no "
            "smooth rectangular patches or contrast areas visible.\n"
            "HOUSE=YES\nSOLAR=NO"
        )
    msg = MagicMock()
    msg.content = [MagicMock(text=response_text)]
    client = MagicMock()
    client.messages.create.return_value = msg
    return client


# ── TEST_CASES structure validation (always runs) ─────────────────────────────

class TestTestCasesStructure:
    """Validate the TEST_CASES fixture itself is well-formed.

    These checks always run in CI regardless of whether an API key is present.
    """

    def test_has_at_least_three_cases(self):
        assert len(TEST_CASES) >= 3, (
            "TEST_CASES must have at least 3 verified addresses"
        )

    def test_each_case_has_four_fields(self):
        for case in TEST_CASES:
            assert len(case) == 4, (
                f"Each test case must be (lat, lng, has_solar, label), got: {case!r}"
            )

    def test_coordinates_are_floats_in_sweden(self):
        """Lat/lng must be plausible Swedish coordinates (roughly 55–70 N, 10–25 E)."""
        for lat, lng, _has_solar, label in TEST_CASES:
            assert isinstance(lat, float), f"{label}: lat must be float"
            assert isinstance(lng, float), f"{label}: lng must be float"
            assert 55.0 <= lat <= 70.0, f"{label}: lat {lat} outside Sweden range"
            assert 10.0 <= lng <= 25.0, f"{label}: lng {lng} outside Sweden range"

    def test_has_solar_flags_are_bool(self):
        for lat, lng, has_solar, label in TEST_CASES:
            assert isinstance(has_solar, bool), (
                f"{label}: has_solar must be bool, got {type(has_solar).__name__}"
            )

    def test_labels_are_nonempty_strings(self):
        for _, _, _, label in TEST_CASES:
            assert isinstance(label, str) and label.strip(), (
                "Each test case label must be a non-empty string"
            )

    def test_at_least_one_positive_and_one_negative(self):
        positives = [c for c in TEST_CASES if c[2] is True]
        negatives = [c for c in TEST_CASES if c[2] is False]
        assert len(positives) >= 1, "TEST_CASES must include at least one solar_yes case"
        assert len(negatives) >= 1, "TEST_CASES must include at least one solar_no case"

    def test_no_duplicate_coordinates(self):
        coords = [(lat, lng) for lat, lng, _, _ in TEST_CASES]
        assert len(coords) == len(set(coords)), (
            "TEST_CASES contains duplicate (lat, lng) pairs"
        )


# ── Mock-mode F1 harness (always runs in CI) ──────────────────────────────────

class TestF1HarnessWithMock:
    """Run the full F1 scoring loop with deterministic mocks.

    Validates that the harness machinery works end-to-end:
    image bytes → _analyze_building → scoring → F1 >= 0.80.

    The mock client mirrors ground-truth verdicts perfectly, so F1 == 1.0.
    This also confirms nothing in the pipeline crashes.
    """

    def test_mock_f1_equals_one(self):
        """Perfect-mock run must yield F1 = 1.0 (the harness itself is correct)."""
        results: list[tuple[bool, bool]] = []

        for lat, lng, has_solar, label in TEST_CASES:
            client = _mock_client_for(has_solar)
            fake_img = b"\xff\xd8\xff" + b"\x00" * 64  # minimal JPEG-like bytes
            is_house, detected_solar, _unsure, _reasoning = _analyze_building(
                client, fake_img
            )
            predicted = is_house and detected_solar
            results.append((predicted, has_solar))

        precision, recall, f1 = _compute_f1(results)
        assert f1 == pytest.approx(1.0), (
            f"Mock-mode F1 should be 1.0, got {f1:.3f} "
            f"(precision={precision:.3f} recall={recall:.3f})"
        )

    def test_mock_precision_and_recall_are_both_one(self):
        """With a perfect mock, precision and recall must both be 1.0."""
        results: list[tuple[bool, bool]] = []
        for lat, lng, has_solar, label in TEST_CASES:
            client = _mock_client_for(has_solar)
            fake_img = b"\xff\xd8\xff" + b"\x00" * 64
            is_house, detected_solar, _unsure, _reasoning = _analyze_building(
                client, fake_img
            )
            results.append((is_house and detected_solar, has_solar))

        precision, recall, f1 = _compute_f1(results)
        assert precision == pytest.approx(1.0)
        assert recall    == pytest.approx(1.0)

    def test_f1_threshold_assertion_logic(self):
        """Confirm that _compute_f1 correctly triggers below 0.80."""
        # Simulate all negatives predicted — F1 should be 0.0
        all_negative_results = [(False, actual) for _, _, actual, *_ in
                                [(0, 0, True, "a"), (0, 0, False, "b")]]
        _, _, f1_zero = _compute_f1(all_negative_results)
        assert f1_zero == pytest.approx(0.0)

        # Simulate perfect results — F1 should be 1.0
        perfect_results = [(actual, actual) for _, _, actual, *_ in TEST_CASES]
        _, _, f1_perfect = _compute_f1(perfect_results)
        assert f1_perfect == pytest.approx(1.0)


# ── Real-API F1 test (skipped when no ANTHROPIC_API_KEY) ──────────────────────

@pytest.mark.skipif(
    not _HAS_API_KEY,
    reason="ANTHROPIC_API_KEY not set — real-API F1 test skipped in CI",
)
class TestF1BaselineRealAPI:
    """Real-image F1 test against verified Malmö addresses.

    Requires ANTHROPIC_API_KEY in the environment.
    Fetches live Lantmäteriet WMS images and calls Claude Vision.
    Asserts F1 >= 0.80 (documented threshold).
    """

    def test_f1_above_threshold(self):
        """Full pipeline F1 must be >= 0.80 on the verified baseline set."""
        import anthropic as _anthropic

        client = _anthropic.Anthropic()
        results: list[tuple[bool, bool]] = []
        failures: list[str] = []

        for lat, lng, has_solar, label in TEST_CASES:
            img_bytes = _fetch_lm_wms(lat, lng)
            if img_bytes is None:
                failures.append(f"{label}: _fetch_lm_wms returned None")
                # Count as false-negative to avoid silently skipping ground truth
                results.append((False, has_solar))
                continue

            is_house, detected_solar, _unsure, reasoning = _analyze_building(
                client, img_bytes
            )
            predicted = is_house and detected_solar
            results.append((predicted, has_solar))

            outcome = "TP" if (predicted and has_solar) else \
                      "TN" if (not predicted and not has_solar) else \
                      "FP" if (predicted and not has_solar) else "FN"
            print(
                f"  [{outcome}] {label} | "
                f"predicted={predicted} actual={has_solar} | {reasoning[:80]}"
            )

        precision, recall, f1 = _compute_f1(results)
        print(
            f"\nF1 baseline results: "
            f"precision={precision:.3f} recall={recall:.3f} F1={f1:.3f}"
        )
        if failures:
            print("Image fetch failures:", "\n  ".join(failures))

        assert f1 >= 0.80, (
            f"F1={f1:.3f} is below the 0.80 threshold "
            f"(precision={precision:.3f}, recall={recall:.3f}). "
            f"Image fetch failures: {failures}"
        )

    def test_all_images_fetchable(self):
        """Each TEST_CASES coordinate must return a non-None image from LM WMS."""
        unfetchable = []
        for lat, lng, _has_solar, label in TEST_CASES:
            img = _fetch_lm_wms(lat, lng)
            if img is None:
                unfetchable.append(label)

        assert not unfetchable, (
            f"Could not fetch LM WMS images for: {unfetchable}. "
            "Check network access to minkarta.lantmateriet.se."
        )

    def test_image_bytes_are_jpeg(self):
        """Images returned by _fetch_lm_wms must start with a JPEG magic number."""
        for lat, lng, _has_solar, label in TEST_CASES:
            img = _fetch_lm_wms(lat, lng)
            if img is None:
                pytest.skip(f"Could not fetch image for {label}")
            # JPEG magic bytes: FF D8 FF
            assert img[:2] == b"\xff\xd8", (
                f"{label}: expected JPEG (\\xff\\xd8), got {img[:4].hex()}"
            )


# ── Prompt content regression (always runs) ───────────────────────────────────

class TestPromptNordicDenyList:
    """The _analyze_building prompt must contain all Nordic-specific deny-list terms.

    These terms guard against the most common Swedish false-positive roof types.
    Regression: if the prompt is refactored and any term is dropped, this test fails.
    """

    REQUIRED_TERMS = [
        "tegelpannor",   # clay/concrete roof tiles — common Swedish FP source
        "eternite",      # fibre-cement / asbestos-cement tiles — common on older Swedish houses
        "snow",          # snow/frost patches — bright uniform areas in Nordic winter imagery
        "solfångare",    # solar thermal collectors — narrow-tube strips, NOT PV panels
    ]

    def _get_prompt_text(self) -> str:
        return inspect.getsource(scanner._analyze_building)

    def test_prompt_contains_tegelpannor(self):
        """Prompt must mention 'tegelpannor' (clay roof tiles)."""
        assert "tegelpannor" in self._get_prompt_text(), (
            "Prompt missing 'tegelpannor' — risk of clay-tile false positives"
        )

    def test_prompt_contains_eternite(self):
        """Prompt must mention 'eternite' (fibre-cement tiles)."""
        assert "eternite" in self._get_prompt_text().lower(), (
            "Prompt missing 'eternite' — risk of grey fibre-cement tile false positives"
        )

    def test_prompt_contains_snow(self):
        """Prompt must mention 'snow' (frost/snow patches common in Nordic winter)."""
        assert "snow" in self._get_prompt_text().lower(), (
            "Prompt missing 'snow' — risk of snow-patch false positives in winter imagery"
        )

    def test_prompt_contains_solfangare(self):
        """Prompt must mention 'solfångare' (solar thermal collectors)."""
        assert "solfångare" in self._get_prompt_text(), (
            "Prompt missing 'solfångare' — risk of thermal-collector false positives"
        )

    def test_all_required_terms_present(self):
        """Omnibus check: all REQUIRED_TERMS must appear in the prompt source."""
        prompt = self._get_prompt_text().lower()
        missing = [term for term in self.REQUIRED_TERMS if term.lower() not in prompt]
        assert not missing, (
            f"Prompt is missing Nordic deny-list terms: {missing}. "
            "These are required to suppress common Swedish false-positive roof types."
        )
