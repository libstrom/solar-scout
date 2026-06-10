"""Solar Scout -- självträning från manuella valideringar.

Varje gång du bekräftar/avvisar ett lead i Verification Lab skapas en
träningsetikett. Det här skriptet läser etiketterna och gör pipelinen
bättre, helt automatiskt, i tre steg som låses upp i takt med datamängden:

    1. Few-shot-kalibrering (från 1 bekräftad + 1 avvisad):
       plockar de mest LÄRORIKA exemplen -- bekräftade tak som AI:n
       undervärderade och avvisade tak som AI:n övervärderade -- och
       skriver kalibreringsrader som prescreen.py automatiskt lägger
       in i prompten vid nästa körning.
    2. Tröskelkalibrering (från 5 bekräftade + 5 avvisade):
       räknar fram en konservativ auto-avvisningsgräns (lägsta bekräftade
       score minus marginal). Väntande leads under gränsen får status
       'auto_rejected' och försvinner ur din valideringskö.
    3. Embedding-klassificerare (från ~200 etiketter, EJ implementerad än):
       lokal modell som graderar gratis. Byggs när datan finns.

Resultatet sparas i data/calibration.json. Kör efter ett valideringspass
eller låt nattjobbet köra det -- systemet blir bättre medan du sover.

Run:
    python train.py            # kalibrera + auto-avvisa
    python train.py --dry-run  # visa vad som skulle hända, ändra inget
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Optional

import db as shared_db

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

MIN_LABELS_THRESHOLD = 5   # per klass, innan auto-avvisning aktiveras
THRESHOLD_MARGIN = 10      # säkerhetsavstånd under lägsta bekräftade score
FEW_SHOT_PER_CLASS = 3


def calibration_path():
    return shared_db.DATA_DIR / "calibration.json"


def fetch_labels() -> tuple[list, list]:
    """(bekräftade, avvisade) leads som har en AI-score, dvs etiketterade."""
    with shared_db.db() as c:
        rows = c.execute(
            """
            SELECT status, ai_score, ai_reason FROM leads
            WHERE status IN ('confirmed', 'rejected') AND ai_score IS NOT NULL
            """
        ).fetchall()
    confirmed = [r for r in rows if r["status"] == "confirmed"]
    rejected = [r for r in rows if r["status"] == "rejected"]
    return confirmed, rejected


def pick_threshold(confirmed_scores: list, rejected_scores: list) -> Optional[int]:
    """Konservativ auto-avvisningsgräns, eller None om dataunderlaget är tunt.

    Gränsen läggs THRESHOLD_MARGIN under den LÄGSTA score en människa
    någonsin bekräftat -- under den har inget tak någonsin dugt.
    """
    if len(confirmed_scores) < MIN_LABELS_THRESHOLD:
        return None
    if len(rejected_scores) < MIN_LABELS_THRESHOLD:
        return None
    cutoff = min(confirmed_scores) - THRESHOLD_MARGIN
    return cutoff if cutoff > 0 else None


def mine_few_shot(confirmed: list, rejected: list, k: int = FEW_SHOT_PER_CLASS) -> list:
    """Plocka de exempel där AI och människa var mest OENIGA -- där finns lärdomen."""
    lines = []
    # Bekräftade som AI:n gav lägst score: lär modellen värdera upp.
    for r in sorted(confirmed, key=lambda r: r["ai_score"])[:k]:
        if r["ai_reason"]:
            lines.append(
                f'- BEKRÄFTAT av säljare trots AI-score {r["ai_score"]}: '
                f'"{r["ai_reason"]}" -- liknande tak ska få HÖGRE score.'
            )
    # Avvisade som AI:n gav högst score: lär modellen värdera ner.
    for r in sorted(rejected, key=lambda r: -r["ai_score"])[:k]:
        if r["ai_reason"]:
            lines.append(
                f'- AVVISAT av säljare trots AI-score {r["ai_score"]}: '
                f'"{r["ai_reason"]}" -- liknande tak ska få LÄGRE score.'
            )
    return lines


def auto_reject_below(cutoff: int, dry_run: bool = False) -> int:
    """Flytta väntande leads under gränsen till 'auto_rejected'. Returnerar antal."""
    with shared_db.db() as c:
        if dry_run:
            return c.execute(
                "SELECT COUNT(*) FROM leads WHERE status='pending' AND ai_score < ?",
                (cutoff,),
            ).fetchone()[0]
        cur = c.execute(
            """
            UPDATE leads SET status = 'auto_rejected', verified_at = ?
            WHERE status = 'pending' AND ai_score IS NOT NULL AND ai_score < ?
            """,
            (shared_db.utcnow(), cutoff),
        )
        return cur.rowcount


def main() -> int:
    p = argparse.ArgumentParser(description="Självträning från Verification Lab-valideringar")
    p.add_argument("--dry-run", action="store_true", help="Visa utan att ändra något")
    args = p.parse_args()

    shared_db.ensure_schema()
    confirmed, rejected = fetch_labels()
    n_c, n_r = len(confirmed), len(rejected)
    print(f"Etiketter: {n_c} bekräftade, {n_r} avvisade")

    if n_c == 0 and n_r == 0:
        print("Inga valideringar än -- bekräfta/avvisa leads i Verification Lab först.")
        return 0

    few_shot = mine_few_shot(confirmed, rejected)
    cutoff = pick_threshold(
        [r["ai_score"] for r in confirmed], [r["ai_score"] for r in rejected]
    )

    n_auto = 0
    if cutoff is not None:
        n_auto = auto_reject_below(cutoff, dry_run=args.dry_run)
        verb = "skulle auto-avvisas" if args.dry_run else "auto-avvisade"
        print(f"Tröskel: score < {cutoff} -> {n_auto} väntande leads {verb}")
    else:
        print(f"Tröskel: inaktiv (kräver {MIN_LABELS_THRESHOLD}+ per klass)")

    calib = {
        "generated_at": shared_db.utcnow(),
        "n_confirmed": n_c,
        "n_rejected": n_r,
        "auto_reject_below": cutoff,
        "few_shot": few_shot,
    }
    if not args.dry_run:
        calibration_path().parent.mkdir(parents=True, exist_ok=True)
        calibration_path().write_text(
            json.dumps(calib, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"Kalibrering sparad: {calibration_path()}")
    if few_shot:
        print(f"Few-shot: {len(few_shot)} kalibreringsrader till prescreen-prompten:")
        for line in few_shot:
            print(f"  {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
