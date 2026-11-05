"""Response headers that settings alone cannot set."""
from django.conf import settings


class PermissionsPolicyMiddleware:
    """Send Permissions-Policy (settings.PERMISSIONS_POLICY): a page cannot use the camera, microphone, location or
    payment features, even if a script that should not be there asks for them."""

    def __init__(self, get_response):
        self.get_response = get_response
        self.policy = getattr(settings, 'PERMISSIONS_POLICY', '')

    def __call__(self, request):
        response = self.get_response(request)
        if self.policy:
            response.headers.setdefault('Permissions-Policy', self.policy)
        return response
