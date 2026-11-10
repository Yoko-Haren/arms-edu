from django.apps import AppConfig


class MlEngineConfig(AppConfig):
    """The grade forecaster (forecast.py): trained by `python manage.py train_ml_models`, loaded on first use."""
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'ml_engine'
