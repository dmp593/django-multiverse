import pathlib
from pathlib import Path


SQLITE_EXTENSIONS = [
    '.sqlite',
    '.sqlite3'
]


# def _sanitize_database_name(name: str | Path) -> Path | str:
#     if ':memory:' in name:
#         return name
#
#     name = Path(name)
#
#     if name.suffix not in SQLITE_EXTENSIONS:
#         name = name.with_suffix(SQLITE_EXTENSIONS[-1])
#
#     if name.parent != settings.BASE_DIR:
#         name = settings.BASE_DIR / name
#
#     return name


def create_database_if_not_exists(name: str | Path) -> tuple[str, bool]:
    name = pathlib.Path(name)  # name = _sanitize_database_name(name)
    created = False

    if not name.exists():
        name.touch()
        created = True

    return str(name), created


def drop_database_if_exists(name: str | Path) -> tuple[str, bool]:
    name = pathlib.Path(name)  # name = _sanitize_database_name(name)
    dropped = False

    if isinstance(name, pathlib.Path) and name.exists():
        name.unlink()
        dropped = True

    return str(name), dropped
