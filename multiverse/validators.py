"""
Validation for the two tenant fields that reach outside the ORM.

``database_name`` is interpolated into a ``CREATE DATABASE`` statement and used
as a filesystem path by the SQLite backend, and ``subdomain`` is taken from an
attacker-controlled ``Host`` header. Both are validated at the model boundary so
that no caller can reach a provisioner with a hostile value, regardless of which
code path created the tenant.

This is the outermost of two layers: the SQLite provisioner independently
confines the resolved path to a configured directory. Neither layer is trusted
to be the only one.
"""

from __future__ import annotations

import re

from django.core.exceptions import ValidationError

#: Safe as an unquoted PostgreSQL identifier, as a MySQL schema name and as a
#: single filesystem path segment. Deliberately excludes ``/``, ``\``, spaces,
#: quotes, semicolons and NUL.
DATABASE_NAME_PATTERN = re.compile(r'^[A-Za-z0-9_][A-Za-z0-9_.\-]{0,49}$')

#: A single DNS label, per RFC 1123: lowercase alphanumerics and inner hyphens.
SUBDOMAIN_PATTERN = re.compile(r'^[a-z0-9]([a-z0-9\-]{0,48}[a-z0-9])?$')


def validate_database_name(value: str) -> None:
    """Reject database names that are unsafe as an identifier or a path."""
    if not DATABASE_NAME_PATTERN.match(value or ''):
        raise ValidationError(
            '"%(value)s" is not a valid database name. Use 1-50 characters '
            'limited to letters, digits, underscore, dot and hyphen, starting '
            'with a letter, digit or underscore.',
            code='invalid_database_name',
            params={'value': value},
        )

    # ".." passes the character check but is the classic traversal payload.
    if '..' in value:
        raise ValidationError(
            '"%(value)s" is not a valid database name: ".." is not allowed.',
            code='invalid_database_name',
            params={'value': value},
        )


def validate_subdomain(value: str) -> None:
    """Reject subdomains that are not a valid single DNS label."""
    if not SUBDOMAIN_PATTERN.match(value or ''):
        raise ValidationError(
            '"%(value)s" is not a valid subdomain. Use 1-50 characters '
            'limited to lowercase letters, digits and inner hyphens.',
            code='invalid_subdomain',
            params={'value': value},
        )
