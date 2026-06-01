"""Tester för scan_cost — kostnadsestimat och budgetspärr. Ingen API-kostnad."""

import pytest

from scan_cost import (
    estimate_scan_cost,
    BudgetTracker,
    ScanBudgetExceededError,
    DEFAULT_BUDGET_SEK,
    APPROVAL_THRESHOLD_SEK,
    SONNET_4_6,
    OPUS_4_8,
)


# ── Estimat ──────────────────────────────────────────────────────────────────

def test_zero_buildings_is_cheap():
    est = estimate_scan_cost(0)
    assert est.n_buildings == 0
    # bara engångs-cache-write, någon ören
    assert est.expected_sek < 1.0
    assert not est.exceeds_budget
    assert not est.requires_approval


def test_estimate_span_is_ordered():
    est = estimate_scan_cost(600)
    assert est.low_sek <= est.expected_sek <= est.high_sek


def test_typical_city_scan_is_reasonable():
    # 600 hus ska kosta i tiotals–hundratals kronor, inte tusentals
    est = estimate_scan_cost(600)
    assert 10 < est.expected_sek < 1000
    assert not est.exceeds_budget


def test_per_building_cost_positive_and_small():
    est = estimate_scan_cost(100)
    assert 0 < est.per_building_sek < 5.0  # några ören per byggnad


def test_huge_scan_flags_approval_and_budget():
    est = estimate_scan_cost(100_000)
    assert est.requires_approval
    assert est.exceeds_budget


def test_approval_threshold_boundary():
    # hitta ett antal byggnader som passerar godkännande-tröskeln
    small = estimate_scan_cost(10)
    assert not small.requires_approval
    # tillräckligt många för att passera APPROVAL_THRESHOLD_SEK
    big = estimate_scan_cost(20_000)
    assert big.requires_approval


def test_estimate_scales_monotonically():
    a = estimate_scan_cost(100).expected_sek
    b = estimate_scan_cost(1000).expected_sek
    assert b > a


def test_summary_string_contains_kr():
    est = estimate_scan_cost(300)
    s = est.summary()
    assert "kr" in s
    assert "300" in s


# ── BudgetTracker ──────────────────────────────────────────────────────────────

def test_tracker_starts_at_zero():
    t = BudgetTracker()
    assert t.spent_sek == 0.0
    assert t.buildings_done == 0
    t.check()  # ska inte kasta


def test_tracker_accumulates_cost():
    t = BudgetTracker()
    t.add_usage(input_tokens=1_000_000, output_tokens=0)
    # 1 MTok input på Opus 4.8 (trackerns default) = $5 → ~52.5 kr
    assert t.spent_sek == pytest.approx(OPUS_4_8.input * 10.5, rel=1e-6)


def test_tracker_check_raises_over_budget():
    t = BudgetTracker(budget_sek=10.0)
    # tillräckligt med output-tokens för att passera 10 kr
    # 1 MTok output = $15 = 157.5 kr → vida över 10 kr
    t.add_usage(output_tokens=1_000_000)
    with pytest.raises(ScanBudgetExceededError) as exc:
        t.check()
    assert exc.value.budget_sek == 10.0
    assert exc.value.spent_sek > 10.0


def test_tracker_under_budget_does_not_raise():
    t = BudgetTracker(budget_sek=5000.0)
    t.add_usage(input_tokens=1000, output_tokens=220)
    t.check()  # långt under taket


def test_tracker_default_budget_is_5000():
    t = BudgetTracker()
    assert t.budget_sek == DEFAULT_BUDGET_SEK == 5000.0


def test_tracker_marks_buildings():
    t = BudgetTracker()
    t.mark_building()
    t.mark_building()
    assert t.buildings_done == 2


def test_add_anthropic_usage_object():
    class FakeUsage:
        input_tokens = 1500
        output_tokens = 220
        cache_read_input_tokens = 11000
        cache_creation_input_tokens = 0

    t = BudgetTracker()
    t.add_anthropic_usage(FakeUsage())
    assert t.spent_sek > 0


def test_tracker_threadsafe_accumulation():
    import threading

    t = BudgetTracker(budget_sek=1e9)  # högt tak, vi testar bara summering
    def worker():
        for _ in range(100):
            t.add_usage(input_tokens=1000)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    # 8 trådar × 100 × 1000 = 800_000 input-tokens på Opus 4.8 (default)
    expected_sek = 800_000 / 1_000_000 * OPUS_4_8.input * 10.5
    assert t.spent_sek == pytest.approx(expected_sek, rel=1e-6)


# ── Ny funktionalitet: budget shared across multiple scan_buildings_ai calls ──

def test_shared_budget_accumulates_across_calls():
    """BudgetTracker som delas mellan flera scan_buildings_ai-anrop ska
    ackumulera korrekt — INTE återställa till 0 kr per anrop."""
    t = BudgetTracker(budget_sek=1000.0)
    # Simulera tre on-by-one anrop
    t.add_usage(input_tokens=10_000)
    t.add_usage(input_tokens=10_000)
    t.add_usage(input_tokens=10_000)
    total_expected = 30_000 / 1_000_000 * OPUS_4_8.input * 10.5
    assert t.spent_sek == pytest.approx(total_expected, rel=1e-6)


def test_budget_stopped_over_budget_flag():
    """stopped_over_budget startar False och kan sättas till True."""
    t = BudgetTracker(budget_sek=100.0)
    assert not t.stopped_over_budget
    # Överskrid taket manuellt (som scan_buildings_ai gör i except-blocket)
    t.add_usage(input_tokens=10_000_000)  # >>> 100 SEK
    with pytest.raises(ScanBudgetExceededError) as exc_info:
        t.check()
    assert exc_info.value.spent_sek >= 100.0
    t.stopped_over_budget = True
    assert t.stopped_over_budget


def test_estimate_exceeds_budget_blocks_scan():
    """estimate_scan_cost(N) ska flagga exceeds_budget när N är för stort."""
    # 200 000 byggnader borde spränka 5000 kr-taket
    est = estimate_scan_cost(200_000)
    assert est.exceeds_budget


def test_estimate_small_scan_does_not_exceed_budget():
    """En liten scan (600 byggnader) ska INTE överstiga 5000 kr-taket."""
    est = estimate_scan_cost(600)
    assert not est.exceeds_budget
    assert est.high_sek < DEFAULT_BUDGET_SEK
