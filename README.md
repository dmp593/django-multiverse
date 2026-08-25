# Django Multiverse

Database-per-tenant multi-tenancy for Django. Every tenant gets its own physical
database — a separate SQLite file or a separate PostgreSQL database — and the
active tenant is resolved per request.

```python
from multiverse.awareness import tenant_context

with tenant_context(acme):
    Invoice.objects.count()   # reads acme's database
```

---

## How this differs from django-tenants

`django-tenants` gives each tenant a PostgreSQL *schema* inside one database.
This package gives each tenant a whole database. That single difference drives
everything else:

| | django-multiverse | django-tenants |
| --- | --- | --- |
| Isolation boundary | Separate database | Separate schema, one database |
| Backup / restore one tenant | Native, per database | Requires schema-level tooling |
| Move a noisy tenant to its own host | Change one row | Not possible |
| Data residency per tenant | Different server per tenant | One server |
| Engines | SQLite, PostgreSQL | PostgreSQL only |
| Cross-tenant queries | Not possible | Possible with effort |
| Connections at scale | One pool per active tenant | One pool total |
| Migrating 1,000 tenants | 1,000 databases | 1,000 schemas, one connection |

**Choose this** when isolation is a requirement you have to be able to
demonstrate — regulated data, per-customer residency, per-customer backup and
restore, or the ability to lift one customer onto their own hardware.

**Choose django-tenants instead** when you have thousands of small tenants,
need cross-tenant reporting queries, or are constrained on database connections.
Physical separation costs connections and makes fleet-wide migrations slower.

---

## Requirements

| | Supported |
| --- | --- |
| Python | 3.10 – 3.13 |
| Django | 5.0 – 5.2 |
| Databases | SQLite, PostgreSQL |

## Install

```bash
pip install django-multiverse                 # SQLite
pip install "django-multiverse[postgres]"     # + PostgreSQL
pip install "django-multiverse[all]"          # + DRF and django-q2 helpers
```

---

## Quickstart

Every step below is required. Skipping step 4 in particular produces a project
that starts, serves traffic, and quietly writes every tenant's rows into one
shared database — so the package refuses to start without it.

### 1. Classify your apps

Each app belongs to exactly one tier.

```python
# settings.py

SYSTEM_APPS = [                    # only in the system database
    'django.contrib.contenttypes',
    'django.contrib.auth',
    'django.contrib.admin',
    'timestamps',
    'multiverse',                  # the tenant registry lives here
    'billing',
]

COMMON_APPS = [                    # table in every database, rows are local
    'django.contrib.sessions',
]

TENANT_APPS = [                    # only in tenant databases
    'invoices',
    'projects',
]

INSTALLED_APPS = SYSTEM_APPS + COMMON_APPS + TENANT_APPS
```

Unlisted apps are routed to tenant databases and reported at startup. See
[docs/routing.md](docs/routing.md) for what each tier means.

### 2. Configure the databases

```python
DATABASES = {
    # Tenant registry, and everything readable before a tenant is known.
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'system',
        'USER': 'app',
        'PASSWORD': os.environ['DATABASE_PASSWORD'],
        'HOST': 'db.internal',
        'PORT': '5432',
    },
    # A *template*, not a live target. Supplies the engine and credentials
    # every tenant connection inherits; NAME is only a placeholder, replaced
    # by whichever tenant is active.
    'tenant': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'tenant_base',
        'USER': 'app',
        'PASSWORD': os.environ['DATABASE_PASSWORD'],
        'HOST': 'db.internal',
        'PORT': '5432',
    },
}
```

### 3. Point at your tenant model

```python
TENANT_MODEL = 'multiverse.Tenant'
```

Use the built-in model, or subclass `multiverse.models.BaseTenant` to add
columns and point `TENANT_MODEL` at yours — exactly like `AUTH_USER_MODEL`.

### 4. Install the router

```python
DATABASE_ROUTERS = ['multiverse.db.router.TenantRouter']
```

### 5. Install the middleware

```python
MIDDLEWARE = [
    # ...
    'multiverse.middleware.TenantMiddleware',
]
```

Place it after `SessionMiddleware` and `AuthenticationMiddleware` if those read
from the system database.

### 6. Migrate

```bash
python manage.py migrate                      # the system database
python manage.py create_tenant acme           # a tenant, its database, migrated
```

---

## Managing tenants

