"""
scanner_eval.py — Autoresearch-style prompt evaluation for solar panel detection.

Inspired by karpathy/autoresearch: run prompt variants in a fixed time budget,
keep improvements, discard failures, report a leaderboard ranked by F1.

Usage:
    python scanner_eval.py --cases eval_cases.json               # baseline only
    python scanner_eval.py --cases eval_cases.json --autoresearch  # all variants
    python scanner_eval.py --cases eval_cases.json --variant strict
    python scanner_eval.py --cases eval_cases.json --autoresearch --budget 600
"""

import os
import io
import json
import time
import base64
import math
import logging
import argparse
from dataclasses import dataclass, field

import httpx
import anthropic

try:
    import numpy as np
    from PIL import Image as _PILImage
    _ENHANCE_AVAILABLE = True
except ImportError:
    _ENHANCE_AVAILABLE = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [eval] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
_log = logging.getLogger("scanner_eval")


# ── Image fetching ─────────────────────────────────────────────────────────────

def _fetch_lm_wms(lat: float, lng: float, size_m: float = 18) -> bytes | None:
    """Lantmäteriet minkarta WMS — free, no key, high-res Swedish orthophoto."""
    d_lat = (size_m / 2) / 111_000
    d_lng = d_lat / math.cos(math.radians(lat))
    bbox = f"{lng-d_lng},{lat-d_lat},{lng+d_lng},{lat+d_lat}"
    url = (
        "https://minkarta.lantmateriet.se/map/ortofoto"
        "?SERVICE=WMS&VERSION=1.1.1&REQUEST=GetMap"
        "&LAYERS=Ortofoto_0.25,Ortofoto_0.16"
        "&FORMAT=image/jpeg&WIDTH=640&HEIGHT=640"
        f"&SRS=EPSG:4326&BBOX={bbox}"
    )
    try:
        resp = httpx.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code == 200 and "image" in resp.headers.get("content-type", ""):
            return resp.content
    except Exception:
        pass
    return None


def _enhance_contrast(img_bytes: bytes) -> bytes:
    """Tile-based CLAHE on the Y channel — mirrors scanner.py's implementation."""
    if not _ENHANCE_AVAILABLE:
        return img_bytes
    try:
        img = _PILImage.open(io.BytesIO(img_bytes)).convert("YCbCr")
        y, cb, cr = img.split()
        y_arr = np.array(y, dtype=np.uint8)
        h, w = y_arr.shape
        tile_h, tile_w = h // 4, w // 4
        out = np.empty_like(y_arr)
        for ti in range(4):
            for tj in range(4):
                r0, r1 = ti * tile_h, (ti + 1) * tile_h if ti < 3 else h
                c0, c1 = tj * tile_w, (tj + 1) * tile_w if tj < 3 else w
                tile = y_arr[r0:r1, c0:c1]
                hist, _ = np.histogram(tile.flatten(), 256, (0, 256))
                cdf = hist.cumsum()
                cdf_min = int(cdf[cdf > 0][0])
                n = tile.size
                lut = np.round(
                    (cdf - cdf_min) / max(n - cdf_min, 1) * 255
                ).clip(0, 255).astype(np.uint8)
                out[r0:r1, c0:c1] = lut[tile]
        blended = (0.6 * out + 0.4 * y_arr).clip(0, 255).astype(np.uint8)
        y_new = _PILImage.fromarray(blended, mode="L")
        enhanced = _PILImage.merge("YCbCr", (y_new, cb, cr)).convert("RGB")
        buf = io.BytesIO()
        enhanced.save(buf, format="JPEG", quality=85)
        return buf.getvalue()
    except Exception:
        return img_bytes


# ── Prompt variants ────────────────────────────────────────────────────────────
#
# Each variant modifies only the INSTRUCTION text sent as the system prompt.
# The few-shot image examples remain identical across all variants so that
# the only independent variable is the instruction wording.

