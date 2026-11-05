from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import User, Group
from django.contrib import messages
from django.db.models import Count, Q, Sum, Avg
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.conf import settings
from django.db import transaction
from django.db.models import ProtectedError
from django.http import JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from datetime import datetime, time, timedelta
from urllib.parse import urlencode
import secrets

# ===== USE THE REAL MODELS =====
from academics.models import School, GradeLevel, SchoolYear, Quarter, GradingSchema
from accounts.models import UserProfile
from accounts.views import _get_dashboard_url
from communication.models import ContactRequest
from enrollment.models import Enrollment

from core.caching import remember_filters

from core import grading_policy

from . import accounts as A
from .emails import email_ready, send_setup_emails
from .forms import (
    SchoolForm, PrincipalCreationForm, PrincipalEditForm,
    RegistrarCreationForm, GradeLevelForm, SchoolYearForm, QuarterForm,
    QuarterUpdateForm, GRADE_CHOICES, PERIOD_CONFIG, POLICY_PERIODS, period_config,
    add_grade_levels, grade_stage,
)


# =============================================================================
# HELPERS
# =============================================================================

def is_admin(user):
    return user.is_authenticated and (
        user.is_superuser or user.groups.filter(name='Admin').exists()
    )


def get_school_principals(school):
    return UserProfile.objects.filter(role='schoolhead', school=school, is_active=True)


def get_school_registrars(school):
    return UserProfile.objects.filter(role='registrar', school=school, is_active=True)


def get_school_teachers(school):
    return UserProfile.objects.filter(role='teacher', school=school, is_active=True)


def get_school_students(school):
    return Enrollment.objects.filter(
        section__school=school, status__in=['Enrolled', 'Transferred_In']
    ).select_related('student', 'section__grade_level')


# Grading periods are created the same way here and on the School Head's School Year page.
from academics.school_years import generate_periods as _generate_periods, period_deadlines as _period_deadlines  # noqa: E402


def _with_period_info(school_years):
    """Attach period wording and progress to each school year for the templates."""
    school_years = list(school_years)
    for sy in school_years:
        sy.period_cfg = period_config(sy.school)
        sy.periods = list(sy.quarters.all())
        sy.missing_periods = max(sy.period_cfg['count'] - len(sy.periods), 0)
    return school_years


def _log_admin_action(request, action_type, description):
    try:
        from audit.models import ActivityLog
        ActivityLog.objects.create(
            user=request.user if request.user.is_authenticated else None,
            user_name=request.user.get_full_name() if request.user.is_authenticated else 'System',
            user_role='admin', action_type=action_type,
            resource_type='Admin Panel', resource_id='',
            details_json={'description': description},
            ip_address=request.META.get('REMOTE_ADDR', '127.0.0.1'),
            user_agent=request.META.get('HTTP_USER_AGENT', '')[:255],
        )
    except Exception:
        pass


# =============================================================================
# AUTHENTICATION
# =============================================================================

def admin_login(request):
    """The admin portal has no sign-in page of its own: everyone signs in on the shared page,
    which sends administrators to the admin dashboard."""
    if request.user.is_authenticated:
        if is_admin(request.user):
            return redirect('admin_panel:dashboard')
        messages.error(request, 'That page is only for ARMS administrators.')
        profile = getattr(request.user, 'profile', None)
        return redirect(_get_dashboard_url(profile.role if profile else ''))
    target = reverse('signin')
    if request.GET.get('next'):
        target += '?' + urlencode({'next': request.GET['next']})
    return redirect(target)


def admin_logout(request):
    if request.user.is_authenticated:
        logout(request)
        messages.info(request, 'You have been successfully logged out.')
    return redirect('signin')

# =============================================================================
# DASHBOARD
# =============================================================================

# Replace the dashboard function with this:

@login_required
@user_passes_test(is_admin, login_url='admin_panel:login')
def dashboard(request):
    """Admin dashboard with comprehensive statistics"""
    current_sy = SchoolYear.objects.filter(is_current=True).first()

    total_schools = School.objects.filter(is_active=True).count()
    total_inactive_schools = School.objects.filter(is_active=False).count()
    total_principals = UserProfile.objects.filter(role='schoolhead', is_active=True).count()
    total_registrars = UserProfile.objects.filter(role='registrar', is_active=True).count()
    total_teachers = UserProfile.objects.filter(role='teacher', is_active=True).count()
    total_students = Enrollment.objects.filter(status__in=['Enrolled', 'Transferred_In']).count()
    total_grade_levels = GradeLevel.objects.all().count()

    # ✅ FIXED: School has NO school_type field — remove this or use grading_scale
    # Instead, group by grading_scale which DOES exist
    school_distribution = School.objects.filter(is_active=True).values(
        'grading_scale'
    ).annotate(count=Count('id')).order_by('grading_scale')

    recent_schools = School.objects.filter(is_active=True).order_by('-created_at')[:5]

    recent_principals = UserProfile.objects.filter(
        role='schoolhead'
    ).select_related('user', 'school').order_by('-created_at')[:5]

    schools_with_principals = UserProfile.objects.filter(
        role='schoolhead', is_active=True, school__isnull=False
    ).values_list('school_id', flat=True)
    schools_without_principal = School.objects.filter(is_active=True).exclude(
        id__in=schools_with_principals
    ).count()

    schools_with_sy = SchoolYear.objects.values_list('school_id', flat=True)
    schools_without_sy = School.objects.filter(is_active=True).exclude(
        id__in=schools_with_sy
    ).count()

    context = {
        'total_schools': total_schools,
        'total_inactive_schools': total_inactive_schools,
        'total_principals': total_principals,
        'total_registrars': total_registrars,
        'total_teachers': total_teachers,
        'total_students': total_students,
        'total_grade_levels': total_grade_levels,
        'active_school_years': SchoolYear.objects.filter(is_current=True).count(),
        'current_sy': current_sy,
        'recent_schools': recent_schools,
        'recent_principals': recent_principals,
        'school_distribution': school_distribution,  # ✅ Renamed from school_type_distribution
        'schools_without_principal': schools_without_principal,
        'schools_without_sy': schools_without_sy,
        'new_request_count': ContactRequest.objects.filter(status=ContactRequest.STATUS_NEW).count(),
    }
    return render(request, 'admin/dashboard.html', context)


