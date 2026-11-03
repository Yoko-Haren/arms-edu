from django import forms
from django.contrib.auth.models import User
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.db.models import Q
import json
import re

# ===== USE REAL MODELS =====
from academics.models import School, GradeLevel, SchoolYear, Quarter, GradingSchema
from academics.school_years import PERIOD_CONFIG, period_config  # noqa: F401 (grading periods follow School.period_type)
from accounts.models import UserProfile
from core import grading_policy

from .accounts import (domain_problem, email_taken, normalize_domain, person_email, registrar_email, short_name_from,
                       temporary_password)

# Kindergarten (0) to Grade 12, the levels a DepEd school can offer.
GRADE_CHOICES = [(0, 'Kindergarten')] + [(n, f'Grade {n}') for n in range(1, 13)]
GRADE_NUMBERS = [n for n, _ in GRADE_CHOICES]
# The grading periods each DepEd order is written for: DO 015, s. 2026 brought the three terms of DO 009, s. 2026.
POLICY_PERIODS = {grading_policy.DO15_2026: 'TRIMESTRAL', grading_policy.DO8_2015: 'QUARTERLY'}
MIN_PASSWORD = 10


def grade_stage(number):
    """The note under a grade level's name on the forms (Kindergarten's own name is its stage)."""
    return 'Before Grade 1' if number == 0 else 'Elementary' if number <= 6 else 'Junior High' if number <= 10 else 'Senior High'


def add_grade_levels(school, numbers):
    """Add the grade levels a school offers; ones it already has are skipped. Returns how many were added."""
    added = 0
    for number in sorted(set(numbers)):
        _, made = GradeLevel.objects.get_or_create(
            school=school, grade_code='K' if number == 0 else f'G{number}',
            defaults={'grade_name': 'Kindergarten' if number == 0 else f'Grade {number}', 'grade_number': number,
                      'sort_order': number},
        )
        added += made
    return added


def _one_line(value):
    return ' '.join((value or '').split())


# =============================================================================
# ADMIN LOGIN
# =============================================================================

class AdminLoginForm(forms.Form):
    username = forms.CharField(
        max_length=150,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Username'})
    )
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': 'Password'})
    )


# =============================================================================
# SCHOOL FORM — With Grading Schema Auto-Creation
# =============================================================================

