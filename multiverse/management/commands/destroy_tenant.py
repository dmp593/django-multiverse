"""Decommission a tenant, optionally dropping its database."""

from __future__ import annotations

from argparse import BooleanOptionalAction

from django.core.management.base import BaseCommand, CommandError
from django.db import DEFAULT_DB_ALIAS

from multiverse.utils import drop_tenant_database, get_tenant, get_tenant_model


class Command(BaseCommand):
    help = (
        'Decommission a tenant. By default the tenant row is soft-deleted and '
        'its database is left untouched.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            'lookup',
            help='Primary key, subdomain or database name of the tenant.',
        )
        # Dropping the database and removing the row are now independent.
        # Previously one flag did both, so there was no way to retire a tenant
        # while keeping its data, nor to forget a tenant whose database had
        # already been removed by other means.
        parser.add_argument(
            '--drop-database',
            action=BooleanOptionalAction,
            default=False,
            help='Irreversibly drop the tenant database. Off by default.',
        )
        parser.add_argument(
            '--hard',
            action=BooleanOptionalAction,
            default=False,
            help=(
                'Delete the tenant row outright instead of soft-deleting it. '
                'Off by default: the row records which database belonged to '
                'which customer, which is worth keeping after decommissioning.'
            ),
        )
        parser.add_argument(
            '--noinput',
            '--no-input',
            action='store_false',
            dest='interactive',
            help='Do not prompt for confirmation before dropping a database.',
        )

    def handle(self, *args, **options):
        tenant = self._find_tenant(options['lookup'])
        drop_database = options['drop_database']
        verbosity = options['verbosity']

        if drop_database and options['interactive']:
            self._confirm_drop(tenant)

        # The database is dropped before the row is removed. The row is the only
        # record of which database belongs to this tenant, so removing it first
        # and then failing to drop — which is exactly what happened while the
        # PostgreSQL backend issued invalid SQL — orphans a database that nobody
        # can identify afterwards.
        if drop_database:
            _, dropped = drop_tenant_database(tenant)

            if not dropped:
                self.stderr.write(
                    self.style.WARNING(
                        f'Database "{tenant.database_name}" did not exist.'
                    )
                )
            elif verbosity:
                self.stdout.write(f'Dropped database "{tenant.database_name}".')

        tenant.delete(using=DEFAULT_DB_ALIAS, hard=options['hard'])

        if verbosity:
            verb = 'permanently deleted' if options['hard'] else 'soft-deleted'
            self.stdout.write(
                self.style.SUCCESS(f'Tenant "{tenant.subdomain}" {verb}.')
            )

    def _find_tenant(self, lookup: str):
        tenant_model = get_tenant_model()

        try:
            # Deleted tenants are included: a soft-deleted tenant whose database
            # still exists is precisely the one an operator needs to reach.
            return get_tenant(lookup, include_deleted=True)
        except tenant_model.DoesNotExist as exc:
            raise CommandError(f'No tenant matches "{lookup}".') from exc
        except tenant_model.MultipleObjectsReturned as exc:
            raise CommandError(
                f'"{lookup}" matches more than one tenant. Use the primary key.'
            ) from exc

    def _confirm_drop(self, tenant) -> None:
        self.stdout.write(
            self.style.WARNING(
                f'This will irreversibly drop database "{tenant.database_name}" '
                f'belonging to tenant "{tenant.subdomain}".'
            )
        )

        if input('Type the subdomain to confirm: ').strip() != tenant.subdomain:
            raise CommandError('Aborted: confirmation did not match.')
