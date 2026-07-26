"""
The tenant model and the abstract base it is built from.

Timestamps and soft deletion come from ``django-timestampable``, which is where
that behaviour is maintained. Earlier releases reached it through an unrelated
private package that was never published, which is what made ``multiverse``
impossible to install from PyPI.

The field set is unchanged from ``0001_initial``, so existing databases keep
working without a schema change.
"""

from __future__ import annotations

import uuid

from django.db import models
from timestamps.models import Model as TimestampedSoftDeleteModel

from multiverse.validators import validate_database_name, validate_subdomain


class BaseTenant(TimestampedSoftDeleteModel):
    """
    Abstract tenant. Subclass this when you need extra columns (plan, region,
    billing reference) and point ``TENANT_MODEL`` at your subclass.

    Inherited from ``django-timestampable``:

    * ``created_at``, ``updated_at``, ``deleted_at``
    * ``delete(hard=False)``, ``restore()``
    * managers ``objects`` (live only), ``objects_deleted``, ``objects_with_deleted``

    ``objects`` excluding deleted rows is load-bearing: it means a decommissioned
    tenant stops resolving from a request the moment it is deleted, without every
    lookup having to remember a filter.
    """

    #: Fields re-validated on every save. Their values leave the ORM — one
    #: becomes a filesystem path and a SQL identifier, the other arrives from a
    #: ``Host`` header — so correctness cannot depend on each call site
    #: remembering to call ``full_clean()``.
    SECURITY_VALIDATED_FIELDS = ('subdomain', 'database_name')

    # A UUID rather than a sequential integer: tenant identifiers travel through
    # background-job payloads, URLs and log lines, where a counter would leak how
    # many customers exist and let one tenant's identifier be guessed from
    # another's.
    id = models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True)

    subdomain = models.CharField(
        max_length=50,
        null=False,
        unique=True,
        validators=[validate_subdomain],
        help_text='Single DNS label used to resolve this tenant, e.g. "acme".',
    )

    database_name = models.CharField(
        max_length=50,
        null=False,
        unique=True,
        validators=[validate_database_name],
        help_text='Name of the database backing this tenant.',
    )

    class Meta:
        abstract = True

    def __str__(self) -> str:
        return self.subdomain

    def save(self, *args, **kwargs):
        excluded = [
            field.name
            for field in self._meta.fields
            if field.name not in self.SECURITY_VALIDATED_FIELDS
        ]
        self.clean_fields(exclude=excluded)
        super().save(*args, **kwargs)


class Tenant(BaseTenant):
    """
    Default concrete tenant.

    Swappable: set ``TENANT_MODEL`` to your own model to replace it, exactly as
    ``AUTH_USER_MODEL`` replaces ``auth.User``.
    """

    class Meta(BaseTenant.Meta):
        abstract = False
        swappable = 'TENANT_MODEL'
