"""
TDD: Cookie-baserad session-persistens.

Beteenden:
  1. do_login sätter cookies för access_token + refresh_token
  2. init_auth läser cookies om session_state är tom → användaren förblir inloggad
  3. do_logout rensar cookies
  4. init_auth returnerar None om varken session_state eller cookies finns
"""

import sys
from unittest.mock import MagicMock, patch, call

for _mod in ("googlemaps", "streamlit", "supabase", "stripe", "folium",
             "streamlit_folium", "openpyxl", "extra_streamlit_components"):
    sys.modules.setdefault(_mod, MagicMock())

import app


def _make_cookie_mock(data: dict | None = None):
    """Returnera en mock CookieManager med förinläst cookie-data."""
    cm = MagicMock()
    cookie_store = dict(data or {})
    cm.get.side_effect = lambda cookie_name: cookie_store.get(cookie_name)
    cm.set.side_effect = lambda cookie_name, value, **_kw: cookie_store.update({cookie_name: value})
    cm.delete.side_effect = lambda cookie_name, **_kw: cookie_store.pop(cookie_name, None)
    cm._store = cookie_store
    return cm


def _make_supabase(access="tok-access", refresh="tok-refresh"):
    """Returnera en mockad Supabase-klient som svarar med giltiga tokens."""
    user = MagicMock()
    user.id = "user-123"
    session = MagicMock()
    session.access_token = access
    session.refresh_token = refresh
    resp = MagicMock()
    resp.session = session
    resp.user = user
    sb = MagicMock()
    sb.auth.sign_in_with_password.return_value = resp
    sb.auth.set_session.return_value = None
    sb.auth.get_user.return_value = resp
    sb.auth.refresh_session.return_value = resp
    return sb, user


# ── 1. do_login sätter cookies ─────────────────────────────────────────────────

def test_do_login_sets_access_token_cookie():
    """do_login ska kalla cookie_manager.set('access_token', ...) efter lyckad login."""
    sb, _ = _make_supabase(access="AT-abc")
    cm = _make_cookie_mock()

    with patch("app.create_client", return_value=sb), \
         patch("app._get_cookie_manager", return_value=cm):
        app.do_login("test@example.com", "pass")

    assert cm._store.get("access_token") == "AT-abc"


def test_do_login_sets_refresh_token_cookie():
    """do_login ska kalla cookie_manager.set('refresh_token', ...) efter lyckad login."""
    sb, _ = _make_supabase(refresh="RT-xyz")
    cm = _make_cookie_mock()

    with patch("app.create_client", return_value=sb), \
         patch("app._get_cookie_manager", return_value=cm):
        app.do_login("test@example.com", "pass")

    assert cm._store.get("refresh_token") == "RT-xyz"


# ── 2. init_auth läser cookies om session_state är tom ────────────────────────

def test_init_auth_restores_session_from_cookies():
    """Om session_state är tom men cookies finns → init_auth återställer sessionen."""
    sb, user = _make_supabase(access="AT-cookie", refresh="RT-cookie")
    cm = _make_cookie_mock({"access_token": "AT-cookie", "refresh_token": "RT-cookie"})

    # session_state är tom (inga tokens)
    st_mock = sys.modules["streamlit"]
    st_mock.session_state = {}

    with patch("app.get_supabase", return_value=sb), \
         patch("app._get_cookie_manager", return_value=cm):
        result = app.init_auth()

    assert result is not None, "init_auth ska returnera user när cookies finns"


def test_init_auth_returns_none_without_session_or_cookies():
    """Om varken session_state eller cookies finns → init_auth returnerar None."""
    cm = _make_cookie_mock({})  # inga cookies

    st_mock = sys.modules["streamlit"]
    st_mock.session_state = {}

    with patch("app._get_cookie_manager", return_value=cm):
        result = app.init_auth()

    assert result is None


# ── 3. do_logout rensar cookies ───────────────────────────────────────────────

def test_do_logout_removes_access_token_cookie():
    """do_logout ska rensa access_token-cookien."""
    sb = MagicMock()
    cm = _make_cookie_mock({"access_token": "AT", "refresh_token": "RT"})

    with patch("app.get_supabase", return_value=sb), \
         patch("app._get_cookie_manager", return_value=cm):
        app.do_logout()

    assert "access_token" not in cm._store


def test_do_logout_removes_refresh_token_cookie():
    """do_logout ska rensa refresh_token-cookien."""
    sb = MagicMock()
    cm = _make_cookie_mock({"access_token": "AT", "refresh_token": "RT"})

    with patch("app.get_supabase", return_value=sb), \
         patch("app._get_cookie_manager", return_value=cm):
        app.do_logout()

    assert "refresh_token" not in cm._store
