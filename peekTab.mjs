#!/usr/bin/env node
/**
 * peekTab.mjs — Visar alla kolumner i enspecta.tab med exempelvärden.
 * Usage: node peekTab.mjs enspecta.tab
 */
import { readFileSync } from 'fs';

const [tabPath] = process.argv.slice(2);
if (!tabPath) { console.error('Usage: node peekTab.mjs enspecta.tab'); process.exit(1); }

const lines = readFileSync(tabPath, 'utf-8').split('\n');

// Samla exempelvärden per kolumnindex (max 3 unika, ej tomma)
const examples = {};
let mainRows = 0;

for (const line of lines) {
  const c = line.split('\t');
  if (!c[0]?.trim()) continue; // hoppa över intressent-rader
  mainRows++;
  for (let i = 0; i < c.length; i++) {
    const v = c[i].replace(/[\x00-\x1f]/g, ' ').trim();
    if (!v) continue;
    if (!examples[i]) examples[i] = new Set();
    if (examples[i].size < 3) examples[i].add(v);
  }
  if (mainRows >= 500) break; // 500 rader räcker
}

console.log(`Kolumner med data (av ${Object.keys(examples).length} totalt, från ${mainRows} huvudrader):\n`);
for (const [idx, vals] of Object.entries(examples).sort((a, b) => +a[0] - +b[0])) {
  console.log(`  [${idx.padStart(2)}]  ${[...vals].map(v => v.slice(0, 60)).join('  |  ')}`);
}
