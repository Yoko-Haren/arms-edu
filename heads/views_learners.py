"""School Head: the learners of the Academic Dashboard's selection, page by page, and one learner with a recommendation in
every subject.

The table sits under the forecast. It follows the dashboard's selection (school year, period or all periods, the
discipline or subject, the grade levels): each learner's average in it, the three grade components (with grades), the
performance level of the school's DepEd order and how many of its subjects are below 75. A school has thousands of
learners, so the server filters, sorts and pages them (PAGE rows a page) and keeps the list in the cache until grades
change. A learner opens on the same dialog as the teacher's table (teachers/learners.record), worded for the School
Head to pass on to the teachers.
"""
from collections import defaultdict
from statistics import fmean as mean

from django.contrib.auth.decorators import login_required
from django.core.cache import caches
from django.db.models import F
from django.http import JsonResponse
from django.urls import reverse

from academics.models import Section
from core import grading_policy as policy
from core.caching import data_version
from core.periods import GRADE, HAS_GRADE
from enrollment.models import Enrollment
from grades.models import GradeComponent
from teachers import learners
from teachers.views_advisory import section_assignments

from .views_academic import ACTIVE, _head_school, _selection

PAGE = 25
SELECTION = ('view_mode', 'subject_family', 'school_year', 'quarter', 'grade_level', 'subject')


def _rows(school, params):
    """Every learner of the selection: [{id, name, lrn, section, section_name, average, ww, pt, qa, level, below}]."""
    s = _selection(params, school)
    key = 'heads-learners:' + ':'.join(str(x) for x in (school.id, data_version(), policy.key_of(school),
                                                       *(params.get(k, '') for k in SELECTION)))
    cache = caches['default']
    cached = cache.get(key)
    if cached is not None:
        return cached, s
    out = []
    if s['ids'] and (s['period'] or s['all_periods']):
        base = GradeComponent.objects.filter(HAS_GRADE, subject_id__in=s['ids'], enrollment__school_year=s['sy'],
                                             enrollment__status__in=ACTIVE, enrollment__section__school=school)
        base = base.filter(quarter__in=s['span']) if s['all_periods'] else base.filter(quarter=s['period'])
        per = defaultdict(lambda: defaultdict(list))        # enrollment -> subject -> [(grade, ww, pt, qa)] (all periods: several)
        for e, subject, g, ww, pt, qa in base.values_list('enrollment_id', 'subject_id', GRADE, 'written_work_percent',
                                                          'performance_task_percent', 'quarterly_assessment_percent'):
            per[e][subject].append((float(g), ww, pt, qa))
        people = {e.id: e for e in Enrollment.objects.filter(id__in=per).select_related('student', 'section', 'section__grade_level')}
        for e_id, subjects in per.items():
            e = people.get(e_id)
            if e is None:
                continue
            grades, parts = [], {'ww': [], 'pt': [], 'qa': []}
            for values in subjects.values():                  # a subject's grade: its period, or the mean of its periods so far
                grades.append(mean(v[0] for v in values))
                for i, k in enumerate(('ww', 'pt', 'qa'), start=1):
                    found = [float(v[i]) for v in values if v[i] is not None]
                    if found:
                        parts[k].append(mean(found))
            average = round(mean(grades), 1)
            out.append({'id': e.id, 'name': learners.display(e.student), 'lrn': e.student.lrn or '', 'section': e.section_id,
                        'section_name': f'{e.section.grade_level.grade_name} {e.section.section_name}',
                        'grade_order': e.section.grade_level.sort_order, 'average': average,
                        **{k: round(mean(v), 1) if v else None for k, v in parts.items()},
                        'level': policy.level_of(average)['key'], 'below': sum(1 for g in grades if g < policy.PASSING)})
    cache.set(key, out, 6 * 3600)
    return out, s


SORTS = {
    'name': lambda r: (r['name'].lower(),),
    'section': lambda r: (r['grade_order'], r['section_name'], r['name'].lower()),
    'low': lambda r: (r['average'], r['name'].lower()),
    'high': lambda r: (-r['average'], r['name'].lower()),
    'below': lambda r: (-r['below'], r['average'], r['name'].lower()),
}


@login_required
def learner_list(request):
    """One page of the selection's learners, filtered by section, level and a name or LRN, in the order asked for."""
    school = _head_school(request)
    if school is None:
        return JsonResponse({'error': 'Access denied'}, status=403)
    policy.activate(school)
    rows, s = _rows(school, request.GET)
    sections = sorted({(r['grade_order'], r['section_name'], r['section']) for r in rows})
    q = (request.GET.get('q') or '').strip().lower()
    section, level = request.GET.get('section') or '', request.GET.get('level') or ''
    shown = [r for r in rows if (not section or str(r['section']) == section) and (not level or r['level'] == level)
             and (not q or q in r['name'].lower() or q in r['lrn'])]
    shown.sort(key=SORTS.get(request.GET.get('sort'), SORTS['name']))
    pages = max(1, -(-len(shown) // PAGE))
    try:
        page = min(max(int(request.GET.get('page') or 1), 1), pages)
    except ValueError:
        page = 1
    levels = {lv['key']: lv for lv in policy.levels()}
    rows_out = [dict({k: r[k] for k in ('id', 'name', 'lrn', 'section_name', 'average', 'ww', 'pt', 'qa', 'below')},
                     level={'key': r['level'], 'short': levels[r['level']]['short']}) for r in shown[(page - 1) * PAGE:page * PAGE]]
    counts = defaultdict(int)
    for r in rows:
        counts[r['level']] += 1
    response = JsonResponse({
        'rows': rows_out, 'total': len(shown), 'all': len(rows), 'page': page, 'pages': pages, 'per_page': PAGE,
        'sections': [{'id': sid, 'name': name} for _, name, sid in sections],
        'levels': [{'key': lv['key'], 'name': lv['name'], 'count': counts[lv['key']]} for lv in policy.levels()],
        'components': [{'key': k, 'abbr': a, 'name': n} for k, n, a in policy.components()],
        'scope': s['scope_name'], 'period': s['shown_period'],
    })
    response['Cache-Control'] = 'no-store'
    return response


@login_required
def learner_record(request, enrollment_id):
    """One learner of the school: the record and a recommendation in every subject (teachers/learners.record)."""
    school = _head_school(request)
    if school is None:
        return JsonResponse({'error': 'Access denied'}, status=403)
    policy.activate(school)
    s = _selection(request.GET, school)
    enrollment = Enrollment.objects.select_related('student', 'section', 'section__grade_level').filter(
        id=enrollment_id, school_year=s['sy'], section__school=school).first() if s['sy'] else None
    if enrollment is None:
        return JsonResponse({'error': 'Not a learner of this school year'}, status=404)
    period = s['period'] or s['ref_period']
    data = learners.record(enrollment, s['sy'], s['periods'], period, section_assignments(enrollment.section, s['sy']), reader='head')
    data['poll'] = reverse('heads-poro', args=[data['token']]) if data['token'] else ''
    response = JsonResponse(data)
    response['Cache-Control'] = 'no-store'
    return response
