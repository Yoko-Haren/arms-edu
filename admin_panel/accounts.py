"""The rules that let the admin forms fill themselves in: short names, sign-in emails and temporary passwords.

A school's staff sign in with an email that is also their username. When the school has an email domain, ARMS
makes it (firstname.lastname@domain, shortname.registrar@domain); without one, the administrator types it. Every
account starts with a temporary password that its owner replaces at the first sign-in (must_change_password), so
the administrator never has to think one up. The new sign-in details are shown once, on the next page.
"""
import re
import secrets

from django.contrib.auth.models import Group, User
from django.db import transaction

from registrars.forms import _email_taken, school_email_domain, suggest_teacher_email

# Words left out of a school's initials ("Panabo National High School" is PNHS).
_SMALL_WORDS = {'of', 'and', 'the', 'for', 'in', 'at', 'de', 'del', 'la', 'ng', 'sa', 'inc', 'incorporated', 'corp', 'co'}
# Public mail services: a school cannot claim their domain for its staff accounts.
PUBLIC_MAIL_DOMAINS = {'gmail.com', 'googlemail.com', 'yahoo.com', 'ymail.com', 'outlook.com', 'hotmail.com', 'live.com',
                       'icloud.com', 'aol.com', 'proton.me', 'protonmail.com'}
_DOMAIN = re.compile(r'^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+$')

# Letters and digits that cannot be misread when a password is read aloud or copied by hand.
_UPPER, _LOWER, _DIGITS = 'ABCDEFGHJKMNPQRSTUVWXYZ', 'abcdefghjkmnpqrstuvwxyz', '23456789'
SESSION_KEY = 'ap_new_accounts'


def short_name_from(name):
    """Initials of a school's name: 'ARMS Demo Three-Term High School' gives 'ADTHS'."""
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'-]*", name or '')
    big = [w for w in words if w.lower().strip("'") not in _SMALL_WORDS]
    if len(big) >= 2:
        return ''.join(w[0] for w in big).upper()[:12]
    return (big[0] if big else '').upper()[:12]


def normalize_domain(value):
    """'@School.edu.ph' or 'school.edu.ph' gives 'school.edu.ph'; '' when blank."""
    return (value or '').strip().lower().lstrip('@').strip()


def domain_problem(domain):
    """Why a school email domain cannot be used, or None."""
    if not domain:
        return None
    if not _DOMAIN.match(domain):
        return 'Type a domain such as school.edu.ph, without a name before it.'
    if domain in PUBLIC_MAIL_DOMAINS:
        return (f'{domain} belongs to a public mail service, so ARMS would make addresses that belong to other people. '
                'Leave it blank and type each email instead.')
    return None


def temporary_password():
    """A readable temporary password such as 'Kpmw-4827-Tzhr'."""
    pick = secrets.choice
    word = lambda: pick(_UPPER) + ''.join(pick(_LOWER) for _ in range(3))  # noqa: E731
    return f'{word()}-{"".join(pick(_DIGITS) for _ in range(4))}-{word()}'


def person_email(school, first_name, last_name):
    """firstname.lastname@<school domain>, numbered when taken; None when the school has no domain."""
    return suggest_teacher_email(school, first_name, last_name)


def registrar_email(school):
    """<short name>.registrar@<school domain>, numbered when taken; None when the school has no domain."""
    domain = school_email_domain(school)
    slug = re.sub(r'[^a-z0-9]+', '', (school.short_name or short_name_from(school.school_name)).lower())
    if not domain or not slug:
        return None
    email, counter = f'{slug}.registrar@{domain}', 1
    while _email_taken(email):
        counter += 1
        email = f'{slug}.registrar{counter}@{domain}'
    return email


def email_taken(email):
    return _email_taken((email or '').strip().lower())


def create_staff(*, school, role, group_name, position_title, first_name, last_name, email, password, employee_id='',
                 designation=''):
    """A school account whose username is its email. The password is temporary: it must be changed at the first sign-in."""
    email = email.strip().lower()
    with transaction.atomic():
        user = User.objects.create_user(username=email, email=email, password=password, first_name=first_name.strip(),
                                        last_name=last_name.strip(), is_active=True, is_staff=False)
        profile = user.profile
        profile.role = role
        profile.school = school
        profile.employee_number = employee_id or None
        profile.designation = designation or group_name
        profile.position_title = position_title
        profile.employment_status = 'Regular_Permanent'
        profile.must_change_password = True
        profile.save()
        user.groups.add(Group.objects.get_or_create(name=group_name)[0])
    return user


def reset_temporary_password(user):
    """Give an account a fresh temporary password and return it; the owner must change it at the next sign-in."""
    password = temporary_password()
    user.set_password(password)
    user.save()
    profile = user.profile
    profile.must_change_password = True
    profile.save(update_fields=['must_change_password', 'updated_at'])
    return password


def account_card(user, password, role_label):
    """What the one-time sign-in card shows for a new or reset account."""
    return {'name': user.get_full_name() or user.username, 'role': role_label, 'username': user.username, 'password': password}


def remember_new_accounts(request, cards):
    """Keep sign-in details for the next page only; they are never stored anywhere else."""
    request.session[SESSION_KEY] = cards


def pop_new_accounts(request):
    return request.session.pop(SESSION_KEY, [])