# =============================================================================
# SCHOOL MANAGEMENT
# =============================================================================

@login_required
@user_passes_test(is_admin, login_url='admin_panel:login')
@remember_filters('admin-school_list', ['search', 'grading', 'status', 'sort'])
def school_list(request):
    """List all schools"""
    schools = School.objects.all().annotate(
        principal_count=Count('staff_profiles', filter=Q(staff_profiles__role='schoolhead', staff_profiles__is_active=True)),
        registrar_count=Count('staff_profiles', filter=Q(staff_profiles__role='registrar', staff_profiles__is_active=True)),
        teacher_count=Count('staff_profiles', filter=Q(staff_profiles__role='teacher', staff_profiles__is_active=True)),
    )

    search = request.GET.get('search', '')
    if search:
        schools = schools.filter(
            Q(school_name__icontains=search) | 
            Q(school_id__icontains=search) | 
            Q(address__icontains=search)
        )

    # the DepEd order a school grades by (core/grading_policy.py)
    grading_filter = request.GET.get('grading', '')
    if grading_filter in grading_policy.POLICIES:
        schools = schools.filter(grading_policy=grading_filter)
    else:
        grading_filter = ''

    status = request.GET.get('status', 'active')
    if status == 'inactive':
        schools = schools.filter(is_active=False)
    elif status != 'all':
        schools = schools.filter(is_active=True)

    sort = request.GET.get('sort', '-created_at')
    valid_sorts = ['school_name', '-school_name', 'created_at', '-created_at']
    schools = schools.order_by(sort if sort in valid_sorts else '-created_at')

    paginator = Paginator(schools, 10)
    page_obj = paginator.get_page(request.GET.get('page', 1))

    context = {
        'schools': page_obj,
        'search': search,
        'grading_filter': grading_filter,
        'status': status,
        'sort': sort,
        'total_count': schools.count(),
        'active_count': School.objects.filter(is_active=True).count(),
        'inactive_count': School.objects.filter(is_active=False).count(),
    }
    return render(request, 'admin/school_list.html', context)

def _school_form_page(request, form, school=None):
    return render(request, 'admin/school_create.html', {
        'form': form, 'school': school, 'edit_mode': school is not None,
        'title': f'Edit: {school.school_name}' if school else 'Add a school',
        'submit_text': 'Save changes' if school else 'Create school',
        'policies': list(grading_policy.POLICIES.values()), 'policy_periods': POLICY_PERIODS,
        'period_config': PERIOD_CONFIG,
        'grade_choices': [(n, label, grade_stage(n)) for n, label in GRADE_CHOICES],
        'chosen_grades': [int(g) for g in form['grades'].value() or []] if form.is_bound else list(range(7, 13)),
    })


@login_required
@user_passes_test(is_admin, login_url='admin_panel:login')
def school_create(request):
    # a request from the website can open this page with the school's name already typed (?name=)
    form = SchoolForm(request.POST or None, initial={'grading_policy': grading_policy.DO15_2026, 'period_type': 'TRIMESTRAL',
                                                     'is_active': True, 'school_name': request.GET.get('name', '')[:200]})
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            school = form.save()
        levels = getattr(form, 'grades_added', 0)
        messages.success(request, f'{school.school_name} is added{f" with {levels} grade level" + ("s" if levels != 1 else "") if levels else ""}. '
                                  'Next, give it a principal and a registrar.')
        _log_admin_action(request, 'CREATE_SCHOOL', f'Added {school.school_name} ({school.school_id})')
        return redirect('admin_panel:school_detail', school_id=school.id)
    return _school_form_page(request, form)

@login_required
@user_passes_test(is_admin, login_url='admin_panel:login')
def school_detail(request, school_id):
    school = get_object_or_404(School, id=school_id)
    principals = get_school_principals(school)
    registrars = get_school_registrars(school)
    teachers = get_school_teachers(school)
    students = get_school_students(school)
    grade_levels = GradeLevel.objects.filter(school=school).order_by('grade_number')  # ✅ No is_active
    school_years = SchoolYear.objects.filter(school=school).prefetch_related('quarters')

    context = {
        'school': school,
        'principals': principals,
        'registrars': registrars,
        'teachers': teachers,
        'students': students,
        'grade_levels': grade_levels,
        'grade_level_count': grade_levels.count(),
        'school_years': school_years,
        'current_sy': school_years.filter(is_current=True).first(),
        'principal_count': principals.count(),
        'registrar_count': registrars.count(),
        'teacher_count': teachers.count(),
        'student_count': students.count(),
        'new_accounts': A.pop_new_accounts(request),
    }
    # what the school still needs before its people can start, in the order the admin does it
    context['setup'] = [
        {'done': context['principal_count'] > 0, 'label': 'Principal', 'hint': 'Signs in as the School Head.',
         'url': f"{reverse('admin_panel:principal_create')}?school={school.id}", 'action': 'Add the principal'},
        {'done': context['registrar_count'] > 0, 'label': 'Registrar', 'hint': 'Enrolls learners from SF1 and adds the teachers.',
         'url': f"{reverse('admin_panel:registrar_create')}?school={school.id}", 'action': 'Add the registrar'},
        {'done': context['grade_level_count'] > 0, 'label': 'Grade levels', 'hint': 'The ones it offers, Kindergarten to Grade 12.',
         'url': f"{reverse('admin_panel:grade_level_create')}?school={school.id}", 'action': 'Add grade levels'},
        {'done': context['current_sy'] is not None, 'label': 'Current school year', 'hint': 'With its grading periods.',
         'url': f"{reverse('admin_panel:school_year_manage')}?school={school.id}", 'action': 'Start the school year'},
    ]
    context['setup_left'] = sum(not step['done'] for step in context['setup'])
    context['policy'] = grading_policy.POLICIES.get(school.grading_policy, grading_policy.POLICIES[grading_policy.DEFAULT])
    return render(request, 'admin/school_detail.html', context)


