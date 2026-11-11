from django.apps import AppConfig


class AuditConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'audit'
    verbose_name = 'Audit Trail & Blockchain'

    def ready(self):
        from . import trail
        trail.connect()             # the audit trail: adds, edits, deletes and sign-ins (audit/trail.py)
