# Upstream work required: django-timestampable

This package depends on [django-timestampable](https://github.com/xgeekshq/django-timestampable).
**Version 1.1.6 on PyPI cannot be installed on a current toolchain**, so
django-multiverse 2.0.0 cannot ship until a fixed release exists.

This document is the specification for that work. It was implemented and
verified against Django 4.2, 5.2 and 6.0 (56 tests, from 39), but the session
that produced it could not push to `xgeekshq` and its working copy was lost when
the container was reclaimed. Everything below is reproducible from this
description.

Target: branch off `develop`, PR into `develop` (the repo's existing flow).

---

## 1. Packaging — the blocker

There is no `pyproject.toml`, so pip falls back to the legacy
`setup.py bdist_wheel` path, which routes through a distutils command removed
from modern setuptools:

```
AttributeError: install_layout. Did you mean: 'install_platlib'?
ERROR: Could not build wheels for django-timestampable
```

Replace `setup.py`, `setup.cfg` and `MANIFEST.in` with PEP 621 metadata:

```toml
[build-system]
requires = ["setuptools>=77"]
build-backend = "setuptools.build_meta"

[project]
name = "django-timestampable"
version = "1.2.0"
description = "Timestamps and Soft Delete Patterns in Django Models"
readme = "README.md"
requires-python = ">=3.10"
license = "MIT"
license-files = ["LICENSE"]
authors = [{ name = "Daniel Pinto", email = "dmp593@gmail.com" }]
dependencies = ["Django>=4.2"]

[project.optional-dependencies]
drf = ["djangorestframework>=3.12"]

[tool.setuptools]
packages = ["timestamps", "timestamps.drf"]
```

Classifiers should list only the Django versions CI runs (4.2, 5.2, 6.0). The
previous `setup.cfg` advertised Django 6.0 and Python 3.14 with no CI at all.

Verify by building a wheel *and* an sdist and installing both into a clean
Python 3.13 environment. Django 6.0 requires Python 3.12+.

## 2. Remove `aspectlib` and the monkey-patch it exists for

`timestamps/drf/mixins.py` ends with:

```python
aspectlib.weave(target=GenericAPIView.get_queryset, aspects=__get_queryset)
```

This replaces `GenericAPIView.get_queryset` **process-wide**. Two measured
consequences:

1. DRF's schema generators call `get_queryset()` with `view.request = None`
   (`rest_framework/schemas/generators.py`). The aspect reaches for
   `request.query_params` and raises `AttributeError`, so `generateschema`,
   drf-spectacular and drf-yasg fail for **every** view in any project that
   merely imports `timestamps.drf` — including views with no connection to soft
   deletion. Reproduced on a vanilla `viewsets.ModelViewSet`.
2. `aspectlib` is a *required* dependency used exclusively by the optional DRF
   subpackage. Last released October 2022, no classifier above Python 3.10.

Replace with an ordinary DRF override: a `SoftDeleteQuerysetMixin` whose
`get_queryset()` calls `super().get_queryset()` and applies the same
action-to-mixin mapping the aspect did. Every public mixin
(`ListDeletedModelMixin`, `RestoreModelMixin`, `DestroyModelMixin`, …) inherits
from it. `timestamps/drf/viewsets.py::ModelViewSet` already lists all of them
before `viewsets.ModelViewSet`, so the mixin lands above `GenericAPIView` in the
MRO. No public API changes.

Then harden `timestamps/drf/utils.py`:

```python
def is_hard_delete_request(view) -> bool:
    request = getattr(view, 'request', None)
    if request is None:
        return False          # introspection: there is no ?permanent
    ...
```

Needed even after the weave is gone, or this package's *own* viewsets still
crash during schema generation.

## 3. `timestamps/drf/routers.py` rewrites DRF's own router

The route inserts run against `routers.DefaultRouter.routes` itself, in a class
body. Measured:

```
DRF DefaultRouter.routes before: 4   mapping {'get': 'list', 'post': 'create'}
DRF DefaultRouter.routes after : 10  mapping {'get': 'list_deleted'}
```

So importing the module changes every plain DRF router in the project and
remaps `DELETE` on every list endpoint to `bulk_destroy`. Build the subclass's
route table from `copy.deepcopy(routers.DefaultRouter.routes)` and assign it to
`DefaultRouter.routes`; leave the parent untouched. Same ten routes, same order.

## 4. Managers discard the database and hints

`SoftDeleteManager.get_queryset()` returns `SoftDeleteQuerySet(self.model)`,
dropping `_db` and `_hints`, so `Model.objects.db_manager(alias)` is silently
ignored. Mirror Django:

```python
queryset = SoftDeleteQuerySet(model=self.model, using=self._db, hints=self._hints)
```

Matters directly for django-multiverse, which is multi-database.

## 5. `delete(using=...)` writes somewhere else

`SoftDeletes.delete()` computes `using`, reports it to `pre_soft_delete` and
`post_soft_delete`, then calls `self.save()` with **no** alias — so the router
picks again and the row can land in one database while both signals name
another. Pass it: `self.save(using=using)`. Give `restore()` the same
`using=None` parameter and send it to its signals too.

## 6. Mutating methods lack `alters_data`

`{{ object.delete }}` in a template silently deletes the row being rendered.
Django marks its own writers; match it:

```python
SoftDeletes.delete.alters_data = True
SoftDeletes.soft_delete.alters_data = True
SoftDeletes.hard_delete.alters_data = True
SoftDeletes.restore.alters_data = True
```

## 7. Widening a queryset removes the wrong clause

`_remove_clause_deleted_at` finds the soft-delete restriction by field **name**
alone and stops at the first `deleted_at IS NULL`. A filter across a relation to
another soft-deletable model adds a second one. Measured on `Bar` → `Foo`:

```
Bar.objects_with_deleted.filter(foo__deleted_at__isnull=True)
  before [Foo.deleted_at]                 removed Foo.deleted_at   <- developer's filter
Bar.objects_with_deleted.filter(...).without_deleted()
  before [Foo.deleted_at, Bar.deleted_at] removed Foo.deleted_at   <- wrong one
Bar.objects.filter(foo__deleted_at__isnull=True)
  before [Bar.deleted_at, Foo.deleted_at] removed Bar.deleted_at   <- correct, by luck
```

So `/with-deleted/` dropped the restriction the developer asked for and kept the
one it exists to remove — depending purely on clause order. Anchor the match to
the queryset's base table:

```python
base_alias = queryset.query.get_initial_alias()
... and child.lhs.field.name == 'deleted_at' and child.lhs.alias == base_alias
```

When nothing matches, remove nothing rather than the wrong thing.

## 8. Housekeeping

- `tests/settings.py` sets `USE_L10N`, removed in Django 5.0.
- `tests/settings.py` needs a **second database alias**, or every routing bug
  looks like correct behaviour and items 4 and 5 are untestable.
- `Makefile` calls the removed `setup.py sdist`; use `python -m build`.
- `requirements.txt` mixes the package's dependencies with the dev environment,
  and lists `django-fake-model`, which is unused.
- No CI. Add Django 4.2/5.2/6.0 x Python 3.10–3.13 (excluding 6.0 below 3.12 and
  4.2 on 3.13), plus a job that installs the built wheel into a clean
  environment and imports it.

## Verifying

Every fix above should have a regression test that **fails against the current
code**. Confirm by reverting the fix and watching the test go red — several of
these are invisible with a single database or a single import order.

```bash
python manage.py test tests   # under each of Django 4.2, 5.2, 6.0
```
