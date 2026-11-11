"""What advisers upload so the school forms are complete: SF2 attendance, SF3 books, SF8 weight and height.

Like grades, an upload never writes straight away: the file is read, every row is matched to a learner of the section
(the same matching as e-class records), the adviser checks what was found, and only then is it saved. A month or a
stage uploaded again replaces the earlier one. The forms read what is saved here: SF2 becomes SF4's attendance and the
SF9 attendance record, SF3 keeps its dates, SF8 gets its BMI. The files may be the ones ARMS made or DepEd's own
(.xlsx or .xls): columns are found by their headings, not their letters.
"""
import calendar
import io
import re
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

import openpyxl
import xlrd
from django.db import transaction
from django.utils import timezone

from grades import ecr

from . import form_data as FD

MAX_BYTES = 15 * 1024 * 1024
WEEK_LETTERS = ('M', 'T', 'W', 'TH', 'F', 'S')
KINDS = {'SF2': 'attendance', 'SF3': 'books', 'SF8': 'weight and height'}
MONTHS = {name.upper(): i for i, name in enumerate(calendar.month_name) if name}


class UploadProblem(Exception):
    """The file cannot be used at all."""


def _text(value):
    return re.sub(r'\s+', ' ', str(value)).strip() if value not in (None, '') else ''


def _sheet_rows(uploaded, names):
    """The rows of the sheet for this form (a workbook may hold every form), as lists of cell values."""
    if uploaded.size > MAX_BYTES:
        raise UploadProblem('The file is larger than 15 MB.')
    data = uploaded.read()
    title = uploaded.name.lower()
    try:
        if title.endswith('.xls'):
            book = xlrd.open_workbook(file_contents=data)
            sheets = [(s.name, [_xls_row(s, r, book.datemode) for r in range(s.nrows)]) for s in book.sheets()]
        elif title.endswith(('.xlsx', '.xlsm')):
            book = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
            sheets = []
            for ws in book.worksheets:
                if ws.sheet_state == 'visible':
                    ws.reset_dimensions()                  # a file saved by another program may state the wrong size
                    sheets.append((ws.title, [list(row) for row in ws.iter_rows(values_only=True)]))
            book.close()
        else:
            raise UploadProblem('Upload the Excel file of the form (.xlsx or .xls).')
    except UploadProblem:
        raise
    except Exception:
        raise UploadProblem('This file cannot be read. Save it again from Excel and upload it.')
    wanted = [rows for name, rows in sheets if any(n in name.upper().replace(' ', '') for n in names)]
    return wanted[0] if wanted else sheets[0][1]


def _xls_row(sheet, r, datemode):
    out = []
    for c, value in enumerate(sheet.row_values(r)):
        if sheet.cell_type(r, c) == xlrd.XL_CELL_DATE:
            value = xlrd.xldate_as_datetime(value, datemode)
        out.append(value)
    return out


def _name_column(rows, start, limit):
    """The column left of `limit` that holds names, read from the rows below the headings."""
    counts = {}
    for row in rows[start:start + 60]:
        for col, value in enumerate(row[:limit]):
            if isinstance(value, str) and ',' in value and len(value) > 4:
                counts[col] = counts.get(col, 0) + 1
    if not counts:
        raise UploadProblem('No learner names were found. Keep the names as "LAST NAME, FIRST NAME".')
    return max(counts, key=counts.get)


def _rows_of_learners(rows, start, limit):
    """(row number, row, sex) of every learner row, following the MALE and FEMALE blocks."""
    name_col = _name_column(rows, start, limit)
    sex = 'M'
    for r in range(start, len(rows)):
        row = rows[r]
        left = ' '.join(_text(v) for v in row[:limit]).upper()
        if 'COMBINED' in left or 'GUIDELINES' in left or left.startswith('SEX') or 'SUMMARY TABLE' in left:
            break
        if 'TOTAL' in left:
            if 'FEMALE' not in left and 'MALE' in left:
                sex = 'F'
            continue
        if re.fullmatch(r'(NO\.? )?MALE', left.strip()):
            sex = 'M'
            continue
        if left.strip() == 'FEMALE':
            sex = 'F'
            continue
        name = row[name_col] if name_col < len(row) else None
        if isinstance(name, str) and ',' in name:
            yield r, row, sex, _text(name)


def _cell(row, col):
    return row[col] if col is not None and col < len(row) else None


