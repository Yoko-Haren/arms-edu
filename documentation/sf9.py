"""SF9, DepEd's own workbooks filled by ARMS, every sheet kept (documentation/sf_templates/sf9).

Kindergarten, Grades 1, 2, 3, 4 to 10, and Grades 11 and 12 by track each have a workbook: Home, INPUT DATA, the term
summaries, the report card, the attendance record. ARMS fills them as an adviser would, and the workbook's own formulas
make the report card: INPUT DATA from the SF1, the summaries from the class records, the attendance sheet from the
adviser's SF2 uploads. A class gets one workbook, where the report card's dropdown chooses the learner; a learner gets
the same workbook with only that learner in it, so a card that goes home does not carry its classmates.

A cell with no grade yet gets an empty text: the workbooks read an empty cell as 0, which would fail the learner. In
Kindergarten and Grade 1 the competencies stay for the adviser to write, in the workbook. These workbooks are for
three-term years; DepEd's set has none for a quarterly or semestral year, and ARMS does not make one up.
"""
import calendar
import re
from datetime import date
from pathlib import Path

from academics import subject_catalog
from core import school_header, xlsx_patch
from core import periods as P

from . import form_data as FD

TEMPLATES = Path(__file__).resolve().parent / 'sf_templates' / 'sf9'
PASSING = 75
TERMS = ('TERM 1 SUMMARY', 'TERM 2 SUMMARY', 'TERM 3 SUMMARY')
ACADEMIC, TECHPRO, IMMERSION = 'academic', 'techpro', 'immersion'
ROOM = 50
MONTH_COLUMNS = dict(zip((6, 7, 8, 9, 10, 11, 12, 1, 2, 3, 4), 'FGHIJKLMNOP'))
TWO_SEPTEMBERS = dict(zip((6, 7, 8, 10, 11, 12, 1, 2, 3, 4), 'FGHKLMNOPQ'))     # I and J: September in Terms 1 and 2

_FRONT = {'region': 'B7', 'division': 'B8', 'district': 'B9', 'place': 'B10', 'school': 'B12', 'year': 'B15', 'pick': 'C17',
          'comments': ('Q15', 'Q19', 'Q23')}
_FRONT_G12 = {'region': 'B5', 'division': 'B6', 'district': 'B7', 'place': 'B8', 'school': 'B10', 'year': 'B13', 'pick': 'C15',
              'comments': ('Q13', 'Q17', 'Q21')}
_G11 = {'sheets': TERMS, 'first': 16, 'second': 67, 'sex': 'J', 'front': _FRONT, 'core': True, 'admitted': 'Q37', 'eligible': 'Q38'}
_G12 = {'sheets': ('SUMMARY',), 'first': 16, 'second': 67, 'sex': 'J', 'front': _FRONT_G12, 'admitted': 'Q35', 'eligible': 'Q36'}
_JUNIOR = {'sheets': TERMS, 'sex': 'J', 'front': _FRONT, 'admitted': 'Q37', 'eligible': 'Q38'}

