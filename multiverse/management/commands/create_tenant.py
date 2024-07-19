from django.core.management.base import BaseCommand

from multiverse.utils import get_tenant_model, create_tenant_database, migrate_tenant_database

Tenant = get_tenant_model()


class Command(BaseCommand):
    help = 'Creates a tenant for MyWise'

    def add_arguments(self, parser):
        parser.add_argument('subdomain', type=str)
        parser.add_argument('--database-name', type=str, nargs='?')

        parser.add_argument('--create-database', type=bool, default=True)
        parser.add_argument('--migrate', type=bool, default=True)

    def handle(self, *args, **options):
        subdomain = options.get('subdomain')
        database_name = options.get('database_name') or subdomain

        tenant, created = Tenant.objects.get_or_create(
            subdomain=subdomain,
            database_name=database_name
        )

        if options.get('create_database'):
            create_tenant_database(tenant)

        if options.get('migrate'):
            migrate_tenant_database()

        self.stdout.write(
            self.style.SUCCESS(f"Tenant with Domain '{subdomain}' and Database '{database_name}' created successfully")
        )
