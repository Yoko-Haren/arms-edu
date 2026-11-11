"""The audit trail (objective 4 of the capstone): every record a person adds, edits or deletes, and every sign-in.

How it works:
- AuditMiddleware notes who is acting (the signed-in user, their role, address and browser) for the request.
- For the TRACKED models, a snapshot of each record is taken when it is loaded (post_init), so an edit can say exactly
  which fields changed, from what to what, without another query. post_save logs an add or an edit (an edit that changes
  nothing is not logged), post_delete a delete. Passwords, tokens and keys are never stored: they show as changed only.
- An event is kept only when its transaction commits (transaction.on_commit), so a change that was rolled back is never
  in the trail. At the end of the request the events are written in one go, each one chained to the previous entry by
  its SHA-256 hash (audit.models.ActivityLog), so editing or removing an entry later breaks the chain (verify()).
- Sign-ins, sign-outs and failed sign-ins come from Django's auth signals.

Changes made outside a web request (management commands such as the demo seeders) are not logged: no person made them.
The actions pages already log themselves (validating grades, locking a period, imports) stay as they are; this adds the
record-level detail underneath them.
"""
import hashlib
import json
from contextvars import ContextVar
from datetime import timedelta

from django.apps import apps
from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.db import transaction
from django.db.models.signals import post_delete, post_init, post_save
from django.utils import timezone

# app_label.Model -> how the trail names it
TRACKED = {
    'students.Student': 'Learner', 'students.StudentAddress': 'Learner address', 'students.Guardian': 'Parent or guardian',
    'students.GuardianContact': 'Guardian contact',
    'enrollment.Enrollment': 'Enrollment',
    'academics.School': 'School', 'academics.SchoolYear': 'School year', 'academics.Quarter': 'Grading period',
    'academics.GradeLevel': 'Grade level', 'academics.Section': 'Section', 'academics.Subject': 'Subject',
    'academics.SubjectFamily': 'Discipline', 'academics.Track': 'Track', 'academics.Strand': 'Strand',
    'scheduling.ClassAssignment': 'Class',
    'grades.GradeComponent': 'Grade', 'grades.FinalGrade': 'Final grade',
    'attendance.AttendanceSummary': 'Attendance (month)', 'attendance.SF2AttendanceReport': 'SF2 report',
    'documentation.LearnerNutrition': 'Nutritional status', 'documentation.LearnerComment': 'Report card comment',
    'auth.User': 'User account', 'accounts.UserProfile': 'User profile',
}
# never stored: only that they changed
SECRET = ('password', 'token', 'secret', 'otp', 'hash', 'google_sub', 'key')
# changes that are bookkeeping, not anyone's edit
NOISE = {'updated_at', 'created_at', 'last_login', 'last_calculated_at', 'computed_at', 'modified_at', 'current_enrollment_count'}
MAX_CHANGES = 25

_who = ContextVar('arms_audit_who', default=None)
_buffer = ContextVar('arms_audit_buffer', default=None)


def _secret(name):
    return any(word in name.lower() for word in SECRET)


def _text(value):
    if value is None or value == '':
        return '—'
    if isinstance(value, bool):
        return 'Yes' if value else 'No'
    if isinstance(value, (int, float)) or type(value).__name__ == 'Decimal':
        text = f'{value:.2f}'.rstrip('0').rstrip('.') if not isinstance(value, int) else str(value)
        return text or '0'
    text = str(value)
    return text if len(text) <= 120 else text[:117] + '…'


def _snapshot(instance):
    return {f.attname: getattr(instance, f.attname, None) for f in instance._meta.concrete_fields}


def _label(instance):
    try:
        text = str(instance)
    except Exception:
        text = ''
    return _text(text)[:120] if text else f'#{instance.pk}'


def _student_id(instance):
    label = instance._meta.label
    if label == 'students.Student':
        return instance.pk
    if label == 'enrollment.Enrollment':
        return instance.student_id
    return None


def _event(action, instance, changes=None):
    who = _who.get()
    if who is None:
        return
    model = instance._meta.label
    # a learner's record (an enrollment, a grade, attendance) is named from its learner, subject and period when the
    # request ends, in a few queries for all of them (write); anything else by its own name
    refs = {k: getattr(instance, f'{k}_id', None) for k in ('enrollment', 'subject', 'quarter')}
    if model == 'enrollment.Enrollment':
        refs['enrollment'] = instance.pk
    details = {'action': action, 'record_type': TRACKED.get(model, instance._meta.verbose_name.title()),
               'record': '' if refs['enrollment'] else _label(instance)}
    if changes is not None:
        details['changes'] = changes[:MAX_CHANGES]
        if len(changes) > MAX_CHANGES:
            details['more'] = len(changes) - MAX_CHANGES
    entry = dict(who, action_type={'Added': 'DATA_ADDED', 'Edited': 'DATA_EDITED', 'Deleted': 'DATA_DELETED'}[action],
                 resource_type=details['record_type'], resource_id=str(instance.pk or ''), affected_student_id=_student_id(instance),
                 details=details)
    if refs['enrollment']:
        details['refs'], entry['_has_refs'] = refs, True
    buffer = _buffer.get()
    if buffer is not None:
        transaction.on_commit(lambda: buffer.append(entry))         # a rolled-back change never reaches the trail


