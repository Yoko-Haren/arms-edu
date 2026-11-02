"""Teacher: School Forms, what an adviser gives the school forms and what comes out of them.

The registrar's SF1 starts everything. The adviser adds what only the adviser knows: SF2 (attendance, every month),
SF3 (books) and SF8 (weight and height), by upload, and the comments on the report cards. From these and the grades
ARMS fills the section's forms: SF1, SF2, SF3, SF5, SF8, and SF9, one learner at a time or
the whole section as a folder in a zip, in DepEd's own workbook. The registrar's SF4 and SF6 add up the same records. Only
a section's adviser can open it; the page follows the school year's grading system.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import urlencode
from django.views.decorators.http import require_POST

from academics.models import Section
from communication.notify import notify, school_registrars
from core import periods as P
from core.caching import bump_data_version
from documentation import form_data as FD
from documentation import form_uploads as FU
from documentation import school_forms as SF
from documentation import sf9 as SF9
from documentation.models import LearnerComment
from enrollment.models import Enrollment

from . import records as R
from .views_records import _denied, _log, _teacher, _xlsx, teacher_context

KINDS = {
    'SF2': {'title': 'Daily attendance', 'badge': 'SF2'},
    'SF3': {'title': 'Books issued and returned', 'badge': 'SF3'},
    'SF8': {'title': 'Weight and height', 'badge': 'SF8'},
}


def _sections(request, school, sy):
    if sy is None:
        return []
    return list(Section.objects.filter(school=school, school_year=sy, adviser=request.user, is_active=True)
                .select_related('grade_level', 'strand', 'strand__track', 'adviser', 'school').order_by('grade_level__sort_order', 'section_name'))


def _my_section(request, section_id):
    profile = _teacher(request)
    return get_object_or_404(Section.objects.select_related('grade_level', 'strand', 'strand__track', 'adviser', 'school', 'school_year'),
                             id=section_id, adviser=request.user, school=profile.school)


def _page(section):
    return f'{reverse("teachers:school_forms")}?{urlencode({"section": section.id})}'


def _forms(section, sy, periods):
    """The downloads of the section's forms, in DepEd's order: what each is, what it is filled from, what it asks for."""
    senior = section.grade_level.is_senior_high
    terms = P.prefix(section.school, periods) == 'T'
    word = P.word(section.school, periods)
    out = [
        {'code': 'SF1', 'title': 'School Register', 'from': 'The SF1 the registrar imported', 'semester': senior},
        {'code': 'SF2', 'title': 'Daily Attendance', 'from': 'Your SF2 uploads', 'month': True},
        {'code': 'SF3', 'title': 'Books Issued and Returned', 'from': 'Your SF3 upload, and the section\'s subjects', 'semester': senior},
        {'code': 'SF5', 'title': f'End of {word} Status' if senior else 'Report on Promotion', 'badge': 'SF5A' if senior else 'SF5',
         'from': f'Grades of every {word.lower()}', 'semester': senior},
    ]
    if not senior and not terms:                           # a three-term year's summaries are the TERM sheets of SF9
        out.append({'code': 'SUMMARY', 'badge': 'Grades', 'title': 'Summary of Final Grades', 'from': f'Grades of every {word.lower()}'})
    out.append({'code': 'SF8', 'title': "Health and Nutrition", 'from': 'Your SF8 upload', 'stage': True})
    return terms, out


def _comments(section, periods, people):
    saved = {}
    for e, q, text in LearnerComment.objects.filter(enrollment__in=[p['enrollment'] for p in people], quarter__in=periods).values_list(
            'enrollment_id', 'quarter_id', 'comment'):
        saved.setdefault(e, {})[q] = text
    return saved


@login_required
def school_forms(request):
    if not _teacher(request):
        return _denied(request)
    school, sy, periods, current, _ = teacher_context(request)
    sections = _sections(request, school, sy)
    section = next((s for s in sections if str(s.id) == request.GET.get('section')), None) or (sections[0] if sections else None)
    context = {'school': school, 'sy': sy, 'sections': sections, 'section': section}
    if section is None:
        return render(request, 'teachers/school_forms/index.html', context)
    terms, forms = _forms(section, sy, periods)
    status = FU.section_status(section, sy, periods)
    people = FD.learners(section, sy)
    saved = _comments(section, periods, people)
    learners = [{'id': p['enrollment'].id, 'name': p['short'], 'lrn': p['lrn'], 'sex': p['sex'], 'left': not p['active'],
                 'comments': {str(q): t for q, t in saved.get(p['enrollment'].id, {}).items()},
                 'url': reverse('teachers:report_card', args=[section.id, p['enrollment'].id])} for p in people]
    context.update({
        'current': current, 'current_name': P.name(current), 'periods': periods, 'terms': terms, 'forms': forms, 'status': status,
        'period_word': P.word(school, periods), 'period_word_plural': P.word(school, periods, plural=True),
        'system': {'T': 'Trimestral', 'S': 'Semestral'}.get(P.prefix(school, periods), 'Quarterly'),
        'months': FD.months_of(sy), 'month': FD.default_month(sy), 'senior': section.grade_level.is_senior_high,
        'semesters': SF.semesters(sy), 'semester': getattr(SF.semester_of(sy), 'semester_number', 1), 'stages': SF.STAGES.items(), 'learners': learners, 'first_learners': learners[:6],
        'official_card': terms, 'card_periods': [{'id': p.id, 'name': P.name(p)} for p in periods], 'no_row': SF9.fit(section, sy, periods)[1] if terms else [],
        'commented': sum(1 for row in learners if row['comments']),
        'needs': _needs(status),
    })
    return render(request, 'teachers/school_forms/index.html', context)


def _needs(status):
    """What the adviser still owes the forms, for the three upload cards."""
    months = status['months_missing']
    return {
        'SF2': {'done': not months, 'state': 'Up to date' if not months else f'{len(months)} month{"" if len(months) == 1 else "s"} to upload',
                'detail': f'{status["months_done"]} of {len(status["months"])} months so far' + (f'. Missing: {", ".join(months[:3])}' + ('...' if len(months) > 3 else '') if months else '')},
        'SF3': {'done': bool(status['books']), 'state': f'{status["books"]} of {status["learners"]} learners' if status['books'] else 'Not uploaded',
                'detail': 'Issued and returned dates; SF3 keeps them'},
        'SF8': {'done': bool(status['weighed']['BOSY']), 'state': f'{status["weighed"]["BOSY"]} of {status["learners"]} weighed' if status['weighed']['BOSY'] else 'Not uploaded',
                'detail': f'Beginning of the year: {status["weighed"]["BOSY"]}. End of the year: {status["weighed"]["EOSY"]}.'},
    }


@login_required
def section_form(request, section_id):
    """A school form of the adviser's section, ready to download."""
    if not _teacher(request):
        return _denied(request)
    section = _my_section(request, section_id)
    code = request.GET.get('code', '')
    if code not in ('SF1', 'SF2', 'SF3', 'SF5', 'SF8', 'SF9', 'SUMMARY'):
        messages.error(request, 'Choose one of the forms of your advisory class.')
        return redirect(_page(section))
    sy = section.school_year
    try:
        content, name = SF.build(code, school=section.school, sy=sy, section=section, month=SF.month_param(request.GET.get('month')),
                                 semester=SF.semester_param(sy, request.GET.get('semester')), stage=request.GET.get('stage', 'BOSY'))
    except SF.FormUnavailable as exc:
        messages.error(request, str(exc))
        return redirect(_page(section))
    return _xlsx(content, name)


