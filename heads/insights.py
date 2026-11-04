"""Analysis behind the School Head's Academic Dashboard and School Performance.

Everything a page states is computed here from the grades, with rules a person can check:

- Levels and remediation follow the DepEd order the school chose (core/grading_policy.py): the
  descriptors of DepEd Order No. 8, s. 2015 (Outstanding to Did Not Meet Expectations) or of DepEd
  Order No. 015, s. 2026 (Advancing to Emerging). Below 75 a learner needs remediation under either.
- Decisions: one for each level that has disciplines (or subjects, or sections) on it. Keep the top level
  there with contests and a LAC session, move each passing level up one, bring a failing level up with
  remediation, and act first on the lowest. Each decision aims at the grade component the item is weakest in,
  in two sentences at most and with no numbers, since the page shows them.
- Projections: damped trend (Gardner & McKenzie) over this school year's quarters. The likely range
  comes from how much section averages in this school usually move from one quarter to the next.

Poro AI (heads/poro.py) writes its own advice for each decision from the same facts; its text is shown only when it
passes the checks there (no numbers, lists, other levels or other disciplines, nothing about results or causes).
"""
import math
import re
from collections import defaultdict
from statistics import pstdev

from django.core.cache import caches
from django.db.models import Avg, Count

from core import grading_policy as policy
from core.caching import data_version
from core.grading_policy import PASSING, level_of  # noqa: F401 (the pages import level_of from here)

PHI_SECTION = 0.8          # damping of a class trend per quarter
Z90 = 1.645                # 90% range

# The five levels come from the school's DepEd order (core/grading_policy.py). Their slot keys, lowest
# 'beginning' to highest 'advanced', are the same under every order, so the steps up are written once.
NEXT_LEVEL = {'beginning': 'developing', 'developing': 'approaching', 'approaching': 'proficient', 'proficient': 'advanced'}


def _next_level(lv):
    return policy.level_by_key().get(NEXT_LEVEL.get(lv['key'], ''))


def _clamp(value, low=50.0, high=100.0):
    return max(low, min(high, value))


# --------------------------------------------------------------------------------------------
# Projections
# --------------------------------------------------------------------------------------------

def slope(points):
    """Least-squares slope of (x, y) points; 0 with fewer than two points."""
    n = len(points)
    if n < 2:
        return 0.0
    mx = sum(x for x, _ in points) / n
    my = sum(y for _, y in points) / n
    sxx = sum((x - mx) ** 2 for x, _ in points)
    return sum((x - mx) * (y - my) for x, y in points) / sxx if sxx else 0.0


def project_series(points, last_period, step_sd, phi=PHI_SECTION):
    """Project a class average for the periods after the last one with data.

    points: [(period_number, average)]. Starts from the latest average and adds a damped share of
    the trend, so a short run of quarters cannot be stretched into a runaway line. The range widens
    with each step ahead and is wider when fewer quarters are known.
    """
    if not points:
        return []
    points = sorted(points)
    x_last, y_last = points[-1]
    b = slope(points)
    widen = 1.0 if len(points) >= 3 else (1.25 if len(points) == 2 else 1.6)
    out, damp = [], 0.0
    for h in range(1, last_period - x_last + 1):
        damp += phi ** h
        value = _clamp(y_last + b * damp)
        half = Z90 * step_sd * math.sqrt(h) * widen
        out.append({'period': x_last + h, 'value': round(value, 1),
                    'low': round(_clamp(value - half), 1), 'high': round(_clamp(value + half), 1)})
    return out


