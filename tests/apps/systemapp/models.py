"""A SYSTEM-tier app: its tables live only in the `default` database."""

from django.db import models


class SystemNote(models.Model):
    text = models.CharField(max_length=100)
