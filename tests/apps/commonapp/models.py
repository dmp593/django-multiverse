"""A COMMON-tier app: tables created everywhere, read from `default`."""

from django.db import models


class Country(models.Model):
    code = models.CharField(max_length=2, unique=True)
