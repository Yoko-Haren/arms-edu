"""Grading periods and grades, the way each school counts them.

A school grades by quarter (Q1-Q4), term (T1-T3, trimestral) or semester (S1-S2) (School.period_type);
its periods are the Quarter rows of the school year. A school may change its grading system with a new
school year (DepEd moved from four quarters to three terms in SY 2026-2027), so a year's own period
labels decide how its periods are named. Every page, chart and report names and counts periods through
these helpers instead of assuming four quarters.

The grade that counts (report card, passing at 75, proficiency level) is the quarterly grade:
GradeComponent.transmuted_grade. Older records kept it in initial_grade only, so readers fall
back to that.
"""
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import DecimalField, F, Q
from django.db.models.functions import Coalesce
from django.utils import timezone

# A trimestral school's periods are its three terms, as DepEd Orders No. 009 and 015, s. 2026 and their e-class records name them
PERIOD_WORDS = {'Q': ('Quarter', 'Quarters'), 'T': ('Term', 'Terms'), 'S': ('Semester', 'Semesters')}
TYPE_PREFIX = {'QUARTERLY': 'Q', 'TRIMESTRAL': 'T', 'SEMESTRAL': 'S'}
ORDINALS = {1: 'First', 2: 'Second', 3: 'Third', 4: 'Fourth'}

# the grade on the report card; use in .annotate()/.aggregate() instead of 'initial_grade'
GRADE = Coalesce(F('transmuted_grade'), F('initial_grade'), output_field=DecimalField(max_digits=5, decimal_places=2))
HAS_GRADE = Q(transmuted_grade__isnull=False) | Q(initial_grade__isnull=False)


def grade_value(transmuted, initial):
    """The quarterly grade of one record as a float (None when there is none)."""
    value = transmuted if transmuted is not None else initial
    return None if value is None else float(value)


def prefix(school=None, periods=()):
    """'Q', 'T' or 'S': the school year's own periods decide, else the school's setting."""
    for p in periods:
        if p.quarter_label and p.quarter_label[:1].upper() in PERIOD_WORDS:
            return p.quarter_label[:1].upper()
    if school is not None and getattr(school, 'period_type', None) in TYPE_PREFIX:
        return TYPE_PREFIX[school.period_type]
    return 'Q'


def word(school=None, periods=(), plural=False):
    """'Quarter', 'Term' or 'Semester' (or the plural)."""
    words = PERIOD_WORDS.get(prefix(school, periods), PERIOD_WORDS['Q'])
    return words[1] if plural else words[0]


def name(period):
    """'Quarter 2', 'Term 1', 'Semester 2'."""
    if not period:
        return ''
    w = PERIOD_WORDS.get((period.quarter_label or 'Q')[:1].upper(), PERIOD_WORDS['Q'])[0]
    return f'{w} {period.quarter_number}'


GRADE_WORDS = {'Q': 'quarterly', 'T': 'term', 'S': 'semester'}


def grade_word(period):
    """'quarterly', 'term' or 'semester': what a period's grade is called ('the term grade')."""
    return GRADE_WORDS.get(((period.quarter_label if period else '') or 'Q')[:1].upper(), 'quarterly')


def long_name(period):
    """'Second Quarter' (the e-class record says it this way in cell A7)."""
    if not period:
        return ''
    w = PERIOD_WORDS.get((period.quarter_label or 'Q')[:1].upper(), PERIOD_WORDS['Q'])[0]
    return f'{ORDINALS.get(period.quarter_number, period.quarter_number)} {w}'


def short(period):
    """'Q2', 'T1', 'S2' as stored."""
    return period.quarter_label if period else ''


def current(periods, has_grades=None):
    """The period marked current, else the one today falls in, else (if has_grades) the latest with grades.
    A locked period hands over to the next one that is not locked: Term 2 locked, Term 3 current."""
    periods = list(periods)
    if not periods:
        return None
    today = timezone.localdate()
    found = next((p for p in periods if p.is_current_quarter), None) or \
        next((p for p in periods if p.date_start <= today <= p.date_end), None)
    if found:
        if found.is_grades_locked:
            return next((p for p in periods if p.quarter_number > found.quarter_number and not p.is_grades_locked), found)
        return found
    if has_grades:
        for p in reversed(periods):
            if has_grades(p):
                return p
    return periods[-1] if periods[-1].date_end < today else periods[0]


def mark_current(period):
    """Make this the school year's current period, alone."""
    type(period).objects.filter(school_year=period.school_year_id).exclude(pk=period.pk).update(is_current_quarter=False)
    type(period).objects.filter(pk=period.pk).update(is_current_quarter=True)
    period.is_current_quarter = True


def for_class(assignment, periods):
    """The periods a class is graded in: all of them, or only its semester's for a Senior High subject."""
    periods = list(periods)
    semester = getattr(assignment, 'semester', None)
    if not semester:
        return periods
    inside = [p for p in periods if p.date_start <= semester.date_end and p.date_end >= semester.date_start]
    return inside or periods


