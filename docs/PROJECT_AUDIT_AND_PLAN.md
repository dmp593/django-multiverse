# django-multiverse — Deep Audit & Documentation Plan

> **Status: acted on.** Every S0–S3 finding below was fixed in 2.0.0. This
> document is kept as the record of what was wrong and why the design changed;
> see [CHANGELOG.md](../CHANGELOG.md) for what shipped and
> [UPGRADING.md](UPGRADING.md) for the breaking changes.

**Audited revision:** `bb6eb1a` (branch `main`, `pyproject` version `1.0.5`)
**Latest published release:** `1.0.9` on PyPI
**Audit date:** 2026-07-26
**Scope:** every module, every code path, packaging, docs, and project hygiene (~500 LOC of library code).

Findings marked **[VERIFIED]** were reproduced by executing code against Django 5.2.16, not inferred
by reading. Reproduction scripts are described inline so any of them can be re-run.

---

## Table of contents

1. [How the library actually works](#1-how-the-library-actually-works)
2. [Strengths](#2-strengths)
3. [Weaknesses and bugs](#3-weaknesses-and-bugs)
4. [Repository / process findings](#4-repository--process-findings)
5. [Documentation plan](#5-documentation-plan)
6. [Proposed remediation roadmap](#6-proposed-remediation-roadmap)

---

## 1. How the library actually works

### 1.1 The core idea

`django-multiverse` implements **database-per-tenant** multi-tenancy. This is genuinely different
from `django-tenants`, which uses schema-per-tenant inside a single PostgreSQL database. Here, each
tenant gets a physically separate database (a separate SQLite file, or a separate PostgreSQL
database), and the library swings a single Django database alias — `tenant` by default — to point at
whichever tenant's database is currently active.

That last sentence is the whole design, and it is also the source of most of the serious bugs. There
is exactly one `tenant` alias in `settings.DATABASES`. Activating a tenant does not create a new
alias or a new connection object; it **rewrites `settings.DATABASES['tenant']['NAME']` in place** and
closes the current connection so the next query reconnects to the new target.

```
                          ┌──────────────────────────────────┐
   request ──▶ Middleware │ guess tenant from X-Tenant / Host │
                          └────────────────┬─────────────────┘
                                           ▼
                              awareness.set_current_tenant(t)
                                           │
                    ┌──────────────────────┴───────────────────────┐
                    ▼                                              ▼
      thread-local: current_tenant = t          PROCESS-GLOBAL (bug, see §3.2):
      (correct, per-thread)                     settings.DATABASES['tenant']['NAME'] = t.database_name
                                                connections['tenant'].close()
                                           │
                                           ▼
                          ┌──────────────────────────────────┐
   ORM query ──────────▶  │ TenantRouter.db_for_read/write   │
                          │  app in COMMON_APPS → None       │──▶ 'default'
                          │  app in SYSTEM_APPS → 'default'  │──▶ 'default'
                          │  otherwise          → 'tenant'   │──▶ whatever NAME currently points at
                          └──────────────────────────────────┘
```

### 1.2 Module-by-module map

| Module | Responsibility | State |
| --- | --- | --- |
| `multiverse/models.py` | `BaseTenant` (abstract) + `Tenant` (swappable via `TENANT_MODEL`) | **Does not import** — see §3.1 |
| `multiverse/awareness.py` | Thread-local current tenant; activate/deactivate; request holder | Partly broken — §3.2, §3.4, §3.10 |
| `multiverse/middleware.py` | `TenantMiddleware`: resolve tenant per request | Broken — §3.5, §3.11 |
| `multiverse/db/router.py` | `TenantRouter`: routes models to `default` vs `tenant` | Incomplete — §3.12–§3.15 |
| `multiverse/db/backends/utils.py` | `get_tenant_database_alias/name` settings accessors | `@cache` bug — §3.4 |
| `multiverse/db/backends/sqlite3/utils.py` | Create/drop a SQLite file | Path traversal — §3.23 |
| `multiverse/db/backends/postgresql/utils.py` | Create/drop a PG database | Drop is broken — §3.9 |
| `multiverse/utils.py` | Tenant lookup, request→tenant guessing, app classification, DDL dispatch | Mixed |
| `multiverse/management/commands/` | `create_tenant`, `destroy_tenant` | Both broken — §3.7, §3.8 |
| `multiverse/tasks/django_q.py` | Tenant-aware `async_task` / `schedule` | Leaks tenant — §3.6 |
| `multiverse/test/` | `TenantTestCase`, `TenantClient` (+ DRF variants) | Disables the feature under test — §3.17 |
| `multiverse/signals.py` | `tenant_changed` signal | Fires asymmetrically — §3.27 |
| `multiverse/admin.py` | Registers `Tenant` | Breaks when swapped — §3.22 |

### 1.3 Every path through tenant resolution

`utils.guess_tenant_from_request()` has four branches, in priority order:

1. **`X-Tenant` header present** → `Tenant.objects.get(subdomain=<header value>)`. Trusted with no
   validation whatsoever (§3.5).
2. **`DEBUG=True` and host is `127.0.0.1`/`localhost`** → looks up the tenant by
   `database_name=guess_tenant_database_name()`.
3. **Otherwise** → first label of the hostname is treated as the subdomain
   (`acme.example.com` → `acme`).
4. Any miss raises `Http404`.

Note that branch 3 also fires for a bare hostname with no subdomain: `example.com` yields the
subdomain `example`. There is no concept of a "public"/landing tenant.

### 1.4 Every path through the router

`TenantRouter.db_for(mode, model)`:

| Condition | Returns | Effect |
| --- | --- | --- |
| `is_test_environment()` | `'default'` | **All routing disabled** (§3.17) |
| app in `COMMON_APPS` | `None` | Defers to the next router; in practice `default` |
| app in `SYSTEM_APPS` | `'default'` | System database |
| anything else | `'tenant'` | Tenant database — **this is the fallthrough**, §3.12 |

`allow_migrate(db, app_label)`:

| Condition | Returns |
| --- | --- |
| `is_test_environment()` | `True` for every db |
| app in `COMMON_APPS` | `True` for every db (tables created in *all* databases) |
| app in `SYSTEM_APPS` | `db == 'default'` |
| anything else | `db == 'tenant'` |

The asymmetry for `COMMON_APPS` is deliberate but undocumented: common tables are **created in every
database** yet always **read from `default`**, so every tenant database carries a set of empty
duplicate tables. This needs to be either documented as intentional or reconsidered.

`app_label_in_apps()` matches an app label against either the full entry or its last dotted segment,
so `contenttypes` matches `django.contrib.contenttypes`. It cannot distinguish `myproject.billing`
from `vendor.billing`, and it fails entirely for apps that override `AppConfig.label`.

---

## 2. Strengths

These are real and worth preserving through any refactor.

1. **A genuinely differentiated niche.** Database-per-tenant gives hard physical isolation, per-tenant
   backup/restore, per-tenant residency, and the ability to move a noisy tenant to its own host.
   `django-tenants` cannot do any of that. The README's positioning is correct.
2. **Small and legible.** ~500 lines of library code. The entire system can be held in your head,
   which is exactly what you want from something sitting on the data-isolation boundary.
3. **Clean layering.** Awareness, routing, middleware, backend DDL helpers, background tasks, and
   test helpers are properly separated into their own modules with sane boundaries. The structure is
   better than the implementation, which means the fixes are mostly local.
4. **Idiomatic swappable model.** `swappable = "TENANT_MODEL"` plus a `get_tenant_model()` accessor
   mirrors `AUTH_USER_MODEL` / `get_user_model()`. Users can add tenant-specific fields (plan, region,
   billing ref) without forking.
5. **Pluggable backend contract.** `get_db_utils_module()` dispatches on the engine string and the
   helper modules are duck-typed on `create_database_if_not_exists` / `drop_database_if_exists`. Adding
   MySQL support is a single new module — no core changes.
6. **Optional extras done properly.** `postgres`, `drf`, and `django-q2` are real Poetry extras, and
   each subpackage raises an `ImportError` with the exact `pip install` command to fix it. That is
   better ergonomics than most libraries this size.
7. **No SQL injection in the DDL path.** `psycopg.sql.Identifier` is used correctly for database
   names, and the existence check is properly parameterised. This is the easiest place to get
   multi-tenancy wrong and it was got right.
8. **Batteries included.** Shipping `TenantTestCase`, `TenantClient`, DRF variants, and a django-q
   integration is unusually thorough for a library at this maturity.
9. **An extension point exists.** `tenant_changed` gives downstream code a hook for cache namespacing,
   logging context, feature flags, and so on.
10. **A sound conceptual model.** The SYSTEM / COMMON / TENANT three-tier app classification is the
    right mental model for this problem. It is simply not fully implemented yet (§3.13).
11. **Honesty.** The README's "Draft Mode" note sets expectations rather than overselling.

---

## 3. Weaknesses and bugs

Severity key — **S0**: unusable. **S1**: cross-tenant data leakage. **S2**: core feature broken.
**S3**: correctness/design. **S4**: hygiene.

### S0 — The package cannot be installed and used at all

#### 3.1 `models.py` imports a private module that does not exist **[VERIFIED]**

`multiverse/models.py:1`

```python
from mywise.models import BaseModel, models
```

`mywise` is a leftover from the author's private project. It is **not** a declared dependency, **not**
vendored, and **not** on PyPI (`https://pypi.org/pypi/mywise/json` → HTTP 404). Because `multiverse`
is an installed app, Django auto-imports its `models.py` during `django.setup()`:

```
INSTALLED_APPS = ['multiverse']  →  django.setup()
ModuleNotFoundError: No module named 'mywise'
```

**This is true of the currently published `1.0.9` wheel**, which was downloaded and inspected — its
`models.py` is byte-identical on this line, and `mywise` appears nowhere in its `requires_dist`.
Every `pip install django-multiverse` today produces a package that cannot boot.

From `migrations/0001_initial.py` we can reconstruct what `BaseModel` provided: a UUID primary key,
`created_at`, `updated_at`, and `deleted_at` — i.e. timestamps plus soft-delete. `destroy_tenant`
confirms it, calling `tenant.delete(using='default', hard=...)`, a non-standard soft-delete signature.

**Fix:** vendor a small `TimeStampedSoftDeleteModel` into `multiverse/models.py` that reproduces
exactly those four fields, so `0001_initial` still applies cleanly with no new migration. This is the
single highest-value change in the entire audit and unblocks everything else.

---

### S1 — Cross-tenant data leakage

#### 3.2 Tenant activation is process-global, not thread-local **[VERIFIED — reproduced leak]**

`multiverse/awareness.py:20-30`

```python
def set_current_tenant(tenant):
    setattr(__thread_local__, 'current_tenant', tenant)   # thread-local ✅
    alias = get_tenant_database_alias()
    connections[alias].close()
    settings.DATABASES[alias]['NAME'] = tenant.database_name if tenant else ''   # GLOBAL ❌
```

The tenant *object* is stored per-thread, but the database name it resolves to is stored on the
process-global `settings` object. Under any threaded deployment — `runserver` (threaded by default),
`gunicorn --threads`, `uwsgi --threads`, ASGI, or a django-q cluster with worker threads — two
concurrent requests race, and the loser executes its queries against the winner's database.

Reproduced directly against Django 5.2.16 with two threads replicating `set_current_tenant`:

```
thread A: set NAME='tenant_A.sqlite3'  settings_dict NAME='tenant_B.sqlite3'  CONNECTED TO='tenant_B.sqlite3'
          -> *** CROSS-TENANT LEAK ***
thread B: set NAME='tenant_B.sqlite3'  settings_dict NAME='tenant_B.sqlite3'  CONNECTED TO='tenant_B.sqlite3'
          -> OK
```

Thread A asked for tenant A and was physically connected to tenant B's database. `PRAGMA
database_list` confirms the connection target — this is not a settings-reading artefact, it is the
real file.

The code comments acknowledge the hazard ("We shouldn't alter settings at runtime…") and link to the
Django docs, but the link is typo'd — `docs.djangxoproject.com` — in both `set_current_tenant` and
`forget_current_tenant`.

**Fix (architectural, the main body of work):** stop mutating `settings`. The supported approach is
to keep the per-tenant database configuration in a thread-local and register connections dynamically,
e.g. give each tenant its own alias (`tenant::<db_name>`) created on demand in
`connections.databases`, and have the router return that alias. Django's `ConnectionHandler` keeps
`_connections` in a `Local()`, so per-thread wrappers are already isolated once the alias differs.
This is the change that makes the library safe to deploy.

#### 3.3 The middleware never deactivates the tenant

`TenantMiddleware.__call__` calls `set_current_tenant()` on the way in and **nothing** on the way out.
`forget_current_tenant()` exists but has **zero call sites in the entire codebase** (verified by grep).

Consequences on a reused worker thread:

- The tenant and the global `NAME` survive into the next request.
- A request whose route is in `SYSTEM_ROUTES` returns early (line 18-19) **before** setting a tenant,
  so it runs under whatever tenant the previous request left behind.
- An exception raised downstream leaves the tenant pinned indefinitely.

**Fix:** wrap in `try/finally` and always deactivate; also deactivate before the `SYSTEM_ROUTES`
early return.

#### 3.4 `forget_current_tenant()` restores the *previous tenant's* database, not the base one **[VERIFIED]**

`awareness.py:41` resets `NAME` to `guess_tenant_database_name()`, which reads
`settings.DATABASES[alias]['NAME']` — the value `set_current_tenant` just overwrote. So "forgetting"
the tenant is a no-op that leaves you pointed at the tenant you were trying to forget.

```
at boot,                      guess_tenant_database_name() -> 'base.sqlite3'
after serving tenant 'acme',  forget_current_tenant() resets NAME to -> 'tenant_acme.sqlite3'
```

Compounding this, `db/backends/utils.py` decorates both settings accessors with `functools.cache`:

```python
@cache
def get_tenant_database_name(or_default: str = ':memory:'):
    return getattr(settings, 'TENANT_DATABASE_NAME', or_default)
```

The cache key is `or_default`, which is derived from *mutable* runtime state. That means the cache
grows one entry per distinct tenant database name (unbounded), returns a stale answer for repeated
keys, and permanently defeats `override_settings()` in tests.

**Fix:** capture the pristine boot-time database name once at `AppConfig.ready()`, and drop `@cache`
from both settings accessors (they are trivial `getattr` calls; the cache buys nothing and costs
correctness).

#### 3.5 The `X-Tenant` header is trusted unconditionally

`utils.guess_tenant_from_request()` checks the `X-Tenant` header **first**, above the `Host` header,
and accepts it with no allowlist, no signature, and no proxy check. Any client can send

```
GET /api/invoices/  HTTP/1.1
Host: acme.example.com
X-Tenant: competitor
```

and be served the competitor's database. The library provides no per-tenant authorization boundary of
its own, so unless the application independently re-validates the tenant against the authenticated
user, this is a direct cross-tenant read/write primitive reachable by an unauthenticated request.

**Fix:** make header-based resolution opt-in (`MULTIVERSE_TRUST_TENANT_HEADER = False` by default),
make the header name configurable, and document loudly that it must only be enabled when a trusted
reverse proxy strips and re-sets it.

#### 3.6 django-q tasks never release the tenant

`tasks/django_q.py:10-20` — `tenant_aware_func` activates the tenant and returns the user function's
result with no `finally`. A Q cluster worker therefore carries tenant A into the next unrelated task
it picks up, including tasks enqueued with no tenant at all. Combined with §3.2 this is a
cross-tenant write path in background jobs.

**Fix:** `try/finally: forget_current_tenant()`, and prefer a context manager
(`with tenant_context(t):`) as the single activation primitive across middleware, tasks, and commands.

---

### S2 — Core features are broken

#### 3.7 `create_tenant` migrates the wrong database

`management/commands/create_tenant.py:27-31`

```python
if options.get('create_database'):
    create_tenant_database(tenant)     # creates the NEW tenant's database ✅
if options.get('migrate'):
    migrate_tenant_database()          # migrates whatever the alias currently points at ❌
```

`migrate_tenant_database()` is just `call_command('migrate', database=get_tenant_database_alias())`.
The new tenant is never activated, so the alias still points at the boot-time database. The new
tenant's database is created and left **completely empty**, while the base database gets re-migrated.
The headline command in the README does not do what it says.

**Fix:** activate the tenant (ideally via the context manager from §3.6) around the migrate call.

#### 3.8 The command flags cannot be used as documented **[VERIFIED]**

Both commands use `type=bool` for their flags. In argparse that means the flag *requires* a value, and
that value is passed through `bool()` — so every non-empty string is `True` and there is no way to
express false.

The exact command in the README:

```
python manage.py create_tenant acme --database-name acme_db --create-database --migrate
```

fails with:

```
error: argument --create-database: expected one argument
```

and the "working" form does the opposite of what it reads as:

```
--create-database False --migrate False   →  {'create_database': True, 'migrate': True}
```

`destroy_tenant --drop-database` has the same defect with `default=False`.

**Fix:** `action=argparse.BooleanOptionalAction` (gives `--migrate` / `--no-migrate`), then correct
the README.

#### 3.9 PostgreSQL database drop is a copy-paste error

`db/backends/postgresql/utils.py:39-44`

```python
def _database_drop(cursor, name):
    cursor.execute(
        sql.SQL('CREATE DATABASE IF EXISTS {}').format(sql.Identifier(name))
    )
```

It says `CREATE`, not `DROP`, and `CREATE DATABASE IF EXISTS` is not valid PostgreSQL syntax under
any reading — it raises a `SyntaxError` from the server every time.

The blast radius is worse than a failed drop. In `destroy_tenant`, `tenant.delete()` runs **first**,
so the tenant row is already gone when `drop_tenant_database()` raises. You are left with an orphaned
database and no record of which tenant owned it.

Two further problems in the same file:

- `_get_connection()` hardcodes `settings.DATABASES['default']` for USER/PASSWORD/HOST/PORT, so tenant
  databases must live on the *default* server with the *default* credentials — it ignores the tenant
  alias's own connection settings entirely.
- Those are bare `[...]` lookups, so a `DATABASES['default']` without an explicit `PORT` key raises
  `KeyError` rather than defaulting.

**Fix:** `DROP DATABASE IF EXISTS`; read connection parameters from the tenant alias with `.get()`;
terminate open backends (`pg_terminate_backend`) before dropping; reorder `destroy_tenant` to drop the
database before deleting the row, inside a transaction.

#### 3.10 `set_request()` is a no-op — it reads instead of writes

`awareness.py:48-49`

```python
def set_request(request: Any = None):
    return getattr(__thread_local__, 'request', request)   # should be setattr
```

The middleware calls it on every request, so it looks wired up, but nothing is ever stored and
`get_request()` always returns `None`. The entire "current request" feature is dead.

**Fix:** `setattr`, clear it in the same `finally` as the tenant, and decide whether this API should
exist at all (holding a request in a thread-local is a known footgun).

#### 3.11 Every 404 in the application becomes a 500 **[VERIFIED]**

`middleware.py:16` calls `resolve(request.path)` unguarded. For any URL that does not match the
URLconf, `resolve()` raises `Resolver404`:

```
TenantMiddleware line 16 raises: Resolver404 -> every 404 in the app becomes a 500
```

Because this happens in middleware rather than in Django's own resolution step, `Resolver404` is not
converted into a friendly 404 — it propagates as a server error. Every bot scanning for `/wp-admin/`
generates a 500 and an error-tracker alert.

Secondary issues in the same block: `resolver_match.app_name` is `''` for any URL not inside a
namespaced `include()`, so `SYSTEM_ROUTES` only works for namespaced apps and an accidental `''` entry
would match everything. And resolving before the tenant is set makes per-tenant URLconfs impossible.

**Fix:** wrap in `try/except Resolver404` and fall through; match on `resolver_match.namespace` or on
path prefixes; document `SYSTEM_ROUTES`, which currently appears nowhere in the README.

#### 3.12 The `Tenant` table is routed to the tenant database (chicken-and-egg)

`Tenant._meta.app_label` is `multiverse`. Unless the user adds `multiverse` to `SYSTEM_APPS`, the
router's fallthrough sends it to the **tenant** alias. But the tenant table lives in `default` — that
is the whole point of it. So:

- `guess_tenant_from_request()` queries `Tenant` on every request against the tenant database.
- `get_tenant()` in the django-q worker does the same.
- `create_tenant` writes the row wherever the alias currently points.

Nothing in the README tells the user to add `multiverse` to `SYSTEM_APPS`. This is almost certainly
the first wall every new user hits.

**Fix:** special-case the tenant model in the router so it is always pinned to `default`
(independent of user configuration), and add a system check that verifies it.

#### 3.13 `TENANT_APPS` is documented but completely inert

`get_tenant_apps()` is defined in `utils.py:153` and has **zero call sites** in this revision
(verified by grep). The README documents `TENANT_APPS` prominently as one of the three settings. The
router's behaviour is instead a bare `else` — *anything* not explicitly listed in `COMMON_APPS` or
`SYSTEM_APPS` goes to the tenant database, including every third-party app the user forgot to
classify.

So a user who follows the README exactly and puts their apps in `TENANT_APPS` gets correct behaviour
**by accident**, via the fallthrough, and gets silent misrouting for anything they omit.

**Fix:** make `TENANT_APPS` authoritative, and decide explicitly what the fallthrough should be. A
strict mode that raises on unclassified apps would turn a silent data-placement bug into a startup
error. (Note: the published `1.0.9` *does* reference `get_tenant_apps` in the router — see §4.1.)

#### 3.14 `allow_relation` rejects foreign keys into `COMMON_APPS`

`router.py:41-45` — for a common app, `db_for_read` returns `None`, and the final expression is
`db_obj1 and db_obj2 and db_obj1 == db_obj2`. `None` is falsy, so any relation with a common-app model
on either side returns `False`, and Django raises
`ValueError: Cannot assign … the current database router prevents this relation.`

Since `COMMON_APPS` is exactly where shared reference data belongs, this breaks the most natural
schema you would write with this library.

Also in the same method, `hints.get('database')` is dead — Django passes `instance` in relation hints,
never `database`.

**Fix:** treat `None` as "the default alias" before comparing. The published `1.0.9` adds an
`obj1._state.db == obj2._state.db` short-circuit and a `relation` hint that partially addresses
this — that fix is not in git (§4.1).

#### 3.15 `@cache` on settings accessors

Covered in §3.4. Listed separately here because it also silently breaks `override_settings` for any
downstream project's test suite, which is a support burden out of proportion to the micro-optimisation
it was meant to be.

---

### S3 — Correctness and design

#### 3.16 The router imports the test package on the production query path

`db/router.py:3` → `from multiverse.test import is_test_environment` → `multiverse/test/__init__.py`
→ `cases.py` → `from django.test import TestCase`. So `django.test` and `TestCase` are imported into
every production process, and `test/cases.py` executes `get_tenant_model()` at module scope during
that import.

**Fix:** import `is_test_environment` from `multiverse.test.utls` directly, or better, move it out of
the `test` package entirely.

#### 3.17 Test helpers disable the very feature they exist to test

`TenantRouter.db_for` returns `DEFAULT_DB_ALIAS` for everything when `is_test_environment()` is true,
and `allow_migrate` returns `True` for everything. `TenantTestCase.setUpClass` sets that flag.

The consequence: a downstream project using `TenantTestCase` runs its entire suite against a single
database with routing switched off. Every routing bug, every misclassified app, every cross-tenant
leak is invisible in tests and appears only in production. The safety net has a hole exactly where the
risk is.

**Fix:** run tests against real multiple databases (Django's test runner already supports this via
`databases = {'default', 'tenant'}` and per-alias `TEST` settings). Keep an escape hatch, but it must
not be the default.

#### 3.18 Module-scope `get_tenant_model()` calls

`middleware.py:8`, `test/client.py:7`, `test/cases.py:9`, `management/commands/create_tenant.py:5` all
resolve the tenant model at import time. That freezes the model before `override_settings(TENANT_MODEL=…)`
can take effect, and creates app-registry ordering hazards. `middleware.py:8` is additionally **dead** —
the name `Tenant` is never used in that file. `router.py:5` imports `get_current_tenant` and never
uses it either.

**Fix:** resolve lazily inside functions; delete the dead imports.

#### 3.19 `get_tenant()` relies on private Django API and is case-sensitive

`utils.py:136-140` builds `Q(_connector=Q.OR, subdomain=lookup, database_name=lookup)`. `_connector`
is a private keyword argument; the supported form is `Q(subdomain=lookup) | Q(database_name=lookup)`.

`is_valid_uuid()` requires `str(uuid.UUID(val)) == val`, so an uppercase or hyphen-less UUID is
rejected, silently falls through to the subdomain branch, and raises `DoesNotExist`.

`Tenant.objects.get()` can also raise `DoesNotExist` / `MultipleObjectsReturned` straight out of a
management command as an unhandled traceback rather than a `CommandError`.

#### 3.20 `get_tenant_model()` misses the most likely failure

It catches `ValueError` and `LookupError`, but if `TENANT_MODEL` is simply **not set** — the most
common mistake — `settings.TENANT_MODEL` raises `AttributeError`, which escapes as a raw traceback
instead of the intended `ImproperlyConfigured`. Django's swappable machinery also normally expects a
default; here even the built-in model requires the user to write `TENANT_MODEL = 'multiverse.Tenant'`.

The docstring is copy-pasted from `django.contrib.auth`: *"Return the User model that is active in
this project."*

#### 3.21 `admin.py` registers a possibly-swapped-out model

`admin.site.register(Tenant)` imports the concrete `multiverse.Tenant` unconditionally. When a user
swaps `TENANT_MODEL` to their own model, `multiverse.Tenant` is swapped out and admin registration of
it is not meaningful. Should register `get_tenant_model()`, or skip registration when swapped.

#### 3.22 Unvalidated database names — arbitrary file write/delete on SQLite

`db/backends/sqlite3/utils.py` calls `Path(name).touch()` and `Path(name).unlink()` on the raw
`database_name`. The function that would have constrained this — `_sanitize_database_name`, which
forced the file under `settings.BASE_DIR` with a `.sqlite3` suffix — is **commented out** (lines
11-23).

Nothing validates `database_name` at the model level either (`max_length=50`, no validators). Whoever
can create a tenant — a management-command operator, or an admin user via the registered `Tenant`
admin — can therefore touch or unlink an arbitrary path, e.g. `../../../app/settings.py`.

**Fix:** re-enable and finish sanitisation for SQLite; add a strict validator on `database_name`
(`^[a-z][a-z0-9_]{0,49}$`) that also satisfies PostgreSQL identifier rules.

#### 3.23 Tenant creation is not transactional

`create_tenant` does `get_or_create` → create database → migrate, with no transaction and no rollback.
Any failure leaves a committed tenant row pointing at a missing or half-migrated database. Because
both `subdomain` and `database_name` are unique, re-running with a changed value raises a raw
`IntegrityError`.

#### 3.24 Signal contract is asymmetric and untyped

`tenant_changed` fires only from `set_current_tenant`, never from `forget_current_tenant`, so
listeners are told when a tenant is entered but never when it is left — which is precisely when a
cache-namespacing or logging-context listener needs to act. `set_current_tenant(None)` sends the
signal with `sender=NoneType` and sets `NAME = ''`, which yields an obscure connection error on the
next query rather than a clear one.

#### 3.25 `multiverse/test/utls.py` — typo in a public module name

It is reachable as `multiverse.test.utls` and therefore part of the public API surface. Rename to
`utils.py` with a deprecation shim.

#### 3.26 Test helpers mutate global state

`set_test_environment()` writes `settings.TESTING`; `add_allowed_host()` appends to
`settings.ALLOWED_HOSTS` (which fails if the project declares it as a tuple); `tearDownClass` sets
`cls.tenant = None`, destroying a user-declared class attribute for any re-run. `TenantTestCaseMixin.__init__`
flips the global testing flag merely by *constructing* a test case, which happens at collection time
for the whole suite. None of this is safe under `--parallel`.

#### 3.27 Miscellaneous

- `TenantClient` sets `HTTP_HOST` to the bare subdomain, so the test path exercises a different branch
  of `guess_tenant_from_request` than production hostnames do.
- `matches_int_field` only recognises `AutoField` subclasses, missing a plain `IntegerField` primary key.
- Tenant lookup runs an uncached query on **every** request.
- No `py.typed` marker despite partial type hints, so downstream type checkers get nothing.
- No `__version__` exported from `multiverse/__init__.py`.
- Both management commands' `help` strings leak the author's internal project: *"Creates a tenant for
  MyWise"*, *"Destroys a tenant of MyWise"*.

---

## 4. Repository / process findings

### 4.1 Git is four releases behind PyPI, and the published code is not in git

`pyproject.toml` says `1.0.5`; PyPI's latest is **`1.0.9`**. Diffing the published wheel against this
working tree shows `router.py` and `tasks/django_q.py` contain **real fixes that were never
committed**:

- `db_for` gained a `relation` hint so common-app models can resolve toward system or tenant databases.
- `allow_relation` gained an `obj1._state.db == obj2._state.db` short-circuit (partially fixes §3.14).
- `schedule()` with no active tenant now correctly calls `django_q_schedule` instead of
  `django_q_async_task` (a genuine bug fixed upstream but still present in this tree).

Everything else is byte-identical — which also confirms that §3.1, §3.5, §3.8, §3.9, §3.10, §3.11 and
the `@cache` bug are all **live in the latest published release**.

There are no git tags for any release. The practical effect is that contributors cannot reproduce a
release, cannot tell which code is deployed, and any PR is written against stale code.

**Fix:** reconcile the published `1.0.9` source back into git, tag every release retroactively where
possible, and never publish from an uncommitted tree again.

### 4.2 Zero tests

There is no `tests/` directory at all, yet `manage.py:9` sets
`DJANGO_SETTINGS_MODULE = 'tests.settings'`. Running `python manage.py` in a fresh clone fails
immediately. A library that ships test *helpers* and sits on a data-isolation boundary has no tests of
its own.

### 4.3 No CI, no lockfile, no lint config

No `.github/`, no `poetry.lock`, no ruff/flake8/mypy configuration, no `CHANGELOG.md`, no
`CONTRIBUTING.md`, no issue or PR templates.

### 4.4 Packaging

`packages = [{ include = "multiverse/**/*.py" }]` should be `{ include = "multiverse" }`. The wheel
happens to be correct today only because the project contains no non-`.py` package data — the moment
a template, locale, or `py.typed` is added it will be silently dropped.

### 4.5 README defects

| Claim | Reality |
| --- | --- |
| `'django_multiverse.middleware.TenantMiddleware'` | The package is `multiverse`; this path does not exist |
| Never mentions `DATABASE_ROUTERS` | Without it the router never runs — following the README verbatim yields **zero** tenant routing |
| Never mentions `DATABASES['tenant']` | The tenant alias is the central concept of the library |
| Never mentions `TENANT_DATABASE_ALIAS`, `TENANT_DATABASE_NAME`, `SYSTEM_ROUTES`, `TESTING` | All are read from settings by the code |
| `TENANT_APPS` documented as a key setting | Inert in this revision (§3.13) |
| "Support for Django 4.0, 4.1, 4.2, 5.0" | `pyproject` pins `Django = "^5.0"`; 4.x is not installable |
| `create_tenant … --create-database --migrate` | Errors out (§3.8) |
| `django-multiverse = "^1.0.1"` | Current release is `1.0.9` |
| Nothing on `X-Tenant`, subdomain resolution, the DEBUG fallback, test helpers, django-q, or `tenant_changed` | All are shipped features with no documentation |

---

## 5. Documentation plan

The goal is documentation a developer can follow start-to-finish and end up with a **correct and safe**
multi-tenant deployment — which today is not achievable from the README.

### 5.1 `README.md` — rewrite

Kept short and accurate; it is a front door, not a manual.

1. **What it is / how it differs** — database-per-tenant vs schema-per-tenant, with an honest
   comparison table against `django-tenants` (isolation, connection count, migration cost, cross-tenant
   queries, backup granularity).
2. **When *not* to use it** — thousands of small tenants, cross-tenant analytics, connection-pool
   limits.
3. **Status and stability banner** — supported Django/Python matrix that matches `pyproject`, and a
   clear statement of the current threading limitations until §3.2 is fixed.
4. **Install**, including extras.
5. **A complete, copy-pasteable quickstart** that actually works: `INSTALLED_APPS`, `DATABASES` with
   the `tenant` alias, `DATABASE_ROUTERS`, `MIDDLEWARE` (correct path), `TENANT_MODEL`, and app
   classification — with `multiverse` shown in `SYSTEM_APPS` (§3.12).
6. **Creating and destroying tenants** with the corrected flags.
7. **Security note** on the `X-Tenant` header, above the fold.
8. Links into `docs/`, contributing, license, support.

### 5.2 `docs/` — the developer manual

| File | Contents |
| --- | --- |
| `architecture.md` | The alias-swinging model, request lifecycle diagram, connection lifecycle, where state lives and why that matters |
| `configuration.md` | Reference for **every** setting: `TENANT_MODEL`, `TENANT_DATABASE_ALIAS`, `TENANT_DATABASE_NAME`, `SYSTEM_APPS`, `COMMON_APPS`, `TENANT_APPS`, `SYSTEM_ROUTES`, `TESTING` — type, default, effect, and failure mode when wrong |
| `routing.md` | The three-tier app model; the full `db_for` / `allow_migrate` / `allow_relation` decision tables from §1.4; why `COMMON_APPS` tables exist in every database; how to place a new app |
| `tenant-resolution.md` | All four resolution branches, the `X-Tenant` trust model, subdomain/DNS/TLS setup, the DEBUG localhost fallback |
| `api.md` | Public API reference: `awareness`, `utils`, `signals`, the router, the backend-helper contract, with the intended `tenant_context()` manager |
| `management-commands.md` | `create_tenant` / `destroy_tenant`, every flag, exit codes, idempotency, recovery from a partial failure |
| `migrations.md` | Migrating N tenant databases, adding an app, moving an app between tiers, zero-downtime ordering |
| `testing.md` | `TenantTestCase`, `TenantClient`, DRF variants, how to configure real multi-database tests, and an explicit warning about §3.17 |
| `background-tasks.md` | django-q integration, tenant propagation, the worker lifecycle, and what happens with no active tenant |
| `backends.md` | SQLite and PostgreSQL specifics, and how to add a backend (the duck-typed contract) |
| `security.md` | Threat model: header spoofing, thread-safety, database-name validation, connection isolation, what the library does and explicitly does **not** guarantee |
| `deployment.md` | Worker model guidance (why threaded workers are dangerous today), connection limits, pooling, per-tenant backups |
| `limitations.md` | Known gaps, honestly stated |
| `troubleshooting.md` | Symptom → cause → fix, seeded from every bug in §3 |

### 5.3 Supporting files

- `CONTRIBUTING.md` — dev setup, running tests, the release procedure (and the rule from §4.1: never
  publish from an uncommitted tree).
- `CHANGELOG.md` — reconstructed from git history and the PyPI releases, `1.0.1` → `1.0.9`.
- `docs/AUDIT.md` — this document, retained as the rationale for the roadmap.
- Docstrings on every public function, and a `py.typed` marker.

### 5.4 Working example project

`example/` — a runnable two-tenant project. It doubles as living documentation and as the smoke test
that would have caught §3.1 immediately.

---

## 6. Proposed remediation roadmap

Ordered so that each phase leaves the project in a better state than it found it. Phases 0-1 are the
ones that matter; everything after is polish.

### Phase 0 — Make it installable (blocks everything)
- Vendor `BaseModel` into `multiverse/models.py`, matching `0001_initial` field-for-field (§3.1).
- Fix `packages` in `pyproject.toml` (§4.4).
- Add `tests/settings.py` so `manage.py` runs.
- Add a smoke test that boots Django with `INSTALLED_APPS = ['multiverse']`.

**Exit criterion:** `pip install` + `django.setup()` succeeds. Today it does not.

### Phase 1 — Close the isolation holes
- Replace global `settings` mutation with per-tenant aliases registered in `connections.databases`
  (§3.2). This is the largest single change.
- Introduce `tenant_context()` as the one activation primitive; use it in the middleware, the tasks,
  and `create_tenant` (§3.3, §3.6, §3.7).
- Always deactivate in a `finally`, including on the `SYSTEM_ROUTES` early return.
- Capture the pristine database name at `ready()`; delete `@cache` (§3.4).
- Gate the `X-Tenant` header behind opt-in configuration (§3.5).
- Pin the tenant model to `default` in the router (§3.12).
- Validate `database_name`; restore SQLite path sanitisation (§3.22).
- **Regression tests for each**, including the two-thread leak test from §3.2 as a permanent guard.

### Phase 2 — Fix the broken features
- `BooleanOptionalAction` on both commands (§3.8).
- `DROP DATABASE IF EXISTS`, tenant-alias credentials, backend termination, correct ordering in
  `destroy_tenant` (§3.9).
- `setattr` in `set_request`, cleared in `finally` (§3.10).
- Guard `Resolver404` in the middleware (§3.11).
- Make `TENANT_APPS` authoritative; decide the fallthrough policy (§3.13).
- Fix `allow_relation` for `COMMON_APPS` (§3.14).
- Reconcile the `1.0.9` router and django-q fixes back into git (§4.1).

### Phase 3 — Correctness and hygiene
- §3.16 through §3.27: decouple the router from `django.test`, make test helpers exercise real
  routing, lazy model resolution, public `Q` API, `ImproperlyConfigured` on a missing `TENANT_MODEL`,
  swappable-aware admin, transactional tenant creation, symmetric signals, rename `utls.py`.
- Add Django **system checks** so misconfiguration fails loudly at startup: missing `TENANT_MODEL`,
  missing tenant alias in `DATABASES`, router not installed, `multiverse` not in `SYSTEM_APPS`,
  unclassified apps. Most of §3 manifests today as silent data misplacement; checks convert that class
  of bug into a startup error.

### Phase 4 — Documentation
- Everything in §5.

### Phase 5 — Project infrastructure
- CI matrix across supported Python × Django × {SQLite, PostgreSQL}.
- `poetry.lock`, ruff, mypy, coverage gate.
- `CHANGELOG.md`, `CONTRIBUTING.md`, issue/PR templates, release tags.
- Align the `pyproject` classifiers with what is actually supported and tested.

---

## Bottom line

The design is sound and the niche is real — physical database-per-tenant isolation is something the
dominant alternative genuinely cannot offer, and the module boundaries here are good enough that most
fixes are local rather than structural.

But the library as published cannot start (§3.1), and the one guarantee a multi-tenancy library exists
to provide — that tenant A never sees tenant B's data — does not hold under threads, which was
reproduced rather than theorised (§3.2). Both are fixable, and Phases 0 and 1 are where essentially all
the value is. The documentation work in Phase 4 should follow the fixes rather than precede them:
documenting the current behaviour accurately would mostly mean documenting the bugs.
