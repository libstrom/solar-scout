/**
 * xlsm.mjs  —  Energivision XLSM-parser (no extra dependencies)
 * Reads ZIP entries directly in memory — zero disk writes, no temp dirs.
 * Finds fields by label-search, returns the same shape as pdf.mjs.
 */
import { readFile }      from 'node:fs/promises';
import { inflateRawSync } from 'node:zlib';

// ── tiny utilities ───────────────────────────────────────────────────────────

function toNum(v) {
  if (v == null) return null;
  const n = parseFloat(String(v).replace(/\s/g, '').replace(',', '.'));
  return Number.isFinite(n) ? n : null;
}

function classifyEnergy(prestanda, krav) {
  if (!prestanda || !krav) return null;
  const r = prestanda / krav;
  if (r <= 0.50) return 'A';
  if (r <= 0.75) return 'B';
  if (r <= 1.00) return 'C';
  if (r <= 1.35) return 'D';
  if (r <= 1.80) return 'E';
  if (r <= 2.35) return 'F';
  return 'G';
}

// ── regex-based XML helpers (no dependencies) ────────────────────────────────

// Extract all <t>…</t> text nodes inside each <si>…</si> block
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

// Parse sheet XML → Map<"A1", value>
function parseSheet(xml, sharedStrings) {
  const cells = new Map();
  // Match every <c r="..." t="..."> ... <v>...</v> ... </c>
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

// ── in-memory ZIP reader ─────────────────────────────────────────────────────
// Pure Node.js, zero disk writes. Handles standard ZIP (not ZIP64).

class ZipReader {
  constructor(buf) {
    this.buf = buf;
    this.entries = this._readCentralDir();
  }

  _readCentralDir() {
    const b = this.buf;
    // Find End of Central Directory (EOCD) signature PK\x05\x06
    let eocd = -1;
    for (let i = b.length - 22; i >= 0; i--) {
      if (b[i] === 0x50 && b[i+1] === 0x4b && b[i+2] === 0x05 && b[i+3] === 0x06) {
        eocd = i; break;
      }
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

  // All entry names that start with prefix
  list(prefix = '') {
    return [...this.entries.keys()].filter(k => k.startsWith(prefix));
  }
}

// ── label-search ─────────────────────────────────────────────────────────────
// Find a cell matching labelRe, return the value of the adjacent cell(s).
// Tries: same-row next column (up to 3 cols right), then one row below.

function nextColRef(colStr, offset) {
  // Convert column letters to number, add offset, convert back
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

function findValueByLabel(cells, labelRe) {
  for (const [ref, val] of cells) {
    if (typeof val !== 'string') continue;
    if (!labelRe.test(val.trim())) continue;
    const m = ref.match(/^([A-Z]+)(\d+)$/);
    if (!m) continue;
    const col = m[1], row = parseInt(m[2], 10);
    // Try right (1..5 cols) — some merged-cell layouts skip columns
    for (let d = 1; d <= 5; d++) {
      const cand = cells.get(nextColRef(col, d) + row);
      if (cand !== undefined && cand !== '' && (toNum(cand) !== 0 || typeof cand === 'string')) {
        return cand;
      }
    }
    // Try 1 row below (same col)
    const below = cells.get(col + (row + 1));
    if (below !== undefined && below !== '' && below !== 0) return below;
    // Try 2 rows below (some Energivision layouts put value 2 rows below label)
    const below2 = cells.get(col + (row + 2));
    if (below2 !== undefined && below2 !== '' && below2 !== 0) return below2;
  }
  return null;
}

// ── main export ──────────────────────────────────────────────────────────────

export async function extractXlsmFields(filePath) {
  let zip;
  try {
    const buf = await readFile(filePath);
    zip = new ZipReader(buf);
  } catch {
    return null; // password-protected (can't read as buffer) or missing
  }

  // 1. Shared strings
  let sharedStrings = [];
  try {
    const ss = zip.read('xl/sharedStrings.xml');
    if (ss) sharedStrings = parseSharedStrings(ss);
  } catch { /* none */ }

  // 2. Sheet list from workbook + rels
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
        sheetEntries.push({ name: sM[1].toLowerCase(), zipKey: key });
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

  if (sheetEntries.length === 0) return null;

  // 3. Sort: certifikat / indata first; merge all cells
  const PRIO = ['inmatning', 'certifikat', 'indata', 'rapport', 'deklaration', 'data'];
  sheetEntries.sort((a, b) => {
    const pa = PRIO.findIndex(p => a.name.includes(p));
    const pb = PRIO.findIndex(p => b.name.includes(p));
    return (pa === -1 ? 99 : pa) - (pb === -1 ? 99 : pb);
  });

  const merged = new Map();
  for (const { zipKey } of [...sheetEntries].reverse()) {
    try {
      const xml = zip.read(zipKey);
      if (xml) for (const [k, v] of parseSheet(xml, sharedStrings)) merged.set(k, v);
    } catch { /* skip */ }
  }

    if (merged.size === 0) return null;

    // ── 4. Extract fields ──────────────────────────────────────────────────
    const L = (re) => findValueByLabel(merged, re);
    const str = (v) => (v != null && String(v).trim() !== '' ? String(v).trim() : null);

    const fastighetsbeteckning = str(
      L(/^fastighetsbeteckning:?$/i) ??
      L(/^fastighetsbeteckning\s*\*+$/i) ??
      L(/beteckning\s*\(utan\s*kommun/i) ??
      L(/^fastighet\s*beteckning/i) ??
      L(/^fastighetsbeteckning\b/i)
    );

    const adress = str(
      L(/^(gatu)?adress$/i) ??
      L(/^adress$/i)
    );

    const postnummer = (() => {
      const v = str(L(/^postnummer$/i) ?? L(/^post\s*nr\.?$/i));
      if (!v) return null;
      const s = v.replace(/\s/g, '');
      return /^\d{5}$/.test(s) ? s : null;
    })();

    const ort = str(
      L(/^(post)?ort$/i) ??
      L(/^stad$/i) ??
      L(/^ort\s*\/?\s*stad/i)
    );

    const kommun = (() => {
      const v = str(L(/^kommun$/i));
      return v ? v.replace(/\s*kommun\s*$/i, '') : null;
    })();

    const lan = (() => {
      const v = str(L(/^l[äa]n$/i));
      return v ? v.replace(/\s*l[äa]n\s*$/i, '') : null;
    })();

    const nybyggnadsar = (() => {
      const v = toNum(L(/^nybyggnads[aå]r$/i) ?? L(/^byggår$/i));
      return v && v > 1700 && v < 2100 ? v : null;
    })();

    const atemp_m2 = toNum(
      L(/^a\s*temp/i) ??
      L(/^uppvärmd\s+(golvarea|area)/i) ??
      L(/^uppvärmd\s+yta/i)
    );

    const uppvarmningssystem = str(
      L(/^uppv[äa]rmningssystem$/i) ??
      L(/^v[äa]rmesystem$/i) ??
      L(/^uppv[äa]rmning$/i)
    );

    const energideklarations_id = str(L(/^(energideklarations[\s-]*id|diarienummer)$/i));

    const energiprestanda_kwh = toNum(
      L(/^energiprestanda$/i) ??
      L(/^prim[äa]renergi/i) ??
      L(/^specifik\s+energianv[äa]ndning/i)
    );

    const krav_nybyggnad_kwh = toNum(
      L(/^krav\s+nybyggnad/i) ??
      L(/^nybyggnadskrav/i) ??
      L(/^krav\s+ny\s+byggnad/i)
    );

    const energiklass_computed = classifyEnergy(energiprestanda_kwh, krav_nybyggnad_kwh);
    const energiklass_cell = (() => {
      const v = str(L(/^energiklass$/i));
      return v && /^[A-G]$/i.test(v) ? v.toUpperCase() : null;
    })();
    const energiklass = energiklass_cell ?? energiklass_computed;

    const total_energi_kwh = toNum(
      L(/^(total\s+)?energianv[äa]ndning/i) ??
      L(/^byggnadens\s+energianv/i)
    );

    const hushallsel_kwh = toNum(L(/^hush[åa]llsel$/i));

    const giltig_till = (() => {
      const v = str(L(/^giltig\s+till/i) ?? L(/^g[äa]ller\s+till/i));
      if (!v) return null;
      const m = v.match(/(\d{4}-\d{2}-\d{2})/);
      return m ? m[1] : v;
    })();

    const deklaration_datum = (() => {
      const v = str(
        L(/^deklarationsdatum$/i) ??
        L(/^datum\s+f[öo]r\s+godk[äa]nnande/i) ??
        L(/^uppr[äa]ttad$/i)
      );
      if (!v) return null;
      const m = v.match(/(\d{4}-\d{2}-\d{2})/);
      return m ? m[1] : v;
    })();

    const energiexpert = str(
      L(/^(energi)?expert$/i) ??
      L(/^handl[äa]ggare$/i) ??
      L(/^certifierad\s+energiexpert/i)
    );

    const expert_foretag = str(
      L(/^f[öo]retag$/i) ??
      L(/^f[öo]retagsnamn$/i)
    );

    const certifikatnummer = str(L(/^certifikatnummer$/i));

    const har_solceller = (() => {
      const v = L(/^solcell(ssystem)?/i);
      if (!v) return false;
      return String(v).toLowerCase() === 'ja' || (toNum(v) ?? 0) > 0;
    })();

    const har_solvarme = (() => {
      const v = L(/^solv[äa]rme/i);
      if (!v) return false;
      return String(v).toLowerCase() === 'ja' || (toNum(v) ?? 0) > 0;
    })();

    // Energy per source
    const SOURCES = {
      fjarrvarme:     /fj[äa]rrv[äa]rme/i,
      olja:           /eldningsolja|olja/i,
      gas:            /naturgas|stadsgas/i,
      ved:            /\bved\b/i,
      pellets:        /flis|pellets|brikett/i,
      biobransle:     /biobr[äa]nsle/i,
      el_vattenburen: /el.*vatten|vattenburen.*el/i,
      el_direkt:      /direktverkande.*el|el.*direkt/i,
      el_luftburen:   /luftburen.*el|el.*luftburen/i,
      markvp:         /markvp|markv[äa]rmepump/i,
      fl_vp:          /fr[åa]nluft.*v[äa]rmepump|v[äa]rmepump.*fr[åa]nluft/i,
      ll_vp:          /luft.*luft.*v[äa]rmepump/i,
      lv_vp:          /luft.*vatten.*v[äa]rmepump/i,
      fjarrkyla:      /fj[äa]rrkyla/i,
    };
    const energi_per_kalla = {};
    for (const [key, re] of Object.entries(SOURCES)) {
      energi_per_kalla[key] = toNum(L(re)) ?? 0;
    }

    const el_uppvarmning_kwh =
      (energi_per_kalla.el_vattenburen || 0) +
      (energi_per_kalla.el_direkt      || 0) +
      (energi_per_kalla.el_luftburen   || 0) +
      (energi_per_kalla.markvp         || 0) +
      (energi_per_kalla.fl_vp          || 0) +
      (energi_per_kalla.ll_vp          || 0) +
      (energi_per_kalla.lv_vp          || 0);

    const total_el_kwh = el_uppvarmning_kwh + (hushallsel_kwh || 0);

    // Åtgärdsförslag — pick long strings that look like recommendations
    const atgardsforslag = (() => {
      const bits = [];
      for (const [, val] of merged) {
        if (typeof val !== 'string' || val.length < 30) continue;
        if (/[åäö]/i.test(val) &&
            /([åa]tg[äa]rd|f[öo]rb[äa]ttr|byt\s+till|tilläggsisolera|fönster|v[äa]rmepump)/i.test(val)) {
          bits.push(val.trim());
        }
      }
      if (bits.length === 0) return null;
      return { typ: 'forslag', text: bits.slice(0, 8).join(' | ').slice(0, 2000), summary: bits[0].slice(0, 120) };
    })();

    return {
      source: 'xlsm',
      energideklarations_id,
      fastighetsbeteckning,
      adress,
      postnummer,
      ort,
      lan,
      kommun,
      nybyggnadsar,
      atemp_m2,
      uppvarmningssystem,
      primarenergital:           energiprestanda_kwh,
      energiprestanda_kwh,
      krav_nybyggnad_kwh,
      energiklass,
      specifik_energianvandning: energiprestanda_kwh,
      el_uppvarmning_kwh,
      hushallsel_kwh,
      total_el_kwh,
      total_energi_kwh,
      har_solceller,
      har_solvarme,
      energi_per_kalla,
      energiexpert,
      expert_foretag,
      expert_email: null,
      certifikatnummer,
      deklaration_datum,
      giltig_till,
      atgardsforslag,
    };
}
