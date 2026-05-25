"""
Unit tests för _prefilter_building — den billiga Haiku-grinden som körs FÖRE
Sonnet-anropet (_analyze_building) i _process_building. Syftet är att slänga
uppenbara icke-hus innan vi spenderar Sonnet-tokens — mest värde i glesbygds-
fallbackpasset där många icke-bostäder når AI:n.

Designprincip — GRINDEN ÄR TOLERANT:
  En felaktig AVVISNING tappar ett riktigt lead (dyrt, oåterkalleligt).
  En felaktig PASSERING kostar bara ett Sonnet-anrop (billigt).
  Därför: avvisa BARA på ett tydligt HOUSE=NO. Vid tvetydigt svar eller
  API-fel ska grinden släppa igenom (return True).

Körs utan API-nycklar via mockad Anthropic-klient.
"""

import sys
from unittest.mock import MagicMock

# Stub out packages unavailable in test environment before importing scanner
for _mod in ("googlemaps", "streamlit", "supabase", "stripe", "folium",
             "streamlit_folium", "openpyxl"):
    sys.modules.setdefault(_mod, MagicMock())

from scanner import _prefilter_building  # noqa: E402


def _make_client(response_text: str):
    """Returnera en mockad Anthropic-klient som svarar med response_text."""
    msg = MagicMock()
    msg.content = [MagicMock(text=response_text)]
    client = MagicMock()
    client.messages.create.return_value = msg
    return client


# ── Tydliga fall ───────────────────────────────────────────────────────────────

def test_prefilter_passes_clear_house():
    """HOUSE=YES → grinden släpper igenom (True), Sonnet får köra."""
    client = _make_client("HOUSE=YES")
    assert _prefilter_building(client, b"fake_image") is True


def test_prefilter_rejects_clear_non_house():
    """HOUSE=NO → grinden avvisar (False), Sonnet hoppas över."""
    client = _make_client("HOUSE=NO")
    assert _prefilter_building(client, b"fake_image") is False


def test_prefilter_rejects_lowercase_no():
    """Gemener 'house=no' (icke-konformt modellsvar) ska också avvisas — parsern kör .upper()."""
    client = _make_client("house=no")
    assert _prefilter_building(client, b"fake_image") is False


# ── Tolerans: tveksamma/trasiga svar ska SLÄPPA IGENOM ─────────────────────────

def test_prefilter_lenient_on_response_without_verdict():
    """Svar utan tydligt HOUSE=NO ska släppa igenom (hellre dyrt Sonnet än tappat lead)."""
    client = _make_client("Detta är svårt att avgöra från ovan.")
    assert _prefilter_building(client, b"fake_image") is True


def test_prefilter_lenient_when_both_verdicts_present():
    """Om både YES och NO förekommer i svaret → tvetydigt → släpp igenom."""
    client = _make_client("Could read as HOUSE=YES, but possibly HOUSE=NO.")
    assert _prefilter_building(client, b"fake_image") is True


def test_prefilter_passes_on_api_error():
    """API-fel ska ALDRIG tappa ett lead — grinden släpper igenom."""
    client = MagicMock()
    client.messages.create.side_effect = Exception("network error")
    assert _prefilter_building(client, b"fake_image") is True


def test_prefilter_passes_on_empty_response():
    """Tomt svar ska tolkas som 'osäkert' → släpp igenom."""
    client = _make_client("")
    assert _prefilter_building(client, b"fake_image") is True


# ── Kostnadskrav: måste använda Haiku och be om få tokens ──────────────────────

def test_prefilter_uses_haiku_model():
    """Grinden MÅSTE anropa en Haiku-modell — annars sparar den ingen kostnad
    jämfört med att bara köra Sonnet direkt."""
    client = _make_client("HOUSE=YES")
    _prefilter_building(client, b"fake_image")
    _args, kwargs = client.messages.create.call_args
    assert "haiku" in kwargs["model"].lower(), (
        f"Prefilter ska använda Haiku, fick model={kwargs.get('model')!r}"
    )


def test_prefilter_is_cheap_low_max_tokens():
    """Grinden behöver bara ett HOUSE=YES/NO-svar — be om få tokens."""
    client = _make_client("HOUSE=YES")
    _prefilter_building(client, b"fake_image")
    _args, kwargs = client.messages.create.call_args
    assert kwargs["max_tokens"] <= 32, (
        f"Prefilter ska be om få tokens (kostnad), fick max_tokens={kwargs.get('max_tokens')}"
    )


def test_prefilter_sends_image():
    """Grinden ska skicka bilden som ett image-block till modellen."""
    client = _make_client("HOUSE=YES")
    _prefilter_building(client, b"fake_image")
    _args, kwargs = client.messages.create.call_args
    content = kwargs["messages"][0]["content"]
    assert any(block.get("type") == "image" for block in content), (
        "Prefilter måste skicka ett image-block"
    )
