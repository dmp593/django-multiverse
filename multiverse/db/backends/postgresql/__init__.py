def __require_psycopg():
    try:
        import psycopg  # noqa: F401
    except ImportError as e:
        raise ImportError(
            'psycopg not installed. '
            'run `pip install django-multiverse[postgres]` or `pip install psycopg`'
        ) from e


__require_psycopg()
