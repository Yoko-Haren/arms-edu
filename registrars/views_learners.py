"""Registrar: Learners, every learner of the school in one searchable list, and each learner's page.

The list searches by name or LRN, filters by grade, section, status, sex and DepEd's learner groups, and sorts. A
learner's page holds the register details, the grades and attendance of the school year, the report card, and the
learner's movement. The movement recorded here (dropped out, transferred out, transferred in, back in school) is what
SF1's remarks, SF2, SF4, SF5 and SF6 print, with its date, so the registrar records it once.
"""
from datetime import date

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import urlencode

from academics.models import GradeLevel, SchoolYear, Section
from core import grading_policy as policy
from core import periods as P
from core.caching import bump_data_version
from documentation import form_data as FD
from documentation import sf9 as SF9
from enrollment.models import Enrollment
from grades.models import GradeComponent

ACTIVE = ('Enrolled', 'Transferred_In')
LEFT = ('Transferred_Out', 'Dropped')
PAGE_SIZE = 25
STATUS = {'Enrolled': ('Enrolled', 'good'), 'Transferred_In': ('Transferred in', 'info'), 'Transferred_Out': ('Transferred out', 'warn'),
          'Dropped': ('Dropped out', 'risk'), 'Graduated': ('Graduated', 'info'), 'Inactive': ('Inactive', 'muted'), 'Deceased': ('Deceased', 'muted')}
SORTS = {
    'name': ('Surname A to Z', ('student__last_name', 'student__first_name')),
    'name_desc': ('Surname Z to A', ('-student__last_name', '-student__first_name')),
    'grade': ('Grade and section', ('section__grade_level__sort_order', 'section__section_name', 'student__last_name', 'student__first_name')),
    'lrn': ('LRN', ('student__lrn',)),
    'age': ('Youngest first', ('-student__birth_date', 'student__last_name')),
    'recent': ('Newest enrolled', ('-enrollment_date', '-id')),
}
GROUPS = {'4ps': ('4Ps', Q(is_4ps=True) | Q(student__is_4ps=True)), 'lwd': ('With disability', Q(student__has_disability=True)),
          'ip': ('Indigenous peoples', Q(student__is_ip=True)), 'balik': ('Balik-Aral', Q(is_balik_aral=True) | Q(enrollment_type='Balik_Aral')),
          'late': ('Late enrollee', Q(remarks__regex=r'\yLE\y'))}
MOVES = {'Dropped': 'Dropped out', 'Transferred_Out': 'Transferred out', 'Enrolled': 'Back in class', 'Transferred_In': 'Transferred in'}


def _years(school):
    return list(SchoolYear.objects.filter(school=school).order_by('-year_start'))


def _chosen_year(request, years):
    wanted = request.GET.get('sy')
    return next((y for y in years if str(y.id) == str(wanted)), None) or next((y for y in years if y.is_current), None) or (years[0] if years else None)


def _status(e):
    label, tone = STATUS.get(e.status, (e.status, 'muted'))
    return {'label': label, 'tone': tone}


def learner_list(request):
    school = request.user.profile.school
    years = _years(school)
    sy = _chosen_year(request, years)
    if sy is None:
        return render(request, 'registrars/learners/list.html', {'school': school, 'no_year': True})
    everyone = Enrollment.objects.filter(section__school=school, school_year=sy)
    counts = everyone.aggregate(active=Count('id', filter=Q(status__in=ACTIVE)), male=Count('id', filter=Q(status__in=ACTIVE, student__sex='M')),
                                female=Count('id', filter=Q(status__in=ACTIVE, student__sex='F')), left=Count('id', filter=Q(status__in=LEFT)))
    rows = everyone.select_related('student', 'section', 'section__grade_level', 'section__adviser')
    q = ' '.join(request.GET.get('q', '').split())
    grade, section, status, sex, group = (request.GET.get(k, '') for k in ('grade', 'section', 'status', 'sex', 'group'))
    for word in q.split():
        rows = rows.filter(Q(student__lrn__icontains=word) | Q(student__last_name__icontains=word) | Q(student__first_name__icontains=word)
                           | Q(student__middle_name__icontains=word))
    if grade.isdigit():
        rows = rows.filter(section__grade_level_id=grade)
    if section.isdigit():
        rows = rows.filter(section_id=section)
    if status == 'active':
        rows = rows.filter(status__in=ACTIVE)
    elif status == 'left':
        rows = rows.filter(status__in=LEFT)
    elif status in STATUS:
        rows = rows.filter(status=status)
    if sex in ('M', 'F'):
        rows = rows.filter(student__sex=sex)
    if group in GROUPS:
        rows = rows.filter(GROUPS[group][1])
    sort = request.GET.get('sort', 'name')
    sort = sort if sort in SORTS else 'name'
    rows = rows.order_by(*SORTS[sort][1], 'id')
    page = Paginator(rows, PAGE_SIZE).get_page(request.GET.get('page'))
    cutoff = FD.first_friday_of_june(sy)
    learners = []
    for e in page.object_list:
        st = e.student
        learners.append({'id': e.id, 'name': FD.short_name(st.last_name, st.first_name, st.middle_name, st.name_extension), 'lrn': st.lrn or '',
                         'sex': st.sex, 'age': FD.age_on(st.birth_date, cutoff), 'grade': e.section.grade_level.grade_name, 'section': e.section.section_name,
                         'adviser': FD.adviser_of(e.section).title(), 'status': _status(e)})
    grades = GradeLevel.objects.filter(id__in=everyone.values('section__grade_level_id')).order_by('sort_order')
    sections = Section.objects.filter(school=school, school_year=sy).select_related('grade_level').order_by('grade_level__sort_order', 'section_name')
    query = {k: v for k, v in (('sy', sy.id if not sy.is_current else ''), ('q', q), ('grade', grade), ('section', section), ('status', status),
                               ('sex', sex), ('group', group), ('sort', sort if sort != 'name' else '')) if v}
    return render(request, 'registrars/learners/list.html', {
        'school': school, 'years': years, 'current_sy': sy, 'learners': learners, 'page': page, 'counts': counts, 'q': q, 'grade': grade,
        'section': section, 'status': status, 'sex': sex, 'group': group, 'sort': sort, 'grades': grades, 'sections': sections,
        'sorts': [(k, v[0]) for k, v in SORTS.items()], 'groups': [(k, v[0]) for k, v in GROUPS.items()], 'query': urlencode(query),
        'filtered': any((q, grade, section, status, sex, group)), 'total': page.paginator.count,
    })


