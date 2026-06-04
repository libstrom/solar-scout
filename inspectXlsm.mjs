#!/usr/bin/env node
/**
 * inspectXlsm.mjs  —  Debug a single Energivision XLSM energy declaration.
 *
 * Usage:
 *   node inspectXlsm.mjs "path/to/file.xlsm"
 *
 * Output:
 *   - Sheet names found in the workbook
 *   - All cells whose value contains any debug keyword
 *     (fastighetsbeteckning, beteckning, fastighet, adress, energi, klass, atemp, solcell)
 *   - For each matching cell: ref, label text, adjacent cells on same row and row below
 *   - Full JSON result from extractXlsmFields
 */

import { readFile }       from 'node:fs/promises';
import { inflateRawSync } from 'node:zlib';
import { extractXlsmFields } from './xlsm.mjs';

// ── In-memory ZIP reader (mirrors ZipReader in xlsm.mjs) ─────────────────────

class ZipReader {
  constructor(buf) {
    this.buf = buf;
    this.entries = this._readCentralDir();
  }
  _readCentralDir() {
    const b = this.buf;
    let eocd = -1;
    for (let i = b.length - 22; i >= 0; i--) {
      if (b[i] === 0x50 && b[i+1] === 0x4b && b[i+2] === 0x05 && b[i+3] === 0x06) { eocd = i; break; }
    }
    if (eocd === -1) throw new Error('not a ZIP');
    const count    = b.readUInt16LE(eocd + 10);
    const cdOffset = b.readUInt32LE(eocd + 16);
    const entries  = new Map();
    let off = cdOffset;
    for (let i = 0; i < count; i++) {
      if (b.readUInt32LE(off) !== 0x02014b50) break;
      const method   = b.readUInt16LE(off + 10);
      const compSz   = b.readUInt32LE(off + 20);
      const uncompSz = b.readUInt32LE(off + 24);
      const nameLen  = b.readUInt16LE(off + 28);
      const extraLen = b.readUInt16LE(off + 30);
      const commLen  = b.readUInt16LE(off + 32);
      const locOff   = b.readUInt32LE(off + 42);
      const name     = b.toString('utf8', off + 46, off + 46 + nameLen);
      entries.set(name, { method, compSz, uncompSz, locOff });
      off += 46 + nameLen + extraLen + commLen;
    }
    return entries;
  }
  read(name) {
    const e = this.entries.get(name);
    if (!e) return null;
    const b = this.buf;
    if (b.readUInt32LE(e.locOff) !== 0x04034b50) return null;
    const nameLen  = b.readUInt16LE(e.locOff + 26);
    const extraLen = b.readUInt16LE(e.locOff + 28);
    const dataOff  = e.locOff + 30 + nameLen + extraLen;
    const data     = b.subarray(dataOff, dataOff + e.compSz);
    if (e.method === 0) return data.toString('utf8');
    if (e.method === 8) return inflateRawSync(data).toString('utf8');
    return null;
  }
  list(prefix = '') { return [...this.entries.keys()].filter(k => k.startsWith(prefix)); }
}

// ── Shared XML helpers ────────────────────────────────────────────────────────

