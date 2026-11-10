"""Grow the three-term demo school (DEMO-TERM) to a full school: Kindergarten with 4 sections, Grades 1 to 12 with 20 each.

    python manage.py seed_demo_sections            # add the sections, learners, teachers, classes, grades and attendance
    python manage.py seed_demo_sections --dry-run  # the same, then undo it (a check that it works)
    python manage.py seed_demo_sections --grade1   # give Grade 1 its learning areas and grades (MATATAG)
    python manage.py seed_demo_sections --fill     # every learner of every demo class gets a Term 2 grade
    python manage.py seed_demo_sections --remove   # take out everything this command added

Every new section copies the subjects of the demo section already in its grade (Grade 11 alternates Academic and
TechPro, Grade 12 HUMSS and STEM). Each gets its own adviser, who teaches its subjects (a background account that
cannot sign in). Kindergarten reports competencies, so it has learners but no grades; Grade 1 gets its MATATAG
learning areas with --grade1 (its DepEd SF9 still reports competencies). Term 1 is graded
and final; Term 2 is in progress class by class, as in the rest of the demo.

Grades are drawn around each discipline's level target (seed_demo.TERM_LEVEL_TARGETS), so the School Head sees every
level of DepEd Order No. 015, s. 2026: Emerging (HUMSS), Developing (Mathematics), Connecting (Filipino),
Benchmarking (English, Science, Araling Panlipunan, Values) and Advancing (MAPEH, TLE). The written work,
performance task and examination scores sit around each grade.

What this adds is marked so it can be removed: learners' LRNs start with 99982, the advisers' employee numbers with
DEMOT-X-. Nothing the user imported (Grade 7 Oakwood) or the rest of the demo is touched.
"""
import random
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.contrib.auth.hashers import make_password
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from academics.models import GradeLevel, Quarter, School, SchoolYear, Section
from accounts.models import UserProfile
from attendance.models import AttendanceSummary
from enrollment.models import Enrollment
from grades.models import GradeComponent
from scheduling.models import ClassAssignment
from students.models import Guardian, Student

from .seed_demo import (FIRST_F, FIRST_M, LASTS, MIDDLES, Q2_STATES, TERM_LEVEL_TARGETS, TERMS, _aware, _descriptor)

LRN_PREFIX = '99982'                 # expansion learners: 99982 and a 7-digit serial
STAFF_PREFIX = 'DEMOT-X-'
KINDER_SECTIONS, SECTIONS_PER_GRADE = 4, 20
CLASS_SIZE, KINDER_SIZE = 30, 25
BASE = 83.5                          # the ability around which every discipline's offset is set (TERM_FAMILIES)
# a grade cannot go below 60: draw Emerging a little lower so its average still lands on the target
CLAMP_SHIFT = {'HUMSS': -1.5}

