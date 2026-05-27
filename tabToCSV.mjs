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
 * Kända kolumner i enspecta.tab:
 *   0  CaseID      1  Status         2  Besiktningsdatum
 *   5  Adress      6  Postnr         7  Ort             8  Kommun
 *   9  Namn       12  Fastighetsbeteckning             18  Byggår
 *  19  RenoveratÅr 20  Namn2         21  Adress2
 *  22  Email      23  Telefon
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
  // fastig → { köpare: [], intressenter: [], säljare: [], meta: {} }
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
    if (curStatus === 'Köpare')   b.köpare.push(curRecord);
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

      const byggår = clean(c[18]).replace(/^--$/, '');
      const renoveratAr = clean(c[19]).replace(/^--$/, '');

      curRecord = fastig ? {
        caseId:          caseId,
        namn:            clean(c[9]) || clean(c[20]),
        namn2:           clean(c[20]) !== clean(c[9]) ? clean(c[20]) : '',
        adress:          clean(c[5]),
        adress2:         clean(c[21]),
        postnr:          clean(c[6]),
        ort:             clean(c[7]),
        kommun:          clean(c[8]),
        telefon:         clean(c[23]),
        email:           clean(c[22]),
        status:          curStatus,
        besiktningsdatum: clean(c[2]),
        byggår,
        renoveratAr,
        fastig,
      } : null;
      curInts = [];

      const fn = clean(c[24]);
      if (fn) curInts.push({
        namn:    `${fn} ${clean(c[25])}`.trim(),
        telefon: clean(c[27]) || clean(c[28]),
        email:   clean(c[26]),
      });
    } else {
      const fn = clean(c[24]);
      if (fn && curRecord) curInts.push({
        namn:    `${fn} ${clean(c[25])}`.trim(),
        telefon: clean(c[27]) || clean(c[28]),
        email:   clean(c[26]),
      });
    }
  }
  flush();
  return byFastig;
}

function bestContact(b) {
  for (const r of b.köpare)       if (r.telefon || r.email) return { ...r, kontakttyp: 'Köpare' };
  for (const r of b.intressenter) if (r.telefon || r.email) return { ...r, kontakttyp: 'Intressent' };
  for (const r of b.säljare)      if (r.telefon || r.email) return { ...r, kontakttyp: 'Säljare' };
  const fallback = b.köpare[0] ?? b.säljare[0];
  return fallback ? { ...fallback, kontakttyp: fallback.status } : null;
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

  const header = [
    'kontakttyp',
    'namn', 'namn2',
    'adress', 'adress2', 'postnr', 'ort', 'kommun',
    'telefon', 'email',
    'int_namn', 'int_telefon', 'int_email',
    'besiktningsdatum', 'byggår', 'renoveratAr',
    'fastighetsbeteckning', 'caseId',
  ];
  const rows = [header.join(',')];

  let stats = { köpare: 0, intressent: 0, säljare: 0, ingenKontakt: 0, medTel: 0, medEmail: 0 };

  for (const [fastig, b] of byFastig) {
    const best = bestContact(b);
    if (!best) { stats.ingenKontakt++; continue; }

    const typ = best.kontakttyp ?? best.status ?? '';
    if (typ === 'Köpare')      stats.köpare++;
    else if (typ === 'Intressent') stats.intressent++;
    else                           stats.säljare++;
    if (best.telefon) stats.medTel++;
    if (best.email)   stats.medEmail++;

    // First intressent (if different from best contact)
    const int = b.intressenter[0];
    const intNamn  = int?.namn    ?? '';
    const intTel   = int?.telefon ?? '';
    const intEmail = int?.email   ?? '';

    rows.push([
      typ,
      best.namn ?? '', best.namn2 ?? '',
      best.adress ?? '', best.adress2 ?? '',
      best.postnr ?? '', best.ort ?? '', best.kommun ?? '',
      best.telefon ?? '', best.email ?? '',
      intNamn, intTel, intEmail,
      best.besiktningsdatum ?? '', best.byggår ?? '', best.renoveratAr ?? '',
      fastig, best.caseId ?? '',
    ].map(csvVal).join(','));
  }

  const dest = outPath ?? tabPath.replace(/\.tab$/i, '-kontakter.csv');
  writeFileSync(dest, '﻿' + rows.join('\r\n'), 'utf-8');

  console.log(`\n=== Klar ===`);
  console.log(`  Fastigheter totalt:    ${byFastig.size}`);
  console.log(`  Köpare (bäst):         ${stats.köpare}`);
  console.log(`  Intressent (Säljare-besiktning): ${stats.intressent}`);
  console.log(`  Säljare (har flyttat): ${stats.säljare}`);
  console.log(`  Med telefonnummer:     ${stats.medTel}`);
  console.log(`  Med e-post:            ${stats.medEmail}`);
  console.log(`  Ingen kontaktinfo:     ${stats.ingenKontakt}`);
  console.log(`\nSparad till: ${dest}`);
}

main();