@login_required
@user_passes_test(is_admin, login_url='admin_panel:login')
def school_edit(request, school_id):
    school = get_object_or_404(School, id=school_id)
    form = SchoolForm(request.POST or None, instance=school)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, f'{school.school_name} is updated.')
        _log_admin_action(request, 'UPDATE_SCHOOL', f'Updated {school.school_name}')
        return redirect('admin_panel:school_detail', school_id=school.id)
    return _school_form_page(request, form, school)


@login_required
@user_passes_test(is_admin, login_url='admin_panel:login')
def school_toggle_status(request, school_id):
    school = get_object_or_404(School, id=school_id)
    if request.method == 'POST':
        school.is_active = not school.is_active
        school.save()
        status = "activated" if school.is_active else "deactivated"
        messages.success(request, f'School "{school.school_name}" {status}.')
    return redirect('admin_panel:school_detail', school_id=school.id)


@login_required
@user_passes_test(is_admin, login_url='admin_panel:login')
def school_delete(request, school_id):
    school = get_object_or_404(School, id=school_id)
    if request.method == 'POST':
        has_staff = UserProfile.objects.filter(school=school).exists()
        has_students = Enrollment.objects.filter(section__school=school).exists()
        if has_staff or has_students:
            messages.error(request, f'Cannot delete "{school.school_name}". Deactivate it instead.')
        else:
            school.delete()
            messages.success(request, f'School "{school.school_name}" deleted.')
            return redirect('admin_panel:school_list')
    return render(request, 'admin/school_confirm_delete.html', {'school': school})


# =============================================================================
# PRINCIPAL MANAGEMENT
# =============================================================================

@login_required
@user_passes_test(is_admin, login_url='admin_panel:login')
@remember_filters('admin-principal_list', ['search', 'school', 'status', 'sort'])
def principal_list(request):
    principals = UserProfile.objects.filter(role='schoolhead').select_related('user', 'school').annotate(
        registrar_count=Count('school__staff_profiles', filter=Q(school__staff_profiles__role='registrar', school__staff_profiles__is_active=True)),
        teacher_count=Count('school__staff_profiles', filter=Q(school__staff_profiles__role='teacher', school__staff_profiles__is_active=True)),
    ).order_by('user__last_name')

    search = request.GET.get('search', '')
    if search:
        principals = principals.filter(Q(user__first_name__icontains=search) | Q(user__last_name__icontains=search) | Q(user__email__icontains=search) | Q(employee_number__icontains=search) | Q(school__school_name__icontains=search))

    school_filter = request.GET.get('school', '')
    if school_filter:
        principals = principals.filter(school_id=school_filter)

    status = request.GET.get('status', 'active')
    if status == 'inactive':
        principals = principals.filter(is_active=False)
    elif status == 'active':
        principals = principals.filter(is_active=True)

    sort = request.GET.get('sort', 'user__last_name')
    valid_sorts = {'user__last_name', '-user__last_name', 'school__school_name', '-school__school_name', '-created_at', 'created_at'}
    principals = principals.order_by(sort if sort in valid_sorts else 'user__last_name')

    paginator = Paginator(principals, 10)
    page_obj = paginator.get_page(request.GET.get('page', 1))

    context = {
        'principals': page_obj,
        'search': search, 'school_filter': school_filter, 'status': status, 'sort': sort,
        'schools': School.objects.filter(is_active=True),
        'active_count': UserProfile.objects.filter(role='schoolhead', is_active=True).count(),
        'inactive_count': UserProfile.objects.filter(role='schoolhead', is_active=False).count(),
    }
    return render(request, 'admin/principal_list.html', context)


def _account_schools(role):
    """The active schools for the account forms, with what the forms fill in for each one."""
    schools = list(School.objects.filter(is_active=True).order_by('school_name'))
    staff = {}
    for profile in UserProfile.objects.filter(role=role, is_active=True, school__in=schools).select_related('user'):
        staff.setdefault(profile.school_id, []).append(profile.full_name)
    data = {}
    for school in schools:
        data[school.id] = {'name': school.school_name, 'short': school.short_name, 'domain': A.normalize_domain(school.email_domain),
                           'staff': staff.get(school.id, []),
                           'registrar': A.registrar_email(school) if role == 'registrar' else None}
    return schools, data


