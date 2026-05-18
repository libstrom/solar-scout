"""
Smoke-test för solar-scout pipelinen.

Kör pipelinen end-to-end mot ett urval kända adresser och rapporterar
hur AI:n presterar (precision, recall, F1).

Usage:
    python test_scanner.py <ANTHROPIC_API_KEY>

Bilden hämtas via Lantmäteriet minkarta WMS (gratis). AI-bedömningen körs via
samma _analyze_building som produktionsappen — så denna test verifierar exakt
samma kodväg som AI Scanner-fliken använder.
"""

import sys
import time
import httpx
import anthropic

from scanner import _analyze_building, _fetch_lm_wms, _fetch_mapbox

MAPBOX_TOKEN_FALLBACK = (
    "pk.eyJ1IjoibGlic3Ryb20iLCJhIjoiY21wMmxsN3ZwMDJnejJzc2hhMGJicHZuMiJ9."
    "K1GhO9yhO1EYbwzvmrJ3vQ"
)

# Kända test-fall. Lägg till fler i takt med att ni granskar leads i produktionsappen.
#   expected_has_solar = True  → vi vet det finns solpaneler på taket
#   expected_has_solar = False → vi vet att huset INTE har solpaneler
#   expected_is_house  = False → strukturen är inte en bostad (carport, lager, kyrka...)
TEST_CASES = [
    # Nässjö — solcellsfastigheter enligt befintlig data
    {"address": "Rågången 81, Nässjö",       "expected_is_house": True,  "expected_has_solar": True},
    {"address": "Körsbärsstigen 1, Nässjö",  "expected_is_house": True,  "expected_has_solar": True},
    {"address": "Hultgatan 1, Nässjö",       "expected_is_house": True,  "expected_has_solar": True},

    # Användarverifierade — Småland/Skåne, mix av positiv/negativ
    {"address": "Plommonvägen 1, Lund",      "expected_is_house": True,  "expected_has_solar": True},
    {"address": "Östra Ringvägen 34, Växjö", "expected_is_house": True,  "expected_has_solar": False},
    {"address": "Vilhelmsrogatan 3, Nässjö", "expected_is_house": True,  "expected_has_solar": False},

    # Edge case — solpaneler på garaget, inte huvudbyggnad. Sätt true för att mäta
    # affärsmissen "vi flaggar denna som lead trots att fastigheten redan har sol".
    # {"address": "Skatgatan 5, Jönköping",  "expected_is_house": True,  "expected_has_solar": True},

    # Malmö 2026-05-18: observerade falska positiver från Ljunggatan/Ligustergatan-scan.
    # Ingen av dessa har solceller — ska ge FALSE NEGATIVE om de hittas.
    {"address": "Ljunggatan 12, Malmö",    "expected_is_house": True, "expected_has_solar": False},
    {"address": "Ljunggatan 20, Malmö",    "expected_is_house": True, "expected_has_solar": False},
    {"address": "Ligustergatan 4, Malmö",  "expected_is_house": True, "expected_has_solar": False},
    {"address": "Ligustergatan 13, Malmö", "expected_is_house": True, "expected_has_solar": False},

    # Malmö 2026-05-18: solceller på garage/adjacent roof (samtomt-fall).
    # Huvudhuset har INTE solceller — förväntat SOLAR=NO på central byggnad.
    {"address": "Myrtengatan 11, Malmö",   "expected_is_house": True, "expected_has_solar": False},

    # Inväntar labels:
    # {"address": "Plommonvägen 3, Lund",        ...},
    # {"address": "Handskmakaregatan 1A, Lund",  ...},
]


def geocode_mapbox(address: str, token: str) -> tuple[float, float] | None:
    import urllib.parse
    query = urllib.parse.quote(address)
    resp = httpx.get(
        f"https://api.mapbox.com/geocoding/v5/mapbox.places/{query}.json",
        params={"access_token": token, "country": "se", "limit": 1},
        timeout=10,
    )
    features = resp.json().get("features", [])
    if not features:
        return None
    lng, lat = features[0]["center"]
    return lat, lng


def fetch_image(lat: float, lng: float, mapbox_token: str) -> tuple[bytes | None, str]:
    img = _fetch_lm_wms(lat, lng)
    if img:
        return img, "LM WMS"
    img = _fetch_mapbox(mapbox_token, lat, lng, zoom=20)
    if img:
        return img, "Mapbox"
    return None, ""


def main():
    if len(sys.argv) < 2:
        print("Usage: python test_scanner.py <ANTHROPIC_API_KEY> [MAPBOX_TOKEN]")
        sys.exit(1)

    anthropic_key = sys.argv[1]
    mapbox_token = sys.argv[2] if len(sys.argv) > 2 else MAPBOX_TOKEN_FALLBACK
    client = anthropic.Anthropic(api_key=anthropic_key)

    print(f"\n{'='*70}")
    print(f"SOLAR-SCOUT — smoke-test ({len(TEST_CASES)} adresser)")
    print(f"{'='*70}\n")

    tp = fp = tn = fn = errors = 0

    for case in TEST_CASES:
        addr = case["address"]
        expected_house = case["expected_is_house"]
        expected_solar = case["expected_has_solar"]
        print(f"Adress:   {addr}")
        print(f"Förväntat: HOUSE={'YES' if expected_house else 'NO'}  "
              f"SOLAR={'YES' if expected_solar else 'NO'}")

        coords = geocode_mapbox(addr, mapbox_token)
        if not coords:
            print("  ✗ Kunde inte geokoda — hoppar över\n")
            errors += 1
            continue
        lat, lng = coords
        print(f"  Koord:   {lat:.5f}, {lng:.5f}")

        img, src = fetch_image(lat, lng, mapbox_token)
        if not img:
            print("  ✗ Kunde inte hämta satellitbild — hoppar över\n")
            errors += 1
            continue
        print(f"  Bild:    {len(img)//1024} KB ({src})")

        is_house, has_solar = _analyze_building(client, img)
        print(f"  AI svar: HOUSE={'YES' if is_house else 'NO'}  "
              f"SOLAR={'YES' if has_solar else 'NO'}")

        truth = expected_house and expected_solar
        pred  = is_house and has_solar
        if   truth and pred:       tp += 1; verdict = "✓ TRUE POSITIVE"
        elif truth and not pred:   fn += 1; verdict = "✗ FALSE NEGATIVE (missade)"
        elif not truth and pred:   fp += 1; verdict = "✗ FALSE POSITIVE (felaktig träff)"
        else:                       tn += 1; verdict = "✓ TRUE NEGATIVE"
        print(f"  Verdikt: {verdict}\n")

        time.sleep(0.5)

    total = tp + fp + tn + fn
    precision = tp / (tp + fp) if (tp + fp) else None
    recall    = tp / (tp + fn) if (tp + fn) else None
    f1        = (2 * precision * recall / (precision + recall)
                 if precision and recall else None)

    print(f"{'='*70}")
    print(f"Resultat över {total} fall (errors={errors}):")
    print(f"  TP={tp}  TN={tn}  FP={fp}  FN={fn}")
    if precision is not None:
        print(f"  Precision: {precision:.0%}   (av träffarna, så många var korrekta)")
    if recall is not None:
        print(f"  Recall:    {recall:.0%}   (av sanna fall, så många hittades)")
    if f1 is not None:
        print(f"  F1:        {f1:.0%}")
    print(f"{'='*70}\n")

    if f1 is not None and f1 < 0.8:
        print("⚠  F1 < 80% — gå igenom falska positiv/negativ ovan och justera prompt eller filter.")
        sys.exit(1)


if __name__ == "__main__":
    main()
