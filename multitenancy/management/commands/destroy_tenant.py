from django.core.management.base import BaseCommand

from multiverse.utils import get_tenant, drop_tenant_database


class Command(BaseCommand):
    help = 'Destroys a tenant of MyWise'

    def add_arguments(self, parser):
        parser.add_argument('lookup', type=str)
        parser.add_argument('--drop-database', type=bool, default=False)

    def handle(self, *args, **options):
        lookup = options.get('lookup')
        drop_database = options.get('drop_database')

        tenant = get_tenant(lookup)
        tenant.delete(using='default', hard=drop_database)

        if drop_database:
            drop_tenant_database(tenant)