# section names by grade level, each a theme of its own; the demo's own section comes first and is kept
NAMES = {
    0: ['Camia', 'Dahlia', 'Orchid'],
    1: ['Adelfa', 'Azucena', 'Bougainvillea', 'Cadena de Amor', 'Calachuchi', 'Camellia', 'Carnation', 'Cosmos', 'Daisy', 'Gardenia',
        'Hibiscus', 'Iris', 'Jasmine', 'Lily', 'Lotus', 'Marigold', 'Petunia', 'Sunflower', 'Waling-Waling'],
    2: ['Atis', 'Avocado', 'Balimbing', 'Bayabas', 'Chico', 'Dalandan', 'Duhat', 'Guyabano', 'Kaimito', 'Kalamansi', 'Langka',
        'Lanzones', 'Mabolo', 'Makopa', 'Mangga', 'Mangosteen', 'Marang', 'Rambutan', 'Santol'],
    3: ['Agila', 'Banog', 'Batala', 'Kalapati', 'Kalaw', 'Kilyawan', 'Kulasisi', 'Kuwago', 'Lawin', 'Martines', 'Maya', 'Pipit',
        'Pugo', 'Sabukot', 'Tagak', 'Tariktik', 'Tikling', 'Bato-bato', 'Uwak'],
    4: ['Aguinaldo', 'Agoncillo', 'Aquino', 'Burgos', 'Dagohoy', 'Del Pilar', 'Gomez', 'Jacinto', 'Jaena', 'Lapu-Lapu', 'Laurel',
        'Luna', 'Malvar', 'Ponce', 'Recto', 'Sakay', 'Silang', 'Tandang Sora', 'Zamora'],
    5: ['Acacia', 'Agoho', 'Akle', 'Apitong', 'Balete', 'Banaba', 'Bangkal', 'Dao', 'Ipil', 'Kalumpit', 'Kamagong', 'Lauan',
        'Mahogany', 'Malabayabas', 'Molave', 'Narra', 'Pili', 'Talisay', 'Yakal'],
    6: ['Agate', 'Amethyst', 'Aquamarine', 'Beryl', 'Citrine', 'Diamond', 'Emerald', 'Garnet', 'Jade', 'Jasper', 'Onyx', 'Opal',
        'Pearl', 'Peridot', 'Quartz', 'Ruby', 'Sapphire', 'Topaz', 'Zircon'],
    7: ['Kabaitan', 'Kababaang-loob', 'Kagandahang-loob', 'Kahusayan', 'Kalayaan', 'Karunungan', 'Kasipagan', 'Katapangan',
        'Katapatan', 'Katarungan', 'Katatagan', 'Malasakit', 'Pag-asa', 'Pagkakaisa', 'Paggalang', 'Pagmamahal', 'Pagtitiyaga',
        'Pakikiramay'],
    8: ['Apo', 'Arayat', 'Banahaw', 'Dulang-Dulang', 'Guiting-Guiting', 'Halcon', 'Hamiguitan', 'Isarog', 'Kalatungan', 'Kanlaon',
        'Kitanglad', 'Makiling', 'Malindang', 'Mantalingajan', 'Matutum', 'Mayon', 'Pinatubo', 'Pulag', 'Talomo'],
    9: ['Abra', 'Agno', 'Agusan', 'Bato', 'Bicol', 'Buhi', 'Cagayan', 'Davao', 'Lanao', 'Libuganon', 'Mainit', 'Mindanao', 'Naujan',
        'Pampanga', 'Pasig', 'Pulangi', 'Sebu', 'Taal', 'Tagum'],
    10: ['Alcala', 'Baltazar', 'Banzon', 'Campos', 'Cruz', 'Del Mundo', 'Diokno', 'Encarnacion', 'Gonzalez', 'Juliano', 'Kintanar',
         'Ocampo', 'Perez', 'Quisumbing', 'Rodriguez', 'Santos', 'Tan', 'Velasquez', 'Zara'],
}
# Senior High: the strands of the two demo sections, B onwards, until the grade has 20 sections
SHS_STRANDS = {11: ('Academic', 'TechPro'), 12: ('HUMSS', 'STEM')}
MONTHS_NOW = [(6, 17), (7, 23), (8, 21), (9, 22), (10, 20)]


