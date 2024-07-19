from django.db import DEFAULT_DB_ALIAS

from multiverse.test import is_test_environment
from multiverse.utils import get_tenant_database_alias, get_system_apps, get_common_apps
from multiverse.awareness import get_current_tenant


class TenantRouter:
    def db_for(self, mode, model, **hints):
        if is_test_environment():
            return DEFAULT_DB_ALIAS

        app_label = model._meta.app_label

        if app_label in get_system_apps():
            return DEFAULT_DB_ALIAS

        if app_label in get_common_apps():
            is_tenant_active = get_current_tenant() is not None
            return get_tenant_database_alias() if is_tenant_active else DEFAULT_DB_ALIAS

        return get_tenant_database_alias()

    def db_for_read(self, model, **hints):
        return self.db_for('read', model, **hints)

    def db_for_write(self, model, **hints):
        return self.db_for('write', model, **hints)

    def allow_relation(self, obj1, obj2, **hints):
        db_obj1 = hints.get('database', None) or self.db_for_read(obj1)
        db_obj2 = hints.get('database', None) or self.db_for_read(obj2)

        return db_obj1 and db_obj2 and db_obj1 == db_obj2

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if is_test_environment():
            return True

        if app_label in get_common_apps():
            return True

        if app_label in get_system_apps():
            return db == DEFAULT_DB_ALIAS

        return db == get_tenant_database_alias()
