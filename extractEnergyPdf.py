#!/usr/bin/env python3
"""
extractEnergyPdf.py — Extraherar energidata från Energivision PDF-deklarationer

Läser alla PDF-filer rekursivt, extraherar nyckelfält och sparar
energy-data.json i exakt samma format som batchXlsm.mjs output.
Integreras sedan med makeLeads.py via --energy flaggan.

Usage:
    python extractEnergyPdf.py <mapp-med-pdf> [output.json]

Kräver:
    pip install pypdf

Alternativt (om pypdf ej fungerar):
    pip install pdfminer.six
"""

import sys, re, json
from pathlib import Path

try:
    from pypdf import PdfReader as _Reader
    def _extract_text(path):
        r = _Reader(str(path))
        return '\n'.join(page.extract_text() or '' for page in r.pages)
except ImportError:
    try:
        from pdfminer.high_level import extract_text as _pdfminer_extract
        def _extract_text(path):
            return _pdfminer_extract(str(path))
    except ImportError:
        print("Kör: pip install pypdf")
        sys.exit(1)


_WS = re.compile(r'\s+')

def norm(s):
    if not s: return ''
    return _WS.sub(' ', str(s)).lower().strip()

def to_int(s):
    if s is None: return None
    m = re.search(r'\d[\d\s]*', str(s))
    if not m: return None
    try: return int(m.group().replace(' ', ''))
    except ValueError: return None

# Energy class from ratio to new-build requirement (same formula as xlsm.mjs)
def classify_energy(prestanda, krav):
    if not prestanda or not krav or krav == 0: return None
    r = prestanda / krav
    if r <= 0.50: return 'A'
    if r <= 0.75: return 'B'
    if r <= 1.00: return 'C'
    if r <= 1.35: return 'D'
    if r <= 1.80: return 'E'
    if r <= 2.35: return 'F'
    return 'G'

# Strip the radio-button rendering garbage from PDF text (nmlkji, gfedcb patterns)
_RADIO = re.compile(r'\b(?:n\s*m\s*l\s*k\s*j(?:\s*i)?|g\s*f\s*e\s*d\s*c(?:\s*b)?)\b', re.I)

def clean(txt):
    txt = txt.replace('²', '2')   # m² → m2
    txt = _RADIO.sub(' ', txt)
    return _WS.sub(' ', txt).strip()


def extract_from_text(raw):
    txt = clean(raw)

    # ── Fastighetsbeteckning ─────────────────────────────────────────────────
    # "Fastighetsbeteckning (anges utan kommunnamn) Härfågeln 1"
    m = re.search(
        r'Fastighetsbeteckning\s*(?:\([^)]{0,60}\))?\s+([A-ZÅÄÖ][^,\n]{1,60}?)(?=\s{2,}|\s+Egen\b|\s+Husnummer\b|\s+Prefix\b|\s+Byggnads|\s+Orsak\b)',
        txt, re.I
    )
    fastbet = m.group(1).strip() if m else None

    # ── Nybyggnadsår ─────────────────────────────────────────────────────────
    m = re.search(r'Nybyggnads[aå]r\s+(\d{4})', txt)
    nybyggnadsar = int(m.group(1)) if m else None

    # ── Atemp ────────────────────────────────────────────────────────────────
    # "Atemp (exkl. Avarmgarage) ... Mätt värde 177 m 2"
    m = re.search(r'Atemp\b[\s\S]{0,120}?M[äa]tt\s+v[äa]rde\s+([\d]+)\s+m\s*2', txt, re.I)
    atemp_m2 = to_int(m.group(1)) if m else None

    # ── Energiprestanda (from the customer-facing summary paragraph) ─────────
    # "Detta hus använder 176 kWh/m2 och år, varav el 0 kWh/m2."
    m = re.search(r'[Dd]etta hus använder\s+([\d]+)\s+kWh', txt)
    energiprestanda = int(m.group(1)) if m else None

    # varav el kWh/m2
    m = re.search(r'varav el\s+([\d]+)\s+kWh', txt)
    varav_el_kwh_m2 = int(m.group(1)) if m else None

    # ── Nybyggnadskrav (referensvärde) ────────────────────────────────────────
    # "Liknande hus 123 – 151 kWh/m2 och år, nya hus 110 kWh/m2."
    m = re.search(r'nya hus\s+([\d]+)\s+kWh', txt)
    krav_nybyggnad = int(m.group(1)) if m else None

    # ── Solceller ────────────────────────────────────────────────────────────
    m = re.search(r'[Ff]inns solcells(?:system)?\?.*?(\d+)\s+m\s*2', txt, re.DOTALL)
    if not m:
        m = re.search(r'solcellsarea\s+([\d]+)\s+m', txt, re.I)
    har_solceller = bool(m and int(m.group(1)) > 0)

    # Solceller: check for "Ja" response variant
    if not har_solceller:
        m = re.search(r'[Ff]inns solcells(?:system)?\?\s+([Jj]a)\b', txt)
        har_solceller = bool(m)

    # ── Solvärme ─────────────────────────────────────────────────────────────
    m = re.search(r'[Ff]inns solv[äa]rme\?.*?(\d+)\s+m\s*2', txt, re.DOTALL)
    if not m:
        m = re.search(r'solfångararea\s+([\d]+)\s+m', txt, re.I)
    har_solvarme = bool(m and int(m.group(1)) > 0)

    if not har_solvarme:
        m = re.search(r'[Ff]inns solv[äa]rme\?\s+([Jj]a)\b', txt)
        har_solvarme = bool(m)

    # ── Deklarations-ID ───────────────────────────────────────────────────────
    # Skip version number like "2.0" before the actual 5–7-digit ID
    m = re.search(r'(?:Dekl\.id:|Energideklarations-ID:?|Diarienummer:?)\s*(?:\d+\.\d+\s+)?(\d{5,7})\b', txt, re.I)
    dekl_id = m.group(1) if m else None

    # ── Deklarationsdatum ─────────────────────────────────────────────────────
    m = re.search(r'[Dd]atum\s+f[öo]r\s+godk[äa]nnande\s+(\d{4}-\d{2}-\d{2})', txt)
    if not m:
        m = re.search(r'energideklaration\s+utf[öo]r[dl]\s+(\d{4}-\d{2}-\d{2})', txt, re.I)
    deklaration_datum = m.group(1) if m else None

    # ── Energiklass (computed) ────────────────────────────────────────────────
    energiklass = classify_energy(energiprestanda, krav_nybyggnad)

    # Estimate total el kWh from varav_el_kwh_m2 × atemp_m2
    total_el_kwh = (varav_el_kwh_m2 * atemp_m2) if (varav_el_kwh_m2 and atemp_m2) else None

    return {
        'energideklarations_id': dekl_id,
        'fastighetsbeteckning':  fastbet,
        'nybyggnadsar':          nybyggnadsar,
        'atemp_m2':              atemp_m2,
        'energiprestanda_kwh':   energiprestanda,
        'krav_nybyggnad_kwh':    krav_nybyggnad,
        'energiklass':           energiklass,
        'varav_el_kwh_m2':       varav_el_kwh_m2,
        'el_uppvarmning_kwh':    total_el_kwh,
        'total_el_kwh':          total_el_kwh,
        'har_solceller':         har_solceller,
        'har_solvarme':          har_solvarme,
        'deklaration_datum':     deklaration_datum,
        'energi_per_kalla':      {},   # not reliable from PDF text dumps
        'atgardsforslag':        None,
    }


