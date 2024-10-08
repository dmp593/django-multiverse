from django.db import DEFAULT_DB_ALIAS

from multiverse.test import is_test_environment
from multiverse.utils import get_tenant, get_tenant_apps, get_tenant_database_alias, get_system_apps, get_common_apps
from multiverse.awareness import get_current_tenant


class TenantRouter:
    def app_label_in_apps(self, app_label, apps):
        for app in apps:
            # eg, typical apps: mycustomapp
            if app_label == app:
                return True

            # eg, apps with full name: django.contrib.contenttypes
            if app_label == app.split('.')[-1]:
                return True

        return False

    def db_for(self, mode, model, **hints):
        if is_test_environment():
            return DEFAULT_DB_ALIAS

        app_label = model._meta.app_label

        if self.app_label_in_apps(app_label, get_common_apps()):
            if 'relation' in hints:
                relation = hints['relation']
                relation_app_label = relation._meta.app_label

                if self.app_label_in_apps(relation_app_label, get_system_apps()):
                    return DEFAULT_DB_ALIAS

                if self.app_label_in_apps(relation_app_label, get_tenant_apps()):
                    return get_tenant_database_alias()

            return None

        if self.app_label_in_apps(app_label, get_system_apps()):
            return DEFAULT_DB_ALIAS

        return get_tenant_database_alias()

    def db_for_read(self, model, **hints):
        return self.db_for('read', model, **hints)

    def db_for_write(self, model, **hints):
        return self.db_for('write', model, **hints)

    def allow_relation(self, obj1, obj2, **hints):
        if obj1._state.db == obj2._state.db:
            return True

        db_obj1 = hints.get('database', None) or self.db_for_read(obj1, relation=obj2)
        db_obj2 = hints.get('database', None) or self.db_for_read(obj2, relation=obj1)

        return db_obj1 and db_obj2 and db_obj1 == db_obj2

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if is_test_environment():
            return True

        if self.app_label_in_apps(app_label, get_common_apps()):
            return True

        if self.app_label_in_apps(app_label, get_system_apps()):
            return db == DEFAULT_DB_ALIAS

        return db == get_tenant_database_alias()
