def __require_django_q2():
    try:
        import django_q
    except ImportError as e:
        raise ImportError(
            'django-q2 not installed. '
            'run `pip install django-multiverse[django-q2]` or `pip install django-q2`'
        ) from e


__require_django_q2()
