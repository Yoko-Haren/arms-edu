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
    view = 'classes' if not advised or (request.GET.get('view') == 'classes' and assignments) else 'advisory'
    section = None
    if view == 'advisory':
        section = next((s for s in advised if str(s.id) == request.GET.get('section')), None) or advised[0]
        scope_assignments = section_assignments(section, sy)
        scope = f'{section.grade_level.grade_name} {section.section_name}'
        reader, who = 'adviser', 'your advisory class'
    else:
        scope_assignments = assignments
        scope, reader, who = 'Your classes', 'teacher', 'your classes'
    pairs = {(a.section_id, a.subject_id) for a in scope_assignments}
    period = _period(periods, current, request.GET.get('period'), sy, pairs) if sy else None

    context = {'school': school, 'sy': sy, 'periods': periods, 'period': period, 'current': current,
               'period_name': P.name(period), 'view': view, 'advised': advised, 'section': section, 'scope': scope,
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
