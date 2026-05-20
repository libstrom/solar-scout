"""
Regressionstester för AI-promptens innehåll.

Verifierar att prompten inte innehåller gammal "prefer SOLAR=YES"-bias
och att SOLAR=NO-defaulten är explicit. Fångar promptregressioner utan
att anropa Claude API.
"""

import sys
from unittest.mock import MagicMock
import inspect

for _mod in ("googlemaps", "streamlit", "supabase", "stripe", "folium",
             "streamlit_folium", "openpyxl"):
    sys.modules.setdefault(_mod, MagicMock())

import scanner


def _get_prompt_text() -> str:
    src = inspect.getsource(scanner._analyze_building)
    # Extrahera promptsträngen ur källkoden
    return src


def test_prompt_does_not_prefer_solar_yes():
    """Prompten får INTE innehålla 'prefer SOLAR=YES'."""
    prompt = _get_prompt_text()
    assert "prefer SOLAR=YES" not in prompt, (
        "Prompten innehåller fortfarande gammal 'prefer SOLAR=YES'-bias!"
    )


def test_prompt_defaults_to_solar_no_when_uncertain():
    """Prompten ska hantera osäkerhet konservativt — UNSURE eller NO, aldrig YES."""
    prompt = _get_prompt_text()
    assert "uncertain" in prompt.lower(), "Prompten saknar 'uncertain'-instruktion"
    # SOLAR=UNSURE är nu tillåtet för osäkra fall (leder till human review)
    # men SOLAR=YES ska aldrig vara default vid osäkerhet
    assert "SOLAR=UNSURE" in prompt or "SOLAR=NO" in prompt, (
        "Prompten saknar SOLAR=UNSURE eller SOLAR=NO för osäkra fall"
    )
    assert "prefer SOLAR=YES" not in prompt, (
        "Prompten kopplar fortfarande osäkerhet till SOLAR=YES"
    )


def test_prompt_lists_false_positive_roof_types():
    """Prompten ska explicit nämna tak-typer som INTE är solceller."""
    prompt = _get_prompt_text().lower()
    false_positive_terms = ["epdm", "metal", "asphalt", "skylight"]
    for term in false_positive_terms:
        assert term in prompt, (
            f"Prompten saknar '{term}' i deny-listan för falska positiver"
        )


def test_prompt_requires_clear_identification():
    """Prompten ska kräva tydlig identifiering, inte 'plausible features'."""
    prompt = _get_prompt_text()
    assert "clearly" in prompt.lower() or "clear" in prompt.lower(), (
        "Prompten ska kräva att paneler är tydligt identifierbara"
    )
    # Den gamla varianten tillät "any plausible solar-panel features"
    assert "any plausible" not in prompt.lower(), (
        "Prompten innehåller fortfarande 'any plausible' — för brett kriterium"
    )


def test_prompt_denies_standing_seam_metal_roof():
    """Standing seam metal roof (plåttak) ska vara i deny-listan.

    Plåttak är ett vanligt falskt positivt i Sverige — uniformt slät yta med
    parallella skarvar som kan se ut som solpaneler om inte prompten
    explicit nämner dem.
    """
    prompt = _get_prompt_text().lower()
    assert "standing seam" in prompt or "plåttak" in prompt, (
        "Prompten saknar 'standing seam' / 'plåttak' i deny-listan — "
        "vanlig svensk falsk-positiv"
    )


def test_prompt_unsure_tier_covers_ambiguous_eternite_with_smooth_patch():
    """SOLAR=UNSURE ska finnas för fall som eternite + slät fläck (ambiguous).

    Om ett tak har eternite men också en distinkt slät rektangulär fläck
    ska modellen välja SOLAR=UNSURE, inte gissa SOLAR=YES eller SOLAR=NO.
    Prompten måste (1) nämna eternite i deny-listan OCH (2) erbjuda UNSURE-tieren
    för tvetydiga fall.
    """
    prompt = _get_prompt_text()
    assert "eternite" in prompt.lower() or "fibre-cement" in prompt.lower(), (
        "Prompten saknar eternite i deny-listan"
    )
    assert "SOLAR=UNSURE" in prompt, (
        "Prompten saknar SOLAR=UNSURE-tieren — kan inte hantera tvetydiga eternitetak"
    )
    # Bekräfta att UNSURE är avsett för osäkra fall, inte som default för YES
    assert "uncertain" in prompt.lower() or "could be" in prompt.lower(), (
        "Prompten beskriver inte när UNSURE ska användas (osäkert/tvetydigt fall)"
    )


def test_prompt_smoothness_contrast_is_primary_signal():
    """Smoothness contrast vs surrounding tiles ska vara det primära signalet.

    Detta är nyckeln till att undvika falska positiver på typiska Småland-tak
    (lertegelpannor med ojämn yta) — om modellen inte ser en distinkt slätare
    rektangelplatta mot omgivande tegelpannor ska den säga SOLAR=NO.
    """
    prompt = _get_prompt_text().lower()
    # Prompten ska explicit nämna smoothness contrast som primär signal
    assert "smoothness contrast" in prompt or (
        "smooth" in prompt and "contrast" in prompt
    ), (
        "Prompten saknar 'smoothness contrast' som primär signal — "
        "riskerar falska positiver på tegeltakens ojämna yta"
    )
    # Prompten ska säga SOLAR=NO om ingen distinkt slätare fläck syns
    assert "solar=no" in prompt, (
        "Prompten saknar explicit SOLAR=NO-fallback när ingen slätare fläck syns"
    )
