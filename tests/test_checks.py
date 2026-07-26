"""
Startup checks.

Every check here corresponds to a misconfiguration that previously produced no
error at all — just data quietly written to the wrong database.
"""

from __future__ import annotations

from django.test import SimpleTestCase, override_settings

from multiverse import checks


def run(check, **kwargs) -> set[str]:
    return {message.id for message in check(app_configs=None, **kwargs)}


class ConfigurationCheckTests(SimpleTestCase):
    def test_a_correct_configuration_reports_nothing(self):
        self.assertEqual(run(checks.check_tenant_model), set())
        self.assertEqual(run(checks.check_tenant_database_alias), set())
        self.assertEqual(run(checks.check_router_installed), set())
        self.assertEqual(run(checks.check_tenant_header), set())

    @override_settings(TENANT_DATABASE_ALIAS='default')
    def test_pointing_the_tenant_alias_at_the_system_database_is_an_error(self):
        self.assertIn('multiverse.E002', run(checks.check_tenant_database_alias))

    @override_settings(TENANT_DATABASE_ALIAS='missing')
    def test_a_tenant_alias_absent_from_databases_is_an_error(self):
        self.assertIn('multiverse.E003', run(checks.check_tenant_database_alias))

    @override_settings(DATABASE_ROUTERS=[])
    def test_a_missing_router_is_an_error(self):
        """
        Without the router nothing is routed anywhere: the project starts, serves
        traffic, and writes every tenant's rows into one shared database. The
        README never mentioned DATABASE_ROUTERS at all.
        """
        self.assertIn('multiverse.E004', run(checks.check_router_installed))

    @override_settings(
        SYSTEM_APPS=['tests.apps.commonapp'],
        COMMON_APPS=['tests.apps.commonapp'],
    )
    def test_an_app_in_two_tiers_is_an_error(self):
        self.assertIn('multiverse.E005', run(checks.check_app_classification))

    @override_settings(TENANT_APPS=[])
    def test_an_unclassified_app_is_a_warning(self):
        self.assertIn('multiverse.W001', run(checks.check_app_classification))

    @override_settings(TENANT_HEADER_ENABLED=True)
    def test_enabling_the_tenant_header_is_a_warning(self):
        """
        The header lets any client choose which tenant to be served, so turning
        it on is a decision that should be visible at startup.
        """
        self.assertIn('multiverse.W002', run(checks.check_tenant_header))
