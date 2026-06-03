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

import { readFile, mkdir, rm, readdir } from 'node:fs/promises';
import { join }                          from 'node:path';
import { tmpdir }                        from 'node:os';
import { randomBytes }                   from 'node:crypto';
import { execFile }                      from 'node:child_process';
import { promisify }                     from 'node:util';
import { extractXlsmFields }             from './xlsm.mjs';

const exec = promisify(execFile);

// ── Shared helpers (duplicated from xlsm.mjs so this script is self-contained) ─

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

// ── Unzip ────────────────────────────────────────────────────────────────────

async function unzip(xlsmPath, dir) {
  try {
    await exec('unzip', ['-q', '-o', xlsmPath, '-d', dir]);
  } catch {
    await exec('python', [
      '-c',
      'import zipfile,sys; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])',
      xlsmPath, dir,
    ]);
  }
}

// ── Main ─────────────────────────────────────────────────────────────────────

const DEBUG_KEYWORDS = /fastighetsbeteckning|beteckning|fastighet|adress|energi|klass|atemp|solcell/i;

async function inspect(filePath) {
  console.log(`\n${'='.repeat(70)}`);
  console.log(`File: ${filePath}`);
  console.log('='.repeat(70));

  const dir = join(tmpdir(), 'inspect_' + randomBytes(6).toString('hex'));
  await mkdir(dir, { recursive: true });
  try {
    // Unzip
    try {
      await unzip(filePath, dir);
    } catch (e) {
      console.error('ERROR: Could not unzip file:', e.message);
      return;
    }

    // Shared strings
    let sharedStrings = [];
    try {
      const ss = await readFile(join(dir, 'xl', 'sharedStrings.xml'), 'utf8');
      sharedStrings = parseSharedStrings(ss);
      console.log(`\nShared strings count: ${sharedStrings.length}`);
    } catch {
      console.log('\nNo sharedStrings.xml found.');
    }

    // Workbook + rels → sheet list
    const sheetFiles = [];
    try {
      const wb  = await readFile(join(dir, 'xl', 'workbook.xml'),  'utf8');
      const rel = await readFile(join(dir, 'xl', '_rels', 'workbook.xml.rels'), 'utf8');

      const relMap = new Map();
      const relRe = /<Relationship[^>]+Id="([^"]+)"[^>]+Target="([^"]+)"/g;
      let rM;
      while ((rM = relRe.exec(rel)) !== null) relMap.set(rM[1], rM[2]);

      const sheetRe = /<sheet\s[^>]*name="([^"]+)"[^>]*r:id="([^"]+)"/g;
      let sM;
      while ((sM = sheetRe.exec(wb)) !== null) {
        const target = relMap.get(sM[2]) || '';
        const wsFile = target.startsWith('worksheets/')
          ? join(dir, 'xl', target)
          : join(dir, 'xl', 'worksheets', target);
        sheetFiles.push({ name: sM[1], file: wsFile });
      }
    } catch { /* fallback below */ }

    if (sheetFiles.length === 0) {
      try {
        const wsDir = join(dir, 'xl', 'worksheets');
        const files = await readdir(wsDir);
        for (const f of files.filter(f => f.endsWith('.xml'))) {
          sheetFiles.push({ name: f.replace('.xml', ''), file: join(wsDir, f) });
        }
      } catch { /* no worksheets */ }
    }

    // Print sheet names
    console.log(`\nSheets found (${sheetFiles.length}):`);
    for (const { name } of sheetFiles) {
      console.log(`  - ${name}`);
    }

    if (sheetFiles.length === 0) {
      console.log('No sheets found — cannot continue.');
      return;
    }

    // Parse all sheets and dump matching cells per sheet
    for (const { name, file } of sheetFiles) {
      let cells;
      try {
        const xml = await readFile(file, 'utf8');
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

  } finally {
    await rm(dir, { recursive: true, force: true }).catch(() => {});
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