def _finish(rows, roster):
    """Match the rows to the section and say who is missing."""
    ecr.match_rows(rows, roster)
    for i, row in enumerate(rows):
        row['index'] = i
        row['reasons'] = row.get('reasons', [])
    found = {row.get('enrollment') for row in rows}
    return [p for p in roster if p['active'] and p['id'] not in found]


# ---------------------------------------------------------------------------------------------
# SF2
# ---------------------------------------------------------------------------------------------

def _mark(value):
    text = _text(value).upper()
    if text in ('', '-', 'P', 'PRESENT', '/', '✓'):
        return None
    if text in ('X', 'A', 'ABSENT'):
        return 'Absent'
    if text in ('T', 'L', 'H', 'LATE', 'TARDY'):
        return 'Late'
    if text in ('C', 'CC', 'CUTTING'):
        return 'Cutting'
    return '?'


def _number(value):
    try:
        return int(float(_text(value).replace(',', '')))
    except ValueError:
        return None


def _month_columns(rows, week_row, columns, year, month, sy):
    """{column: date}, from the dates the form shows, else the calendar laid out week by week as ARMS does."""
    dated = {}
    for col in columns:
        value = _cell(rows[week_row - 1], col) if week_row else None
        day = value.day if isinstance(value, (date, datetime)) else _number(value)
        if day and 1 <= day <= calendar.monthrange(year, month)[1]:
            dated[col] = date(year, month, day)
    if dated:
        return dated
    weeks, current = [], []
    for col in columns:
        if _text(rows[week_row][col]).upper() == 'M' and current:
            weeks.append(current)
            current = []
        current.append(col)
    weeks.append(current)
    days = FD.month_days(year, month, sy)
    monday = days[0].toordinal() - days[0].weekday() if days else 0
    for d in days:
        week = (d.toordinal() - monday) // 7
        if week < len(weeks) and d.weekday() < len(weeks[week]):
            dated[weeks[week][d.weekday()]] = d
    return dated


def read_sf2(uploaded, section, sy, month, roster):
    """One month of attendance: each learner's marks per day, or the month's totals when only those are filled."""
    rows = _sheet_rows(uploaded, ('SF2', 'SF-2', 'SCHOOLFORM2', 'SHSF-2'))
    year, _ = FD.month_of(sy, month)
    for row in rows[:10]:
        for value in row:
            m = re.search(r'\b([A-Za-z]+)\s+(20\d\d)\b', _text(value))
            if m and m.group(1).upper() in MONTHS and (MONTHS[m.group(1).upper()], int(m.group(2))) != (month, year):
                raise UploadProblem(f'This file is for {m.group(0)}. Choose that month, or upload the {calendar.month_name[month]} {year} SF2.')
    week_row, columns = None, []
    for r, row in enumerate(rows[:30]):
        found = [c for c, value in enumerate(row) if _text(value).upper() in WEEK_LETTERS]
        if len(found) >= 10:
            week_row, columns = r, found
            break
    if week_row is None:
        raise UploadProblem('This is not an SF2: its row of days (M T W TH F) was not found.')
    heads = {_text(value).upper(): c for c, value in enumerate(rows[week_row])}
    absent_col, tardy_col = heads.get('ABSENT'), heads.get('TARDY')
    remark_col = next((c for r in range(max(0, week_row - 3), week_row + 1) for c, value in enumerate(rows[r]) if 'REMARKS' in _text(value).upper()), None)
    dated = _month_columns(rows, week_row, columns, year, month, sy)
    school_days = None
    for row in rows:
        for c, value in enumerate(row):
            if 'DAYS OF CLASSES' in _text(value).upper():
                school_days = next((n for n in (_number(v) for v in row[c + 1:c + 6]) if n), None)
    school_days = school_days or len(FD.month_days(year, month, sy))
    out, odd = [], set()
    for _, row, sex, name in _rows_of_learners(rows, week_row + 1, columns[0]):
        marks = {}
        for col, day in dated.items():
            status = _mark(_cell(row, col))
            if status == '?':
                odd.add(_text(_cell(row, col)))
            elif status:
                marks[day.isoformat()] = status
        absent = sum(1 for s in marks.values() if s == 'Absent')
        tardy = sum(1 for s in marks.values() if s in ('Late', 'Cutting'))
        if not marks:                                      # only the month's totals are filled in
            absent, tardy = _number(_cell(row, absent_col)) or 0, _number(_cell(row, tardy_col)) or 0
        remarks = _text(_cell(row, remark_col))
        out.append({'name': name, 'lrn': '', 'sex': sex, 'marks': marks, 'absent': absent, 'tardy': tardy, 'remarks': remarks,
                    'summary': f'{absent} absent, {tardy} tardy', 'note': remarks, 'moved': bool(re.search(r'\b(DRP|T/?O|T/?I)\b', remarks.upper()))})
    if not out:
        raise UploadProblem('No learner rows were found in this SF2.')
    checks = []
    if odd:
        checks.append(('warn', 'Marks ARMS did not understand were left out: ' + ', '.join(sorted(odd)[:6]) + '. Use x for absent and T for tardy.'))
    over = [r['name'] for r in out if r['absent'] > school_days]
    if over:
        checks.append(('warn', f'{len(over)} learner{"s have" if len(over) > 1 else " has"} more absences than the {school_days} days of classes.'))
    missing = _finish(out, roster)
    return {'kind': 'SF2', 'month': month, 'year': year, 'label': f'{calendar.month_name[month]} {year}', 'school_days': school_days, 'rows': out,
            'checks': checks, 'missing': [p['id'] for p in missing], 'facts': [('Month', f'{calendar.month_name[month]} {year}'), ('Days of classes', school_days)]}


