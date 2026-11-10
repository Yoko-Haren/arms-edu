"""Poro AI: the School Head's recommendations in its own words, from a local language model.

What to recommend is decided by rules a person can check (heads/insights.py, school_decisions): which disciplines stand
on which level of the school's DepEd order, the step that fits each level, and the grade component to aim at. Poro
writes the advice for the teachers itself, from those facts, in up to three short sentences: what to do in class for
that discipline, in its own ideas. The adviser asked for that freedom on 2026-11-04, with no numbers (the dashboard
shows them) and no lists. It runs on the school's own computer through Ollama; 'poro' is qwen2.5 with Poro's persona
(ml_engine/poro/Modelfile).

Nothing is shown unchecked. Advice is kept only when it has no numbers, lists or markup, at most three sentences, no
level but the ones it was given, no grade component but the weakest, no other discipline of the page, nothing about
results going up or down, no cause, promise, learner or "he"/"she", and a second reading by the model finds it fits the
facts. Otherwise the computed wording stays, and the School Head can switch between the two. Writing takes a while on
a laptop, so it happens in one background worker: the page shows the computed text at once and swaps in Poro's as each
recommendation passes. It stays cached until the grades change, because the token is built from the facts themselves.
"""
import hashlib
import json
import logging
import queue
import re
import threading
import time

from django.conf import settings
from django.core.cache import caches

from core import grading_policy as policy

logger = logging.getLogger(__name__)
TTL = 7 * 24 * 3600               # advice is kept a week, or until its facts change (a new token)
RETRY_AFTER = 600                 # an unreachable model is tried again after 10 minutes
OLD_LEVELS = ('Beginning', 'Approaching Proficiency', 'Proficient', 'Advanced')     # DepEd Order No. 73, s. 2012
DRIFT = ('guarantee', 'definitely', 'surely', 'promise', 'as an ai', 'language model', 'expected to', 'is expected', 'will improve',
         'will rise', 'will increase', 'will drop', 'will decrease', 'due to', 'caused by', 'affected by', 'as a result of',
         'because of')
PRONOUNS = re.compile(r'\b(?:he|she|her|hers|his|him|himself|herself)\b', re.I)     # a discipline is not "her"
NUMBER_WORDS = re.compile(r'\b(?:two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty|thirty|forty|fifty|'
                          r'sixty|seventy|eighty|ninety|hundred|percent|percentage|half)\b', re.I)
# claims about what the grades did: the advice says what to do, never what happened
TREND = re.compile(r'\b(?:rose|risen|fell|fallen|dropped|declined|decreased|increased|slipped|improved|worsened|went (?:up|down)|'
                   r'has (?:gone|been) (?:up|down))\b', re.I)
PROMISE = re.compile(r'\bwill\s+(?:help|boost|raise|lift|ensure|lead|result|make|improve|increase|elevate|bring)\b', re.I)
LIST = re.compile(r'[*#`•]|(?:^|\n)\s*(?:-|\d+[.)]|[a-c][.)])\s|\n')
# phrases that name each grade component, whatever the DepEd order calls it ("performance" alone is plain English)
COMPONENT_WORDS = {'ww': ('written work', 'written/oral', 'oral work'), 'pt': ('performance task', 'product/performance', 'product task'),
                   'qa': ('quarterly assessment', 'quarterly exam', 'summative test', 'term exam', 'examination')}
ADVICE_SCHEMA = {'type': 'object', 'properties': {'text': {'type': 'string'}}, 'required': ['text']}
FITS_SCHEMA = {'type': 'object', 'properties': {'ok': {'type': 'boolean'}}, 'required': ['ok']}
MAX_SENTENCES = 3


def _conf():
    return getattr(settings, 'PORO_AI', {})


def _cache():
    return caches['default']


def models():
    """Poro's model first, then the base it is built on, then the small model, whichever Ollama has."""
    conf, out = _conf(), []
    for name in (conf.get('MODEL', 'poro'), conf.get('BASE_MODEL', 'qwen2.5:3b'), 'qwen2.5:1.5b'):
        if name and name not in out:
            out.append(name)
    return out


def ask(prompt, schema, *, num_predict, temperature, timeout):
    """(parsed JSON, model name) from the first of models() that Ollama has. Raises when Ollama cannot answer."""
    import requests

    url = _conf().get('OLLAMA_URL', 'http://127.0.0.1:11434').rstrip('/') + '/api/generate'
    missing = []
    for model in models():
        response = requests.post(url, timeout=timeout, json={
            'model': model, 'prompt': prompt, 'stream': False, 'format': schema,
            'options': {'temperature': temperature, 'num_predict': num_predict}})
        if response.status_code == 404:                      # not installed on this computer: try the next
            missing.append(model)
            continue
        response.raise_for_status()
        return json.loads(response.json().get('response') or '{}'), model
    raise RuntimeError('None of these models is installed in Ollama: ' + ', '.join(missing))