BOOKS = {
    'SF9_KINDER': {'card': 'SF9 - KINDER', 'sheets': TERMS, 'first': 16, 'second': 66, 'room': 49, 'sex': 'M', 'attendance': 'KINDER - ATTENDANCE RECORD SUMM',
                   'rows': (13, 64), 'days': 116, 'named': True, 'blank': ('N', 'BU'), 'front': {'region': 'C7', 'division': 'C8', 'district': 'C10', 'place': 'C11',
                                                                                                   'school': 'C13', 'year': 'C16', 'pick': 'E18'}},
    'SF9_G1': {'card': 'SF9 - GRADE 1', 'sheets': TERMS, 'first': 14, 'second': 65, 'sex': 'M', 'attendance': 'G1 - ATTENDANCE SUMMARY', 'rows': (13, 64), 'days': 116,
               'blank': ('N', 'Q'), 'front': {'region': 'C5', 'division': 'C6', 'district': 'C7', 'place': 'C8', 'school': 'C10', 'year': 'C13', 'pick': 'D15'}},
    'SF9_G2': dict(_JUNIOR, card='SF9 - GRADE 2', first=14, second=65, areas={'FILIPINO': 'K', 'ENGLISH': 'L', 'MATH': 'M', 'ESP': 'N', 'AP': 'O'},
                   admitted='Q36', eligible='Q37'),
    'SF9_G3': dict(_JUNIOR, card='SF9 - GRADE 3', first=14, second=65,
                   areas={'FILIPINO': 'K', 'ENGLISH': 'L', 'MATH': 'M', 'SCIENCE': 'N', 'ESP': 'O', 'AP': 'P'}),
    'SF9_G4_10': dict(_JUNIOR, card='SF9 - GRADE 4-10', first=15, second=66,
                      areas={'FILIPINO': 'K', 'ENGLISH': 'L', 'MATH': 'M', 'SCIENCE': 'N', 'AP': 'O', 'ESP': 'P', 'TLE': 'Q'}, mapeh=('R', 'S', 'T')),
    'SF9_G11_ACADEMIC': dict(_G11, card='SF9 - GRADE 11 ACADEMIC', slots=[('R', 41, 0, ACADEMIC), ('R', 42, 1, ACADEMIC), ('R', 43, 2, ACADEMIC)]),
    'SF9_G11_TECHPRO': dict(_G11, card='SF9 - GRADE 11 TechPro', slots=[('R', 41, None, TECHPRO)]),
    'SF9_G12_ACADEMIC': dict(_G12, card='SF9 - GRADE 12 ACADEMIC', slots=[(c, r, t, ACADEMIC) for c, r, t in zip(
        'KLMNOPQRSTUV', range(31, 43), (0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2))]),
    'SF9_G12_ACADEMIC_CROSS': dict(_G12, card='SF9 - GRADE 12 ACADEMIC', slots=[(c, r, t, ACADEMIC) for c, r, t in zip(
        'KLMNOPQR', range(31, 39), (0, 0, 0, 0, 1, 1, 1, 1))] + [('S', 39, 2, TECHPRO)]),
    'SF9_G12_TECHPRO': dict(_G12, card='SF9 - GRADE 12 TechPro', slots=[('K', 31, 0, TECHPRO), ('L', 32, 1, TECHPRO), ('M', 33, 2, IMMERSION)]),
    'SF9_G12_TECHPRO_CROSS': dict(_G12, card='SF9 - GRADE 12 TechPro', first=15, second=66, slots=[('K', 31, 0, TECHPRO)] + [
        (c, r, 1, ACADEMIC) for c, r in zip('LMNO', range(32, 36))] + [('P', 36, 2, IMMERSION)]),
}
# Effective Communication and Mabisang Komunikasyon share a row; the summary keeps the two and their term grade
_CORE_COLUMNS = {'ec': 'L', 'mk': 'M', 'math': 'N', 'science': 'O', 'career': 'P', 'history': 'Q'}
_CORE_NAMES = (('ec', r'EFFECTIVE COMM'), ('mk', r'MABISANG'), ('math', r'GENERAL MATH'), ('science', r'GENERAL SCIENCE'),
               ('career', r'LIFE AND CAREER'), ('history', r'KASAYSAYAN|KASANAYAN|PKLP'))


class CardUnavailable(Exception):
    pass


def _slug(text):
    return re.sub(r'[^A-Za-z0-9]+', '-', str(text)).strip('-')


def available(periods):
    return P.prefix(None, periods) == 'T'


def _track(section):
    s = section.strand
    return s.track.track_code.upper() if s and s.track_id else ''


def _kind(subject):
    if 'IMMERSION' in subject.subject_name.upper():
        return IMMERSION
    return TECHPRO if subject_catalog.classify(subject.subject_name, subject.subject_code).key == 'TLE' else ACADEMIC


