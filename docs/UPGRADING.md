# Upgrading

## 1.x → 2.0

2.0 fixes defects that made 1.x unsafe to run under any threaded server. Some of
those fixes change behaviour you may be relying on. Read the two **Action
required** sections at minimum.

There is **no schema change**. `0002` attaches validators and help text; no
column is added, removed or retyped.

### Action required 1: install `timestamps`

1.x imported its base model from an unpublished private package, so
`pip install django-multiverse` produced something that could not start:

```
ModuleNotFoundError: No module named 'mywise'
```

2.0 uses [django-timestampable](https://pypi.org/project/django-timestampable/),
which is a real dependency and installs automatically. Add its app:

```python
INSTALLED_APPS = [
    # ...
    'timestamps',
    'multiverse',
]
```

The fields are unchanged, so existing tenant rows are untouched.

The soft-delete managers are named by that package:

| 1.x (if you had a working `mywise`) | 2.0 |
| --- | --- |
| `Tenant.objects` | `Tenant.objects` — live tenants only |
| — | `Tenant.objects_deleted` — deleted only |
| — | `Tenant.objects_with_deleted` — everything |

### Action required 2: the `X-Tenant` header is now off

In 1.x the header selected the tenant and outranked the hostname, with no
validation. Any client could send `X-Tenant: <someone-else>` and be served that
customer's database.

It is now **disabled by default**. If you rely on it:

```python
TENANT_HEADER_ENABLED = True
```

Only do this behind a proxy that discards the inbound header and sets it itself:

```nginx
proxy_set_header X-Tenant "";
proxy_set_header X-Tenant $tenant_from_jwt;
```

Enabling it raises `multiverse.W002` at startup, by design. See
[security.md](security.md).

### Breaking: management command flags

The flags were declared `type=bool`, so argparse demanded a value and passed it
through `bool()` — every non-empty string meant `True`, and the documented form
failed outright:

```console
$ python manage.py create_tenant acme --create-database --migrate
error: argument --create-database: expected one argument
```

They are now proper boolean flags. The documented form works, and `--no-` negates:

```bash
python manage.py create_tenant acme --create-database --migrate
python manage.py create_tenant acme --no-migrate
```

`destroy_tenant` also changes. Previously one flag both dropped the database and
hard-deleted the row:

| Intent | 1.x | 2.0 |
| --- | --- | --- |
| Retire, keep everything | `destroy_tenant acme` | `destroy_tenant acme` |
| Retire and drop the database | `destroy_tenant acme --drop-database x` | `destroy_tenant acme --drop-database --noinput` |
| Also remove the row | — | add `--hard` |

Dropping now prompts for confirmation unless `--noinput` is passed. **Update
your scripts** — an unattended `--drop-database` will hang waiting on stdin.

### Breaking: tests now exercise routing

1.x routed *every* model to `default` while `TESTING` was true, so downstream
suites ran with routing switched off. 2.0 keeps routing fully active; `TESTING`
now only stops per-tenant aliases from being derived.

Add to your test settings:

```python
TESTING = True
```

Expect tests that never exercised routing to start doing so. Failures here are
usually real: a model in the wrong tier, or a cross-database foreign key. See
[routing.md](routing.md).

`TenantTestCase` also gains declarative configuration:

```python
class InvoiceTests(TenantTestCase):
    tenant_subdomain = 'acme'          # was: tenant = Tenant(subdomain='acme')
```

### Breaking: SQLite database paths are confined and absolute

1.x called `Path(database_name).touch()` on the raw value, relative to the
current working directory — so the file you got depended on where you ran the
process from, and `../../` escaped anywhere on disk.

Paths are now resolved against `TENANT_DATABASE_DIRECTORY` (default `BASE_DIR`)
and rejected if they land outside it.

If your existing files are not in `BASE_DIR`, point the setting at them:

```python
TENANT_DATABASE_DIRECTORY = BASE_DIR / 'tenant_databases'
```

Names containing `/`, `\`, `..`, spaces or quotes are now rejected on save. If
you have such tenants, rename them and move the files before upgrading.

### Breaking: `settings.DATABASES` is no longer mutated

The central fix. Activating a tenant used to rewrite
`settings.DATABASES['tenant']['NAME']`, which is process-global — so two threads
serving two tenants raced and one read the other's database.

Each tenant database now gets its own connection alias, `tenant::<database_name>`.

This matters if you:

* **read `settings.DATABASES['tenant']['NAME']`** to find the active database.
  Use `multiverse.awareness.get_current_database_alias()`.
* **wrote a custom router** comparing `db == 'tenant'`. Use
  `tenant_connections.is_tenant_alias(db)`.
* **iterate `connections`** — expect derived aliases to appear as tenants are
  activated.

`DATABASES['tenant']` is now a template supplying engine and credentials. Its
`NAME` is only the fallback used when no tenant is active.

### Behaviour changes you probably want

These fix bugs; no action needed unless you depended on the broken behaviour.

* **The tenant is released at the end of a request.** 1.x never deactivated, so a
  pooled worker thread carried one customer's tenant into the next request.
* **`forget_current_tenant()` returns to the base database** instead of the
  tenant it was asked to forget.
* **Unmatched URLs are 404, not 500.** With `SYSTEM_ROUTES` set, 1.x resolved
  every URL in middleware and let `Resolver404` escape as a server error.
* **`create_tenant` migrates the new tenant's database.** 1.x migrated whichever
  database the shared alias pointed at, leaving every new tenant empty.
* **`destroy_tenant --drop-database` works on PostgreSQL.** 1.x issued
  `CREATE DATABASE IF EXISTS`, which is not valid SQL.
* **The tenant registry is pinned to `default`.** 1.x routed it to the tenant
  database unless you knew to add `multiverse` to `SYSTEM_APPS`.
* **`TENANT_APPS` is honoured.** It was documented but unused.
* **Foreign keys from tenant apps into common apps are allowed.**
* **`get_request()` works.** `set_request()` read instead of writing.
* **Settings accessors are not cached,** so `override_settings` works.
* **Soft-deleted tenants stop resolving** from requests.
* **PostgreSQL provisioning uses the tenant alias' credentials**, not
  `default`'s, so tenant databases can live on another server.
* **`schedule()` without an active tenant creates a schedule**, not a one-shot
  task.

### Renamed and removed

| Was | Now |
| --- | --- |
| `multiverse.test.utls` | `multiverse.test.utils` (old path still works, warns) |
| `multiverse.utils.get_db_utils_module` | `multiverse.utils.get_tenant_provisioner` |
| `multiverse.tasks.django_q.tenant_aware_func(tenant_id, func, *a, **kw)` | keyword-only: `(fn, *, tenant_id, fn_args, fn_kwargs)` |

`async_task` now takes django-q's own options in `q_options`:

```python
async_task('reports.build', invoice_id, q_options={'group': 'reports'})
```

`schedule()` is unchanged — scheduler options and task arguments are separated
automatically.

### New

* `multiverse.awareness.tenant_context` — the preferred way to activate.
* `multiverse.conf.multiverse_settings` — one typed accessor for every setting.
* Startup checks `multiverse.E001`–`E005`, `W001`–`W002`.
* `TENANT_DATABASE_DIRECTORY`, `TENANT_PROVISIONING_DATABASE`,
  `TENANT_HEADER_ENABLED`, `TENANT_HEADER_NAME`.
* `py.typed`.

## Upgrade checklist

1. `pip install --upgrade django-multiverse`
2. Add `'timestamps'` to `INSTALLED_APPS`
3. `python manage.py check` — fix every error and read every warning
4. Set `TENANT_HEADER_ENABLED = True` **only** if a proxy sets that header
5. Set `TENANT_DATABASE_DIRECTORY` if SQLite files live outside `BASE_DIR`
6. Update scripts calling `destroy_tenant --drop-database` to pass `--noinput`
7. Add `TESTING = True` to test settings; run your suite and expect real routing
8. `python manage.py migrate` (metadata only)
