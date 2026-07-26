# Security

## What this package guarantees

* A tenant's tables live in a physically separate database. There is no query
  that reaches another tenant's rows by accident, because the connection is not
  pointed at that database.
* Tenant activation is thread-local. Two threads serving two tenants cannot
  observe each other's tenant — verified by a regression test that runs two
  threads and asserts they resolve to different databases.
* A soft-deleted tenant stops resolving from a request immediately.
* `subdomain` and `database_name` are validated on every save, and file-backed
  database paths are confined to a configured directory.

## What it does not guarantee

* **Authorization.** The package decides *which database* a request reads. It
  does not decide whether the authenticated user is entitled to that tenant. If
  users can belong to more than one tenant, or a session can outlive a tenant
  switch, you must check that yourself.
* **Isolation from your own code.** Anything using `.using()` explicitly, raw
  SQL, or a connection it opened itself bypasses the router entirely.
* **Cross-tenant leakage through shared caches.** Django's cache, session store
  and rate limiters are not namespaced by tenant. See below.

## Threat model

### Tenant selection via the `X-Tenant` header

**Risk: critical. Disabled by default.**

The header overrides the hostname, and any client can send it:

```http
GET /api/invoices/ HTTP/1.1
Host: acme.example.com
X-Tenant: competitor
```

Trusted unconditionally, this is a direct cross-tenant read/write primitive
reachable without authenticating.

`TENANT_HEADER_ENABLED` defaults to `False`, and turning it on raises
`multiverse.W002` at startup so the decision is visible.

Only enable it behind a reverse proxy that **strips the inbound header and sets
it itself**:

```nginx
proxy_set_header X-Tenant "";              # discard whatever the client sent
proxy_set_header X-Tenant $tenant_from_jwt;
```

Setting it *after* discarding is the part that matters. A proxy that only adds
the header leaves the client's value in place.

### Hostname spoofing

`Host` is also client-controlled. Django's `ALLOWED_HOSTS` is what constrains it,
and this package relies on that — set it to your real domains, never `['*']` in
production.

```python
ALLOWED_HOSTS = ['.example.com']
```

### Path traversal via `database_name`

**Risk: high under SQLite. Mitigated in two layers.**

A SQLite database name is a filesystem path. A tenant named `../../app/settings.py`
would previously have been created with `touch()` and removed with `unlink()`.

1. `multiverse.validators.validate_database_name` rejects `/`, `\`, `..`, spaces,
   quotes and semicolons, and runs on every save — not only in forms.
2. The SQLite provisioner resolves the path and then checks it is inside
   `TENANT_DATABASE_DIRECTORY`, raising `SuspiciousOperation` otherwise.
   Resolution happens *before* the check, so symlinks and `..` are collapsed
   first.

Neither layer is trusted to be the only one.

### SQL injection in provisioning DDL

**Risk: mitigated.** Database names cannot be parameterised in
`CREATE DATABASE`. The PostgreSQL backend uses `psycopg.sql.Identifier`, which
quotes and escapes correctly, and existence checks are properly parameterised.
The name is validated before it gets there as well.

### Tenant enumeration

An unknown subdomain returns 404, the same as an unknown URL. Whether your
tenants' subdomains are discoverable is a product decision — public DNS records
usually make them so.

### Cache, session and file-storage leakage

**Not handled by this package.** These are shared by default and are the most
likely place for a cross-tenant leak in an application built on it.

```python
from django.core.cache import cache
from multiverse.awareness import get_current_tenant

def tenant_cache_key(key):
    tenant = get_current_tenant()
    return f'{tenant.pk}:{key}' if tenant else key
```

The `tenant_changed` signal is the hook for this — it fires on release too, with
`instance=None`:

```python
@receiver(tenant_changed)
def switch_storage_prefix(sender, instance, **kwargs):
    storage.prefix = f'tenants/{instance.pk}/' if instance else 'system/'
```

For sessions, either place the session store in `COMMON_APPS` so each database
holds its own sessions, or namespace the cookie by domain so a session cannot
travel between tenants.

### Background jobs

`multiverse.tasks.django_q` carries only the tenant's primary key through the
broker and re-activates it inside a `tenant_context`, so the worker releases the
tenant when the task ends. A worker that kept it active would run the *next*,
unrelated task against the previous customer's database.

If you enqueue work by another route, activate explicitly and always with the
context manager:

```python
with tenant_context(get_tenant(tenant_id)):
    do_the_work()
```

Treat the broker as trusted infrastructure: a payload naming a dotted path is
imported and called, which is inherent to how django-q works.

### Credentials

The PostgreSQL provisioner uses the **tenant alias'** credentials, not
`default`'s, so tenant databases can live on a different server under a
different role. Grant that role only `CREATEDB` and rights on tenant databases —
it does not need access to the system database.

## Deployment checklist

- [ ] `TENANT_HEADER_ENABLED` is `False`, or the proxy strips and re-sets it
- [ ] `ALLOWED_HOSTS` lists real domains, not `['*']`
- [ ] `DEBUG = False` — the loopback tenant shortcut is `DEBUG`-gated
- [ ] `python manage.py check` reports nothing
- [ ] `TENANT_DATABASE_DIRECTORY` is outside the web root (SQLite only)
- [ ] Caches and file storage are namespaced by tenant
- [ ] The tenant database role cannot read the system database
- [ ] Connection budget checked against `max_connections`

## Reporting a vulnerability

Email dmp593@gmail.com rather than opening a public issue.
