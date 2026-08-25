"""
Router behaviour: which database each model lands in.

Assertions are made against ``_state.db`` — the alias a row was actually read
from or written to — rather than against the router's return value, so a router
that returns the right alias but is never consulted still fails.
"""

from __future__ import annotations

from django.db import DEFAULT_DB_ALIAS
from django.test import TestCase, override_settings

from multiverse.db.router import AppTier, TenantRouter, app_classifier
from multiverse.models import Tenant
from tests.apps.commonapp.models import Country
from tests.apps.systemapp.models import SystemNote
from tests.apps.tenantapp.models import Invoice


class AppClassificationTests(TestCase):
    databases = '__all__'

    def test_apps_are_classified_by_their_tier(self):
        self.assertEqual(app_classifier.tier_for('systemapp'), AppTier.SYSTEM)
        self.assertEqual(app_classifier.tier_for('commonapp'), AppTier.COMMON)
        self.assertEqual(app_classifier.tier_for('tenantapp'), AppTier.TENANT)

    def test_dotted_paths_and_bare_labels_both_resolve(self):
        """
        ``INSTALLED_APPS`` entries are written both ways in real projects, and
        the settings lists mirror them.
        """
        self.assertEqual(app_classifier.tier_for('contenttypes'), AppTier.SYSTEM)

    def test_unlisted_apps_are_reported_rather_than_silently_routed(self):
        with override_settings(TENANT_APPS=[]):
            self.assertIn('tenantapp', app_classifier.unclassified_app_labels())

    def test_an_app_in_two_tiers_is_reported_as_a_conflict(self):
        with override_settings(
            SYSTEM_APPS=['tests.apps.commonapp'],
            COMMON_APPS=['tests.apps.commonapp'],
        ):
            self.assertIn('commonapp', app_classifier.conflicting_app_labels())

    def test_classification_follows_overridden_settings(self):
        """The cached mapping must invalidate, or overrides silently do nothing."""
        self.assertEqual(app_classifier.tier_for('tenantapp'), AppTier.TENANT)

        with override_settings(TENANT_APPS=[], SYSTEM_APPS=['tests.apps.tenantapp']):
            self.assertEqual(app_classifier.tier_for('tenantapp'), AppTier.SYSTEM)

        self.assertEqual(app_classifier.tier_for('tenantapp'), AppTier.TENANT)


class ModelPlacementTests(TestCase):
    databases = '__all__'

    def test_system_models_are_written_to_the_default_database(self):
        note = SystemNote.objects.create(text='hello')

        self.assertEqual(note._state.db, DEFAULT_DB_ALIAS)

    def test_tenant_models_are_written_to_the_tenant_database(self):
        invoice = Invoice.objects.create(reference='INV-1')

        self.assertNotEqual(invoice._state.db, DEFAULT_DB_ALIAS)

    def test_common_models_default_to_the_default_database(self):
        country = Country.objects.create(code='PT')

        self.assertEqual(country._state.db, DEFAULT_DB_ALIAS)

    def test_common_models_can_hold_rows_in_a_tenant_database(self):
        """
        COMMON means the table exists everywhere and each database keeps its own
        rows, so the same model can be written to a tenant database explicitly.
        """
        country = Country.objects.using('tenant').create(code='ES')

        self.assertEqual(country._state.db, 'tenant')

    def test_the_tenant_registry_stays_in_the_default_database(self):
        """
        Without this pin, resolving a tenant would query the tenant's own
        database — a lookup that cannot succeed until it has already succeeded.
        """
        tenant = Tenant.objects.create(subdomain='acme', database_name='db_acme')

        self.assertEqual(tenant._state.db, DEFAULT_DB_ALIAS)

    def test_tenant_registry_is_pinned_even_when_its_app_is_a_tenant_app(self):
        with override_settings(SYSTEM_APPS=[], TENANT_APPS=['multiverse']):
            self.assertEqual(
                TenantRouter().db_for_read(Tenant), DEFAULT_DB_ALIAS
            )


class RelationTests(TestCase):
    databases = '__all__'

    def setUp(self):
        self.router = TenantRouter()

    def test_a_tenant_model_may_relate_to_a_common_model(self):
        """
        ``allow_relation`` compared two raw router results, and a COMMON model
        resolves to ``None`` meaning "no opinion". ``None != 'tenant'`` made
        every foreign key from tenant data into shared reference data fail with
        "the current database router prevents this relation".
        """
        country = Country(code='PT')
        invoice = Invoice(reference='INV-1')

        self.assertTrue(self.router.allow_relation(invoice, country))

    def test_a_foreign_key_into_a_common_model_can_be_saved(self):
        # Seeded into the tenant database, because that is where the invoice
        # referencing it lives. A COMMON row in `default` is a different row
        # from the one a tenant database holds.
        country = Country.objects.using('tenant').create(code='PT')
        invoice = Invoice.objects.create(reference='INV-1', country=country)

        self.assertEqual(invoice.country, country)
        self.assertEqual(invoice._state.db, 'tenant')

    def test_objects_from_the_same_database_are_always_relatable(self):
        first = Country(code='PT')
        second = Country(code='ES')
        first._state.db = second._state.db = DEFAULT_DB_ALIAS

        self.assertTrue(self.router.allow_relation(first, second))

    def test_a_system_model_may_not_relate_to_a_tenant_model(self):
        self.assertFalse(
            self.router.allow_relation(SystemNote(text='x'), Invoice(reference='y'))
        )


class MigrationRoutingTests(TestCase):
    databases = '__all__'

    def setUp(self):
        self.router = TenantRouter()
        self.tenant_alias = 'tenant'

    def test_system_apps_migrate_only_on_default(self):
        self.assertTrue(self.router.allow_migrate(DEFAULT_DB_ALIAS, 'systemapp'))
        self.assertFalse(self.router.allow_migrate(self.tenant_alias, 'systemapp'))

    def test_tenant_apps_migrate_only_on_tenant_databases(self):
        self.assertFalse(self.router.allow_migrate(DEFAULT_DB_ALIAS, 'tenantapp'))
        self.assertTrue(self.router.allow_migrate(self.tenant_alias, 'tenantapp'))

    def test_tenant_apps_migrate_on_derived_aliases_too(self):
        """
        Derived aliases are what tenant databases are actually addressed by, so
        an equality check against the template alias would skip every real
        tenant during `migrate`.
        """
        self.assertTrue(self.router.allow_migrate('tenant::db_acme', 'tenantapp'))

    def test_common_apps_migrate_everywhere(self):
        self.assertTrue(self.router.allow_migrate(DEFAULT_DB_ALIAS, 'commonapp'))
        self.assertTrue(self.router.allow_migrate(self.tenant_alias, 'commonapp'))

    def test_the_tenant_table_is_only_created_in_the_default_database(self):
        self.assertTrue(
            self.router.allow_migrate(DEFAULT_DB_ALIAS, 'multiverse', 'tenant')
        )
        self.assertFalse(
            self.router.allow_migrate(self.tenant_alias, 'multiverse', 'tenant')
        )
