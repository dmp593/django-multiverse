from django.contrib import admin

from multiverse.models import Tenant


class TenantAdmin(admin.ModelAdmin):
    list_display = ('subdomain', 'database_name', 'created_at', 'deleted_at')
    list_filter = ('created_at', 'deleted_at')
    search_fields = ('subdomain', 'database_name')
    readonly_fields = ('id', 'created_at', 'updated_at', 'deleted_at')
    ordering = ('subdomain',)

    def get_queryset(self, request):
        # Deliberately bypasses the default manager so soft-deleted tenants stay
        # visible. Hiding them here would leave an operator unable to see which
        # database a decommissioned customer owned — the very reason the row is
        # kept rather than removed.
        return Tenant.objects_with_deleted.all()


# Only register the built-in model when the project is actually using it.
# Registering a model that TENANT_MODEL has swapped out would put an admin page
# in front of a table that no longer exists.
if not Tenant._meta.swapped:
    admin.site.register(Tenant, TenantAdmin)