def _create_account_page(request, form_class, *, role, group_name, position_title, role_label, template):
    schools, data = _account_schools(role)
    form = form_class(request.POST or None, initial={'school': request.GET.get('school'), 'designation': group_name,
                                                     'password': A.temporary_password()})
    if request.method == 'POST' and form.is_valid():
        d = form.cleaned_data
        user = A.create_staff(school=d['school'], role=role, group_name=group_name, position_title=position_title,
                              first_name=d['first_name'], last_name=d['last_name'], email=d['email'], password=d['password'],
                              employee_id=d.get('employee_id'), designation=d.get('designation') or group_name)
        A.remember_new_accounts(request, [A.account_card(user, d['password'], role_label)])
        messages.success(request, f'{user.get_full_name()} can now sign in as the {role_label} of {d["school"].school_name}.')
        _log_admin_action(request, f'CREATE_{role.upper()}', f'Added {role_label} {user.username} for {d["school"].school_name}')
        return redirect('admin_panel:school_detail', school_id=d['school'].id)
    return render(request, template, {'form': form, 'schools': schools, 'school_data': data})


@login_required
@user_passes_test(is_admin, login_url='admin_panel:login')
def principal_create(request):
    return _create_account_page(request, PrincipalCreationForm, role='schoolhead', group_name='Principal',
                                position_title='School Principal', role_label='School Head', template='admin/principal_create.html')


def _form_errors_to_messages(request, form):
    if form.is_bound:
        for field, errors in form.errors.items():
            for error in errors:
                messages.error(request, f'{field}: {error}')


@login_required
@user_passes_test(is_admin, login_url='admin_panel:login')
@remember_filters('admin-registrar_list', ['search', 'status'])
def registrar_list(request):
    registrars = UserProfile.objects.filter(role='registrar').select_related('user', 'school').order_by('user__last_name')
    search = request.GET.get('search', '').strip()
    if search:
        registrars = registrars.filter(
            Q(user__first_name__icontains=search) | Q(user__last_name__icontains=search)
            | Q(user__email__icontains=search) | Q(employee_number__icontains=search)
            | Q(school__school_name__icontains=search)
        )
    status = request.GET.get('status', 'active')
    if status == 'active':
        registrars = registrars.filter(is_active=True)
    elif status == 'inactive':
        registrars = registrars.filter(is_active=False)
    return render(request, 'admin/registrar_list.html', {
        'registrars': Paginator(registrars, 10).get_page(request.GET.get('page', 1)),
        'search': search, 'status': status,
        'active_count': UserProfile.objects.filter(role='registrar', is_active=True).count(),
        'inactive_count': UserProfile.objects.filter(role='registrar', is_active=False).count(),
    })


@login_required
@user_passes_test(is_admin, login_url='admin_panel:login')
def registrar_create(request):
    return _create_account_page(request, RegistrarCreationForm, role='registrar', group_name='Registrar',
                                position_title='School Registrar', role_label='Registrar', template='admin/registrar_create.html')


@login_required
@user_passes_test(is_admin, login_url='admin_panel:login')
def suggest_account(request):
    """The sign-in email the account forms would make: firstname.lastname@ or shortname.registrar@ the school's domain."""
    school = School.objects.filter(pk=request.GET.get('school') or None, is_active=True).first()
    if school is None:
        return JsonResponse({'email': None, 'domain': '', 'taken': False})
    first, last = ' '.join(request.GET.get('first', '').split()), ' '.join(request.GET.get('last', '').split())
    if request.GET.get('role') == 'registrar' and not (first and last):
        email = A.registrar_email(school)
    else:
        email = A.person_email(school, first, last) if first and last else None
    typed = request.GET.get('email', '').strip().lower()
    return JsonResponse({'email': email, 'domain': A.normalize_domain(school.email_domain),
                         'taken': bool(typed) and A.email_taken(typed)})


@login_required
@user_passes_test(is_admin, login_url='admin_panel:login')
def principal_detail(request, principal_id):
    principal = get_object_or_404(UserProfile.objects.select_related('user', 'school'), id=principal_id, role='schoolhead')
    school = principal.school
    registrars = get_school_registrars(school) if school else UserProfile.objects.none()
    teachers = get_school_teachers(school) if school else UserProfile.objects.none()
    student_count = get_school_students(school).count() if school else 0
    context = {'principal': principal, 'registrars': registrars, 'teachers': teachers, 'student_count': student_count,
               'new_accounts': A.pop_new_accounts(request)}
    return render(request, 'admin/principal_detail.html', context)


@login_required
@user_passes_test(is_admin, login_url='admin_panel:login')
def principal_toggle_status(request, principal_id):
    principal = get_object_or_404(UserProfile, id=principal_id, role='schoolhead')
    if request.method == 'POST':
        principal.is_active = not principal.is_active
        principal.user.is_active = principal.is_active
        principal.user.save()
        principal.save()
        status = "activated" if principal.is_active else "deactivated"
        messages.success(request, f'Principal "{principal.full_name}" {status}.')
    return redirect('admin_panel:principal_list')


@login_required
@user_passes_test(is_admin, login_url='admin_panel:login')
def principal_reset_password(request, principal_id):
    principal = get_object_or_404(UserProfile, id=principal_id, role='schoolhead')
    if request.method == 'POST':
        password = A.reset_temporary_password(principal.user)
        A.remember_new_accounts(request, [A.account_card(principal.user, password, 'School Head')])
        messages.success(request, f'{principal.full_name} has a new temporary password. Their old one no longer works.')
        _log_admin_action(request, 'RESET_PASSWORD', f'New temporary password for {principal.user.username}')
    return redirect('admin_panel:principal_detail', principal_id=principal.id)


# =============================================================================
# GRADE LEVEL MANAGEMENT (FIXED: No is_active field)
# =============================================================================

