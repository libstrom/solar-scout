"""
E2E test fixtures — spins up a real Streamlit process and connects Playwright to it.

Run:
    playwright install chromium
    python -m pytest tests/e2e/ -v

The server starts once per session (scope="session") so all tests share it.
"""
import os
import subprocess
import time
import pytest
import httpx

BASE_URL = "http://localhost:8502"
_STARTUP_TIMEOUT = 30  # seconds


@pytest.fixture(scope="session")
def streamlit_server():
    """Launch app.py on port 8502 and wait until the health endpoint responds."""
    env = {
        **os.environ,
        # Minimal fake secrets so the app boots without crashing.
        # The login page renders before any real API calls are made.
        "SUPABASE_URL": os.environ.get("SUPABASE_URL", "https://fake.supabase.co"),
        "SUPABASE_ANON_KEY": os.environ.get("SUPABASE_ANON_KEY", "fake-anon-key"),
        "STREAMLIT_SERVER_HEADLESS": "true",
        "STREAMLIT_BROWSER_GATHER_USAGE_STATS": "false",
        "STREAMLIT_GLOBAL_DEVELOPMENT_MODE": "false",
    }
    proc = subprocess.Popen(
        [
            "streamlit", "run", "app.py",
            "--server.port=8502",
            "--server.headless=true",
            "--browser.gatherUsageStats=false",
        ],
        env=env,
        cwd=os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    deadline = time.time() + _STARTUP_TIMEOUT
    while time.time() < deadline:
        try:
            r = httpx.get(f"{BASE_URL}/healthz", timeout=1.0)
            if r.status_code == 200:
                break
        except Exception:
            pass
        time.sleep(0.5)
    else:
        proc.kill()
        stderr = proc.stderr.read().decode() if proc.stderr else ""
        raise RuntimeError(
            f"Streamlit did not start within {_STARTUP_TIMEOUT}s.\n{stderr}"
        )

    yield BASE_URL

    proc.kill()
    proc.wait()


@pytest.fixture
def app_page(page, streamlit_server):
    """Playwright page already navigated to the app root."""
    page.goto(streamlit_server)
    # Wait for Streamlit's React shell to mount
    page.wait_for_selector('[data-testid="stApp"]', timeout=15_000)
    return page
