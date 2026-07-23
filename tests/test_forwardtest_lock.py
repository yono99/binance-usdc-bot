"""Lock instance per-mode: dry+live paralel OK; mode sama diblok."""
from pathlib import Path

from forwardtest import _lock_path_for_mode


def test_lock_path_per_mode():
    assert _lock_path_for_mode("dry").name == "forwardtest_dry.lock"
    assert _lock_path_for_mode("live").name == "forwardtest_live.lock"
    assert _lock_path_for_mode("test").name == "forwardtest_test.lock"
    assert _lock_path_for_mode(None).name == "forwardtest.lock"
    assert _lock_path_for_mode("").name == "forwardtest.lock"
    for m in ("dry", "live"):
        p = _lock_path_for_mode(m)
        assert p.parent.name == "logs"
        assert isinstance(p, Path)


def test_dry_and_live_locks_are_distinct():
    assert _lock_path_for_mode("dry") != _lock_path_for_mode("live")
