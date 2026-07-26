# Architecture

## The core idea

One Django connection alias per tenant database.

`DATABASES['tenant']` is a **template**, not a live connection target. It
supplies the engine, host, credentials and options. Its `NAME` is a placeholder.
When a tenant is activated, the package derives a dedicated alias for that
tenant's database and registers it once:

```
DATABASES['tenant']          engine, host, credentials      (template)
        │
        ├── tenant::acme_db   NAME = acme_db
        ├── tenant::globex_db NAME = globex_db
        └── tenant::initech_db NAME = initech_db
```

The `::` separator never appears in a hand-written alias, so a derived alias is
recognisable on sight in tracebacks and query logs.

## Why it is built this way

Earlier releases activated a tenant by rewriting
`settings.DATABASES['tenant']['NAME']` in place. `settings` is a process-global
object, so two threads serving two tenants raced — the loser executed its
queries against the winner's database. Reproduced against Django 5.2:

```
thread A: set NAME='tenant_A.sqlite3'   CONNECTED TO='tenant_B.sqlite3'  ← leak
thread B: set NAME='tenant_B.sqlite3'   CONNECTED TO='tenant_B.sqlite3'
```

Three fixes were possible:

1. **Lock around the mutation.** Serialises every request, and the window stays
   open across the whole query, not just the assignment.
2. **Mutate the per-thread `DatabaseWrapper.settings_dict`.** A smaller change,
   but it depends on a Django internal being re-read at connect time, and
   silently loses the tenant whenever the wrapper is recreated.
3. **One alias per tenant.** Chosen.

The third removes the shared mutable state instead of guarding it. Django
already keeps `ConnectionHandler._connections` in a thread-local, so once two
threads use two different aliases they cannot see each other's connections at
all. Registration is idempotent and append-only, so nothing is left to race on.

It also makes `create_tenant` honest: it can migrate an explicit alias rather
than juggling global state and hoping.

## Request lifecycle

```
  request
     │
     ▼
  TenantMiddleware
     │
     ├─ is this a SYSTEM_ROUTES path?
     │     yes → run inside tenant_context(None) ── no tenant, guaranteed
     │
     ├─ resolve tenant from header / hostname          (utils.guess_tenant_from_request)
     ├─ request.tenant = tenant
     │
     ▼
  with tenant_context(tenant):          ← released however the request ends
     │
     ▼
  view → ORM query
     │
     ▼
  TenantRouter.db_for_read/write(model)
     │
     ├─ tenant registry model  → 'default'
     ├─ SYSTEM app             → 'default'
     ├─ COMMON app             → None (follow the related object, else default)
     └─ TENANT app             → get_current_database_alias()
                                     │
                                     ▼
                          tenant_connections.alias_for(tenant.database_name)
                                     │
                                     └─ 'tenant::acme_db'
```

## Where state lives

| State | Scope | Why |
| --- | --- | --- |
| Current tenant | Thread-local | Two concurrent requests serve two tenants |
| Current request | Thread-local | Same, and cleared in `finally` |
| Derived alias settings | Process-global | Immutable per alias, so sharing is safe |
| Connections | Thread-local | Django's own `ConnectionHandler` behaviour |

Activation itself has **no side effects**. It records a fact and sends a signal.
It does not touch `settings`, open connections or close them. The database that
follows from that fact is resolved lazily, on demand, by
`get_current_database_alias()`.

That is what makes activation safe to nest, safe in a `finally`, and possible to
reason about under concurrency.

## Connection lifecycle

Activation deliberately does **not** close connections. With one alias per
tenant, an open connection is always pointed at the right database, so closing
it on every switch would only defeat `CONN_MAX_AGE`.

Connections are closed by the mechanisms Django already provides:

* `close_old_connections`, on `request_started` / `request_finished`;
* django-q calls the same between tasks;
* `CONN_MAX_AGE` governs reuse in both cases.

The exception is `destroy_tenant`, which closes this process's connection and
unregisters the alias before dropping. PostgreSQL additionally terminates other
processes' sessions, because it refuses to drop a database anyone is connected
to and a single idle worker connection elsewhere in the fleet is enough to block
it.

### Connection count

Worst case is one connection per *active* tenant per thread. In a web process
with the default `CONN_MAX_AGE = 0` this settles at roughly one per thread,
because each request closes what it opened. If you raise `CONN_MAX_AGE`, budget
for `threads × concurrently-active tenants` and check it against your server's
`max_connections`. This is the main scaling cost of physical isolation.

## Module map

| Module | Responsibility |
| --- | --- |
| `conf` | Every setting the package reads. Resolved, never cached |
| `models` | `BaseTenant`, `Tenant` (swappable) |
| `validators` | Constrains the two fields that leave the ORM |
| `awareness` | Thread-local current tenant; `tenant_context` |
| `db.connections` | Derives and registers per-tenant aliases |
| `db.router` | `AppClassifier` and `TenantRouter` |
| `db.backends.base` | The `DatabaseProvisioner` contract |
| `db.backends.{sqlite3,postgresql}` | Engine-specific provisioning |
| `middleware` | Binds a request to a tenant |
| `utils` | Tenant resolution and lookup |
| `checks` | Startup validation of the configuration |
| `tasks.django_q` | Carries the tenant through a queue |
| `test` | `TenantTestCase`, `TenantClient` |

Dependencies point inward: `conf` and `validators` depend on nothing but Django;
`router` and `middleware` sit at the edge. The router no longer imports the test
package, so `django.test` is not pulled into production processes.

## Adding a database backend

Implement two methods and register the class:

```python
from multiverse.db.backends.base import DatabaseProvisioner, register_provisioner

class MySQLProvisioner(DatabaseProvisioner):
    def create_if_not_exists(self, database_name): ...
    def drop_if_exists(self, database_name): ...

register_provisioner('mysql', 'myproject.provisioners.MySQLProvisioner')
```

Matching is by substring against `ENGINE`, so backends that wrap a core engine
(`django.contrib.gis.db.backends.postgis`) are picked up automatically.

Override `connection_name()` if your engine addresses databases by something
other than the name — the SQLite backend returns a resolved absolute path, which
is what guarantees the file it creates is the file Django then opens.
