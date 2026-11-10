"""Where a teacher's learners stand in one grading period, what to do next, and the forecast.

The teacher's home page reads the grades the way the School Head's Academic Dashboard does (heads/insights.py,
heads/forecasts.py), for a smaller scope:

- My advisory: the adviser's section, every subject in it. The items are the section's subjects; their teachers act,
  so the advice is what the adviser asks of them and of the parents.
- My classes: the teacher's own classes. The items are the classes, and the teacher acts.

One status for the scope (its level in the school's DepEd order, core/grading_policy.py), where each item stands on the
five levels, one recommendation for each level that has items (the scope's own level first), and the averages of the
school year with what comes next. Recommendations have no numbers, since the status beside them shows every one; Poro AI
writes its own advice from the same facts when it runs (heads/poro.py) and the computed wording shows otherwise.
"""
from collections import defaultdict
from statistics import fmean as mean          # fast float mean: these are averages of thousands of grades

from django.db.models import F

from core import grading_policy as policy
from core import periods as P
from core.grading_policy import PASSING, level_of
from grades.models import GradeComponent
from heads import forecasts, insights, poro

from . import records as R
from . import remedial

URGENT = 70         # a learner this far below the passing mark gets remediation starting this week, not just twice a week

# what each step asks for, in a few words, for Poro's facts (the School Head's are in heads/insights.STEP_WORDS)
STEP_WORDS = {
    'adviser': {
        'keep': 'contests for the strongest learners, and asking the subject teachers to share what works',
        'remediate-now': 'remediation classes this week, and a talk with the parents of learners below the passing mark',
        'remediate': 'remediation classes twice a week, and follow-up with the learners below the passing mark',
        'small-groups': 'practice in small groups, with classmates who do well helping those who struggle',
        'practice': 'targeted practice with a short check every other week',
        'enrich': 'enrichment: harder problems, projects and independent work',
    },
    'teacher': {
        'keep': 'contests for the strongest learners, and sharing what works at the next LAC session',
        'remediate-now': 'remediation classes this week, and a talk with the parents of learners below the passing mark',
        'remediate': 'remediation classes twice a week',
        'small-groups': 'targeted practice in small groups',
        'practice': 'targeted practice with a short check every other week',
        'enrich': 'enrichment: harder problems, projects and independent work',
    },
}


def _step(lv, *, reader, who, teachers, contests, first, when):
    """(kind, title, sentence) of the step that fits a level, said to an adviser (whose subject teachers act) or to the
    teacher of the classes. who: "this subject", "these classes", "your advisory class"."""
    order = policy.levels()                                         # highest first
    top, lowest_passing = order[0], [x for x in order if x['passing']][-1]
    target = insights._next_level(lv)
    act = 'Act first on' if first else 'Act on'
    ask = f'ask {teachers} to ' if reader == 'adviser' else ''
    if lv['key'] == top['key']:
        share = (f'ask {teachers} what works so the other subjects can try it' if reader == 'adviser'
                 else 'share what works at the next LAC session')
        return ('keep', f'Stay at {lv["name"]}: contests and sharing',
                f'Keep {who} at {lv["name"]}: put the strongest learners forward for {contests}, and {share}.')
    if not lv['passing'] and lv['key'] == order[-1]['key']:
        return ('remediate-now', f'Up to {target["name"]}: remediation this week',
                f'{act} {who}: {ask}start remediation classes this week, and talk with the parents of the learners below '
                f'the passing mark, to lift them to {target["name"]} {when}.')
    if not lv['passing']:
        return ('remediate', 'Up to passing: remediation twice a week',
                f'{act} {who}: {ask}hold remediation classes twice a week to bring learners up to the passing mark {when}.')
    if lv['key'] == lowest_passing['key']:
        pair = ', and pair learners who struggle with classmates who do well' if reader == 'adviser' else ''
        return ('small-groups', f'Up to {target["name"]}: practice in small groups',
                f'Lift {who} to {target["name"]}: {ask}give targeted practice in small groups{pair}, before learners slip '
                'below the passing mark.')
    if target['key'] == top['key']:
        return ('enrich', f'Up to {target["name"]}: enrichment',
                f'Push {who} up to {target["name"]}: {ask}give enrichment, with harder problems, projects and independent work.')
    return ('practice', f'Up to {target["name"]}: targeted practice',
            f'Move {who} up to {target["name"]}: {ask}give targeted practice with a short check every other week.')


