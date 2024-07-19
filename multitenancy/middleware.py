from django.conf import settings
from django.urls import resolve

from multiverse.awareness import set_current_tenant, set_request
from multiverse.utils import guess_tenant_from_request, get_tenant_model


Tenant = get_tenant_model()


class TenantMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        resolver_match = resolve(request.path)
        
        if resolver_match.app_name in getattr(settings, 'SYSTEM_ROUTES', []):
            return self.get_response(request)

        tenant = guess_tenant_from_request(request)
        setattr(request, 'tenant', tenant)

        set_request(request)
        set_current_tenant(tenant)

        return self.get_response(request)
