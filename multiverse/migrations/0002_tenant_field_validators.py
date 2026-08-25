"""
Attach validators and help text to the two tenant fields that leave the ORM.

Metadata only: no column is added, removed or retyped, so this applies to an
existing database without touching data.

`subdomain` arrives from a Host header and `database_name` becomes both a SQL
identifier and a filesystem path, so both are constrained at the model boundary.
"""

from django.db import migrations, models

import multiverse.validators


class Migration(migrations.Migration):

    dependencies = [
        ("multiverse", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="tenant",
            name="database_name",
            field=models.CharField(
                help_text="Name of the database backing this tenant.",
                max_length=50,
                unique=True,
                validators=[multiverse.validators.validate_database_name],
            ),
        ),
        migrations.AlterField(
            model_name="tenant",
            name="subdomain",
            field=models.CharField(
                help_text='Single DNS label used to resolve this tenant, e.g. "acme".',
                max_length=50,
                unique=True,
                validators=[multiverse.validators.validate_subdomain],
            ),
        ),
    ]
