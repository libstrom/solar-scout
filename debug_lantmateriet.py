"""
Debug-skript för Lantmäteriet ortofoto API.

Kör: python debug_lantmateriet.py <LM_API_KEY>

Provar alla kända lag-namn och sparar fungerande bilder till /tmp/lm_test_*.png
"""

import sys
import os
import httpx

sys.path.insert(0, os.path.dirname(__file__))
from scanner import _lat_lng_to_tile, _lm_tile_url, _LM_LAYERS, _LM_SERVICE, _LM_ZOOM, _fetch_lantmateriet

# Stockholm city center
TEST_LAT, TEST_LNG = 59.3293, 18.0686

def probe_all_layers(token: str):
    cx, cy = _lat_lng_to_tile(TEST_LAT, TEST_LNG, _LM_ZOOM)
    print(f"Tile ({cx}, {cy}) at zoom {_LM_ZOOM} for ({TEST_LAT}, {TEST_LNG})")
    print(f"Service: {_LM_SERVICE}\n")

    working = []
    for layer in _LM_LAYERS:
        url = _lm_tile_url(token, layer, _LM_ZOOM, cx, cy)
        print(f"Probing layer '{layer}'...")
        print(f"  URL: {url}")
        try:
            r = httpx.get(url, timeout=15)
            ct = r.headers.get("content-type", "")
            print(f"  Status: {r.status_code}  Content-Type: {ct}  Size: {len(r.content)} bytes")
            if r.status_code == 200 and ct.startswith("image"):
                path = f"/tmp/lm_test_{layer.replace('.','_')}.png"
                with open(path, "wb") as f:
                    f.write(r.content)
                print(f"  ✅ Saved to {path}")
                working.append(layer)
            else:
                print(f"  ❌ Response body: {r.text[:200]}")
        except Exception as e:
            print(f"  ❌ Error: {e}")
        print()

    return working


def test_stitched(token: str, layer: str):
    print(f"\nTesting 3×3 tile stitch with layer '{layer}'...")
    img = _fetch_lantmateriet(token, TEST_LAT, TEST_LNG, layer=layer)
    if img:
        path = "/tmp/lm_stitched_640.png"
        with open(path, "wb") as f:
            f.write(img)
        print(f"✅ 640×640 stitched image saved to {path}")
        print(f"   Size: {len(img)} bytes")
    else:
        print("❌ Stitch failed — check that Pillow is installed: pip install pillow")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python debug_lantmateriet.py <LM_API_KEY>")
        print("\nHämta din nyckel på: https://apimanager.lantmateriet.se/devportal/apis")
        sys.exit(1)

    token = sys.argv[1]
    working = probe_all_layers(token)

    if working:
        print(f"✅ Fungerande lager: {working}")
        test_stitched(token, working[0])
        print(f"\nSätt i .env:")
        print(f"  LANTMATERIET_KEY={token}")
        print(f"  # Fungerande lager: {working[0]}")
    else:
        print("❌ Inga lager fungerade.")
        print("\nMöjliga orsaker:")
        print("  1. Fel API-nyckel")
        print("  2. Ortofoto-tjänsten kräver annan prenumeration")
        print("  3. Service-namnet stämmer inte — kolla https://apimanager.lantmateriet.se")
