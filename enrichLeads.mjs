#!/usr/bin/env node
/**
 * enrichLeads.mjs
 *
 * Enriches leads.json with contact data from enspecta.tab.
 * Priority per property: Köpare > Intressent > Säljare
 *
 * Usage:
 *   node enrichLeads.mjs leads.json enspecta.tab [output.json]
 *
 * Columns in enspecta.tab:
 *   0  CaseID      1  Status      2  Date        5  Address
 *   6  Postnr      7  City        8  Kommun      9  PrimaryName
 *  12  Fastighet  20  SecondaryName  22 Email    23 Phone
 *  24  IntFirstName  25 IntLastName  26 IntEmail  27 IntPhone1  28 IntPhone2
 */

import { readFileSync, writeFileSync } from 'fs';

const STATUS_PRIORITY = { 'Köpare': 0, '': 1, 'Avhoppad': 2, 'Säljare': 3 };

function clean(s) {
  if (!s) return '';
  return s.replace(/[\x00-\x1f]/g, ' ').replace(/\s+/g, ' ').trim();
}

function normalizeFastig(s) {
  return clean(s).toLowerCase().replace(/\s+/g, ' ');
}

function parseTab(tabPath) {
  const lines = readFileSync(tabPath, 'utf-8').split('\n');
  const byFastig = new Map(); // fastig → { köpare: [], intressenter: [], säljare: [] }

  let currentCase = null;

  for (const line of lines) {
    const c = line.split('\t');
    const caseId = clean(c[0]);

    if (caseId) {
      // Main record row
      const status  = clean(c[1]);
      const fastig  = normalizeFastig(c[12]);
      const record  = {
        status,
        name:      clean(c[9]),
        name2:     clean(c[20]),
        address:   clean(c[5]),
        postnr:    clean(c[6]),
        city:      clean(c[7]),
        email:     clean(c[22]),
        phone:     clean(c[23]),
        // first intressent slot (often populated on same row)
        intFirstName: clean(c[24]),
        intLastName:  clean(c[25]),
        intEmail:     clean(c[26]),
        intPhone:     clean(c[27]),
      };

      currentCase = { fastig, status, record, intressenter: [] };

      // If there's an intressent embedded on this row, collect it
      if (record.intFirstName) {
        currentCase.intressenter.push({
          name:  `${record.intFirstName} ${record.intLastName}`.trim(),
          email: record.intEmail,
          phone: record.intPhone || clean(c[28]),
        });
      }

      if (!fastig) continue; // skip rows with no fastighetsbeteckning

      if (!byFastig.has(fastig)) {
        byFastig.set(fastig, { köpare: [], intressenter: [], säljare: [] });
      }
      const bucket = byFastig.get(fastig);
      const slot = status === 'Köpare' ? 'köpare'
                 : status === 'Säljare' ? 'säljare'
                 : null;
      if (slot) bucket[slot].push(currentCase);

    } else {
      // Intressent continuation row (empty col0)
      const fname = clean(c[24]);
      if (!fname || !currentCase) continue;
      currentCase.intressenter.push({
        name:  `${fname} ${clean(c[25])}`.trim(),
        email: clean(c[26]),
        phone: clean(c[27]) || clean(c[28]),
      });
    }
  }

  // Attach intressenter to each fastig bucket
  // Walk again to associate intressenter to their parent case's fastig
  // (already done above inline — now push intressenter buckets to byFastig)
  // Re-walk to propagate collected intressenter to fastig map
  const lines2 = readFileSync(tabPath, 'utf-8').split('\n');
  let curFastig = null;
  let curStatus = null;
  let curInts   = [];

  for (const line of lines2) {
    const c = line.split('\t');
    const caseId = clean(c[0]);

    if (caseId) {
      // Flush previous
      if (curFastig && curStatus === 'Säljare' && curInts.length > 0) {
        if (!byFastig.has(curFastig)) byFastig.set(curFastig, { köpare: [], intressenter: [], säljare: [] });
        byFastig.get(curFastig).intressenter.push(...curInts);
      }
      curFastig = normalizeFastig(c[12]);
      curStatus = clean(c[1]);
      curInts   = [];
      const fname = clean(c[24]);
      if (fname) curInts.push({ name: `${fname} ${clean(c[25])}`.trim(), email: clean(c[26]), phone: clean(c[27]) || clean(c[28]) });
    } else {
      const fname = clean(c[24]);
      if (fname) curInts.push({ name: `${fname} ${clean(c[25])}`.trim(), email: clean(c[26]), phone: clean(c[27]) || clean(c[28]) });
    }
  }
  // Flush last
  if (curFastig && curStatus === 'Säljare' && curInts.length > 0) {
    if (!byFastig.has(curFastig)) byFastig.set(curFastig, { köpare: [], intressenter: [], säljare: [] });
    byFastig.get(curFastig).intressenter.push(...curInts);
  }

  return byFastig;
}

