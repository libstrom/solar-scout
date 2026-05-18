# leads-now

Get David actual solar leads as fast as possible. Skip everything else.

## Decision tree

```
Har vi Railway-appen uppe?
  Ja → Gå direkt till appen, scanna en stad, spara leads
  Nej → Kolla Railway dashboard

Fungerar scanningen?
  Ort-läge fungerar → Använd det, rita-läge är sekundärt
  0 leads → Kör /scan-debug
  
Vilken stad ger mest träffar?
  → Nässjö, Huskvarna, Vetlanda (mellanstor stad, hög villa-täthet)
  → Undvik Stockholm/Göteborg (för stort, timeout-risk)
  → Undvik glesbygd (för få byggnader i OSM)
```

## Snabbaste vägen till leads

1. Öppna Railway-appen
2. Flik "Scanna" → Ort/stad → t.ex. "Huskvarna"
3. Max antal leads: 30
4. Starta scanning (vänta 2–5 min)
5. Klicka "Spara alla till Leadslista"
6. Flik "Leads" → exportera CSV

## Om 0 leads

Kör `/scan-debug` för strukturerad felsökning.

Snabbkoll utan felsökning:
- Försök med en annan ort
- Kontrollera att ANTHROPIC_API_KEY är satt på Railway
- Kolla Railway-loggarna efter `Overpass returned 0 elements`

## Vad som är live just nu

Kontrollera att main-branchen har:
- `fix: remove Accept header from Overpass` (rotorsaken till 0 leads)
- `feat: cookie-based auth` (sessioner överlever omstarter)
- `fix: persist scan results in session_state` (spara-knapp funkar)

```bash
git log --oneline origin/main | head -5
```