def final_grade(values, periods_count):
    """Rounded average of the period grades, only when every period of the class has one (DepEd Order No. 8, s. 2015)."""
    present = [v for v in values if v is not None]
    if not periods_count or len(present) < periods_count:
        return None
    return int(_round_half_up(sum(present) / len(present)))


def _round_half_up(x):
    return Decimal(repr(float(x))).quantize(Decimal(1), rounding=ROUND_HALF_UP)


def deadline_info(period, now=None, field='grade_encoding_deadline'):
    """(days left, overdue?) for a period's grade encoding (or validation) deadline, or (None, False) without one."""
    deadline = getattr(period, field, None)
    if not deadline:
        return None, False
    now = now or timezone.now()
    days = (timezone.localtime(deadline).date() - timezone.localtime(now).date()).days
    return days, deadline < now


def due_text(period, field='grade_encoding_deadline'):
    """' Due in 3 days.', ' Due today.', ' The deadline was Nov 13.' or '' (with a leading space)."""
    days, overdue = deadline_info(period, field=field)
    if days is None:
        return ''
    if overdue:
        return f' The deadline was {timezone.localtime(getattr(period, field)):%b %d}.'
    if days == 0:
        return ' Due today.'
    return f' Due in {days} day{"s" if days != 1 else ""}.'


# --------------------------------------------------------------------------------------------
# What a Period picker shows: one period, all of them, or the final grades
# --------------------------------------------------------------------------------------------

class View:
    """One grading period ('period'); all of them ('all': a subject's grade is the mean of its periods so far, each grade
    component too); or the final grades ('final': the rounded mean of the period grades, only once every period of the
    class has one). span: the periods 'all' reads (every period of the year unless given)."""

    def __init__(self, kind, periods, period=None, span=None):
        self.kind = kind if kind in ('all', 'final') else 'period'
        self.periods = list(periods)
        self.period = period if self.kind == 'period' else None
        self.span = list(span) if span else self.periods

    @property
    def key(self):
        return str(self.period.id) if self.period else self.kind

    @property
    def label(self):
        """'Term 2', 'All terms', 'Final grades'."""
        if self.period:
            return name(self.period)
        if self.kind == 'all':
            return f'All {word(periods=self.periods, plural=True).lower()}'
        return 'Final grades'

    @property
    def short(self):
        """'T2', 'All', 'Final'."""
        return short(self.period) if self.period else ('All' if self.kind == 'all' else 'Final')

    @property
    def phrase(self):
        """How a sentence names the grades: 'Term 2 grades', 'grades', 'final grades'."""
        return f'{name(self.period)} grades' if self.period else ('grades' if self.kind == 'all' else 'final grades')

    @property
    def quarters(self):
        """The periods whose grades the view reads."""
        if self.period:
            return [self.period]
        return self.span if self.kind == 'all' else self.periods


def pick(periods, wanted, default=None):
    """The View a picker asks for: 'all', 'final' or a period id; else one period, `default`. None without periods."""
    periods = list(periods)
    if not periods:
        return None
    if wanted in ('all', 'final'):
        return View(wanted, periods)
    chosen = next((p for p in periods if str(p.id) == str(wanted or '')), None) or default
    return View('period', periods, chosen) if chosen else None


def picker(periods, current=None):
    """The Period picker's options: All, every period by name (the current one marked), Final."""
    periods = list(periods)
    if not periods:
        return []
    return ([{'value': 'all', 'label': 'All'}] +
            [{'value': str(p.id), 'label': name(p) + (' (current)' if current and p.id == current.id else '')} for p in periods] +
            [{'value': 'final', 'label': 'Final'}])


def merge(rows, view, count_of=None):
    """Grade rows (dicts with enrollment_id, subject_id, grade and the parts ww, pt, qa when present) as the view reads
    them. One period: as they are. All: one row per learner and subject, the grade and each part the mean of its periods.
    Final: the final grade (final_grade) with the parts' means; a subject without every period's grade is left out.
    count_of(row): how many periods the row's class is graded in (default: every period of the year)."""
    if view is None or view.kind == 'period':
        return list(rows)
    by = {}
    for r in rows:
        by.setdefault((r['enrollment_id'], r['subject_id']), []).append(r)
    out = []
    for found in by.values():
        grades = [float(r['grade']) for r in found]
        if view.kind == 'final':
            grade = final_grade(grades, count_of(found[0]) if count_of else len(view.periods))
            if grade is None:
                continue
        else:
            grade = sum(grades) / len(grades)
        merged = dict(found[0], grade=grade)
        for k in ('ww', 'pt', 'qa'):
            if k in found[0]:
                values = [float(r[k]) for r in found if r[k] is not None]
                merged[k] = sum(values) / len(values) if values else None
        out.append(merged)
    return out
