"""Teacher: the classes of an advisory section, and the old My Advisory address.

The section's learners, their grades and each learner's record are on the teacher's home page now (teachers/views.py,
teachers/learners.py).
"""
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.http import urlencode

from scheduling.models import ClassAssignment


def section_assignments(section, sy):
    return list(ClassAssignment.objects.filter(section=section, school_year=sy, is_active=True)
                .select_related('section', 'section__grade_level', 'subject', 'teacher', 'semester', 'school_year')
                .order_by('subject__subject_code'))


@login_required
def advisory(request):
    """My Advisory is the teacher's home page now; old links keep their section and period."""
    query = {k: request.GET[k] for k in ('section', 'period') if request.GET.get(k)}
    return redirect(reverse('teachers:dashboard') + ('?' + urlencode(dict(query, view='advisory')) if query else ''))
