#!/usr/bin/env python3
"""
makeLeads.py — Genererar leads.xlsx optimerat för telefon-mötesbokning

Flikar:
  • Ringlista   — prioriterat ringköö med status-kolumner för Ivan/David
  • Översikt    — statistik + prioritet-guide

Usage:
    python makeLeads.py enspecta.tab [leads.xlsx]
"""
import sys, re
from collections import Counter

try:
    from openpyxl import Workbook
    from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.table import Table, TableStyleInfo
    from openpyxl.worksheet.datavalidation import DataValidation
    from openpyxl.styles.differential import DifferentialStyle
    from openpyxl.formatting.rule import Rule
except ImportError:
    print("Kör: pip install openpyxl")
    sys.exit(1)


# ── helpers ───────────────────────────────────────────────────────────────────

def clean(s):
    if not s: return ''
    return re.sub(r'[\x00-\x1f]', ' ', s).strip()

def norm(s):
    return clean(s).lower().replace('  ', ' ')

def title_case(s):
    return clean(s).title() if s else ''

def extract_year(s):
    m = re.match(r'(\d{4})', str(s or ''))
    return int(m.group(1)) if m else None

def priority(byggår_str):
    """Prioritet 1-3 baserat på husålder. 1=högst (gamla hus, störst behov)."""
    y = extract_year(byggår_str)
    if not y: return 2
    if y <= 1980: return 1   # Eldningsolja/direktel, sämst isolering
    if y <= 1995: return 2   # Bra kandidater
    return 3                  # Nyare, lägre potential

def pitch_hint(byggår_str):
    """Kort pitch-poäng baserat på byggår."""
    y = extract_year(byggår_str)
    if not y: return ''
    if y <= 1960: return 'Gammalt hus — troligen direktel/olja. Sol+VP-pitch.'
    if y <= 1980: return 'Energiklass D-F sannolikt. Sol+batteri stark pitch.'
    if y <= 1995: return 'Bra sol-kandidat. Fråga om uppvärmning.'
    return 'Nyare hus — fråga om solceller redan finns.'


# ── parse enspecta.tab ────────────────────────────────────────────────────────

def parse_tab(path):
    with open(path, encoding='utf-8', errors='replace') as f:
        lines = f.read().split('\n')

    by_fastig = {}
    cur_fastig = cur_status = cur_rec = None
    cur_ints = []

    def flush():
        if not cur_fastig or not cur_rec: return
        if cur_fastig not in by_fastig:
            by_fastig[cur_fastig] = {'kopare': [], 'intressenter': [], 'saljare': []}
        b = by_fastig[cur_fastig]
        if cur_status == 'Köpare':   b['kopare'].append(cur_rec)
        elif cur_status == 'Säljare': b['saljare'].append(cur_rec)
        if cur_status == 'Säljare' and cur_ints:
            b['intressenter'].extend(cur_ints)

    def mk_int(c, prop):
        fn = clean(c[24]) if len(c)>24 else ''
        if not fn: return None
        return {
            'namn':    f"{fn} {clean(c[25]) if len(c)>25 else ''}".strip(),
            'telefon': (clean(c[27]) if len(c)>27 else '') or (clean(c[28]) if len(c)>28 else ''),
            'email':   clean(c[26]) if len(c)>26 else '',
            'adress':  prop.get('adress','') if prop else '',
            'postnr':  prop.get('postnr','') if prop else '',
            'ort':     prop.get('ort','')    if prop else '',
            'kommun':  prop.get('kommun','') if prop else '',
            'besdat':  prop.get('besdat','') if prop else '',
            'byggår':  prop.get('byggår','') if prop else '',
        }

    for line in lines:
        c = line.split('\t')
        case_id = clean(c[0]) if c else ''
        if case_id:
            flush()
            fastig = norm(c[12]) if len(c)>12 else ''
            cur_fastig = fastig or None
            cur_status = clean(c[1]) if len(c)>1 else ''
            cur_rec = {
                'case_id': case_id,
                'namn':    clean(c[9])  if len(c)>9  else '',
                'namn2':   clean(c[20]) if len(c)>20 else '',
                'adress':  clean(c[5])  if len(c)>5  else '',
                'postnr':  clean(c[6])  if len(c)>6  else '',
                'ort':     clean(c[7])  if len(c)>7  else '',
                'kommun':  clean(c[8])  if len(c)>8  else '',
                'telefon': clean(c[23]) if len(c)>23 else '',
                'email':   clean(c[22]) if len(c)>22 else '',
                'besdat':  clean(c[2])  if len(c)>2  else '',
                'byggår':  clean(c[18]).replace('--','') if len(c)>18 else '',
                'renovat': clean(c[19]).replace('--','') if len(c)>19 else '',
                'fastig':  fastig,
            } if fastig else None
            cur_ints = []
            i = mk_int(c, cur_rec)
            if i: cur_ints.append(i)
        else:
            fn = clean(c[24]) if len(c)>24 else ''
            if fn and cur_rec:
                i = mk_int(c, cur_rec)
                if i: cur_ints.append(i)
    flush()
    return by_fastig


