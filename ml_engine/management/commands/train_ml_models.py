"""Train the grade forecaster (XGBoost) that every forecast in ARMS uses (ml_engine/forecast.py).

    python manage.py train_ml_models            # train on every school's grades and save the model
    python manage.py train_ml_models --record   # also record how the run ended for the Admin Portal (ml_engine/training.py)

Vercel trains it on every deployment (vercel.json); the System Administrator and a registrar locking a grading
period start a run here.
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Train the grade forecaster (XGBoost) on every school\'s grades and save it.'

    def add_arguments(self, parser):
        # --model forecast is what the build and the Admin Portal pass; the forecaster is the only model
        parser.add_argument('--model', choices=['forecast'], default='forecast', help='The model to train (the grade forecaster).')
        parser.add_argument('--record', action='store_true',
                            help='Record how the run ended in the status the Admin Portal shows (ml_engine/training.py)')

    def handle(self, *args, **options):
        from ml_engine import training
        from ml_engine.forecast import train

        self.stdout.write('Training the grade forecaster (XGBoost)...')
        try:
            meta = train(log=self.stdout.write)
        except Exception as exc:
            self.stdout.write(self.style.ERROR(f'Training failed: {exc}'))
            if options['record']:
                training.finish(False, str(exc)[:300])
            raise SystemExit(1)
        latest = meta['evaluation'][-1]
        summary = (f'Version {meta["version"]}: {meta["samples"]:,} records, off by {latest["model"]["mae"]:.1f} points on '
                   f'average for {latest["split"]}.')
        if options['record']:
            training.finish(True, summary)
        self.stdout.write(self.style.SUCCESS(summary))
