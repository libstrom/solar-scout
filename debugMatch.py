#!/usr/bin/env python3
"""
debugMatch.py — Visar varför energy-data.json inte matchar enspecta.tab

Usage:
  python debugMatch.py energy-data.json enspecta.tab
"""
import sys, json, re
from pathlib import Path

def norm(s):
    if not s: return ''
    s = str(s).lower().replace('_', ' ').replace(':', ' ')
    return re.sub(r'\s+', ' ', s).strip(' _-.')

def norm_tab(s):
    # Same as makeLeads.py norm
    if not s: return ''
    s = str(s).lower().replace('_', ' ').replace(':', ' ')
    return re.sub(r'\s+', ' ', s).strip(' _-.')

args = sys.argv[1:]
energy_path = args[0] if args else 'energy-data.json'
tab_path    = args[1] if len(args) > 1 else 'enspecta.tab'

energy = json.loads(Path(energy_path).read_text(encoding='utf-8'))
print(f"energy-data.json: {len(energy)} nycklar")
print("\nFörsta 20 nycklar i energy-data.json:")
for k in list(energy.keys())[:20]:
    print(f"  {repr(k)}")

print("\n\nLäser enspecta.tab col 12 (fastighetsbeteckning) ...")
tab_keys = set()
tab_samples = []
with open(tab_path, encoding='utf-8', errors='replace') as f:
    for line in f:
        c = line.split('\t')
        if c and c[0].strip():  # huvud-rad (har case_id)
            raw = c[12].strip() if len(c) > 12 else ''
            if raw:
                k = norm_tab(raw)
                tab_keys.add(k)
                if len(tab_samples) < 20:
                    tab_samples.append((raw, k))

print(f"Unika fastigheter i enspecta.tab: {len(tab_keys)}")
print("\nFörsta 20 fastighetsbeteckningar i enspecta.tab (råvärde → normaliserat):")
for raw, k in tab_samples:
    print(f"  {repr(raw):40s} → {repr(k)}")

# Check overlap
energy_keys = set(energy.keys())
matched = energy_keys & tab_keys
print(f"\n\nÖverlapp: {len(matched)} / {len(energy_keys)} energy-nycklar finns i enspecta.tab")

if matched:
    print("\nExempel på MATCHANDE nycklar:")
    for k in list(matched)[:10]:
        print(f"  {repr(k)}")

# Find near-misses
print("\nExempel på energy-nycklar som INTE matchar (första 20):")
no_match = [k for k in energy_keys if k not in tab_keys]
for k in no_match[:20]:
    # Find closest tab key (first token match)
    first_word = k.split()[0] if k.split() else ''
    candidates = [t for t in tab_keys if t.startswith(first_word)]
    hint = candidates[:2] if candidates else ['(ingen träff på första token)']
    print(f"  energy: {repr(k)}")
    if candidates:
        print(f"    tab-kandidater: {[repr(h) for h in hint]}")