class SchoolForm(forms.ModelForm):
    """A school and, when it is new, the grade levels it offers.

    The short name is made from the name when left blank, the number of grading periods follows the period type, and
    the grade ranges of DepEd's K to 12 are created for a new school (the DepEd order it follows is grading_policy).
    """

    grades = forms.TypedMultipleChoiceField(choices=GRADE_CHOICES, coerce=int, required=False)

    class Meta:
        model = School
        fields = [
            'school_id', 'school_name', 'short_name', 'email_domain', 'address',
            'grading_policy', 'period_type', 'grading_scale', 'passing_grade', 'quarters_count', 'is_active',
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ('short_name', 'quarters_count', 'grading_scale', 'passing_grade'):
            self.fields[name].required = False

    def clean_school_id(self):
        school_id = _one_line(self.cleaned_data.get('school_id'))
        if School.objects.exclude(pk=self.instance.pk).filter(school_id__iexact=school_id).exists():
            raise ValidationError('Another school already has this School ID.')
        return school_id

    def clean_school_name(self):
        return _one_line(self.cleaned_data.get('school_name'))

    def clean_short_name(self):
        short = _one_line(self.cleaned_data.get('short_name')).upper()
        return short or short_name_from(self.cleaned_data.get('school_name', ''))

    def clean_email_domain(self):
        domain = normalize_domain(self.cleaned_data.get('email_domain'))
        problem = domain_problem(domain)
        if problem:
            raise ValidationError(problem)
        return f'@{domain}' if domain else ''

    def clean_grading_scale(self):
        return self.cleaned_data.get('grading_scale') or 'PERCENTAGE'

    def clean_passing_grade(self):
        value = self.cleaned_data.get('passing_grade')
        return 75.0 if value is None else value

    def clean(self):
        cleaned_data = super().clean()
        if not cleaned_data.get('short_name') and cleaned_data.get('school_name'):
            cleaned_data['short_name'] = short_name_from(cleaned_data['school_name'])
        period_type = cleaned_data.get('period_type')
        if period_type in PERIOD_CONFIG:
            cleaned_data['quarters_count'] = PERIOD_CONFIG[period_type]['count']
        return cleaned_data

    def save(self, commit=True):
        creating = self.instance.pk is None
        school = super().save(commit=False)
        if commit:
            school.save()
            if creating:
                if not GradingSchema.objects.filter(school=school).exists():
                    self._create_default_schema(school, 'DEPED_K12_COMPLETE')
                self.grades_added = add_grade_levels(school, self.cleaned_data.get('grades') or [])
        return school

    def _create_default_schema(self, school, schema_type):
        """Create default grading schema(s) based on school type."""
        import json

        # ===== COMMON GRADE RANGES =====

        # DepEd descriptors, DO 8, s. 2015 (the School Head can switch the school to DO 015, s. 2026 on the
        # School Year page; core/grading_policy.py)
        DEPED_RANGES = [
            (90, 100, 'O', 'Outstanding', 4.0, True),
            (85, 89, 'VS', 'Very Satisfactory', 3.5, True),
            (80, 84, 'S', 'Satisfactory', 3.0, True),
            (75, 79, 'FS', 'Fairly Satisfactory', 2.5, True),
            (0, 74, 'DNME', 'Did Not Meet Expectations', 1.0, False),
        ]

        # CHED CMO 14, s. 2019 — College
        CHED_RANGES = [
            (96, 100, '1.00', 'Excellent', 4.0, True),
            (90, 95, '1.25', 'Superior', 3.5, True),
            (85, 89, '1.50', 'Very Good', 3.0, True),
            (80, 84, '1.75', 'Good', 2.5, True),
            (75, 79, '2.00', 'Satisfactory', 2.0, True),
            (70, 74, '2.50', 'Fair', 1.5, False),
            (65, 69, '3.00', 'Pass', 1.0, False),
            (0, 64, '5.00', 'Failed', 0.0, False),
        ]

        # TESDA Competency-Based
        TESDA_RANGES = [
            (90, 100, 'COC', 'Certificate of Competency', 4.0, True),
            (80, 89, 'NC', 'National Certificate', 3.5, True),
            (75, 79, 'NC-L1', 'NC Level 1', 3.0, True),
            (0, 74, 'NYC', 'Not Yet Competent', 0.0, False),
        ]

        # Custom fallback
        CUSTOM_RANGES = [
            (90, 100, 'A', 'Excellent', 4.0, True),
            (80, 89, 'B', 'Good', 3.0, True),
            (75, 79, 'C', 'Pass', 2.0, True),
            (0, 74, 'F', 'Fail', 0.0, False),
        ]

        # ===== BUILD SCHEMAS TO CREATE =====
        schemas_to_create = []

        if schema_type == 'DEPED_JHS':
            schemas_to_create.append(('DEPED_JHS', 'DepEd JHS — DO 8, s. 2015 (Grades 7-10)', 'QUARTERLY', 'JHS', DEPED_RANGES))

        elif schema_type == 'DEPED_SHS':
            schemas_to_create.append(('DEPED_SHS', 'DepEd SHS — DO 8, s. 2015 (Grades 11-12)', 'SEMESTRAL', 'SHS', DEPED_RANGES))

        elif schema_type == 'DEPED_INTEGRATED':
            # Creates 2 schemas: JHS (Quarterly) + SHS (Semestral)
            schemas_to_create.append(('DEPED_INTEGRATED_JHS', 'DepEd JHS — DO 8, s. 2015 (Grades 7-10)', 'QUARTERLY', 'JHS', DEPED_RANGES))
            schemas_to_create.append(('DEPED_INTEGRATED_SHS', 'DepEd SHS — DO 8, s. 2015 (Grades 11-12)', 'SEMESTRAL', 'SHS', DEPED_RANGES))

        elif schema_type == 'DEPED_ELEMENTARY':
            schemas_to_create.append(('DEPED_ELEMENTARY', 'DepEd Elementary — DO 8, s. 2015 (Grades 1-6)', 'QUARTERLY', 'ELEMENTARY', DEPED_RANGES))

        elif schema_type == 'DEPED_K12_COMPLETE':
            # Creates 3 schemas: Elementary + JHS + SHS
            schemas_to_create.append(('DEPED_K12_ELEM', 'DepEd Elementary — DO 8, s. 2015 (Grades 1-6)', 'QUARTERLY', 'ELEMENTARY', DEPED_RANGES))
            schemas_to_create.append(('DEPED_K12_JHS', 'DepEd JHS — DO 8, s. 2015 (Grades 7-10)', 'QUARTERLY', 'JHS', DEPED_RANGES))
            schemas_to_create.append(('DEPED_K12_SHS', 'DepEd SHS — DO 8, s. 2015 (Grades 11-12)', 'SEMESTRAL', 'SHS', DEPED_RANGES))

        elif schema_type == 'CHED_STANDARD':
            schemas_to_create.append(('CHED_STANDARD', 'CHED Standard — CMO 14, s. 2019', 'SEMESTRAL', 'COLLEGE', CHED_RANGES))

        elif schema_type == 'CHED_TRIMESTER':
            schemas_to_create.append(('CHED_TRIMESTER', 'CHED Trimester — CMO 14, s. 2019', 'TRIMESTER', 'COLLEGE', CHED_RANGES))

        elif schema_type == 'TESDA_COMPETENCY':
            schemas_to_create.append(('TESDA_COMPETENCY', 'TESDA Competency-Based', 'MODULAR', 'TVL', TESDA_RANGES))

        else:  # CUSTOM or fallback
            schemas_to_create.append(('CUSTOM', 'Custom Grading System', 'QUARTERLY', 'ALL', CUSTOM_RANGES))

        # ===== CREATE ALL SCHEMAS =====
        for scale_type, desc, period, grade_level, ranges in schemas_to_create:
            ranges_list = [{'min': mn, 'max': mx, 'letter': lt, 'label': lb, 'gpa': gp, 'passing': ps}
                           for mn, mx, lt, lb, gp, ps in ranges]

            GradingSchema.objects.create(
                school=school,
                scale_type=scale_type,
                passing_threshold=75,
                highest_is_best=True,
                rounding_rule='ROUND_HALF_UP',
                min_value=0,
                max_value=100,
                letter_grade_mapping=json.dumps({
                    'ranges': ranges_list,
                    'description': desc,
                    'period_type': period,
                    'grade_level': grade_level,
                }),
            )

# =============================================================================
# PRINCIPAL CREATION
# =============================================================================

class _StaffAccountForm(forms.Form):
    """A school account whose email is also its username. The email is made from the school's email domain when left
    blank, and the temporary password is generated when left blank."""

    role_label = 'account'

    school = forms.ModelChoiceField(queryset=School.objects.filter(is_active=True))
    first_name = forms.CharField(max_length=100, required=False)
    last_name = forms.CharField(max_length=100, required=False)
    email = forms.EmailField(required=False)
    password = forms.CharField(max_length=128, required=False, strip=True)
    employee_id = forms.CharField(max_length=50, required=False)
    designation = forms.CharField(max_length=150, required=False)

    def clean_first_name(self):
        return _one_line(self.cleaned_data.get('first_name'))

    def clean_last_name(self):
        return _one_line(self.cleaned_data.get('last_name'))

    def clean_employee_id(self):
        emp_id = _one_line(self.cleaned_data.get('employee_id'))
        if emp_id and UserProfile.objects.filter(employee_number=emp_id).exists():
            raise ValidationError('Another account already has this Employee ID.')
        return emp_id

    def clean_password(self):
        password = self.cleaned_data.get('password') or temporary_password()
        if len(password) < MIN_PASSWORD:
            raise ValidationError(f'Use at least {MIN_PASSWORD} characters, or leave it blank for ARMS to make one.')
        return password

    def suggested_email(self, school, cleaned):
        raise NotImplementedError

    def clean(self):
        cleaned = super().clean()
        school = cleaned.get('school')
        if school is None or self.errors.get('first_name') or self.errors.get('last_name'):
            return cleaned
        email = (cleaned.get('email') or '').strip().lower()
        if not email:
            email = self.suggested_email(school, cleaned)
            if not email and 'email' not in self.errors:
                self.add_error('email', f'{school.school_name} has no email domain in ARMS, so type the {self.role_label}\'s '
                                        'email. It is also their username.')
        elif email_taken(email):
            self.add_error('email', 'That email already has an ARMS account.')
        cleaned['email'] = email
        return cleaned


class PrincipalCreationForm(_StaffAccountForm):
    """The School Head's account."""

    role_label = 'principal'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['first_name'].required = self.fields['last_name'].required = True

    def suggested_email(self, school, cleaned):
        return person_email(school, cleaned.get('first_name'), cleaned.get('last_name'))


class PrincipalEditForm(forms.Form):
    """Edit an existing principal's account details and school assignment."""

    first_name = forms.CharField(max_length=100)
    last_name = forms.CharField(max_length=100)
    username = forms.CharField(max_length=150)
    email = forms.EmailField()
    school = forms.ModelChoiceField(queryset=School.objects.none())
    employee_id = forms.CharField(max_length=50, required=False)
    designation = forms.CharField(max_length=150, required=False)

    def __init__(self, *args, principal, **kwargs):
        self.principal = principal
        super().__init__(*args, **kwargs)
        # Keep the current school selectable even if it has been deactivated.
        self.fields['school'].queryset = School.objects.filter(
            Q(is_active=True) | Q(pk=principal.school_id)
        )

    def clean_username(self):
        username = self.cleaned_data.get('username', '').strip().lower()
        if User.objects.filter(username__iexact=username).exclude(pk=self.principal.user_id).exists():
            raise ValidationError('This username is already taken.')
        return username

    def clean_email(self):
        email = self.cleaned_data.get('email', '').strip().lower()
        if User.objects.filter(email__iexact=email).exclude(pk=self.principal.user_id).exists():
            raise ValidationError('This email is already in use.')
        return email

    def clean_employee_id(self):
        emp_id = (self.cleaned_data.get('employee_id') or '').strip()
        if emp_id and UserProfile.objects.filter(employee_number=emp_id).exclude(pk=self.principal.pk).exists():
            raise ValidationError('This Employee ID is already assigned.')
        return emp_id


class RegistrarCreationForm(_StaffAccountForm):
    """A registrar's account. With no name it is the school's registrar account: '<short name> Registrar' signing in as
    shortname.registrar@<email domain>."""

    role_label = 'registrar'

    def suggested_email(self, school, cleaned):
        if cleaned.get('first_name') and cleaned.get('last_name'):
            return person_email(school, cleaned['first_name'], cleaned['last_name'])
        return registrar_email(school)

    def clean(self):
        cleaned = super().clean()
        school = cleaned.get('school')
        if school is not None and not (cleaned.get('first_name') and cleaned.get('last_name')):
            cleaned['first_name'] = school.short_name or short_name_from(school.school_name)
            cleaned['last_name'] = 'Registrar'
        return cleaned


# =============================================================================
# GRADE LEVEL FORM
# =============================================================================

class GradeLevelForm(forms.Form):
    school = forms.ModelChoiceField(queryset=School.objects.filter(is_active=True))
    grades = forms.TypedMultipleChoiceField(
        choices=GRADE_CHOICES,
        coerce=int,
        error_messages={'required': 'Select at least one grade level.'},
    )


# =============================================================================
# SCHOOL YEAR FORM
# =============================================================================

class SchoolYearForm(forms.ModelForm):
    """Only the starting year and class dates are entered; label, end year and status are derived."""

    auto_create_periods = forms.BooleanField(required=False)

    class Meta:
        model = SchoolYear
        fields = ['school', 'year_start', 'date_start', 'date_end', 'is_current']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['school'].queryset = School.objects.filter(is_active=True)

    def clean(self):
        cleaned_data = super().clean()
        school = cleaned_data.get('school')
        year_start = cleaned_data.get('year_start')
        if school and year_start:
            label = f'{year_start}-{year_start + 1}'
            if SchoolYear.objects.filter(school=school, year_label=label).exists():
                raise ValidationError({'year_start': f'School year {label} already exists for {school.school_name}.'})
        return cleaned_data

    def save(self, commit=True):
        sy = super().save(commit=False)
        sy.year_end = sy.year_start + 1
        sy.year_label = f'{sy.year_start}-{sy.year_end}'
        sy.status = 'Active' if sy.is_current else 'Upcoming'
        if commit:
            sy.save()
        return sy


# =============================================================================
# GRADING PERIOD FORMS (stored in the Quarter table)
# =============================================================================

class QuarterForm(forms.ModelForm):
    """One grading period; its label (Q/T/S + number) follows the school's period type."""

    class Meta:
        model = Quarter
        fields = ['school_year', 'quarter_number', 'date_start', 'date_end', 'is_current_quarter']
        error_messages = {
            NON_FIELD_ERRORS: {'unique_together': 'That period already exists for this school year.'},
        }

    def clean(self):
        cleaned_data = super().clean()
        school_year = cleaned_data.get('school_year')
        number = cleaned_data.get('quarter_number')
        if school_year and number:
            cfg = period_config(school_year.school)
            if number > cfg['count']:
                raise ValidationError({
                    'quarter_number': f'{school_year.school.school_name} only has {cfg["count"]} {cfg["plural"].lower()} per school year.'
                })
            self.instance.quarter_label = f'{cfg["prefix"]}{number}'
        return cleaned_data


class QuarterUpdateForm(forms.ModelForm):
    class Meta:
        model = Quarter
        fields = ['date_start', 'date_end', 'is_current_quarter', 'is_grades_locked']