function bestContact(bucket) {
  // 1. Köpare with contact info
  for (const cs of bucket.köpare) {
    if (cs.record.phone || cs.record.email) {
      return { type: 'Köpare', name: cs.record.name, phone: cs.record.phone, email: cs.record.email };
    }
  }
  // 2. Intressent from Säljare inspection with contact info
  for (const int of bucket.intressenter) {
    if (int.phone || int.email) {
      return { type: 'Intressent', name: int.name, phone: int.phone, email: int.email };
    }
  }
  // 3. Säljare (last resort — has moved away)
  for (const cs of bucket.säljare) {
    if (cs.record.phone || cs.record.email) {
      return { type: 'Säljare', name: cs.record.name, phone: cs.record.phone, email: cs.record.email };
    }
  }
  return null;
}

function main() {
  const [leadsPath, tabPath, outPath] = process.argv.slice(2);
  if (!leadsPath || !tabPath) {
    console.error('Usage: node enrichLeads.mjs leads.json enspecta.tab [output.json]');
    process.exit(1);
  }

  console.log('Parsing enspecta.tab …');
  const byFastig = parseTab(tabPath);
  console.log(`  Unique fastighetsbeteckningar i registret: ${byFastig.size}`);

  const leads = JSON.parse(readFileSync(leadsPath, 'utf-8'));
  console.log(`Enriching ${leads.length} leads …`);

  let stats = { köpare: 0, intressent: 0, säljare: 0, none: 0, same: 0, upgraded: 0 };

  for (const lead of leads) {
    const key = normalizeFastig(lead.fastighetsbeteckning || '');
    const bucket = byFastig.get(key);

    if (!bucket) {
      lead._contactType = 'none';
      stats.none++;
      continue;
    }

    const contact = bestContact(bucket);
    if (!contact) {
      lead._contactType = 'none';
      stats.none++;
      continue;
    }

    const prevType = lead._contactType;
    lead._contactType = contact.type;
    lead.namn         = contact.name;
    lead.telefon      = contact.phone;
    lead.email        = contact.email;
    stats[contact.type === 'Köpare' ? 'köpare' : contact.type === 'Intressent' ? 'intressent' : 'säljare']++;

    if (prevType && prevType !== contact.type) stats.upgraded++;
    else if (prevType === contact.type) stats.same++;
  }

  const out = outPath || leadsPath.replace('.json', '-enriched.json');
  writeFileSync(out, JSON.stringify(leads, null, 2));

  console.log('\n=== Resultat ===');
  console.log(`  Köpare (bäst):      ${stats.köpare}`);
  console.log(`  Intressent:         ${stats.intressent}`);
  console.log(`  Säljare (sämst):    ${stats.säljare}`);
  console.log(`  Ingen matchning:    ${stats.none}`);
  console.log(`  Uppgraderade:       ${stats.upgraded}`);
  console.log(`\nSkriven till: ${out}`);
}

main();
