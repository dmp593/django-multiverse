import psycopg
from psycopg import sql

from django.conf import settings


def _get_connection() -> psycopg.Connection:
    return psycopg.connect(
        dbname="postgres",
        user=settings.DATABASES['default']['USER'],
        password=settings.DATABASES['default']['PASSWORD'],
        host=settings.DATABASES['default']['HOST'],
        port=settings.DATABASES['default']['PORT'],
        autocommit=True,
    )


def _database_exists(cursor, name) -> bool:
    cursor.execute(
        sql.SQL(
            'SELECT datname FROM pg_catalog.pg_database WHERE datname = %(datname)s'
        ),
        params={
            'datname': name
        }
    )

    return cursor.rowcount > 0


def _database_create(cursor, name):
    cursor.execute(
        sql.SQL('CREATE DATABASE {}').format(
            sql.Identifier(name)
        )
    )


def _database_drop(cursor, name):
    cursor.execute(
        sql.SQL('CREATE DATABASE IF EXISTS {}').format(
            sql.Identifier(name)
        )
    )


def create_database_if_not_exists(name: str) -> tuple[str, bool]:
    connection = _get_connection()
    created = False

    with connection.cursor() as cursor:
        if not _database_exists(cursor, name):
            _database_create(cursor, name)
            connection.commit()
            created = True

    connection.close()

    return name, created


def drop_database_if_exists(name: str) -> tuple[str, bool]:
    connection = _get_connection()
    dropped = False

    with connection.cursor() as cursor:
        if _database_exists(cursor, name):
            _database_drop(cursor, name)
            connection.commit()
            dropped = True

    connection.close()

    return name, dropped
