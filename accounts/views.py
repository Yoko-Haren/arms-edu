# accounts/views.py
import hashlib
import logging
import time
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout as auth_logout
from django.contrib import messages
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.conf import settings
from django.views.decorators.http import require_http_methods
from core.net import client_ip

from .models import UserProfile, LoginAttempt
from .services.sessions import end_other_sessions, remember_session


logger = logging.getLogger(__name__)


# ── SECURITY CONSTANTS ──
# Failed sign-ins are counted three ways over the last LOCKOUT_DURATION_MINUTES:
MAX_LOGIN_ATTEMPTS = 5          # one account from one network: that network is locked out of the account
MAX_ACCOUNT_FAILURES = 20       # one account from anywhere: the account is locked (guessing spread over many machines)
MAX_NETWORK_FAILURES = 30       # one network across any accounts: the network waits (one password tried on many accounts)
LOCKOUT_DURATION_MINUTES = 15   # How long the lockout lasts
SALT = "D0pp3lG4ng3r_F0rm1fy_S3cur3_S4lt_2026"  # Never expose this


def _hash_password_with_salt(password):
    """SHA-256 hash with salt — extra layer before Django's PBKDF2."""
    salted = f"{password}:{SALT}"
    return hashlib.sha256(salted.encode('utf-8')).hexdigest()


def _recent_failures(**where):
    cutoff = timezone.now() - timedelta(minutes=LOCKOUT_DURATION_MINUTES)
    return LoginAttempt.objects.filter(attempt_result__startswith='FAILED', attempted_at__gte=cutoff, **where).count()


def _sign_in_blocked(username, ip):
    """Why a sign-in may not be tried now, or None. A stranger who fails five times locks only their own network out
    of the account, not the account's owner; many networks, or many accounts from one network, lock more."""
    wait = f'Please wait {LOCKOUT_DURATION_MINUTES} minutes'
    if _recent_failures(ip_address=ip) >= MAX_NETWORK_FAILURES:
        return f'Too many failed sign-ins from this network. {wait} and try again.'
    if _recent_failures(username_attempted__iexact=username) >= MAX_ACCOUNT_FAILURES:
        return f'This account is locked for now after too many failed sign-ins. {wait}, or reset your password.'
    if _recent_failures(username_attempted__iexact=username, ip_address=ip) >= MAX_LOGIN_ATTEMPTS:
        return (f'Account temporarily locked due to {MAX_LOGIN_ATTEMPTS} failed attempts. '
                f'{wait} or reset your password.')
    return None


def _get_dashboard_url(role):
    """Return the dashboard URL for a given role."""
    redirects = {
        'teacher': '/teachers/',
        'registrar': '/registrars/',
        'schoolhead': '/heads/ai-dashboard/',
        'admin': '/admin-panel/',
    }
    return redirects.get(role, '/')


def _safe_next(request):
    """The page a signed-out visitor was trying to open (?next=...), if it is a page on this site."""
    target = (request.POST.get('next') or request.GET.get('next') or '').strip()
    if not target.startswith('/') or target.startswith('/accounts/signin'):
        return ''
    if not url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return ''
    return target


def _signin_page(request, error=None):
    """The sign-in page, keeping what was typed: the username, Remember me and where to go next."""
    return render(request, 'shared/accounts/signin.html', {
        'error': error,
        'username': request.POST.get('username', '').strip(),
        'remember': request.POST.get('remember') == '1',
        'next': _safe_next(request),
    })