def _longest_run(days):
    best, run, before = 0, 0, None
    for d in sorted(days):
        school_days_between = before is not None and (d - before).days <= (3 if before.weekday() == 4 else 1)
        run = run + 1 if school_days_between else 1
        best, before = max(best, run), d
    return best


@transaction.atomic
def save_sf2(section, sy, result, plan, user):
    """Replace the month's attendance of the learners in the file."""
    from attendance.models import AttendanceRecord, AttendanceSummary, SF2AttendanceReport
    month, year, days = result['month'], result['year'], result['school_days']
    now = timezone.now()
    for row, enrollment_id in plan:
        AttendanceRecord.objects.filter(enrollment_id=enrollment_id, date__year=year, date__month=month).delete()
        AttendanceRecord.objects.bulk_create([
            AttendanceRecord(enrollment_id=enrollment_id, date=date.fromisoformat(day), status=status, marked_by=user, updated_by=user)
            for day, status in row['marks'].items()])
        absent = row['absent']
        rate = round(Decimal(absent) * 100 / days, 2) if days else None
        AttendanceSummary.objects.update_or_create(enrollment_id=enrollment_id, school_year=sy, month=month, defaults={
            'total_school_days': days, 'days_present': max(0, days - absent), 'days_absent': absent, 'days_late': row['tardy'],
            'absence_rate_percent': rate, 'is_at_risk': bool(rate is not None and rate >= 20),
            'consecutive_absences': _longest_run([date.fromisoformat(d) for d, s in row['marks'].items() if s == 'Absent'])})
    term = next((q for q in FD.periods_of(sy) if q.date_start <= date(year, month, 28) and q.date_end >= date(year, month, 1)), None)
    SF2AttendanceReport.objects.update_or_create(section=section, school_year=sy, report_month=month, defaults={
        'report_year': year, 'quarter': term, 'total_enrollment': len(plan), 'status': 'Submitted', 'prepared_by': user, 'prepared_at': now})
    return len(plan)


# ---------------------------------------------------------------------------------------------
# SF3
# ---------------------------------------------------------------------------------------------

def _date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = _text(value)
    for pattern in ('%m/%d/%Y', '%m/%d/%y', '%Y-%m-%d', '%m-%d-%Y', '%m-%d-%y'):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    if isinstance(value, (int, float)) and 30000 < value < 60000:  # a date Excel kept as its serial number
        return date(1899, 12, 30) + timedelta(days=int(value))
    return None