# --------------------------------------------------------------------------------------------
# Recommendations to write, and one background worker (Ollama runs one model at a time anyway)
# --------------------------------------------------------------------------------------------

def register(recs, *, names=()):
    """Remember the recommendations for Poro to write and return the token the page polls with. recs: {field: (computed
    text, facts)} with facts from insights.poro_facts. Only what the server decided can be written up, so the endpoint
    is no open AI proxy. names: every discipline, subject or section on the page (advice may name only its own)."""
    rules = policy.current()
    payload = {'v': 5, 'recs': [[field, text, facts] for field, (text, facts) in recs.items()],
               'names': sorted({n for n in names if n}), 'levels': [lv['name'] for lv in rules['levels']]}
    token = hashlib.sha1(json.dumps(payload, sort_keys=True).encode('utf-8')).hexdigest()[:24]
    _cache().set(f'poro:src:{token}', payload, TTL)
    return token


def status(token):
    """{'state': 'writing'} until the recommendations are written (with the ones done so far, if any), then the result:
    {'state': 'done', 'source': 'ai' | 'rules', 'fields', 'written', 'total', 'model'}. None for an unknown token."""
    if not _cache().get(f'poro:src:{token}'):
        return None
    done = _cache().get(f'poro:out:{token}')
    if done is not None:
        return dict(done, state='done')
    if not _conf().get('ENABLED', True):
        return {'state': 'done', 'source': 'rules', 'model': '', 'disabled': True}
    _submit(token)
    return dict(_cache().get(f'poro:part:{token}') or {}, state='writing')


def written(token):
    """The fields Poro has written and checked for a token ({'main': '...'}), or {} while it is still writing or could
    not. Starts nothing (for downloads)."""
    done = _cache().get(f'poro:out:{token}') if token else None
    return (done or {}).get('fields') or {}


_jobs = queue.Queue()
_worker = None
_worker_lock = threading.Lock()


def _submit(token):
    global _worker
    if not _cache().add(f'poro:job:{token}', time.time(), 900):      # already waiting or being written
        return
    _jobs.put(token)
    with _worker_lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_work, name='poro-writer', daemon=True)
            _worker.start()


def _work():
    while True:
        token = _jobs.get()
        try:
            _write(token)
        except Exception as exc:
            logger.warning('Poro could not write %s: %s', token, exc)
            _cache().set(f'poro:out:{token}', {'state': 'done', 'source': 'rules', 'model': '', 'unavailable': True}, RETRY_AFTER)
        finally:
            _cache().delete(f'poro:job:{token}')
            _jobs.task_done()


# --------------------------------------------------------------------------------------------
# Writing one recommendation from its facts, then checking it
# --------------------------------------------------------------------------------------------

def _fact_lines(facts):
    lines = [f'For: {facts["about"]}', f'Where they are now: {facts["level"]} ({facts["order"]})', f'Goal: {facts["goal"]}',
             f'The step the school decided: {facts["step"]}']
    if facts.get('weak'):
        lines.append(f'Weakest part of their grades: {facts["weak"]} (what lifts it: {facts["tip"]})')
    return '\n'.join(lines)


# who reads the advice: the School Head passes it on; a class adviser shares it with the section's subject teachers
# (teachers/standing.py); a teacher uses it in their own classes
READERS = {
    'head': 'Write advice that a School Head can pass on to the teachers below. In two or three short sentences, tell the '
            'teachers what to do in class next',
    'adviser': 'Write advice that a class adviser can share with the subject teachers of the section below. In two or three '
               'short sentences, say what the teachers can do in class next',
    'teacher': 'Write advice for the teacher of the classes below. In two or three short sentences, say what to do in class next',
}


def _advice_prompt(facts):
    return (READERS.get(facts.get('reader'), READERS['head']) + ' to reach the goal: concrete activities, materials or '
            'routines that suit the discipline, in your own ideas. Do not copy the facts word for word; turn the decided step'
            + (' and the weakest part' if facts.get('weak') else '') + ' into classroom ideas. Start with the most important '
            'action.\nRules: no numbers, percentages, dates or grade levels; do not name the levels; no lists; do not name '
            'learners; do not say what the grades did or will do; do not promise results; do not write "I". Call them '
            'learners. Answer with {"text": ...}.\n\n' + _fact_lines(facts))


