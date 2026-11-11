"""Admin Portal: the audit trail (objective 4), every add, edit and delete in ARMS, sign-ins, and the actions pages log.

Entries come from audit/trail.py (record-level: which fields changed, from what to what) and from the pages that log
their own actions (validating grades, locking a period, imports). The System Administrator filters them by kind of
action, role, record type, date and a search, pages through them, downloads them as a designed Excel report, and
checks the hash chain: every entry carries the SHA-256 of the one before it, so an entry changed or removed later shows.
"""
from datetime import datetime, time, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.http import urlencode

from audit import trail
from audit.models import ActivityLog
from core.xlsx import Report

from .views import is_admin

PAGE = 50
KINDS = (
    ('', 'Everything'),
    ('added', 'Added'),
    ('edited', 'Edited'),
    ('deleted', 'Deleted'),
    ('signin', 'Sign-ins'),
    ('actions', 'Page actions'),
)
KIND_FILTERS = {
    'added': Q(action_type='DATA_ADDED'), 'edited': Q(action_type='DATA_EDITED'), 'deleted': Q(action_type='DATA_DELETED'),
    'signin': Q(action_type__in=('LOGIN_SUCCESS', 'LOGOUT', 'LOGIN_FAILED')),
    'actions': ~Q(action_type__in=('DATA_ADDED', 'DATA_EDITED', 'DATA_DELETED', 'LOGIN_SUCCESS', 'LOGOUT', 'LOGIN_FAILED')),
}
ROLES = (('', 'Every role'), ('admin', 'System Administrator'), ('schoolhead', 'School Head'), ('registrar', 'Registrar'),
         ('teacher', 'Teacher'))
TONES = {'DATA_ADDED': 'good', 'DATA_EDITED': 'info', 'DATA_DELETED': 'risk', 'LOGIN_FAILED': 'warn'}


def _date(text):
    try:
        return datetime.strptime(text or '', '%Y-%m-%d').date()
    except ValueError:
        return None


def _filtered(params):
    qs = ActivityLog.objects.all()
    kind = params.get('kind', '')
    if kind in KIND_FILTERS:
        qs = qs.filter(KIND_FILTERS[kind])
    if params.get('role'):
        qs = qs.filter(user_role=params['role'])
    if params.get('record'):
        qs = qs.filter(resource_type=params['record'])
    start, end = _date(params.get('from')), _date(params.get('to'))
    tz = timezone.get_current_timezone()
    if start:
        qs = qs.filter(timestamp__gte=datetime.combine(start, time.min, tz))
    if end:
        qs = qs.filter(timestamp__lt=datetime.combine(end + timedelta(days=1), time.min, tz))
    q = (params.get('q') or '').strip()
    if q:
        qs = qs.filter(Q(user_name__icontains=q) | Q(resource_type__icontains=q) | Q(action_type__icontains=q.replace(' ', '_'))
                       | Q(details_json__record__icontains=q) | Q(details_json__description__icontains=q) | Q(ip_address__icontains=q))
    return qs.order_by('-timestamp', '-pk')


def _entry(log):
    """One row as the page and the report show it."""
    d = log.details_json or {}
    action = d.get('action') or log.action_type.replace('_', ' ').capitalize()
    changes = d.get('changes') or []
    record = d.get('record') or (f'{log.resource_type} #{log.resource_id}' if log.resource_id else log.resource_type)
    detail = d.get('description', '')
    return {'log': log, 'action': action, 'tone': TONES.get(log.action_type, 'muted' if log.action_type in ('LOGIN_SUCCESS', 'LOGOUT') else ''),
            'type': d.get('record_type') or log.resource_type, 'record': record, 'changes': changes, 'more': d.get('more', 0),
            'detail': detail, 'role': (dict(ROLES).get(log.user_role, log.user_role.title()) if log.user_role else
                                       'Not signed in' if log.action_type == 'LOGIN_FAILED' else 'System')}


def _pages(page, count):
    """1 … 4 5 6 … 30"""
    out = []
    for n in [1, page - 1, page, page + 1, count]:
        if 1 <= n <= count and n not in out:
            if out and n - out[-1] > 1:
                out.append(None)
            out.append(n)
    return out


@login_required
@user_passes_test(is_admin, login_url='admin_panel:login')
def audit_trail(request):
    params = request.GET
    if params.get('verify'):
        checked, broken = trail.verify()
        if broken is None:
            messages.success(request, f'The chain is intact: all {checked:,} entries match their SHA-256 hashes, each linked to the one before.')
        else:
            bad = ActivityLog.objects.filter(pk=broken).first()
            when = timezone.localtime(bad.timestamp).strftime('%b %d, %Y %I:%M %p') if bad else ''
            messages.error(request, f'The chain breaks at entry #{broken} ({when}): it was changed or an entry before it was removed. '
                                    f'{checked:,} entries before it are intact.')
        keep = {k: v for k, v in params.items() if k != 'verify'}
        return redirect(request.path + ('?' + urlencode(keep) if keep else ''))
    qs = _filtered(params)
    if params.get('export'):
        return _export(request, qs)
    paginator = Paginator(qs, PAGE)
    page = paginator.get_page(params.get('page'))
    since = timezone.now() - timedelta(days=7)
    week = dict(ActivityLog.objects.filter(timestamp__gte=since).values_list('action_type').annotate(n=Count('id')))
    keep = {k: v for k, v in params.items() if k not in ('page', 'export') and v}
    return render(request, 'admin/audit_trail.html', {
        'entries': [_entry(log) for log in page], 'page': page, 'pages': _pages(page.number, paginator.num_pages),
        'total': paginator.count, 'kinds': KINDS, 'roles': ROLES, 'params': params, 'query': urlencode(keep),
        'records': list(ActivityLog.objects.order_by('resource_type').values_list('resource_type', flat=True).distinct()),
        'week': {'added': week.get('DATA_ADDED', 0), 'edited': week.get('DATA_EDITED', 0), 'deleted': week.get('DATA_DELETED', 0),
                 'signins': week.get('LOGIN_SUCCESS', 0), 'failed': week.get('LOGIN_FAILED', 0)},
    })


def _export(request, qs):
    report = Report('Audit Trail', 'ARMS', request.user.get_full_name() or request.user.username)
    shown = [label for key, label in KINDS if key == request.GET.get('kind', '')] or ['Everything']
    sh = report.sheet('Audit trail', [20, 24, 16, 14, 20, 34, 60, 16], subtitle=shown[0],
                      meta=[('Entries', f'{qs.count():,}'), ('Downloaded', timezone.localtime().strftime('%b %d, %Y %I:%M %p'))])
    rows = []
    for log in qs[:5000]:
        e = _entry(log)
        what = '; '.join(f'{c["field"]}: {c["old"]} → {c["new"]}' for c in e['changes']) or e['detail']
        rows.append([timezone.localtime(log.timestamp).strftime('%Y-%m-%d %H:%M:%S'), log.user_name or 'System', e['role'], e['action'],
                     e['type'], e['record'], what, log.ip_address])
    sh.table([{'title': 'When'}, {'title': 'Who'}, {'title': 'Role'}, {'title': 'Action'}, {'title': 'Record type'},
              {'title': 'Record', 'wrap': True}, {'title': 'What changed', 'wrap': True}, {'title': 'IP address'}], rows)
    return report.response(f'arms-audit-trail-{timezone.localdate():%Y%m%d}.xlsx')
