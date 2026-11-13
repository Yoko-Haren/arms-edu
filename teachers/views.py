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
from django.urls import reverse
from django.utils import timezone

from academics.models import Section
from core import grading_policy as policy
from core import periods as P
from core.caching import cache_page_for_user
from grades.models import GradeComponent
from heads import poro

from . import learners
from . import records as R
from . import standing
from .views_advisory import section_assignments
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
    # the Period picker: All, each period, Final (core/periods.View); one period by default
    wanted = request.GET.get('period')
    shown = (P.pick(periods, wanted) if wanted in ('all', 'final') else
             P.pick(periods, None, default=_period(periods, current, wanted, sy, pairs))) if sy else None

    context = {'school': school, 'sy': sy, 'periods': periods, 'period': shown.period if shown else None, 'shown': shown,
               'period_options': P.picker(periods, current), 'current': current,
               'period_name': shown.label if shown else '', 'view': view, 'advised': advised, 'taught': taught, 'section': section,
               'scope': scope, 'has_classes': bool(assignments), 'todo': _todo(assignments, periods, current)}
    context.update(standing.build(school=school, sy=sy, periods=periods, view=shown, assignments=scope_assignments,
                                  reader=reader, scope=scope, who=who, section=section))
    # every learner of the teacher's sections, for the table under the forecast: filtered there by section (learners.table)
    context['table'] = learners.table(sy, periods, shown, _table_scope(sy, advised, taught, assignments)) if sy else None
    context['table_section'] = str(section.id) if section else 'all'
    context['components'] = policy.components()
    return render(request, 'teachers/home.html', context)


def _table_scope(sy, advised, taught, assignments):
    """[(section, assignments)]: an advisory section with every subject, any other section with the teacher's own."""
    return [(s, section_assignments(s, sy)) for s in advised] + \
           [(s, [a for a in assignments if a.section_id == s.id]) for s in taught]


def _teacher_sections(request):
    """(school, sy, periods, current, the table's [(section, assignments)], advised ids) for the signed-in teacher."""
    school, sy, periods, current, assignments = teacher_context(request)
    advised = list(Section.objects.filter(school_year=sy, school=school, adviser=request.user, is_active=True)
                   .select_related('grade_level').order_by('grade_level__sort_order', 'section_name')) if sy else []
    taught = []
    for a in assignments:
        if a.section not in taught and a.section not in advised:
            taught.append(a.section)
    return school, sy, periods, current, _table_scope(sy, advised, taught, assignments), {s.id for s in advised}


def _learner(request, enrollment_id):
    """(school, sy, periods, the view asked for, the enrollment, the classes the teacher may see, reader) for one of the
    signed-in teacher's learners: every subject for the learner's adviser, their own for a subject teacher; None else."""
    from enrollment.models import Enrollment
    school, sy, periods, current, scope, advised = _teacher_sections(request)
    mine = {s.id: a for s, a in scope}
    enrollment = Enrollment.objects.select_related('student', 'section', 'section__grade_level').filter(
        id=enrollment_id, school_year=sy, status__in=R.ACTIVE, section_id__in=mine).first() if sy else None
    shown = P.pick(periods, request.GET.get('period'), default=current)
    if enrollment is None or shown is None:
        return None
    policy.activate(school)
    return (school, sy, periods, shown, enrollment, mine[enrollment.section_id],
            'adviser' if enrollment.section_id in advised else 'teacher')


@login_required
def learner_record(request, enrollment_id):
    """One learner's record and a recommendation in every subject with a grade (teachers/learners.record), for the
    Period picker's choice: every subject for the learner's adviser, their own subjects for a subject teacher, nothing
    for anyone else."""
    if not _teacher(request):
        return JsonResponse({'error': 'Access denied'}, status=403)
    found = _learner(request, enrollment_id)
    if found is None:
        return JsonResponse({'error': 'Not one of your learners'}, status=404)
    school, sy, periods, shown, enrollment, assignments, reader = found
    data = learners.record(enrollment, sy, periods, shown, assignments, reader=reader)
    data['poll'] = reverse('teachers:poro', args=[data['token']]) if data['token'] else ''
    data['print'] = reverse('teachers:learner_print', args=[enrollment.id]) + f'?period={shown.key}'
    response = JsonResponse(data)
    response['Cache-Control'] = 'no-store'
    return response


def print_page(request, *, school, sy, shown, records, scope_name, one, by_role):
    """Learners' records and recommendations as a page to print or save as PDF (shared/learner_records_print.html).
    Also the School Head's (heads/views_learners.learner_print)."""
    return render(request, 'shared/learner_records_print.html', {
        'school': school, 'sy': sy, 'period_name': shown.label, 'records': records, 'scope_name': scope_name, 'one': one,
        'by': request.user.get_full_name() or request.user.username, 'by_role': by_role,
        'levels': policy.levels(), 'printed': timezone.localtime(), 'ai': sum(r.get('ai', 0) for r in records),
    })


@login_required
def learner_print(request, enrollment_id):
    """One learner's record and recommendations, as the dialog shows them, to print or save as PDF; Poro AI's wording
    where it has written and checked it, the computed wording elsewhere."""
    if not _teacher(request):
        messages.error(request, 'Access denied. Teachers only.')
        return redirect('signin')
    found = _learner(request, enrollment_id)
    if found is None:
        messages.error(request, 'That learner is not in your sections.')
        return redirect('teachers:dashboard')
    school, sy, periods, shown, enrollment, assignments, reader = found
    data = learners.record(enrollment, sy, periods, shown, assignments, reader=reader, register=False)
    learners.with_written(data, poro.written(data['token']))
    return print_page(request, school=school, sy=sy, shown=shown, records=[data], scope_name=data['section'], one=True,
                      by_role='Teacher')


