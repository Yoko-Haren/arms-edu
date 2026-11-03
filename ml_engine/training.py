"""Retraining the grade forecaster in the background, and the status of the last run.

Training reads every school's grades and takes about a minute, so it never runs inside a web request. It runs as
`python manage.py train_ml_models --model forecast --record` in a process of its own, and a status file next to the
model says whether a run is going, why it started, who started it and how the last one ended. Two things start a
run: the System Administrator (Admin Portal, Grade Forecaster) and the registrar locking a grading period, which
makes that period's grades final. Pages pick up the new model by themselves (ml_engine.forecast.load).

On Vercel nothing can run after a request and the disk is read-only, but every deployment trains the model while it
builds (vercel.json). There a run is a new deployment, asked for through the project's Deploy Hook
(settings.VERCEL_DEPLOY_HOOK_URL); each request is written to the activity log, which says when the last one began.
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone as dt_timezone
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from .forecast import MODEL_DIR

STATUS_FILE = MODEL_DIR / 'training.json'      # git-ignored with the model
LOG_FILE = MODEL_DIR / 'training.log'
STALE = timedelta(minutes=20)                    # a run that never reported back is treated as stopped after this


def _read():
    try:
        return json.loads(STATUS_FILE.read_text(encoding='utf-8'))
    except (FileNotFoundError, ValueError):
        return {}


def _write(data):
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATUS_FILE.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, indent=2), encoding='utf-8')
    os.replace(tmp, STATUS_FILE)


def status():
    """{'state': 'idle' | 'running' | 'done' | 'failed' | 'stopped', 'reason', 'by', 'started_at', 'finished_at', 'detail'}."""
    data = _read() or {'state': 'idle'}
    if data.get('state') == 'running':
        try:
            started = datetime.fromisoformat(data['started_at'])
        except (KeyError, ValueError):
            started = None
        if started is None or timezone.now() - started > STALE:
            data['state'] = 'stopped'
    return data


def running():
    return status().get('state') == 'running'


DEPLOY_ACTION = 'RETRAIN_DEPLOY'
DEPLOY_GAP = timedelta(minutes=15)               # one deployment at a time: a build takes a few minutes


def deploy_hook_ready():
    return bool(getattr(settings, 'VERCEL_DEPLOY_HOOK_URL', ''))


def last_deploy():
    """The latest deployment ARMS asked Vercel for, from the activity log, or None."""
    from audit.models import ActivityLog
    return ActivityLog.objects.filter(action_type=DEPLOY_ACTION).order_by('-timestamp').first()


def deploying(trained_at=None):
    """Whether a deployment asked for in the last 20 minutes has not yet brought a newer model."""
    last = last_deploy()
    if last is None or timezone.now() - last.timestamp > STALE:
        return None
    if trained_at is not None:
        trained = trained_at if timezone.is_aware(trained_at) else timezone.make_aware(trained_at, dt_timezone.utc)
        if trained >= last.timestamp:
            return None
    return last


def start_deploy(reason, by=''):
    """On Vercel: ask for a new deployment, whose build retrains the model. True when Vercel accepted it; False when the
    hook is not set, a deployment was asked for less than 15 minutes ago, or Vercel refused."""
    import urllib.request
    from audit.models import ActivityLog

    if not deploy_hook_ready():
        return False
    last = last_deploy()
    if last is not None and timezone.now() - last.timestamp < DEPLOY_GAP:
        return False
    try:
        with urllib.request.urlopen(urllib.request.Request(settings.VERCEL_DEPLOY_HOOK_URL, method='POST'), timeout=10) as reply:
            if reply.status >= 300:
                return False
    except OSError:
        return False
    ActivityLog.objects.create(user_name=by or 'ARMS', user_role='admin', action_type=DEPLOY_ACTION, resource_type='Grade forecaster',
                               details_json={'description': reason}, ip_address='127.0.0.1')
    return True


def start(reason, by=''):
    """Start a run unless one is going. True when it started. On Vercel the run is a new deployment (start_deploy)."""
    if getattr(settings, 'ON_VERCEL', False):
        return start_deploy(reason, by)
    if running():
        return False
    try:
        _write({'state': 'running', 'reason': reason, 'by': by, 'started_at': timezone.now().isoformat()})
    except OSError:                                  # no writable model folder: skip, never fail the caller
        return False
    flags = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP) if os.name == 'nt' else 0
    try:
        with open(LOG_FILE, 'w', encoding='utf-8') as log:
            subprocess.Popen([sys.executable, str(Path(settings.BASE_DIR) / 'manage.py'), 'train_ml_models', '--model', 'forecast', '--record'],
                             cwd=settings.BASE_DIR, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                             creationflags=flags, close_fds=True, start_new_session=os.name != 'nt')
    except OSError as exc:
        finish(False, f'Could not start the training process: {exc}')
        return False
    return True


def finish(ok, detail=''):
    """Called by the training command when a recorded run ends."""
    data = _read()
    data.update(state='done' if ok else 'failed', finished_at=timezone.now().isoformat(), detail=detail)
    _write(data)


def log_tail(lines=12):
    try:
        return LOG_FILE.read_text(encoding='utf-8', errors='replace').splitlines()[-lines:]
    except FileNotFoundError:
        return []
