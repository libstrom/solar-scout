"""
Testskript — kör tre kända solcellsadresser mot Claude Vision.
Primär bild: Lantmäteriet minkarta WMS (gratis). Fallback: Mapbox.
"""

import base64
import math
import sys
import httpx
import anthropic

MAPBOX_TOKEN  = "pk.eyJ1IjoibGlic3Ryb20iLCJhIjoiY21wMmxsN3ZwMDJnejJzc2hhMGJicHZuMiJ9.K1GhO9yhO1EYbwzvmrJ3vQ"
ANTHROPIC_KEY = sys.argv[1] if len(sys.argv) > 1 else ""

TEST_ADDRESSES = [
    "Rågången 81, Nässjö",
    "Körsbärsstigen 1, Nässjö",
    "Hultgatan 1, Nässjö",
]


def geocode(address: str) -> tuple[float, float] | None:
    import urllib.parse
    query = urllib.parse.quote(address)
    resp = httpx.get(
        f"https://api.mapbox.com/geocoding/v5/mapbox.places/{query}.json",
        params={"access_token": MAPBOX_TOKEN, "country": "se", "limit": 1},
        timeout=10,
    )
    data = resp.json()
    features = data.get("features", [])
    if not features:
        return None
    lng, lat = features[0]["center"]
    return lat, lng


def fetch_lm_wms(lat: float, lng: float, size_m: float = 50) -> bytes | None:
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
        print(f"  LM WMS {resp.status_code}: {resp.text[:80]}")
    except Exception as e:
        print(f"  LM WMS fel: {e}")
    return None


def fetch_mapbox(lat: float, lng: float, zoom: int = 20) -> bytes | None:
    url = (
        f"https://api.mapbox.com/styles/v1/mapbox/satellite-v9/static"
        f"/{lng},{lat},{zoom}/640x640"
        f"?access_token={MAPBOX_TOKEN}"
    )
    resp = httpx.get(url, timeout=20)
    if resp.status_code != 200:
        print(f"  Mapbox {resp.status_code}: {resp.text[:100]}")
        return None
    return resp.content


def fetch_image(lat: float, lng: float) -> tuple[bytes | None, str]:
    img = fetch_lm_wms(lat, lng)
    if img:
        return img, "LM WMS"
    img = fetch_mapbox(lat, lng, zoom=20)
    if img:
        return img, "Mapbox"
    return None, ""


def ask_claude(client: anthropic.Anthropic, img: bytes) -> tuple[str, str]:
    b64 = base64.standard_b64encode(img).decode()
    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=120,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/jpeg", "data": b64},
                },
                {
                    "type": "text",
                    "text": (
                        "You are looking at a Swedish aerial orthophoto (top-down, ~50m wide) "
                        "centred on a residential building.\n\n"
                        "From directly above, PV solar panels appear as:\n"
                        "- Uniformly flat, very dark (dark blue/black/charcoal) rectangular patches\n"
                        "- Distinctly flatter and more uniform than surrounding roof tiles\n"
                        "- Arranged in a rectangular array on part of the roof\n"
                        "- Often slightly shinier or more reflective than the rest of the roof\n\n"
                        "Does any part of the central building's roof look like solar panels? "
                        "Answer YES or NO, then one sentence explaining what you see."
                    ),
                },
            ],
        }],
    )
    text = msg.content[0].text.strip()
    clean = text.lstrip("*_ ").upper()
    verdict = "YES" if clean.startswith("YES") else "NO"
    return verdict, text


def main():
    if not ANTHROPIC_KEY:
        print("Usage: python test_scanner.py <ANTHROPIC_API_KEY>")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    correct = 0

    print(f"\n{'='*60}")
    print("SOLCELLSTEST — 3 kända adresser")
    print(f"{'='*60}\n")

    for addr in TEST_ADDRESSES:
        print(f"Adress: {addr}")

        coords = geocode(addr)
        if not coords:
            print("  Kunde inte geokoda adressen\n")
            continue
        lat, lng = coords
        print(f"  Koordinater: {lat:.5f}, {lng:.5f}")

        img, source = fetch_image(lat, lng)
        if not img:
            print("  Kunde inte hämta satellitbild\n")
            continue
        print(f"  Bild: {len(img)//1024} KB ({source})")

        verdict, explanation = ask_claude(client, img)
        icon = "YES" if verdict == "YES" else "NO"
        print(f"  Claude: {icon}")
        print(f"  Förklaring: {explanation}")

        if verdict == "YES":
            correct += 1
        print()

    print(f"{'='*60}")
    print(f"Resultat: {correct}/{len(TEST_ADDRESSES)} korrekt detekterade")
    accuracy = correct / len(TEST_ADDRESSES) * 100
    print(f"Träffsäkerhet: {accuracy:.0f}%")

    if accuracy < 67:
        print("\nLåg träffsäkerhet — promoten behöver justeras eller zoom är fel")
    elif accuracy < 100:
        print("\nDelvis OK — en adress missades, kolla zoom/geocoding för den")
    else:
        print("\nPerfekt — AI hittar alla kända solcellstak!")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