def decisions(items, *, reader, item_word, next_period_name):
    """One recommendation for each level that has items on it, the lowest level first (see heads.insights.school_decisions)."""
    by = defaultdict(list)
    for item in items:
        by[item['level']['key']].append(item)
    out = []
    when = insights._when(next_period_name)
    for lv in reversed(policy.levels()):
        group = sorted(by.get(lv['key'], []), key=lambda i: i['average'])
        if not group:
            continue
        one = len(group) == 1
        who = f'this {item_word}' if one else f'these {item_word}es' if item_word == 'class' else f'these {item_word}s'
        teachers = 'its teacher' if one else 'their teachers'
        contests = insights._contests(group[0]['topic']) if one else 'contests in their fields'
        kind, title, step = _step(lv, reader=reader, who=who, teachers=teachers, contests=contests, first=not out, when=when)
        weak = insights._shared_weak(group) if kind != 'keep' else None
        focus = insights._focus(weak)
        out.append({'level': lv, 'target': insights._next_level(lv) if kind != 'keep' else None, 'kind': kind, 'items': group,
                    'title': title, 'urgent': not lv['passing'], 'step': step, 'focus': focus, 'focus_key': weak,
                    'text': ' '.join(s for s in (step, focus) if s), 'item_word': item_word, 'topic': ''})
    return out


def main_decision(found, *, reader, scope, who, average, overall, next_period_name):
    """The first recommendation: the one for the scope's own level, aimed at the grade component the whole scope scores
    lowest in. Returns (main, the other recommendations)."""
    if average is None:
        return None, found
    level = level_of(round(average, 1))
    own = next((d for d in found if d['level']['key'] == level['key']), None)
    others = [d for d in found if d is not own]
    known = [(k, overall[k]) for k in ('ww', 'pt', 'qa') if overall.get(k) is not None]
    weak = min(known, key=lambda kv: kv[1])[0] if len(known) == 3 else None
    teachers = 'the subject teachers' if reader == 'adviser' else 'you'
    kind, title, step = _step(level, reader=reader, who=who, teachers=teachers, contests='contests and quiz bees', first=True,
                              when=insights._when(next_period_name))
    focus = insights._focus(weak)
    main = {'level': level, 'target': insights._next_level(level) if kind != 'keep' else None, 'kind': kind, 'title': title,
            'step': step, 'focus': focus, 'focus_key': weak, 'text': ' '.join(s for s in (step, focus) if s),
            'items': own['items'] if own else [], 'urgent': not level['passing'], 'scope': scope,
            'item_word': own['item_word'] if own else '', 'topic': ''}
    if weak:
        for d in others:            # the main recommendation, always open above, already names that grade component
            if d['focus_key'] == weak:
                d['text'] = d['step']
    return main, others


def learner_subject(r, subject):
    """One subject a learner is below 75 in: the grade, the three component scores with the weakest marked, and what
    to do for them in that subject (teachers/remedial.py). r: a grade row with grade, ww, pt and qa."""
    abbr = {k: a for k, _, a in policy.components()}
    mine = {k: float(r[k]) for k in ('ww', 'pt', 'qa') if r[k] is not None}
    weak = min(mine, key=mine.get) if mine else None
    grade = round(float(r['grade']), 1)
    urgent = grade < URGENT
    return {'key': f's{subject.id}', 'subject': subject.id, 'name': subject.subject_name, 'code': subject.subject_code, 'grade': grade,
            'parts': [{'abbr': abbr[k], 'value': round(mine[k]) if k in mine else None, 'weak': k == weak} for k in ('ww', 'pt', 'qa')],
            'weak': weak or '', 'weak_name': remedial.component_name(weak) if weak else '', 'urgent': urgent,
            'text': remedial.learner_advice(subject, weak, urgent=urgent)}


def learner_facts(entry, subject):
    """What Poro is told about one learner in one subject: no name, no grade, only where they stand and what helps."""
    key = entry['weak'] or None
    return {'about': f'one learner below the passing mark in {insights._wordy(subject.subject_name)}', 'names': [subject.subject_name],
            'level': level_of(entry['grade'])['name'], 'target': '', 'goal': 'reach the passing mark', 'kind': 'remediate',
            'step': 'remediation in a small group or one on one' + (' starting this week' if entry['urgent'] else ' twice a week')
            + ', with a short check each week', 'weak_key': key or '', 'weak': remedial.component_name(key) if key else '',
            'tip': remedial.activity(subject, key) if key else '', 'order': policy.current()['name'], 'reader': 'teacher'}