def _printed_rows(request, sy, periods, shown, scope):
    """The table's learners as the page shows them: the section chosen (or all), the search and the order."""
    wanted = request.GET.get('section') or 'all'
    chosen = [(s, a) for s, a in scope if wanted == 'all' or str(s.id) == wanted] or scope
    data = learners.table(sy, periods, shown, chosen) if sy else {'columns': [], 'rows': [], 'sections': []}
    q = (request.GET.get('q') or '').strip().lower()
    rows = [r for r in data['rows'] if not q or q in r['name'].lower() or q in r['lrn']]
    many = len(chosen) > 1
    scope_name = 'All my sections' if many else (data['sections'][0]['name'] if data['sections'] else '')
    return chosen, data, learners.sort_rows(rows, request.GET.get('sort')), many, scope_name


@login_required
def learners_print_all(request):
    """Every learner of the table (its section, search and order): each one's record and recommendations, one after
    another, to print or save as PDF. Poro AI's wording where it has already written it for a learner; nothing new is
    asked of it (a class would keep it busy for minutes)."""
    from enrollment.models import Enrollment
    if not _teacher(request):
        messages.error(request, 'Access denied. Teachers only.')
        return redirect('signin')
    school, sy, periods, current, scope, advised = _teacher_sections(request)
    shown = P.pick(periods, request.GET.get('period'), default=current)
    if shown is None:
        return redirect('teachers:dashboard')
    policy.activate(school)
    chosen, data, rows, many, scope_name = _printed_rows(request, sy, periods, shown, scope)
    classes = {s.id: a for s, a in chosen}
    people = Enrollment.objects.select_related('student', 'section', 'section__grade_level').in_bulk([r['id'] for r in rows])
    found = learners.grades_of(list(people), periods, {a.subject_id for mine in classes.values() for a in mine})
    records = [learners.record(people[r['id']], sy, periods, shown, classes[people[r['id']].section_id], found=found.get(r['id'], {}),
                               reader='adviser' if people[r['id']].section_id in advised else 'teacher', register=False)
               for r in rows if r['id'] in people]
    written = poro.written_many([d['token'] for d in records])
    for d in records:
        learners.with_written(d, written.get(d['token'], {}))
    return print_page(request, school=school, sy=sy, shown=shown, records=records, scope_name=scope_name, one=False,
                      by_role='Teacher')


@login_required
def learners_print(request):
    """The learner table as a page to print or save as PDF: the section chosen in the table (or all of them), with the
    grade in each subject, in the table's order; no Action column."""
    if not _teacher(request):
        messages.error(request, 'Access denied. Teachers only.')
        return redirect('signin')
    school, sy, periods, current, scope, advised = _teacher_sections(request)
    shown = P.pick(periods, request.GET.get('period'), default=current)
    policy.activate(school)
    chosen, data, rows, many, scope_name = _printed_rows(request, sy, periods, shown, scope)
    return render(request, 'teachers/learners_print.html', {
        'school': school, 'sy': sy, 'period_name': shown.label if shown else '',
        'teacher': request.user.get_full_name() or request.user.username, 'scope_name': scope_name,
        'columns': data['columns'], 'rows': rows, 'many': many, 'levels': policy.levels(), 'printed': timezone.localtime(),
    })


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
    periods = list(Quarter.objects.filter(school_year_id=enrollment.school_year_id).order_by('quarter_number')) if enrollment else []
    shown = P.pick(periods, request.GET.get('period'))
    if enrollment is None or shown is None:
        return JsonResponse({'error': 'Unknown learner'}, status=404)
    assigned = ClassAssignment.objects.filter(section=enrollment.section, school_year_id=enrollment.school_year_id, is_active=True)
    if enrollment.section.adviser_id != request.user.id:
        assigned = assigned.filter(teacher=request.user)
    assigned = list(assigned.select_related('subject', 'subject__subject_family', 'semester'))
    subjects = {a.subject_id: a.subject for a in assigned}
    if not subjects:
        return JsonResponse({'error': 'Access denied'}, status=403)
    counts = {a.subject_id: len(P.for_class(a, periods)) for a in assigned}
    recs = {}
    found = GradeComponent.objects.filter(P.HAS_GRADE, enrollment=enrollment, quarter__in=shown.quarters, subject_id__in=subjects).values(
        'enrollment_id', 'subject_id', grade=P.GRADE, ww=F('written_work_percent'), pt=F('performance_task_percent'),
        qa=F('quarterly_assessment_percent'))
    for r in P.merge(found, shown, count_of=lambda r: counts[r['subject_id']]):
        if float(r['grade']) < standing.PASSING:
            entry = standing.learner_subject(r, subjects[r['subject_id']])
            recs[entry['key']] = (entry['text'], standing.learner_facts(entry, subjects[r['subject_id']]))
    token = poro.register(recs, names=[s.subject_name for s in subjects.values()]) if recs else ''
    response = JsonResponse({'token': token})
    response['Cache-Control'] = 'no-store'
    return response