def typical_step_sd(school_id):
    """How much a section's average in one subject usually moves from one quarter to the next (SD).

    Measured on this school's own records (all school years), cached until data changes.
    """
    from core.periods import GRADE, HAS_GRADE
    from grades.models import GradeComponent

    key = f'insights:step-sd:{school_id}:{data_version()}'
    cache = caches['default']
    cached = cache.get(key)
    if cached is not None:
        return cached
    rows = (GradeComponent.objects
            .filter(HAS_GRADE, enrollment__section__school_id=school_id)
            .values('enrollment__section_id', 'subject_id', 'quarter__school_year_id', 'quarter__quarter_number')
            .annotate(avg=Avg(GRADE), n=Count('id')))
    series = defaultdict(dict)
    for r in rows:
        if r['n'] >= 5:
            series[(r['enrollment__section_id'], r['subject_id'], r['quarter__school_year_id'])][r['quarter__quarter_number']] = float(r['avg'])
    changes = []
    for s in series.values():
        nums = sorted(s)
        changes += [s[b] - s[a] for a, b in zip(nums, nums[1:]) if b == a + 1]
    sd = pstdev(changes) if len(changes) >= 10 else 2.5
    sd = round(min(max(sd, 1.0), 6.0), 2)
    cache.set(key, sd, 6 * 3600)
    return sd


# --------------------------------------------------------------------------------------------
# The School Head's decisions: one for each level that has items on it
# --------------------------------------------------------------------------------------------

def components():
    """(key, how a sentence names it) for the three grade components of the school's DepEd order."""
    names = {key: name.lower() for key, name, _ in policy.components()}
    return (('ww', names['ww']), ('pt', names['pt']), ('qa', 'the ' + names['qa']))


def component_tip(key):
    """What lifts one grade component."""
    return {'ww': 'short quizzes, seatwork and reading drills with quick feedback',
            'pt': 'tasks broken into steps, with the rubric shared at the start',
            'qa': 'a review of the least-mastered competencies and practice in the test format'}[key]


def weakest(parts, overall=None):
    """The grade component an item is weakest in: its lowest average score, the one the page shows marked.
    parts: {'ww', 'pt', 'qa'} average percents, None where a component has no scores."""
    keys = [k for k in ('ww', 'pt', 'qa') if parts.get(k) is not None]
    return min(keys, key=lambda k: parts[k]) if keys else None


# what the strongest learners of a discipline or subject can be put forward for, by a word in its name (the first
# match counts, so "Social Sciences" is found before "science")
CONTESTS = (
    ('humanities', 'debates and essay contests'), ('social', 'debates and essay contests'),
    ('philosoph', 'debates and essay contests'), ('politic', 'debates and essay contests'),
    ('math', 'math challenges and quiz bees'), ('statistic', 'math challenges and quiz bees'),
    ('calculus', 'math challenges and quiz bees'), ('science', 'science fairs and quiz bees'),
    ('biology', 'science fairs and quiz bees'), ('chemistry', 'science fairs and quiz bees'),
    ('physics', 'science fairs and quiz bees'), ('research', 'research congresses'),
    ('english', 'spelling bees and oratorical contests'), ('oral comm', 'oratorical contests and debates'),
    ('reading', 'spelling bees and essay contests'), ('writing', 'essay and creative writing contests'),
    ('literature', 'literary and oratorical contests'), ('media', 'campus journalism contests'),
    ('filipino', 'declamation and sabayang pagbigkas contests'), ('komunikasyon', 'declamation and sabayang pagbigkas contests'),
    ('pagbasa', 'declamation and sabayang pagbigkas contests'), ('araling', 'quiz bees and history contests'),
    ('history', 'quiz bees and history contests'), ('mapeh', 'sports meets and arts festivals'),
    ('physical', 'sports meets'), ('music', 'music festivals'), ('arts', 'arts festivals'),
    ('technolog', 'skills contests'), ('livelihood', 'skills contests'),
    ('edukasyon', 'leadership and service awards'), ('personal', 'leadership and service awards'),
)


def _contests(name):
    lowered = name.lower()
    return next((what for word, what in CONTESTS if word in lowered), 'contests and quiz bees')


def _and(words):
    words = list(words)
    return words[0] if len(words) == 1 else ', '.join(words[:-1]) + ' and ' + words[-1]


# --------------------------------------------------------------------------------------------
# The recommendations are short and direct, with no numbers: the page beside them already shows every average, so a
# recommendation says only what to do (the adviser's review, 2026-11-04). Each is at most two sentences, the step that
# fits the level and the grade component to aim at; Poro AI (heads/poro.py) writes its own advice from the same facts.
# --------------------------------------------------------------------------------------------