function decodeXmlEntities(s) {
  return s
    .replace(/&amp;/g,  '&')
    .replace(/&lt;/g,   '<')
    .replace(/&gt;/g,   '>')
    .replace(/&quot;/g, '"')
    .replace(/&apos;/g, "'")
    .replace(/&#(\d+);/g,  (_, n) => String.fromCharCode(+n))
    .replace(/&#x([0-9a-f]+);/gi, (_, h) => String.fromCharCode(parseInt(h, 16)));
}

function parseSharedStrings(xml) {
  const strings = [];
  const siRe  = /<si\b[^>]*>([\s\S]*?)<\/si>/g;
  const tRe   = /<t(?:\s[^>]*)?>([^<]*)<\/t>/g;
  let siM;
  while ((siM = siRe.exec(xml)) !== null) {
    let text = '';
    let tM;
    tRe.lastIndex = 0;
    while ((tM = tRe.exec(siM[1])) !== null) text += tM[1];
    strings.push(decodeXmlEntities(text));
  }
  return strings;
}

function toNum(v) {
  if (v == null) return null;
  const n = parseFloat(String(v).replace(/\s/g, '').replace(',', '.'));
  return Number.isFinite(n) ? n : null;
}

function parseSheet(xml, sharedStrings) {
  const cells = new Map();
  const cRe = /<c\s+r="([^"]+)"([^>]*)>([\s\S]*?)<\/c>/g;
  const vRe = /<v>([^<]*)<\/v>/;
  let m;
  while ((m = cRe.exec(xml)) !== null) {
    const ref   = m[1].toUpperCase();
    const attrs = m[2];
    const inner = m[3];
    const t     = (attrs.match(/\bt="([^"]*)"/) || [])[1] || '';
    const vM    = vRe.exec(inner);
    if (!vM) continue;
    const raw = vM[1].trim();
    let val;
    if (t === 's') {
      val = sharedStrings[parseInt(raw, 10)] ?? '';
    } else {
      val = toNum(raw) ?? raw;
    }
    cells.set(ref, val);
  }
  return cells;
}

function nextColRef(colStr, offset) {
  let n = 0;
  for (const c of colStr) n = n * 26 + (c.charCodeAt(0) - 64);
  n += offset;
  let result = '';
  while (n > 0) {
    const rem = (n - 1) % 26;
    result = String.fromCharCode(65 + rem) + result;
    n = Math.floor((n - 1) / 26);
  }
  return result;
}

// ── Main ──────────────────────────────────────────────────────────────────────

const DEBUG_KEYWORDS = /fastighetsbeteckning|beteckning|fastighet|adress|energi|klass|atemp|solcell/i;

