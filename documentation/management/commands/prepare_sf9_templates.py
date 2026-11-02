"""Make ARMS's SF9 templates from DepEd's workbooks in static/forms/schoolforms: the whole workbook, every sheet.

ARMS fills these files as an adviser would: INPUT DATA, the term summaries, the attendance sheet and the learner chosen
on the report card. Their sheets, layout and formulas stay DepEd's. This command only clears the sample data the files
came with and repairs formulas that would print a wrong figure once real data is in them, each listed here:

  Every workbook with a guard on the first learner's row: a report card column stayed blank for the whole class when
  the first boy had no grade or no attendance yet. The guard now looks at the card's own learner.
  Grades 2, 3, 11: the final grade computed from whatever terms were in, and Grade 11 returned #VALUE! until Term 3.
  Now as in DepEd's Grades 4 to 10 file: blank until all three terms are in.
  Grades 2, 3, 4 to 10, 11, 12: the general average ran on any one final grade, and Grade 3 left out Filipino.
  Grades 2, 3, 4 to 10: No. of Class Days was not linked, so days absent printed the days present.
  Grade 11 TechPro and all of Grade 12: days present from July on looked up empty cells (the formula was dragged sideways).
  Kindergarten: No. Days Present was not linked to the attendance sheet.
  Grade 11: in Terms 2 and 3 the Effective Communication and Mabisang Komunikasyon rows read each other's columns.
  Grade 1: July read August's attendance. Kindergarten: a Term 1 rating read Term 2.
  Senior High: female LRNs read an empty column, and the attendance sheet's female names read empty rows in four files.
  Four Senior High files linked to another copy of the workbook, so Excel asked to update links.
  Grades 4 to 10, Term 1 summary: the School ID tested an empty cell.

Run it again when DepEd updates a workbook.
"""
import re
from pathlib import Path

import openpyxl
from django.conf import settings
from django.core.management.base import BaseCommand

from core import xlsx_patch

TERMS = ('TERM 1 SUMMARY', 'TERM 2 SUMMARY', 'TERM 3 SUMMARY')
ATTENDANCE = 'ATTENDANCE RECORD SUMMARY'
SOURCES = {
    'SF9_KINDER': ('KINDER SF9/Kinder E-Class Record with SF9.xlsx', 'SF9 - KINDER'),
    'SF9_G1': ('G1 SF9/Grade 1 ECR, PACE Form, and SF9.xlsx', 'SF9 - GRADE 1'),
    'SF9_G2': ('G2 SF9/Grade 2 School Form 9 (SY 26-27).xlsx', 'SF9 - GRADE 2'),
    'SF9_G3': ('G3 SF9/Grade 3 School Form 9 (SY 26-27).xlsx', 'SF9 - GRADE 3'),
    'SF9_G4_10': ('G4-10 SF9/UPDATED G4-10  School Form 9 (SY 26-27).xlsx', 'SF9 - GRADE 4-10'),
    'SF9_G11_ACADEMIC': ('SSHS SF9/G11 Academic School Form 9 (SY 26-27).xlsx', 'SF9 - GRADE 11 ACADEMIC'),
    'SF9_G11_TECHPRO': ('SSHS SF9/G11 TechPro School Form 9 (SY 26-27).xlsx', 'SF9 - GRADE 11 TechPro'),
    'SF9_G12_ACADEMIC': ('SSHS SF9/G12 Academic School Form 9 (SY 26-27).xlsx', 'SF9 - GRADE 12 ACADEMIC'),
    'SF9_G12_ACADEMIC_CROSS': ('SSHS SF9/G12 Academic with Cross School Form 9 (SY 26-27).xlsx', 'SF9 - GRADE 12 ACADEMIC'),
    'SF9_G12_TECHPRO': ('SSHS SF9/G12 TechPro School Form 9 (SY 26-27).xlsx', 'SF9 - GRADE 12 TechPro'),
    'SF9_G12_TECHPRO_CROSS': ('SSHS SF9/G12 TechPro with Cross School Form 9 (SY 26-27).xlsx', 'SF9 - GRADE 12 TechPro'),
}
LEFT_OF_GRADES = {'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J', 'K'}


def _all_terms(row):
    return f'=IF(COUNT(F{row}:H{row})<3,"",ROUND(AVERAGE(F{row}:H{row}),0))'


