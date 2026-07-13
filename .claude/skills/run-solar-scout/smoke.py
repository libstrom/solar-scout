#!/usr/bin/env python3
"""Smoke driver for Solar Scout — launches app, screenshots key pages.

Usage (from repo root):
    python3 .claude/skills/run-solar-scout/smoke.py [--port 8501] [--keep]

Screenshots land in /tmp/shots/solar-scout-*.png.
Exit 0 = app loaded and rendered. Exit 1 = failure.

NOTE: Must be run from repo root (where app.py lives).
"""
import argparse, os, subprocess, sys, time, urllib.request

def wait_for_port(port: int, timeout: int = 30) -> bool:
    for _ in range(timeout):
        try:
            urllib.request.urlopen(f"http://localhost:{port}", timeout=1)
            return True
        except Exception:
            time.sleep(1)
    return False


def run(port: int = 8501, keep: bool = False) -> int:
    os.makedirs("/tmp/shots", exist_ok=True)

    # Kill any existing process on this port (pkill -f avoids, it self-matches
    # the bash eval string and kills the shell — use fuser instead).
    subprocess.run(["fuser", "-k", f"{port}/tcp"], capture_output=True)
    time.sleep(1)

    # nohup is required — plain subprocess.Popen gets killed by the shell
    # on exit in some container environments (exit 144 / SIGSTKFLT).
    log_fh = open("/tmp/streamlit.log", "w")
    proc = subprocess.Popen(
        ["nohup", "python3", "-m", "streamlit", "run", "app.py",
         f"--server.port={port}", "--server.headless=true",
         "--server.runOnSave=false"],
        stdout=log_fh,
        stderr=subprocess.STDOUT,
    )

    if not wait_for_port(port, timeout=30):
        print("ERROR: Streamlit did not start within 30s", file=sys.stderr)
        with open("/tmp/streamlit.log") as _lf:
            print(_lf.read()[-500:], file=sys.stderr)
        proc.terminate()
        log_fh.close()
        return 1

    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--no-sandbox", "--disable-setuid-sandbox"])
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.goto(f"http://localhost:{port}", wait_until="networkidle", timeout=20000)
            try:
                page.wait_for_selector('[data-testid="stApp"]', timeout=12000)
                page.wait_for_timeout(2000)
            except Exception:
                pass

            shot = "/tmp/shots/solar-scout-login.png"
            page.screenshot(path=shot)
            print(f"Screenshot: {shot}")

            text = page.inner_text("body")
            if "Scout" not in text:
                print(f"ERROR: expected 'Scout' in page, got: {text[:200]}", file=sys.stderr)
                browser.close()
                return 1

            print("OK: Login page rendered correctly")
            browser.close()
        return 0
    finally:
        if not keep:
            proc.terminate()
        log_fh.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8501)
    ap.add_argument("--keep", action="store_true", help="keep server running after smoke")
    args = ap.parse_args()
    sys.exit(run(args.port, args.keep))