class Expansion:
    def __init__(self, out):
        self.out = out
        self.rng = random.Random(20261110)
        self.school = School.objects.get(school_id=TERMS.school_id)
        self.sy = SchoolYear.objects.get(school=self.school, is_current=True)
        self.terms = list(Quarter.objects.filter(school_year=self.sy).order_by('quarter_number'))
        self.registrar = UserProfile.objects.filter(school=self.school, role='registrar').first().user
        self.levels = {g.grade_number: g for g in GradeLevel.objects.filter(school=self.school)}
        self.group = Group.objects.get_or_create(name='Teacher')[0]
        self.password = make_password(None)           # an unusable password: background teachers cannot sign in
        self.totals = {}

    # ---------------------------------------------------------------- what each grade's sections take
    def _templates(self):
        """{grade: [(section to copy, its classes)]}: the demo sections already in each grade, the user's own left out."""
        out = {}
        mine = Section.objects.filter(school=self.school, school_year=self.sy).select_related('grade_level', 'strand')
        for section in mine.order_by('grade_level__sort_order', 'section_name'):
            seeded = Enrollment.objects.filter(section=section, student__lrn__startswith=TERMS.lrn_prefix).exists()
            if not seeded:                                 # Grade 7 Oakwood: the user's SF1 import
                continue
            classes = list(ClassAssignment.objects.filter(section=section, is_active=True).select_related('subject__subject_family'))
            out.setdefault(section.grade_level.grade_number, []).append((section, classes))
        return out

    def _names(self, grade, have):
        if grade in SHS_STRANDS:
            names = []
            for letter in 'BCDEFGHIJ':
                names += [f'{strand}-{letter}' for strand in SHS_STRANDS[grade]]
            return names[:SECTIONS_PER_GRADE - have]
        return NAMES[grade][:(KINDER_SECTIONS if grade == 0 else SECTIONS_PER_GRADE) - have]

    # ---------------------------------------------------------------- people
    def _person(self, used):
        rng = self.rng
        while True:
            sex = rng.choice('MF')
            first, last = rng.choice(FIRST_M if sex == 'M' else FIRST_F), rng.choice(LASTS)
            middle = rng.choice(MIDDLES)
            if (first, last, middle) not in used:
                used.add((first, last, middle))
                return sex, first, last, middle

    def _adviser(self, n, used_names):
        rng = self.rng
        while True:
            first, last = rng.choice(FIRST_F if rng.random() < 0.7 else FIRST_M), rng.choice(LASTS)
            username = f'{first}.{last}'.lower().replace(' ', '') + '@' + TERMS.domain
            if username not in used_names:
                used_names.add(username)
                break
        return User(username=username, email=username, first_name=first, last_name=last, password=self.password, is_active=True), n

    # ---------------------------------------------------------------- grades
    def _grade(self, ability, offset, family, term):
        value = ability + offset + (TERM_LEVEL_TARGETS.get(family, BASE) - BASE) + CLAMP_SHIFT.get(family, 0.0) \
            + 0.4 * (term - 1) + self.rng.gauss(0, 2.4)
        return int(max(60, min(100, round(value))))

    def _component(self, enrollment, subject, family, term, grade, teacher, status, when):
        rng = self.rng
        ww = max(50, min(100, grade + rng.randint(-4, 4)))
        pt = max(50, min(100, grade + rng.randint(-3, 5)))
        qa = max(50, min(100, grade + rng.randint(-6, 3)))
        w_ww, w_pt, w_qa = TERMS.weights(family)
        checked = status in ('Validated', 'Finalized', 'Returned')
        locked = status == 'Finalized'
        return GradeComponent(
            enrollment=enrollment, subject=subject, quarter=term,
            written_work_raw=Decimal(ww), written_work_max=Decimal(100), written_work_percent=Decimal(ww), written_work_weighted=Decimal(ww) * w_ww,
            performance_task_raw=Decimal(pt), performance_task_max=Decimal(100), performance_task_percent=Decimal(pt),
            performance_task_weighted=Decimal(pt) * w_pt,
            quarterly_assessment_raw=Decimal(qa), quarterly_assessment_max=Decimal(100), quarterly_assessment_percent=Decimal(qa),
            quarterly_assessment_weighted=Decimal(qa) * w_qa,
            initial_grade=Decimal(grade), transmuted_grade=grade, descriptor=_descriptor(grade), encoded_by=teacher, validation_status=status,
            validated_by=self.registrar if checked else None, validation_date=when if checked else None,
            validation_notes='Some grades look far below the first term. Please re-check the class record.' if status == 'Returned' else '',
            is_locked=locked, locked_by=self.registrar if locked else None, locked_at=when if locked else None)

    # ---------------------------------------------------------------- build
    def build(self):
        rng = self.rng
        templates = self._templates()
        used_people = set(Student.objects.filter(lrn__startswith=TERMS.lrn_prefix).values_list('first_name', 'last_name', 'middle_name'))
        used_names = set(User.objects.values_list('username', flat=True))
        plan = []                                     # (grade, name, template section, its classes)
        for grade in sorted(self.levels):
            have = Section.objects.filter(school=self.school, school_year=self.sy, grade_level=self.levels[grade]).count()
            pool = templates.get(grade, [])
            for i, name in enumerate(self._names(grade, have)):
                template, classes = pool[i % len(pool)] if pool else (None, [])
                plan.append((grade, name, template, classes))

        # advisers: one a section, teaching all of its classes
        advisers = [self._adviser(n, used_names) for n in range(1, len(plan) + 1)]
        User.objects.bulk_create([u for u, _ in advisers], batch_size=200)
        made = {u.username: u for u in User.objects.filter(username__in=[u.username for u, _ in advisers])}
        teachers, profiles = [], []
        for (user, n), (grade, name, _, _) in zip(advisers, plan):       # bulk_create sends no signal: the profiles are made here
            user = made[user.username]
            teachers.append(user)
            profiles.append(UserProfile(
                user=user, role='teacher', school=self.school, employee_number=f'{STAFF_PREFIX}{n:03d}',
                designation=rng.choice(['Teacher I', 'Teacher II', 'Teacher III']), position_title='Teacher',
                teaching_area='Kindergarten' if grade == 0 else f'Grade {grade}', employment_status='Regular_Permanent',
                institutional_email=user.username, is_active=True, must_change_password=False, is_homeroom_adviser=True))
        UserProfile.objects.bulk_create(profiles, batch_size=200)
        self.group.user_set.add(*teachers)

        # sections, learners, guardians, enrollments
        sections, students, members = [], [], []
        serial = 0                                    # handle() refuses to run twice, so the serials start fresh
        for (grade, name, template, classes), teacher in zip(plan, teachers):
            size = KINDER_SIZE if grade == 0 else CLASS_SIZE
            section = Section(school=self.school, grade_level=self.levels[grade], school_year=self.sy, section_name=name,
                              strand=template.strand if template else None, adviser=teacher, max_capacity=40, is_homeroom=True,
                              current_enrollment_count=size)
            sections.append((section, grade, classes, teacher, rng.gauss(0, 1.5)))
            for _ in range(size):
                sex, first, last, middle = self._person(used_people)
                serial += 1
                student = Student(lrn=f'{LRN_PREFIX}{serial:07d}', first_name=first, last_name=last, middle_name=middle, sex=sex,
                                  birth_date=date(2026 - grade - 5, rng.randint(1, 12), rng.randint(1, 28)), birth_place='Panabo City',
                                  nationality='Filipino', mother_tongue='Cebuano', is_4ps=rng.random() < 0.18, is_active=True,
                                  is_verified=True, created_by=self.registrar)
                students.append(student)
                members.append((section, student, max(62.0, min(99.0, rng.gauss(BASE, 6.0)))))
        Section.objects.bulk_create([s for s, *_ in sections], batch_size=200)
        Student.objects.bulk_create(students, batch_size=1000)
        Guardian.objects.bulk_create([Guardian(student=s, first_name=rng.choice(FIRST_F if rng.random() < 0.7 else FIRST_M), last_name=s.last_name,
                                               relationship='Mother' if rng.random() < 0.7 else 'Father', is_primary_guardian=True)
                                      for s in students], batch_size=1000)
        enrollments = []
        for section, student, ability in members:
            enrollments.append(Enrollment(student=student, section=section, school_year=self.sy, grade_level_at_entry=section.grade_level,
                                          enrollment_date=date(2026, 5, rng.randint(18, 29)),
                                          enrollment_type='New' if section.grade_level.grade_number == 0 else 'Continuing',
                                          status='Enrolled', is_4ps=student.is_4ps, created_by=self.registrar))
        Enrollment.objects.bulk_create(enrollments, batch_size=1000)
        by_section = {}
        for (section, student, ability), enrollment in zip(members, enrollments):
            by_section.setdefault(section.pk, []).append((enrollment, ability))

        # classes: the template's subjects, taught by the section's adviser
        assignments = []
        for section, grade, classes, teacher, _ in sections:
            for c in classes:
                assignments.append(ClassAssignment(teacher=teacher, section=section, subject=c.subject, school_year=self.sy, semester=c.semester,
                                                   is_advisory=True, is_active=True, max_students=40, minutes_per_meeting=c.minutes_per_meeting,
                                                   meetings_per_week=c.meetings_per_week, created_by=self.registrar))
        ClassAssignment.objects.bulk_create(assignments, batch_size=1000)

        # grades: Term 1 final, Term 2 in progress class by class; written in batches as they are drawn
        t1, t2 = self.terms[0], self.terms[1]
        when1 = _aware(t1.date_end + timedelta(days=11))
        batch, total, index = [], 0, rng.randrange(len(Q2_STATES))

        def flush(force=False):
            nonlocal batch, total
            if batch and (force or len(batch) >= 5000):
                GradeComponent.objects.bulk_create(batch, batch_size=1000)
                total += len(batch)
                batch = []

        for section, grade, classes, teacher, offset in sections:
            people = by_section.get(section.pk, [])
            for c in classes:
                family = c.subject.subject_family.family_code if c.subject.subject_family_id else ''
                for enrollment, ability in people:
                    batch.append(self._component(enrollment, c.subject, family, t1, self._grade(ability, offset, family, 1), teacher, 'Finalized', when1))
                state = Q2_STATES[index % len(Q2_STATES)]
                index += 1
                if state != 'not_started':
                    graded = people if state != 'incomplete' else people[:max(3, int(len(people) * rng.uniform(0.45, 0.8)))]
                    status = {'validated': 'Validated', 'submitted': 'Submitted', 'ready': 'Draft', 'incomplete': 'Draft', 'returned': 'Returned'}[state]
                    when = timezone.now() - timedelta(days=rng.randint(1, 6))
                    for enrollment, ability in graded:
                        batch.append(self._component(enrollment, c.subject, family, t2, self._grade(ability, offset, family, 2), teacher, status, when))
                flush()
            self.out.write(f'  {section.grade_level.grade_name} {section.section_name}: {len(people)} learners, {len(classes)} classes')
        flush(force=True)

        # attendance: each learner's months so far
        rows = []
        for section, *_ in sections:
            for enrollment, ability in by_section.get(section.pk, []):
                careless = ability < 76
                for month, days in MONTHS_NOW:
                    absent = min(days, rng.choice([0, 0, 0, 1, 1, 2]) + (rng.randint(1, 4) if careless else 0))
                    rate = Decimal(absent * 100) / Decimal(days)
                    rows.append(AttendanceSummary(enrollment=enrollment, school_year=self.sy, month=month, total_school_days=days,
                                                  days_present=days - absent, days_absent=absent, days_late=rng.choice([0, 0, 1, 2]),
                                                  days_excused=min(absent, rng.choice([0, 0, 1])), absence_rate_percent=rate.quantize(Decimal('0.01')),
                                                  is_at_risk=rate >= 20, consecutive_absences=min(absent, 3) if careless else 0))
        AttendanceSummary.objects.bulk_create(rows, batch_size=2000)
        self.totals = {'sections': len(sections), 'advisers': len(teachers), 'learners': len(students), 'classes': len(assignments),
                       'grades': total, 'attendance months': len(rows)}
        return self.totals