def template_of(section, subjects=()):
    """Which of DepEd's workbooks a section uses."""
    number = section.grade_level.grade_number or 0
    if number <= 0:
        return 'SF9_KINDER'
    if number == 1:
        return 'SF9_G1'
    if number in (2, 3):
        return f'SF9_G{number}'
    if number <= 10:
        return 'SF9_G4_10'
    techpro = _track(section) in ('TECHPRO', 'TVL')
    if number == 11:
        return 'SF9_G11_TECHPRO' if techpro else 'SF9_G11_ACADEMIC'
    kinds = {_kind(s) for s in subjects}
    if techpro:
        return 'SF9_G12_TECHPRO_CROSS' if ACADEMIC in kinds else 'SF9_G12_TECHPRO'
    return 'SF9_G12_ACADEMIC_CROSS' if TECHPRO in kinds else 'SF9_G12_ACADEMIC'


# ---------------------------------------------------------------------------------------------
# the facts
# ---------------------------------------------------------------------------------------------

def _mapeh_parts(enrollment_ids, subject, periods):
    """{(enrollment id, period id): {'MA': grade, 'PEH': grade}} from the notes a three-term MAPEH upload leaves."""
    from grades import ecr
    from grades.models import GradeComponent
    out = {}
    if subject is None:
        return out
    rows = GradeComponent.objects.filter(enrollment_id__in=enrollment_ids, subject=subject, quarter__in=periods).exclude(remarks='')
    for e, q, remarks in rows.values_list('enrollment_id', 'quarter_id', 'remarks'):
        for piece in remarks.split('·')[-1].split(','):
            m = re.fullmatch(r'\s*(.*?)\s+(\d{2,3})\s*', piece)
            key = ecr.term_mapeh_part(m.group(1)) if m else None
            if key:
                out.setdefault((e, q), {})[key] = int(m.group(2))
    return out


def _senior_grades(section, sy, people, periods):
    """[(subject, kind)] of the section, the terms each is taught in, and {(enrollment, subject, period): grade}."""
    ids = [p['enrollment'].id for p in people]
    classes = FD.section_subjects(section, sy, FD.ids_of_section(section, sy))
    grades = FD.quarter_grades(ids, [s.id for s, _ in classes], periods)
    everyone = FD.quarter_grades(FD.ids_of_section(section, sy), [s.id for s, _ in classes], periods)
    taught = {s.id: [i for i, q in enumerate(periods) if any(k[1] == s.id and k[2] == q.id for k in everyone)] for s, _ in classes}
    return [s for s, _ in classes], taught, grades


def place_electives(book, electives, taught):
    """{slot: subject} and the subjects with no slot. A slot is a column of the summary and a row of the card, for one
    term or all of them; a subject goes where its grades are."""
    free = list(book['slots'])
    placed, left = {}, []
    for subject in electives:
        kind = _kind(subject)
        terms = taught.get(subject.id) or []
        slot = next((s for s in free if s[3] == kind and (s[2] is None or s[2] in terms)), None) or \
            next((s for s in free if s[3] == kind and not terms), None)
        if slot is None:
            left.append(subject)
            continue
        free.remove(slot)
        placed[slot] = subject
    return placed, left


def fit(section, sy, periods):
    """What the adviser should know about the section's workbook: its name, and the subjects DepEd's form has no place for."""
    if section.grade_level.is_senior_high:
        subjects, taught, _ = _senior_grades(section, sy, [], periods)
        name = template_of(section, subjects)
        book = BOOKS[name]
        electives = [s for s in subjects if not (book.get('core') and _core_key(s))]
        return name, [s.subject_name for s in place_electives(book, electives, taught)[1]]
    return template_of(section), []


def _core_key(subject):
    return next((key for key, pattern in _CORE_NAMES if re.search(pattern, subject.subject_name.upper())), None)