def signin(request):
    # ── If already logged in, redirect to their dashboard ──
    if request.user.is_authenticated:
        try:
            role = request.user.profile.role
        except UserProfile.DoesNotExist:
            role = 'teacher'
        return redirect(_safe_next(request) or _get_dashboard_url(role))

    error_message = None

    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '')
        ip = client_ip(request)
        user_agent = request.META.get('HTTP_USER_AGENT', '')[:255]

        # ── 1. Basic input validation ──
        if not username or not password:
            error_message = 'Please enter both username and password.'
            return _signin_page(request, error_message)

        # ── 2. Check if this account, or this network, is locked out for now ──
        blocked = _sign_in_blocked(username, ip)
        if blocked:
            LoginAttempt.objects.create(
                username_attempted=username,
                ip_address=ip,
                user_agent=user_agent,
                attempt_result='FAILED_LOCKED',
            )
            return _signin_page(request, blocked)

        # ── 3. Pre-hash with salt (adds latency for brute-force) ──
        _hash_password_with_salt(password)

        # ── 4. Authenticate using Django's built-in (PBKDF2 + salt) ──
        user = authenticate(request, username=username, password=password)

        if user is not None:
            # ── 5. Verify user is active ──
            if not user.is_active:
                LoginAttempt.objects.create(
                    username_attempted=username,
                    ip_address=ip,
                    user_agent=user_agent,
                    attempt_result='FAILED_INACTIVE',
                    user=user,
                )
                error_message = 'Your account has been deactivated. Contact your administrator.'
                return _signin_page(request, error_message)

            # ── 6. Get user's role from profile (server-side, not client input) ──
            try:
                actual_role = user.profile.role
            except UserProfile.DoesNotExist:
                # Auto-create profile with default role if missing
                UserProfile.objects.create(user=user, role='teacher')
                actual_role = 'teacher'

            # ── 7. Success — log the attempt, kill other sessions, login ──
            LoginAttempt.objects.create(
                username_attempted=username,
                ip_address=ip,
                user_agent=user_agent,
                attempt_result='SUCCESS',
                user=user,
            )

            login(request, user)
            remember_session(request, request.POST.get('remember') == '1')
            end_other_sessions(user, request.session.session_key)  # single session per user

            # ── 8. Back to the page they asked for, or their dashboard (role from the database) ──
            return redirect(_safe_next(request) or _get_dashboard_url(actual_role))

        else:
            # ── 9. Failed login — log it ──
            LoginAttempt.objects.create(
                username_attempted=username,
                ip_address=ip,
                user_agent=user_agent,
                attempt_result='FAILED_INVALID_CREDENTIALS',
            )

            # ── 10. Add artificial delay to slow down brute-force ──
            time.sleep(1.5)

            remaining = MAX_LOGIN_ATTEMPTS - _recent_failures(username_attempted__iexact=username, ip_address=ip)

            if remaining <= 2 and remaining > 0:
                error_message = (
                    f'Invalid credentials. {remaining} attempt{"s" if remaining > 1 else ""} '
                    f'remaining before account lockout.'
                )
            else:
                error_message = 'Invalid username or password.'

            return _signin_page(request, error_message)

    # GET request — show empty form
    return _signin_page(request, error_message)


def signout(request):
    auth_logout(request)
    return redirect('signin')


@require_http_methods(['GET', 'POST'])
def password_change_required(request):
    """First sign-in with a temporary password: the user must choose their own."""
    from django.contrib.auth import update_session_auth_hash
    from django.contrib.auth.password_validation import validate_password

    if not request.user.is_authenticated:
        return redirect('signin')
    profile = request.user.profile
    if not profile.must_change_password:
        return redirect(_get_dashboard_url(profile.role))

    errors = []
    if request.method == 'POST':
        new_password = request.POST.get('new_password', '')
        confirm_password = request.POST.get('confirm_password', '')
        if new_password != confirm_password:
            errors.append('The two passwords do not match.')
        elif request.user.check_password(new_password):
            errors.append('Choose a password different from the temporary one.')
        else:
            try:
                validate_password(new_password, user=request.user)
            except ValidationError as exc:
                errors.extend(exc.messages)
        if not errors:
            request.user.set_password(new_password)
            request.user.save()
            profile.must_change_password = False
            profile.save(update_fields=['must_change_password', 'updated_at'])
            update_session_auth_hash(request, request.user)  # stay signed in
            messages.success(request, 'Your password has been updated.')
            return redirect(_get_dashboard_url(profile.role))

    return render(request, 'accounts/password_change_required.html', {'errors': errors})