_BASELINE_INSTRUCTION = (
    "Swedish aerial orthophoto, ~50m wide, top-down view. "
    "SE3/SE4 grid zone (Småland, Skåne, Jönköping). Dominant roof materials: "
    "red/brown clay tiles (Skandiategel, tegelpannor), grey fibre cement, "
    "black bitumen/EPDM, corrugated cement, standing-seam metal (plåttak).\n\n"

    "STEP 1 — Is the CENTRAL structure a single-family home?\n"
    "(villa, parhus, radhus, fritidshus — NOT garage, carport, barn, shed, "
    "warehouse, industrial, church, school, kiosk, construction site)\n"
    "→ If NO: HOUSE=NO, SOLAR=NO. Stop here.\n\n"

    "STEP 2 — Describe the roof in one sentence:\n"
    "roof shape, dominant texture/colour, and whether you can see any area "
    "that looks distinctly different from the surrounding roof material.\n\n"

    "STEP 3 — Look for PV evidence. EITHER signal counts:\n"
    "  (a) SMOOTHNESS CONTRAST — a clearly smoother, more uniform area against "
    "      the bumpy/ribbed texture of adjacent tiles or shingles. This is the "
    "      primary signal on tegelpannor/Skandiategel roofs.\n"
    "  (b) RECTANGULAR BOUNDARY — a discrete rectangular sub-area with a visible "
    "      edge against the rest of the roof. Panels can also cover a full roof "
    "      slope; in that case look for the bottom/side edge against the roof "
    "      gutter or ridge line, plus visible module seams.\n"
    "Module-grid lines, mirror-bright reflections, and uniform dark-blue/black "
    "panel colour are supporting signals.\n\n"

    "COMMON TRAPS that are NOT solar (default SOLAR=NO):\n"
    "• Skandiategel / tegelpannor (clay tiles) — bumpy ridge texture; ubiquitous in SE3\n"
    "• Corrugated grey fibre cement — ribbed surface, no flat patch contrast; "
    "common on 1960–1980s Swedish housing\n"
    "• Standing-seam metal (plåttak) — long parallel ribs ridge-to-eave with "
    "uniform colour — no smoother sub-area\n"
    "• Eternite / smooth grey fibre cement — uniform grey, no module grid\n"
    "• Copper or green-patina metal — uniform colour across the whole roof\n"
    "• Whole-roof dark bitumen / asphalt / EPDM with no smoother patch\n"
    "• Shadows, skylights, snow patches, dormer windows — irregular shape\n"
    "• Solar thermal collectors (solfångare) — narrow tube strips, not flat panels\n\n"

    "CALIBRATION: Only ~5–10% of Swedish villas have solar. "
    "When the signal is subtle, uncertain, or ambiguous → SOLAR=UNSURE, not YES. "
    "Reserve SOLAR=NO for clear cases where no smoother patch and no rectangular "
    "sub-area is visible. A missed panel costs less than a false alarm, but "
    "SOLAR=NO on a panel that's clearly visible is also a loss.\n\n"

    "End with exactly two lines, nothing after:\n"
    "HOUSE=YES or HOUSE=NO\n"
    "SOLAR=YES or SOLAR=UNSURE or SOLAR=NO\n"
    "(SOLAR=NO whenever HOUSE=NO)"
)

