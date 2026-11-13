"""Learner tables and one learner's record with a recommendation in every subject (objective 2 of the capstone).

Two readers share this module:

- a teacher's home page lists the learners of their sections (teachers/views.py): their advisory section with every
  subject, and each other section they teach with their own subjects only;
- the School Head's Academic Dashboard lists every learner of the selection (heads/views_learners.py).

Both follow the page's Period picker (core/periods.View): one grading period, all of them (a subject's grade is the mean
of its periods so far) or the final grades. A row holds the learner, the section, the grade in each subject, the average
and the performance level of the school's DepEd order. Opening a learner (View) gives their average and level, their
record (the period's grade and its components; every period and the final grade for All; the final grades for Final)
and, for every subject with a grade, a recommendation for academic enhancement at the level they are at: remediation
below the passing mark, practice that moves them up through their weakest component, or enrichment at the top level
(teachers/remedial.learner_plan). Poro AI then writes its own wording of each one, checked before it shows
(heads/poro.py); the computed wording shows meanwhile. The same record prints, one learner or all of them.
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
STAGE_ORDER = {'remediate': 0, 'secure': 1, 'level_up': 2, 'enrich': 3}


def _num(value):
    return round(float(value), 1) if value is not None else None


def display(student):
    middle = (student.middle_name or '').strip()
    return f'{student.last_name}, {student.first_name}' + (f' {middle[0]}.' if middle else '')


def _level(value):
    lv = policy.level_of(round(value, 1))
    return {'key': lv['key'], 'name': lv['name'], 'short': lv['short'], 'letter': lv['letter']}


def table(sy, periods, view, scope):
    """Learner rows for what the Period picker shows (a core/periods.View) over several sections. scope: [(section,
    [assignments])], each section with the subjects the reader follows there. Returns {'columns', 'rows', 'sections'}: a
    column per subject code (with the sections it is taught in), and per learner the section, a grade per column (None
    where it is not theirs or not in yet), the average of their grades, the level and how many subjects are below 75."""
    scope = [(s, list(a)) for s, a in scope if s is not None]
    sections = {s.id: s for s, _ in scope}
    if not scope or view is None:
        return {'columns': [], 'rows': [], 'sections': []}
    own = {}                                           # (section, subject) -> the class the view shows
    for section, assignments in scope:
        for a in assignments:
            if view.period is None or view.period in P.for_class(a, periods):
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
    raw = [r for r in GradeComponent.objects.filter(P.HAS_GRADE, enrollment__in=enrollments, quarter__in=view.quarters).values(
           'enrollment_id', 'enrollment__section_id', 'subject_id', grade=P.GRADE)
           if (r['enrollment__section_id'], r['subject_id']) in own]
    counts = {k: len(P.for_class(a, periods)) for k, a in own.items()}
    grades = defaultdict(dict)
    for r in P.merge(raw, view, count_of=lambda r: counts[(r['enrollment__section_id'], r['subject_id'])]):
        grades[r['enrollment_id']][own[(r['enrollment__section_id'], r['subject_id'])].subject.subject_code] = float(r['grade'])
    for (sid, _), a in own.items():
        columns[index[a.subject.subject_code]]['total'] += by_section[sid]
    rows = []
    for e in enrollments:
        mine = grades.get(e.id, {})
        exact = [mine.get(c['code']) if e.section_id in c['sections'] else None for c in columns]
        for c, g in zip(columns, exact):
            if g is not None:
                c['graded'] += 1
        present = [g for g in exact if g is not None]
        average = round(mean(present), 1) if present else None
        section = sections[e.section_id]
        rows.append({'id': e.id, 'name': display(e.student), 'lrn': e.student.lrn or '', 'section': e.section_id,
                     'section_name': f'{section.grade_level.grade_name} {section.section_name}',
                     'grades': [round(g, 1) if g is not None else None for g in exact],
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


def grades_of(enrollment_ids, periods, subject_ids):
    """{enrollment id: {(subject id, period id): (grade, ww, pt, qa)}} in one query (a printout of many learners)."""
    found = defaultdict(dict)
    for e_id, subject_id, period_id, value, ww, pt, qa in GradeComponent.objects.filter(
            P.HAS_GRADE, enrollment_id__in=enrollment_ids, quarter__in=periods, subject_id__in=subject_ids).values_list(
            'enrollment_id', 'subject_id', 'quarter_id', P.GRADE, 'written_work_percent', 'performance_task_percent',
            'quarterly_assessment_percent'):
        found[e_id][(subject_id, period_id)] = (float(value), _num(ww), _num(pt), _num(qa))
    return found


def record(enrollment, sy, periods, view, assignments, *, reader, found=None, register=True):
    """One learner's record and a recommendation in every subject with a grade, for what the Period picker shows.

    assignments: the classes of the learner's section the reader may see (all of them for the adviser and the School
    Head, a subject teacher's own). The record's grade columns follow the view: the period alone; every period and the
    final grade (All); the final grade (Final). Each row also has the view's three component percents (the period's, or
    their mean over the periods). Returns the heads, rows, the general average per column, the learner's average and
    level, the recommendations (computed now, Poro's wording later through 'token') and the components of the school's
    DepEd order. found: the learner's grades from grades_of (one query here otherwise). register=False reads what Poro
    already wrote without asking it to write (printouts)."""
    if found is None:
        found = grades_of([enrollment.id], periods, [a.subject_id for a in assignments]).get(enrollment.id, {})
    if view.period:
        cols = [view.period]
    elif view.kind == 'all':
        cols = list(periods) + ['final']
    else:
        cols = ['final']
    heads = [{'label': 'Final', 'name': 'Final grade', 'final': True} if c == 'final' else
             {'label': c.quarter_label, 'name': P.name(c), 'final': False} for c in cols]
    rows, recs, poro_recs, chosen = [], [], {}, []
    abbr = {k: a for k, _, a in policy.components()}
    for a in sorted(assignments, key=lambda a: a.subject.subject_code):
        mine = P.for_class(a, periods)
        if view.period and view.period not in mine:
            continue                                   # not taught in the period shown (a Senior High semester)
        values = {p.id: found[(a.subject_id, p.id)] for p in mine if (a.subject_id, p.id) in found}
        final = P.final_grade([values.get(p.id, (None,))[0] for p in mine], len(mine))
        cells = [final if c == 'final' else (values[c.id][0] if c in mine and c.id in values else None) for c in cols]
        # the view's grade and components: the period's; the mean of the periods so far (All); the final grade (Final)
        shown = [values[p.id] for p in view.quarters if p.id in values]
        if view.period:
            grade = shown[0][0] if shown else None
        elif view.kind == 'all':
            grade = mean(v[0] for v in shown) if shown else None
        else:
            grade = float(final) if final is not None else None
        parts = {}
        for i, k in enumerate(('ww', 'pt', 'qa'), start=1):
            got = [v[i] for v in shown if v[i] is not None]
            parts[k] = round(mean(got), 1) if got else None
        rows.append({'code': a.subject.subject_code, 'name': a.subject.subject_name, 'grades': cells,
                     'parts': [parts['ww'], parts['pt'], parts['qa']] if grade is not None else None,
                     'grade': round(grade, 1) if grade is not None else None})
        if grade is None:
            continue
        chosen.append(grade)
        present = {k: v for k, v in parts.items() if v is not None}
        weak = min(present, key=present.get) if present else None
        level, urgent = policy.level_of(round(grade, 1)), grade < URGENT
        key = f's{a.subject_id}'
        text = remedial.learner_plan(a.subject, level, weak, urgent=urgent)
        recs.append({'key': key, 'code': a.subject.subject_code, 'name': a.subject.subject_name, 'grade': round(grade, 1),
                     'level': _level(grade), 'stage': remedial.stage(level), 'text': text,
                     'weak': abbr[weak] if weak else '',
                     'parts': [{'abbr': abbr[k], 'value': round(parts[k]) if parts[k] is not None else None, 'weak': k == weak}
                               for k in ('ww', 'pt', 'qa')]})
        facts = remedial.plan_facts(a.subject, level, weak, urgent=urgent)
        facts['reader'] = reader
        poro_recs[key] = (text, facts)
    averages = []
    for i in range(len(cols)):
        values = [r['grades'][i] for r in rows if r['grades'][i] is not None]
        averages.append(round(mean(values), 1) if values else None)
    recs.sort(key=lambda r: (STAGE_ORDER[r['stage']], r['grade']))
    average = round(mean(chosen), 1) if chosen else None
    names = [a.subject.subject_name for a in assignments]
    token = ''
    if poro_recs:
        token = poro.register(poro_recs, names=names) if register else poro.token_for(poro_recs, names=names)
    student, section = enrollment.student, enrollment.section
    return {
        'id': enrollment.id, 'name': display(student), 'lrn': student.lrn or '',
        'section': f'{section.grade_level.grade_name} {section.section_name}',
        'view': view.key, 'view_label': view.label, 'period_name': view.label, 'heads': heads,
        'parts_note': (f'the {view.label} component scores' if view.period else
                       'the component scores averaged over the ' + P.word(periods=periods, plural=True).lower() + ' so far'
                       if view.kind == 'all' else
                       'the component scores averaged over the ' + P.word(periods=periods, plural=True).lower()),
        'components': [{'abbr': a, 'name': n} for _, n, a in policy.components()],
        'rows': rows, 'averages': averages, 'average': average, 'level': _level(average) if average is not None else None,
        'below': sum(1 for g in chosen if g < policy.PASSING), 'subjects': len(chosen), 'recs': recs, 'token': token,
        'model': poro.model_label(),
    }


def with_written(data, written):
    """The record with Poro's checked wording in place of the computed one, where Poro wrote it (printouts)."""
    for r in data['recs']:
        if written.get(r['key']):
            r['text'], r['ai'] = written[r['key']], True
    data['ai'] = sum(1 for r in data['recs'] if r.get('ai'))
    return data
