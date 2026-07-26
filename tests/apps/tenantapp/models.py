"""A TENANT-tier app: its tables live only in tenant databases."""

from django.db import models


class Invoice(models.Model):
    reference = models.CharField(max_length=50)
    # Relates a TENANT model to a COMMON one, which the router previously
    # rejected outright.
    country = models.ForeignKey(
        'commonapp.Country',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
