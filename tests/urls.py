"""URLconf for the test suite."""

from django.http import JsonResponse
from django.urls import include, path


def whoami(request):
    """Report the tenant the middleware resolved, for middleware assertions."""
    tenant = getattr(request, 'tenant', None)

    return JsonResponse({'tenant': tenant.subdomain if tenant else None})


# A namespaced include, because SYSTEM_ROUTES matches on namespace.
health_patterns = ([path('', whoami, name='check')], 'health')

urlpatterns = [
    path('whoami/', whoami, name='whoami'),
    path('health/', include(health_patterns, namespace='health')),
]
