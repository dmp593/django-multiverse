# Testing

## Setup

Add to your test settings:

```python
TESTING = True
```

Routing stays **fully active** — a test exercises the same router decisions
production does. `TESTING` only stops per-tenant connection aliases from being
derived, so every query stays inside the databases Django's test runner created
and rolls back.

Without it, a test that activates a tenant opens that tenant's **real**
database, outside the test sandbox, with no rollback.

> Earlier releases used this flag to route *every* model to `default`, which
> meant downstream suites ran with routing switched off entirely. Misrouted
> apps, broken relations and cross-tenant leaks were all invisible in tests and
> appeared only in production. If you are upgrading, expect tests that never
> exercised routing to start doing so.

## `TenantTestCase`

```python
from multiverse.test import TenantTestCase
from invoices.models import Invoice

class InvoiceTests(TenantTestCase):
    tenant_subdomain = 'acme'

    def test_invoices_land_in_the_tenant_database(self):
        invoice = Invoice.objects.create(reference='INV-1')

        self.assertNotEqual(invoice._state.db, 'default')
```

It creates the tenant, adds its subdomain to `ALLOWED_HOSTS`, activates it for
each test and releases it afterwards.

| Attribute | Default | Purpose |
| --- | --- | --- |
| `tenant_subdomain` | `'test'` | Subdomain to create |
| `tenant_database_name` | the test tenant database | Database to point it at |
| `tenant` | set in `setUpClass` | The created tenant instance |
| `databases` | `'__all__'` | Tenant data lives outside `default` |

### The test client

`TenantClient` addresses every request to the active tenant:

```python
class ViewTests(TenantTestCase):
    tenant_subdomain = 'acme'

    def test_the_view_sees_the_tenant(self):
        response = self.client.get('/invoices/')

        self.assertEqual(response.wsgi_request.tenant.subdomain, 'acme')
```

Override the host explicitly to test resolution itself:

```python
self.client.get('/invoices/', HTTP_HOST='other.example.com')
```

### DRF

```python
from multiverse.test.drf import TenantAPITestCase

class InvoiceAPITests(TenantAPITestCase):
    tenant_subdomain = 'acme'
```

## Mixing into another base class

```python
from multiverse.test import TenantTestCaseMixin
from django.test import TransactionTestCase

class MyTests(TenantTestCaseMixin, TransactionTestCase):
    tenant_subdomain = 'acme'
```

## Testing without the tenant helpers

Any Django test case works — set `databases` to include the tenant alias and
activate a tenant yourself:

```python
from django.test import TestCase
from multiverse.awareness import tenant_context

class ReportTests(TestCase):
    databases = {'default', 'tenant'}

    def test_report(self):
        tenant = Tenant.objects.create(subdomain='acme', database_name='acme')

        with tenant_context(tenant):
            build_report()
```

## Testing provisioning

Two constraints apply when a test needs *real* per-tenant aliases — that is,
`TESTING = False`.

### 1. Unregister anything you derive

Django's `SimpleTestCase` snapshots the list of connection aliases at class
setup and walks the live list again at teardown, expecting each one to be
wrapped. An alias registered in between makes teardown fail with a confusing
`'function' object has no attribute 'wrapped'`.

```python
from django.test import SimpleTestCase, override_settings
from multiverse.db.connections import tenant_connections

@override_settings(TESTING=False)
class AliasTests(SimpleTestCase):
    def test_alias_is_derived(self):
        self.addCleanup(tenant_connections.unregister, 'acme_db')

        alias = tenant_connections.alias_for('acme_db')

        self.assertIn('acme_db', alias)
```

### 2. Django refuses to query an alias it did not know about

A Django test case rejects queries against any alias absent from `cls.databases`,
and a derived alias by definition is — it is created *during* the test. There is
no way to declare it in advance.

So test the two halves separately:

* **File and row effects** with a normal Django test case, `TESTING` left `True`,
  and `TENANT_DATABASE_DIRECTORY` pointed at a temporary directory. This covers
  `create_tenant` / `destroy_tenant` end to end.
* **Which alias `migrate` was pointed at** as a focused unit test, asserting on
  the argument rather than running it.

Both patterns are in this repository's own suite, in `tests/test_commands.py`
and `tests/test_migration_targeting.py`.

## Running this package's tests

```bash
pip install -e ".[all]"
python manage.py test tests
ruff check multiverse tests
```

The suite uses `tests/settings.py`, which doubles as a worked example of a
correctly configured project. It defines three apps — one per tier — so routing
is exercised for real rather than asserted against the router in isolation.
