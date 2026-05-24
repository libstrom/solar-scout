/**
 * xlsm.mjs  —  Energivision XLSM-parser (no extra dependencies)
 * Unzips the XLSM, parses xl/sharedStrings.xml + worksheets,
 * finds fields by label-search, returns the same shape as pdf.mjs.
 */
import { readFile, mkdir, rm, readdir } from 'node:fs/promises';
import { join }                          from 'node:path';
import { tmpdir }                        from 'node:os';
import { randomBytes }                   from 'node:crypto';
import { execFile }                      from 'node:child_process';
import { promisify }                     from 'node:util';

const exec = promisify(execFile);

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

// ── unzip ────────────────────────────────────────────────────────────────────

async function unzip(xlsmPath, dir) {
  // Try unzip (Linux/Mac) first, fall back to Python's zipfile
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
    // Try right (1..3 cols)
    for (let d = 1; d <= 3; d++) {
      const cand = cells.get(nextColRef(col, d) + row);
      if (cand !== undefined && cand !== '' && toNum(cand) !== 0 || typeof cand === 'number') {
        return cand;
      }
    }
    // Try below (same col)
    const below = cells.get(col + (row + 1));
    if (below !== undefined && below !== '' && below !== 0) return below;
  }
  return null;
}

// ── main export ──────────────────────────────────────────────────────────────

export async function extractXlsmFields(filePath) {
  const dir = join(tmpdir(), 'edek_' + randomBytes(6).toString('hex'));
  await mkdir(dir, { recursive: true });
  try {
    try {
      await unzip(filePath, dir);
    } catch {
      return null; // password-protected or corrupt — fall back to PDF
    }

    // 1. Shared strings
    let sharedStrings = [];
    try {
      const ss = await readFile(join(dir, 'xl', 'sharedStrings.xml'), 'utf8');
      sharedStrings = parseSharedStrings(ss);
    } catch { /* none */ }

    // 2. Sheet → rId map from workbook.xml
    const sheetFiles = [];  // [{name, file}]
    try {
      const wb  = await readFile(join(dir, 'xl', 'workbook.xml'),  'utf8');
      const rel = await readFile(join(dir, 'xl', '_rels', 'workbook.xml.rels'), 'utf8');

      // rId → target path
      const relMap = new Map();
      const relRe = /<Relationship[^>]+Id="([^"]+)"[^>]+Target="([^"]+)"/g;
      let rM;
      while ((rM = relRe.exec(rel)) !== null) relMap.set(rM[1], rM[2]);

      // sheet name + rId
      const sheetRe = /<sheet\s[^>]*name="([^"]+)"[^>]*r:id="([^"]+)"/g;
      let sM;
      while ((sM = sheetRe.exec(wb)) !== null) {
        const target = relMap.get(sM[2]) || '';
        const wsFile = target.startsWith('worksheets/')
          ? join(dir, 'xl', target)
          : join(dir, 'xl', 'worksheets', target);
        sheetFiles.push({ name: sM[1].toLowerCase(), file: wsFile });
      }
    } catch { /* fallback below */ }

    // Fallback: enumerate xl/worksheets/*.xml
    if (sheetFiles.length === 0) {
      try {
        const wsDir = join(dir, 'xl', 'worksheets');
        const files = await readdir(wsDir);
        for (const f of files.filter(f => f.endsWith('.xml'))) {
          sheetFiles.push({ name: f.replace('.xml', ''), file: join(wsDir, f) });
        }
      } catch { /* no worksheets */ }
    }

    if (sheetFiles.length === 0) return null;

    // 3. Sort: certifikat / indata first; load all sheets
    const PRIO = ['certifikat', 'indata', 'rapport', 'deklaration', 'data'];
    sheetFiles.sort((a, b) => {
      const pa = PRIO.findIndex(p => a.name.includes(p));
      const pb = PRIO.findIndex(p => b.name.includes(p));
      return (pa === -1 ? 99 : pa) - (pb === -1 ? 99 : pb);
    });

    // Merge all cells; first (highest-priority) sheet wins
    const merged = new Map();
    for (const { file } of [...sheetFiles].reverse()) {
      try {
        const xml = await readFile(file, 'utf8');
        for (const [k, v] of parseSheet(xml, sharedStrings)) merged.set(k, v);
      } catch { /* skip */ }
    }

    if (merged.size === 0) return null;

    // ── 4. Extract fields ──────────────────────────────────────────────────
    const L = (re) => findValueByLabel(merged, re);
    const str = (v) => (v != null && String(v).trim() !== '' ? String(v).trim() : null);

    const fastighetsbeteckning = str(
      L(/^fastighetsbeteckning$/i) ??
      L(/beteckning\s*\(utan\s*kommun/i) ??
      L(/^fastighet\s*beteckning/i)
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
  } finally {
    await rm(dir, { recursive: true, force: true }).catch(() => {});
  }
}