_STRICT_INSTRUCTION = (
    "Swedish aerial orthophoto, ~50m wide, top-down view. "
    "SE3/SE4 grid zone (Småland, Skåne, Jönköping). Dominant roof materials: "
    "red/brown clay tiles (Skandiategel, tegelpannor), grey fibre cement, "
    "black bitumen/EPDM, corrugated cement, standing-seam metal (plåttak).\n\n"

    "STEP 1 — Is the CENTRAL structure a single-family home?\n"
    "(villa, parhus, radhus, fritidshus — NOT garage, carport, barn, shed, "
    "warehouse, industrial, church, school, kiosk, construction site)\n"
    "→ If NO: HOUSE=NO, SOLAR=NO. Stop here.\n\n"

    "STEP 2 — Describe the roof in one sentence:\n"
    "roof shape, dominant texture/colour, and whether you can see any area "
    "that looks distinctly different from the surrounding roof material.\n\n"

    "STEP 3 — Look for PV evidence. BOTH signals should be visible to say YES:\n"
    "  (a) SMOOTHNESS CONTRAST — a CLEARLY smoother, more uniform area vs the "
    "      surrounding bumpy/ribbed tiles. The contrast must be unambiguous.\n"
    "  (b) RECTANGULAR BOUNDARY — a discrete rectangular sub-area with a definite "
    "      edge. Module-grid lines or visible seams strongly support YES.\n"
    "If only one signal is present and it's subtle → SOLAR=UNSURE.\n"
    "Reserve SOLAR=YES only for cases with strong, unambiguous PV evidence.\n\n"

    "FALSE POSITIVE KILLERS — these are definitively NOT solar panels:\n"
    "• Eternite / smooth grey fibre cement sheets — uniform grey, no module grid, "
    "  no seams. This is the #1 false positive in SE3. When in doubt: SOLAR=NO.\n"
    "• Skandiategel / tegelpannor (clay tiles) — bumpy ridge texture throughout\n"
    "• Corrugated grey fibre cement — ribbed, no flat patch contrast\n"
    "• Standing-seam metal (plåttak) — parallel ribs, no smoother sub-area\n"
    "• Copper or green-patina metal — uniform patina colour\n"
    "• Dark bitumen / asphalt / EPDM — whole roof uniform, no patch\n"
    "• Shadows, skylights, snow, dormers — irregular shape\n"
    "• Solar thermal collectors (solfångare) — narrow tube strips\n"
    "• Any uniform-coloured surface with no visible module boundary\n\n"

    "CALIBRATION: Only ~5–10% of Swedish villas have solar. "
    "Err heavily on the side of SOLAR=NO. "
    "Use SOLAR=UNSURE for genuine ambiguity only — not as a default hedge. "
    "A false positive costs a wasted sales visit; prioritise precision.\n\n"

    "End with exactly two lines, nothing after:\n"
    "HOUSE=YES or HOUSE=NO\n"
    "SOLAR=YES or SOLAR=UNSURE or SOLAR=NO\n"
    "(SOLAR=NO whenever HOUSE=NO)"
)

_RECALL_INSTRUCTION = (
    "Swedish aerial orthophoto, ~50m wide, top-down view. "
    "SE3/SE4 grid zone (Småland, Skåne, Jönköping). Dominant roof materials: "
    "red/brown clay tiles (Skandiategel, tegelpannor), grey fibre cement, "
    "black bitumen/EPDM, corrugated cement, standing-seam metal (plåttak).\n\n"

    "STEP 1 — Is the CENTRAL structure a single-family home?\n"
    "(villa, parhus, radhus, fritidshus — NOT garage, carport, barn, shed, "
    "warehouse, industrial, church, school, kiosk, construction site)\n"
    "→ If NO: HOUSE=NO, SOLAR=NO. Stop here.\n\n"

    "STEP 2 — Describe the roof in one sentence:\n"
    "roof shape, dominant texture/colour, and whether you can see any area "
    "that looks distinctly different from the surrounding roof material.\n\n"

    "STEP 3 — Look for ANY PV evidence. EITHER signal counts:\n"
    "  (a) SMOOTHNESS CONTRAST — even a subtly smoother or more uniform area "
    "      against the surrounding texture. This is the primary SE3 signal.\n"
    "  (b) RECTANGULAR BOUNDARY — any hint of a rectangular sub-area or edge.\n"
    "  (c) Unusual dark-blue/black or reflective patch on the roof.\n"
    "  (d) Module-grid lines or seam patterns.\n"
    "When ANY of these signals appears, even weakly → SOLAR=UNSURE (not NO).\n"
    "Reserve SOLAR=NO only for roofs with zero visual ambiguity: purely uniform "
    "clay tile, metal, or fibre cement with no anomalous patches at all.\n\n"

    "COMMON TRAPS that are NOT solar (still say NO for these clear-cut cases):\n"
    "• Skandiategel — bumpy ridge texture throughout with zero smooth area\n"
    "• Clearly ribbed corrugated fibre cement with fully uniform ribbing\n"
    "• Standing-seam metal with parallel ribs only, no different-texture patch\n"
    "• Copper / green-patina metal — uniform colour\n"
    "• Shadows or snow — irregular shape, not rectangular\n"
    "• Solar thermal collectors (solfångare) — narrow tube strips\n\n"

    "CALIBRATION: Solar penetration is growing fast in SE3. "
    "When uncertain, flag SOLAR=UNSURE so a human can verify. "
    "A missed solar installation (false negative) loses a sales opportunity. "
    "It is better to over-flag and let humans filter than to miss real panels.\n\n"

    "End with exactly two lines, nothing after:\n"
    "HOUSE=YES or HOUSE=NO\n"
    "SOLAR=YES or SOLAR=UNSURE or SOLAR=NO\n"
    "(SOLAR=NO whenever HOUSE=NO)"
)

