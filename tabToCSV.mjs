#!/usr/bin/env node
/**
 * tabToCSV.mjs
 *
 * Läser enspecta.tab och matar ut en CSV med bästa kontaktperson per fastighet.
 * Priority: Köpare > Intressent (från Säljare-besiktning) > Säljare
 *
 * Usage:
 *   node tabToCSV.mjs enspecta.tab [output.csv]
 *
 * Columns in enspecta.tab:
 *   0  CaseID    1  Status    5  Adress    6  Postnr    7  Ort
 *   9  Namn     12  Fastighet 20 Namn2    22  Email    23  Telefon
 *  24  IntFörnamn  25 IntEfternamn  26 IntEmail  27 IntTel1  28 IntTel2
 */

import { readFileSync, writeFileSync } from 'fs';

function clean(s) {
  if (!s) return '';
  return s.replace(/[\x00-\x1f]/g, ' ').replace(/\s+/g, ' ').trim();
}

function normFastig(s) {
  return clean(s).toLowerCase().replace(/\s+/g, ' ');
}

function csvVal(v) {
  const s = String(v ?? '');
  if (s.includes(',') || s.includes('"') || s.includes('\n')) {
    return '"' + s.replace(/"/g, '""') + '"';
  }
  return s;
}

function parseTab(tabPath) {
  const lines = readFileSync(tabPath, 'utf-8').split('\n');
  // fastig → { köpare: [], intressenter: [], säljare: [] }
  const byFastig = new Map();

  let curFastig = null;
  let curStatus = null;
  let curRecord = null;
  let curInts   = [];

  function flush() {
    if (!curFastig || !curRecord) return;
    if (!byFastig.has(curFastig)) {
      byFastig.set(curFastig, { köpare: [], intressenter: [], säljare: [] });
    }
    const b = byFastig.get(curFastig);
    if (curStatus === 'Köpare')  b.köpare.push(curRecord);
    else if (curStatus === 'Säljare') b.säljare.push(curRecord);
    if (curStatus === 'Säljare' && curInts.length > 0) {
      b.intressenter.push(...curInts);
    }
  }

  for (const line of lines) {
    const c = line.split('\t');
    const caseId = clean(c[0]);

    if (caseId) {
      flush();
      const fastig = normFastig(c[12]);
      curFastig = fastig || null;
      curStatus = clean(c[1]);
      curRecord = fastig ? {
        namn:    clean(c[9]) || clean(c[20]),
        adress:  clean(c[5]),
        postnr:  clean(c[6]),
        ort:     clean(c[7]),
        telefon: clean(c[23]),
        email:   clean(c[22]),
        status:  curStatus,
        fastig,
      } : null;
      curInts = [];
      const fn = clean(c[24]);
      if (fn) curInts.push({
        namn:    `${fn} ${clean(c[25])}`.trim(),
        adress:  curRecord?.adress ?? '',
        postnr:  curRecord?.postnr ?? '',
        ort:     curRecord?.ort ?? '',
        telefon: clean(c[27]) || clean(c[28]),
        email:   clean(c[26]),
        status:  'Intressent',
        fastig:  curFastig ?? '',
      });
    } else {
      const fn = clean(c[24]);
      if (fn && curRecord) curInts.push({
        namn:    `${fn} ${clean(c[25])}`.trim(),
        adress:  curRecord.adress,
        postnr:  curRecord.postnr,
        ort:     curRecord.ort,
        telefon: clean(c[27]) || clean(c[28]),
        email:   clean(c[26]),
        status:  'Intressent',
        fastig:  curFastig ?? '',
      });
    }
  }
  flush();
  return byFastig;
}

function bestContact(b) {
  for (const r of b.köpare)      if (r.telefon || r.email) return r;
  for (const r of b.intressenter) if (r.telefon || r.email) return r;
  for (const r of b.säljare)     if (r.telefon || r.email) return r;
  // fallback: any record even without contact info
  return b.köpare[0] ?? b.intressenter[0] ?? b.säljare[0] ?? null;
}

function main() {
  const [tabPath, outPath] = process.argv.slice(2);
  if (!tabPath) {
    console.error('Usage: node tabToCSV.mjs enspecta.tab [output.csv]');
    process.exit(1);
  }

  console.log('Läser', tabPath, '...');
  const byFastig = parseTab(tabPath);
  console.log(`  ${byFastig.size} unika fastigheter`);

  const header = ['namn', 'adress', 'postnr', 'ort', 'telefon', 'email', 'status', 'fastighetsbeteckning'];
  const rows = [header.join(',')];
  let medTelefon = 0, medEmail = 0, ingenKontakt = 0;

  for (const [fastig, b] of byFastig) {
    const r = bestContact(b);
    if (!r) { ingenKontakt++; continue; }
    if (r.telefon) medTelefon++;
    if (r.email)   medEmail++;
    rows.push([r.namn, r.adress, r.postnr, r.ort, r.telefon, r.email, r.status, fastig]
      .map(csvVal).join(','));
  }

  const dest = outPath ?? tabPath.replace(/\.tab$/i, '-kontakter.csv');
  writeFileSync(dest, '﻿' + rows.join('\r\n'), 'utf-8'); // BOM för Excel

  console.log(`\n=== Klar ===`);
  console.log(`  Fastigheter totalt:  ${byFastig.size}`);
  console.log(`  Med telefonnummer:   ${medTelefon}`);
  console.log(`  Med e-post:          ${medEmail}`);
  console.log(`  Ingen kontaktinfo:   ${ingenKontakt}`);
  console.log(`\nSparad till: ${dest}`);
  console.log('Öppna i Excel — välj UTF-8 med BOM om tecken ser konstiga ut.');
}

main();