def poro_token(main, found, items, reader):
    """The token Poro AI writes the recommendations under, or '' when there are none (heads/poro.py)."""
    names = [i['name'] for i in items]
    recs = {}
    for field, d, scope in [('main', main, main['scope'] if main else '')] + [(f'decision-{i}', d, '') for i, d in enumerate(found)]:
        if not d:
            continue
        facts = insights.poro_facts(d, scope=scope)
        facts.update(step=STEP_WORDS[reader][d['kind']], reader=reader)
        if not scope:                   # "the classes Mathematics, Amihan and Science, Amihan", without grade numbers
            word = d['item_word'] + ('' if len(d['items']) == 1 else 'es' if d['item_word'] == 'class' else 's')
            facts['about'] = f'the {word} ' + insights._and(insights._wordy(i['name']) for i in d['items'])
        if field == 'main':
            facts['names'] = sorted(set(facts['names']) | set(names))
        recs[field] = (d['text'], facts)
    return poro.register(recs, names=names) if recs else ''


def ladder(items):
    """The five levels, the lowest first, each listing every item whose average falls in it (a section or a teacher has a
    few, so all of them show)."""
    total = len(items) or 1
    return [{'level': lv, 'items': mine, 'count': len(mine), 'share': round(100 * len(mine) / total)}
            for lv in reversed(policy.levels())
            for mine in [sorted((i for i in items if i['level']['key'] == lv['key']), key=lambda i: -i['average'])]]


def _parts(values):
    return {key: round(mean(values[key]), 1) if values[key] else None for key in ('ww', 'pt', 'qa')}