_NORDIC_INSTRUCTION = (
    "Swedish aerial orthophoto, ~50m wide, top-down view. "
    "SE3/SE4 grid zone (Småland, Skåne, Jönköping). "
    "Nordic climate: overcast light, snow in winter, lower sun angle than Central Europe.\n\n"

    "SWEDISH BUILDING TYPES — know these before scoring:\n"
    "• Villa (detached, 1–2 floors, red/yellow plaster or wood cladding)\n"
    "• Parhus (semi-detached, two mirrored halves)\n"
    "• Radhus (terrace house — row of attached homes)\n"
    "• Kedjehus (chain house — garages link adjacent units)\n"
    "• Fritidshus (summer cabin — smaller, often forested setting)\n"
    "• 60-talslänga / 70-talshus (post-war strip blocks — often NOT single-family)\n\n"

    "SWEDISH ROOF MATERIALS — common in SE3 (Jönköping, Nässjö, Vetlanda):\n"
    "• Tegelpannor / Skandiategel — red-brown clay interlocking tiles; ribbed, bumpy\n"
    "• Betongtegel — grey/black concrete tiles; similar texture to tegelpannor\n"
    "• Eternite / fibercementskiffer — smooth, uniform grey sheets; NO solar\n"
    "• Plåttak / stålplåt — standing-seam metal; parallel long ribs\n"
    "• Papptak / bitumen — flat or low-slope; dark, uniform\n"
    "• Nock / nockpannor — ridge tiles (darker than field tiles, do not confuse with panels)\n\n"

    "STEP 1 — Is the CENTRAL structure a single-family home (see types above)?\n"
    "→ If NO: HOUSE=NO, SOLAR=NO. Stop here.\n\n"

    "STEP 2 — Identify the roof material (one of the types above) and describe it.\n\n"

    "STEP 3 — Look for PV evidence against the identified material:\n"
    "  (a) On tegelpannor: PV shows as a clearly smoother, flatter rectangular patch\n"
    "      contrasting sharply with the surrounding bumpy clay tile texture.\n"
    "  (b) On papptak/dark roofs: PV shows as a slightly different-coloured, more\n"
    "      reflective or differently-textured rectangular sub-area.\n"
    "  (c) On any roof: visible module seams, grid lines, or bright reflections.\n"
    "  (d) On eternite: virtually impossible to install panels without visible anchors — "
    "      be extremely sceptical of any 'smoothness' on grey fibre cement.\n\n"

    "COMMON TRAPS:\n"
    "• Eternite — #1 false positive in SE3; uniform smooth grey with zero module grid\n"
    "• Nockpannor (ridge tiles) — darker strip at ridge only, no rectangular flat area\n"
    "• Takkupa / dormer — small raised box, not a panel\n"
    "• Solfångare (thermal solar) — narrow tube strips, not flat PV panels\n"
    "• Snow patches — irregular shape, bright white\n\n"

    "CALIBRATION: Only ~5–10% of Swedish villas have solar; "
    "rising fastest in Jönköpings län post-2022. "
    "Use SOLAR=UNSURE for genuine ambiguity. "
    "SOLAR=YES requires a clear, unambiguous PV signal.\n\n"

    "End with exactly two lines, nothing after:\n"
    "HOUSE=YES or HOUSE=NO\n"
    "SOLAR=YES or SOLAR=UNSURE or SOLAR=NO\n"
    "(SOLAR=NO whenever HOUSE=NO)"
)

PROMPT_VARIANTS: dict[str, str] = {
    "baseline": _BASELINE_INSTRUCTION,
    "strict":   _STRICT_INSTRUCTION,
    "recall":   _RECALL_INSTRUCTION,
    "nordic":   _NORDIC_INSTRUCTION,
}


# ── Data types ─────────────────────────────────────────────────────────────────