# what each step asks for, in a few words (Poro is told the same)
STEP_WORDS = {
    'keep': 'contests for the strongest learners, and a LAC session where the teachers share what works',
    'remediate-now': 'remediation classes this week and a class observation',
    'remediate': 'remediation classes twice a week',
    'small-groups': 'targeted practice in small groups',
    'practice': 'targeted practice with a short check every other week',
    'enrich': 'enrichment: harder problems, projects and independent work',
}


def _when(next_period_name):
    """'by the end of the next term' (no period number: the page names the periods)."""
    noun = (next_period_name or '').split(' ')[0].lower()
    return f'by the end of the next {noun}' if noun else 'by the next grading period'


def _step(lv, *, who, teachers, contests, first, when):
    """(kind, title, sentence) of the step that fits a level. who: "these disciplines", "this section", "the school"."""
    order = policy.levels()                                         # highest first
    top, lowest_passing = order[0], [x for x in order if x['passing']][-1]
    target = _next_level(lv)
    act = 'Act first on' if first else 'Act on'
    if lv['key'] == top['key']:
        return ('keep', f'Stay at {lv["name"]}: contests and a LAC session',
                f'Keep {who} at {lv["name"]}: put the strongest learners forward for {contests}, and let {teachers} share what '
                'works at the next LAC session.')
    if not lv['passing'] and lv['key'] == order[-1]['key']:
        return ('remediate-now', f'Up to {target["name"]}: remediation this week',
                f'{act} {who}: start remediation classes this week and observe a class, to lift learners to {target["name"]} {when}.')
    if not lv['passing']:
        return ('remediate', 'Up to passing: remediation twice a week',
                f'{act} {who}: hold remediation classes twice a week to bring learners up to the passing mark {when}.')
    if lv['key'] == lowest_passing['key']:
        return ('small-groups', f'Up to {target["name"]}: practice in small groups',
                f'Lift {who} to {target["name"]} with targeted practice in small groups, before learners slip below the passing mark.')
    if target['key'] == top['key']:
        return ('enrich', f'Up to {target["name"]}: enrichment',
                f'Push {who} up to {target["name"]} with enrichment: harder problems, projects and independent work.')
    return ('practice', f'Up to {target["name"]}: targeted practice',
            f'Move {who} up to {target["name"]} with targeted practice and a short check every other week.')


def _focus(key):
    """The sentence that aims a recommendation at one grade component, with what lifts it; '' without one."""
    if not key:
        return ''
    return f'Aim first at {dict(components())[key]}, with {component_tip(key)}.'


def _shared_weak(group):
    """The grade component most of a group's items are weakest in (the lowest one when they tie), or None."""
    keys = [i['weak'] for i in group if i.get('weak')]
    return max(('ww', 'pt', 'qa'), key=lambda k: (keys.count(k), -('ww', 'pt', 'qa').index(k))) if keys else None


def school_decisions(items, *, next_period_name, item_word='discipline', topic=''):
    """What the School Head should decide for each level that has items on it, the lowest level first (act there first).

    items: [{'name', 'average', 'level', 'weak' (the grade component key it is weakest in, or None)}]: the disciplines,
    the subjects of one discipline, or the sections of one subject (item_word says which; topic is then the subject, for
    its contests). Each decision is the step that fits the level and the grade component to aim at, in two sentences
    at most and without numbers. Returns [{'level', 'target', 'kind', 'items', 'title', 'text', 'step', 'focus',
    'focus_key', 'urgent', 'item_word', 'topic'}].
    """
    by = defaultdict(list)
    for item in items:
        by[item['level']['key']].append(item)
    out = []
    for lv in reversed(policy.levels()):                            # lowest first
        group = sorted(by.get(lv['key'], []), key=lambda i: i['average'])
        if not group:
            continue
        one = len(group) == 1
        who = f'this {item_word}' if one else f'these {item_word}s'
        teachers = 'its teacher' if item_word == 'section' and one else 'their teachers'
        contests = _contests(topic or group[0]['name']) if topic or one else 'contests in their fields'
        kind, title, step = _step(lv, who=who, teachers=teachers, contests=contests, first=not out, when=_when(next_period_name))
        weak = _shared_weak(group) if kind != 'keep' else None
        focus = _focus(weak)
        out.append({'level': lv, 'target': _next_level(lv) if kind != 'keep' else None, 'kind': kind, 'items': group,
                    'title': title, 'urgent': not lv['passing'], 'step': step, 'focus': focus, 'focus_key': weak,
                    'text': ' '.join(s for s in (step, focus) if s), 'item_word': item_word, 'topic': topic})
    return out