def _grades(enrollment, periods):
    """Every subject of the learner this school year, with a grade per period and the final grade."""
    from academics.models import Subject
    found = {}
    for subject_id, period_id, transmuted, initial in GradeComponent.objects.filter(enrollment=enrollment, quarter__in=periods).values_list(
            'subject_id', 'quarter_id', 'transmuted_grade', 'initial_grade'):
        found.setdefault(subject_id, {})[period_id] = P.grade_value(transmuted, initial)
    rows = []
    for subject in Subject.objects.filter(id__in=found).order_by('subject_code'):
        values = [found[subject.id].get(p.id) for p in periods]
        shown = [None if v is None else int(FD.round_half_up(v)) for v in values]
        rows.append({'code': subject.subject_code, 'name': subject.subject_name, 'grades': shown, 'final': P.final_grade(shown, len(periods))})
    return rows


def learner_detail(request, enrollment_id):
    from attendance.models import AttendanceSummary
    from students.models import Guardian, StudentAddress
    school = request.user.profile.school
    e = get_object_or_404(Enrollment.objects.select_related('student', 'section', 'section__grade_level', 'section__adviser', 'section__strand', 'school_year'),
                          id=enrollment_id, section__school=school)
    policy.activate(school)
    st, sy = e.student, e.school_year
    if request.method == 'POST':
        return _record_movement(request, e)
    periods = FD.periods_of(sy)
    person = next((p for p in FD.learners(e.section, sy) if p['enrollment'].id == e.id), None)
    attendance = []
    for month, label in FD.months_of(sy):
        s = AttendanceSummary.objects.filter(enrollment=e, school_year=sy, month=month).first()
        if s:
            attendance.append({'label': label, 'days': s.total_school_days, 'present': s.days_present, 'absent': s.days_absent, 'tardy': s.days_late})
    history = Enrollment.objects.filter(student=st).select_related('section', 'section__grade_level', 'school_year').order_by('-school_year__year_start')
    return render(request, 'registrars/learners/detail.html', {
        'school': school, 'e': e, 'st': st, 'sy': sy, 'person': person, 'status': _status(e), 'periods': periods, 'grades': _grades(e, periods),
        'attendance': attendance, 'moves': [(k, v) for k, v in MOVES.items() if k != e.status], 'today': date.today().isoformat(),
        'guardians': Guardian.objects.filter(student=st).order_by('id'), 'address': StudentAddress.objects.filter(student=st).order_by('-is_primary').first(),
        'history': [{'id': h.id, 'year': h.school_year.year_label, 'grade': h.section.grade_level.grade_name, 'section': h.section.section_name,
                     'status': _status(h), 'here': h.id == e.id} for h in history],
        'left': e.status in LEFT,
    })


def _record_movement(request, e):
    new = request.POST.get('status', '')
    url = reverse('registrars:learner_detail', args=[e.id])
    if new not in MOVES or new == e.status:
        messages.error(request, 'Choose what happened to the learner.')
        return redirect(url)
    try:
        when = date.fromisoformat(request.POST.get('date', ''))
    except ValueError:
        messages.error(request, 'Give the date it happened.')
        return redirect(url)
    sy = e.school_year
    if not sy.date_start <= when <= sy.date_end:
        messages.error(request, f'The date must be inside SY {sy.year_label}.')
        return redirect(url)
    reason = ' '.join(request.POST.get('reason', '').split())[:200]
    e.set_status(new, changed_by=request.user, reason=reason)
    fields = {'status_date': when}
    if new == 'Transferred_In':
        fields.update(enrollment_type='Transferred_In', enrollment_date=when, previous_school_name=reason[:255] or e.previous_school_name)
    Enrollment.objects.filter(pk=e.pk).update(**fields)      # save() stamps today: the form gives the real date
    bump_data_version()
    from .views import _log
    _log(request, 'UPDATE_ENROLLMENT', e.id, f'{e.student.last_name}, {e.student.first_name}: {MOVES[new]} on {when:%Y-%m-%d}. {reason}', 'Enrollment')
    messages.success(request, f'{e.student.last_name}, {e.student.first_name}: {MOVES[new].lower()} on {when:%b %d, %Y}. '
                              'SF1, SF2, SF4, SF5 and SF6 now say so.')
    return redirect(url)


def learner_report_card(request, enrollment_id):
    school = request.user.profile.school
    e = get_object_or_404(Enrollment.objects.select_related('section', 'section__grade_level', 'section__strand', 'section__strand__track', 'section__adviser',
                                                            'section__school', 'school_year'), id=enrollment_id, section__school=school)
    try:
        content, name = SF9.build_one(e.section, e.school_year, e.id)
    except SF9.CardUnavailable as exc:
        messages.error(request, str(exc))
        return redirect('registrars:learner_detail', enrollment_id=e.id)
    response = HttpResponse(content, content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename="{name}"'
    return response