def _header(section, sy, info):
    return {'region': info['region_label'] or 'Region',
            'division': f'SCHOOLS DIVISION OF {info["division"].upper()}' if info['division'] else 'SCHOOLS DIVISION OF _____',
            'district': info['district'] or 'District', 'place': info['place'] or 'Municipality/City, Province',
            'school': section.school.school_name.upper(), 'year': f'School Year {sy.year_label}'}


def _input_cells(section, sy, info, males, females):
    grade = section.grade_level.grade_number
    cells = {'F10': info['region_label'] or None, 'F11': info['division'] or None, 'F12': info['place'] or None, 'F13': info['district'] or None,
             'F15': section.school.school_id, 'F16': section.school.school_name.upper(), 'F18': sy.year_label, 'F19': info['head'] or None,
             'F26': FD.adviser_of(section) or None, 'F27': grade if grade else 'Kinder'}
    if section.grade_level.is_senior_high:
        cells.update({'F28': 'TechPro' if _track(section) in ('TECHPRO', 'TVL') else 'Academic', 'F29': section.section_name.upper()})
    else:
        cells['F28'] = section.section_name.upper()
    for block, name, lrn, birth in ((males, 'L', 'M', 'N'), (females, 'Q', 'R', 'S')):
        for i, p in enumerate(block):
            cells.update({f'{name}{11 + i}': p['short'], f'{lrn}{11 + i}': p['lrn'] or None, f'{birth}{11 + i}': p['birth_date']})
    return cells


def _rows(book, males, females):
    """(row in a summary, learner), male block then female block."""
    return [(book['first'] + i, p) for i, p in enumerate(males)] + [(book['second'] + i, p) for i, p in enumerate(females)]


def _blank(value):
    """An empty text, not an empty cell: DepEd's lookups read an empty cell as 0."""
    return '' if value is None else value


def _junior_cells(section, sy, book, rows, periods):
    """{sheet: cells}: every learner's grade in every learning area, a term summary each."""
    people = [p for _, p in rows]
    record = FD.jhs_record(section, sy, people)
    areas = record['areas']
    mapeh = areas.get('MAPEH')
    parts = _mapeh_parts([p['enrollment'].id for p in people], mapeh[0] if mapeh else None, periods) if book.get('mapeh') else {}
    out = {}
    for t, sheet in enumerate(book['sheets']):
        cells = out.setdefault(sheet, {})
        for row, p in rows:
            for key, col in book['areas'].items():
                cells[f'{col}{row}'] = _blank(p['areas'][key]['quarters'][t] if key in areas else None)
            if book.get('mapeh'):
                ma, peh, whole = book['mapeh']
                mine = parts.get((p['enrollment'].id, periods[t].id), {})
                grade = p['areas']['MAPEH']['quarters'][t] if 'MAPEH' in areas else None
                cells[f'{ma}{row}'] = _blank(mine.get('MA'))
                cells[f'{peh}{row}'] = _blank(mine.get('PEH'))
                if len(mine) < 2:                          # only the MAPEH grade is known: it goes in the term grade
                    cells[f'{whole}{row}'] = _blank(grade)
    return out