def _on_init(sender, instance, **kwargs):
    if _who.get() is not None and instance.pk is not None:
        instance._audit_before = _snapshot(instance)


def _on_save(sender, instance, created, raw=False, **kwargs):
    if raw or _who.get() is None:
        return
    after = _snapshot(instance)
    if created:
        _event('Added', instance)
    else:
        before = getattr(instance, '_audit_before', None)
        if before is None:                  # loaded before the request knew who was acting: nothing to compare with
            return
        changes = []
        for f in instance._meta.concrete_fields:
            name = f.attname
            if name in NOISE or before.get(name) == after.get(name):
                continue
            label = str(f.verbose_name)
            if _secret(name):
                changes.append({'field': label, 'old': '(hidden)', 'new': '(changed)'})
            elif f.is_relation:                # a linked record: its number (the trail keeps no names that may change)
                changes.append({'field': label, 'old': f'#{before[name]}' if before.get(name) else '—',
                                'new': f'#{after[name]}' if after.get(name) else '—'})
            else:
                changes.append({'field': label, 'old': _text(before.get(name)), 'new': _text(after.get(name))})
        if not changes:
            return
        _event('Edited', instance, changes)
    instance._audit_before = after


def _on_delete(sender, instance, **kwargs):
    if _who.get() is None:
        return
    _event('Deleted', instance)


def _sign_event(action_type, user, request, details):
    who = _person(user, request) if user is not None else _anonymous(request, details.get('username', ''))
    entry = dict(who, action_type=action_type, resource_type='Sign-in', resource_id=str(getattr(user, 'pk', '') or ''),
                 affected_student_id=None, details=details)
    buffer = _buffer.get()
    if buffer is not None:
        buffer.append(entry)
    else:
        write([entry])


def _on_login(sender, request, user, **kwargs):
    _who.set(_person(user, request))
    _sign_event('LOGIN_SUCCESS', user, request, {'action': 'Signed in', 'record_type': 'Sign-in', 'record': user.get_username()})


def _on_logout(sender, request, user, **kwargs):
    if user is not None:
        _sign_event('LOGOUT', user, request, {'action': 'Signed out', 'record_type': 'Sign-in', 'record': user.get_username()})


def _on_failed(sender, credentials, request=None, **kwargs):
    name = str((credentials or {}).get('username') or '')[:150]
    _sign_event('LOGIN_FAILED', None, request, {'action': 'Sign-in failed', 'record_type': 'Sign-in', 'record': name or '—', 'username': name})


def _ip(request):
    try:
        from core.net import client_ip
        return client_ip(request) or '0.0.0.0'
    except Exception:
        return (getattr(request, 'META', {}) or {}).get('REMOTE_ADDR') or '0.0.0.0'


def _person(user, request):
    profile = getattr(user, 'profile', None)
    role = getattr(profile, 'role', '') or ('admin' if getattr(user, 'is_superuser', False) else '')
    return {'user_id': user.pk, 'user_name': (user.get_full_name() or user.get_username())[:150], 'user_role': role[:30],
            'ip_address': _ip(request) if request is not None else '0.0.0.0',
            'user_agent': (request.META.get('HTTP_USER_AGENT', '') if request is not None else '')[:500]}


def _anonymous(request, username):
    return {'user_id': None, 'user_name': (username or 'Someone')[:150], 'user_role': '', 'ip_address': _ip(request) if request is not None else '0.0.0.0',
            'user_agent': (request.META.get('HTTP_USER_AGENT', '') if request is not None else '')[:500]}


