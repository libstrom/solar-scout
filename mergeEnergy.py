#!/usr/bin/env python3
"""
mergeEnergy.py — Slår ihop flera energy-JSON-filer till ett index.

XLSM-data har företräde framför PDF-data (mer strukturerad).

Usage:
  python mergeEnergy.py xlsm.json pdf.json [extra.json ...] energy-data.json
"""
import sys, json
from pathlib import Path


def norm(s: str) -> str:
    if not s:
        return ''
    return str(s).lower().replace(r'\s+', ' ').strip()


def main():
    args = sys.argv[1:]
    if len(args) < 2:
        print('Usage: python mergeEnergy.py file1.json [file2.json ...] output.json')
        sys.exit(1)

    *inputs, output = args

    # Load in order: last file wins unless later overridden by XLSM priority below.
    # Strategy: PDF first, then XLSM merges on top (XLSM wins on conflict).
    merged: dict = {}

    for path in inputs:
        p = Path(path)
        if not p.exists():
            print(f'Varning: {path} saknas — hoppar över')
            continue
        data = json.loads(p.read_text(encoding='utf-8'))
        before = len(merged)
        for key, val in data.items():
            k = key.lower().strip()
            if k not in merged:
                merged[k] = val
            else:
                # XLSM source wins (has energiklass field from structured XML)
                existing_has_ek = bool((merged[k].get('energiklass') or '').strip())
                new_has_ek      = bool((val.get('energiklass') or '').strip())
                if new_has_ek and not existing_has_ek:
                    merged[k] = val   # replace PDF stub with XLSM record
                # else keep existing (either both have it, or new lacks it)
        print(f'{path}: {len(data)} poster  →  totalt {len(merged)} unika (+{len(merged)-before} nya)')

    Path(output).write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding='utf-8')
    print(f'\nKlar: {len(merged)} fastigheter sparade till {output}')


if __name__ == '__main__':
    main()