@login_required
@user_passes_test(is_admin, login_url='admin_panel:login')
@remember_filters('admin-grade_level_list', ['school'])
def grade_level_list(request):
    grade_levels = GradeLevel.objects.select_related('school').all().order_by('school__school_name', 'grade_number')

    school_id = request.GET.get('school', '')
    if school_id:
        grade_levels = grade_levels.filter(school_id=school_id)

    paginator = Paginator(grade_levels, 20)
    page_obj = paginator.get_page(request.GET.get('page', 1))

    context = {
        'grade_levels': page_obj,
        'schools': School.objects.filter(is_active=True),
        'selected_school': school_id,
        'active_count': GradeLevel.objects.all().count(),  # ✅ All grade levels
    }
    return render(request, 'admin/grade_level_list.html', context)


@login_required
@user_passes_test(is_admin, login_url='admin_panel:login')
def grade_level_create(request):
    """Add one or more grade levels (Kindergarten to Grade 12) to a school."""
    form = GradeLevelForm(request.POST or None, initial={'school': request.GET.get('school')})
    if request.method == 'POST' and form.is_valid():
        school = form.cleaned_data['school']
        with transaction.atomic():
            created = add_grade_levels(school, form.cleaned_data['grades'])
        skipped = len(set(form.cleaned_data['grades'])) - created
        if created:
            note = f' ({skipped} already existed)' if skipped else ''
            messages.success(request, f'{created} grade level(s) added to {school.school_name}.{note}')
        else:
            messages.info(request, f'Those grade levels already exist for {school.school_name}.')
        return redirect(f"{reverse('admin_panel:grade_level_list')}?school={school.id}")
    _form_errors_to_messages(request, form)

    existing = {}
    for school_id, number in GradeLevel.objects.values_list('school_id', 'grade_number'):
        existing.setdefault(school_id, []).append(number)
    return render(request, 'admin/grade_level_create.html', {
        'form': form,
        'schools': School.objects.filter(is_active=True),
        'grade_choices': [(n, label, grade_stage(n)) for n, label in GRADE_CHOICES],
        'existing_grades': existing,
        'selected_school': request.POST.get('school') or request.GET.get('school', ''),
    })


@login_required
@user_passes_test(is_admin, login_url='admin_panel:login')
def grade_level_delete(request, grade_level_id):
    if request.method == 'POST':
        grade_level = get_object_or_404(GradeLevel, id=grade_level_id)
        try:
            grade_level.delete()
            messages.success(request, f'{grade_level.grade_name} removed.')
        except ProtectedError:
            messages.error(request, f'{grade_level.grade_name} is already in use (sections, subjects or enrollments) and cannot be removed.')
    return redirect('admin_panel:grade_level_list')


# =============================================================================
# SCHOOL YEAR & QUARTER MANAGEMENT
# =============================================================================

@login_required
@user_passes_test(is_admin, login_url='admin_panel:login')
@remember_filters('admin-school_year_manage', ['school'])
def school_year_manage(request):
    """Manage school years"""
    form = SchoolYearForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        sy = form.save()
        if form.cleaned_data['auto_create_periods']:
            created, _ = _generate_periods(sy)
            plural = period_config(sy.school)['plural'].lower()
            messages.success(request, f'School Year {sy.year_label} created for {sy.school.school_name} with {created} {plural}.')
        else:
            messages.success(request, f'School Year {sy.year_label} created for {sy.school.school_name}.')
        return redirect('admin_panel:school_year_manage')
    _form_errors_to_messages(request, form)

    school_years = SchoolYear.objects.select_related('school').prefetch_related('quarters').order_by('-date_start')
    school_filter = request.GET.get('school', '')
    if school_filter:
        school_years = school_years.filter(school_id=school_filter)

    schools_with_sy = SchoolYear.objects.values_list('school_id', flat=True).distinct()
    schools_without_sy = School.objects.filter(is_active=True).exclude(id__in=schools_with_sy)

    context = {
        'form': form,
        'school_years': _with_period_info(school_years),
        'schools': School.objects.filter(is_active=True),
        'selected_school': school_filter,
        'schools_without_sy': schools_without_sy,
        'current_school_years': school_years.filter(is_current=True),
        'period_config': PERIOD_CONFIG,
    }
    return render(request, 'admin/school_year_manage.html', context)


@login_required
@user_passes_test(is_admin, login_url='admin_panel:login')
def school_year_set_current(request, sy_id):
    if request.method == 'POST':
        sy = get_object_or_404(SchoolYear, id=sy_id)
        sy.is_current = True
        sy.status = 'Active'
        sy.save()
        messages.success(request, f'{sy.year_label} set as the current school year of {sy.school.school_name}.')
    return redirect('admin_panel:school_year_manage')


@login_required
@user_passes_test(is_admin, login_url='admin_panel:login')
def school_year_delete(request, sy_id):
    if request.method == 'POST':
        sy = get_object_or_404(SchoolYear, id=sy_id)
        try:
            sy.delete()
            messages.success(request, 'School year deleted.')
        except ProtectedError:
            messages.error(request, f'{sy.year_label} already has records attached and cannot be deleted.')
    return redirect('admin_panel:school_year_manage')


