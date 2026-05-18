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