def read_sf3(uploaded, section, sy, roster):
    rows = _sheet_rows(uploaded, ('SF3', 'SF-3', 'SCHOOLFORM3', 'SHSF-3'))
    head = None
    for r, row in enumerate(rows[:30]):
        found = [c for c, value in enumerate(row) if _text(value).upper() == 'ISSUED']
        if len(found) >= 2:
            head, issued = r, found
            break
    if head is None:
        raise UploadProblem('This is not an SF3: its Issued and Returned columns were not found.')
    returned = [c for c, value in enumerate(rows[head]) if _text(value).upper() == 'RETURNED']
    titles = {}
    for col, back in zip(issued, returned):
        for r in range(head - 1, max(head - 5, -1), -1):
            text = _text(_cell(rows[r], col))
            if text and 'DATE' not in text.upper():
                titles[col] = (text, back)
                break
    remark_col = next((c for r in range(max(0, head - 4), head + 1) for c, value in enumerate(rows[r]) if 'REMARKS' in _text(value).upper()), None)
    out, bad = [], 0
    blank = ('SUBJECT AREA', 'BOOK / MODULE', 'BOOK/MODULE')
    for _, row, sex, name in _rows_of_learners(rows, head + 1, issued[0]):
        books = []
        for col, (title, back) in titles.items():
            a, b = _cell(row, col), _cell(row, back)
            if a in (None, '') and b in (None, ''):
                continue
            got, gave = _date(a), _date(b)
            bad += (a not in (None, '') and got is None) + (b not in (None, '') and gave is None)
            if any(title.upper().startswith(w) for w in blank):
                bad += 1
                continue
            books.append({'title': title[:150], 'issued': got.isoformat() if got else '', 'returned': gave.isoformat() if gave else ''})
        remarks = _text(_cell(row, remark_col))
        out.append({'name': name, 'lrn': '', 'sex': sex, 'books': books, 'remarks': remarks[:255], 'note': remarks,
                    'summary': f'{len(books)} book{"" if len(books) == 1 else "s"}, {sum(1 for b in books if b["returned"])} returned'})
    if not out:
        raise UploadProblem('No learner rows were found in this SF3.')
    checks = []
    if bad:
        checks.append(('warn', f'{bad} cell{"s" if bad > 1 else ""} could not be read: a date not written as mm/dd/yyyy, or a column without a book title. Those were left out.'))
    if not any(r['books'] for r in out):
        checks.append(('warn', 'No issued or returned date was found in this file.'))
    missing = _finish(out, roster)
    names = sorted({b['title'] for r in out for b in r['books']})
    return {'kind': 'SF3', 'label': 'Books issued and returned', 'rows': out, 'checks': checks, 'missing': [p['id'] for p in missing],
            'facts': [('Book titles', len(names))]}


@transaction.atomic
def save_sf3(section, sy, result, plan, user):
    from documentation.models import BookRecord
    for row, enrollment_id in plan:
        BookRecord.objects.filter(enrollment_id=enrollment_id).delete()
        BookRecord.objects.bulk_create([
            BookRecord(enrollment_id=enrollment_id, title=b['title'], date_issued=date.fromisoformat(b['issued']) if b['issued'] else None,
                       date_returned=date.fromisoformat(b['returned']) if b['returned'] else None, remarks=row['remarks'], recorded_by=user)
            for b in {b['title']: b for b in row['books']}.values()])
    return len(plan)


# ---------------------------------------------------------------------------------------------
# SF8
# ---------------------------------------------------------------------------------------------

def _decimal(value):
    try:
        return Decimal(_text(value).replace(',', ''))
    except InvalidOperation:
        return None


def read_sf8(uploaded, section, sy, stage, roster):
    rows = _sheet_rows(uploaded, ('NUTRITION', 'SF8', 'SF-8', 'SCHOOLFORM8'))
    head = None
    for r, row in enumerate(rows[:30]):
        names = [_text(v).upper() for v in row]
        if any(n.startswith('WEIGHT') for n in names) and any(n.startswith('HEIGHT') for n in names):
            head = r
            break
    if head is None:
        raise UploadProblem('This is not an SF8: its Weight and Height columns were not found.')

    def column(*starts):
        for r in (head, head + 1):
            for c, value in enumerate(rows[r]):
                if any(_text(value).upper().startswith(s) for s in starts):
                    return c
        return None
    weight_col, height_col, bmi_col = column('WEIGHT'), column('HEIGHT'), column('BMI CATEGORY')
    hfa_col, remark_col = column('HEIGHT FOR AGE'), column('REMARKS')
    out = []
    first = column('BIRTH', 'WEIGHT')
    for _, row, sex, name in _rows_of_learners(rows, head + 2, first):
        weight, height = _decimal(_cell(row, weight_col)), _decimal(_cell(row, height_col))
        if height is not None and height > 3:              # written in centimeters
            height = (height / 100).quantize(Decimal('0.01'))
        problems = []
        if weight is not None and not 10 <= weight <= 200:
            problems.append(f'A weight of {weight} kg looks wrong.')
        if height is not None and not Decimal('0.8') <= height <= Decimal('2.3'):
            problems.append(f'A height of {height} m looks wrong.')
        if not weight and not height:
            continue
        bmi = round(float(weight) / float(height) ** 2, 2) if weight and height else None
        category, hfa = _text(_cell(row, bmi_col))[:40], _text(_cell(row, hfa_col))[:40]
        out.append({'name': name, 'lrn': '', 'sex': sex, 'weight': str(weight or ''), 'height': str(height or ''), 'bmi_category': category,
                    'height_for_age': hfa, 'remarks': _text(_cell(row, remark_col))[:255], 'note': ' '.join(problems),
                    'summary': f'{weight or "no weight"} kg, {height or "no height"} m' + (f', BMI {bmi}' if bmi else '') + (f', {category}' if category else '')})
    if not out:
        raise UploadProblem('No weight or height was found. Fill in the Weight and Height columns and upload again.')
    checks = []
    if sum(1 for r in out if r['note']):
        checks.append(('warn', 'Some weights or heights look wrong. Check the rows marked below.'))
    missing = _finish(out, roster)
    stages = dict(FD_STAGES)
    return {'kind': 'SF8', 'stage': stage, 'label': stages[stage], 'rows': out, 'checks': checks, 'missing': [p['id'] for p in missing],
            'facts': [('Weighing', stages[stage])]}