def _points(row):
    return f'=IF(J{row}="","",J{row}*I{row})'


def _average(rows, count, final='I', points=None):
    """The general average once every final grade is in."""
    if points:
        return f'=IF(COUNT({rows})<{count},"",ROUND({points},0))'
    return f'=IF(COUNT({rows})<{count},"",ROUND(AVERAGE({rows}),0))'


def _senior_cells(name):
    """The report card's repaired cells in the Senior High workbooks."""
    cells = {}
    if name.startswith('SF9_G11'):
        for row in (33, 36, 37, 38, 39):
            cells.update({f'J{row}': _all_terms(row), f'L{row}': _points(row)})
        for col, sheet in zip('FGH', TERMS):       # Effective Communication, Mabisang Komunikasyon: their own columns
            for row, column in ((33, 9), (34, 10), (35, 11)):
                cells[f'{col}{row}'] = f"=IF(C17=\"\",\"\",VLOOKUP(C17,'{sheet}'!C16:R116,{column},0))"
    if name == 'SF9_G11_ACADEMIC':
        cells.update({f'L{row}': _points(row) for row in (41, 42, 43)})
        cells['J44'] = _average('J33:J39,J41:J43', 8, points='L44/I44')
    elif name == 'SF9_G11_TECHPRO':
        cells.update({'J41': _all_terms(41), 'L41': _points(41), 'J42': _average('J33:J39,J41', 6, points='L42/I42')})
    elif name == 'SF9_G12_ACADEMIC':
        cells.update({f'L{row}': _points(row) for row in range(31, 43)})
        cells['J43'] = _average('J31:J42', 12, points='L43/I43')
    elif name == 'SF9_G12_ACADEMIC_CROSS':
        cells.update({f'L{row}': _points(row) for row in range(31, 40)})
        cells['J41'] = _average('J31:J39', 9, points='L41/I41')
    elif name == 'SF9_G12_TECHPRO':
        cells.update({f'L{row}': _points(row) for row in range(31, 34)})
        cells.update({'I36': '=SUM(I31:I33)', 'L36': '=SUM(L31:L33)', 'J36': _average('J31:J33', 3, points='L36/I36')})
    elif name == 'SF9_G12_TECHPRO_CROSS':
        cells.update({f'L{row}': _points(row) for row in range(31, 37)})
        cells.update({'I38': '=SUM(I31:I36)', 'J38': _average('J31:J36', 6, points='L38/I38')})
    return cells


def _card_cells(name):
    if name == 'SF9_G2':
        cells = {f'I{row}': _all_terms(row) for row in range(33, 38)}
        cells['I38'] = _average('I33:I37', 5)
    elif name == 'SF9_G3':
        cells = {f'I{row}': _all_terms(row) for row in range(33, 39)}
        cells['I39'] = _average('I33:I38', 6)
    elif name == 'SF9_G4_10':
        cells = {'I42': _average('I32:I39', 8)}
    elif name == 'SF9_G1':
        cells = {'Q26': "=VLOOKUP(D15,'G1 - ATTENDANCE SUMMARY'!C13:Q113,5,0)"}
    elif name == 'SF9_KINDER':
        cells = {'O64': "=IFERROR(VLOOKUP($E$18,'TERM 1 SUMMARY'!$C$16:$BU$114,71,FALSE()),\"\")"}
        for row, column in zip(range(82, 94), range(4, 16)):
            cells[f'P{row}'] = f"=IFERROR(VLOOKUP($E$18,'KINDER - ATTENDANCE RECORD SUMM'!$C$13:$Q$113,{column},FALSE()),\"\")"
    else:
        cells = _senior_cells(name)
    if name == 'SF9_G11_TECHPRO' or name.startswith('SF9_G12'):
        pick, row = ('C17', 7) if name == 'SF9_G11_TECHPRO' else ('C15', 5)
        for card, column in zip(('R', 'S', 'T', 'U', 'V', 'W', 'X', 'Y', 'Z', 'AA', 'AB'), range(4, 15)):
            cells[f'{card}{row}'] = f"=IF({pick}=\"\",\"\",IFERROR(VLOOKUP({pick},'{ATTENDANCE}'!$C$14:$P$114,{column},0),\"\"))"
    if name in ('SF9_G2', 'SF9_G3', 'SF9_G4_10'):     # class days, linked as in the Senior High files
        for card, sheet in zip(('R', 'S', 'T', 'U', 'V', 'W', 'X', 'Y', 'Z', 'AA', 'AB'), 'FGHIJKLMNOP'):
            cells[f'{card}5'] = f"='{ATTENDANCE}'!{sheet}117"
    return cells


