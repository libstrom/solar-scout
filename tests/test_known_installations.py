"""
Beteendetester för _is_existing_customer.

Testar VADS som ska hända, inte HUR det är implementerat.
Installationslistan injiceras direkt — inget Supabase-mock behövs.
"""
import pytest


NASSJO_INSTALLATION = (57.6398, 14.7056, "Queckfeldtsgatan 17, Nässjö")
MALMO_INSTALLATION  = (55.5706, 13.0379, "Risholmsgatan 8, Malmö")
SAMPLE_LIST = [NASSJO_INSTALLATION, MALMO_INSTALLATION]


def test_nearby_building_is_skipped():
    """Byggnad 10m från känd installation ska skippas."""
    from scanner import _is_existing_customer
    # 10m norr om Queckfeldtsgatan 17
    assert _is_existing_customer(57.6399, 14.7056, installations=SAMPLE_LIST) is True


def test_distant_building_is_not_skipped():
    """Byggnad 500m från närmaste installation ska inte skippas."""
    from scanner import _is_existing_customer
    assert _is_existing_customer(57.6450, 14.7200, installations=SAMPLE_LIST) is False


def test_empty_list_never_skips():
    """Tom installationslista → inget skippas."""
    from scanner import _is_existing_customer
    assert _is_existing_customer(57.6398, 14.7056, installations=[]) is False


def test_supabase_unavailable_does_not_crash():
    """Om Supabase är nere ska funktionen returnera False, inte krascha."""
    from unittest.mock import patch
    from scanner import _is_existing_customer
    with patch("scanner._load_installations", side_effect=Exception("connection refused")):
        # Anropar utan injicerad lista → faller tillbaka på _load_installations
        result = _is_existing_customer(57.6398, 14.7056)
    assert result is False
