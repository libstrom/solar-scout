"""
Token-free end-to-end smoke harness for the scanner AI pipeline.

Exercises `_process_building` from satellite-image fetch through the Haiku
prefilter, Opus analysis, and Lead construction — WITHOUT any live API calls.
Anthropic is a scriptable fake client; image bytes are real synthetic
JPEG/PNG generated in-memory with Pillow.

Regression coverage for the class of bugs recently hit:
  1. Haiku prefilter analysing the RAW image while Opus analyses the
     CLAHE-enhanced image — solar roofs only visible after enhancement get
     gated out before Opus ever sees them. (see test_prefilter_sees_enhanced_image)
  2. `_process_building` returning None for SOLAR=NO (no negatives persisted).
  3. Anthropic HTTP 400 "credit balance" must raise APIQuotaExceededError;
     other 400s are swallowed.
  4. Both prefilter and analyse hardcode media_type "image/jpeg" — a PNG
     fetched from a fallback source is mislabelled.
"""

import base64
import io
import sys
from unittest.mock import MagicMock

import anthropic
import httpx
import pytest

# Stub out packages unavailable in the test environment before importing scanner.
for _mod in ("googlemaps", "streamlit", "supabase", "stripe", "folium",
             "streamlit_folium", "openpyxl"):
    sys.modules.setdefault(_mod, MagicMock())

import scanner  # noqa: E402
from scanner import _process_building, Lead, APIQuotaExceededError  # noqa: E402


# ── Synthetic image bytes ──────────────────────────────────────────────────────