def build(*, school, sy, periods, period, assignments, reader, scope, who, section=None):
    """Status, ladder, recommendations and forecast for one grading period.

    assignments: the classes in scope (the section's, or the teacher's own); reader: 'adviser' (items are the section's
    subjects) or 'teacher' (items are the classes); scope: how the status names the whole ("Grade 11 Academic-A", "Your
    classes"); who: how a recommendation names it ("your advisory class", "your classes").
    """
    empty = {'status': None, 'items': [], 'ladder': [], 'main': None, 'decisions': [], 'forecast': None, 'poro_token': ''}
    if not assignments or period is None:
        return empty
    pairs = {(a.section_id, a.subject_id) for a in assignments}
    subjects = {a.subject_id: a.subject for a in assignments}
    sections = {a.section_id: a.section for a in assignments}
    if len(sections) == 1:              # one section (the advisory class, or one the teacher teaches): its subjects
        item_word = 'subject'
        key_of = lambda r: r['subject_id']
        name_of = lambda k: subjects[k].subject_name
        topic_of = name_of
        short_of = lambda k: subjects[k].subject_code
    else:
        item_word = 'class'
        key_of = lambda r: (r['enrollment__section_id'], r['subject_id'])
        name_of = lambda k: f'{subjects[k[1]].subject_name}, {sections[k[0]].section_name}'
        topic_of = lambda k: subjects[k[1]].subject_name
        short_of = lambda k: f'{subjects[k[1]].subject_code} {sections[k[0]].section_name}'

    base = GradeComponent.objects.filter(P.HAS_GRADE, enrollment__school_year=sy, enrollment__status__in=R.ACTIVE,
                                         enrollment__section_id__in=sections, subject_id__in=subjects)
    rows = [r for r in base.filter(quarter=period).values(
        'enrollment_id', 'enrollment__student_id', 'enrollment__section_id', 'subject_id', 'enrollment__student__last_name',
        'enrollment__student__first_name', 'enrollment__student__middle_name', 'enrollment__student__lrn',
        'enrollment__section__section_name', grade=P.GRADE,
        ww=F('written_work_percent'), pt=F('performance_task_percent'), qa=F('quarterly_assessment_percent'))
        if (r['enrollment__section_id'], r['subject_id']) in pairs]
    if not rows:
        return empty
    earlier = [p for p in periods if p.quarter_number < period.quarter_number]
    prev = earlier[-1] if earlier else None
    prev_grades = [float(g) for s, subj, g in base.filter(quarter=prev).values_list('enrollment__section_id', 'subject_id', P.GRADE)
                   if (s, subj) in pairs] if prev else []

    groups = defaultdict(lambda: {'grades': [], 'ww': [], 'pt': [], 'qa': []})
    whole = {'grades': [], 'ww': [], 'pt': [], 'qa': []}
    for r in rows:
        for g in (groups[key_of(r)], whole):
            g['grades'].append(float(r['grade']))
            for part in ('ww', 'pt', 'qa'):
                if r[part] is not None:
                    g[part].append(float(r[part]))
    items = []
    for k, g in groups.items():
        avg, parts = round(mean(g['grades']), 1), _parts(g)
        items.append({'name': name_of(k), 'short': short_of(k), 'topic': topic_of(k), 'average': avg, 'level': level_of(avg), **parts,
                      'weak': insights.weakest(parts), 'below': sum(1 for x in g['grades'] if x < PASSING)})


    grades = whole['grades']
    average = mean(grades)
    delta = round(average - mean(prev_grades), 1) + 0.0 if prev_grades else None
    status = {'level': level_of(round(average, 1)), 'average': round(average, 1), 'delta': delta,
              'delta_abs': abs(delta) if delta is not None else None, 'prev_name': P.name(prev),
              'passing_rate': round(100 * sum(1 for g in grades if g >= PASSING) / len(grades), 1),
              'below': len({r['enrollment__student_id'] for r in rows if float(r['grade']) < PASSING}),
              'learners': len({r['enrollment__student_id'] for r in rows})}

    after = next((p for p in periods if p.quarter_number == period.quarter_number + 1), None)
    found = decisions(items, reader=reader, item_word=item_word, next_period_name=P.name(after))
    main, found = main_decision(found, reader=reader, scope=scope, who=who, average=status['average'],
                                overall=_parts(whole), next_period_name=P.name(after))

    # "See learners below 75" sits on the recommendations for the failing levels; with none, on the main one
    below = below_learners(rows, subjects, many_sections=len(sections) > 1)
    for d in ([main] if main else []) + found:
        d['below_link'] = bool(below) and d['urgent']
    if below and main and not any(d['below_link'] for d in [main] + found):
        main['below_link'] = True

    # the averages over the school year and what comes next, one line per item and the whole scope in ink
    spec = None
    table, meta = forecasts.year_table(school, sy)
    keys = [k for k, v in table.items() if (v['section'], v['subject']) in pairs]
    if keys:
        if len(sections) == 1:
            order = sorted(subjects, key=lambda s: subjects[s].subject_code)
            series = [{'key': f'subject-{s}', 'name': subjects[s].subject_name,
                       'keys': [k for k in keys if table[k]['subject'] == s]} for s in order]
        else:
            series = [{'key': f'class-{a.section_id}-{a.subject_id}', 'name': name_of((a.section_id, a.subject_id)),
                       'keys': [k for k in keys if (table[k]['section'], table[k]['subject']) == (a.section_id, a.subject_id)]}
                      for a in assignments]
        spec = forecasts.chart(table=table, meta=meta, periods=periods, items=series, level='class',
                               step_sd=insights.typical_step_sd(school.id), reference={'key': 'all', 'name': scope, 'keys': keys},
                               selected=period.quarter_number, period_name=P.name)
        spec['word'] = item_word
    return {'status': status, 'items': items, 'ladder': ladder(items), 'main': main, 'decisions': found,
            'forecast': spec if spec and spec['series'] else None, 'poro_token': poro_token(main, found, items, reader),
            'item_word': item_word, 'below_learners': below,
            'below_subjects': sorted({(s['code'], s['name']) for l in below for s in l['subjects']}, key=lambda c: c[1])}


def below_learners(rows, subjects, *, many_sections=False):
    """Every learner below 75 in at least one subject of the scope, the most subjects first, then the lowest grade: who
    they are and each subject they are below in (learner_subject). Poro writes its advice when a learner is opened
    (teachers/views.learner_advice)."""
    by = defaultdict(list)
    for r in rows:
        if float(r['grade']) < PASSING:
            by[r['enrollment_id']].append(r)
    out = []
    for enrollment, found in by.items():
        r = found[0]
        middle = (r['enrollment__student__middle_name'] or '').strip()
        entries = sorted((learner_subject(x, subjects[x['subject_id']]) for x in found), key=lambda s: s['grade'])
        out.append({'id': enrollment, 'lrn': r['enrollment__student__lrn'] or '',
                    'name': f"{r['enrollment__student__last_name']}, {r['enrollment__student__first_name']}" + (f' {middle[0]}.' if middle else ''),
                    'section': r['enrollment__section__section_name'] if many_sections else '', 'subjects': entries,
                    'lowest': entries[0]['grade']})
    out.sort(key=lambda l: (-len(l['subjects']), l['lowest'], l['name']))
    return out