@login_required
def report_cards(request, section_id):
    """The folder of a section's report cards, for the page to fetch one by one and zip: a class of forty is larger than
    one server response can carry on Vercel."""
    if not _teacher(request):
        return _denied(request)
    section = _my_section(request, section_id)
    sy = section.school_year
    people = FD.learners(section, sy)
    return JsonResponse({'folder': SF9.folder_of(section, sy), 'files': [
        {'name': SF9.learner_file(p, section, sy), 'url': reverse('teachers:report_card', args=[section.id, p['enrollment'].id])} for p in people]})


@login_required
def report_card(request, section_id, enrollment_id):
    """One learner's SF9."""
    if not _teacher(request):
        return _denied(request)
    section = _my_section(request, section_id)
    enrollment = get_object_or_404(Enrollment, id=enrollment_id, section=section)
    try:
        content, name = SF9.build_one(section, section.school_year, enrollment.id)
    except SF9.CardUnavailable as exc:
        messages.error(request, str(exc))
        return redirect(_page(section))
    return _xlsx(content, name)


@login_required
@require_POST
def save_comment(request, section_id):
    """The adviser's comments on one learner, one per grading period, for the report card."""
    if not _teacher(request):
        return _denied(request)
    section = _my_section(request, section_id)
    enrollment = get_object_or_404(Enrollment, id=request.POST.get('enrollment') or 0, section=section)
    for period in FD.periods_of(section.school_year):
        text = (request.POST.get(f'comment-{period.id}') or '').strip()[:300]
        if text:
            LearnerComment.objects.update_or_create(enrollment=enrollment, quarter=period, defaults={'comment': text, 'written_by': request.user})
        else:
            LearnerComment.objects.filter(enrollment=enrollment, quarter=period).delete()
    messages.success(request, f'Comments saved for {enrollment.student.last_name}, {enrollment.student.first_name}.')
    return redirect(_page(section))


# ---------------------------------------------------------------------------------------------
# uploads
# ---------------------------------------------------------------------------------------------

def _roster(section, sy):
    return R.section_roster(section, sy)


