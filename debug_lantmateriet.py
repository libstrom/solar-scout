"""
Debug-skript för Lantmäteriets officiella ortofoto-WMS (Ortofoto Visning, CC-BY).

Kör: python debug_lantmateriet.py <consumer_key:consumer_secret>

Provar alla kända lagernamn via WMS GetMap (Basic auth) och sparar fungerande
bilder till /tmp/lm_test_*.jpg. Använd samma credential-format som i secrets:
LANTMATERIET_KEY = "consumer_key:consumer_secret"
"""

import sys
import os
import httpx

sys.path.insert(0, os.path.dirname(__file__))
from scanner import (
    _LM_LAYERS,
    _LM_WMS_ENDPOINT,
    _lm_wms_url,
    _lm_basic_auth,
    _fetch_lantmateriet,
)

# Stockholm city center
TEST_LAT, TEST_LNG = 59.3293, 18.0686


def probe_all_layers(lm_key: str):
    auth = _lm_basic_auth(lm_key)
    if auth is None:
        print("❌ LANTMATERIET_KEY måste ha formatet 'consumer_key:consumer_secret'")
        return []

    print(f"Endpoint: {_LM_WMS_ENDPOINT}")
    print(f"Punkt: ({TEST_LAT}, {TEST_LNG})  ·  auth-user: {auth[0]}\n")

    working = []
    for layer in _LM_LAYERS:
        url = _lm_wms_url(layer, TEST_LAT, TEST_LNG)
        print(f"Provar lager '{layer}'...")
        print(f"  URL: {url}")
        try:
            r = httpx.get(url, timeout=15, auth=auth)
            ct = r.headers.get("content-type", "")
            print(f"  Status: {r.status_code}  Content-Type: {ct}  Size: {len(r.content)} bytes")
            if r.status_code == 200 and ct.startswith("image"):
                path = f"/tmp/lm_test_{layer.replace('.', '_').replace(',', '+')}.jpg"
                with open(path, "wb") as f:
                    f.write(r.content)
                print(f"  ✅ Sparad till {path}")
                working.append(layer)
            else:
                print(f"  ❌ Svar: {r.text[:200]}")
        except Exception as e:
            print(f"  ❌ Fel: {e}")
        print()

    return working


def test_fetch(lm_key: str, layer: str):
    print(f"\nTestar _fetch_lantmateriet med lager '{layer}'...")
    img = _fetch_lantmateriet(lm_key, TEST_LAT, TEST_LNG, layer=layer)
    if img:
        path = "/tmp/lm_fetch_640.jpg"
        with open(path, "wb") as f:
            f.write(img)
        print(f"✅ 640×640-bild sparad till {path}  ({len(img)} bytes)")
    else:
        print("❌ Hämtning misslyckades — se varningar ovan.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python debug_lantmateriet.py <consumer_key:consumer_secret>")
        print("\nBeställ 'Ortofoto Visning' (CC-BY) på: https://geotorget.lantmateriet.se")
        print("Teknisk beskrivning: GEODOK/64")
        sys.exit(1)

    lm_key = sys.argv[1]
    working = probe_all_layers(lm_key)

    if working:
        print(f"✅ Fungerande lager: {working}")
        test_fetch(lm_key, working[0])
        print("\nSätt i Streamlit secrets:")
        print(f'  LANTMATERIET_KEY = "{lm_key}"')
        print(f"  # Fungerande lager: {working[0]}")
    else:
        print("❌ Inga lager fungerade.")
        print("\nMöjliga orsaker:")
        print("  1. Fel credentials (ska vara consumer_key:consumer_secret från Geotorget)")
        print("  2. Produkten 'Ortofoto Visning' är inte beställd/aktiverad på kontot")
        print("  3. Endpoint/lagernamn ändrat — kolla GEODOK/64 teknisk beskrivning")
