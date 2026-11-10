"""What to do for learners below the passing mark in one subject, in the words of that subject.

A learner below 75 needs remediation under both DepEd orders (core/grading_policy.py). What helps depends on the
learning area and on the grade component the learners lose the most points in, so each learning area has its own
activity for written work, performance tasks and the quarterly assessment (or examinations). The learning area comes
from the subject's discipline (academics.subject_catalog), with a few subject names that need their own words
(research, writing). Poro AI gets the same activity as its starting point and writes its own advice (heads/poro.py).
"""
from core import grading_policy as policy

# learning area -> grade component -> what to give the learners below 75 (one phrase, no numbers)
ACTIVITIES = {
    'math': {
        'ww': 'short daily drills on the skills behind the items they missed, with a worked example before each set',
        'pt': 'problem-solving tasks done one step at a time, with a worked model and the rubric shown before they start',
        'qa': 'a reteach of the least-mastered competencies, then practice problems in the format of the test',
    },
    'science': {
        'ww': 'short concept checks and labelled-diagram exercises with quick feedback',
        'pt': 'guided experiments with a step-by-step activity sheet and the rubric shared at the start',
        'qa': 'a review of the least-mastered concepts, then practice items that ask them to explain why',
    },
    'english': {
        'ww': 'short reading and grammar drills with feedback right away',
        'pt': 'speaking and writing tasks with a model answer and a checklist they can follow',
        'qa': 'reading-comprehension practice in the format of the test, after a review of the least-mastered skills',
    },
    'writing': {
        'ww': 'short sentence and paragraph exercises with feedback right away',
        'pt': 'their piece written in stages (plan, draft, revise), with a model text and a checklist',
        'qa': 'a review of the elements and techniques they missed, then practice items in the format of the test',
    },
    'filipino': {
        'ww': 'short reading and grammar drills in Filipino with feedback right away',
        'pt': 'speaking and writing tasks in Filipino with a model output and a checklist',
        'qa': 'practice with reading passages in Filipino in the format of the test, after a review of the least-mastered skills',
    },
    'social': {
        'ww': 'short quizzes built on timelines, maps and key terms',
        'pt': 'tasks such as timelines, maps or role plays, with the rubric shared at the start',
        'qa': 'a review of the least-mastered topics with graphic organizers, then practice items in the format of the test',
    },
    'mapeh': {
        'ww': 'short quizzes on the key concepts, taught again with pictures and demonstrations',
        'pt': 'practice of the performance or artwork in small groups, with a checklist of what is assessed',
        'qa': 'a review of the least-mastered concepts, then practice items in the format of the test',
    },
    'tle': {
        'ww': 'short quizzes on tools, materials and safety, with the real items shown',
        'pt': 'supervised hands-on practice of each procedure, step by step, with a skills checklist',
        'qa': 'a review of the procedures and terms they missed, then practice items in the format of the test',
    },
    'values': {
        'ww': 'reflection journals and short case discussions with feedback',
        'pt': 'a values-in-action task with clear steps and the rubric shared at the start',
        'qa': 'a review of the key concepts through real-life situations, then practice items in the format of the test',
    },
    'research': {
        'ww': 'short exercises on one research skill at a time, such as citing sources or writing the problem statement',
        'pt': 'their output broken into checkpoints, with feedback at each one',
        'qa': 'a review of research terms and steps, then practice items in the format of the test',
    },
}

FAMILIES = {
    'mathematics': 'math', 'science': 'science', 'english': 'english', 'filipino': 'filipino',
    'araling panlipunan': 'social', 'humanities and social sciences': 'social', 'mapeh': 'mapeh',
    'technology and livelihood education': 'tle', 'edukasyon sa pagpapakatao': 'values',
}
# subject names that need their own words, checked before the discipline (the first match counts)
NAMES = (
    ('research', 'research'), ('inquiries', 'research'), ('immersion', 'research'), ('capstone', 'research'),
    ('writing', 'writing'), ('literature', 'english'), ('komunikasyon', 'filipino'), ('pagbasa', 'filipino'),
    ('math', 'math'), ('calculus', 'math'), ('statistic', 'math'), ('science', 'science'), ('biology', 'science'),
    ('chemistry', 'science'), ('physics', 'science'), ('english', 'english'), ('communication', 'english'),
    ('filipino', 'filipino'), ('araling', 'social'), ('history', 'social'), ('kasaysayan', 'social'), ('social', 'social'),
    ('politic', 'social'), ('culture', 'social'), ('mapeh', 'mapeh'), ('music', 'mapeh'), ('arts', 'mapeh'),
    ('physical education', 'mapeh'), ('health', 'mapeh'), ('technology', 'tle'), ('livelihood', 'tle'), ('nc ii', 'tle'),
    ('computer', 'tle'), ('cookery', 'tle'), ('edukasyon', 'values'), ('values', 'values'), ('gmrc', 'values'),
    ('personal development', 'values'), ('life and career', 'values'),
)


def area(subject):
    """The learning area a subject's activities come from, or '' when none fits."""
    name = (subject.subject_name or '').lower()
    for word, key in NAMES[:8]:                       # research and writing first: they sit under other disciplines
        if word in name:
            return key
    family = subject.subject_family.family_name.lower() if getattr(subject, 'subject_family_id', None) else ''
    if family in FAMILIES:
        return FAMILIES[family]
    return next((key for word, key in NAMES if word in name), '')


def activity(subject, component):
    """What to give the learners below 75 in this subject, for the grade component they are weakest in."""
    from heads.insights import component_tip
    return ACTIVITIES.get(area(subject), {}).get(component) or component_tip(component)


def component_name(key):
    """How a sentence names a grade component in the school's DepEd order ("written work", "the examinations")."""
    from heads.insights import components
    return dict(components()).get(key, '')


def learner_advice(subject, component, *, urgent):
    """The computed recommendation for one learner below 75 in one subject: two sentences, no numbers, no name."""
    when = 'starting this week' if urgent else 'twice a week'
    first = (f'Give this learner remediation in {subject.subject_name} {when}, in a small group or one on one, with a short '
             'check each week.')
    if not component:
        return first
    return f'{first} Start with {component_name(component)}: {activity(subject, component)}.'
