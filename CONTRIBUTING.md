# Contributing

## Development setup

```bash
git clone https://github.com/dmp593/django-multiverse
cd django-multiverse

python -m venv .venv && source .venv/bin/activate
pip install -e ".[all]"
pip install ruff
```

## Running the checks

```bash
python manage.py test tests      # the suite
ruff check multiverse tests      # lint
python manage.py check           # the package's own startup checks
```

Install with `.[all]`. Tests for the optional integrations are decorated
`skipUnless(<dependency> installed)`, so a partial install turns real coverage
into a silent skip rather than a failure. The count printed at the end tells you
which you got — the full suite is 151 tests.

`tests/settings.py` doubles as a worked example of a correctly configured
project. It defines three apps — one per tier — so routing is exercised for
real rather than asserted against the router in isolation.

## What a change needs

**A test that fails before the fix.** For a bug, the test should reproduce the
actual failure, not the shape of the fix. The suite is full of examples: the
cross-tenant leak is caught by running two threads and asserting they resolve to
different databases, not by inspecting the registry.

**A comment explaining *why*, where the reason is not obvious.** Most comments in
this codebase record what went wrong before, because that is the thing a future
reader cannot reconstruct.

**A `CHANGELOG.md` entry**, under Added / Changed / Fixed / Security. Mark
anything that changes existing behaviour **BREAKING** and add it to
`docs/UPGRADING.md`.

## Things to be careful with

**Isolation is the product.** Any change to `db/connections.py`, `awareness.py`
or `db/router.py` can reintroduce cross-tenant leakage. The rule is: no shared
mutable state on the activation path. If you find yourself writing to something
process-global, that is the bug.

**Never mutate `settings` at runtime.** That was the original defect. Settings
are read through `multiverse/conf.py` and resolved on every access.

**Do not cache settings lookups.** `functools.cache` on a settings accessor
froze the first value and silently defeated `override_settings` in every
downstream test suite. Caching a `getattr` buys nothing.

**Validate anything that leaves the ORM.** `database_name` becomes a filesystem
path and a SQL identifier; `subdomain` arrives from a `Host` header. Both are
validated at the model boundary *and* at the point of use. Keep both layers.

**Prefer a startup check over a runtime surprise.** Most ways of misconfiguring
this package used to produce no error at all — just data in the wrong database.
If you add a setting that can be got wrong, add a check in `checks.py`.

## Adding a database backend

Subclass `DatabaseProvisioner`, implement `create_if_not_exists` and
`drop_if_exists`, and register it:

```python
register_provisioner('mysql', 'mypackage.provisioners.MySQLProvisioner')
```

Override `connection_name()` if your engine addresses databases by something
other than the name. See [docs/architecture.md](docs/architecture.md).

## Releasing

The source of truth is git. Earlier releases were published from an uncommitted
tree, so 1.0.6–1.0.9 exist on PyPI with no corresponding commits and no tags —
nobody could reproduce a release or tell which code was deployed.

1. Everything is committed and pushed.
2. `python manage.py test tests` and `ruff check` pass.
3. Bump `version` in `pyproject.toml` and `__version__` in
   `multiverse/__init__.py`.
4. Move the `unreleased` heading in `CHANGELOG.md` to the version and date it.
5. Commit, then tag: `git tag -a v2.0.0 -m "v2.0.0" && git push --tags`.
6. `poetry build && poetry publish`.

Never publish from a tree with uncommitted changes.

## Reporting bugs

Include the Django and Python versions, the database engine, your
`SYSTEM_APPS` / `COMMON_APPS` / `TENANT_APPS`, and the output of
`python manage.py check`.

For anything with security impact, email dmp593@gmail.com rather than opening a
public issue.
