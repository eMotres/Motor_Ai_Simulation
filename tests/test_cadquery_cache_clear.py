"""CadQueryCache.clear_all must never need write access to the cache's PARENT.

Production regression, 2026-09-16.  ``PUT /api/geometry`` calls
``CadQueryCache().clear_all()`` before it saves (routes/geometry.py); the old
implementation did ``shutil.rmtree(cache_dir)`` + ``mkdir``, which needs write
permission on the parent directory.  In the container the API runs as uid 10001
with WORKDIR /app, /app is root-owned and only /app/cadquery_cache is chowned to
the service account — so every geometry save answered

    500 {"detail": "[Errno 13] Permission denied: 'cadquery_cache'"}

and the Geometry tab blinked (the web queued the edit, retried, failed, applied
locally, retried … every ~6 s).

The contract these tests pin: the cache DIRECTORY survives clear_all, only its
contents go, and a missing directory is not an error.
"""

import os

import pytest

from motor_ai_sim.cadquery_geometry import CadQueryCache


def test_clear_all_keeps_the_directory_and_drops_its_contents(tmp_path):
    cache_dir = tmp_path / "cadquery_cache"
    cache_dir.mkdir()
    (cache_dir / "loose.stl").write_text("solid loose\nendsolid loose\n")
    entry = cache_dir / "deadbeef"
    entry.mkdir()
    (entry / "stator.stl").write_text("solid stator\nendsolid stator\n")

    cache = CadQueryCache(str(cache_dir))
    inode_before = cache_dir.stat().st_ino if os.name != "nt" else None

    cache.clear_all()

    assert cache_dir.is_dir(), "the cache directory itself must survive"
    assert list(cache_dir.iterdir()) == [], "everything inside it must be gone"
    if inode_before is not None:
        assert cache_dir.stat().st_ino == inode_before, \
            "the directory must not be re-created — that is what needed the parent"


def test_clear_all_tolerates_a_missing_directory(tmp_path):
    cache_dir = tmp_path / "never_created"
    cache = CadQueryCache(str(cache_dir))
    # __init__ creates it; remove it behind the cache's back (another worker,
    # a cleaner, a volume that vanished) — clear_all must still not raise.
    cache_dir.rmdir()

    cache.clear_all()   # must not raise

    assert not any(cache_dir.iterdir()) if cache_dir.exists() else True


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits only")
@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0,
                    reason="root ignores the permission bits this test sets")
def test_clear_all_works_with_a_read_only_parent(tmp_path):
    """The container's exact shape: parent unwritable, cache writable."""
    parent = tmp_path / "app"
    parent.mkdir()
    cache_dir = parent / "cadquery_cache"
    cache_dir.mkdir()
    (cache_dir / "deadbeef").mkdir()
    (cache_dir / "deadbeef" / "rotor.stl").write_text("solid rotor\nendsolid rotor\n")
    os.chmod(parent, 0o555)          # r-x: cannot unlink or create IN parent
    try:
        cache = CadQueryCache(str(cache_dir))
        cache.clear_all()            # used to raise PermissionError (Errno 13)
        assert cache_dir.is_dir()
        assert list(cache_dir.iterdir()) == []
    finally:
        os.chmod(parent, 0o755)


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits only")
@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0,
                    reason="root ignores the permission bits this test sets")
def test_constructing_the_cache_never_raises_on_an_unwritable_parent(tmp_path):
    """The cache is an optimisation — it must not be able to 500 a route."""
    parent = tmp_path / "app"
    parent.mkdir()
    os.chmod(parent, 0o555)
    try:
        cache = CadQueryCache(str(parent / "cadquery_cache"))   # must not raise
        assert cache.exists("anything") is False
        cache.clear_all()                                       # must not raise
    finally:
        os.chmod(parent, 0o755)