GRADE1_SUBJECTS = [                       # MATATAG Grade 1: code, name, discipline (Language and Reading in Filipino)
    ('LANG1', 'Language 1', 'FIL'), ('RL1', 'Reading and Literacy 1', 'FIL'), ('MATH1', 'Mathematics 1', 'MATH'),
    ('MAK1', 'Makabansa 1', 'AP'), ('GMRC1', 'GMRC 1', 'ESP'),
]


def grade_one(out):
    """Grade 1's learning areas, a class for each in every Grade 1 section (taught by its adviser), and their grades:
    Term 1 final, Term 2 in progress, drawn around each discipline's level target like the other grades."""
    from decimal import Decimal as D

    from academics.models import Subject, SubjectFamily

    ex = Expansion(out)
    level = ex.levels[1]
    families = {f.family_code: f for f in SubjectFamily.objects.filter(school=ex.school)}
    subjects = []
    for code, name, family in GRADE1_SUBJECTS:
        subject, _ = Subject.objects.get_or_create(school=ex.school, subject_code=code, defaults={
            'subject_name': name, 'subject_category': 'Core', 'grade_level': level, 'subject_family': families[family],
            'hours_per_week': D(4)})
        subjects.append((subject, family))
    sections = list(Section.objects.filter(school=ex.school, school_year=ex.sy, grade_level=level).select_related('adviser'))
    assignments, rows = [], []
    t1, t2 = ex.terms[0], ex.terms[1]
    when1 = _aware(t1.date_end + timedelta(days=11))
    index = ex.rng.randrange(len(Q2_STATES))
    for section in sections:
        people = [(e, max(62.0, min(99.0, ex.rng.gauss(BASE, 6.0))))
                  for e in Enrollment.objects.filter(section=section, school_year=ex.sy, status__in=('Enrolled', 'Transferred_In'))]
        offset = ex.rng.gauss(0, 1.5)
        for subject, family in subjects:
            if ClassAssignment.objects.filter(section=section, subject=subject, school_year=ex.sy).exists():
                continue
            assignments.append(ClassAssignment(teacher=section.adviser, section=section, subject=subject, school_year=ex.sy,
                                               is_advisory=True, is_active=True, max_students=40, minutes_per_meeting=50,
                                               meetings_per_week=5, created_by=ex.registrar))
            for enrollment, ability in people:
                rows.append(ex._component(enrollment, subject, family, t1, ex._grade(ability, offset, family, 1), section.adviser, 'Finalized', when1))
            state = Q2_STATES[index % len(Q2_STATES)]
            index += 1
            if state == 'not_started':
                continue
            graded = people if state != 'incomplete' else people[:max(3, int(len(people) * ex.rng.uniform(0.45, 0.8)))]
            status = {'validated': 'Validated', 'submitted': 'Submitted', 'ready': 'Draft', 'incomplete': 'Draft', 'returned': 'Returned'}[state]
            when = timezone.now() - timedelta(days=ex.rng.randint(1, 6))
            for enrollment, ability in graded:
                rows.append(ex._component(enrollment, subject, family, t2, ex._grade(ability, offset, family, 2), section.adviser, status, when))
    ClassAssignment.objects.bulk_create(assignments, batch_size=500)
    GradeComponent.objects.bulk_create(rows, batch_size=1000)
    totals = {'Grade 1 sections': len(sections), 'subjects': len(subjects), 'classes': len(assignments), 'grades': len(rows)}
    for k, v in totals.items():
        out.write(f'  {k}: {v:,}')
    return totals


