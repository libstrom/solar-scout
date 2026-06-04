#!/usr/bin/env python3
"""
enrichContacts.py — Berika energy-data.json med kontaktuppgifter via Hitta.se

Kör en riktig Chromium-webbläsare (Playwright) så att Hitta.se inte
blockerar. Kan köras synligt (--headed) eller i bakgrunden.

Usage:
    python enrichContacts.py energy-data.json [--limit 50] [--headed] [--dry-run]

Kräver:
    pip install playwright
    playwright install chromium
"""
import sys
import json
import time
import re
import random
import argparse
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
except ImportError:
    print("Kör:\n  pip install playwright\n  playwright install chromium")
    sys.exit(1)

_TEL_RE  = re.compile(r'(0|\+46)[\d\s\-]{6,12}')
_NAME_RE = re.compile(r'^[A-ZÅÄÖ][a-zåäö]+(\s[A-ZÅÄÖ][a-zåäö]+)+$')


def clean_tel(s: str) -> str:
    s = re.sub(r'[\s\-]', '', s.strip())
    if s.startswith('+46'):
        s = '0' + s[3:]
    return s


def human_delay(base=1.8):
    time.sleep(base + random.uniform(0.3, 1.2))


def search_hitta(page, adress: str, ort: str) -> dict | None:
    q = f"{adress} {ort}".strip()
    if not q:
        return None

    url = f"https://www.hitta.se/sök?vad=&var={q}"
    try:
        page.goto(url, wait_until='domcontentloaded', timeout=20_000)
        human_delay(1.5)
    except PWTimeout:
        return None

    # Acceptera cookies om dialog dyker upp
    try:
        btn = page.locator('button:has-text("Acceptera"), button:has-text("Godkänn")').first
        if btn.is_visible(timeout=2000):
            btn.click()
            human_delay(0.8)
    except Exception:
        pass

    content = page.content()

    # --- JSON-LD (mest stabilt) ---
    import json as _json
    for m in re.finditer(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>',
                         content, re.S):
        try:
            obj = _json.loads(m.group(1))
            items = obj if isinstance(obj, list) else [obj]
            for item in items:
                if item.get('@type') in ('Person', 'LocalBusiness'):
                    namn = item.get('name', '').strip()
                    tel  = clean_tel(item.get('telephone', ''))
                    if namn:
                        return {'namn': namn, 'telefon': tel,
                                'email': item.get('email', ''),
                                'källa': 'hitta/json-ld'}
        except Exception:
            pass

    # --- HTML: personsökningskort ---
    soup_cards = page.locator('[class*="PersonCard"], [class*="person-card"], [data-testid*="person"]')
    try:
        count = soup_cards.count()
    except Exception:
        count = 0

    if count:
        card = soup_cards.first
        try:
            namn = card.locator('h2, h3, [class*="name"]').first.inner_text(timeout=2000).strip()
        except Exception:
            namn = ''
        try:
            tel_el = card.locator('[href^="tel:"]').first
            tel = clean_tel(tel_el.get_attribute('href', timeout=2000).replace('tel:', ''))
        except Exception:
            tel = ''
        if namn:
            return {'namn': namn, 'telefon': tel, 'email': '', 'källa': 'hitta/html'}

    # --- Fallback: regex på rå HTML ---
    tel_hits = _TEL_RE.findall(content)
    if tel_hits:
        return {'namn': '', 'telefon': clean_tel(tel_hits[0]), 'email': '', 'källa': 'hitta/regex'}

    return None


def needs_enrichment(rec: dict) -> bool:
    if rec.get('enriched'):
        return False
    if rec.get('kontakt_telefon') or rec.get('telefon'):
        return False
    return bool(rec.get('adress') or rec.get('ort'))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('json_file')
    ap.add_argument('--limit',   type=int,   default=0,    help='Max antal poster (0=alla)')
    ap.add_argument('--headed',  action='store_true',      help='Visa webbläsarfönstret')
    ap.add_argument('--dry-run', action='store_true',      help='Skriv inte tillbaka till fil')
    ap.add_argument('--delay',   type=float, default=2.0,  help='Baspaus sekunder (default 2.0)')
    args = ap.parse_args()

    p = Path(args.json_file)
    data: dict = json.loads(p.read_text(encoding='utf-8'))
    print(f"Laddade {len(data)} poster från {p.name}")

    to_enrich = [(k, v) for k, v in data.items() if needs_enrichment(v)]
    print(f"Behöver berikas: {len(to_enrich)}")
    if args.limit:
        to_enrich = to_enrich[:args.limit]
        print(f"Kör {len(to_enrich)} poster (--limit {args.limit})")

    found = 0; notfound = 0

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=not args.headed,
            args=['--disable-blink-features=AutomationControlled'],
        )
        ctx = browser.new_context(
            user_agent=(
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/124.0.0.0 Safari/537.36'
            ),
            locale='sv-SE',
            viewport={'width': 1280, 'height': 900},
        )
        # Dölj att det är Playwright
        ctx.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        """)
        page = ctx.new_page()

        # Öppna Hitta.se en gång för att sätta cookies rätt
        try:
            page.goto('https://www.hitta.se', wait_until='domcontentloaded', timeout=15_000)
            human_delay(1.5)
            btn = page.locator('button:has-text("Acceptera"), button:has-text("Godkänn")').first
            if btn.is_visible(timeout=3000):
                btn.click()
                human_delay(1.0)
        except Exception:
            pass

        for i, (key, rec) in enumerate(to_enrich, 1):
            adress = rec.get('adress', '')
            ort    = rec.get('ort', '') or rec.get('kommun', '')
            print(f"  [{i}/{len(to_enrich)}] {key[:38]:<38}  {adress} {ort} ... ",
                  end='', flush=True)

            result = search_hitta(page, adress, ort)

            if result:
                rec['kontakt_namn']    = result['namn']
                rec['kontakt_telefon'] = result['telefon']
                rec['kontakt_email']   = result.get('email', '')
                rec['kontakt_kalla']   = result['källa']
                rec['enriched']        = True
                found += 1
                print(f"✓  {result['namn']}  {result['telefon']}")
            else:
                rec['enriched'] = True
                notfound += 1
                print("—")

            if not args.dry_run:
                p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')

            if i < len(to_enrich):
                time.sleep(args.delay + random.uniform(0, 1.0))

        browser.close()

    pct = f"{found / (found + notfound):.0%}" if (found + notfound) else '—'
    print(f"\n=== Klar ===")
    print(f"  Hittade kontakt: {found}  ({pct})")
    print(f"  Inget hittat:    {notfound}")
    if not args.dry_run:
        print(f"\nSparat: {p.name}")
        print("Kör sedan: python exportEnergyList.py energy-data.json energy-list.xlsx")


if __name__ == '__main__':
    main()
