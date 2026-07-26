"""
Deprecated alias for :mod:`multiverse.test.utils`.

The original module name was a typo. It is kept because it was published as part
of the public API, and importing it still works — it just tells you where the
module moved to.
"""

from __future__ import annotations

import warnings

from multiverse.test.utils import (  # noqa: F401
    SETTINGS_TESTING_KEY,
    is_test_environment,
    set_test_environment,
)

warnings.warn(
    'multiverse.test.utls is a misspelling and will be removed in a future '
    'release. Import from multiverse.test.utils instead.',
    DeprecationWarning,
    stacklevel=2,
)


__all__ = [
    'SETTINGS_TESTING_KEY',
    'is_test_environment',
    'set_test_environment',
]
