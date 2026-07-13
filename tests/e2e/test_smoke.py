"""
E2E smoke tests — verify the app loads and shows the login form without crashing.

These run against a real Streamlit process (no mocks).

NOTE on waits: Streamlit serves an HTML shell immediately; the React app
hydrates 1–3 s later and the page title / widgets appear after that. Every
assertion therefore uses Playwright's `expect` (auto-waiting) instead of
instant `count()` checks, which raced the hydration and failed in CI.
"""
import re
import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e

_HYDRATION_TIMEOUT = 15_000  # ms — CI runners are slow on first paint


class TestPageLoad:
    def test_page_title(self, app_page):
        # Title flips from "Streamlit" to "Scout · ..." once set_page_config runs.
        expect(app_page).to_have_title(re.compile("Scout"), timeout=_HYDRATION_TIMEOUT)

    def test_stapp_mounts(self, app_page):
        """Streamlit's root element must be present — means no startup crash."""
        expect(app_page.locator('[data-testid="stApp"]')).to_be_visible(
            timeout=_HYDRATION_TIMEOUT
        )

    def test_no_unhandled_exceptions(self, app_page):
        """No Streamlit exception banner (red box) on first render."""
        # Wait until the app actually rendered content before asserting absence.
        expect(app_page.locator('[data-testid="stApp"]')).to_be_visible(
            timeout=_HYDRATION_TIMEOUT
        )
        assert app_page.locator(".stException").count() == 0


class TestLoginForm:
    def test_email_input_visible(self, app_page):
        """Login page must show an e-mail field before auth.

        Streamlit's st.text_input renders type="text" (never type="email"),
        labelled via aria-label — match the Swedish label used in page_auth.
        """
        field = app_page.get_by_label(re.compile("E-post", re.IGNORECASE))
        expect(field.first).to_be_visible(timeout=_HYDRATION_TIMEOUT)

    def test_login_button_visible(self, app_page):
        """A login / sign-in button must be visible."""
        # Match Swedish "Logga in" or English "Login"/"Sign in"
        btn = app_page.get_by_role("button", name=re.compile(r"[Ll]ogga|[Ll]ogin|[Ss]ign"))
        expect(btn.first).to_be_visible(timeout=_HYDRATION_TIMEOUT)

    def test_no_console_errors(self, app_page):
        """Capture browser console errors — none should appear on the login page."""
        errors = []
        app_page.on("console", lambda msg: errors.append(msg) if msg.type == "error" else None)
        app_page.reload()
        app_page.wait_for_selector('[data-testid="stApp"]', timeout=_HYDRATION_TIMEOUT)
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

        app_page.get_by_label(re.compile("E-post", re.IGNORECASE)).fill(email)
        app_page.get_by_label("Lösenord").fill(password)
        app_page.get_by_role("button", name=re.compile(r"[Ll]ogga")).click()
        app_page.wait_for_selector('[data-testid="stTabs"]', timeout=10_000)

        tab_buttons = app_page.locator('[data-testid="stTab"]')
        assert tab_buttons.count() == 4