def fill_term2(out):
    """Give every learner of every demo class a Term 2 grade: the classes not started and the ones half encoded get the
    missing grades as drafts (so they are ready to submit), each a little above the learner's Term 1 grade in that
    subject. Sections holding learners the user imported (Grade 7 Oakwood) are left as they are."""
    ex = Expansion(out)
    t1, t2 = ex.terms[0], ex.terms[1]
    imported = set(Enrollment.objects.filter(section__school=ex.school, school_year=ex.sy)
                   .exclude(student__lrn__startswith=TERMS.lrn_prefix).values_list('section_id', flat=True))
    classes = list(ClassAssignment.objects.filter(section__school=ex.school, school_year=ex.sy, is_active=True)
                   .exclude(section_id__in=imported).select_related('subject__subject_family', 'teacher'))
    sections = {c.section_id for c in classes}
    active = {}
    for e in Enrollment.objects.filter(section_id__in=sections, school_year=ex.sy, status__in=('Enrolled', 'Transferred_In')):
        active.setdefault(e.section_id, []).append(e)
    have = set(GradeComponent.objects.filter(quarter=t2, enrollment__section_id__in=sections).values_list('enrollment_id', 'subject_id'))
    first = {(e, s): g for e, s, g in GradeComponent.objects.filter(quarter=t1, enrollment__section_id__in=sections)
             .values_list('enrollment_id', 'subject_id', 'transmuted_grade') if g is not None}
    rows, filled = [], set()
    for c in classes:
        family = c.subject.subject_family.family_code if c.subject.subject_family_id else ''
        for e in active.get(c.section_id, []):
            if (e.id, c.subject_id) in have:
                continue
            base = first.get((e.id, c.subject_id))
            if base is None:                              # no Term 1 grade either: draw one around the discipline's target
                base = ex._grade(max(62.0, min(99.0, ex.rng.gauss(BASE, 6.0))), 0.0, family, 1)
            grade = int(max(60, min(100, round(base + 0.4 + ex.rng.gauss(0, 2.0)))))
            rows.append(ex._component(e, c.subject, family, t2, grade, c.teacher, 'Draft', None))
            filled.add(c.id)
    GradeComponent.objects.bulk_create(rows, batch_size=1000)

    # Term 1 too: the one-term electives of the first demo sections get their other term (the SF9 still places each
    # elective in one term, the first its grades are in: documentation/sf9.place_electives)
    have1 = set(first)
    second = {(e, s): g for e, s, g in GradeComponent.objects.filter(quarter=t2, enrollment__section_id__in=sections)
              .values_list('enrollment_id', 'subject_id', 'transmuted_grade') if g is not None}
    when1 = _aware(t1.date_end + timedelta(days=11))
    term1 = []
    for c in classes:
        family = c.subject.subject_family.family_code if c.subject.subject_family_id else ''
        for e in active.get(c.section_id, []):
            if (e.id, c.subject_id) in have1:
                continue
            base = second.get((e.id, c.subject_id)) or ex._grade(max(62.0, min(99.0, ex.rng.gauss(BASE, 6.0))), 0.0, family, 2)
            grade = int(max(60, min(100, round(base - 0.4 + ex.rng.gauss(0, 2.0)))))
            term1.append(ex._component(e, c.subject, family, t1, grade, c.teacher, 'Finalized', when1))
            filled.add(c.id)
    GradeComponent.objects.bulk_create(term1, batch_size=1000)
    totals = {'classes completed': len(filled), 'Term 2 grades added': len(rows), 'Term 1 grades added': len(term1),
              'sections left as imported': len(imported)}
    for k, v in totals.items():
        out.write(f'  {k}: {v:,}')
    return totals


