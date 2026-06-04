#!/usr/bin/env node
/**
 * batchXlsm.mjs — Batch-parsear en mapp med Energivision XLSM-filer
 *
 * Läser varje .xlsm rekursivt, extraherar energidata via xlsm.mjs,
 * och sparar ett JSON-index indexerat på fastighetsbeteckning.
 *
 * Usage:
 *   node batchXlsm.mjs <mapp-med-xlsm> [output.json]
 *
 * Output: energy-data.json — objekt keyed på normaliserad fastighetsbeteckning
 *   {
 *     "lund husie 12:34": {
 *       energiklass, energiprestanda_kwh, total_el_kwh, el_uppvarmning_kwh,
 *       har_solceller, har_solvarme, uppvarmningssystem, atemp_m2,
 *       nybyggnadsar, atgardsforslag, ...
 *     }, ...
 *   }
 *
 * Används sedan av makeLeads.py (--energy energy-data.json) för
 * exakt scoring baserad på verklig energiklass och elförbrukning.
 */

import { readdir, stat } from 'node:fs/promises';
import { writeFileSync }  from 'node:fs';
import { join, extname } from 'node:path';
import { extractXlsmFields } from './xlsm.mjs';

function norm(s) {
  if (!s) return '';
  return String(s).toLowerCase()
    .replace(/[_:]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/[\s_.\\-]+$/, '');
}

async function findXlsm(dir, results = []) {
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) {
      await findXlsm(full, results);
    } else if (entry.isFile() && extname(entry.name).toLowerCase() === '.xlsm') {
      results.push(full);
    }
  }
  return results;
}

async function main() {
  const [inputDir, outPath] = process.argv.slice(2);
  if (!inputDir) {
    console.error('Usage: node batchXlsm.mjs <mapp-med-xlsm> [output.json]');
    process.exit(1);
  }

  const out = outPath || 'energy-data.json';

  console.log(`Söker efter .xlsm filer i: ${inputDir}`);
  const files = await findXlsm(inputDir);
  console.log(`  Hittade ${files.length} filer\n`);

  const index = {};
  let ok = 0, skipped = 0, errors = 0;

  for (let i = 0; i < files.length; i++) {
    const f = files[i];
    if (i > 0 && i % 100 === 0) {
      process.stdout.write(`  ${i}/${files.length} (ok=${ok} skip=${skipped} err=${errors})\n`);
    }
    try {
      const data = await extractXlsmFields(f);
      if (!data) { skipped++; continue; }

      const key = norm(data.fastighetsbeteckning);
      if (!key) { skipped++; continue; }

      // Keep whichever record has the more recent declaration date
      const existing = index[key];
      if (existing && existing.deklaration_datum && data.deklaration_datum) {
        if (existing.deklaration_datum >= data.deklaration_datum) { ok++; continue; }
      }
      index[key] = data;
      ok++;
    } catch (e) {
      errors++;
      if (process.env.VERBOSE) console.error(`  ERR ${f}: ${e.message}`);
    }
  }

  const count = Object.keys(index).length;
  writeFileSync(out, JSON.stringify(index, null, 2));

  console.log(`\n=== Klar ===`);
  console.log(`  Filer lästa:         ${files.length}`);
  console.log(`  Unika fastigheter:   ${count}`);
  console.log(`  Skippade (null/ej):  ${skipped}`);
  console.log(`  Fel:                 ${errors}`);
  console.log(`\nSparad till: ${out}`);
  console.log('Kör sedan: python makeLeads.py enspecta.tab leads.xlsx --energy energy-data.json');
}

main().catch(e => { console.error(e); process.exit(1); });