FD_STAGES = (('BOSY', 'Beginning of School Year'), ('EOSY', 'End of School Year'))


@transaction.atomic
def save_sf8(section, sy, result, plan, user):
    from documentation.models import LearnerNutrition
    for row, enrollment_id in plan:
        LearnerNutrition.objects.update_or_create(enrollment_id=enrollment_id, stage=result['stage'], defaults={
            'weight_kg': Decimal(row['weight']) if row['weight'] else None, 'height_m': Decimal(row['height']) if row['height'] else None,
            'bmi_category': row['bmi_category'], 'height_for_age': row['height_for_age'], 'remarks': row['remarks'], 'recorded_by': user})
    return len(plan)


SAVERS = {'SF2': save_sf2, 'SF3': save_sf3, 'SF8': save_sf8}


def _dec(value):
    return Decimal(str(value)).normalize() if value not in (None, '') else None


def mark_duplicates(result, sy):
    """No record twice (objective 1): a matched row whose form data ARMS already keeps exactly as in the file is marked
    'duplicate' and not saved again (SF2: the month's marks and totals; SF3: the books; SF8: the stage's measures). When
    every matched row is one, the file is refused as a duplicate. Returns how many rows are duplicates."""
    rows = [r for r in result['rows'] if r.get('status') == 'matched' and r.get('enrollment')]
    ids = [r['enrollment'] for r in rows]
    same = set()
    if result['kind'] == 'SF2' and ids:
        from attendance.models import AttendanceRecord, AttendanceSummary
        month, year, days = result['month'], result['year'], result['school_days']
        totals = {s.enrollment_id: s for s in AttendanceSummary.objects.filter(enrollment_id__in=ids, school_year=sy, month=month)}
        marks = {}
        for e, d, status in AttendanceRecord.objects.filter(enrollment_id__in=ids, date__year=year, date__month=month).values_list(
                'enrollment_id', 'date', 'status'):
            marks.setdefault(e, {})[d.isoformat()] = status
        for r in rows:
            s = totals.get(r['enrollment'])
            if s and s.total_school_days == days and s.days_absent == r['absent'] and s.days_late == r['tardy'] \
                    and marks.get(r['enrollment'], {}) == r['marks']:
                same.add(r['index'])
    elif result['kind'] == 'SF3' and ids:
        from documentation.models import BookRecord
        books = {}
        for e, title, issued, returned in BookRecord.objects.filter(enrollment_id__in=ids).values_list(
                'enrollment_id', 'title', 'date_issued', 'date_returned'):
            books.setdefault(e, set()).add((title, issued.isoformat() if issued else '', returned.isoformat() if returned else ''))
        for r in rows:
            mine = {(b['title'], b['issued'] or '', b['returned'] or '') for b in {b['title']: b for b in r['books']}.values()}
            if mine and books.get(r['enrollment']) == mine:
                same.add(r['index'])
    elif result['kind'] == 'SF8' and ids:
        from documentation.models import LearnerNutrition
        saved = {n.enrollment_id: n for n in LearnerNutrition.objects.filter(enrollment_id__in=ids, stage=result['stage'])}
        for r in rows:
            n = saved.get(r['enrollment'])
            if n and _dec(n.weight_kg) == _dec(r['weight']) and _dec(n.height_m) == _dec(r['height']) \
                    and (n.bmi_category or '') == (r['bmi_category'] or '') and (n.height_for_age or '') == (r['height_for_age'] or ''):
                same.add(r['index'])
    for r in result['rows']:
        r['duplicate'] = r['index'] in same
    if same and len(same) == len(rows) and not [r for r in result['rows'] if r.get('status') != 'matched']:
        result['checks'].insert(0, ('error', f'Duplicated: everything in this {result["kind"]} is already in ARMS exactly as in the file '
                                             f'({result["label"]}), so there is nothing new to save.'))
    return len(same)