def remove_grade_one(out, school):
    from academics.models import Subject
    subjects = Subject.objects.filter(school=school, subject_code__in=[c for c, _, _ in GRADE1_SUBJECTS])
    counts = {'Grade 1 grades': GradeComponent.objects.filter(subject__in=subjects).delete()[0],
              'Grade 1 classes': ClassAssignment.objects.filter(subject__in=subjects).delete()[0],
              'Grade 1 subjects': subjects.delete()[0]}
    for k, v in counts.items():
        out.write(f'  removed {k}: {v}')


def remove(out):
    """Everything seed_demo_sections added: its learners (and their enrollments, grades, attendance), sections and advisers."""
    school = School.objects.get(school_id=TERMS.school_id)
    students = Student.objects.filter(lrn__startswith=LRN_PREFIX)
    enrollments = Enrollment.objects.filter(student__in=students)
    sections = Section.objects.filter(school=school, id__in=enrollments.values('section_id')) \
        .exclude(id__in=Enrollment.objects.exclude(student__lrn__startswith=LRN_PREFIX).values('section_id'))
    advisers = User.objects.filter(profile__employee_number__startswith=STAFF_PREFIX, profile__school=school)
    remove_grade_one(out, school)
    counts = {}
    counts['grades'] = GradeComponent.objects.filter(enrollment__in=enrollments).delete()[0]
    counts['attendance'] = AttendanceSummary.objects.filter(enrollment__in=enrollments).delete()[0]
    counts['classes'] = ClassAssignment.objects.filter(section__in=sections).delete()[0]
    counts['enrollments'] = enrollments.delete()[0]
    counts['learners'] = students.delete()[0]
    counts['sections'] = sections.delete()[0]
    counts['advisers'] = advisers.delete()[0]
    for k, v in counts.items():
        out.write(f'  removed {k}: {v}')
    return counts


