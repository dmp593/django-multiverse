"""Create a tenant, provision its database and migrate it."""

from __future__ import annotations

from argparse import BooleanOptionalAction

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import DEFAULT_DB_ALIAS, transaction

from multiverse.utils import (
    create_tenant_database,
    drop_tenant_database,
    get_tenant_model,
    get_tenant_provisioner,
    get_tenant_queryset,
    migrate_tenant_database,
)


class Command(BaseCommand):
    help = 'Create a tenant, provision its database and run migrations against it.'

    def add_arguments(self, parser):
        parser.add_argument(
            'subdomain',
            help='Subdomain the tenant will be served on, e.g. "acme".',
        )
        parser.add_argument(
            '--database-name',
            default=None,
            help='Database backing the tenant. Defaults to the subdomain.',
        )
        # BooleanOptionalAction gives `--migrate` / `--no-migrate`. These flags
        # were previously declared as `type=bool`, which made argparse demand a
        # value and then pass it through bool() — so `--migrate False` meant
        # True and the documented `--create-database --migrate` form failed
        # outright with "expected one argument".
        parser.add_argument(
            '--create-database',
            action=BooleanOptionalAction,
            default=True,
            help='Create the physical database. Enabled by default.',
        )
        parser.add_argument(
            '--migrate',
            action=BooleanOptionalAction,
            default=True,
            help='Run migrations against the new database. Enabled by default.',
        )

    def handle(self, *args, **options):
        subdomain = options['subdomain']
        database_name = options['database_name'] or subdomain
        verbosity = options['verbosity']

        self._guard_against_existing(subdomain, database_name)

        if options['create_database'] and get_tenant_provisioner() is None:
            raise CommandError(
                'No provisioner is available for the tenant database engine, '
                'so the database cannot be created automatically. Create it '
                'yourself and re-run with --no-create-database.'
            )

        tenant = self._build_tenant(subdomain, database_name)
        database_was_created = False

        try:
            # The tenant row and its database are provisioned together: a row
            # pointing at a database that was never created is worse than no row
            # at all, because the tenant looks live and fails at query time.
            with transaction.atomic(using=DEFAULT_DB_ALIAS):
                tenant.save(using=DEFAULT_DB_ALIAS)

                if options['create_database']:
                    _, database_was_created = create_tenant_database(tenant)

                if options['migrate']:
                    migrate_tenant_database(
                        tenant, verbosity=verbosity, interactive=False
                    )
        except Exception:
            # Only clean up a database this command actually created. Dropping
            # one that already existed would destroy data the operator never
            # asked us to touch.
            if database_was_created:
                self._discard_database(tenant)
            raise

        if verbosity:
            self.stdout.write(
                self.style.SUCCESS(
                    f'Tenant "{subdomain}" created, backed by database '
                    f'"{database_name}".'
                )
            )

    def _build_tenant(self, subdomain: str, database_name: str):
        tenant = get_tenant_model()(subdomain=subdomain, database_name=database_name)

        try:
            tenant.full_clean(validate_unique=False)
        except ValidationError as exc:
            raise CommandError('; '.join(self._flatten_errors(exc))) from exc

        return tenant

    def _guard_against_existing(self, subdomain: str, database_name: str) -> None:
        """
        Refuse ambiguous re-creation instead of raising a bare IntegrityError.

        ``subdomain`` and ``database_name`` are independently unique, so a
        partial match is a genuine conflict the operator has to resolve.
        """
        existing = get_tenant_queryset(include_deleted=True).filter(
            subdomain=subdomain
        ).first() or get_tenant_queryset(include_deleted=True).filter(
            database_name=database_name
        ).first()

        if existing is None:
            return

        if existing.deleted_at is not None:
            raise CommandError(
                f'Tenant "{existing.subdomain}" exists but was deleted. '
                f'Restore it with '
                f'Tenant.objects_deleted.get(pk="{existing.pk}").restore(), '
                f'or destroy it permanently with '
                f'`destroy_tenant {existing.subdomain} --hard`.'
            )

        if (existing.subdomain, existing.database_name) == (subdomain, database_name):
            raise CommandError(f'Tenant "{subdomain}" already exists.')

        raise CommandError(
            f'Conflict: tenant "{existing.subdomain}" already uses database '
            f'"{existing.database_name}". Subdomains and database names must '
            f'each be unique.'
        )

    def _discard_database(self, tenant) -> None:
        try:
            drop_tenant_database(tenant)
        except Exception as exc:  # noqa: BLE001 - never mask the original failure
            self.stderr.write(
                self.style.WARNING(
                    f'Could not remove the partially created database '
                    f'"{tenant.database_name}": {exc}. Remove it manually.'
                )
            )

    @staticmethod
    def _flatten_errors(exc: ValidationError) -> list[str]:
        return [
            f'{field}: {message}'
            for field, messages in exc.message_dict.items()
            for message in messages
        ]
