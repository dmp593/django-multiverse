"""
The core must not know about any particular database engine.

Provisioning is the only operation Django's ORM does not abstract, so it is the
one place engine knowledge legitimately lives. Everywhere else, adding an engine
must be a matter of writing a new provisioner and registering it — never of
editing shared code.

These tests exist because that property is easy to erode one convenience at a
time, and nothing else in the suite would notice.
"""

from __future__ import annotations

import ast
import inspect
import re

from django.db import connections
from django.test import SimpleTestCase

from multiverse import awareness, checks, conf, middleware, models, utils
from multiverse.db import connections as connections_module
from multiverse.db import router
from multiverse.db.backends import base
from multiverse.db.backends.base import (
    DatabaseProvisioner,
    get_provisioner,
    register_provisioner,
)
from multiverse.db.connections import tenant_connections
from tests.support import DerivesTenantAliases

#: Modules that must stay engine-neutral. `db.backends` is deliberately absent:
#: that package is where engine knowledge belongs.
ENGINE_NEUTRAL_MODULES = (
    conf,
    awareness,
    models,
    utils,
    middleware,
    checks,
    router,
    connections_module,
    base,
)

ENGINE_NAMES = re.compile(
    r'sqlite|postgres|psycopg|mysql|mariadb|oracle|mssql', re.IGNORECASE
)

#: An engine this package has never heard of.
FAKE_ENGINE = 'vendor.db.backends.fakedb'
BASE_ALIAS = 'tenant'


def executable_source(module) -> str:
    """
    Module source with docstrings and comments stripped.

    The invariant is about what the code *does*, not what the prose explains.
    Naming an engine while documenting where its settings moved to is exactly
    the kind of comment that should be encouraged, so scanning raw source would
    punish good documentation.

    String *literals* are deliberately kept — ``DEFAULT_PROVISIONING_DATABASE =
    'postgres'`` is precisely the kind of coupling this is looking for.
    """
    source = inspect.getsource(module)
    prose_lines: set[int] = set()

    for node in ast.walk(ast.parse(source)):
        if not isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            continue

        if ast.get_docstring(node, clean=False) is not None:
            docstring_node = node.body[0]
            prose_lines.update(
                range(docstring_node.lineno, docstring_node.end_lineno + 1)
            )

    return '\n'.join(
        line.split('#', 1)[0]
        for number, line in enumerate(source.splitlines(), start=1)
        if number not in prose_lines
    )


class CoreIsEngineNeutralTests(SimpleTestCase):
    def test_no_core_module_names_a_database_engine(self):
        """
        A guard against erosion. `TENANT_PROVISIONING_DATABASE` defaulting to
        'postgres' once lived in `conf.py`, which meant adding an engine
        required editing the engine-neutral core — exactly what the provisioner
        abstraction exists to avoid.

        `db/backends/base.py` is allowed the engine markers in its registry:
        mapping a marker to a dotted path is how dispatch works, and the modules
        behind those paths are imported lazily.
        """
        offenders = {}

        for module in ENGINE_NEUTRAL_MODULES:
            source = executable_source(module)

            if module is base:
                # Strip the dispatch table, the one legitimate place a marker
                # appears outside a backend package.
                source = source.split('_PROVISIONERS')[0]

            found = set(ENGINE_NAMES.findall(source))

            if found:
                offenders[module.__name__] = sorted(found)

        self.assertEqual(offenders, {})

    def test_no_core_module_imports_a_database_driver(self):
        for module in ENGINE_NEUTRAL_MODULES:
            source = executable_source(module)

            with self.subTest(module=module.__name__):
                self.assertNotIn('import psycopg', source)


class FakeProvisioner(DatabaseProvisioner):
    """A third-party backend, written without touching this package."""

    created: list[str] = []
    dropped: list[str] = []

    def create_if_not_exists(self, database_name):
        self.created.append(database_name)
        return database_name, True

    def drop_if_exists(self, database_name):
        self.dropped.append(database_name)
        return database_name, True

    def connection_name(self, database_name):
        return f'fake::{database_name}'


class ThirdPartyBackendTests(DerivesTenantAliases, SimpleTestCase):
    """
    Registering an engine this package has never heard of must be enough.

    If any of these fail, supporting a new engine requires a change inside the
    package rather than outside it.
    """

    def setUp(self):
        super().setUp()

        FakeProvisioner.created = []
        FakeProvisioner.dropped = []

        register_provisioner(
            'fakedb', 'tests.test_backend_agnosticism.FakeProvisioner'
        )
        self.addCleanup(base._PROVISIONERS.pop, 'fakedb', None)

        # `connections.settings` is patched directly rather than via
        # `override_settings(DATABASES=...)`. `ConnectionHandler.settings` is a
        # cached_property with no invalidating receiver, so a DATABASES override
        # never reaches it — which is exactly why Django warns that overriding
        # DATABASES leads to unexpected behaviour.
        original = connections.settings[BASE_ALIAS]
        connections.settings[BASE_ALIAS] = {
            **original,
            'ENGINE': FAKE_ENGINE,
            'NAME': 'base',
        }
        self.addCleanup(connections.settings.__setitem__, BASE_ALIAS, original)

    def test_a_registered_engine_is_dispatched_to(self):
        self.assertIsInstance(get_provisioner('tenant'), FakeProvisioner)

    def test_the_backend_decides_how_databases_are_addressed(self):
        """
        `connection_name` is the hook that lets an engine name databases its own
        way — a resolved file path for SQLite, something else entirely here.
        """
        alias = self.alias_for('acme')

        self.assertEqual(connections.settings[alias]['NAME'], 'fake::acme')

    def test_the_derived_alias_inherits_the_registered_engine(self):
        alias = self.alias_for('acme')

        self.assertEqual(connections.settings[alias]['ENGINE'], FAKE_ENGINE)

    def test_provisioning_is_delegated_to_the_backend(self):
        tenant = models.Tenant(subdomain='acme', database_name='acme')

        utils.create_tenant_database(tenant)
        utils.drop_tenant_database(tenant)

        self.assertEqual(FakeProvisioner.created, ['acme'])
        self.assertEqual(FakeProvisioner.dropped, ['acme'])

    def test_routing_is_unaffected_by_the_engine(self):
        """Routing decisions are made on app tiers, never on the engine."""
        from tests.apps.tenantapp.models import Invoice

        self.assertTrue(
            tenant_connections.is_tenant_alias(
                router.TenantRouter().db_for_read(Invoice)
            )
        )


class UnknownEngineTests(DerivesTenantAliases, SimpleTestCase):
    def setUp(self):
        super().setUp()

        original = connections.settings[BASE_ALIAS]
        connections.settings[BASE_ALIAS] = {
            **original,
            'ENGINE': 'vendor.db.backends.unregistered',
        }
        self.addCleanup(connections.settings.__setitem__, BASE_ALIAS, original)

    def test_an_unregistered_engine_degrades_rather_than_breaks(self):
        """
        An engine with no provisioner is a legitimate configuration: the
        databases may be created out of band by a DBA or by infrastructure code.
        Everything except provisioning must keep working.
        """
        self.assertIsNone(get_provisioner(BASE_ALIAS))
        self.assertEqual(
            self.alias_for('acme'),
            f'{BASE_ALIAS}{connections_module.ALIAS_SEPARATOR}acme',
        )
