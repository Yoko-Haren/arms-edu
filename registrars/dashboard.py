"""Registrar dashboard: what needs the registrar now and where the current grading period stands."""
from django.urls import reverse

from core import periods as P

# how the period's classes are drawn, in order (label, color)
STATES = (
    ('validated', 'Validated', '#2E7D32'),
    ('locked', 'Finalized', '#1B5E20'),
    ('submitted', 'Ready to validate', '#123499'),
    ('ready', 'Not submitted', '#7C93D8'),
    ('encoding', 'Teacher encoding', '#E65100'),
    ('returned', 'Returned', '#C62828'),
    ('not_started', 'Not started', '#CBD5E0'),
)


def period_summary(period, rows):
    """The current period's classes by state, for the stacked bar."""
    total = len(rows)
    counts = {key: sum(1 for r in rows if r['state'] == key) for key, _, _ in STATES}
    return {
        'name': P.name(period), 'total': total, 'locked': bool(period and period.is_grades_locked),
        'states': [{'key': key, 'label': label, 'color': color, 'count': counts[key],
                    'percent': round(counts[key] * 100 / total, 1) if total else 0}
                   for key, label, color in STATES if counts[key]],
        'done': counts['validated'] + counts['locked'],
        'encode_due': P.due_text(period).strip() if period else '',
        'validate_due': P.due_text(period, field='grade_validation_deadline').strip() if period else '',
    }


def by_grade_level(rows):
    """The period's classes by grade level, lowest grade first: [{name, total, done, states}] for one small bar each."""
    levels = {}
    for r in rows:
        grade = r['assignment'].section.grade_level
        levels.setdefault(grade.id, (grade.sort_order, grade.grade_name, []))[2].append(r)
    out = []
    for _, name, mine in sorted(levels.values(), key=lambda v: v[0]):
        counts = {key: sum(1 for r in mine if r['state'] == key) for key, _, _ in STATES}
        out.append({'name': name, 'total': len(mine), 'done': counts['validated'] + counts['locked'],
                    'states': [{'label': label, 'color': color, 'count': counts[key], 'percent': round(counts[key] * 100 / len(mine), 1)}
                               for key, label, color in STATES if counts[key]]})
    return out


def todo(period, rows, numbers):
    """What needs the registrar, most urgent first: [{tone, icon, title, detail, url}]."""
    items = []
    grades = reverse('registrars:grade_validation')
    if period and not period.is_grades_locked:
        word = P.name(period)
        submitted = [r for r in rows if r['state'] == 'submitted']
        clean = sum(1 for r in submitted if r['clean'])
        days, overdue = P.deadline_info(period, field='grade_validation_deadline')
        if submitted:
            items.append((0 if overdue or (days is not None and days <= 3) else 1, 'warn' if overdue else 'info', 'fi-rr-shield-check',
                          f'Validate {len(submitted)} class{"es" if len(submitted) != 1 else ""} for {word}',
                          f'{clean} passed every check' + (f', {len(submitted) - clean} need a closer look' if clean < len(submitted) else '') +
                          (f'. {P.due_text(period, field="grade_validation_deadline").strip()}' if days is not None else '.'),
                          f'{grades}?period={period.id}&show=submitted'))
        waiting = [r for r in rows if r['state'] in ('not_started', 'encoding', 'ready')]
        if waiting and P.deadline_info(period)[1]:
            items.append((1, 'warn', 'fi-rr-time-quarter-past', f'{len(waiting)} class{"es" if len(waiting) != 1 else ""} not submitted after the deadline',
                          f'The {word} encoding deadline has passed. Remind their teachers from Grade Validation.', f'{grades}?period={period.id}'))
        if rows and all(r['state'] == 'validated' for r in rows):
            items.append((1, 'good', 'fi-rr-lock', f'Every class is validated: lock {word}', 'Locking makes the grades final for the School Head.',
                          f'{grades}?period={period.id}'))
    if numbers['sections_without_teacher']:
        n = numbers['sections_without_teacher']
        items.append((2, 'warn', 'fi-rr-chalkboard-user', f'{n} section{"s" if n != 1 else ""} without a teacher',
                      'Add their classes and teachers so the teachers can see them.', f'{reverse("registrars:section_list")}?show=noteacher'))
    if numbers['teachers_without_classes']:
        n = numbers['teachers_without_classes']
        items.append((3, 'info', 'fi-rr-user-time', f'{n} teacher{"s" if n != 1 else ""} with no class this school year',
                      'Their class records stay empty until a class list names them.', f'{reverse("registrars:teacher_list")}?show=noclasses'))
    if numbers['pending_password']:
        n = numbers['pending_password']
        items.append((4, 'muted', 'fi-rr-key', f'{n} teacher{"s have" if n != 1 else " has"} not signed in yet',
                      'They still have the temporary password you gave them.', f'{reverse("registrars:teacher_list")}?show=temporary'))
    if numbers['empty_sections']:
        n = numbers['empty_sections']
        items.append((5, 'muted', 'fi-rr-folder-open', f'{n} section{"s" if n != 1 else ""} with no learners',
                      'Upload their class lists to enroll the learners.', f'{reverse("registrars:section_list")}?show=empty'))
    items.sort(key=lambda i: i[0])
    return [{'tone': tone, 'icon': icon, 'title': title, 'detail': detail, 'url': url} for _, tone, icon, title, detail, url in items]


RING_R = 52                         # the ring of the period's classes: radius in the 140-unit SVG, and the gap between parts
RING_GAP = 2.5


def ring(summary):
    """The period's classes as arcs of one ring, in the order of STATES: [{color, label, count, dash, offset}]."""
    import math
    total, length = summary['total'], 2 * math.pi * RING_R
    arcs, start = [], 0.0
    for s in summary['states']:
        size = length * s['count'] / total if total else 0
        gap = RING_GAP if len(summary['states']) > 1 else 0
        arcs.append({'color': s['color'], 'label': s['label'], 'count': s['count'],
                     'dash': f'{max(size - gap, 0.1):.2f} {length:.2f}', 'offset': f'{-start:.2f}'})
        start += size
    return {'r': RING_R, 'arcs': arcs, 'percent': round(summary['done'] * 100 / total) if total else 0}


def enrollment_by_grade(school, sy):
    """Learners enrolled now, by grade level, lowest first: [{name, total, male, female, share}] (share of the largest)."""
    from django.db.models import Count, Q
    from enrollment.models import Enrollment

    rows = (Enrollment.objects.filter(school_year=sy, section__school=school, status__in=('Enrolled', 'Transferred_In'))
            .values('section__grade_level__grade_name', 'section__grade_level__sort_order')
            .annotate(total=Count('id'), male=Count('id', filter=Q(student__sex='M')), female=Count('id', filter=Q(student__sex='F')))
            .order_by('section__grade_level__sort_order'))
    rows = list(rows)
    top = max((r['total'] for r in rows), default=0)
    return [{'name': r['section__grade_level__grade_name'], 'total': r['total'], 'male': r['male'], 'female': r['female'],
             'share': round(r['total'] * 100 / top) if top else 0} for r in rows]