class Command(BaseCommand):
    help = 'Grow the three-term demo school to 4 Kindergarten sections and 20 sections in each of Grades 1 to 12.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Add everything, report, then undo it.')
        parser.add_argument('--remove', action='store_true', help='Remove everything this command added.')
        parser.add_argument('--grade1', action='store_true', help="Add Grade 1's learning areas, classes and grades to every Grade 1 section.")
        parser.add_argument('--fill', action='store_true', help='Give every learner of every demo class a Term 2 grade.')

    def handle(self, *args, **options):
        class Undo(Exception):
            pass

        try:
            with transaction.atomic():
                if options['remove']:
                    remove(self.stdout)
                elif options['grade1']:
                    grade_one(self.stdout)
                elif options['fill']:
                    fill_term2(self.stdout)
                else:
                    if Student.objects.filter(lrn__startswith=LRN_PREFIX).exists():
                        self.stdout.write(self.style.WARNING('The demo school is already grown; run with --remove first.'))
                        return
                    totals = Expansion(self.stdout).build()
                    for k, v in totals.items():
                        self.stdout.write(f'  {k}: {v:,}')
                if options['dry_run']:
                    raise Undo
        except Undo:
            self.stdout.write(self.style.WARNING('Dry run: nothing was saved.'))
            return
        from core.caching import bump_data_version
        bump_data_version()
        self.stdout.write(self.style.SUCCESS('Done. Retrain the grade forecaster: python manage.py train_ml_models'))
