"""
Decides which database each model lives in.

Every app belongs to exactly one of three tiers:

``SYSTEM``
    Tables exist only in ``default``. The tenant registry itself, billing,
    sign-up — anything that must be readable before a tenant is known.

``COMMON``
    Tables are created in *every* database, and each database holds its own
    rows. The router expresses no opinion for these models, which is Django's
    signal to follow the related object's database and fall back to ``default``
    when there is none.

    ``django.contrib.contenttypes`` is the canonical example: every database
    needs the table, and each copy must describe the models in *that* database.
    A row is not shared between databases — seed the ones you need wherever you
    need them.

``TENANT``
    Tables exist only in tenant databases. Customer data.

Apps that appear in none of the three lists are routed to the tenant database,
which is what earlier releases did and is almost always what the author meant.
Because "almost always" is not "always", :mod:`multiverse.checks` reports them at
startup rather than letting a misfiled app quietly write to the wrong database.
"""

from __future__ import annotations

import enum

from django.apps import apps as django_apps
from django.core.signals import setting_changed
from django.db import DEFAULT_DB_ALIAS

from multiverse.awareness import get_current_database_alias
from multiverse.conf import multiverse_settings
from multiverse.db.connections import tenant_connections
from multiverse.utils import get_tenant_model


class AppTier(enum.Enum):
    """Which database family an app's tables belong to."""

    SYSTEM = 'system'
    COMMON = 'common'
    TENANT = 'tenant'
    UNCLASSIFIED = 'unclassified'


class AppClassifier:
    """
    Resolves an app label to its tier.

    Entries in ``SYSTEM_APPS`` / ``COMMON_APPS`` / ``TENANT_APPS`` may be written
    either as a dotted path (``django.contrib.contenttypes``) or as an app label
    (``contenttypes``), because both forms appear in real ``INSTALLED_APPS``.
    Resolution goes through Django's app registry so that an app which overrides
    ``AppConfig.label`` is matched correctly — the previous "last dotted segment"
    heuristic silently failed for those and misrouted their tables.
    """

    #: Changing any of these invalidates the resolved mapping.
    _INVALIDATING_SETTINGS = frozenset({
        'INSTALLED_APPS',
        'SYSTEM_APPS',
        'COMMON_APPS',
        'TENANT_APPS',
    })

    def __init__(self) -> None:
        self._tiers: dict[str, AppTier] | None = None
        setting_changed.connect(self._invalidate_on_setting_changed)

    def tier_for(self, app_label: str) -> AppTier:
        return self._resolved_tiers().get(app_label, AppTier.UNCLASSIFIED)

    def unclassified_app_labels(self) -> list[str]:
        """Installed apps that no tier claims. Used by the startup checks."""
        tiers = self._resolved_tiers()

        return sorted(
            config.label
            for config in django_apps.get_app_configs()
            if config.label not in tiers
        )

    def conflicting_app_labels(self) -> dict[str, list[str]]:
        """
        Apps claimed by more than one tier, mapped to the tiers claiming them.

        A conflict makes routing depend on the order the lists happen to be
        evaluated in, which is exactly the kind of invisible coupling that
        produces "it works on my machine" data placement.
        """
        claims: dict[str, list[str]] = {}

        for tier, entries in self._tier_entries():
            for entry in entries:
                claims.setdefault(self._resolve_label(entry), []).append(tier.value)

        return {label: tiers for label, tiers in claims.items() if len(tiers) > 1}

    def invalidate(self) -> None:
        self._tiers = None

    def _resolved_tiers(self) -> dict[str, AppTier]:
        if self._tiers is not None:
            return self._tiers

        tiers = {
            self._resolve_label(entry): tier
            for tier, entries in self._tier_entries()
            for entry in entries
        }

        # Only memoise once the app registry can answer authoritatively.
        # Caching a mapping built from the fallback heuristic would let one
        # early call poison every later one.
        if django_apps.apps_ready:
            self._tiers = tiers

        return tiers

    @staticmethod
    def _tier_entries() -> tuple[tuple[AppTier, list[str]], ...]:
        return (
            (AppTier.SYSTEM, multiverse_settings.system_apps),
            (AppTier.COMMON, multiverse_settings.common_apps),
            (AppTier.TENANT, multiverse_settings.tenant_apps),
        )

    @staticmethod
    def _resolve_label(entry: str) -> str:
        if django_apps.apps_ready:
            for config in django_apps.get_app_configs():
                if entry in (config.name, config.label):
                    return config.label

        return entry.rsplit('.', 1)[-1]

    def _invalidate_on_setting_changed(self, setting: str, **kwargs) -> None:
        if setting in self._INVALIDATING_SETTINGS:
            self.invalidate()