```bash
# Create a tenant, provision its database, migrate it.
python manage.py create_tenant acme

# Same, with an explicit database name and no migrations yet.
python manage.py create_tenant acme --database-name acme_eu --no-migrate

# Migrate every tenant database. Run this after the ordinary `migrate`,
# every time an app in TENANT_APPS gains a migration.
python manage.py migrate_tenants

# Retire a tenant: it stops serving traffic, its data is kept.
python manage.py destroy_tenant acme

# Retire it and destroy its database. Prompts unless --noinput.
python manage.py destroy_tenant acme --drop-database

# Also remove the registry row.
python manage.py destroy_tenant acme --drop-database --hard
```

`destroy_tenant` soft-deletes by default. The row is the only record of which
database belonged to which customer, which is worth keeping after the customer
is gone.

---

## Working with tenants in code

```python
from multiverse.awareness import get_current_tenant, tenant_context
from multiverse.utils import get_tenant

# Inside a request, the middleware has already activated the tenant.
def invoice_list(request):
    request.tenant                 # the resolved tenant
    return Invoice.objects.all()   # reads that tenant's database

# Outside a request, activate one explicitly. Nests, and always releases.
with tenant_context(get_tenant('acme')):
    Invoice.objects.count()

# Fan out over every tenant.
for tenant in Tenant.objects.all():
    with tenant_context(tenant):
        rebuild_search_index()
```

React to activation with the `tenant_changed` signal — it fires on release too,
with `instance=None`:

```python
from django.dispatch import receiver
from multiverse.signals import tenant_changed

@receiver(tenant_changed)
def set_log_context(sender, instance, **kwargs):
    logging_context.tenant = instance.subdomain if instance else None
```

---

## How a request finds its tenant

1. The `X-Tenant` header, if it is trusted — see below.
2. While `DEBUG` is on and the host is loopback, the tenant whose database is
   `TENANT_DATABASE_NAME` — your default local tenant.
3. Otherwise the first label of the hostname: `acme.example.com` → `acme`.

No match is a 404.

### Switching tenants in development

There is no subdomain to route on at `localhost`, and rule 2 only ever reaches
one tenant. So in development you name the tenant with a header:

```bash
curl -H 'X-Tenant: acme' http://localhost:8000/invoices/
```

This works out of the box — **`TENANT_HEADER_ENABLED` defaults to `DEBUG`**, so
it is on locally and off in production, with no configuration either way.

If you would rather use real subdomains locally, Chrome and Firefox resolve
`*.localhost` to loopback with no setup (Safari and most CLI tools do not, so
add `/etc/hosts` entries for those):

```python
ALLOWED_HOSTS = ['.localhost', '127.0.0.1']
# → http://acme.localhost:8000/
```

### In production the same header is a liability

With `DEBUG` off the header is ignored, because it overrides the hostname and
any client can send it — trusted blindly, an unauthenticated request could pick
which customer's database to read.

Turn it on only behind a proxy that **strips the inbound value and sets it
itself**:

```python
TENANT_HEADER_ENABLED = True
TENANT_HEADER_NAME = 'X-Tenant'
```

Doing so raises a startup warning by design. Full threat model in
[docs/security.md](docs/security.md).

---

## Testing

```python
from multiverse.test import TenantTestCase

class InvoiceTests(TenantTestCase):
    tenant_subdomain = 'acme'

    def test_invoices_are_tenant_scoped(self):
        Invoice.objects.create(reference='INV-1')
        self.assertEqual(Invoice.objects.count(), 1)
```

Add `TESTING = True` to your test settings. Routing stays fully active; this
only stops per-tenant connection aliases from being derived, which keeps every
query inside the databases Django's test runner creates and rolls back. See
[docs/testing.md](docs/testing.md).

---

## Documentation

| | |
| --- | --- |
| [Architecture](docs/architecture.md) | How tenant activation and connection routing work |
| [Configuration](docs/configuration.md) | Every setting, its default and its failure mode |
| [Routing](docs/routing.md) | The three tiers, with full decision tables |
| [Security](docs/security.md) | Threat model and what is and is not guaranteed |
| [Testing](docs/testing.md) | Test helpers, and testing provisioning |
| [Upgrading](docs/UPGRADING.md) | **1.x → 2.0 breaking changes** |
| [Changelog](CHANGELOG.md) | Release history |
| [Contributing](CONTRIBUTING.md) | Development setup and release process |

---

## Contributing

Issues and pull requests welcome at
[github.com/dmp593/django-multiverse](https://github.com/dmp593/django-multiverse).
See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT.

---

If this library is useful to you, you can
[buy me a coffee](https://www.buymeacoffee.com/dmp593).