def chain_hash(prev, timestamp, user_id, action_type, resource_id, details):
    """The SHA-256 of one entry, as audit.models.ActivityLog.save computes it."""
    raw = f"{prev}{timestamp.isoformat()}{user_id or ''}{action_type}{resource_id or ''}{json.dumps(details or {}, sort_keys=True)}"
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def _name_refs(entries):
    """'Dela Cruz, Juan · MATH7 · T2' for the entries about a learner's record, in three queries for the whole request."""
    mine = [e for e in entries if e.pop('_has_refs', False)]
    if not mine:
        return
    refs = [e['details'].pop('refs') for e in mine]
    from academics.models import Quarter, Subject
    from enrollment.models import Enrollment
    people = {i: f'{last}, {first}' for i, last, first in Enrollment.objects.filter(
        id__in={r['enrollment'] for r in refs}).values_list('id', 'student__last_name', 'student__first_name')}
    subjects = dict(Subject.objects.filter(id__in={r['subject'] for r in refs if r['subject']}).values_list('id', 'subject_code'))
    periods = dict(Quarter.objects.filter(id__in={r['quarter'] for r in refs if r['quarter']}).values_list('id', 'quarter_label'))
    for e, r in zip(mine, refs):
        parts = [people.get(r['enrollment'], f'Enrollment #{r["enrollment"]}'), subjects.get(r['subject']), periods.get(r['quarter'])]
        e['details']['record'] = ' · '.join(p for p in parts if p)


def write(entries):
    """Write events in one go, each chained to the one before (the last entry in the trail first)."""
    if not entries:
        return
    from .models import ActivityLog
    _name_refs(entries)
    with transaction.atomic():
        last = ActivityLog.objects.select_for_update().order_by('-timestamp', '-pk').values_list('blockchain_hash', 'timestamp').first()
        prev = last[0] if last else '0' * 64
        start = max(timezone.now(), last[1] + timedelta(microseconds=1)) if last else timezone.now()
        rows = []
        for i, e in enumerate(entries):
            ts = start + timedelta(microseconds=i)               # strictly increasing, so the chain has one order
            h = chain_hash(prev, ts, e['user_id'], e['action_type'], e['resource_id'], e['details'])
            rows.append(ActivityLog(timestamp=ts, user_id=e['user_id'], user_name=e['user_name'], user_role=e['user_role'],
                                    action_type=e['action_type'][:50], resource_type=e['resource_type'][:100], resource_id=e['resource_id'][:50],
                                    affected_student_id=e['affected_student_id'], details_json=e['details'], ip_address=e['ip_address'],
                                    user_agent=e['user_agent'], blockchain_hash=h, prev_blockchain_hash=prev))
            prev = h
        ActivityLog.objects.bulk_create(rows)


def verify(limit=None):
    """(checked, the first entry whose hash does not match, or None): recomputes the chain from the oldest entry."""
    from .models import ActivityLog
    qs = ActivityLog.objects.order_by('timestamp', 'pk').values_list(
        'pk', 'timestamp', 'user_id', 'action_type', 'resource_id', 'details_json', 'blockchain_hash', 'prev_blockchain_hash')
    prev, checked = None, 0
    for pk, ts, user_id, action, resource, details, h, p in qs.iterator(chunk_size=2000):
        if prev is not None and p != prev:
            return checked, pk
        if chain_hash(p, ts, user_id, action, resource, details) != h:
            return checked, pk
        prev, checked = h, checked + 1
        if limit and checked >= limit:
            break
    return checked, None


class AuditMiddleware:
    """Who is acting in this request, and the trail's events written once the response is ready."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, 'user', None)
        signed_in = user is not None and user.is_authenticated
        who_token = _who.set(_person(user, request) if signed_in else None)
        if signed_in:                 # loaded before the trail knew who was acting: their own account edits need a "before" too
            user._audit_before = _snapshot(user)
            profile = getattr(user, 'profile', None)
            if profile is not None:
                profile._audit_before = _snapshot(profile)
        buffer = []
        buffer_token = _buffer.set(buffer)
        try:
            response = self.get_response(request)
        finally:
            _who.reset(who_token)
            _buffer.reset(buffer_token)
            try:
                write(buffer)
            except Exception:                        # the trail must never break the page; the request's own work is done
                import logging
                logging.getLogger('audit').exception('Could not write the audit trail')
        return response


def connect():
    """Hook the trail to the tracked models and to sign-ins (audit.apps.AuditConfig.ready)."""
    for label in TRACKED:
        try:
            model = apps.get_model(label)
        except LookupError:
            continue
        post_init.connect(_on_init, sender=model, dispatch_uid=f'audit-init-{label}')
        post_save.connect(_on_save, sender=model, dispatch_uid=f'audit-save-{label}')
        post_delete.connect(_on_delete, sender=model, dispatch_uid=f'audit-delete-{label}')
    user_logged_in.connect(_on_login, dispatch_uid='audit-login')
    user_logged_out.connect(_on_logout, dispatch_uid='audit-logout')
    user_login_failed.connect(_on_failed, dispatch_uid='audit-login-failed')