def _senior_cells(section, sy, book, rows, periods, name):
    """{sheet: cells} of the summaries, and the card's subject names."""
    people = [p for _, p in rows]
    subjects, taught, grades = _senior_grades(section, sy, people, periods)
    core = {_core_key(s): s for s in subjects if book.get('core') and _core_key(s)}
    electives = [s for s in subjects if s not in core.values()]
    placed, _ = place_electives(book, electives, taught)
    out = {sheet: {} for sheet in book['sheets']}

    def grade(p, subject, t):
        return grades.get((p['enrollment'].id, subject.id, periods[t].id))
    for row, p in rows:
        if book.get('core'):
            for t, sheet in enumerate(book['sheets']):
                cells = out[sheet]
                for key, col in _CORE_COLUMNS.items():
                    cells[f'{col}{row}'] = _blank(grade(p, core[key], t)) if key in core else ''
                pair = [grade(p, core[k], t) for k in ('ec', 'mk') if k in core]
                both = pair and all(v is not None for v in pair)
                cells[f'K{row}'] = int(FD.round_half_up(sum(pair) / len(pair))) if both else ''
                for (col, _, slot_term, _), subject in placed.items():
                    if slot_term is None or slot_term == t:
                        cells[f'{col}{row}'] = _blank(grade(p, subject, t))
                cells.setdefault(f'R{row}', '')
        else:
            cells = out[book['sheets'][0]]
            for slot in book['slots']:
                subject = placed.get(slot)
                cells[f'{slot[0]}{row}'] = _blank(grade(p, subject, slot[2])) if subject else ''
    return out, {f'B{row}': subject.subject_name for (_, row, _, _), subject in placed.items()}


def _attendance_cells(sy, book, males, females, periods):
    from attendance.models import AttendanceSummary
    ids = [p['enrollment'].id for p in males + females]
    summaries = list(AttendanceSummary.objects.filter(enrollment_id__in=ids, school_year=sy))
    mine = {(s.enrollment_id, s.month): s for s in summaries}
    days = {}
    for s in summaries:
        days[s.month] = max(days.get(s.month, 0), s.total_school_days)
    days_row = book.get('days', 117)
    two = days_row == 116
    columns = TWO_SEPTEMBERS if two else MONTH_COLUMNS
    male_row, female_row = book.get('rows', (14, 65))
    cells = {}
    share = _september_share(sy, periods, days.get(9)) if two else None
    for block, first in ((males, male_row), (females, female_row)):
        for i, p in enumerate(block):
            row = first + i
            if book.get('named'):
                cells[f'C{row}'] = p['short']
            for month, col in columns.items():
                s = mine.get((p['enrollment'].id, month))
                cells[f'{col}{row}'] = s.days_present if s else ''
            if two:
                s = mine.get((p['enrollment'].id, 9))
                first_part = round(s.days_present * share) if s and share is not None else None
                cells.update({f'I{row}': _blank(first_part), f'J{row}': _blank(s.days_present - first_part if first_part is not None else None)})
    for month, col in columns.items():
        cells[f'{col}{days_row}'] = days.get(month, '')
    if two and share is not None:
        cells.update({f'I{days_row}': round(days[9] * share), f'J{days_row}': days[9] - round(days[9] * share)})
    return cells


def _september_share(sy, periods, days):
    """The part of September in Term 1: Kindergarten and Grade 1 list the month once per term; attendance is per month."""
    if not days:
        return None
    year, _ = FD.month_of(sy, 9)
    school_days = FD.month_days(year, 9, sy)
    return sum(1 for d in school_days if d <= periods[0].date_end) / len(school_days) if school_days else None


def _card_cells(section, sy, book, info, chosen, name_cells):
    front = book['front']
    cells = {front[k]: v for k, v in _header(section, sy, info).items()}
    cells.update(name_cells)
    cells[front['pick']] = chosen['short'] if chosen else None
    return cells


def _learner_cells(section, book, p, periods):
    """What the card itself holds for one learner: the adviser's comments and the certificate of transfer."""
    if 'comments' not in book['front']:
        return {}
    cells = {}
    for cell, term, note in zip(book['front']['comments'], periods, p['comments']):
        cells[cell] = f'{P.name(term)}\n{note}' if note else P.name(term)
    number = section.grade_level.grade_number
    cells[book['admitted']] = f'Admitted to Grade: {number}'
    if p['promoted'] and number and number < 12:
        cells[book['eligible']] = f'Eligible for Admission to Grade: {number + 1}'
    return cells