def _synthetic_jpeg() -> bytes:
    """A real, decodable JPEG with non-uniform content so CLAHE has something
    to chew on (4×4 tiles, each must have >0 pixels)."""
    from PIL import Image
    img = Image.new("RGB", (64, 64))
    px = img.load()
    for y in range(64):
        for x in range(64):
            # gradient + a brighter rectangular "panel" patch
            v = (x * 3 + y * 2) % 256
            if 10 <= x <= 30 and 10 <= y <= 25:
                v = 240
            px[x, y] = (v, v // 2, 255 - v)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def _synthetic_png() -> bytes:
    """A real, decodable PNG — exercises the non-JPEG fallback-source path."""
    from PIL import Image
    img = Image.new("RGB", (64, 64))
    px = img.load()
    for y in range(64):
        for x in range(64):
            px[x, y] = ((x * 4) % 256, (y * 4) % 256, 128)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


SYNTH_JPEG = _synthetic_jpeg()
SYNTH_PNG = _synthetic_png()


# ── Scriptable fake Anthropic client ───────────────────────────────────────────

class _FakeAnthropic:
    """Records every messages.create call and returns scripted text keyed by
    model family (haiku vs opus). Captures the base64 image data of the FIRST
    image block in each call so tests can assert which bytes each stage saw.
    """

    def __init__(self, *, haiku_text="YES", opus_text="HOUSE=YES\nSOLAR=YES",
                 haiku_error=None, opus_error=None):
        self.haiku_text = haiku_text
        self.opus_text = opus_text
        self.haiku_error = haiku_error
        self.opus_error = opus_error
        self.calls = []  # list of {"model", "media_types", "image_b64s", "is_haiku"}
        self.messages = MagicMock()
        self.messages.create.side_effect = self._create

    @staticmethod
    def _extract_images(messages):
        media_types, image_b64s = [], []
        for m in messages:
            content = m.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if isinstance(block, dict) and block.get("type") == "image":
                    src = block.get("source", {})
                    media_types.append(src.get("media_type"))
                    image_b64s.append(src.get("data"))
        return media_types, image_b64s

    def _create(self, *, model, messages, **kwargs):
        is_haiku = "haiku" in model
        media_types, image_b64s = self._extract_images(messages)
        self.calls.append({
            "model": model,
            "is_haiku": is_haiku,
            "media_types": media_types,
            "image_b64s": image_b64s,
        })
        if is_haiku and self.haiku_error is not None:
            raise self.haiku_error
        if not is_haiku and self.opus_error is not None:
            raise self.opus_error
        text = self.haiku_text if is_haiku else self.opus_text
        msg = MagicMock()
        msg.content = [MagicMock(text=text)]
        msg.usage = MagicMock(
            input_tokens=10, output_tokens=5,
            cache_read_input_tokens=0, cache_creation_input_tokens=0,
        )
        return msg

    @property
    def haiku_calls(self):
        return [c for c in self.calls if c["is_haiku"]]

    @property
    def opus_calls(self):
        return [c for c in self.calls if not c["is_haiku"]]


# ── Fixtures ────────────────────────────────────────────────────────────────────

@pytest.fixture
def building():
    return {
        "lat": 57.65,
        "lng": 14.70,
        "address": "Testgatan 1, Nässjö",
        "osm_id": "way/123456",
        "building_type": "house",
        "zoom": 20,
    }


@pytest.fixture
def patched(monkeypatch):
    """Patch every network/IO boundary in _process_building so it runs offline.

    Returns the satellite image bytes used (mutable via .img attr on the holder).
    """
    holder = type("H", (), {"img": SYNTH_JPEG})()

    monkeypatch.setattr(scanner, "_is_existing_customer", lambda *a, **k: False)
    monkeypatch.setattr(
        scanner, "_fetch_satellite",
        lambda *a, **k: holder.img,
    )
    # No extra OSM solar nearby and no villa nearby (so SOLAR=NO ⇒ None as today).
    monkeypatch.setattr(
        scanner, "_has_extra_solar_nearby",
        lambda *a, **k: {"extra_solar_found": False, "solar_locations": [], "villa_nearby": False},
    )
    # lm_wms_url is pure string-building, but pin it to avoid any drift.
    monkeypatch.setattr(scanner, "lm_wms_url", lambda lat, lng, **k: f"https://lm/{lat},{lng}")
    monkeypatch.setattr(scanner, "_fetch_street_view", lambda *a, **k: None)
    # Reset the Google circuit breaker so Street View logic is reachable.
    scanner._google_exhausted.clear()
    return holder


# ── (d) End-to-end outcomes ─────────────────────────────────────────────────────

def test_clear_solar_yields_confirmed_lead(building, patched):
    client = _FakeAnthropic(haiku_text="YES", opus_text="A clear PV array.\nHOUSE=YES\nSOLAR=YES")
    lead = _process_building(building, "gkey", client)
    assert isinstance(lead, Lead)
    assert lead.needs_review is False
    assert lead.source == "ai"
    assert lead.confidence == 0.90
    assert lead.tile_key == "bld/way/123456"
    # both stages ran
    assert len(client.haiku_calls) == 1
    assert len(client.opus_calls) == 1


def test_no_solar_returns_none(building, patched):
    """Documents CURRENT behaviour: SOLAR=NO (no samtomt extra) ⇒ None.

    NOTE: this is diagnosed bug #2 — negatives are never persisted. When the
    fix lands (a negative/false_positive Lead is returned instead of None),
    this assertion must be updated. See the module docstring.
    """
    client = _FakeAnthropic(haiku_text="YES", opus_text="Plain clay tile.\nHOUSE=YES\nSOLAR=NO")
    lead = _process_building(building, "gkey", client)
    assert lead is None


def test_unsure_yields_review_lead(building, patched):
    client = _FakeAnthropic(haiku_text="YES", opus_text="Possible panels.\nHOUSE=YES\nSOLAR=UNSURE")
    lead = _process_building(building, "gkey", client)
    assert isinstance(lead, Lead)
    assert lead.needs_review is True
    assert lead.confidence == 0.50


def test_non_house_returns_none(building, patched):
    client = _FakeAnthropic(haiku_text="YES", opus_text="A warehouse.\nHOUSE=NO\nSOLAR=NO")
    lead = _process_building(building, "gkey", client)
    assert lead is None
    # Opus was consulted but rejected as non-house.
    assert len(client.opus_calls) == 1


def test_haiku_prefilter_no_gates_out_building(building, patched):
    """Haiku says NO ⇒ Opus is never called and no Lead is produced."""
    client = _FakeAnthropic(haiku_text="NO")
    lead = _process_building(building, "gkey", client)
    assert lead is None
    assert len(client.haiku_calls) == 1
    assert len(client.opus_calls) == 0


# ── (e) Prefilter must see the SAME enhanced bytes Opus sees ─────────────────────

def test_prefilter_sees_enhanced_image(building, patched):
    """REGRESSION (diagnosed bug #1): the Haiku prefilter must analyse the same
    CLAHE-enhanced image that Opus analyses. Today `_prefilter_building` is fed
    the raw bytes while `_analyze_building` enhances first, so a solar roof only
    visible after enhancement is gated out before Opus sees it.

    Post-fix expectation: the base64 image the Haiku call receives equals the
    base64 image the Opus call receives (both enhanced). This xfails until the
    enhance-before-prefilter fix lands.
    """
    client = _FakeAnthropic(haiku_text="YES", opus_text="HOUSE=YES\nSOLAR=YES")
    _process_building(building, "gkey", client)

    assert client.haiku_calls and client.opus_calls
    haiku_b64 = client.haiku_calls[0]["image_b64s"][0]
    opus_b64 = client.opus_calls[0]["image_b64s"][0]

    # Post-fix: the prefilter and Opus must see the identical CLAHE-enhanced
    # image (enhancement happens once in _process_building, before the gate).
    assert haiku_b64 == opus_b64, (
        "Haiku prefilter must analyse the same enhanced image as Opus — "
        "otherwise faint panels visible only after CLAHE are gated out "
        "before Opus is consulted (regression of diagnosed bug #1)."
    )


def test_prefilter_and_analyze_enhanced_bytes_decode(building, patched):
    """Whatever the prefilter is fed, it must be a real decodable image (guards
    against passing un-decodable / wrong-format bytes to the model)."""
    from PIL import Image
    client = _FakeAnthropic(haiku_text="YES", opus_text="HOUSE=YES\nSOLAR=YES")
    _process_building(building, "gkey", client)
    for call in client.calls:
        for b64 in call["image_b64s"]:
            raw = base64.standard_b64decode(b64)
            Image.open(io.BytesIO(raw)).verify()  # raises if not a valid image


# ── Media-type labelling (diagnosed bug #4) ──────────────────────────────────────

def test_png_fallback_image_media_type(building, patched):
    """A PNG fetched from a fallback source flows through the pipeline. The
    pipeline hardcodes media_type 'image/jpeg' for every image block.

    `_analyze_building` re-encodes to JPEG via `_enhance_contrast`, so its block
    is genuinely JPEG. The Haiku prefilter, however, forwards the original PNG
    bytes labelled as 'image/jpeg' — a real Anthropic API would reject that
    mismatch. Documents the bug; xfails until media_type is derived from bytes.
    """
    patched.img = SYNTH_PNG
    client = _FakeAnthropic(haiku_text="YES", opus_text="HOUSE=YES\nSOLAR=YES")
    _process_building(building, "gkey", client)

    haiku_call = client.haiku_calls[0]
    declared = haiku_call["media_types"][0]
    actual_bytes = base64.standard_b64decode(haiku_call["image_b64s"][0])
    is_png = actual_bytes[:8] == b"\x89PNG\r\n\x1a\n"

    # Post-fix: the declared media_type must match the actual image bytes,
    # whether the prefilter sees JPEG (re-encoded by CLAHE) or PNG (raw
    # fallback when enhancement is unavailable). Never a mismatch.
    assert (declared == "image/png") == is_png, (
        f"declared media_type {declared!r} does not match actual bytes "
        f"(is_png={is_png}) — regression of diagnosed bug #4."
    )


# ── Credit-balance vs other 400 propagation through the full pipeline ────────────

def _status_error(message: str, status_code: int = 400):
    response = httpx.Response(
        status_code=status_code,
        request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"),
    )
    return anthropic.APIStatusError(message, response=response, body=None)


def test_credit_balance_400_raises_quota_through_pipeline(building, patched):
    """HTTP 400 'credit balance is too low' from Opus must propagate as
    APIQuotaExceededError out of _process_building — not be swallowed into a
    silent None that looks like 'no solar' (diagnosed bug #3)."""
    client = _FakeAnthropic(
        haiku_text="YES",
        opus_error=_status_error("Your credit balance is too low to access the Anthropic API."),
    )
    with pytest.raises(APIQuotaExceededError):
        _process_building(building, "gkey", client)


def test_other_400_swallowed_yields_none(building, patched):
    """A non-credit 400 (e.g. invalid image) is swallowed in _analyze_building
    (returns False,False,False), so _process_building yields None without
    crashing."""
    client = _FakeAnthropic(
        haiku_text="YES",
        opus_error=_status_error("messages.0.content.0.image: invalid base64 data"),
    )
    lead = _process_building(building, "gkey", client)
    assert lead is None


def test_haiku_prefilter_fails_open_on_error(building, patched):
    """If the Haiku prefilter raises, it fails OPEN (returns True) so Opus still
    decides — a prefilter outage must not silently drop every building."""
    client = _FakeAnthropic(
        haiku_error=Exception("haiku network blip"),
        opus_text="HOUSE=YES\nSOLAR=YES",
    )
    lead = _process_building(building, "gkey", client)
    assert isinstance(lead, Lead)
    assert len(client.opus_calls) == 1