def decide(result, choices):
    """(rows paired with enrollments, problems). Certain rows go as matched; the adviser settles the others."""
    plan, problems, used = [], [], {}
    for row in result['rows']:
        if row.get('duplicate'):                      # already in ARMS exactly as in the file: not saved again
            continue
        choice = choices.get(str(row['index']))
        if row['status'] == 'matched' and choice in (None, ''):
            target = row['enrollment']
        elif choice in (None, '', 'skip'):
            continue
        else:
            target = int(choice)
        if target in used:
            problems.append(f'{row["name"]} and {used[target]} are the same learner. Choose one of them.')
            continue
        used[target] = row['name']
        plan.append((row, target))
    return plan, problems


# ---------------------------------------------------------------------------------------------
# how far a section's forms are
# ---------------------------------------------------------------------------------------------

def months_so_far(sy):
    """The school year's months that have ended, so their SF2 is due: [(month, label)]."""
    today = date.today()
    out = []
    for month, label in FD.months_of(sy):
        year, _ = FD.month_of(sy, month)
        if date(year, month, calendar.monthrange(year, month)[1]) < today:
            out.append((month, label))
    return out


def school_status(sections, sy):
    """Every section's uploads, for the registrar: SF2 months, SF3 learners, SF8 weighed. One query each."""
    from attendance.models import AttendanceSummary
    from django.db.models import Count
    from documentation.models import BookRecord, LearnerNutrition
    from enrollment.models import Enrollment
    months = months_so_far(sy)
    learners = dict(Enrollment.objects.filter(section__in=sections, school_year=sy, status__in=FD.ACTIVE).values_list('section_id').annotate(n=Count('id')))
    done = {}
    for section_id, month in AttendanceSummary.objects.filter(enrollment__section__in=sections, school_year=sy).values_list('enrollment__section_id', 'month').distinct():
        done.setdefault(section_id, set()).add(month)
    books = dict(BookRecord.objects.filter(enrollment__section__in=sections).values_list('enrollment__section_id').annotate(n=Count('enrollment', distinct=True)))
    weighed = dict(LearnerNutrition.objects.filter(enrollment__section__in=sections, stage='BOSY').values_list('enrollment__section_id').annotate(n=Count('id')))
    rows = [{'section': f'{s.grade_level.grade_name} {s.section_name}', 'adviser': FD.adviser_of(s).title(), 'learners': learners.get(s.id, 0),
             'months': len(months), 'months_done': sum(1 for m, _ in months if m in done.get(s.id, ())), 'books': books.get(s.id, 0),
             'weighed': weighed.get(s.id, 0)} for s in sections]
    return {'rows': rows, 'behind': sum(1 for r in rows if r['learners'] and r['months_done'] < r['months'])}


def section_status(section, sy, periods=None):
    """What the adviser has provided so far: SF2 months, SF3 books, SF8 weighings and SF9 comments."""
    from attendance.models import AttendanceSummary
    from documentation.models import BookRecord, LearnerComment, LearnerNutrition
    from enrollment.models import Enrollment
    ids = list(Enrollment.objects.filter(section=section, school_year=sy, status__in=FD.ACTIVE).values_list('id', flat=True))
    months = months_so_far(sy)
    done = set(AttendanceSummary.objects.filter(enrollment_id__in=ids, school_year=sy).values_list('month', flat=True))
    missing = [label for month, label in months if month not in done]
    weighed = {stage: LearnerNutrition.objects.filter(enrollment_id__in=ids, stage=stage).count() for stage, _ in FD_STAGES}
    periods = periods if periods is not None else FD.periods_of(sy)
    return {'learners': len(ids), 'months': months, 'months_done': sum(1 for m, _ in months if m in done), 'months_missing': missing,
            'books': BookRecord.objects.filter(enrollment_id__in=ids).values('enrollment_id').distinct().count(),
            'weighed': weighed, 'comments': LearnerComment.objects.filter(enrollment_id__in=ids, quarter__in=periods).count()}
