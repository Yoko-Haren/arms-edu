"""Teacher home: My Advisory for an adviser, My Classes for every teacher.

One page, two scopes. An adviser opens on their section: its status in the school's DepEd order, where each subject
stands, Poro AI's recommendations, the averages over the school year with the forecast, and every learner's grades. A
teacher who advises no section (or an adviser who switches) sees the same reading of their own classes. Class Records,
School Forms and Assessments hold the rest. The reading is in teachers/standing.py.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, render

from academics.models import Section
from core import periods as P
from core.caching import cache_page_for_user
from grades.models import GradeComponent
from heads import poro

from . import records as R
from . import standing
from .views_advisory import section_assignments, section_grades
from .views_records import _teacher, teacher_context


def _period(periods, current, wanted, sy, pairs):
    """The period asked for; else the current one when it has grades in scope; else the latest one with grades."""
    chosen = next((p for p in periods if str(p.id) == str(wanted or '')), None)
    if chosen:
        return chosen
    sections, subjects = {s for s, _ in pairs}, {s for _, s in pairs}
    graded = {q for s, subj, q in GradeComponent.objects.filter(
        P.HAS_GRADE, enrollment__school_year=sy, enrollment__section_id__in=sections, subject_id__in=subjects,
        quarter__in=periods).values_list('enrollment__section_id', 'subject_id', 'quarter_id').distinct() if (s, subj) in pairs}
    if current and current.id in graded:
        return current
    return next((p for p in reversed(periods) if p.id in graded), current)


def _todo(assignments, periods, current):
    """Classes that need the teacher in the current period: returned for correction, and ready to submit."""
    if not assignments or not current:
        return {}
    states = [row['state'] for row in R.overview(assignments, periods, current)]
    return {'returned': states.count('returned'), 'ready': states.count('ready')}


@login_required
@cache_page_for_user()
def dashboard(request):
    profile = getattr(request.user, 'profile', None)
    if not profile or profile.role != 'teacher':
        messages.error(request, 'Access denied. Teachers only.')
        return redirect('signin')
    if not profile.school:
        messages.error(request, 'You are not assigned to any school.')
        return redirect('signin')
    school, sy, periods, current, assignments = teacher_context(request)
    advised = list(Section.objects.filter(school_year=sy, school=school, adviser=request.user, is_active=True)
                   .select_related('grade_level').order_by('grade_level__sort_order', 'section_name')) if sy else []
    # the Section picker: the advisory class, every other section the teacher teaches, and all their classes together
    taught = []
    for a in assignments:
        if a.section not in taught and a.section not in advised:
            taught.append(a.section)
    wanted = request.GET.get('section') or ('all' if request.GET.get('view') == 'classes' else '')
    section = next((s for s in advised + taught if str(s.id) == wanted), None)
    if section is None and not (wanted == 'all' and assignments):
        section = advised[0] if advised else None
    if section in advised:
        view, scope_assignments, reader, who = 'advisory', section_assignments(section, sy), 'adviser', 'your advisory class'
    elif section is not None:
        scope_assignments = [a for a in assignments if a.section_id == section.id]
        view, reader = 'section', 'teacher'
        who = f'your class{"es" if len(scope_assignments) != 1 else ""} in {section.section_name}'
    else:
        view, scope_assignments, reader, who = 'classes', assignments, 'teacher', 'your classes'
    scope = f'{section.grade_level.grade_name} {section.section_name}' if section else 'Your classes'
    pairs = {(a.section_id, a.subject_id) for a in scope_assignments}
    period = _period(periods, current, request.GET.get('period'), sy, pairs) if sy else None

    context = {'school': school, 'sy': sy, 'periods': periods, 'period': period, 'current': current,
               'period_name': P.name(period), 'view': view, 'advised': advised, 'taught': taught, 'section': section, 'scope': scope,
               'has_classes': bool(assignments), 'todo': _todo(assignments, periods, current)}
    context.update(standing.build(school=school, sy=sy, periods=periods, period=period, assignments=scope_assignments,
                                  reader=reader, scope=scope, who=who, section=section))
    if section is not None:
        context.update(section_grades(school, sy, periods, period, section, scope_assignments))
    return render(request, 'teachers/home.html', context)


@login_required
def poro_status(request, token):
    """Poro AI's wording of the home page's recommendations, as the School Head's dashboard polls it (heads/poro.py)."""
    if not _teacher(request):
        return JsonResponse({'error': 'Access denied'}, status=403)
    result = poro.status(token)
    if result is None:
        return JsonResponse({'error': 'Unknown recommendations'}, status=404)
    response = JsonResponse(result)
    response['Cache-Control'] = 'no-store'
    return response


@login_required
def learner_advice(request):
    """Poro AI's advice for one learner below 75, one recommendation per subject they are below in, registered when the
    teacher opens the learner (the page already shows the computed wording). Only the learner's adviser (every subject)
    or one of their subject teachers (their own subjects) gets it. Returns {'token'} for teachers:poro, '' for none."""
    from django.db.models import F
    from academics.models import Quarter
    from enrollment.models import Enrollment
    from scheduling.models import ClassAssignment

    if not _teacher(request):
        return JsonResponse({'error': 'Access denied'}, status=403)
    enrollment = Enrollment.objects.select_related('section').filter(
        id=request.GET.get('enrollment') or 0, status__in=R.ACTIVE).first()
    period = Quarter.objects.filter(id=request.GET.get('period') or 0, school_year_id=enrollment.school_year_id).first() if enrollment else None
    if enrollment is None or period is None:
        return JsonResponse({'error': 'Unknown learner'}, status=404)
    assigned = ClassAssignment.objects.filter(section=enrollment.section, school_year_id=enrollment.school_year_id, is_active=True)
    if enrollment.section.adviser_id != request.user.id:
        assigned = assigned.filter(teacher=request.user)
    subjects = {a.subject_id: a.subject for a in assigned.select_related('subject', 'subject__subject_family')}
    if not subjects:
        return JsonResponse({'error': 'Access denied'}, status=403)
    recs = {}
    for r in GradeComponent.objects.filter(P.HAS_GRADE, enrollment=enrollment, quarter=period, subject_id__in=subjects).values(
            'subject_id', grade=P.GRADE, ww=F('written_work_percent'), pt=F('performance_task_percent'), qa=F('quarterly_assessment_percent')):
        if float(r['grade']) < standing.PASSING:
            entry = standing.learner_subject(r, subjects[r['subject_id']])
            recs[entry['key']] = (entry['text'], standing.learner_facts(entry, subjects[r['subject_id']]))
    token = poro.register(recs, names=[s.subject_name for s in subjects.values()]) if recs else ''
    response = JsonResponse({'token': token})
    response['Cache-Control'] = 'no-store'
    return response