def _people(section, sy, only=None):
    from documentation.models import LearnerComment
    people = FD.learners(section, sy)
    if only is not None:
        people = [p for p in people if p['enrollment'].id == only]
    if not people:
        raise CardUnavailable('This section has no learner to make a report card for.')
    return people, {(c.enrollment_id, c.quarter_id): c.comment for c in LearnerComment.objects.filter(enrollment_id__in=[p['enrollment'].id for p in people])}


def _workbook(section, sy, only=None):
    """(bytes of the workbook, the learners in it). only: one enrollment id, for that learner's own copy."""
    periods = FD.periods_of(sy)
    if not available(periods):
        raise CardUnavailable(f'SY {sy.year_label} is not a three-term year. The SF9 workbooks in ARMS are DepEd\'s three-term ones; '
                              'there is no official quarterly or semestral SF9 in the set yet.')
    people, notes = _people(section, sy, only)
    info = FD.school_info(section.school, sy)
    subjects = FD.section_subjects(section, sy, FD.ids_of_section(section, sy)) if section.grade_level.is_senior_high else ()
    name = template_of(section, [s for s, _ in subjects])
    book = BOOKS[name]
    males, females = FD.split_sex(people)
    room = book.get('room', ROOM)
    if len(males) > room or len(females) > room:
        raise CardUnavailable(f"DepEd's SF9 workbook has room for {room} male and {room} female learners.")
    rows = _rows(book, males, females)
    number = section.grade_level.grade_number or 0
    edits = {'INPUT DATA': _input_cells(section, sy, info, males, females)}
    name_cells = {}
    if section.grade_level.is_senior_high:
        summaries, name_cells = _senior_cells(section, sy, book, rows, periods, name)
    elif number >= 2:
        summaries = _junior_cells(section, sy, book, rows, periods)
    else:
        summaries = {sheet: {} for sheet in book['sheets']}
        low, high = xlsx_patch.col_number(book['blank'][0]), xlsx_patch.col_number(book['blank'][1])
        columns = [xlsx_patch.col_letters(n) for n in range(low, high + 1)] if number <= 0 else list(book['blank'])
        for sheet in book['sheets']:
            for row, _ in rows:
                summaries[sheet].update({f'{col}{row}': '' for col in columns})
    for sheet in book['sheets']:
        for row, p in rows:
            summaries[sheet][f'{book["sex"]}{row}'] = p['sex']
    edits.update(summaries)
    edits[book.get('attendance', 'ATTENDANCE RECORD SUMMARY')] = _attendance_cells(sy, book, males, females, periods)
    chosen = (males + females)[0]
    card = _card_cells(section, sy, book, info, chosen, name_cells)
    if only is not None:
        for p in people:
            p['comments'] = [notes.get((p['enrollment'].id, q.id), '') for q in periods]
            p['promoted'] = p.get('action') == 'PROMOTED'
        card.update(_learner_cells(section, book, people[0], periods))
    edits[book['card']] = card
    data = xlsx_patch.patch((TEMPLATES / f'{name}.xlsx').read_bytes(), edits, active=book['card'])
    return xlsx_patch.replace_shape(data, 'School Logo', school_header.logo(section.school)), people


def _class_name(section, sy):
    return f'{_slug(section.grade_level.grade_name)}-{_slug(section.section_name)}_SY{sy.year_label}'


def learner_file(p, section, sy):
    return f'SF9_{_slug(p["last"])}-{_slug(p["first"])}_{_class_name(section, sy)}.xlsx'


def build_class(section, sy):
    """(bytes, file name): the section's workbook, every learner in it."""
    data, _ = _workbook(section, sy)
    return data, f'SF9_{_class_name(section, sy)}.xlsx'


def build_one(section, sy, enrollment_id):
    """(bytes, file name): the same workbook with one learner in it."""
    data, people = _workbook(section, sy, enrollment_id)
    return data, learner_file(people[0], section, sy)


def folder_of(section, sy):
    return f'SF9_{_class_name(section, sy)}'
