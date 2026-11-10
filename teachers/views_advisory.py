"""Teacher: the advisory section's grades, every subject for one grading period, and each learner's record.

Subject teachers upload the grades of their own classes; the adviser follows the whole section on the teacher's home
page (teachers/views.py): each learner's grade in every subject for one grading period, and each learner's record for
the school year (every subject and period, the grade components and the general average). Only the section's adviser
sees it. Levels follow the school's DepEd order (core/grading_policy.py).
"""
from collections import defaultdict
from statistics import mean

from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.http import urlencode

from core import grading_policy as policy
from core import periods as P
from enrollment.models import Enrollment
from grades.models import GradeComponent
from scheduling.models import ClassAssignment

from . import records as R


def _num(value):
    return round(float(value), 1) if value is not None else None


def section_assignments(section, sy):
    return list(ClassAssignment.objects.filter(section=section, school_year=sy, is_active=True)
                .select_related('section', 'section__grade_level', 'subject', 'teacher', 'semester', 'school_year')
                .order_by('subject__subject_code'))


def section_grades(school, sy, periods, period, section, assignments):
    """The section's grades table for one period and every learner's record for the school year."""
    enrollments = list(Enrollment.objects.filter(section=section, school_year=sy, status__in=R.ACTIVE).select_related('student')
                       .order_by('student__last_name', 'student__first_name'))
    records = defaultdict(dict)          # enrollment -> (subject, period) -> (grade, ww, pt, qa)
    for e_id, subject_id, period_id, transmuted, initial, ww, pt, qa in GradeComponent.objects.filter(
            P.HAS_GRADE, enrollment__in=enrollments, quarter__in=periods).values_list(
            'enrollment_id', 'subject_id', 'quarter_id', 'transmuted_grade', 'initial_grade', 'written_work_percent',
            'performance_task_percent', 'quarterly_assessment_percent'):
        records[e_id][(subject_id, period_id)] = (P.grade_value(transmuted, initial), _num(ww), _num(pt), _num(qa))

    own = {a.id: P.for_class(a, periods) for a in assignments}
    subjects = [a for a in assignments if period and period in own[a.id]]
    progress = {row['assignment'].id: row['current'] for row in R.overview(subjects, periods, period)} if subjects else {}
    columns = []
    for a in subjects:
        cell = progress.get(a.id) or {}
        columns.append({'code': a.subject.subject_code, 'name': a.subject.subject_name, 'teacher': a.teacher.get_full_name() if a.teacher else '',
                        'graded': cell.get('graded', 0), 'total': cell.get('total', len(enrollments))})

    learners, record = [], []
    for e in enrollments:
        st, mine = e.student, records.get(e.id, {})
        grades = [mine.get((a.subject_id, period.id), (None,))[0] for a in subjects]
        present = [g for g in grades if g is not None]
        below = [a.subject.subject_code for a, g in zip(subjects, grades) if g is not None and g < policy.PASSING]
        average = round(mean(present), 1) if present else None
        learners.append({'name': f'{st.last_name}, {st.first_name}' + (f' {st.middle_name[0]}.' if st.middle_name else ''),
                         'lrn': st.lrn or '', 'sex': st.sex, 'grades': grades, 'average': average,
                         'level': policy.level_of(average) if average is not None else None, 'below': below})
        rows = []
        for a in assignments:
            values = [mine.get((a.subject_id, p.id), (None,))[0] if p in own[a.id] else None for p in periods]
            parts = mine.get((a.subject_id, period.id)) if period else None
            rows.append({'code': a.subject.subject_code, 'name': a.subject.subject_name, 'grades': values,
                         'final': P.final_grade([v for v, p in zip(values, periods) if p in own[a.id]], len(own[a.id])),
                         'parts': list(parts[1:]) if parts else None})
        averages = []
        for i in range(len(periods)):
            values = [r['grades'][i] for r in rows if r['grades'][i] is not None]
            averages.append(round(mean(values), 1) if values else None)
        record.append({'rows': rows, 'averages': averages})

    return {
        'columns': columns, 'learners': learners, 'components': policy.components(),
        'record': {'word': P.word(school, periods), 'periods': [{'label': p.quarter_label, 'name': P.name(p)} for p in periods],
                   'period': period.quarter_label if period else '', 'learners': record,
                   'people': [{'name': l['name'], 'lrn': l['lrn'], 'sex': l['sex']} for l in learners]},
    }


@login_required
def advisory(request):
    """My Advisory is the teacher's home page now; old links keep their section and period."""
    query = {k: request.GET[k] for k in ('section', 'period') if request.GET.get(k)}
    return redirect(reverse('teachers:dashboard') + ('?' + urlencode(dict(query, view='advisory')) if query else ''))
