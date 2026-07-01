"""scan_cost.py — kostnadsestimat och budgetspärr för Solar Scout-scanningar.

Två syften:
  1. Visa användaren *innan* en scan vad den kan kosta (estimate_scan_cost).
  2. Avbryta en pågående scan om den ackumulerade kostnaden når ett tak
     (BudgetTracker + ScanBudgetExceededError).

Designmål (från Linus/Ibrahim):
  - Tydligt vad en scan kan kosta INNAN man startar.
  - Hård spärr vid DEFAULT_BUDGET_SEK (5000 kr) — skenande kostnad ska vara omöjlig.
  - Scans över APPROVAL_THRESHOLD_SEK kräver Ibrahims godkännande (gaten byggs i app.py).

VIKTIGT: prissättningen nedan är hårdkodad och måste stämmas av mot
Anthropic Console → Billing när modeller/priser ändras. Siffrorna är i USD per
1 000 000 tokens (MTok), enligt Claude 4.5-familjens prislista.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field


# ── Prissättning ─────────────────────────────────────────────────────────────
# USD per 1 000 000 tokens. VERIFIERA mot https://console.anthropic.com/settings/billing
@dataclass(frozen=True)
class ModelPricing:
    input: float          # vanlig input
    output: float         # output
    cache_write: float    # skriva till prompt-cache (dyrare än input)
    cache_read: float     # läsa från prompt-cache (mycket billigt)


# Claude Opus 4.8 — primär analysmodell i scanner.py
# $5/$25 in/out, cache-write 5m = 1.25x input, cache-read = 0.1x input.
OPUS_4_8 = ModelPricing(input=5.00, output=25.00, cache_write=6.25, cache_read=0.50)
# Claude Sonnet 4.6 (behålls för referens och eval-skript)
SONNET_4_6 = ModelPricing(input=3.00, output=15.00, cache_write=3.75, cache_read=0.30)
# Claude Haiku 4.5 — prefilter i scanner.py
HAIKU_4_5 = ModelPricing(input=1.00, output=5.00, cache_write=1.25, cache_read=0.10)

# Växelkurs. Justera vid behov — medvetet konservativ (hellre överskatta kostnad).
USD_TO_SEK = 10.50

# Budget-trösklar (SEK)
DEFAULT_BUDGET_SEK = 5000.0       # hård spärr — scan avbryts här
CONFIRM_THRESHOLD_SEK = 200.0     # över detta måste scannaren bocka i "jag förstår kostnaden"
APPROVAL_THRESHOLD_SEK = CONFIRM_THRESHOLD_SEK  # bakåtkompat-alias


# ── Token-antaganden per byggnad ─────────────────────────────────────────────
# Grundade på _analyze_building: en satellitbild + cachad systemprompt/few-shot,
# output max 220 tokens. UNSURE-fall triggar ett extra Street View-anrop.
CACHED_CONTEXT_TOKENS = 11_000    # systemprompt + upp till 8 few-shot-bilder (cachas en gång)
SATELLITE_IMG_TOKENS = 1_500      # en färsk satellitbild per byggnad (ej cachad)
STREETVIEW_IMG_TOKENS = 1_500     # extra bild när modellen är osäker
OUTPUT_TOKENS = 220               # max_tokens i _analyze_building

# Google Maps Static API — primär satellitbildskälla (Standard tier).
# Verifiera mot Google Maps Platform Console vid prisändringar.
GOOGLE_STATIC_MAPS_USD_PER_REQUEST = 0.002  # 2 USD / 1 000 requests

# Andel byggnader som blir UNSURE och drar ett extra Street View-anrop
# (av de byggnader som når Opus — se PREFILTER_PASS_RATE_* nedan).
# Lågt/förväntat/högt — ger ett spann i estimatet.
UNSURE_FRACTION_LOW = 0.05
UNSURE_FRACTION_EXPECTED = 0.15
UNSURE_FRACTION_HIGH = 0.35

# Andel byggnader som klarar Haiku-prefiltret och når (dyra) Opus 4.8.
# _prefilter_building() dokumenterar "saves ~60% of Opus calls" — dvs ~40% når fram.
# HIGH = 1.0 (inget prefilter alls) används medvetet som säkerhetstak för
# budget-grinden (requires_approval/exceeds_budget) — se estimate_scan_cost().
PREFILTER_PASS_RATE_LOW = 0.30
PREFILTER_PASS_RATE_EXPECTED = 0.40
PREFILTER_PASS_RATE_HIGH = 1.0

# Haiku-prefiltret körs på ALLA byggnader (samma satellitbild, kort ja/nej-svar).
HAIKU_PREFILTER_OUTPUT_TOKENS = 10  # max_tokens i _prefilter_building


def _cost_usd(tokens: int, rate_per_mtok: float) -> float:
    return tokens / 1_000_000 * rate_per_mtok


def _per_building_usd(
    unsure_fraction: float,
    prefilter_pass_rate: float = PREFILTER_PASS_RATE_HIGH,
    pricing: ModelPricing = OPUS_4_8,
) -> float:
    """Förväntad kostnad (USD) för en byggnad i steady state (cachen redan varm).

    Haiku-prefiltret körs på alla byggnader; bara prefilter_pass_rate andel
    av dem går vidare till (mycket dyrare) Opus 4.8.
    """
    haiku_cost = _cost_usd(SATELLITE_IMG_TOKENS, HAIKU_4_5.input) + _cost_usd(
        HAIKU_PREFILTER_OUTPUT_TOKENS, HAIKU_4_5.output
    )
    cache_read = _cost_usd(CACHED_CONTEXT_TOKENS, pricing.cache_read)
    fresh_input = _cost_usd(SATELLITE_IMG_TOKENS, pricing.input)
    output = _cost_usd(OUTPUT_TOKENS, pricing.output)
    base = cache_read + fresh_input + output
    # extra Street View-anrop för en andel av de Opus-analyserade byggnaderna
    sv_extra = unsure_fraction * (
        _cost_usd(STREETVIEW_IMG_TOKENS, pricing.input)
        + _cost_usd(CACHED_CONTEXT_TOKENS, pricing.cache_read)
        + _cost_usd(OUTPUT_TOKENS, pricing.output)
    )
    return haiku_cost + prefilter_pass_rate * (base + sv_extra)


@dataclass
class CostEstimate:
    n_buildings: int
    low_sek: float
    expected_sek: float
    high_sek: float
    per_building_sek: float
    requires_approval: bool
    exceeds_budget: bool

    @property
    def requires_confirm(self) -> bool:
        """Alias — scannaren måste bocka i "jag förstår kostnaden" först."""
        return self.requires_approval

    def summary(self) -> str:
        return (
            f"~{self.n_buildings} byggnader · est. "
            f"{self.expected_sek:.0f} kr "
            f"(spann {self.low_sek:.0f}–{self.high_sek:.0f} kr)"
        )


def estimate_scan_cost(
    n_buildings: int,
    budget_sek: float = DEFAULT_BUDGET_SEK,
    approval_threshold_sek: float = APPROVAL_THRESHOLD_SEK,
) -> CostEstimate:
    """Uppskatta vad en scan av n_buildings kommer att kosta, i SEK.

    Returnerar ett spann (lågt/förväntat/högt) plus flaggor för om scanen
    kräver godkännande eller överskrider budgettaket.
    """
    n = max(0, int(n_buildings))
    # Engångskostnad för att värma cachen (skrivs en gång per scan).
    cache_write_usd = _cost_usd(CACHED_CONTEXT_TOKENS, OPUS_4_8.cache_write)
    # Google Maps Static API: en bild per byggnad (alla, för Haiku-prefiltret)
    # + en per UNSURE-fall bland de som nådde Opus (street view).
    maps_low = n * (
        1 + PREFILTER_PASS_RATE_LOW * UNSURE_FRACTION_LOW
    ) * GOOGLE_STATIC_MAPS_USD_PER_REQUEST
    maps_expected = n * (
        1 + PREFILTER_PASS_RATE_EXPECTED * UNSURE_FRACTION_EXPECTED
    ) * GOOGLE_STATIC_MAPS_USD_PER_REQUEST
    maps_high = n * (
        1 + PREFILTER_PASS_RATE_HIGH * UNSURE_FRACTION_HIGH
    ) * GOOGLE_STATIC_MAPS_USD_PER_REQUEST

    low = cache_write_usd + n * _per_building_usd(
        UNSURE_FRACTION_LOW, PREFILTER_PASS_RATE_LOW
    ) + maps_low
    expected = cache_write_usd + n * _per_building_usd(
        UNSURE_FRACTION_EXPECTED, PREFILTER_PASS_RATE_EXPECTED
    ) + maps_expected
    # HIGH = säkerhetstak: antar att prefiltret INTE hjälper alls (pass_rate=1.0).
    # Detta är vad requires_approval/exceeds_budget grindar mot — medvetet
    # konservativt så budgetspärren aldrig underskattar en skenande scan.
    high = cache_write_usd + n * _per_building_usd(
        UNSURE_FRACTION_HIGH, PREFILTER_PASS_RATE_HIGH
    ) + maps_high

    low_sek = low * USD_TO_SEK
    expected_sek = expected * USD_TO_SEK
    high_sek = high * USD_TO_SEK
    per_building_sek = (
        _per_building_usd(UNSURE_FRACTION_EXPECTED, PREFILTER_PASS_RATE_EXPECTED)
        * USD_TO_SEK
    )

    return CostEstimate(
        n_buildings=n,
        low_sek=low_sek,
        expected_sek=expected_sek,
        high_sek=high_sek,
        per_building_sek=per_building_sek,
        # Använd det HÖGA spannet för gaten — hellre be om godkännande i onödan.
        requires_approval=high_sek >= approval_threshold_sek,
        exceeds_budget=high_sek >= budget_sek,
    )


# ── Runtime-budgetspärr ──────────────────────────────────────────────────────

class ScanBudgetExceededError(RuntimeError):
    """Kastas när en pågående scan når kostnadstaket. Redan hittade leads sparas."""

    def __init__(self, spent_sek: float, budget_sek: float, buildings_done: int):
        self.spent_sek = spent_sek
        self.budget_sek = budget_sek
        self.buildings_done = buildings_done
        super().__init__(
            f"Scan-budget nådd: {spent_sek:.0f} kr av taket {budget_sek:.0f} kr "
            f"efter {buildings_done} byggnader — scanen stoppades."
        )


@dataclass
class BudgetTracker:
    """Trådsäker ackumulator för faktisk scan-kostnad.

    Mata in verklig token-användning per anrop via add_usage(). Anropa check()
    efter varje byggnad — den kastar ScanBudgetExceededError när taket nås.

    Trådsäker eftersom scan_buildings_ai kör en ThreadPoolExecutor.
    """

    budget_sek: float = DEFAULT_BUDGET_SEK
    pricing: ModelPricing = field(default=OPUS_4_8)
    stopped_over_budget: bool = False
    _cost_usd: float = 0.0
    _buildings: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add_usage(
        self,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
    ) -> None:
        cost = (
            _cost_usd(input_tokens, self.pricing.input)
            + _cost_usd(output_tokens, self.pricing.output)
            + _cost_usd(cache_read_tokens, self.pricing.cache_read)
            + _cost_usd(cache_write_tokens, self.pricing.cache_write)
        )
        with self._lock:
            self._cost_usd += cost

    def add_anthropic_usage(self, usage) -> None:
        """Bekvämlighet: mata in ett anthropic usage-objekt direkt."""
        self.add_usage(
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
        )

    def mark_building(self) -> None:
        with self._lock:
            self._buildings += 1

    @property
    def spent_sek(self) -> float:
        with self._lock:
            return self._cost_usd * USD_TO_SEK

    @property
    def buildings_done(self) -> int:
        with self._lock:
            return self._buildings

    def check(self) -> None:
        """Kasta om budgeten är överskriden. Anropas efter varje byggnad."""
        spent = self.spent_sek
        if spent >= self.budget_sek:
            raise ScanBudgetExceededError(spent, self.budget_sek, self.buildings_done)
