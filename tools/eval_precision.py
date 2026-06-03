"""Precision/recall evaluation for the solar-scout AI classifier.

Fetches labeled leads from Supabase, re-runs _prefilter_building + _analyze_building,
and reports precision/recall/F1 plus failure analysis.

Usage:
    python tools/eval_precision.py [--limit N] [--dry-run]
"""
from __future__ import annotations

import argparse
import base64
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any

import httpx


def _check_env() -> tuple[str, str, str]:
    """Return (supabase_url, supabase_key, anthropic_key) or print error and exit."""
    missing = []
    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_ANON_KEY", "")
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("SOLAR_SCOUT_ANTHROPIC_KEY", "")
    if not url:
        missing.append("SUPABASE_URL")
    if not key:
        missing.append("SUPABASE_ANON_KEY")
    if not anthropic_key:
        missing.append("ANTHROPIC_API_KEY (or SOLAR_SCOUT_ANTHROPIC_KEY)")
    if missing:
        print("ERROR: Missing required environment variables:")
        for m in missing:
            print(f"  {m}")
        print("\nSet them before running:")
        print("  export SUPABASE_URL=https://<project>.supabase.co")
        print("  export SUPABASE_ANON_KEY=<anon-key>")
        print("  export ANTHROPIC_API_KEY=<key>")
        sys.exit(1)
    return url, key, anthropic_key


@dataclass
class EvalRow:
    id: str
    user_id: str
    lat: float
    lng: float
    address: str
    image_url: str
    ai_reasoning: str
    # Ground truth
    ground_truth: bool  # True = confirmed solar, False = false positive
    # AI result (filled after running)
    prefilter_pass: bool | None = None
    ai_solar: bool | None = None
    ai_unsure: bool | None = None
    ai_reasoning_new: str = ""
    img_bytes: bytes | None = field(default=None, repr=False)


def _fetch_labeled_leads(sb_url: str, sb_key: str, limit: int) -> list[EvalRow]:
    """Fetch leads with ground truth labels from Supabase."""
    from supabase import create_client
    sb = create_client(sb_url, sb_key)

    rows = (
        sb.table("scout_leads")
        .select("id,user_id,lat,lng,address,image_url,confirmed_image_url,ai_reasoning,user_confirmed,false_positive")
        .eq("source", "ai")
        .or_("user_confirmed.not.is.null,false_positive.not.is.null")
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
        .data or []
    )

    result = []
    for r in rows:
        user_confirmed = r.get("user_confirmed")
        false_positive = r.get("false_positive")

        # Determine ground truth: confirmed=True means solar; false_positive=True means no solar
        if user_confirmed is True and not false_positive:
            ground_truth = True
        elif false_positive is True:
            ground_truth = False
        elif user_confirmed is False:
            # Rejected in review queue — no solar
            ground_truth = False
        else:
            continue  # ambiguous, skip

        image_url = r.get("confirmed_image_url") or r.get("image_url") or ""
        if not image_url:
            continue  # can't evaluate without image

        result.append(EvalRow(
            id=r["id"],
            user_id=r.get("user_id", ""),
            lat=float(r.get("lat", 0)),
            lng=float(r.get("lng", 0)),
            address=r.get("address", ""),
            image_url=image_url,
            ai_reasoning=r.get("ai_reasoning") or "",
            ground_truth=ground_truth,
        ))

    return result


def _download_images(rows: list[EvalRow]) -> None:
    """Download image bytes for each row in-place, skipping failures."""
    def _fetch(row: EvalRow) -> None:
        try:
            with httpx.Client(timeout=15) as client:
                resp = client.get(row.image_url)
                if resp.status_code == 200 and resp.content:
                    row.img_bytes = resp.content
                else:
                    print(f"  WARN: image {row.id} HTTP {resp.status_code} — skipping")
        except Exception as e:
            print(f"  WARN: image {row.id} download failed: {e} — skipping")

    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {pool.submit(_fetch, row): row for row in rows}
        for fut in as_completed(futures):
            fut.result()


