"""
Visual QA loop — screenshot → Claude vision evaluation → fix signal.

Usage (from Claude's workflow after pushing UI changes):

    from tools.visual_qa import screenshot_html, screenshot_url, evaluate, qa_result

    # Option A: isolated HTML snippet
    result = qa_result(
        screenshot_fn=lambda path: screenshot_html(MY_HTML, path),
        criteria="The table shows a 🛰 icon. Hovering it renders a thumbnail div.",
    )

    # Option B: live Streamlit page (app must already be running)
    result = qa_result(
        screenshot_fn=lambda path: screenshot_url("http://localhost:8501", path),
        criteria="Login form is visible with Swedish text.",
    )

    print(result.passed, result.feedback)

This module does NOT loop or make fixes — it is one evaluation step.
The caller (Claude) owns the loop: inspect result, fix, re-run, notify user.
"""

from __future__ import annotations

import base64
import os
import textwrap
import time
from dataclasses import dataclass
from pathlib import Path

SCREENSHOT_DIR = Path(__file__).parent.parent / "screenshots"
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)


# ── Screenshot helpers ────────────────────────────────────────────────────────

def screenshot_url(
    url: str,
    out_path: str | Path,
    *,
    viewport: tuple[int, int] = (1280, 900),
    wait_ms: int = 2500,
    hover_selector: str | None = None,
) -> Path:
    """Navigate to *url*, optionally hover *hover_selector*, then save PNG."""
    from playwright.sync_api import sync_playwright  # noqa: PLC0415

    out = Path(out_path)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": viewport[0], "height": viewport[1]})
        page.goto(url, wait_until="networkidle", timeout=30_000)
        page.wait_for_timeout(wait_ms)
        if hover_selector:
            try:
                page.hover(hover_selector, timeout=5_000)
                page.wait_for_timeout(600)  # let CSS transition complete
            except Exception:
                pass  # selector not found — screenshot anyway
        page.screenshot(path=str(out), full_page=False)
        browser.close()
    return out


def screenshot_html(
    html: str,
    out_path: str | Path,
    *,
    viewport: tuple[int, int] = (1280, 900),
    hover_selector: str | None = None,
) -> Path:
    """Render raw *html* in a blank browser page and save PNG."""
    from playwright.sync_api import sync_playwright  # noqa: PLC0415

    out = Path(out_path)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": viewport[0], "height": viewport[1]})
        page.set_content(html, wait_until="networkidle")
        page.wait_for_timeout(500)
        if hover_selector:
            try:
                page.hover(hover_selector, timeout=5_000)
                page.wait_for_timeout(600)
            except Exception:
                pass
        page.screenshot(path=str(out), full_page=True)
        browser.close()
    return out


# ── Claude vision evaluation ──────────────────────────────────────────────────

@dataclass
class QAResult:
    passed: bool
    feedback: str
    screenshot_path: Path
    iteration: int


def evaluate(screenshot_path: str | Path, criteria: str) -> QAResult:
    """
    Ask Claude (claude-opus-4-8) to evaluate a screenshot against *criteria*.
    Returns QAResult with passed=True/False and written feedback.
    """
    import anthropic  # noqa: PLC0415

    api_key = (
        os.environ.get("ANTHROPIC_API_KEY")
        or os.environ.get("SOLAR_SCOUT_ANTHROPIC_KEY")
        or ""
    )
    if not api_key:
        return QAResult(
            passed=False,
            feedback="ANTHROPIC_API_KEY not set — cannot evaluate screenshot.",
            screenshot_path=Path(screenshot_path),
            iteration=0,
        )

    img_bytes = Path(screenshot_path).read_bytes()
    b64 = base64.standard_b64encode(img_bytes).decode()

    client = anthropic.Anthropic(api_key=api_key)
    system = textwrap.dedent("""
        You are a visual QA reviewer for a Streamlit web application.
        You will be given a screenshot and a set of criteria.
        Reply ONLY with a JSON object:
        {
          "passed": true | false,
          "feedback": "one sentence — what passes and what fails"
        }
        Be strict: if the criterion is not clearly visible in the screenshot, mark passed=false.
    """).strip()

    msg = client.messages.create(
        model="claude-opus-4-8",
        max_tokens=256,
        system=system,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": "image/png", "data": b64},
                    },
                    {
                        "type": "text",
                        "text": f"Evaluate this screenshot against these criteria:\n{criteria}",
                    },
                ],
            }
        ],
    )

    raw = msg.content[0].text.strip()
    import json  # noqa: PLC0415
    try:
        data = json.loads(raw)
        passed = bool(data.get("passed", False))
        feedback = str(data.get("feedback", raw))
    except Exception:
        passed = "true" in raw.lower() and "false" not in raw.lower()
        feedback = raw

    return QAResult(
        passed=passed,
        feedback=feedback,
        screenshot_path=Path(screenshot_path),
        iteration=0,
    )


# ── Single-shot helper ────────────────────────────────────────────────────────

def qa_result(
    screenshot_fn,
    criteria: str,
    *,
    tag: str = "qa",
    iteration: int = 1,
) -> QAResult:
    """
    Take one screenshot via *screenshot_fn(path)*, evaluate against *criteria*.
    *screenshot_fn* must accept a single Path argument and return the saved path.
    """
    ts = int(time.time())
    out_path = SCREENSHOT_DIR / f"{tag}_{ts}_iter{iteration}.png"
    screenshot_fn(out_path)
    result = evaluate(out_path, criteria)
    result.iteration = iteration
    return result