def extract_from_pdf(path):
    try:
        text = _extract_text(path)
        if not text or len(text.strip()) < 50:
            return None
        return extract_from_text(text)
    except Exception:
        return None


def fastbet_from_filename(path):
    """Fallback: parse fastighetsbeteckning from filename like Energideklaration_Härfågeln_1.pdf"""
    stem = Path(path).stem
    stem = re.sub(r'^[Ee]nergideklaration_?', '', stem)
    return stem.replace('_', ' ').strip()


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)

    input_dir = Path(args[0])
    out_path  = args[1] if len(args) > 1 else 'energy-data.json'

    if not input_dir.exists():
        print(f"Fel: mappen finns inte: {input_dir}")
        sys.exit(1)

    print(f"Söker PDF-filer i: {input_dir}")
    pdf_files = sorted(list(input_dir.rglob('*.[Pp][Dd][Ff]')))
    print(f"  Hittade {len(pdf_files)} PDF-filer\n")

    index = {}
    ok = skipped = errors = 0

    for i, f in enumerate(pdf_files):
        if i > 0 and i % 100 == 0:
            print(f"  {i}/{len(pdf_files)}  ok={ok}  skip={skipped}  err={errors}")

        try:
            data = extract_from_pdf(f)
            if not data:
                skipped += 1
                continue

            fastbet = data.get('fastighetsbeteckning') or fastbet_from_filename(f)
            data['fastighetsbeteckning'] = fastbet
            key = norm(fastbet)
            if not key:
                skipped += 1
                continue

            existing = index.get(key)
            if (existing
                    and existing.get('deklaration_datum')
                    and data.get('deklaration_datum')
                    and existing['deklaration_datum'] >= data['deklaration_datum']):
                ok += 1
                continue

            index[key] = data
            ok += 1
        except Exception as e:
            errors += 1
            if __import__('os').environ.get('VERBOSE'):
                print(f"  ERR {f}: {e}")

    count = len(index)
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(index, f, ensure_ascii=False, indent=2)

    print(f"\n=== Klar ===")
    print(f"  PDF-filer:           {len(pdf_files)}")
    print(f"  Unika fastigheter:   {count}")
    print(f"  Skippade:            {skipped}")
    print(f"  Fel (korrupt/lösen): {errors}")
    print(f"\nSparad till: {out_path}")
    print("Kör sedan: python makeLeads.py enspecta.tab leads.xlsx --energy energy-data.json")


if __name__ == '__main__':
    main()
