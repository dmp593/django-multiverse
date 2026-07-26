"""
Binds each request to the tenant it is addressed to.
"""

from __future__ import annotations

from django.urls import Resolver404, resolve

from multiverse.awareness import forget_request, set_request, tenant_context
from multiverse.conf import multiverse_settings
from multiverse.utils import guess_tenant_from_request


class TenantMiddleware:
    """
    Activates a tenant for the duration of the request, and only for that duration.

    Activation is bracketed by ``tenant_context`` so the tenant is released
    however the request ends — normally, via an exception, or via a redirect
    raised deep in a view. Earlier releases activated on the way in and never
    deactivated, so a pooled worker thread carried one customer's tenant into the
    next customer's request.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # System routes run with no tenant at all, rather than merely skipping
        # activation. Skipping would leave whatever the thread was last serving
        # in place, which is how a health check ends up querying a customer's
        # database.
        if self._is_system_route(request):
            request.tenant = None

            with tenant_context(None):
                return self.get_response(request)

        tenant = guess_tenant_from_request(request)
        request.tenant = tenant

        set_request(request)

        try:
            with tenant_context(tenant):
                return self.get_response(request)
        finally:
            forget_request()

    def _is_system_route(self, request) -> bool:
        system_routes = multiverse_settings.system_routes

        if not system_routes:
            return False

        try:
            # `path_info` rather than `path`: it excludes the WSGI script prefix,
            # which is what Django's own resolver matches against.
            match = resolve(request.path_info)
        except Resolver404:
            # An unmatched URL is not a system route. Let Django's own resolution
            # raise the 404 so the project's handler renders it. Calling resolve()
            # unguarded here previously turned every 404 in the project — every
            # scan for /wp-admin/ included — into a 500.
            return False

        return match.namespace in system_routes or match.app_name in system_routes
