"""
Settings for the package's own test suite.

Doubles as a worked example of a correctly configured project: every setting
django-multiverse understands appears here with a comment explaining the choice.
"""

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = 'django-multiverse-test-key-not-a-secret'
DEBUG = True
ALLOWED_HOSTS = ['*']
USE_TZ = True

ROOT_URLCONF = 'tests.urls'

# Apps are grouped by tier and concatenated, so INSTALLED_APPS and the routing
# configuration cannot drift apart.
SYSTEM_APPS = [
    'django.contrib.contenttypes',
    'django.contrib.auth',
    'timestamps',
    'multiverse',
    'tests.apps.systemapp',
]

COMMON_APPS = [
    'tests.apps.commonapp',
]

TENANT_APPS = [
    'tests.apps.tenantapp',
]

INSTALLED_APPS = SYSTEM_APPS + COMMON_APPS + TENANT_APPS

MIDDLEWARE = [
    'multiverse.middleware.TenantMiddleware',
]

# `default` holds the tenant registry and everything else a request needs before
# its tenant is known. `tenant` is a *template*: its NAME is a placeholder that
# the active tenant replaces. Both are real, separate databases.
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'system.sqlite3',
    },
    'tenant': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'tenant_base.sqlite3',
    },
}

DATABASE_ROUTERS = ['multiverse.db.router.TenantRouter']

TENANT_MODEL = 'multiverse.Tenant'
TENANT_DATABASE_ALIAS = 'tenant'

# File-backed tenant databases are confined to this directory. Anything that
# resolves outside it is rejected before it reaches the filesystem.
TENANT_DATABASE_DIRECTORY = BASE_DIR / 'tenant_databases'

# Off by default, and left off here so the suite exercises the secure default.
# Individual tests turn it on with override_settings.
TENANT_HEADER_ENABLED = False

# Recommended in every test settings module. Routing stays fully active; this
# only stops per-tenant connection aliases from being derived, which keeps every
# query inside the databases Django's test runner created and rolls back.
# Without it, a test that activates a tenant would open that tenant's *real*
# database.
TESTING = True

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
