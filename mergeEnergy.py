#!/usr/bin/env python3
"""
mergeEnergy.py — Slår ihop flera energy-JSON-filer till ett index.

Fältvis merge: XLSM vinner per fält, PDF fyller luckor.
Inga fält går förlorade — varje källa bidrar med det den är bäst på.

XLSM är bäst på: adress, postnummer, ort, kommun, energi_per_kalla,
                  uppvarmningssystem, atgardsforslag, energiklass (direkt ur cell)
PDF är bäst på:  varav_el_kwh_m2, ibland deklaration_datum

Usage:
  python mergeEnergy.py xlsm.json pdf.json [extra.json ...] energy-data.json
"""
import sys, json, re
from pathlib import Path


def norm(s: str) -> str:
    if not s:
        return ''
    s = str(s).lower().replace('_', ' ').replace(':', ' ')
    return re.sub(r'\s+', ' ', s).strip(' _-.')


_JUNK_PREFIX = re.compile(
    r'^(faktura|area|unik\s+identifikation|sustend|underhållsplan|'
    r'fastighetsbeteckning|energideklaration|brf\s|ab\s)',
    re.I
)
_ONLY_DIGITS = re.compile(r'^\d+$')
_HAS_LETTER  = re.compile(r'[a-zåäö]', re.I)

def is_valid_fastig(key: str) -> bool:
    """Reject garbage keys: pure numbers, label text, invoice prefixes etc."""
    if not key or len(key) < 3:
        return False
    if _ONLY_DIGITS.match(key):
        return False
    if not _HAS_LETTER.search(key):
        return False
    if _JUNK_PREFIX.match(key):
        return False
    return True


def is_empty(v) -> bool:
    if v is None:
        return True
    if isinstance(v, str) and not v.strip():
        return True
    if isinstance(v, dict) and not any(v.values()):
        return True
    return False


def merge_records(base: dict, overlay: dict) -> dict:
    """overlay wins per field — but only if it has a non-empty value."""
    result = dict(base)
    for k, v in overlay.items():
        if not is_empty(v):
            result[k] = v
    return result


def priority_score(rec: dict) -> int:
    score = 0
    if rec.get('source') == 'xlsm':
        score += 100
    if not is_empty(rec.get('energiklass')):
        score += 10
    if not is_empty(rec.get('energi_per_kalla')):
        score += 5
    if not is_empty(rec.get('atgardsforslag')):
        score += 3
    if not is_empty(rec.get('adress')):
        score += 2
    return score


def main():
    args = sys.argv[1:]
    if len(args) < 2:
        print('Usage: python mergeEnergy.py file1.json [file2.json ...] output.json')
        sys.exit(1)

    *inputs, output = args

    all_data: list[dict] = []
    for path in inputs:
        p = Path(path)
        if not p.exists():
            print(f'Varning: {path} saknas — hoppar över')
            continue
        data = json.loads(p.read_text(encoding='utf-8'))
        all_data.append(data)
        print(f'{path}: {len(data)} poster')

    if not all_data:
        print('Inga filer laddades — avbryter')
        sys.exit(1)

    # Re-index each source with normed keys (raw keys may have _ or : from batchXlsm)
    normed_data: list[dict] = []
    for data in all_data:
        nd = {}
        for k, v in data.items():
            nk = norm(k)
            if nk and is_valid_fastig(nk) and nk not in nd:
                nd[nk] = v
        normed_data.append(nd)

    all_keys: set[str] = set()
    for nd in normed_data:
        all_keys.update(nd.keys())

    merged: dict[str, dict] = {}
    for key in all_keys:
        records = []
        for nd in normed_data:
            rec = nd.get(key)
            if rec:
                records.append(rec)

        if not records:
            continue

        records.sort(key=priority_score, reverse=True)

        result = records[0]
        for rec in records[1:]:
            result = merge_records(rec, result)  # overlay=result (higher prio) wins

        merged[key] = result

    xlsm_count = sum(1 for r in merged.values() if r.get('source') == 'xlsm')
    pdf_only   = sum(1 for r in merged.values() if r.get('source') != 'xlsm')
    has_epk    = sum(1 for r in merged.values() if not is_empty(r.get('energi_per_kalla')))
    has_adr    = sum(1 for r in merged.values() if not is_empty(r.get('adress')))
    has_ek     = sum(1 for r in merged.values() if not is_empty(r.get('energiklass')))
    has_atg    = sum(1 for r in merged.values() if not is_empty(r.get('atgardsforslag')))

    Path(output).write_text(
        json.dumps(merged, indent=2, ensure_ascii=False), encoding='utf-8'
    )

    print(f'\n=== Klar: {len(merged)} unika fastigheter → {output} ===')
    print(f'  XLSM-poster (rik data):  {xlsm_count}')
    print(f'  Bara PDF:                {pdf_only}')
    print(f'  Har energiklass:         {has_ek}')
    print(f'  Har energi_per_källa:    {has_epk}')
    print(f'  Har adress:              {has_adr}')
    print(f'  Har åtgärdsförslag:      {has_atg}')


if __name__ == '__main__':
    main()
