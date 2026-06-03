"""
E2E smoke tests — verify the app loads and shows the login form without crashing.

These run against a real Streamlit process (no mocks).
"""
import re
import pytest

pytestmark = pytest.mark.e2e


class TestPageLoad:
    def test_page_title(self, app_page):
        assert "Scout" in app_page.title()

    def test_stapp_mounts(self, app_page):
        """Streamlit's root element must be present — means no startup crash."""
        el = app_page.locator('[data-testid="stApp"]')
        assert el.count() == 1

    def test_no_unhandled_exceptions(self, app_page):
        """No Streamlit exception banner (red box) on first render."""
        # st.exception renders a div with class 'stException'
        assert app_page.locator(".stException").count() == 0


class TestLoginForm:
    def test_email_input_visible(self, app_page):
        """Login page must show an e-mail field before auth."""
        inputs = app_page.locator('input[type="email"], input[placeholder*="mail"]')
        assert inputs.count() >= 1

    def test_login_button_visible(self, app_page):
        """A login / sign-in button must be visible."""
        # Match Swedish "Logga in" or English "Login"/"Sign in"
        btn = app_page.get_by_role("button", name=re.compile(r"[Ll]ogga|[Ll]ogin|[Ss]ign"))
        assert btn.count() >= 1

    def test_no_console_errors(self, app_page):
        """Capture browser console errors — none should appear on the login page."""
        errors = []
        app_page.on("console", lambda msg: errors.append(msg) if msg.type == "error" else None)
        app_page.reload()
        app_page.wait_for_selector('[data-testid="stApp"]', timeout=15_000)
        # Filter out known benign third-party noise
        real_errors = [
            e for e in errors
            if "favicon" not in e.text.lower()
            and "sourcemap" not in e.text.lower()
        ]
        assert real_errors == [], f"Console errors: {[e.text for e in real_errors]}"


class TestTabStructure:
    def test_four_tabs_after_login(self, app_page):
        """
        After a successful login the app must render exactly 4 tabs.
        This test is skipped if no SUPABASE_URL/test credentials are available.
        """
        import os
        if not os.environ.get("E2E_TEST_EMAIL"):
            pytest.skip("E2E_TEST_EMAIL not set — skip authenticated tab test")

        email = os.environ["E2E_TEST_EMAIL"]
        password = os.environ["E2E_TEST_PASSWORD"]

        app_page.get_by_placeholder("mail").fill(email)
        app_page.get_by_label("Lösenord").fill(password)
        app_page.get_by_role("button", name=re.compile(r"[Ll]ogga")).click()
        app_page.wait_for_selector('[data-testid="stTabs"]', timeout=10_000)

        tab_buttons = app_page.locator('[data-testid="stTab"]')
        assert tab_buttons.count() == 4
