# Configuration

Every setting this package reads is declared in `multiverse/conf.py`. Nothing is
cached: values are resolved on each access so `override_settings` works in your
test suite.

## Required

### `TENANT_MODEL`

Dotted `app_label.ModelName` of the tenant model.

```python
TENANT_MODEL = 'multiverse.Tenant'
```

No default. Unset raises `ImproperlyConfigured` and reports `multiverse.E001` at
startup. Use `multiverse.Tenant`, or subclass `multiverse.models.BaseTenant` and
point this at yours.

### `DATABASE_ROUTERS`

```python
DATABASE_ROUTERS = ['multiverse.db.router.TenantRouter']
```

Reported as `multiverse.E004` when missing. Without it nothing is routed
anywhere and every tenant shares the system database — silently.

### `DATABASES['tenant']`

The template every tenant connection is derived from. Supplies engine, host,
credentials and options; its `NAME` is only a placeholder. Reported as
`multiverse.E003` when missing.

## Databases

### `TENANT_DATABASE_ALIAS`

*Default:* `'tenant'`

Which `DATABASES` entry is the template. Setting it to `'default'` is
`multiverse.E002` — tenant data would land in the system database.

### `TENANT_DATABASE_NAME`

*Default:* the `NAME` on the tenant alias

The database used when no tenant is active, and the tenant matched by the
loopback development shortcut. Read from `settings.DATABASES`, never from a live
connection.

### `TENANT_DATABASE_DIRECTORY`

*Default:* `BASE_DIR`, else the directory of the base tenant database

Directory that file-backed (SQLite) tenant databases are confined to. Any
`database_name` resolving outside it raises `SuspiciousOperation`. This is the
second of two layers stopping a hostile name from escaping; the first is the
model validator.

### `TENANT_PROVISIONING_DATABASE`

*Default:* `'postgres'`

Maintenance database used to issue `CREATE DATABASE` / `DROP DATABASE`, which
cannot be run from the database they target. Change it if your provider does not
expose `postgres`.

## App classification

### `SYSTEM_APPS`, `COMMON_APPS`, `TENANT_APPS`

*Default:* `[]`

Each app belongs to **exactly one**. Entries may be dotted paths
(`django.contrib.auth`) or app labels (`auth`); both are resolved through
Django's app registry, so apps that override `AppConfig.label` match correctly.

An app in two tiers is `multiverse.E005`. An installed app in none is
`multiverse.W001` and is routed to tenant databases. See
[routing.md](routing.md).

## Requests

### `SYSTEM_ROUTES`

*Default:* `[]`

URL namespaces served without a tenant — health checks, sign-up, billing.
Matched against `ResolverMatch.namespace` and `.app_name`, so the route must be
in a namespaced `include()`:

```python
# urls.py
path('health/', include(('health.urls', 'health'), namespace='health')),

# settings.py
SYSTEM_ROUTES = ['health']
```

These run inside `tenant_context(None)`, so they are guaranteed to have no
tenant rather than merely skipping activation.

Leaving this empty skips URL resolution in the middleware entirely, which is
marginally faster.

### `TENANT_HEADER_ENABLED`

*Default:* `DEBUG` — on in development, off in production

Whether an HTTP header may select the tenant.

**In development it is how you switch tenants.** `localhost` has no subdomain to
route on, and the loopback rule above only ever reaches one tenant, so naming
the tenant in a header is the only practical way to work against several:

```bash
curl -H 'X-Tenant: acme' http://localhost:8000/invoices/
```

**In production it is a liability.** The header overrides the hostname and any
client can send it. Trusting it in production emits `multiverse.W002` and
requires a proxy that strips the inbound value and sets it itself. See
[security.md](security.md).

Set it explicitly to override the `DEBUG` default in either direction —
`TENANT_HEADER_ENABLED = False` while developing is how you reproduce production
resolution locally.

### `TENANT_HEADER_NAME`

*Default:* `'X-Tenant'`

## Testing

### `TESTING`

*Default:* `False`

Set `True` in your test settings. Routing stays fully active; this only stops
per-tenant aliases from being derived, so every query stays inside the databases
Django's test runner created and rolls back. Without it, a test that activates a
tenant opens that tenant's **real** database. See [testing.md](testing.md).

`TenantTestCase` sets it for you.

## Startup checks

| ID | Level | Meaning |
| --- | --- | --- |
| `multiverse.E001` | Error | `TENANT_MODEL` unset, malformed or not installed |
| `multiverse.E002` | Error | Tenant alias points at the system database |
| `multiverse.E003` | Error | Tenant alias missing from `DATABASES` |
| `multiverse.E004` | Error | `TenantRouter` not in `DATABASE_ROUTERS` |
| `multiverse.E005` | Error | An app is claimed by more than one tier |
| `multiverse.W001` | Warning | Installed apps in no tier |
| `multiverse.W002` | Warning | The tenant header is trusted with `DEBUG` off |

Run them with `python manage.py check`.

## Complete example

```python
from pathlib import Path
import os

BASE_DIR = Path(__file__).resolve().parent.parent

SYSTEM_APPS = [
    'django.contrib.contenttypes', 'django.contrib.auth', 'django.contrib.admin',
    'timestamps', 'multiverse', 'billing',
]
COMMON_APPS = ['django.contrib.sessions']
TENANT_APPS = ['invoices', 'projects']
INSTALLED_APPS = SYSTEM_APPS + COMMON_APPS + TENANT_APPS

MIDDLEWARE = [
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'multiverse.middleware.TenantMiddleware',
]

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'system',
        'USER': 'app',
        'PASSWORD': os.environ['DATABASE_PASSWORD'],
        'HOST': 'db.internal',
        'PORT': '5432',
    },
    'tenant': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'tenant_base',
        'USER': 'app',
        'PASSWORD': os.environ['DATABASE_PASSWORD'],
        'HOST': 'db.internal',
        'PORT': '5432',
    },
}

DATABASE_ROUTERS = ['multiverse.db.router.TenantRouter']

TENANT_MODEL = 'multiverse.Tenant'
TENANT_DATABASE_ALIAS = 'tenant'
SYSTEM_ROUTES = ['health', 'signup']

# Left unset: follows DEBUG, so the header is trusted locally and ignored in
# production. Set it to True only behind a proxy that strips and re-sets it.
```