def best_contact(b):
    for r in b['kopare']:
        if r['telefon'] or r['email']:
            return 'Köpare', r, b['intressenter']
    for r in b['intressenter']:
        if r.get('telefon') or r.get('email'):
            prop = b['saljare'][0] if b['saljare'] else {}
            merged = {**prop, **r, 'namn': r['namn'], 'namn2': '',
                      'case_id': prop.get('case_id',''),
                      'besdat':  r.get('besdat', prop.get('besdat','')),
                      'byggår':  r.get('byggår',  prop.get('byggår',''))}
            return 'Intressent', merged, []
    for r in b['saljare']:
        if r['telefon'] or r['email']:
            return 'Säljare', r, b['intressenter']
    return None, None, []


# ── Excel styling ─────────────────────────────────────────────────────────────

def F(hex_):  return PatternFill('solid', fgColor=hex_)
def Fn(*a, **k): return Font(*a, **k)

def thin_border(color='DDDDDD'):
    s = Side(style='thin', color=color)
    return Border(left=s, right=s, top=s, bottom=s)

# Palette
BLU  = '1A237E'; BLU2 = '283593'; BLU3 = 'E8EAF6'
GRN  = 'E8F5E9'; GRN2 = '1B5E20'
YLW  = 'FFFDE7'; YLW2 = 'F57F17'
ORG  = 'FFF3E0'; ORG2 = 'E65100'
RED  = 'FFEBEE'; RED2 = 'B71C1C'
LGRY = 'FAFAFA'

STATUS_OPTS = '"Ej kontaktad,Bokad möte,Ej intresserad,Återkom,Fel nummer,Röstbrevlåda,Såld"'
SALJARE_OPTS = '"Ivan,David,Linus,Annan"'

# Column definitions: (header, width, note)
COLS = [
    # ── CRM-kolumner ──────────────────────────────────
    ('Prio',              5,  '1=gammalt hus, 3=nytt'),
    ('Status',            16, 'Välj från lista'),
    ('Säljare',           10, 'Vem ringer?'),
    ('Nästa kontakt',     14, 'Datum för uppföljning'),
    ('Anteckningar',      35, 'Fritext — vad sa kunden?'),
    # ── Kontaktinfo ───────────────────────────────────
    ('Namn',              26, ''),
    ('Namn 2',            20, 'Partner/medsökande'),
    ('Telefon',           16, ''),
    ('E-post',            30, ''),
    # ── Adress ────────────────────────────────────────
    ('Adress',            28, ''),
    ('Postnr',             8, ''),
    ('Ort',               16, ''),
    ('Kommun',            16, ''),
    # ── Fastighetsfakta ───────────────────────────────
    ('Byggår',             8, ''),
    ('Renoverat',          9, ''),
    ('Besiktningsdatum',  16, ''),
    ('Pitch',             42, 'Automatiskt baserat på byggår'),
    # ── Metadata ──────────────────────────────────────
    ('Kontakttyp',        13, 'Köpare/Intressent/Säljare'),
    ('Intressent — Namn', 22, ''),
    ('Intressent — Tel',  16, ''),
    ('Fastighetsbeteckning', 24, ''),
    ('CaseID',            12, ''),
]


