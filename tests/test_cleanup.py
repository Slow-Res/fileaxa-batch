"""Smoke tests for the orphan-Chromium sweeper. We can't easily simulate
a real Playwright Chromium in tests, so we cover the function contracts."""
import sys

from fileaxa_batch.core.cleanup import find_orphan_chromiums, kill_orphan_chromiums


def test_find_returns_list():
    """Always returns a list — empty on non-Linux or when no orphans exist.
    Should not raise on any platform."""
    result = find_orphan_chromiums()
    assert isinstance(result, list)
    # Anyone running this test almost certainly isn't running Playwright
    # Chromiums under the test's PID; the list should be empty.
    assert all(isinstance(pid, int) for pid in result)


def test_kill_returns_count():
    """Returns an int (zero when nothing to kill). Must not raise."""
    result = kill_orphan_chromiums()
    assert isinstance(result, int)
    assert result >= 0


def test_no_op_on_non_linux(monkeypatch):
    """On non-Linux platforms find returns [] without touching /proc."""
    monkeypatch.setattr(sys, "platform", "darwin")
    assert find_orphan_chromiums() == []