async function inspect(filePath) {
  console.log(`\n${'='.repeat(70)}`);
  console.log(`File: ${filePath}`);
  console.log('='.repeat(70));

  // Read file into memory
  let buf;
  try {
    buf = await readFile(filePath);
  } catch (e) {
    console.error('ERROR: Could not read file:', e.message);
    return;
  }

  let zip;
  try {
    zip = new ZipReader(buf);
  } catch (e) {
    console.error('ERROR: Could not parse ZIP:', e.message);
    return;
  }

  // Shared strings
  let sharedStrings = [];
  try {
    const ss = zip.read('xl/sharedStrings.xml');
    if (ss) {
      sharedStrings = parseSharedStrings(ss);
      console.log(`\nShared strings count: ${sharedStrings.length}`);
    } else {
      console.log('\nNo sharedStrings.xml found.');
    }
  } catch {
    console.log('\nNo sharedStrings.xml found.');
  }

  // Workbook + rels → sheet list
  const sheetEntries = []; // [{name, zipKey}]
  try {
    const wb  = zip.read('xl/workbook.xml');
    const rel = zip.read('xl/_rels/workbook.xml.rels');
    if (wb && rel) {
      const relMap = new Map();
      const relRe = /<Relationship[^>]+Id="([^"]+)"[^>]+Target="([^"]+)"/g;
      let rM;
      while ((rM = relRe.exec(rel)) !== null) relMap.set(rM[1], rM[2]);

      const sheetRe = /<sheet\s[^>]*name="([^"]+)"[^>]*r:id="([^"]+)"/g;
      let sM;
      while ((sM = sheetRe.exec(wb)) !== null) {
        const target = relMap.get(sM[2]) || '';
        const key = target.startsWith('worksheets/')
          ? 'xl/' + target
          : 'xl/worksheets/' + target;
        sheetEntries.push({ name: sM[1], zipKey: key });
      }
    }
  } catch { /* fallback below */ }

  // Fallback: enumerate xl/worksheets/*.xml from ZIP
  if (sheetEntries.length === 0) {
    for (const k of zip.list('xl/worksheets/')) {
      if (k.endsWith('.xml')) {
        sheetEntries.push({ name: k.replace(/.*\//, '').replace('.xml', ''), zipKey: k });
      }
    }
  }

  // Print sheet names
  console.log(`\nSheets found (${sheetEntries.length}):`);
  for (const { name } of sheetEntries) {
    console.log(`  - ${name}`);
  }

  if (sheetEntries.length === 0) {
    console.log('No sheets found — cannot continue.');
    return;
  }

  // Parse all sheets and dump matching cells per sheet
  for (const { name, zipKey } of sheetEntries) {
    let cells;
    try {
      const xml = zip.read(zipKey);
      if (!xml) {
        console.log(`\n  Sheet "${name}": entry not found in ZIP (${zipKey})`);
        continue;
      }
      cells = parseSheet(xml, sharedStrings);
    } catch (e) {
      console.log(`\n  Sheet "${name}": could not parse (${e.message})`);
      continue;
    }

    // Find all cells matching debug keywords
    const hits = [];
    for (const [ref, val] of cells) {
      if (typeof val === 'string' && DEBUG_KEYWORDS.test(val)) {
        hits.push([ref, val]);
      }
    }

    if (hits.length === 0) continue;

    console.log(`\n${'─'.repeat(70)}`);
    console.log(`Sheet: "${name}"  (${cells.size} total cells, ${hits.length} keyword hits)`);
    console.log('─'.repeat(70));

    for (const [ref, label] of hits) {
      const m = ref.match(/^([A-Z]+)(\d+)$/);
      if (!m) continue;
      const col = m[1], row = parseInt(m[2], 10);

      // Same-row neighbors (cols B–F relative to label col, up to offset +5)
      const rowNeighbors = {};
      for (let d = 1; d <= 5; d++) {
        const neighborRef = nextColRef(col, d) + row;
        const v = cells.get(neighborRef);
        if (v !== undefined) rowNeighbors[neighborRef] = v;
      }

      // Row below (same col, offset cols)
      const belowNeighbors = {};
      for (let d = 0; d <= 5; d++) {
        const belowRef = nextColRef(col, d) + (row + 1);
        const v = cells.get(belowRef);
        if (v !== undefined) belowNeighbors[belowRef] = v;
      }

      // Row below+2
      const below2Neighbors = {};
      for (let d = 0; d <= 5; d++) {
        const below2Ref = nextColRef(col, d) + (row + 2);
        const v = cells.get(below2Ref);
        if (v !== undefined) below2Neighbors[below2Ref] = v;
      }

      console.log(`\n  Cell ${ref}: "${label}"`);
      if (Object.keys(rowNeighbors).length > 0) {
        console.log(`    Same row →  ${JSON.stringify(rowNeighbors)}`);
      } else {
        console.log(`    Same row →  (no values in next 5 cols)`);
      }
      if (Object.keys(belowNeighbors).length > 0) {
        console.log(`    Row below:  ${JSON.stringify(belowNeighbors)}`);
      }
      if (Object.keys(below2Neighbors).length > 0) {
        console.log(`    2 rows below: ${JSON.stringify(below2Neighbors)}`);
      }
    }
  }

  // Final: full extractXlsmFields result
  console.log(`\n${'='.repeat(70)}`);
  console.log('extractXlsmFields result:');
  console.log('='.repeat(70));
  try {
    const result = await extractXlsmFields(filePath);
    console.log(JSON.stringify(result, null, 2));
  } catch (e) {
    console.error('extractXlsmFields threw:', e.message);
  }
}

// ── Entry point ───────────────────────────────────────────────────────────────

const [,, ...args] = process.argv;
if (args.length === 0) {
  console.error('Usage: node inspectXlsm.mjs "path/to/file.xlsm"');
  process.exit(1);
}

for (const filePath of args) {
  await inspect(filePath);
}