def make_ringlista(wb, rows):
    ws = wb.active
    ws.title = 'Ringlista'
    ws.sheet_view.showGridLines = False

    ncols = len(COLS)
    last_col_letter = get_column_letter(ncols)

    # ── Row 1: banner ──
    ws.merge_cells(f'A1:{last_col_letter}1')
    b = ws['A1']
    b.value     = '☀️  ENSPECTA RINGLISTA  —  Mötesbokning sol + batteri + VP  |  Prio 1 = ring idag'
    b.font      = Fn(bold=True, size=12, color='FFFFFF')
    b.fill      = F(BLU)
    b.alignment = Alignment(horizontal='center', vertical='center')
    ws.row_dimensions[1].height = 28

    # ── Row 2: filter guide ──
    ws.merge_cells(f'A2:{last_col_letter}2')
    g = ws['A2']
    g.value     = (
        'FILTER-TIPS:  Klicka ▾ på kolumnrubriken för att filtrera  •  '
        'Status → visa bara "Ej kontaktad"  •  Prio → visa bara "1"  •  '
        'Kommun → välj ett område att jobba med idag'
    )
    g.font      = Fn(size=10, color='1A237E', italic=True)
    g.fill      = F(BLU3)
    g.alignment = Alignment(horizontal='left', vertical='center')
    ws.row_dimensions[2].height = 20

    # ── Row 3: column headers ──
    for ci, (hdr, w, _) in enumerate(COLS, 1):
        c = ws.cell(3, ci, hdr)
        c.fill      = F(BLU2)
        c.font      = Fn(bold=True, color='FFFFFF', size=10)
        c.alignment = Alignment(horizontal='center', vertical='center')
        c.border    = thin_border('1A237E')
    ws.row_dimensions[3].height = 24

    # ── Data validation dropdowns ──
    dv_status  = DataValidation(type='list', formula1=STATUS_OPTS,  showDropDown=False, showErrorMessage=False)
    dv_saljare = DataValidation(type='list', formula1=SALJARE_OPTS, showDropDown=False, showErrorMessage=False)
    dv_status.sqref  = f'B4:B{len(rows)+3}'
    dv_saljare.sqref = f'C4:C{len(rows)+3}'
    ws.add_data_validation(dv_status)
    ws.add_data_validation(dv_saljare)

    # ── Data rows ──
    TYPE_FILL = {'Köpare': F(GRN), 'Intressent': F(YLW), 'Säljare': F(ORG)}
    PRIO_FONT = {1: Fn(bold=True, color=RED2), 2: Fn(color=YLW2), 3: Fn(color='555555')}

    for ri, row in enumerate(rows, 4):
        typ   = row[17]   # Kontakttyp column
        rfill = TYPE_FILL.get(typ, F(LGRY))
        prio  = row[0]

        for ci, val in enumerate(row, 1):
            c = ws.cell(ri, ci, val)
            c.fill   = rfill
            c.border = thin_border()
            c.alignment = Alignment(vertical='center', wrap_text=False)

            if ci == 1:   # Prio
                c.font      = PRIO_FONT.get(prio, Fn())
                c.alignment = Alignment(horizontal='center', vertical='center')
            elif ci == 2:  # Status — default
                c.value = 'Ej kontaktad'
                c.font  = Fn(color='555555', italic=True, size=10)
            elif ci in (8, 9):   # Telefon, e-post
                c.font = Fn(name='Consolas', size=10)
            elif ci == 17:  # Pitch
                c.font = Fn(size=9, color='444444', italic=True)
            elif ci == 18:  # Kontakttyp
                col_map = {'Köpare': GRN2, 'Intressent': YLW2, 'Säljare': ORG2}
                c.font = Fn(bold=True, size=9, color=col_map.get(typ, '000000'))

        ws.row_dimensions[ri].height = 18

    # ── Excel Table ──
    tbl = Table(displayName='Ringlista', ref=f'A3:{last_col_letter}{len(rows)+3}')
    tbl.tableStyleInfo = TableStyleInfo(
        name='TableStyleLight1',
        showFirstColumn=False, showLastColumn=False,
        showRowStripes=False,  showColumnStripes=False,
    )
    ws.add_table(tbl)

    # ── Conditional formatting: "Bokad möte" → grön rad ──
    booked_fill = F('C8E6C9')
    ds_booked = DifferentialStyle(fill=booked_fill)
    rule_booked = Rule(type='expression', dxf=ds_booked,
                       formula=['$B4="Bokad möte"'])
    ws.conditional_formatting.add(f'A4:{last_col_letter}{len(rows)+3}', rule_booked)

    # ── Conditional formatting: "Ej intresserad" → gråtonad ──
    grey_fill = F('EEEEEE')
    grey_font = DifferentialStyle(fill=grey_fill, font=Fn(color='AAAAAA'))
    rule_nej = Rule(type='expression', dxf=grey_font,
                    formula=['$B4="Ej intresserad"'])
    ws.conditional_formatting.add(f'A4:{last_col_letter}{len(rows)+3}', rule_nej)

    ws.freeze_panes = 'A4'

    for ci, (_, w, _) in enumerate(COLS, 1):
        ws.column_dimensions[get_column_letter(ci)].width = w


