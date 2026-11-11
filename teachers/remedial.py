"""What to do for a learner in one subject, at every performance level, in the words of that subject.

A learner below 75 needs remediation under both DepEd orders (core/grading_policy.py). What helps depends on the
learning area and on the grade component the learners lose the most points in, so each learning area has its own
activity for written work, performance tasks and the quarterly assessment (or examinations). The learning area comes
from the subject's discipline (academics.subject_catalog), with a few subject names that need their own words
(research, writing). Poro AI gets the same activity as its starting point and writes its own advice (heads/poro.py).

Learners who pass get a recommendation too (learner_plan): the lowest passing level keeps them safely above the mark,
the levels above it move them up through their weakest component, and the top level gets enrichment in that learning
area (ENRICHMENT), so every learner has a next step for academic enhancement.
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


# learning area -> what stretches a learner who already does very well in it (one phrase, no numbers)
ENRICHMENT = {
    'math': 'challenge problems that need more than one strategy, and a math contest or quiz bee',
    'science': 'an investigatory project or a science fair entry built on a question of their own',
    'english': 'wider reading of their own choice, and the school paper, a speech or a debate',
    'writing': 'a piece for the school paper or a writing contest, revised with feedback',
    'filipino': 'Filipino journalism, declamation or a balagtasan, and reading of their own choice in Filipino',
    'social': 'a small research task on local history or current events, shared with the class',
    'mapeh': 'leading a group performance, an exhibit or a sports clinic for classmates',
    'tle': 'a more advanced project that builds toward a trade skill, done with less supervision',
    'values': 'leading a service project or a class activity on a value they practise',
    'research': 'presenting their work in a school research forum and helping classmates with their own',
}


def stage(level):
    """Where a learner's grade puts them, for what to do next: 'remediate' (below the passing mark), 'secure' (the lowest
    passing level), 'level_up' (any other passing level but the top) or 'enrich' (the top level). The levels and the
    passing mark come from the school's DepEd order, so the same grade can fall in a different stage under each."""
    levels = policy.levels()                          # highest first
    if not level['passing']:
        return 'remediate'
    if level['key'] == levels[0]['key']:
        return 'enrich'
    below = levels[[lv['key'] for lv in levels].index(level['key']) + 1] if level['key'] != levels[-1]['key'] else None
    return 'secure' if below is None or not below['passing'] else 'level_up'


def enrichment(subject):
    return ENRICHMENT.get(area(subject)) or 'harder tasks of their own choice, and the chance to help classmates'


def learner_plan(subject, level, component, *, urgent):
    """The computed recommendation for one learner in one subject at any level: two sentences, no numbers, no name.
    Below the passing mark it is remediation; at the top level, enrichment; in between, practice aimed at the weakest
    grade component so the learner moves up."""
    kind = stage(level)
    if kind == 'remediate':
        return learner_advice(subject, component, urgent=urgent)
    name = subject.subject_name
    if kind == 'enrich':
        first = f'Keep challenging this learner in {name} with enrichment: {enrichment(subject)}.'
        return f'{first} Invite them to help classmates as a peer tutor.'
    if kind == 'secure':
        first = (f'Keep this learner safely above the passing mark in {name} with targeted practice once a week and a quick '
                 'check after each task.')
    else:
        first = f'Help this learner move up a level in {name} with extra practice on their weakest part and feedback after each task.'
    if not component:
        return first
    return f'{first} Start with {component_name(component)}: {activity(subject, component)}.'


def plan_facts(subject, level, component, *, urgent):
    """What Poro is told about one learner in one subject, for any level: no name, no grade, only where they stand, the
    decided step and what helps (heads/poro.py writes its own advice from it)."""
    from heads.insights import _wordy
    kind = stage(level)
    wordy = _wordy(subject.subject_name)
    tip = enrichment(subject) if kind == 'enrich' else (activity(subject, component) if component else '')
    steps = {
        'remediate': ('reach the passing mark', 'remediation in a small group or one on one'
                      + (' starting this week' if urgent else ' twice a week') + ', with a short check each week'),
        'secure': ('stay safely above the passing mark and move up a level',
                   'targeted practice once a week on the weakest part, with a quick check after each task'),
        'level_up': ('move up a level', 'extra practice on the weakest part, with feedback after each task'),
        'enrich': ('keep excelling and go further than the lessons', 'enrichment and the chance to tutor classmates'),
    }
    goal, step = steps[kind]
    return {'about': f'one learner {"below the passing mark " if kind == "remediate" else ""}in {wordy}', 'names': [subject.subject_name],
            'level': level['name'], 'target': '', 'goal': goal, 'kind': kind, 'step': step,
            'weak_key': component or '', 'weak': component_name(component) if component else '',
            'tip': tip, 'order': policy.current()['name'], 'reader': 'teacher'}
