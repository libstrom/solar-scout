"""
test_lantmateriet.py — enheter för officiella Ortofoto Visning-WMS:en.

Verifierar att koden bygger rätt WMS GetMap-URL mot maps.lantmateriet.se,
tolkar credentials som Basic auth, och faller tillbaka snyggt vid fel.
Inga riktiga nätverksanrop — httpx mockas.
"""

import httpx
import pytest
from unittest.mock import patch, MagicMock

import scanner


# ── Basic auth-parsing ─────────────────────────────────────────────────────────

def test_basic_auth_parses_key_secret():
    assert scanner._lm_basic_auth("consumerkey:consumersecret") == ("consumerkey", "consumersecret")


def test_basic_auth_handles_secret_with_colon():
    # Secret kan i teorin innehålla kolon — bara första kolon splittar.
    assert scanner._lm_basic_auth("key:sec:ret") == ("key", "sec:ret")


def test_basic_auth_rejects_missing_colon():
    assert scanner._lm_basic_auth("bareToken") is None


def test_basic_auth_rejects_empty():
    assert scanner._lm_basic_auth("") is None


# ── WMS GetMap-URL ───────────────────────────────────────────────────────────

def test_wms_url_targets_official_endpoint():
    url = scanner._lm_wms_url("Ortofoto_0.16", 59.33, 18.07)
    assert url.startswith("https://maps.lantmateriet.se/ortofoto/wms/v1.3")
    assert "api.lantmateriet.se/open" not in url  # gamla felaktiga endpointen


def test_wms_url_contains_required_params():
    url = scanner._lm_wms_url("Ortofoto_0.16,Ortofoto_0.25", 57.65, 14.70)
    assert "SERVICE=WMS" in url
    assert "REQUEST=GetMap" in url
    assert "LAYERS=Ortofoto_0.16,Ortofoto_0.25" in url
    assert "SRS=EPSG:4326" in url
    assert "BBOX=" in url
    assert "FORMAT=image/jpeg" in url


def test_wms_url_bbox_centered_on_point():
    lat, lng = 57.65, 14.70
    url = scanner._lm_wms_url("Ortofoto_0.16", lat, lng)
    bbox_str = url.split("BBOX=")[1]
    minx, miny, maxx, maxy = (float(v) for v in bbox_str.split(","))
    # Punkten ska ligga i mitten av bboxen
    assert minx < lng < maxx
    assert miny < lat < maxy


# ── _fetch_lantmateriet ────────────────────────────────────────────────────────

def _img_response():
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {"content-type": "image/jpeg"}
    resp.content = b"\xff\xd8\xff\xe0jpegbytes"
    return resp


def test_fetch_returns_image_bytes_on_success():
    with patch("scanner.httpx.get", return_value=_img_response()) as mock_get:
        img = scanner._fetch_lantmateriet("key:secret", 59.33, 18.07)
    assert img == b"\xff\xd8\xff\xe0jpegbytes"
    # Basic auth ska ha skickats med
    _, kwargs = mock_get.call_args
    assert kwargs["auth"] == ("key", "secret")


def test_fetch_returns_none_on_bad_auth_format():
    # Utan kolon → ingen auth → None utan att ens anropa nätet
    with patch("scanner.httpx.get") as mock_get:
        img = scanner._fetch_lantmateriet("nocolon", 59.33, 18.07)
    assert img is None
    mock_get.assert_not_called()


def test_fetch_returns_none_on_401():
    resp = MagicMock()
    resp.status_code = 401
    resp.headers = {"content-type": "text/plain"}
    with patch("scanner.httpx.get", return_value=resp):
        img = scanner._fetch_lantmateriet("key:secret", 59.33, 18.07)
    assert img is None


def test_fetch_returns_none_on_network_error():
    with patch("scanner.httpx.get", side_effect=httpx.ConnectError("boom")):
        img = scanner._fetch_lantmateriet("key:secret", 59.33, 18.07)
    assert img is None


# ── _probe_lm_layer ────────────────────────────────────────────────────────────

def test_probe_returns_first_working_layer():
    with patch("scanner.httpx.get", return_value=_img_response()):
        layer = scanner._probe_lm_layer("key:secret")
    assert layer == scanner._LM_LAYERS[0]


def test_probe_returns_none_without_auth():
    with patch("scanner.httpx.get") as mock_get:
        layer = scanner._probe_lm_layer("nocolon")
    assert layer is None
    mock_get.assert_not_called()


def test_probe_returns_none_when_all_layers_fail():
    resp = MagicMock()
    resp.status_code = 403
    resp.headers = {"content-type": "text/plain"}
    with patch("scanner.httpx.get", return_value=resp):
        layer = scanner._probe_lm_layer("key:secret")
    assert layer is None