def make_oversikt(wb, rows):
    ws = wb.create_sheet('Översikt')
    ws.sheet_view.showGridLines = False

    def hdr(r, txt, size=13):
        c = ws.cell(r, 1, txt)
        c.font = Fn(bold=True, size=size, color=BLU)
        ws.cell(r, 2).fill = F('FFFFFF')

    def kv(r, k, v, vc=None):
        ws.cell(r, 1, k).font  = Fn(bold=True, size=11)
        c = ws.cell(r, 2, v)
        c.font = Fn(bold=True, size=11, color=vc or '000000')
        c.alignment = Alignment(horizontal='right')

    r = 1
    hdr(r, '☀️  Enspecta Leads — Statistik & Prioritetsguide', 15); r += 2

    stats = Counter(row[17] for row in rows)  # Kontakttyp
    hdr(r, 'Kontakttyp'); r += 1
    kv(r, '🟢  Köpare — bor på fastigheten nu',          stats['Köpare'],     GRN2); r += 1
    kv(r, '🟡  Intressent — var på säljarbesiktning',    stats['Intressent'], YLW2); r += 1
    kv(r, '🟠  Säljare — har flyttat',                   stats['Säljare'],    ORG2); r += 1
    kv(r, '    Totalt med kontaktinfo',                   sum(stats.values()), BLU);  r += 2

    prios = Counter(row[0] for row in rows)
    hdr(r, 'Prioritet (byggår)'); r += 1
    kv(r, '🔴  Prio 1 — byggd ≤1980  (direktel/olja, störst behov)', prios[1], RED2); r += 1
    kv(r, '🟡  Prio 2 — byggd 1981–1995 (bra kandidater)',           prios[2], YLW2); r += 1
    kv(r, '⚪  Prio 3 — byggd >1995 (nyare hus)',                    prios[3], '555555'); r += 2

    hdr(r, 'Topp 20 kommuner'); r += 1
    ws.cell(r, 1, 'Kommun').font = Fn(bold=True)
    ws.cell(r, 2, 'Antal').font  = Fn(bold=True)
    ws.cell(r, 2).alignment = Alignment(horizontal='right')
    r += 1
    for kom, cnt in Counter(row[12] for row in rows if row[12]).most_common(20):
        ws.cell(r, 1, kom)
        c = ws.cell(r, 2, cnt)
        c.alignment = Alignment(horizontal='right')
        r += 1
    r += 1

    hdr(r, 'Byggår per decennium'); r += 1
    ws.cell(r, 1, 'Decennium').font = Fn(bold=True)
    ws.cell(r, 2, 'Antal').font     = Fn(bold=True)
    ws.cell(r, 2).alignment = Alignment(horizontal='right')
    r += 1
    dcnt = Counter()
    for row in rows:
        y = extract_year(row[13])
        dcnt[(y//10)*10 if y else 'Okänt'] += 1
    for dec in sorted(d for d in dcnt if d != 'Okänt'):
        ws.cell(r, 1, f'{dec}-talet')
        ws.cell(r, 2, dcnt[dec]).alignment = Alignment(horizontal='right')
        r += 1
    if 'Okänt' in dcnt:
        ws.cell(r, 1, 'Okänt')
        ws.cell(r, 2, dcnt['Okänt']).alignment = Alignment(horizontal='right')
        r += 1
    r += 1

    hdr(r, 'Hur du jobbar med ringlistan'); r += 1
    tips = [
        'Filtrera Prio = 1 + Status = "Ej kontaktad" för att få dagens ringköö.',
        'Filtrera Kommun för att ringa ett område åt gången — effektivare restid.',
        'Skriv datum i "Nästa kontakt" direkt i samtalet.',
        'Bokad möte → raden färgas grön automatiskt.',
        'Ej intresserad → raden tonas ned automatiskt.',
        'Exportera filtrerad vy (Ctrl+Skift+L) för att dela med teamet.',
    ]
    for tip in tips:
        ws.cell(r, 1, f'• {tip}').font = Fn(size=10)
        ws.row_dimensions[r].height = 16
        r += 1

    ws.column_dimensions['A'].width = 56
    ws.column_dimensions['B'].width = 12


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]
    if not args:
        print('Usage: python makeLeads.py enspecta.tab [leads.xlsx]')
        sys.exit(1)

    tab_path = args[0]
    out_path = args[1] if len(args)>1 else tab_path.replace('.tab', '-leads.xlsx')

    print(f'Läser {tab_path} ...')
    by_fastig = parse_tab(tab_path)
    print(f'  {len(by_fastig)} unika fastigheter')

    print('Sorterar och rangordnar ...')
    ORDER = {'Köpare': 0, 'Intressent': 1, 'Säljare': 2}
    rows = []
    stats = Counter()

    for fastig, b in by_fastig.items():
        typ, r, ints = best_contact(b)
        if not r: continue
        stats[typ] += 1
        int0 = ints[0] if ints and typ != 'Intressent' else {}

        bår   = r.get('byggår', '')
        prio  = priority(bår)
        pitch = pitch_hint(bår)

        rows.append([
            prio,                                                   # 0  Prio
            '',                                                     # 1  Status (fylls av säljare)
            '',                                                     # 2  Säljare
            '',                                                     # 3  Nästa kontakt
            '',                                                     # 4  Anteckningar
            r.get('namn', ''),                                      # 5  Namn
            r.get('namn2','') if r.get('namn2')!=r.get('namn') else '', # 6 Namn2
            r.get('telefon', ''),                                   # 7  Telefon
            r.get('email', ''),                                     # 8  E-post
            r.get('adress', ''),                                    # 9  Adress
            r.get('postnr', ''),                                    # 10 Postnr
            title_case(r.get('ort', '')),                           # 11 Ort
            title_case(r.get('kommun', '')),                        # 12 Kommun
            bår,                                                    # 13 Byggår
            r.get('renovat', ''),                                   # 14 Renoverat
            r.get('besdat','') or r.get('besiktningsdatum',''),     # 15 Besiktningsdatum
            pitch,                                                  # 16 Pitch
            typ,                                                    # 17 Kontakttyp
            int0.get('namn',''),                                    # 18 Int namn
            int0.get('telefon',''),                                 # 19 Int tel
            title_case(fastig),                                     # 20 Fastighetsbeteckning
            r.get('case_id',''),                                    # 21 CaseID
        ])

    # Sort: Prio 1 first, then Köpare > Intressent > Säljare
    rows.sort(key=lambda r: (r[0], ORDER.get(r[17], 9)))

    print(f'Bygger Excel ({len(rows)} rader) ...')
    wb = Workbook()
    make_ringlista(wb, rows)
    make_oversikt(wb, rows)
    wb.save(out_path)

    print(f'\n=== Klar ===')
    print(f'  Köpare:      {stats["Köpare"]}')
    print(f'  Intressent:  {stats["Intressent"]}')
    print(f'  Säljare:     {stats["Säljare"]}')
    print(f'  Prio 1:      {sum(1 for r in rows if r[0]==1)}')
    print(f'  Totalt:      {sum(stats.values())}')
    print(f'\nSparad till: {out_path}')
    print('\nTips: Filtrera Prio=1 + Status="Ej kontaktad" för dagens ringköö.')


if __name__ == '__main__':
    main()