@login_required
def form_upload(request):
    """Upload SF2, SF3 or SF8: the file is read and shown back; nothing is saved here."""
    if not _teacher(request):
        return _denied(request)
    school, sy, periods, current, _ = teacher_context(request)
    sections = _sections(request, school, sy)
    if not sections:
        messages.error(request, 'You do not advise a section this school year.')
        return redirect('teachers:school_forms')
    chosen = request.POST.get('section') or request.GET.get('section') or ''
    section = next((s for s in sections if str(s.id) == chosen), sections[0])
    kind = request.POST.get('kind') or request.GET.get('kind') or 'SF2'
    kind = kind if kind in KINDS else 'SF2'
    month = SF.month_param(request.POST.get('month') or request.GET.get('month')) or FD.default_month(sy)
    stage = request.POST.get('stage') or request.GET.get('stage') or 'BOSY'
    stage = stage if stage in SF.STAGES else 'BOSY'
    context = {'sy': sy, 'sections': sections, 'section': section, 'kind': kind, 'kinds': KINDS, 'months': FD.months_of(sy), 'month': month,
               'stage': stage, 'stages': SF.STAGES.items(), 'senior': section.grade_level.is_senior_high}
    if request.method == 'POST':
        uploaded = request.FILES.get('file')
        if not uploaded:
            return render(request, 'teachers/school_forms/upload.html', dict(context, error='Choose the file to upload.'))
        roster = _roster(section, sy)
        try:
            if kind == 'SF2':
                result = FU.read_sf2(uploaded, section, sy, month, roster)
            elif kind == 'SF3':
                result = FU.read_sf3(uploaded, section, sy, roster)
            else:
                result = FU.read_sf8(uploaded, section, sy, stage, roster)
        except FU.UploadProblem as exc:
            return render(request, 'teachers/school_forms/upload.html', dict(context, error=str(exc)))
        token = R.remember(request.user, {'section': section.id, 'file': uploaded.name, 'result': result})
        return redirect(f'{reverse("teachers:form_review")}?token={token}')
    return render(request, 'teachers/school_forms/upload.html', context)


def _review_context(request, stash, token, problems=None):
    section = _my_section(request, stash['section'])
    result = stash['result']
    roster = _roster(section, section.school_year)
    by_id = {p['id']: p for p in roster}
    ready, settle = [], []
    for row in result['rows']:
        row['label'] = R.display_name(by_id[row['enrollment']]) if row.get('enrollment') in by_id else ''
        (ready if row['status'] == 'matched' else settle).append(row)
    moved = [row for row in result['rows'] if row.get('moved')]
    return {'section': section, 'token': token, 'file': stash['file'], 'result': result, 'ready': ready, 'settle': settle, 'moved': moved,
            'roster': [{'id': p['id'], 'name': R.display_name(p)} for p in roster], 'problems': problems or [],
            'warns': [t for level, t in result['checks'] if level == 'warn'], 'kind_title': KINDS[result['kind']]['title'],
            'missing': [R.display_name(by_id[i]) for i in result['missing'] if i in by_id]}


@login_required
def form_review(request):
    if not _teacher(request):
        return _denied(request)
    token = request.GET.get('token') or request.POST.get('token')
    stash = R.recall(request.user, token)
    if not stash:
        messages.error(request, 'That upload has expired. Upload the file again.')
        return redirect('teachers:form_upload')
    if request.method == 'POST':
        section = _my_section(request, stash['section'])
        result = stash['result']
        choices = {k[4:]: v for k, v in request.POST.items() if k.startswith('row-')}
        plan, problems = FU.decide(result, choices)
        if problems:
            return render(request, 'teachers/school_forms/review.html', _review_context(request, stash, token, problems))
        if not plan:
            messages.info(request, 'Nothing was saved: no row was matched to a learner.')
            return redirect(_page(section))
        count = FU.SAVERS[result['kind']](section, section.school_year, result, plan, request.user)
        R.forget(request.user, token)
        bump_data_version()
        _log(request, 'UPLOAD_FORM', section.id, f'Uploaded {result["kind"]} ({result["label"]}) for {section.section_name}: {count} learners from "{stash["file"]}"')
        moved = [row for row, _ in plan if row.get('moved')]
        if moved:
            notify(school_registrars(section.school), 'LEARNER_MOVEMENT', f'{section.section_name}: learner movement in SF2',
                   f'{request.user.get_full_name() or request.user.username} noted '
                   + '; '.join(f'{row["name"]} ({row["remarks"]})' for row in moved[:4]) + '. Record it on Learners.',
                   reverse('registrars:learner_list'))
        messages.success(request, f'{result["kind"]} saved for {section.section_name}: {count} learner{"" if count == 1 else "s"} ({result["label"]}). '
                                  'The forms that use it are up to date.')
        return redirect(_page(section))
    return render(request, 'teachers/school_forms/review.html', _review_context(request, stash, token))
