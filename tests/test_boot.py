"""M0 composition and persistence smoke tests."""

from pathlib import Path

from journeymap.adapters.sqlite import SQLitePersistence
from journeymap.bootstrap import create_kernel


def test_empty_kernel_boots_without_modules() -> None:
    kernel = create_kernel()

    kernel.boot()

    assert kernel.is_booted
    assert kernel.module_ids == ()

    kernel.close()
    assert not kernel.is_booted


def test_sqlite_persistence_uses_a_real_connection(tmp_path: Path) -> None:
    database = tmp_path / "journeymap.sqlite3"
    persistence = SQLitePersistence(database)

    persistence.initialize()

    assert persistence.is_open
    assert database.is_file()

    persistence.close()
    assert not persistence.is_open
