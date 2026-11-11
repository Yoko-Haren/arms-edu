"""Learner tables and one learner's record with a recommendation in every subject (objective 2 of the capstone).

Two readers share this module:

- a teacher's home page lists the learners of their sections (teachers/views.py): their advisory section with every
  subject, and each other section they teach with their own subjects only;
- the School Head's Academic Dashboard lists every learner of the selection (heads/views_learners.py).

A row holds the learner, the section, the grade in each subject of the period, the average and the performance level of
the school's DepEd order. Opening a learner gives their record for the school year (every subject and period, the
grade components of the period shown, the general average) and, for every subject of the period, a recommendation for
academic enhancement at the level they are at: remediation below the passing mark, practice that moves them up through
their weakest component, or enrichment at the top level (teachers/remedial.learner_plan). Poro AI then writes its own
wording of each one, checked before it shows (heads/poro.py); the computed wording shows meanwhile.
"""
from collections import defaultdict
from statistics import fmean as mean          # fast float mean

from core import grading_policy as policy
from core import periods as P
from enrollment.models import Enrollment
from grades.models import GradeComponent
from heads import poro

from . import records as R
from . import remedial

URGENT = 70          # below this, remediation starts this week (teachers/standing.URGENT)


def _num(value):
    return round(float(value), 1) if value is not None else None


def display(student):
    middle = (student.middle_name or '').strip()
    return f'{student.last_name}, {student.first_name}' + (f' {middle[0]}.' if middle else '')


def table(sy, periods, period, scope):
    """Learner rows for one period over several sections. scope: [(section, [assignments])], each section with the subjects
    the reader follows there. Returns {'columns', 'rows', 'sections'}: a column per subject code (with the sections it is
    taught in), and per learner the section, a grade per column (None where it is not theirs or not in yet), the
    average of their grades, the level and how many subjects are below 75."""
    scope = [(s, list(a)) for s, a in scope if s is not None]
    sections = {s.id: s for s, _ in scope}
    if not scope or period is None:
        return {'columns': [], 'rows': [], 'sections': []}
    own = {}                                           # (section, subject) -> the assignment graded this period
    for section, assignments in scope:
        for a in assignments:
            if period in P.for_class(a, periods):
                own[(section.id, a.subject_id)] = a
    columns, index = [], {}
    for (sid, subject_id), a in sorted(own.items(), key=lambda kv: (kv[1].subject.subject_code, kv[0][0])):
        code = a.subject.subject_code
        if code not in index:
            index[code] = len(columns)
            columns.append({'code': code, 'name': a.subject.subject_name, 'sections': [], 'graded': 0, 'total': 0})
        columns[index[code]]['sections'].append(sid)
    enrollments = list(Enrollment.objects.filter(section_id__in=sections, school_year=sy, status__in=R.ACTIVE)
                       .select_related('student').order_by('student__last_name', 'student__first_name'))
    by_section = defaultdict(int)
    for e in enrollments:
        by_section[e.section_id] += 1
    grades = defaultdict(dict)
    for e_id, sid, subject_id, value in GradeComponent.objects.filter(
            P.HAS_GRADE, enrollment__in=enrollments, quarter=period).values_list(
            'enrollment_id', 'enrollment__section_id', 'subject_id', P.GRADE):
        if (sid, subject_id) in own:
            grades[e_id][own[(sid, subject_id)].subject.subject_code] = float(value)
    for (sid, _), a in own.items():
        col = columns[index[a.subject.subject_code]]
        col['total'] += by_section[sid]
    rows = []
    for e in enrollments:
        mine = grades.get(e.id, {})
        cells = [mine.get(c['code']) if e.section_id in c['sections'] else None for c in columns]
        for c, g in zip(columns, cells):
            if g is not None:
                c['graded'] += 1
        present = [g for g in cells if g is not None]
        average = round(mean(present), 1) if present else None
        section = sections[e.section_id]
        rows.append({'id': e.id, 'name': display(e.student), 'lrn': e.student.lrn or '', 'section': e.section_id,
                     'section_name': f'{section.grade_level.grade_name} {section.section_name}', 'grades': cells,
                     'average': average, 'level': policy.level_of(average) if average is not None else None,
                     'below': sum(1 for g in present if g < policy.PASSING)})
    return {'columns': columns, 'rows': rows,
            'sections': [{'id': s.id, 'name': f'{s.grade_level.grade_name} {s.section_name}'} for s, _ in scope]}