@login_required
@user_passes_test(is_admin, login_url='admin_panel:login')
@remember_filters('admin-quarter_manage', ['school', 'school_year'])
def quarter_manage(request):
    """Manage grading periods (quarters / trimesters / semesters, by school period type)."""
    form = QuarterForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        period = form.save(commit=False)
        period.grade_encoding_deadline, period.grade_validation_deadline = _period_deadlines(period.date_end)
        period.save()
        messages.success(request, f'{period.quarter_label} created for {period.school_year}.')
        return redirect(f"{reverse('admin_panel:quarter_manage')}?school_year={period.school_year_id}")
    _form_errors_to_messages(request, form)

    all_school_years = SchoolYear.objects.select_related('school').order_by('-date_start')
    school_years = all_school_years.prefetch_related('quarters')

    sy_filter = request.GET.get('school_year', '')
    if sy_filter:
        school_years = school_years.filter(id=sy_filter)
    school_filter = request.GET.get('school', '')
    if school_filter:
        school_years = school_years.filter(school_id=school_filter)

    context = {
        'form': form,
        'grouped_school_years': _with_period_info(school_years),
        'school_years': _with_period_info(all_school_years.prefetch_related('quarters')),
        'schools': School.objects.filter(is_active=True),
        'selected_sy': sy_filter,
        'selected_school': school_filter,
    }
    return render(request, 'admin/quarter_manage.html', context)


@login_required
@user_passes_test(is_admin, login_url='admin_panel:login')
def quarter_bulk_create(request, sy_id):
    """Create every missing grading period of a school year."""
    if request.method == 'POST':
        sy = get_object_or_404(SchoolYear.objects.select_related('school'), id=sy_id)
        created, skipped = _generate_periods(sy)
        plural = period_config(sy.school)['plural'].lower()
        if created:
            messages.success(request, f'{created} {plural} created for {sy.year_label} ({skipped} already existed).')
        else:
            messages.info(request, f'All {skipped} {plural} already exist for {sy.year_label}.')
    return redirect(request.POST.get('next') or 'admin_panel:quarter_manage')


@login_required
@user_passes_test(is_admin, login_url='admin_panel:login')
def quarter_update(request, quarter_id):
    """Adjust a grading period's dates, current flag and grade lock."""
    period = get_object_or_404(Quarter, id=quarter_id)
    if request.method == 'POST':
        was_locked = period.is_grades_locked
        form = QuarterUpdateForm(request.POST, instance=period)
        if form.is_valid():
            period = form.save(commit=False)
            if period.is_grades_locked and not was_locked:
                period.locked_by, period.locked_at = request.user, timezone.now()
            elif not period.is_grades_locked:
                period.locked_by, period.locked_at = None, None
            period.save()
            messages.success(request, f'{period.quarter_label} updated.')
        else:
            _form_errors_to_messages(request, form)
    return redirect(f"{reverse('admin_panel:quarter_manage')}?school_year={period.school_year_id}")


@login_required
@user_passes_test(is_admin, login_url='admin_panel:login')
def quarter_delete(request, quarter_id):
    if request.method == 'POST':
        period = get_object_or_404(Quarter, id=quarter_id)
        try:
            period.delete()
            messages.success(request, f'{period.quarter_label} deleted.')
        except ProtectedError:
            messages.error(request, f'{period.quarter_label} already has grades or records attached and cannot be deleted.')
    return redirect('admin_panel:quarter_manage')



@login_required
@user_passes_test(is_admin, login_url='admin_panel:login')
def principal_edit(request, principal_id):
    principal = get_object_or_404(UserProfile.objects.select_related('user', 'school'), id=principal_id, role='schoolhead')
    user = principal.user
    form = PrincipalEditForm(request.POST or None, principal=principal, initial={
        'first_name': user.first_name,
        'last_name': user.last_name,
        'username': user.username,
        'email': user.email,
        'school': principal.school_id,
        'employee_id': principal.employee_number,
        'designation': principal.designation,
    })
    if request.method == 'POST' and form.is_valid():
        data = form.cleaned_data
        with transaction.atomic():
            user.first_name = data['first_name'].strip()
            user.last_name = data['last_name'].strip()
            user.username = data['username']
            user.email = data['email']
            user.save()
            principal.school = data['school']
            principal.employee_number = data['employee_id'] or None
            principal.designation = data['designation'] or 'Principal'
            principal.save()
        messages.success(request, f'Principal "{principal.full_name}" updated!')
        return redirect('admin_panel:principal_detail', principal_id=principal.id)
    _form_errors_to_messages(request, form)
    return render(request, 'admin/principal_edit.html', {'principal': principal, 'form': form})


# =============================================================================
# AJAX ENDPOINTS
# =============================================================================

@login_required
@user_passes_test(is_admin, login_url='admin_panel:login')
def get_school_details(request, school_id):
    school = get_object_or_404(School, id=school_id)
    principal = get_school_principals(school).first()
    return JsonResponse({'id': str(school.id), 'name': school.school_name, 'school_id': school.school_id, 'address': school.address, 'principal': principal.full_name if principal else 'Not assigned'})


@login_required
@user_passes_test(is_admin, login_url='admin_panel:login')
def get_grade_levels_for_school(request, school_id):
    gl = GradeLevel.objects.filter(school_id=school_id).values('id', 'grade_name', 'strand').order_by('grade_number')
    return JsonResponse(list(gl), safe=False)


@login_required
@user_passes_test(is_admin, login_url='admin_panel:login')
def get_school_years_for_school(request, school_id):
    sy = SchoolYear.objects.filter(school_id=school_id).values('id', 'year_label', 'is_current').order_by('-date_start')
    return JsonResponse(list(sy), safe=False)


@login_required
@user_passes_test(is_admin, login_url='admin_panel:login')
def check_school_id(request):
    return JsonResponse({'exists': School.objects.filter(school_id=request.GET.get('school_id', '')).exists()})


@login_required
@user_passes_test(is_admin, login_url='admin_panel:login')
def check_username(request):
    return JsonResponse({'exists': User.objects.filter(username=request.GET.get('username', '')).exists()})


# =============================================================================
# REQUESTS (the landing page's "Contact us" form and the sign-in help dialog)
# =============================================================================

