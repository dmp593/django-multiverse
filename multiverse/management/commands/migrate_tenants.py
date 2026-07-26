"""Run migrations across every tenant database."""

from __future__ import annotations

from argparse import BooleanOptionalAction

from django.core.management.base import BaseCommand, CommandError

from multiverse.awareness import tenant_context
from multiverse.utils import (
    get_tenant,
    get_tenant_model,
    get_tenant_queryset,
    migrate_tenant_database,
)


class Command(BaseCommand):
    help = (
        'Run migrations against every tenant database. Run the ordinary '
        '`migrate` first for the system database.'
    )

    def add_arguments(self, parser):
        # Mirrors `migrate`'s own positional arguments so that targeting a
        # single app or a single migration reads identically across the two.
        parser.add_argument(
            'app_label',
            nargs='?',
            help='App to migrate. Defaults to every app.',
        )
        parser.add_argument(
            'migration_name',
            nargs='?',
            help='Migration to bring the app to. Use "zero" to unapply all.',
        )
        parser.add_argument(
            '--tenant',
            action='append',
            dest='lookups',
            metavar='LOOKUP',
            help=(
                'Primary key, subdomain or database name of a tenant to '
                'migrate. Repeatable. Defaults to every tenant, which is also '
                'how you shard a large fleet across parallel invocations.'
            ),
        )
        parser.add_argument(
            '--include-deleted',
            action=BooleanOptionalAction,
            default=False,
            help=(
                'Also migrate soft-deleted tenants. Off by default: a '
                'decommissioned tenant should not be dragged along by every '
                'subsequent schema change.'
            ),
        )
        parser.add_argument(
            '--keep-going',
            action='store_true',
            help=(
                'Attempt every tenant even after one fails, and report the '
                'failures at the end. Off by default, so a broken migration '
                'stops before it has been half-applied across the fleet.'
            ),
        )

        # Forwarded to `migrate`.
        parser.add_argument('--check', action='store_true', dest='check_unapplied')
        parser.add_argument('--fake', action='store_true')
        parser.add_argument('--fake-initial', action='store_true')
        parser.add_argument('--plan', action='store_true')
        parser.add_argument(
            '--noinput',
            '--no-input',
            action='store_false',
            dest='interactive',
        )

    def handle(self, *args, **options):
        verbosity = options['verbosity']
        tenants = self._select_tenants(options)

        if not tenants:
            self.stderr.write(self.style.WARNING('No tenants to migrate.'))
            return

        migrate_args = [
            value
            for value in (options['app_label'], options['migration_name'])
            if value
        ]
        migrate_options = {
            name: options[name]
            for name in ('check_unapplied', 'fake', 'fake_initial', 'plan', 'interactive')
        }
        migrate_options['verbosity'] = verbosity

        failures = self._migrate_each(
            tenants, migrate_args, migrate_options, options['keep_going'], verbosity
        )

        self._report(tenants, failures, verbosity)

        if failures:
            raise CommandError(
                f'{len(failures)} of {len(tenants)} tenants failed to migrate.'
            )

    def _select_tenants(self, options) -> list:
        queryset = get_tenant_queryset(include_deleted=options['include_deleted'])
        lookups = options['lookups']

        if not lookups:
            # Ordered so that a run over a large fleet is resumable: the same
            # tenants are always attempted in the same sequence.
            return list(queryset.order_by('subdomain'))

        tenant_model = get_tenant_model()
        tenants = []

        for lookup in lookups:
            try:
                tenants.append(
                    get_tenant(lookup, include_deleted=options['include_deleted'])
                )
            except tenant_model.DoesNotExist as exc:
                raise CommandError(f'No tenant matches "{lookup}".') from exc
            except tenant_model.MultipleObjectsReturned as exc:
                raise CommandError(
                    f'"{lookup}" matches more than one tenant. Use the primary key.'
                ) from exc

        return tenants

    def _migrate_each(
        self, tenants, migrate_args, migrate_options, keep_going, verbosity
    ) -> list[tuple]:
        failures = []

        for position, tenant in enumerate(tenants, start=1):
            if verbosity:
                self.stdout.write(
                    f'[{position}/{len(tenants)}] {tenant.subdomain} '
                    f'({tenant.database_name})'
                )

            try:
                # Activated as well as targeted by alias, so that data
                # migrations using RunPython can call get_current_tenant() and
                # know which customer they are rewriting.
                with tenant_context(tenant):
                    migrate_tenant_database(tenant, *migrate_args, **migrate_options)
            except Exception as exc:  # noqa: BLE001 - reported per tenant below
                failures.append((tenant, exc))

                # The failure itself is written whatever the verbosity: a
                # migration that did not apply is never something to be quiet
                # about. The guidance that follows it is not an error, so it
                # respects verbosity like any other commentary.
                self.stderr.write(self.style.ERROR(f'{tenant.subdomain}: {exc}'))

                if keep_going:
                    continue

                if verbosity:
                    self.stderr.write(
                        'Stopped after the first failure. Migrations are '
                        'idempotent, so re-running after a fix skips whatever '
                        'already succeeded; use --keep-going to attempt the '
                        'remaining tenants instead.'
                    )

                raise CommandError(
                    f'{tenant.subdomain} failed to migrate.'
                ) from exc

        return failures

    def _report(self, tenants, failures, verbosity) -> None:
        if not verbosity:
            return

        succeeded = len(tenants) - len(failures)

        if failures:
            self.stdout.write(
                self.style.WARNING(
                    f'{succeeded}/{len(tenants)} tenants migrated. Failed: '
                    + ', '.join(tenant.subdomain for tenant, _ in failures)
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(f'{succeeded} tenant(s) migrated.')
            )