def _samples(ws, rows, first_col):
    """The sample values left in a sheet: typed numbers and names, from first_col on, in the given rows."""
    cells = {}
    for row in rows:
        for c in ws[row]:
            if c.column >= first_col and c.value is not None and not str(c.value).startswith('='):
                cells[c.coordinate] = None
    return cells


class Command(BaseCommand):
    help = "Make ARMS's SF9 templates from DepEd's workbooks: every sheet kept, sample data cleared, wrong formulas repaired."

    def handle(self, *args, **options):
        source = Path(settings.BASE_DIR) / 'static' / 'forms' / 'schoolforms'
        target = Path(settings.BASE_DIR) / 'documentation' / 'sf_templates' / 'sf9'
        target.mkdir(exist_ok=True)
        for old in target.glob('*.xlsx'):
            old.unlink()
        for name, (path, card) in SOURCES.items():
            data = self._prepared(name, (source / path).read_bytes(), card)
            (target / f'{name}.xlsx').write_bytes(data)
            self.stdout.write(f'{name}.xlsx: {len(data) // 1024} KB')

    def _prepared(self, name, data, card):
        book = openpyxl.load_workbook(__import__('io').BytesIO(data))
        names = book.sheetnames
        summaries = [n for n in names if 'SUMMARY' in n.upper() and 'ATTEND' not in n.upper()]
        attendance = next(n for n in names if 'ATTEND' in n.upper())
        pick = 'E18' if name == 'SF9_KINDER' else 'D15' if name == 'SF9_G1' else 'C15' if name.startswith('SF9_G12') else 'C17'
        if 'INPUT DATA' in names and any("'[1]" in str(c.value) for row in book[attendance].iter_rows(max_row=12) for c in row if c.value):
            data = xlsx_patch.drop_external_links(data, [attendance])
        edits = {card: dict(_card_cells(name)), attendance: {}, 'INPUT DATA': {}}
        edits[card][pick] = None
        days = 116 if name in ('SF9_KINDER', 'SF9_G1') else 117
        first = 6
        edits[attendance].update(_samples(book[attendance], range(13, 115), first))
        edits[attendance].update(_samples(book[attendance], [days], first))
        if name == 'SF9_KINDER':
            edits[attendance].update({f'C{r}': None for r in list(range(13, 63)) + list(range(64, 114)) if book[attendance][f'C{r}'].value})
        elif name.startswith(('SF9_G11_TECHPRO', 'SF9_G12')):
            for n in range(1, 51):                     # the female names, from INPUT DATA's female list
                edits[attendance][f'C{64 + n}'] = f"=IF('INPUT DATA'!Q{10 + n}=\"\",\"\",'INPUT DATA'!Q{10 + n})"
        for col in 'LMNQRST':
            edits['INPUT DATA'].update({f'{col}{r}': None for r in range(11, 61) if book['INPUT DATA'][f'{col}{r}'].value is not None})
        data = xlsx_patch.patch(data, edits, unshare=[card, attendance] + summaries)
        guard = 'C15' if name.startswith('SF9_G12') else 'D15' if name == 'SF9_G1' else 'C17'
        steps = [(r"(?:'TERM \d SUMMARY'|SUMMARY)!(?:K|P|Q|R)1[456]=(&quot;|\")(&quot;|\")", rf'{guard}=\1\2'),
                 (r"'ATTENDANCE RECORD SUMMARY'![A-Z]+14=(&quot;|\")(&quot;|\")", rf'{guard}=\1\2')]
        data = xlsx_patch.rewrite_sheet(data, card, steps)
        if name == 'SF9_G4_10':
            data = xlsx_patch.rewrite_sheet(data, 'TERM 1 SUMMARY', [(r"'INPUT DATA'!F155", "'INPUT DATA'!F15")])
        if name.startswith(('SF9_G11', 'SF9_G12')):        # female LRNs: column R of INPUT DATA, not T
            for sheet in summaries:
                data = xlsx_patch.rewrite_sheet(data, sheet, [(r"'INPUT DATA'!T(\d+)", r"'INPUT DATA'!R\1")])
        return data
