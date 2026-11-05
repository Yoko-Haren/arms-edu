"""Who is asking: the visitor's address, for sign-in limits and logs.

On Vercel every request comes through its proxy, which sets X-Real-IP and X-Forwarded-For to the visitor's own
address and overwrites whatever the visitor sent, so there they can be trusted. Anywhere else those headers are
whatever the visitor typed, so only REMOTE_ADDR counts.
"""
import ipaddress

from django.conf import settings


def _valid(value):
    try:
        return str(ipaddress.ip_address((value or '').strip()))
    except ValueError:
        return ''


def client_ip(request):
    """The visitor's IP address, or '0.0.0.0' when none can be read (the logs need one)."""
    if getattr(settings, 'ON_VERCEL', False):
        real = _valid(request.META.get('HTTP_X_REAL_IP'))
        if real:
            return real
        forwarded = _valid((request.META.get('HTTP_X_FORWARDED_FOR') or '').split(',')[0])
        if forwarded:
            return forwarded
    return _valid(request.META.get('REMOTE_ADDR')) or '0.0.0.0'