@dataclass
class EvalResult:
    variant: str
    tp: int = 0   # true positive  (label=yes|unsure, model=yes|unsure)
    fp: int = 0   # false positive (label=no,         model=yes|unsure)
    fn: int = 0   # false negative (label=yes|unsure, model=no)
    tn: int = 0   # true negative  (label=no,         model=no)
    latency_ms_avg: float = 0.0
    errors: int = 0

    @property
    def precision(self) -> float:
        denom = self.tp + self.fp
        return self.tp / denom if denom else 0.0

    @property
    def recall(self) -> float:
        denom = self.tp + self.fn
        return self.tp / denom if denom else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0

    @property
    def n(self) -> int:
        return self.tp + self.fp + self.fn + self.tn


# ── Few-shot loading ───────────────────────────────────────────────────────────

_FEW_SHOT_COORDS = [
    (55.5705978, 13.0378985, "solar_yes"),
    (57.64119,   14.70581,   "solar_yes_3"),
    (55.5764531, 13.0743366, "solar_no"),
    (57.6349444, 14.7103611, "solar_no_3"),
]
_FEW_SHOT_VERDICTS = {
    "solar_yes": (
        "The roof shows a rectangular section of smooth, uniform dark panels that clearly "
        "contrast against the surrounding coarser tile texture — typical PV array from above.\n\n"
        "HOUSE=YES\nSOLAR=YES"
    ),
    "solar_yes_3": (
        "Swedish inland villa (Småland). A clearly defined rectangular area on the roof "
        "surface appears noticeably smoother and more uniform than the surrounding pitched "
        "tile material — the smoothness contrast and regular geometry indicate a PV array.\n\n"
        "HOUSE=YES\nSOLAR=YES"
    ),
    "solar_no": (
        "Uniform roof surface with consistent texture throughout — no smooth rectangular "
        "patches or contrast areas visible.\n\n"
        "HOUSE=YES\nSOLAR=NO"
    ),
    "solar_no_3": (
        "Swedish inland villa (Småland/Nässjö). Roof surface is uniformly textured throughout "
        "— no smooth rectangular patch or contrast area distinguishable from the surrounding "
        "tile material. No photovoltaic installation visible.\n\n"
        "HOUSE=YES\nSOLAR=NO"
    ),
}

_few_shot_cache: list[tuple[str, str]] | None = None


def _load_few_shot() -> list[tuple[str, str]]:
    """Download and cache hardcoded few-shot examples from LM WMS."""
    global _few_shot_cache
    if _few_shot_cache is not None:
        return _few_shot_cache
    examples = []
    for lat, lng, label in _FEW_SHOT_COORDS:
        img = _fetch_lm_wms(lat, lng)
        if img is None:
            _log.warning("Few-shot download failed for %s — running without few-shot", label)
            _few_shot_cache = []
            return []
        b64 = base64.standard_b64encode(img).decode()
        examples.append((b64, _FEW_SHOT_VERDICTS[label]))
    _log.info("Few-shot examples loaded: %d", len(examples))
    _few_shot_cache = examples
    return examples


# ── Core analysis — standalone Claude call ─────────────────────────────────────

def _call_claude(
    client: anthropic.Anthropic,
    img_bytes: bytes,
    instruction: str,
    few_shot: list[tuple[str, str]],
) -> tuple[bool, bool, bool]:
    """
    Send image + instruction to Claude Vision.
    Returns (is_positive, is_unsure, api_ok).
    is_positive=True when SOLAR=YES; is_unsure=True when SOLAR=UNSURE.
    api_ok=False on network/API errors.
    """
    img_bytes = _enhance_contrast(img_bytes)
    b64 = base64.standard_b64encode(img_bytes).decode()

    system_blocks: list[dict] = [
        {"type": "text", "text": instruction, "cache_control": {"type": "ephemeral"}}
    ]

    msgs: list[dict] = []
    if few_shot:
        last_idx = len(few_shot) - 1
        for i, (ex_b64, verdict) in enumerate(few_shot):
            img_block: dict = {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg", "data": ex_b64},
            }
            if i == last_idx:
                img_block["cache_control"] = {"type": "ephemeral"}
            msgs.append({
                "role": "user",
                "content": [
                    {"type": "text", "text": "Analyze this Swedish aerial roof image:"},
                    img_block,
                ],
            })
            msgs.append({"role": "assistant", "content": verdict})

    msgs.append({
        "role": "user",
        "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
        ],
    })

    try:
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=220,
            system=system_blocks,
            messages=msgs,
        )
        text = msg.content[0].text.upper()
        is_house = "HOUSE=YES" in text
        is_positive = is_house and "SOLAR=YES" in text and "SOLAR=UNSURE" not in text
        is_unsure = is_house and "SOLAR=UNSURE" in text
        return is_positive, is_unsure, True
    except Exception as exc:
        _log.error("Claude API error: %s", exc)
        return False, False, False