SORTS = {
    'name': lambda r: (r['name'].lower(),),
    'below': lambda r: (-r['below'], r['average'] if r['average'] is not None else 999, r['name'].lower()),
    'low': lambda r: (r['average'] is None, r['average'] or 0, r['name'].lower()),
    'high': lambda r: (r['average'] is None, -(r['average'] or 0), r['name'].lower()),
}


def sort_rows(rows, key):
    return sorted(rows, key=SORTS.get(key, SORTS['name']))


def record(enrollment, sy, periods, period, assignments, *, reader):
    """One learner's record for the school year and a recommendation in every subject of `period` they have a grade in.

    assignments: the classes of the learner's section the reader may see (all of them for the adviser and the School
    Head, a subject teacher's own). Returns rows (code, name, a grade per period, the final grade, the period's three
    component percents), the general average per period, the recommendations (computed now, Poro's wording later
    through 'token') and the components of the school's DepEd order."""
    own = {a.id: P.for_class(a, periods) for a in assignments}
    found = {}
    for subject_id, period_id, value, ww, pt, qa in GradeComponent.objects.filter(
            P.HAS_GRADE, enrollment=enrollment, quarter__in=periods, subject_id__in=[a.subject_id for a in assignments]).values_list(
            'subject_id', 'quarter_id', P.GRADE, 'written_work_percent', 'performance_task_percent', 'quarterly_assessment_percent'):
        found[(subject_id, period_id)] = (float(value), _num(ww), _num(pt), _num(qa))
    rows, recs, poro_recs = [], [], {}
    abbr = {k: a for k, _, a in policy.components()}
    for a in sorted(assignments, key=lambda a: a.subject.subject_code):
        values = [found.get((a.subject_id, p.id), (None,))[0] if p in own[a.id] else None for p in periods]
        current = found.get((a.subject_id, period.id)) if period else None
        rows.append({'code': a.subject.subject_code, 'name': a.subject.subject_name, 'grades': values,
                     'final': P.final_grade([v for v, p in zip(values, periods) if p in own[a.id]], len(own[a.id])),
                     'parts': list(current[1:]) if current else None})
        if not current:
            continue
        grade, parts = current[0], dict(zip(('ww', 'pt', 'qa'), current[1:]))
        present = {k: v for k, v in parts.items() if v is not None}
        weak = min(present, key=present.get) if present else None
        level, urgent = policy.level_of(grade), grade < URGENT
        key = f's{a.subject_id}'
        text = remedial.learner_plan(a.subject, level, weak, urgent=urgent)
        recs.append({'key': key, 'code': a.subject.subject_code, 'name': a.subject.subject_name, 'grade': round(grade, 1),
                     'level': {'key': level['key'], 'name': level['name'], 'short': level['short'], 'letter': level['letter']},
                     'stage': remedial.stage(level), 'text': text,
                     'parts': [{'abbr': abbr[k], 'value': round(parts[k]) if parts[k] is not None else None, 'weak': k == weak}
                               for k in ('ww', 'pt', 'qa')]})
        facts = remedial.plan_facts(a.subject, level, weak, urgent=urgent)
        facts['reader'] = reader
        poro_recs[key] = (text, facts)
    averages = []
    for i in range(len(periods)):
        values = [r['grades'][i] for r in rows if r['grades'][i] is not None]
        averages.append(round(mean(values), 1) if values else None)
    order = {'remediate': 0, 'secure': 1, 'level_up': 2, 'enrich': 3}
    recs.sort(key=lambda r: (order[r['stage']], r['grade']))
    student, section = enrollment.student, enrollment.section
    return {
        'name': display(student), 'lrn': student.lrn or '', 'section': f'{section.grade_level.grade_name} {section.section_name}',
        'periods': [{'label': p.quarter_label, 'name': P.name(p)} for p in periods], 'period': period.quarter_label if period else '',
        'period_name': P.name(period), 'components': [{'abbr': a, 'name': n} for _, n, a in policy.components()],
        'rows': rows, 'averages': averages, 'recs': recs,
        'token': poro.register(poro_recs, names=[a.subject.subject_name for a in assignments]) if poro_recs else '',
    }
