#!/usr/bin/env python3
"""
enrichContacts.py — Berika energy-data.json med kontaktuppgifter via Hitta.se

Söker adress+ort för varje post som saknar telefon/email.
Skriver tillbaka namn, telefon, email (om hittat) till JSON-posten.

Usage:
    python enrichContacts.py energy-data.json [--limit 100] [--dry-run]

Kräver:
    pip install requests beautifulsoup4 lxml

Tips: kör --limit 50 första gången för att testa träffkvaliteten.
"""
import sys
import json
import time
import re
import argparse
from pathlib import Path

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("Kör: pip install requests beautifulsoup4 lxml")
    sys.exit(1)

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept-Language': 'sv-SE,sv;q=0.9',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)

_TEL_RE = re.compile(r'(\+46|0)[\d\s\-]{6,12}')


def clean_tel(s: str) -> str:
    s = re.sub(r'\s+', '', s.strip())
    if s.startswith('+46'):
        s = '0' + s[3:]
    return s


def search_hitta(adress: str, ort: str) -> dict | None:
    """
    Söker Hitta.se på adress+ort och returnerar första personträffen:
    {'namn': ..., 'telefon': ..., 'email': ..., 'hitta_url': ...}
    Returnerar None om inget hittas.
    """
    q = f"{adress} {ort}".strip()
    if not q:
        return None

    url = f"https://www.hitta.se/sök?vad=&var={requests.utils.quote(q)}"

    try:
        resp = SESSION.get(url, timeout=12, allow_redirects=True)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"    HTTP-fel: {e}")
        return None

    soup = BeautifulSoup(resp.text, 'lxml')

    # Hitta.se renderar personsökning i JSON-LD eller i specifika element.
    # Försök JSON-LD först (mest stabilt).
    for tag in soup.find_all('script', type='application/ld+json'):
        try:
            obj = json.loads(tag.string or '')
            items = obj if isinstance(obj, list) else [obj]
            for item in items:
                if item.get('@type') in ('Person', 'LocalBusiness'):
                    namn   = item.get('name', '')
                    tel    = ''
                    email  = item.get('email', '')
                    tel_raw = item.get('telephone', '')
                    if tel_raw:
                        tel = clean_tel(tel_raw)
                    if namn:
                        return {'namn': namn, 'telefon': tel, 'email': email,
                                'hitta_url': url, 'källa': 'hitta.se/json-ld'}
        except (json.JSONDecodeError, AttributeError):
            pass

    # Fallback: scrapa HTML-element för personkort
    for card in soup.select('[class*="person"], [class*="Person"], [data-testid*="person"]'):
        namn_el = card.select_one('[class*="name"], [class*="Name"], h2, h3')
        tel_el  = card.select_one('[class*="phone"], [class*="Phone"], [href^="tel:"]')
        if namn_el:
            namn = namn_el.get_text(strip=True)
            tel  = ''
            if tel_el:
                href = tel_el.get('href', '')
                tel  = clean_tel(href.replace('tel:', '') if href.startswith('tel:')
                                 else tel_el.get_text(strip=True))
            if namn and len(namn) > 3:
                return {'namn': namn, 'telefon': tel, 'email': '',
                        'hitta_url': url, 'källa': 'hitta.se/html'}

    # Sista fallback: leta telefonnummer i rå HTML
    matches = _TEL_RE.findall(resp.text)
    if matches:
        tel = clean_tel(matches[0])
        return {'namn': '', 'telefon': tel, 'email': '',
                'hitta_url': url, 'källa': 'hitta.se/regex'}

    return None


def needs_enrichment(rec: dict) -> bool:
    """Sant om posten saknar telefon och inte redan berikats."""
    if rec.get('enriched'):
        return False
    if rec.get('kontakt_telefon') or rec.get('telefon'):
        return False
    return bool(rec.get('adress') or rec.get('ort'))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('json_file')
    ap.add_argument('--limit',   type=int, default=0,     help='Max antal att söka (0 = alla)')
    ap.add_argument('--dry-run', action='store_true',     help='Skriv inte tillbaka till fil')
    ap.add_argument('--delay',   type=float, default=1.5, help='Sekunder mellan anrop (default 1.5)')
    args = ap.parse_args()

    p = Path(args.json_file)
    data: dict = json.loads(p.read_text(encoding='utf-8'))
    print(f"Laddade {len(data)} poster från {p.name}")

    to_enrich = [(k, v) for k, v in data.items() if needs_enrichment(v)]
    print(f"Behöver berikas: {len(to_enrich)}")

    if args.limit:
        to_enrich = to_enrich[:args.limit]
        print(f"Begränsat till: {len(to_enrich)}")

    found = 0; notfound = 0; errors = 0

    for i, (key, rec) in enumerate(to_enrich, 1):
        adress = rec.get('adress', '')
        ort    = rec.get('ort', '') or rec.get('kommun', '')
        print(f"  [{i}/{len(to_enrich)}] {key[:40]:<40}  {adress} {ort} ... ", end='', flush=True)

        result = search_hitta(adress, ort)

        if result:
            rec['kontakt_namn']    = result['namn']
            rec['kontakt_telefon'] = result['telefon']
            rec['kontakt_email']   = result['email']
            rec['kontakt_url']     = result['hitta_url']
            rec['kontakt_kalla']   = result['källa']
            rec['enriched']        = True
            found += 1
            print(f"✓  {result['namn']}  {result['telefon']}")
        else:
            rec['enriched'] = True  # markera som försökt
            notfound += 1
            print("—")

        if not args.dry_run:
            p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')

        if i < len(to_enrich):
            time.sleep(args.delay)

    print(f"\n=== Klar ===")
    print(f"  Hittade kontakt:  {found}")
    print(f"  Inget hittat:     {notfound}")
    print(f"  Fel:              {errors}")
    print(f"  Täckning:         {found/(found+notfound):.0%}" if (found+notfound) else '')
    if not args.dry_run:
        print(f"\nSparat till {p.name}")
        print("Kör sedan: python exportEnergyList.py energy-data.json energy-list.xlsx")


if __name__ == '__main__':
    main()
