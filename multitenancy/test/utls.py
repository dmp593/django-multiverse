from django.conf import settings


SETTINGS_TESTING_KEY = 'TESTING'


def is_test_environment():
    return getattr(settings, SETTINGS_TESTING_KEY, False)


def set_test_environment(testing: bool):
    return setattr(settings, SETTINGS_TESTING_KEY, testing)
