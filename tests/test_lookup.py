"""Finding tenants, and the settings layer that everything reads through."""

from __future__ import annotations

import uuid

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, TestCase, override_settings

from multiverse.conf import multiverse_settings
from multiverse.db.backends.utils import get_tenant_database_alias
from multiverse.models import Tenant
from multiverse.utils import get_tenant, get_tenant_model, is_valid_uuid


class TenantLookupTests(TestCase):
    databases = '__all__'

    def setUp(self):
        self.tenant = Tenant.objects.create(subdomain='acme', database_name='db_acme')

    def test_lookup_by_subdomain(self):
        self.assertEqual(get_tenant('acme'), self.tenant)

    def test_lookup_by_database_name(self):
        self.assertEqual(get_tenant('db_acme'), self.tenant)

    def test_lookup_by_primary_key(self):
        self.assertEqual(get_tenant(self.tenant.pk), self.tenant)

    def test_lookup_by_primary_key_as_a_string(self):
        self.assertEqual(get_tenant(str(self.tenant.pk)), self.tenant)

    def test_lookup_by_uppercase_uuid(self):
        """
        The UUID check compared against `str(UUID(value))`, so any form but
        lowercase-hyphenated was rejected, fell through to the subdomain branch
        and failed as DoesNotExist.
        """
        self.assertEqual(get_tenant(str(self.tenant.pk).upper()), self.tenant)

    def test_lookup_by_unhyphenated_uuid(self):
        self.assertEqual(get_tenant(self.tenant.pk.hex), self.tenant)

    def test_a_deleted_tenant_is_hidden_by_default(self):
        self.tenant.delete()

        with self.assertRaises(Tenant.DoesNotExist):
            get_tenant('acme')

    def test_a_deleted_tenant_can_be_found_explicitly(self):
        self.tenant.delete()

        self.assertEqual(get_tenant('acme', include_deleted=True), self.tenant)

    def test_an_unknown_tenant_raises_does_not_exist(self):
        with self.assertRaises(Tenant.DoesNotExist):
            get_tenant('nobody')


class UuidRecognitionTests(SimpleTestCase):
    def test_every_accepted_uuid_form_is_recognised(self):
        value = uuid.uuid4()
        forms = (
            value,
            str(value),
            str(value).upper(),
            value.hex,
            f'urn:uuid:{value}',
            f'{{{value}}}',
        )

        for form in forms:
            with self.subTest(form=form):
                self.assertTrue(is_valid_uuid(form))

    def test_non_uuids_are_rejected(self):
        for value in ('acme', '', None, 123, 'not-a-uuid'):
            with self.subTest(value=value):
                self.assertFalse(is_valid_uuid(value))


class TenantModelResolutionTests(SimpleTestCase):
    def test_the_configured_model_is_returned(self):
        self.assertIs(get_tenant_model(), Tenant)

    def test_a_missing_setting_is_reported_as_a_configuration_error(self):
        """
        `settings.TENANT_MODEL` raised a bare AttributeError when unset — the
        single most likely mistake produced the least helpful message.
        """
        with override_settings():
            del settings.TENANT_MODEL

            with self.assertRaises(ImproperlyConfigured):
                get_tenant_model()

    @override_settings(TENANT_MODEL='not-a-valid-label')
    def test_a_malformed_setting_is_reported_as_a_configuration_error(self):
        with self.assertRaises(ImproperlyConfigured):
            get_tenant_model()

    @override_settings(TENANT_MODEL='nosuchapp.Tenant')
    def test_an_uninstalled_model_is_reported_as_a_configuration_error(self):
        with self.assertRaises(ImproperlyConfigured):
            get_tenant_model()


class SettingsAccessorTests(SimpleTestCase):
    def test_overrides_take_effect_immediately(self):
        """
        These accessors were decorated with functools.cache, keyed on a value
        the package itself mutated. They froze the first answer they ever gave
        and silently defeated override_settings everywhere downstream.
        """
        self.assertEqual(multiverse_settings.tenant_database_alias, 'tenant')

        with override_settings(TENANT_DATABASE_ALIAS='other'):
            self.assertEqual(multiverse_settings.tenant_database_alias, 'other')
            self.assertEqual(get_tenant_database_alias(), 'other')

        self.assertEqual(multiverse_settings.tenant_database_alias, 'tenant')

    def test_the_base_database_name_comes_from_settings_not_a_connection(self):
        self.assertIsNotNone(multiverse_settings.tenant_database_name)

    def test_the_tenant_header_follows_debug_by_default(self):
        """
        On in development, where it is the only way to reach more than one
        tenant from localhost; off in production, where it is a way for a
        client to pick someone else's database.
        """
        with override_settings(DEBUG=True):
            self.assertTrue(multiverse_settings.tenant_header_enabled)

        with override_settings(DEBUG=False):
            self.assertFalse(multiverse_settings.tenant_header_enabled)

    def test_an_explicit_tenant_header_setting_overrides_debug(self):
        with override_settings(DEBUG=True, TENANT_HEADER_ENABLED=False):
            self.assertFalse(multiverse_settings.tenant_header_enabled)

        with override_settings(DEBUG=False, TENANT_HEADER_ENABLED=True):
            self.assertTrue(multiverse_settings.tenant_header_enabled)