REQUEST_VIEWS = {
    'new': ('New', Q(status=ContactRequest.STATUS_NEW)),
    'school': ('School requests', Q(topic=ContactRequest.TOPIC_SCHOOL)),
    'support': ('Sign-in help', Q(topic=ContactRequest.TOPIC_SUPPORT)),
    'all': ('All', Q()),
}


@login_required
@user_passes_test(is_admin, login_url='admin_panel:login')
def request_list(request):
    # opens on the new requests, or on all of them when nothing is new, so the page is never empty while requests exist
    default = 'new' if ContactRequest.objects.filter(status=ContactRequest.STATUS_NEW).exists() else 'all'
    show = request.GET.get('show', default)
    if show not in REQUEST_VIEWS:
        show = default
    search = request.GET.get('q', '').strip()
    requests_qs = ContactRequest.objects.filter(REQUEST_VIEWS[show][1]).select_related('handled_by')
    if search:
        requests_qs = requests_qs.filter(
            Q(name__icontains=search) | Q(email__icontains=search) | Q(school_name__icontains=search) | Q(message__icontains=search)
        )
    counts = {key: ContactRequest.objects.filter(rule).count() for key, (_, rule) in REQUEST_VIEWS.items()}
    page = Paginator(requests_qs, 20).get_page(request.GET.get('page'))

    # what the "Send sign-in details" dialog offers: each school's School Head and registrar accounts
    schools = list(School.objects.filter(is_active=True).order_by('school_name'))
    accounts = {}
    for profile in (UserProfile.objects.filter(is_active=True, role__in=SIGN_IN_ROLES, school__in=schools)
                    .select_related('user').order_by('role', 'user__last_name')):
        accounts.setdefault(str(profile.school_id), []).append(_account_row(profile))
    for item in page:
        item.match_school = _match_school(item.school_name, schools) if item.topic == ContactRequest.TOPIC_SCHOOL else None
        own = _own_accounts(item.email) if item.topic == ContactRequest.TOPIC_SUPPORT else []
        item.own_accounts = [_account_row(profile) for profile in own]
    return render(request, 'admin/request_list.html', {
        'page': page,
        'show': show,
        'search': search,
        'tabs': [(key, label, counts[key]) for key, (label, _) in REQUEST_VIEWS.items()],
        'statuses': ContactRequest.STATUS_CHOICES,
        'contact_email': settings.ARMS_CONTACT_EMAIL,
        'schools': schools,
        'school_accounts': accounts,
        'email_ready': email_ready(),
        'sender': settings.EMAIL_HOST_USER or settings.DEFAULT_FROM_EMAIL,
        'new_accounts': A.pop_new_accounts(request),
    })


SIGN_IN_ROLES = ('schoolhead', 'registrar')
ROLE_LABELS = {'schoolhead': 'School Head', 'registrar': 'Registrar', 'teacher': 'Teacher'}


def _account_row(profile):
    return {'id': profile.id, 'name': profile.full_name, 'role': ROLE_LABELS.get(profile.role, profile.get_role_display()),
            'email': profile.user.username, 'signed_in': profile.user.last_login is not None}


def _match_school(name, schools):
    """The ARMS school a request is about: same name or short name, or a close spelling of the name."""
    import difflib
    import re

    def key(value):
        return re.sub(r'[^a-z0-9]+', ' ', (value or '').lower()).strip()

    target = key(name)
    if not target:
        return None
    best, score = None, 0.0
    for school in schools:
        if target in (key(school.school_name), key(school.short_name)):
            return school
        ratio = difflib.SequenceMatcher(None, key(school.school_name), target).ratio()
        if ratio > score:
            best, score = school, ratio
    return best if score >= 0.75 else None


def _own_accounts(email):
    """The accounts of someone asking for sign-in help: the ones signing in with the email they wrote from."""
    email = (email or '').strip()
    if not email:
        return []
    return list(UserProfile.objects.filter(is_active=True, user__is_superuser=False, school__isnull=False)
                .filter(Q(user__email__iexact=email) | Q(user__username__iexact=email)).exclude(role='admin')
                .select_related('user', 'school'))


@login_required
@user_passes_test(is_admin, login_url='admin_panel:login')
@require_POST
def request_update(request, request_id):
    contact = get_object_or_404(ContactRequest, pk=request_id)
    status = request.POST.get('status')
    if status in dict(ContactRequest.STATUS_CHOICES):
        contact.status = status
        handled = status != ContactRequest.STATUS_NEW
        contact.handled_by = request.user if handled else None
        contact.handled_at = timezone.now() if handled else None
        contact.save(update_fields=['status', 'handled_by', 'handled_at'])
        messages.success(request, f'{contact.school_name or contact.name} is marked {contact.get_status_display().lower()}.')
    back = request.POST.get('next', '')
    if not back.startswith('/admin-panel/requests/'):
        back = reverse('admin_panel:request_list')
    return redirect(back)


