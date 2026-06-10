"""Enspecta Solar Lead Machine -- AI prescreen.

Grades every harvested rooftop crop with a vision model and writes the
verdict to the leads table (ai_score 0-100, ai_has_panels, ai_reason).
The Verification Lab then shows the best roofs first and flags the ones
that already have panels, so the human pass goes much faster.

Backends (auto-picked):
    api  -- Anthropic API. Used when ANTHROPIC_API_KEY is set (.env or env).
            Model from VISION_MODEL (default claude-sonnet-4-6). Fast.
    cli  -- Claude Code CLI (`claude -p`). Used when no API key is set;
            runs on your existing Claude subscription. Slower but free.

Run:
    python prescreen.py                  # grade all ungraded pending leads
    python prescreen.py --limit 5
    python prescreen.py --backend cli    # force a backend
    python prescreen.py --redo           # re-grade everything

Resume-safe: already graded leads (ai_score NOT NULL) are skipped.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

import db as shared_db

load_dotenv()

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

VISION_MODEL = os.getenv("VISION_MODEL", "claude-sonnet-4-6").strip() or "claude-sonnet-4-6"
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()

PROMPT = """\
Du är takgranskare åt ett svenskt solcellsföretag. Bilden är en satellitbild \
(zoom 20) med en byggnad i centrum. Bedöm byggnadens tak som SÄLJLEAD för en \
NY solcellsinstallation.

Bedöm:
- has_panels: finns redan solpaneler på taket? (mörka rektangulära rader/raster)
- score 0-100: hur attraktivt taket är för nyförsäljning. 0 om paneler redan \
finns. Väg in: takyta, enkel/komplex takform, skuggning från träd, \
sydvänd/öppen yta, skick. Villor/gårdar är målgruppen; höga poäng för stora \
rena sadeltak utan skuggning.
- reason: EN kort mening på svenska (max 15 ord).

Svara med ENDAST detta JSON-objekt, ingen annan text:
{"has_panels": true/false, "score": 0-100, "reason": "..."}"""


# ---- Verdict parsing ---------------------------------------------------------

def parse_verdict(text: str) -> Optional[dict]:
    """Extract {"has_panels":..,"score":..,"reason":..} from model output."""
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict) or "score" not in obj:
        return None
    try:
        score = max(0, min(100, int(obj["score"])))
    except (TypeError, ValueError):
        return None
    return {
        "has_panels": bool(obj.get("has_panels")),
        "score": score,
        "reason": str(obj.get("reason", ""))[:200],
    }


# ---- Backends ----------------------------------------------------------------

def grade_api(image_path: Path) -> Optional[dict]:
    import anthropic

    client = anthropic.Anthropic()
    img_b64 = base64.standard_b64encode(image_path.read_bytes()).decode()
    msg = client.messages.create(
        model=VISION_MODEL,
        max_tokens=300,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image",
                 "source": {"type": "base64", "media_type": "image/png", "data": img_b64}},
                {"type": "text", "text": PROMPT},
            ],
        }],
    )
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    return parse_verdict(text)


def grade_cli(image_path: Path) -> Optional[dict]:
    claude = shutil.which("claude")
    if not claude:
        raise RuntimeError("claude CLI hittas inte i PATH")
    prompt = f"Läs bildfilen {image_path} med Read-verktyget.\n\n{PROMPT}"
    system = (
        'Ditt slutsvar ska vara ENBART ett JSON-objekt på formen '
        '{"has_panels": bool, "score": int, "reason": str} — ingen inledning, '
        "ingen beskrivning, ingen markdown. Första tecknet i svaret ska vara {."
    )
    r = subprocess.run(
        [claude, "-p", "--allowedTools", "Read", "--output-format", "text",
         "--append-system-prompt", system, prompt],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=180, stdin=subprocess.DEVNULL,
    )
    if r.returncode != 0:
        raise RuntimeError(f"claude CLI exit {r.returncode}: {(r.stderr or '')[:300]}")
    verdict = parse_verdict(r.stdout or "")
    if verdict is None:
        raise RuntimeError(f"otolkbart CLI-svar: {(r.stdout or '')[:200]!r}")
    return verdict


# ---- Main loop -----------------------------------------------------------------

def pick_backend(forced: Optional[str]) -> str:
    if forced:
        return forced
    return "api" if ANTHROPIC_API_KEY else "cli"


def fetch_targets(redo: bool, limit: Optional[int]) -> list:
    where = "image_path IS NOT NULL AND status = 'pending'"
    if not redo:
        where += " AND ai_score IS NULL"
    sql = f"SELECT id, place_id, address, image_path FROM leads WHERE {where} ORDER BY id"
    if limit:
        sql += f" LIMIT {int(limit)}"
    with shared_db.db() as c:
        return c.execute(sql).fetchall()


def save_verdict(lead_id: int, v: dict) -> None:
    with shared_db.db() as c:
        c.execute(
            "UPDATE leads SET ai_score = ?, ai_has_panels = ?, ai_reason = ? WHERE id = ?",
            (v["score"], 1 if v["has_panels"] else 0, v["reason"], lead_id),
        )


def main() -> int:
    p = argparse.ArgumentParser(description="AI-prescreen av skördade tak")
    p.add_argument("--backend", choices=["api", "cli"], default=None)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--redo", action="store_true", help="Betygsätt om redan bedömda")
    args = p.parse_args()

    shared_db.ensure_schema()
    backend = pick_backend(args.backend)
    if backend == "api" and not ANTHROPIC_API_KEY:
        print("ERROR: --backend api kräver ANTHROPIC_API_KEY i .env", file=sys.stderr)
        return 2

    targets = fetch_targets(args.redo, args.limit)
    if not targets:
        print("Inget att bedöma -- alla pending leads är redan AI-graderade.")
        return 0

    grade = grade_api if backend == "api" else grade_cli
    print(f"Prescreen: {len(targets)} tak, backend={backend}, modell={VISION_MODEL if backend == 'api' else 'claude CLI'}")

    done, failed = 0, 0
    t_start = time.monotonic()
    for i, row in enumerate(targets, 1):
        img = Path(row["image_path"])
        label = row["address"] or row["place_id"]
        if not img.exists():
            print(f"  [{i}/{len(targets)}] SKIP (bild saknas): {label}")
            failed += 1
            continue
        try:
            verdict = grade(img)
        except Exception as e:
            print(f"  [{i}/{len(targets)}] FEL: {label}: {e}")
            failed += 1
            continue
        if not verdict:
            print(f"  [{i}/{len(targets)}] FEL (otolkbart svar): {label}")
            failed += 1
            continue
        save_verdict(row["id"], verdict)
        done += 1
        elapsed = time.monotonic() - t_start
        eta = (elapsed / i) * (len(targets) - i)
        flag = "SOLCELLER FINNS" if verdict["has_panels"] else f"score {verdict['score']}"
        print(f"  [{i}/{len(targets)}] {flag:>15} | {label} | {verdict['reason']}"
              f"  (ETA {int(eta // 60)}:{int(eta % 60):02d})")

    print(f"\nKlart. Bedömda: {done}  Misslyckade: {failed}")
    print("Kör `streamlit run app.py` -- Verification Lab visar nu bästa taken först.")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