def _run_classifier(rows: list[EvalRow], anthropic_key: str) -> None:
    """Run _prefilter_building + _analyze_building on each row that has image bytes."""
    # Add scanner.py directory to path
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import anthropic
    from scanner import _prefilter_building, _analyze_building, _load_few_shot_images

    api_client = anthropic.Anthropic(api_key=anthropic_key)
    few_shot = _load_few_shot_images()
    print(f"  Loaded {len(few_shot)} few-shot examples")

    total = sum(1 for r in rows if r.img_bytes is not None)
    done = 0
    for row in rows:
        if row.img_bytes is None:
            continue

        try:
            passes = _prefilter_building(row.img_bytes, api_client)
            row.prefilter_pass = passes
            if not passes:
                row.ai_solar = False
                row.ai_unsure = False
            else:
                is_house, has_solar, is_unsure, reasoning = _analyze_building(
                    api_client, row.img_bytes, few_shot=few_shot, already_enhanced=False
                )
                row.ai_solar = has_solar
                row.ai_unsure = is_unsure
                row.ai_reasoning_new = reasoning
        except Exception as e:
            print(f"  WARN: classifier error for {row.id}: {e}")
            continue

        done += 1
        if done % 5 == 0 or done == total:
            print(f"  [{done}/{total}] classified...")


def _compute_metrics(rows: list[EvalRow]) -> dict[str, Any]:
    """Compute TP/FP/TN/FN and derived metrics.

    AI prediction: solar=True when ai_solar=True OR ai_unsure=True
    (unsure leads are treated as positives since they become needs_review leads).
    """
    tp = fp = tn = fn = 0
    false_positives: list[EvalRow] = []
    false_negatives: list[EvalRow] = []

    for row in rows:
        if row.ai_solar is None:
            continue  # not evaluated
        ai_positive = row.ai_solar or bool(row.ai_unsure)
        if row.ground_truth and ai_positive:
            tp += 1
        elif not row.ground_truth and ai_positive:
            fp += 1
            false_positives.append(row)
        elif row.ground_truth and not ai_positive:
            fn += 1
            false_negatives.append(row)
        else:
            tn += 1

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    return {
        "tp": tp, "fp": fp, "tn": tn, "fn": fn,
        "precision": precision, "recall": recall, "f1": f1,
        "false_positives": false_positives,
        "false_negatives": false_negatives,
    }


def _print_report(metrics: dict[str, Any], rows: list[EvalRow]) -> None:
    total = metrics["tp"] + metrics["fp"] + metrics["tn"] + metrics["fn"]
    skipped = sum(1 for r in rows if r.ai_solar is None)

    print("\n" + "=" * 60)
    print("EVAL REPORT — Solar Scout AI Classifier")
    print("=" * 60)
    print(f"\nEvaluated: {total}  (skipped/no-image: {skipped})")

    print("\nConfusion Matrix:")
    print(f"  {'':20s} AI=Solar  AI=No-solar")
    print(f"  {'GT=Solar':20s} {metrics['tp']:8d}  {metrics['fn']:11d}")
    print(f"  {'GT=No-solar':20s} {metrics['fp']:8d}  {metrics['tn']:11d}")

    print(f"\nPrecision : {metrics['precision']:.3f}  ({metrics['tp']} TP / {metrics['tp'] + metrics['fp']} predicted positive)")
    print(f"Recall    : {metrics['recall']:.3f}  ({metrics['tp']} TP / {metrics['tp'] + metrics['fn']} actual positive)")
    print(f"F1 score  : {metrics['f1']:.3f}")

    fps = metrics["false_positives"][:5]
    if fps:
        print(f"\nTop {len(fps)} False Positives (AI said solar, ground truth = no):")
        for i, row in enumerate(fps, 1):
            reasoning = row.ai_reasoning_new or row.ai_reasoning or "(no reasoning)"
            print(f"  {i}. {row.address or row.id}")
            print(f"     AI: {reasoning[:120]}")
            print(f"     URL: {row.image_url}")

    fns = metrics["false_negatives"][:5]
    if fns:
        print(f"\nTop {len(fns)} False Negatives (AI said no solar, ground truth = yes):")
        for i, row in enumerate(fns, 1):
            reasoning = row.ai_reasoning_new or row.ai_reasoning or "(no reasoning)"
            print(f"  {i}. {row.address or row.id}")
            print(f"     AI: {reasoning[:120]}")
            print(f"     URL: {row.image_url}")

    print()