#: Shared classifier. Stateless apart from a memo that invalidates itself.
app_classifier = AppClassifier()


class TenantRouter:
    """Django database router implementing the three-tier model."""

    def db_for(self, mode: str, model, **hints) -> str | None:
        """
        Resolve ``model`` to a database alias, or ``None`` to defer.

        ``mode`` is ``'read'`` or ``'write'``. It is unused here — both go to the
        same database — but is passed through so a subclass can send reads to a
        replica without reimplementing the tier logic.
        """
        # The tenant registry is the map from a request to a database, so it has
        # to be readable before any tenant is known. It is pinned to `default`
        # regardless of how its app is classified; without this, the lookup that
        # resolves a tenant would itself be routed to that tenant's database.
        if self._is_tenant_model(model):
            return DEFAULT_DB_ALIAS

        tier = app_classifier.tier_for(model._meta.app_label)

        if tier is AppTier.SYSTEM:
            return DEFAULT_DB_ALIAS

        if tier is AppTier.COMMON:
            return self._db_for_common(hints)

        return get_current_database_alias()

    def db_for_read(self, model, **hints) -> str | None:
        return self.db_for('read', model, **hints)

    def db_for_write(self, model, **hints) -> str | None:
        return self.db_for('write', model, **hints)

    def allow_relation(self, obj1, obj2, **hints) -> bool:
        # Two objects already loaded from the same database are relatable by
        # definition, whatever the tier rules say. Checking this first also
        # keeps relations working inside a tenant database for COMMON models,
        # whose tables exist there too.
        if obj1._state.db and obj1._state.db == obj2._state.db:
            return True

        return self._resolve_alias(obj1, obj2) == self._resolve_alias(obj2, obj1)

    def allow_migrate(self, db: str, app_label: str, model_name=None, **hints) -> bool:
        if self._is_tenant_model_label(app_label, model_name):
            return db == DEFAULT_DB_ALIAS

        tier = app_classifier.tier_for(app_label)

        # COMMON tables are created everywhere so each database can hold its own
        # rows and tenant tables can carry foreign keys into a local copy.
        if tier is AppTier.COMMON:
            return True

        if tier is AppTier.SYSTEM:
            return db == DEFAULT_DB_ALIAS

        return tenant_connections.is_tenant_alias(db)

    def _db_for_common(self, hints: dict) -> str | None:
        """
        Route a COMMON model, following its counterpart when reached via a relation.

        A COMMON row read on its own comes from ``default``. The same row reached
        through a foreign key from a tenant model has to come from the tenant
        database, because that is where the row the key points at actually lives.
        """
        related = hints.get('relation')

        if related is None:
            return None

        related_tier = app_classifier.tier_for(related._meta.app_label)

        if related_tier is AppTier.SYSTEM:
            return DEFAULT_DB_ALIAS

        if related_tier is AppTier.COMMON:
            return None

        return get_current_database_alias()

    def _resolve_alias(self, obj, related) -> str:
        # `None` means "no opinion", which resolves to the default database.
        # Comparing raw `None`s is what previously made every relation into a
        # COMMON app fail with "the current database router prevents this".
        return self.db_for_read(obj, relation=related) or DEFAULT_DB_ALIAS

    @staticmethod
    def _is_tenant_model(model) -> bool:
        return model._meta.label_lower == get_tenant_model()._meta.label_lower

    @staticmethod
    def _is_tenant_model_label(app_label: str, model_name: str | None) -> bool:
        if model_name is None:
            return False

        tenant_label = get_tenant_model()._meta.label_lower
        return f'{app_label}.{model_name}'.lower() == tenant_label