def part_scores(parts, weak=None):
    """The three grade component averages of an item or a whole selection, for the report: [{'key', 'abbr', 'name',
    'value', 'weak'}] in the DepEd order's own words (WW, PT, and QA or EX)."""
    return [{'key': key, 'abbr': abbr, 'name': name, 'value': parts.get(key), 'weak': key == weak}
            for key, name, abbr in policy.components()]


def main_decision(decisions, *, scope, average, overall, next_period_name):
    """The first recommendation on the card: the one for the selection's own level (its status), aimed at the grade
    component the whole selection scores lowest in. Returns (main, the other decisions); main is None without an average.
    scope: how a sentence names the selection ("The school", "Mathematics in Grade 7")."""
    if average is None:
        return None, decisions
    level = policy.level_of(round(average, 1))
    own = next((d for d in decisions if d['level']['key'] == level['key']), None)
    others = [d for d in decisions if d is not own]
    known = [(k, overall[k]) for k in ('ww', 'pt', 'qa') if overall.get(k) is not None]
    weak = min(known, key=lambda kv: kv[1])[0] if len(known) == 3 else None
    if own:
        kind, title, step = own['kind'], own['title'], own['step']
    else:                                                           # no item sits on the selection's own level
        who = 'the school' if scope == 'The school' else scope
        kind, title, step = _step(level, who=who, teachers='its teachers', contests='contests and quiz bees', first=True,
                                  when=_when(next_period_name))
    focus = _focus(weak)
    main = {'level': level, 'target': _next_level(level) if kind != 'keep' else None, 'kind': kind, 'title': title,
            'step': step, 'focus': focus, 'focus_key': weak, 'text': ' '.join(s for s in (step, focus) if s),
            'items': own['items'] if own else [], 'urgent': not level['passing'], 'parts': part_scores(overall, weak),
            'scope': scope, 'item_word': own['item_word'] if own else '', 'topic': own['topic'] if own else ''}
    return main, others


def _wordy(text):
    """A name without its grade numbers ("Mathematics 9" is "Mathematics"), for Poro's facts: advice has no numbers."""
    return ' '.join(re.sub(r'\b(?:Grades?\s+)?\d+\b', ' ', text).split())


def poro_facts(decision, *, scope='', discipline=''):
    """What Poro AI is told about one recommendation (heads/poro.py): who it is for, where they stand, the goal, the
    decided step and the weakest grade component, all in words. discipline: the learning area the page is about, if one."""
    names = [i['name'] for i in decision['items']]
    word = decision.get('item_word') or 'discipline'
    numbered = any(re.search(r'\d', n) for n in names)
    if scope:
        about = 'the whole school, every discipline' if scope == 'The school' else _wordy(scope)
    elif decision.get('topic'):
        about = f'{_wordy(decision["topic"])} classes' + ('' if numbered else f' in sections {_and(names)}')
    elif numbered:                      # "Mathematics 9, Mathematics 6": the discipline says it without numbers
        about = f'some {_wordy(discipline) + " " if discipline else ""}{word}s'
    else:
        about = f'the {word}{"s" if len(names) != 1 else ""} {_and(names)}'
    lv, target, kind = decision['level'], decision.get('target'), decision['kind']
    goal = (f'stay at {lv["name"]}' if kind == 'keep' else 'reach the passing mark' if kind == 'remediate'
            else f'reach {target["name"]}' if target else f'stay at {lv["name"]}')
    key = decision.get('focus_key')
    return {'about': about, 'names': names + ([decision['topic']] if decision.get('topic') else []), 'level': lv['name'],
            'target': target['name'] if target else '', 'goal': goal, 'kind': kind, 'step': STEP_WORDS[kind],
            'weak_key': key or '', 'weak': dict(components()).get(key, ''), 'tip': component_tip(key) if key else '',
            'order': policy.current()['name']}