CREATE_TABLE_SQL = """
-- Run this in Supabase SQL editor to create the few_shot_examples table:
CREATE TABLE IF NOT EXISTS few_shot_examples (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      text,
    tile_key     text NOT NULL UNIQUE,
    image_b64    text NOT NULL,
    verdict      text NOT NULL,
    created_at   timestamptz DEFAULT now()
);
""".strip()


def _upsert_few_shot(
    rows: list[EvalRow],
    sb_url: str,
    sb_key: str,
    dry_run: bool,
    metrics: dict[str, Any],
) -> None:
    """Insert wrong-prediction rows into few_shot_examples for future calibration."""
    wrong_rows = metrics["false_positives"] + metrics["false_negatives"]
    if not wrong_rows:
        print("No wrong predictions — nothing to upsert.")
        return

    examples = []
    for row in wrong_rows:
        if row.img_bytes is None:
            continue
        verdict = (
            "HOUSE=YES\nSOLAR=YES"
            if row.ground_truth
            else "HOUSE=YES\nSOLAR=NO"
        )
        examples.append({
            "user_id": row.user_id or None,
            "tile_key": f"eval/{row.id}",
            "image_b64": base64.standard_b64encode(row.img_bytes).decode(),
            "verdict": verdict,
        })

    print(f"\nFew-shot candidates (wrong predictions): {len(examples)}")

    if dry_run:
        print("\n[dry-run] Would upsert these examples into few_shot_examples.")
        print("\nSQL to create the table if it doesn't exist:\n")
        print(CREATE_TABLE_SQL)
        print(f"\nWould insert {len(examples)} rows (image_b64 truncated for display):")
        for ex in examples:
            print(f"  tile_key={ex['tile_key']}  verdict={ex['verdict'].replace(chr(10), '|')}")
        return

    from supabase import create_client
    sb = create_client(sb_url, sb_key)

    # Check if table exists by attempting a small select
    try:
        sb.table("few_shot_examples").select("id").limit(1).execute()
        table_exists = True
    except Exception:
        table_exists = False

    if not table_exists:
        print("\nTable 'few_shot_examples' does not exist yet.")
        print("Create it with:\n")
        print(CREATE_TABLE_SQL)
        print(f"\nExamples that WOULD be inserted ({len(examples)}):")
        for ex in examples:
            print(f"  tile_key={ex['tile_key']}  verdict={ex['verdict'].replace(chr(10), '|')}")
        return

    try:
        sb.table("few_shot_examples").upsert(examples, on_conflict="tile_key").execute()
        print(f"Upserted {len(examples)} rows into few_shot_examples.")
    except Exception as e:
        print(f"Upsert failed: {e}")
        print("You may need to add a UNIQUE constraint on tile_key:")
        print("  ALTER TABLE few_shot_examples ADD CONSTRAINT few_shot_tile_key_unique UNIQUE (tile_key);")


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate AI classifier precision/recall on labeled leads.")
    parser.add_argument("--limit", type=int, default=50, help="Max labeled leads to evaluate (default: 50)")
    parser.add_argument("--dry-run", action="store_true", help="Skip DB upsert, just print what would happen")
    args = parser.parse_args()

    sb_url, sb_key, anthropic_key = _check_env()

    print(f"Fetching up to {args.limit} labeled AI leads from Supabase...")
    rows = _fetch_labeled_leads(sb_url, sb_key, args.limit)
    if not rows:
        print("No labeled leads found (source='ai' with user_confirmed or false_positive set).")
        sys.exit(0)

    positives = sum(1 for r in rows if r.ground_truth)
    negatives = len(rows) - positives
    print(f"Found {len(rows)} labeled leads: {positives} confirmed solar, {negatives} false positives/rejected")

    print("\nDownloading images...")
    _download_images(rows)

    downloaded = sum(1 for r in rows if r.img_bytes is not None)
    print(f"Downloaded {downloaded}/{len(rows)} images")

    print(f"\nRunning classifier on {downloaded} images...")
    _run_classifier(rows, anthropic_key)

    metrics = _compute_metrics(rows)
    _print_report(metrics, rows)

    _upsert_few_shot(rows, sb_url, sb_key, args.dry_run, metrics)


if __name__ == "__main__":
    main()
