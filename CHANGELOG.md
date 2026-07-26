# Changelog

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.0.0] — unreleased

Fixes the defects that made earlier releases impossible to install and unsafe to
run under any threaded server. See [docs/UPGRADING.md](docs/UPGRADING.md).

No schema change: `0002` attaches validators and help text only.

### Security

- **The `X-Tenant` header is no longer trusted in production.**
  `TENANT_HEADER_ENABLED` now defaults to `DEBUG`. The header exists so that
  tenants can be switched on `localhost`, where there is no subdomain to route
  on, and it keeps working there unchanged. With `DEBUG` off it was a way for
  any client to override the hostname and name the customer database it wanted
  to read; that now requires opting in, and raises `multiverse.W002`.
- **Cross-tenant leak under threads fixed.** Tenant activation rewrote
  `settings.DATABASES['tenant']['NAME']`, which is process-global while the
  tenant itself was thread-local. Two threads serving two tenants raced and the
  loser queried the winner's database. Each tenant database now gets its own
  connection alias. Covered by a two-thread regression test.
- **The tenant is released at the end of every request.** Nothing deactivated,
  so a pooled worker thread carried one customer's tenant into the next request.
  `SYSTEM_ROUTES` returned early *before* activation, so those ran under
  whatever was left behind; they now run inside `tenant_context(None)`.
- **`forget_current_tenant()` returns to the base database** instead of the
  tenant it was asked to forget — it derived the baseline from the value
  activation had just written.
- **django-q workers release the tenant after each task**, so the next unrelated
  task cannot run against the previous customer's database.
- **Path traversal in `database_name` closed.** SQLite names were passed
  straight to `touch()` and `unlink()`. Names are now validated on save, and
  resolved paths must fall inside `TENANT_DATABASE_DIRECTORY`.
- **Soft-deleted tenants stop resolving** from requests.

### Fixed

- **`ModuleNotFoundError: No module named 'mywise'`.** The base model was
  imported from an unpublished private package, so `django.setup()` failed for
  every install including 1.0.9 on PyPI. Now uses `django-timestampable`.
- **`create_tenant` migrates the new tenant's database.** It migrated whichever
  database the shared alias pointed at, leaving every new tenant empty.
- **Command flags are usable.** Declared `type=bool`, argparse demanded a value
  and passed it through `bool()`: the documented
  `create_tenant acme --create-database --migrate` failed with "expected one
  argument", and no value could express false.
- **PostgreSQL `drop_database_if_exists` issued `CREATE DATABASE IF EXISTS`** —
  not valid SQL under any reading, so the drop always failed. It now issues
  `DROP DATABASE IF EXISTS` and terminates open backends first.
- **`destroy_tenant` drops the database before removing the row**, so a failed
  drop cannot orphan a database nobody can identify.
- **PostgreSQL provisioning uses the tenant alias' credentials**, not
  `default`'s, so tenant databases can live on a different server.
- **Unmatched URLs return 404, not 500.** With `SYSTEM_ROUTES` set, `resolve()`
  ran unguarded in middleware and `Resolver404` escaped as a server error.
- **The tenant registry is pinned to `default`.** It was routed to the tenant
  database unless `multiverse` was in `SYSTEM_APPS`, so resolving a tenant
  queried that tenant's own database.
- **`TENANT_APPS` is honoured.** Documented prominently, read by
  `get_tenant_apps()`, and referenced nowhere.
- **Foreign keys from tenant apps into common apps work.** `allow_relation`
  compared raw router results, and a common app resolves to `None`.
- **`set_request()` stores the request.** It used `getattr` instead of
  `setattr`, so `get_request()` always returned `None`.
- **Settings accessors are no longer `functools.cache`d.** The cache key came
  from state the package itself mutated, so they froze the first answer and
  silently defeated `override_settings` downstream.
- **UUID lookups accept every valid form.** `get_tenant()` compared against
  `str(UUID(value))`, rejecting uppercase and unhyphenated keys.
- **`get_tenant_model()` raises `ImproperlyConfigured` when `TENANT_MODEL` is
  unset**, rather than a bare `AttributeError`.
- **`schedule()` without an active tenant creates a schedule**, not a one-shot
  task.
- **App classification resolves through Django's app registry**, so apps that
  override `AppConfig.label` are matched.
- **The admin registers the tenant model only when it is not swapped out.**
- **The router no longer imports `multiverse.test`**, keeping `django.test` off
  the production import path.
- **Packaging: `packages = [{include = "multiverse"}]`.** The previous glob over
  `*.py` silently dropped every non-Python file.

### Changed

- **BREAKING — tests exercise routing.** `TESTING` routed every model to
  `default`, so downstream suites ran with routing switched off. It now only
  stops per-tenant aliases from being derived.
- **BREAKING — `destroy_tenant` flags split.** `--drop-database` drops the
  database; `--hard` removes the row. One flag used to do both. Dropping now
  prompts unless `--noinput`.
- **BREAKING — SQLite paths are absolute and confined** to
  `TENANT_DATABASE_DIRECTORY`, instead of relative to the working directory.
- **BREAKING — `tenant_aware_func` takes keyword arguments.**
- **BREAKING — `async_task` takes django-q options in `q_options`.**
- `tenant_changed` fires on release as well as activation, with `instance=None`,
  and its sender is always the tenant model.
- `create_tenant` validates input, runs in a transaction, reports conflicts
  clearly, and removes a database it created if a later step fails.
- Commands honour `--verbosity`.
- `multiverse.test.utls` → `multiverse.test.utils`; the old path warns.
- `get_db_utils_module` → `get_tenant_provisioner`.
- Django 5.0–5.2 and Python 3.10–3.13; classifiers now match what is tested.

### Added

- `multiverse.awareness.tenant_context` — the preferred activation API. Nests,
  and releases on any exit path.
- `multiverse.conf.multiverse_settings` — one typed, documented accessor per
  setting.
- Startup checks `multiverse.E001`–`E005` and `W001`–`W002`, turning silent
  misconfiguration into a startup message.
- `multiverse.db.backends.base.DatabaseProvisioner` — an explicit contract
  replacing `hasattr` duck-typing, with `register_provisioner()`.
- `multiverse.validators`.
- Settings `TENANT_DATABASE_DIRECTORY`, `TENANT_PROVISIONING_DATABASE`,
  `TENANT_HEADER_ENABLED`, `TENANT_HEADER_NAME`.
- A 102-test suite over a three-tier example project.
- `py.typed`, ruff configuration, `CONTRIBUTING.md`, and documentation under
  `docs/`.

---

## [1.0.9] — 2024

Published to PyPI but never committed to git. Reconstructed from the wheel.

- Router follows a `relation` hint for common apps.
- `allow_relation` short-circuits when both objects are already in the same
  database.
- `schedule()` without a tenant calls django-q's `schedule`, not `async_task`.
- `tenant_aware_func` takes keyword arguments.

## [1.0.6] – [1.0.8]

Published to PyPI; no corresponding commits.

## [1.0.5] — 2024

- `db_for` returns `None` for common apps.
- Tenant awareness handles `set_current_tenant(None)`.

## [1.0.4] — 2024

- Database router fixes for full dotted app names.

## [1.0.1] — 2024-07-18

- Initial release.