@login_required
@user_passes_test(is_admin, login_url='admin_panel:login')
@require_POST
def request_send_accounts(request, request_id):
    """Answer a request with setup links: each chosen account gets a one-time link at its OWN email to choose a password,
    and the person who wrote gets a note that the school is ready, with no link and the addresses masked. Nobody's
    password travels by email, and a request written under a false name cannot redirect an account to its writer.
    The chosen accounts' current passwords stop working; nothing changes when the email cannot be sent."""
    from accounts.services.email_codes import mask_email
    from accounts.views_account import SETUP_DAYS, setup_link

    contact = get_object_or_404(ContactRequest, pk=request_id)
    back = request.POST.get('next', '')
    if not back.startswith('/admin-panel/requests/'):
        back = reverse('admin_panel:request_list')
    if not email_ready():
        messages.error(request, 'Email is not set up on this server. Add EMAIL_HOST_USER and EMAIL_HOST_PASSWORD '
                                '(a Gmail app password) to its environment variables, then send again.')
        return redirect(back)

    school = School.objects.filter(pk=request.POST.get('school') or None).first()
    allowed = Q(user__email__iexact=contact.email) | Q(user__username__iexact=contact.email)
    if school is not None:
        allowed |= Q(school=school, role__in=SIGN_IN_ROLES)
    ids = [i for i in request.POST.getlist('accounts') if i.isdigit()]
    chosen = list(UserProfile.objects.filter(allowed, pk__in=ids, is_active=True, user__is_superuser=False)
                  .exclude(role='admin').select_related('user', 'school').order_by('role', 'user__last_name'))
    if not chosen:
        messages.error(request, 'Tick at least one account to send.')
        return redirect(back)
    no_email = [p.full_name for p in chosen if not (p.user.email or '').strip()]
    if no_email:
        messages.error(request, f'{", ".join(no_email)} {"has" if len(no_email) == 1 else "have"} no email in ARMS, so no '
                                'setup link can be sent. Add the email on the account first.')
        return redirect(back)

    school = school or chosen[0].school
    note = request.POST.get('note', '').strip()[:2000]
    setups = []
    try:
        with transaction.atomic():
            for p in chosen:
                p.user.set_unusable_password()          # only the link holder can sign in from now on
                p.user.save(update_fields=['password'])
                setups.append({'email': p.user.email.strip(), 'name': p.full_name, 'username': p.user.username,
                               'role': ROLE_LABELS.get(p.role, p.get_role_display()), 'link': setup_link(request, p.user),
                               'days': SETUP_DAYS, 'masked': mask_email(p.user.email.strip())})
            sent = send_setup_emails(setups=setups, requester=(contact.email, contact.name),
                                     school_name=school.school_name if school else '', note=note,
                                     first_time=contact.topic == ContactRequest.TOPIC_SCHOOL)
    except Exception as exc:                     # the passwords stay as they were (the transaction rolled back)
        messages.error(request, f'The email could not be sent, so no password was changed. The mail server said: {exc}')
        return redirect(back)

    contact.status, contact.handled_by, contact.handled_at = 'done', request.user, timezone.now()
    contact.save(update_fields=['status', 'handled_by', 'handled_at'])
    count = len(setups)
    messages.success(request, f'Sent {count} setup link{"s" if count != 1 else ""}, each to the account\'s own email, '
                              f'and told {contact.email} the school is ready ({sent} emails from {settings.EMAIL_HOST_USER}). '
                              'The request is marked done.')
    _log_admin_action(request, 'SEND_SETUP_LINKS',
                      f'Emailed setup links to {", ".join(s["username"] for s in setups)} (request from {contact.email})')
    return redirect(back)


# =============================================================================
# GRADE FORECASTER: the model behind every School Head forecast
# =============================================================================
# Retraining is the System Administrator's: the model learns from every school's records at once. It also
# retrains by itself whenever a registrar locks a grading period (ml_engine/training.py).

@login_required
@user_passes_test(is_admin, login_url='admin_panel:login')
def forecaster(request):
    from ml_engine import report, training
    model = report.summary()
    on_vercel = getattr(settings, 'ON_VERCEL', False)
    return render(request, 'admin/forecaster.html', {
        'model': model, 'run': report.run_status(), 'log': [] if on_vercel else training.log_tail(),
        'on_vercel': on_vercel, 'hook_ready': training.deploy_hook_ready(), 'last_deploy': training.last_deploy() if on_vercel else None,
        'deploying': training.deploying(model['trained_at'] if model else None) if on_vercel else None,
    })


@login_required
@user_passes_test(is_admin, login_url='admin_panel:login')
@require_POST
def forecaster_retrain(request):
    from ml_engine import training
    by = request.user.get_full_name() or request.user.username
    if training.start('Started by the System Administrator', by=by):
        if getattr(settings, 'ON_VERCEL', False):
            messages.success(request, 'Retraining started: Vercel is building a new deployment, which trains the model on every '
                                      'school\'s grades. The new model goes live when the build ends, in about 5 minutes.')
        else:
            messages.success(request, 'Retraining started. It takes about a minute, and this page updates when it ends.')
        _log_admin_action(request, 'RETRAIN_MODEL', 'Started retraining the grade forecaster')
    elif getattr(settings, 'ON_VERCEL', False) and not training.deploy_hook_ready():
        messages.error(request, 'Retraining on Vercel needs a Deploy Hook. In Vercel, open the project\'s Settings, then Git, '
                                'then Deploy Hooks; create one for the main branch, and add its URL as the environment variable '
                                'VERCEL_DEPLOY_HOOK_URL. Redeploy once so ARMS can read it.')
    elif getattr(settings, 'ON_VERCEL', False) and training.deploying():
        messages.info(request, 'A new deployment is already building. It retrains the model; check back in a few minutes.')
    elif getattr(settings, 'ON_VERCEL', False):
        messages.error(request, 'Vercel did not accept the Deploy Hook. Check VERCEL_DEPLOY_HOOK_URL in the project\'s '
                                'environment variables.')
    else:
        messages.info(request, 'A retraining run is already going.')
    return redirect('admin_panel:forecaster')


@login_required
@user_passes_test(is_admin, login_url='admin_panel:login')
def forecaster_status(request):
    from ml_engine import training
    run = training.status()
    return JsonResponse({'state': run.get('state', 'idle'), 'detail': run.get('detail', '')})
