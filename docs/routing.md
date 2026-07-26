# Routing

Every app belongs to exactly one of three tiers, and that choice decides which
database its tables are created in and which database its rows are read from.

## The three tiers

### `SYSTEM_APPS` — only in the system database

Tables exist in `default` and nowhere else. Reads and writes always go there,
whether or not a tenant is active.

Use it for anything that must be readable *before* a tenant is known: the tenant
registry itself, sign-up, billing, platform-wide admin users.

```python
SYSTEM_APPS = [
    'django.contrib.contenttypes',
    'django.contrib.auth',
    'timestamps',
    'multiverse',
    'billing',
]
```

### `TENANT_APPS` — only in tenant databases

Tables exist in every tenant database and not in `default`. Reads and writes go
to whichever tenant is currently active. This is your customer data.

```python
TENANT_APPS = ['invoices', 'projects', 'documents']
```

### `COMMON_APPS` — table everywhere, rows are local

Tables are created in **every** database, and each database holds its own rows.
A row in `default` and a row in a tenant database are different rows.

The router expresses *no opinion* for these models, which is Django's signal to
follow the related object's database and fall back to `default` when there is
none.

`django.contrib.contenttypes` is the canonical case: every database needs the
table, and each copy must describe the models in *that* database.

```python
COMMON_APPS = ['django.contrib.sessions']
```

> **`COMMON` is not shared data.** Writing a row here does not replicate it. If a
> tenant database needs a copy, seed it there — `Country.objects.using(alias)` —
> or in a data migration, which runs against every database the app migrates in.

## Decision tables

### Reads and writes — `db_for_read` / `db_for_write`

| Model | Result |
| --- | --- |
| The tenant registry model | `default`, always |
| App in `SYSTEM_APPS` | `default` |
| App in `COMMON_APPS`, no relation hint | `None` → related object's database, else `default` |
| App in `COMMON_APPS`, related to a SYSTEM model | `default` |
| App in `COMMON_APPS`, related to a TENANT model | The active tenant alias |
| App in `TENANT_APPS` | The active tenant alias |
| App in no tier | The active tenant alias, plus `multiverse.W001` |

The tenant registry is pinned to `default` regardless of how its app is
classified. Without that pin, the query that resolves a tenant would itself be
routed to that tenant's database — a lookup that cannot succeed until it has
already succeeded.

### Migrations — `allow_migrate`

| App | `default` | Tenant databases |
| --- | --- | --- |
| Tenant registry model | ✅ | ❌ |
| `SYSTEM_APPS` | ✅ | ❌ |
| `COMMON_APPS` | ✅ | ✅ |
| `TENANT_APPS` | ❌ | ✅ |
| Unclassified | ❌ | ✅ |

Tenant databases are addressed by derived aliases (`tenant::acme_db`), so the
check is "is this a tenant alias?", not equality against the template alias. An
equality check would skip every real tenant.

### Relations — `allow_relation`

1. Both objects already loaded from the same database → allowed.
2. Otherwise both are resolved (with each passed as the other's `relation` hint)
   and must agree. `None` resolves to `default` before comparing.

Step 1 is what keeps relations working inside a tenant database for `COMMON`
models, whose tables exist there too.

## Choosing a tier

| The model… | Tier |
| --- | --- |
| must be readable before a tenant is known | `SYSTEM_APPS` |
| holds customer data | `TENANT_APPS` |
| is referenced by tenant models and needs a local copy per database | `COMMON_APPS` |
| is a Django contrib app used only by system views | `SYSTEM_APPS` |
| is a Django contrib app used *inside* tenant databases (contenttypes for generic relations on tenant models) | `COMMON_APPS` |

When unsure, start with `SYSTEM_APPS`. Moving an app to `TENANT_APPS` later
means copying data out to each tenant; moving it the other way means merging
data in, which is much harder.

## Foreign keys across tiers

| From → To | Works? |
| --- | --- |
| `TENANT` → `TENANT` | ✅ Same database |
| `SYSTEM` → `SYSTEM` | ✅ Same database |
| `COMMON` → `COMMON` | ✅ Within one database |
| `TENANT` → `COMMON` | ✅ Provided the row is seeded in that tenant's database |
| `TENANT` → `SYSTEM` | ❌ Different physical databases |
| `SYSTEM` → `TENANT` | ❌ Different physical databases |

Cross-database foreign keys cannot be enforced by any database engine. Where you
need one, store the identifier and resolve it in application code:

```python
class Invoice(models.Model):          # TENANT
    # No FK: billing_account lives in the system database.
    billing_account_id = models.UUIDField()

    @property
    def billing_account(self):
        return BillingAccount.objects.get(pk=self.billing_account_id)
```

## Moving an app between tiers

There is no migration path the package can generate for you — the data has to be
physically relocated.

* **`SYSTEM` → `TENANT`**: run `migrate` so the tables exist in every tenant
  database, copy each tenant's rows out of `default`, then delete them there.
* **`TENANT` → `SYSTEM`**: merge every tenant's rows into `default`, resolving
  primary key collisions, then drop the tenant-side tables.

Do it in a maintenance window, and verify counts on both sides before deleting
anything.

## Checking your configuration

```bash
python manage.py check
```

```python
from multiverse.db.router import app_classifier

app_classifier.tier_for('invoices')          # AppTier.TENANT
app_classifier.unclassified_app_labels()     # ['somethirdpartyapp']
app_classifier.conflicting_app_labels()      # {'billing': ['system', 'tenant']}
```