def _fits(facts, text):
    """The model as a second reader, after the rule checks: does the advice suit the facts? Its mistakes in this role
    are mostly rejecting good advice, which only keeps the computed wording."""
    prompt = ('Here are facts about some classes and advice for their teachers. Answer {"ok": true} only if the advice suits '
              'the facts: it works toward the goal, fits the decided step' + (' and the weakest part' if facts.get('weak') else '')
              + ', and says nothing about past results, causes or numbers. Otherwise answer {"ok": false}.\n\nFACTS:\n'
              + _fact_lines(facts) + '\n\nADVICE: ' + text)
    try:
        data, _ = ask(prompt, FITS_SCHEMA, num_predict=12, temperature=0, timeout=_conf().get('WRITE_TIMEOUT', 600))
    except Exception:                       # no second reading, no advice
        return False
    return data.get('ok') is True


def sentences(text):
    return [s for s in re.split(r'(?<=[.!?])\s+(?=[A-Z])', ' '.join(text.split())) if s]


def problem(text, facts, stored):
    """Why Poro's advice cannot be shown, or None when it passes every rule (see the module docstring)."""
    if not isinstance(text, str) or not text.strip():
        return 'missing'
    if LIST.search(text.strip()):
        return 'a list, a line break or markup'
    text = house_style(text)
    words = len(text.split())
    count = len(sentences(text))
    if count > MAX_SENTENCES or words > 80:
        return f'too long ({count} sentences, {words} words)'
    if words < 8:
        return 'too short'
    if re.search(r'\d|%', text) or NUMBER_WORDS.search(text):
        return 'a number'
    if re.search(r'https?://|\bI\b|\bmy\b', text):
        return 'a link or "I"'
    lowered = text.lower()
    drift = [w for w in DRIFT if w in lowered]
    if drift:
        return 'a promise or a cause: ' + ', '.join(drift)
    if TREND.search(text):
        return 'a claim about what the grades did'
    if PROMISE.search(text):
        return 'a promise'
    if PRONOUNS.search(text):
        return 'a "he" or "she"'
    # the card names the level; advice that names one can only repeat it or get it wrong ("already at Advancing"). A level
    # is a name when capitalized or called a level; "advanced problems" and "connecting lessons" are plain English.
    levels = {lv['name'] for rules in policy.POLICIES.values() for lv in rules['levels']} | set(OLD_LEVELS)
    for n in levels:
        if re.search(r'\b' + re.escape(n) + r'\b', text) or re.search(r'\b' + re.escape(n) + r'\s+levels?\b', text, re.I):
            return 'a level name'
    if _components(text) - ({facts['weak_key']} if facts.get('weak_key') else set()):
        return 'a grade component other than the weakest'
    own = [n.lower() for n in facts.get('names', [])] + [facts['about'].lower()]
    for name in stored.get('names', []):
        if re.search(r'\b' + re.escape(name) + r'\b', text, re.I) and not any(name.lower() in o for o in own):
            return f'another {name}'
    return None


def _write(token):
    stored = _cache().get(f'poro:src:{token}')
    if not stored:
        return
    started, written, raw, model = time.time(), {}, [], ''
    recs = stored['recs']
    for n, (field, _computed, facts) in enumerate(recs, start=1):
        for temperature in (0.8, 0.5):          # advice that fails a check gets one calmer try
            data, model = ask(_advice_prompt(facts), ADVICE_SCHEMA, num_predict=160, temperature=temperature,
                              timeout=_conf().get('WRITE_TIMEOUT', 600))
            text = data.get('text')
            why = problem(text, facts, stored)
            ok = _fits(facts, house_style(text)) if why is None else None
            raw.append({'field': field, 'text': text, 'temperature': temperature, 'problem': why, 'fits': ok})
            if why is None and ok:
                written[field] = house_style(text)
                break
        # the page shows each recommendation as soon as it passes
        _cache().set(f'poro:part:{token}', dict(_assemble(recs, written), done=n, model=model), 3600)
    _cache().set(f'poro:raw:{token}', {'items': raw}, 24 * 3600)      # the unchecked answers, for looking into rejections only
    result = dict(_assemble(recs, written), model=model, seconds=round(time.time() - started))
    _cache().set(f'poro:out:{token}', result, TTL if result['written'] else 24 * 3600)
    _cache().delete(f'poro:part:{token}')


def _assemble(recs, written):
    return {'source': 'ai' if written else 'rules', 'fields': dict(written), 'written': len(written), 'total': len(recs)}


def _components(text):
    lowered = text.lower()
    return {key for key, words in COMPONENT_WORDS.items() if any(w in lowered for w in words)}


def house_style(text):
    """DepEd's word for them: learners, not students."""
    return re.sub(r'\b([Ss])tudent(s?)\b', lambda m: ('L' if m.group(1) == 'S' else 'l') + 'earner' + m.group(2),
                  ' '.join(str(text or '').split()))
