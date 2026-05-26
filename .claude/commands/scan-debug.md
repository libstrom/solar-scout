---
name: scan-debug
description: Diagnose why solar-scout scan returns 0 leads. Runs pipeline stage-by-stage to pinpoint the break. Use when David reports "0 funna" or scan returns empty, or scan crashes.
---

# scan-debug — Solar Scout diagnostik

Arbeta igenom dessa steg i ordning. Stoppa vid första felet och rapportera orsak + fix.

## Steg 1 — Kontrollera Overpass-anslutning

Kör detta Python-snippet för att testa om Overpass API svarar:

```python
import httpx
resp = httpx.post(
    "https://overpass-api.de/api/interpreter",
    data='[out:json][timeout:10]; node["solar"="yes"](55.5,13.0,55.6,13.1); out count;',
    timeout=15
)
print(resp.status_code, resp.text[:200])
```

**Förväntat:** HTTP 200 med JSON-svar.
**Om timeout/503:** Overpass är nere — vänta 2 min och försök igen, eller byt till `https://overpass.kumi.systems/api/interpreter`.

## Steg 2 — Kontrollera OSM-byggnader

Bekräfta att `_get_osm_buildings` hittar hus i ett känt område:

```python
from scanner import _get_osm_buildings
buildings = _get_osm_buildings(57.60, 16.18, 57.65, 16.25)  # Nässjö centrum
print(f"{len(buildings)} byggnader hittade")
print(buildings[:2] if buildings else "TOMT")
```

**Förväntat:** 20–200 byggnader.
**Om 0:** Filtret `MIN_BUILDING_AREA_M2` kan vara för strängt, eller Overpass-svaret är tomt (se Steg 1).

## Steg 3 — Kontrollera ANTHROPIC_API_KEY

```python
import os, anthropic
key = os.environ.get("ANTHROPIC_API_KEY", "")
print("Nyckel satt:", bool(key), "— längd:", len(key))
client = anthropic.Anthropic(api_key=key)
msg = client.messages.create(model="claude-haiku-4-5-20251001", max_tokens=5,
      messages=[{"role":"user","content":"ping"}])
print("API OK:", msg.content[0].text)
```

**Om AuthenticationError/402:** Nyckeln ogiltig eller billing-gräns nådd.
**Fix:** Kontrollera Anthropic Console → https://console.anthropic.com/settings/billing

## Steg 4 — Kontrollera LM WMS bildkälla

```python
import httpx
url = ("https://api.lantmateriet.se/open/topowebb-ccby/v1/wmts/1.0.0/"
       "topowebb/default/3857/10/564/309.png?token=TEST")
resp = httpx.get(url, timeout=10)
print(resp.status_code, resp.headers.get("content-type"))
```

**Om 401/403:** LANTMATERIET_KEY saknas eller är ogiltig.
**Om 200:** LM fungerar.
**Fallback:** Google Static Maps används automatiskt om LM misslyckas (kräver GOOGLE_API_KEY).

## Steg 5 — Kontrollera Google API (fallback-bildkälla)

```python
import os
key = os.environ.get("GOOGLE_API_KEY", "")
print("GOOGLE_API_KEY satt:", bool(key))
import httpx
url = f"https://maps.googleapis.com/maps/api/staticmap?center=57.65,16.22&zoom=19&size=100x100&maptype=satellite&key={key}"
resp = httpx.get(url, timeout=10)
print("Status:", resp.status_code, "— content-type:", resp.headers.get("content-type","?"))
```

**Om 403:** Google billing-problem eller API ej aktiverad — https://console.cloud.google.com/billing

## Feldiagnostabell

| Symptom | Mest trolig orsak | Fix |
|---------|-------------------|-----|
| 0 leads, ingen feltext | Overpass timeout | Vänta 2 min, försök igen |
| 0 leads, "0 byggnader" i logg | Bbox för liten eller filter för strängt | Testa med känd Nässjö-bbox |
| Hänger på AI-steget | ANTHROPIC_API_KEY ogiltig/tom | Verifiera i Anthropic Console |
| "APIQuotaExceededError" | Anthropic billing-gräns nådd | Ladda på kontot + du får akut mail |
| Tomma bilder / grå rutor | LM WMS och Google båda nere | Kolla resp. API-konsoler |
| Scannar klart men 0 detekterade | AI ser inga paneler | Normalt i tätort — prova Nässjö/Huskvarna |

## Om logg behövs

```python
import logging
logging.basicConfig(level=logging.DEBUG)
# Kör sedan scan_bbox(...) — se alla steg i terminalen
```
