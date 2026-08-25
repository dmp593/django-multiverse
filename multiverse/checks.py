"""
Startup checks for tenant configuration.

Most ways of misconfiguring this package do not raise. They silently place data
in the wrong database, and you find out weeks later. A missing
``DATABASE_ROUTERS`` entry, for instance, produces an application that starts
cleanly, serves traffic and writes every tenant's rows into one shared database.

These checks convert that class of failure into a message at startup. Anything
that will corrupt data placement is an Error; anything that is merely probably
wrong is a Warning.
"""

from __future__ import annotations

from django.conf import settings
from django.core.checks import Error, Warning, register
from django.core.exceptions import ImproperlyConfigured
from django.db import DEFAULT_DB_ALIAS

from multiverse.conf import multiverse_settings

ROUTER_PATH = 'multiverse.db.router.TenantRouter'


@register()
def check_tenant_model(app_configs, **kwargs):
    from multiverse.utils import get_tenant_model

    try:
        get_tenant_model()
    except ImproperlyConfigured as exc:
        return [
            Error(
                str(exc),
                hint="Set TENANT_MODEL = 'multiverse.Tenant' to use the "
                     'built-in model, or point it at your own.',
                id='multiverse.E001',
            )
        ]

    return []


@register()
def check_tenant_database_alias(app_configs, **kwargs):
    alias = multiverse_settings.tenant_database_alias
    errors = []

    if alias == DEFAULT_DB_ALIAS:
        errors.append(
            Error(
                f"TENANT_DATABASE_ALIAS is '{DEFAULT_DB_ALIAS}', which is the "
                f'system database.',
                hint='Tenant data would be written into the system database. '
                     'Use a separate alias, such as "tenant".',
                id='multiverse.E002',
            )
        )
    elif alias not in settings.DATABASES:
        errors.append(
            Error(
                f'DATABASES has no "{alias}" entry.',
                hint=f'Add a "{alias}" entry describing the engine and '
                     f'credentials your tenant databases use. Its NAME is only '
                     f'a placeholder; the active tenant supplies the real one.',
                id='multiverse.E003',
            )
        )

    return errors


@register()
def check_router_installed(app_configs, **kwargs):
    routers = [str(router) for router in getattr(settings, 'DATABASE_ROUTERS', [])]

    if any(ROUTER_PATH in router for router in routers):
        return []

    return [
        Error(
            f'{ROUTER_PATH} is not in DATABASE_ROUTERS.',
            hint='Without the router nothing is routed to tenant databases: '
                 'every tenant shares the system database. Add '
                 f"DATABASE_ROUTERS = ['{ROUTER_PATH}'].",
            id='multiverse.E004',
        )
    ]


@register()
def check_app_classification(app_configs, **kwargs):
    from multiverse.db.router import app_classifier

    messages = []

    for label, tiers in sorted(app_classifier.conflicting_app_labels().items()):
        messages.append(
            Error(
                f'App "{label}" is listed in more than one tier: '
                f'{", ".join(tiers)}.',
                hint='An app belongs in exactly one of SYSTEM_APPS, '
                     'COMMON_APPS or TENANT_APPS. While it is in several, '
                     'which database its tables land in depends on evaluation '
                     'order.',
                id='multiverse.E005',
            )
        )

    unclassified = app_classifier.unclassified_app_labels()

    if unclassified:
        messages.append(
            Warning(
                'These installed apps are in no tier and will be routed to '
                f'tenant databases: {", ".join(unclassified)}.',
                hint='That is usually right for your own apps and usually '
                     'wrong for third-party ones. List each app in '
                     'SYSTEM_APPS, COMMON_APPS or TENANT_APPS to say so '
                     'explicitly.',
                id='multiverse.W001',
            )
        )

    return messages


@register()
def check_tenant_header(app_configs, **kwargs):
    # Under DEBUG the header is the intended way to switch tenants on
    # localhost, where there is no subdomain to route on. Warning about it
    # there would be noise on every runserver start, and noisy checks are
    # checks people learn to ignore.
    if settings.DEBUG or not multiverse_settings.tenant_header_enabled:
        return []

    return [
        Warning(
            f'TENANT_HEADER_ENABLED is on with DEBUG off, so the '
            f'"{multiverse_settings.tenant_header_name}" header selects the '
            f'tenant and overrides the hostname in production.',
            hint='Any client can set this header. Only keep it enabled if a '
                 'trusted reverse proxy strips the inbound value and sets it '
                 'itself; otherwise a request can choose which customer\'s '
                 'database to read.',
            id='multiverse.W002',
        )
    ]