# ── Case loading ───────────────────────────────────────────────────────────────

def load_cases(path: str) -> list[dict]:
    with open(path) as f:
        cases = json.load(f)
    for c in cases:
        if c.get("label") not in ("yes", "no", "unsure"):
            raise ValueError(f"Invalid label {c.get('label')!r} in {c}")
    return cases


def _is_ground_truth_positive(label: str) -> bool:
    """Unsure cases count as positive for recall — human should flag them."""
    return label in ("yes", "unsure")


def _model_flagged(is_positive: bool, is_unsure: bool) -> bool:
    """Model flagged = YES or UNSURE (anything that would surface a lead)."""
    return is_positive or is_unsure


# ── Main eval function ─────────────────────────────────────────────────────────

def evaluate_prompt(
    cases: list[dict],
    variant: str = "baseline",
    client: anthropic.Anthropic | None = None,
    few_shot: list[tuple[str, str]] | None = None,
) -> EvalResult:
    """Run Claude Vision on each case and return precision/recall/F1."""
    if variant not in PROMPT_VARIANTS:
        raise ValueError(f"Unknown variant {variant!r}. Choose from: {list(PROMPT_VARIANTS)}")

    instruction = PROMPT_VARIANTS[variant]
    api_key = os.environ.get("SOLAR_SCOUT_ANTHROPIC_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("Set SOLAR_SCOUT_ANTHROPIC_KEY (or ANTHROPIC_API_KEY) env var")

    if client is None:
        client = anthropic.Anthropic(api_key=api_key)
    if few_shot is None:
        few_shot = _load_few_shot()

    result = EvalResult(variant=variant)
    latencies: list[float] = []

    for i, case in enumerate(cases):
        lat, lng, label = case["lat"], case["lng"], case["label"]
        note = case.get("note", "")
        _log.info("[%d/%d] lat=%.4f lng=%.4f label=%s  %s", i + 1, len(cases), lat, lng, label, note)

        img = _fetch_lm_wms(lat, lng)
        if img is None:
            _log.warning("Image fetch failed for lat=%.4f lng=%.4f — skipping", lat, lng)
            result.errors += 1
            continue

        t0 = time.monotonic()
        is_positive, is_unsure, api_ok = _call_claude(client, img, instruction, few_shot)
        latency_ms = (time.monotonic() - t0) * 1000
        latencies.append(latency_ms)

        if not api_ok:
            result.errors += 1
            continue

        gt_positive = _is_ground_truth_positive(label)
        model_positive = _model_flagged(is_positive, is_unsure)

        if gt_positive and model_positive:
            result.tp += 1
        elif not gt_positive and model_positive:
            result.fp += 1
        elif gt_positive and not model_positive:
            result.fn += 1
        else:
            result.tn += 1

        verdict_str = "YES" if is_positive else ("UNSURE" if is_unsure else "NO")
        _log.info(
            "  → model=%-6s  gt=%-6s  %s",
            verdict_str,
            label.upper(),
            "✓" if (gt_positive == model_positive) else "✗",
        )

    result.latency_ms_avg = sum(latencies) / len(latencies) if latencies else 0.0
    return result


# ── Output formatting ──────────────────────────────────────────────────────────

def print_result(result: EvalResult) -> None:
    n = result.n
    print(f"\n{'─' * 50}")
    print(f"Variant : {result.variant}")
    print(f"Cases   : {n}  (errors: {result.errors})")
    print(f"TP/FP/FN/TN : {result.tp}/{result.fp}/{result.fn}/{result.tn}")
    print(f"Precision : {result.precision:.3f}")
    print(f"Recall    : {result.recall:.3f}")
    print(f"F1        : {result.f1:.3f}")
    print(f"Latency   : {result.latency_ms_avg:.0f} ms avg")
    print(f"{'─' * 50}\n")


def _print_leaderboard(results: list[EvalResult]) -> None:
    ranked = sorted(results, key=lambda r: r.f1, reverse=True)
    print(f"\n{'═' * 60}")
    print(f"{'LEADERBOARD':^60}")
    print(f"{'═' * 60}")
    print(f"{'Rank':<5} {'Variant':<12} {'F1':>6} {'Prec':>6} {'Recall':>6} {'TP/FP/FN/TN':>14} {'ms/img':>8}")
    print(f"{'─' * 60}")
    for i, r in enumerate(ranked, 1):
        marker = " ★" if i == 1 else ""
        stats = f"{r.tp}/{r.fp}/{r.fn}/{r.tn}"
        print(
            f"{i:<5} {r.variant:<12} {r.f1:>6.3f} {r.precision:>6.3f} {r.recall:>6.3f} "
            f"{stats:>14} {r.latency_ms_avg:>7.0f}{marker}"
        )
    print(f"{'═' * 60}\n")
    if ranked:
        best = ranked[0]
        print(f"Best variant: '{best.variant}'  F1={best.f1:.3f}")
        print()


# ── Autoresearch loop ──────────────────────────────────────────────────────────

def run_autoresearch(
    cases_path: str,
    max_variants: int = 4,
    budget_secs: int = 300,
) -> None:
    """
    Autoresearch loop: try each prompt variant in a fixed time budget, keep the best.

    Mirrors karpathy/autoresearch: metric = F1, fixed wall-clock budget,
    prints a ranked leaderboard at the end.
    """
    cases = load_cases(cases_path)
    api_key = os.environ.get("SOLAR_SCOUT_ANTHROPIC_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("Set SOLAR_SCOUT_ANTHROPIC_KEY (or ANTHROPIC_API_KEY) env var")

    client = anthropic.Anthropic(api_key=api_key)
    few_shot = _load_few_shot()

    variants = list(PROMPT_VARIANTS.keys())[:max_variants]
    deadline = time.monotonic() + budget_secs
    results: list[EvalResult] = []
    best_f1 = -1.0
    best_variant = ""

    print(f"\nAutoresearch: {len(variants)} variants, budget={budget_secs}s, cases={len(cases)}")
    print(f"Variants: {', '.join(variants)}\n")

    for variant in variants:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _log.info("Budget exhausted after %d/%d variants", len(results), len(variants))
            break

        _log.info("=== Evaluating variant '%s' (%.0fs remaining) ===", variant, remaining)
        t0 = time.monotonic()
        result = evaluate_prompt(cases, variant=variant, client=client, few_shot=few_shot)
        elapsed = time.monotonic() - t0
        results.append(result)

        improved = result.f1 > best_f1
        if improved:
            best_f1 = result.f1
            best_variant = variant
            status = f"NEW BEST  F1={result.f1:.3f}"
        else:
            status = f"no improvement  F1={result.f1:.3f} (best={best_f1:.3f})"

        print(f"[{variant}] {status}  ({elapsed:.1f}s)")

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _log.info("Budget exhausted after evaluating '%s'", variant)
            break

    _print_leaderboard(results)

    if best_variant:
        print(f"Recommendation: use variant='{best_variant}' in scanner.py")


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Autoresearch-style prompt evaluation for solar panel detection."
    )
    parser.add_argument(
        "--cases",
        default="eval_cases.json",
        help="Path to JSON file with test cases (default: eval_cases.json)",
    )
    parser.add_argument(
        "--autoresearch",
        action="store_true",
        help="Try all prompt variants and print a leaderboard",
    )
    parser.add_argument(
        "--variant",
        default="baseline",
        choices=list(PROMPT_VARIANTS.keys()),
        help="Prompt variant to evaluate (default: baseline)",
    )
    parser.add_argument(
        "--budget",
        type=int,
        default=300,
        help="Time budget in seconds for autoresearch mode (default: 300)",
    )
    args = parser.parse_args()

    if args.autoresearch:
        run_autoresearch(args.cases, budget_secs=args.budget)
    else:
        cases = load_cases(args.cases)
        result = evaluate_prompt(cases, variant=args.variant)
        print_result(result)
