"""Sweep Playwright Chromium subprocesses that survived previous runs.

Playwright launches each Chromium with a unique
`--user-data-dir=/tmp/playwright_chromiumdev_profile-XXXXXX` argument. If
our process exits cleanly, Playwright's context manager tears the browser
down. If we get SIGKILL'd, crash, or block in a thread that won't exit,
those Chromium processes survive — each holding ~50 X11 client slots.
This module scans /proc on Linux and kills any Chromium whose cmdline
references that prefix.

Linux-only by design. macOS/Windows would need a different process-listing
approach; on those platforms the function is a no-op.
"""
from __future__ import annotations

import os
import signal
import sys
from pathlib import Path
from typing import Iterator, List

# Playwright's unique marker — appears in every Chromium it spawns and
# nowhere else (regular Chrome / Chromium installs use ~/.config/<vendor>).
_PLAYWRIGHT_PROFILE_MARKER = b"playwright_chromiumdev_profile"


def _proc_cmdlines() -> Iterator[tuple[int, bytes]]:
    """Yield (pid, cmdline) for every readable /proc/<pid>/cmdline.
    Skips kernel threads and processes that vanish mid-iteration."""
    proc_root = Path("/proc")
    if not proc_root.is_dir():
        return
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cmdline = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        yield int(entry.name), cmdline


def find_orphan_chromiums() -> List[int]:
    """Return PIDs of Chromium processes whose cmdline references a
    Playwright temp profile. Empty list on non-Linux."""
    if not sys.platform.startswith("linux"):
        return []
    return [
        pid
        for pid, cmd in _proc_cmdlines()
        if _PLAYWRIGHT_PROFILE_MARKER in cmd
    ]


def kill_orphan_chromiums() -> int:
    """SIGKILL every Playwright Chromium found. Returns count killed.
    No-op on non-Linux."""
    pids = find_orphan_chromiums()
    killed = 0
    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL)
            killed += 1
        except (OSError, ProcessLookupError):
            # Process exited between detection and kill — fine.
            pass
    return killed
