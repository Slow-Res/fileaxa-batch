"""Find and remove on-disk duplicates created by the old _unique_path
suffixing behavior — files like 'foo (1).rar' that sit next to 'foo.rar'.

The current downloader skips re-saving when the unsuffixed file already
exists; this module cleans up the leftovers from earlier runs.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

# "<stem> (<N>)<ext>" where N is an integer.
_SUFFIX_PATTERN = re.compile(r"^(?P<stem>.+) \((?P<n>\d+)\)(?P<ext>\.[^.]+)?$")


@dataclass(frozen=True)
class DuplicateGroup:
    canonical: str           # The base filename without " (N)" suffix
    keep: Path               # The file we keep (lowest N or unsuffixed)
    drop: List[Path]         # Files we'd delete

    @property
    def total(self) -> int:
        return 1 + len(self.drop)


def _canonical_name(name: str) -> tuple[str, int]:
    """Return (canonical_name, suffix_number). suffix_number is 0 if the
    file has no '(N)' suffix."""
    m = _SUFFIX_PATTERN.match(name)
    if not m:
        return name, 0
    ext = m.group("ext") or ""
    return f"{m.group('stem')}{ext}", int(m.group("n"))


def find_duplicates(directory: Path) -> List[DuplicateGroup]:
    """Group regular files by canonical name; return groups with more than
    one member. The 'keep' file is the unsuffixed original if present, else
    the lowest-numbered suffix. Groups are sorted by canonical name for a
    stable confirmation dialog."""
    if not directory.exists():
        return []
    groups: Dict[str, List[tuple[int, Path]]] = {}
    for entry in directory.iterdir():
        if not entry.is_file():
            continue
        canonical, n = _canonical_name(entry.name)
        groups.setdefault(canonical, []).append((n, entry))

    result: List[DuplicateGroup] = []
    for canonical, members in sorted(groups.items()):
        if len(members) < 2:
            continue
        # Sort by suffix N ascending; 0 (unsuffixed) wins naturally.
        members.sort(key=lambda x: x[0])
        keep = members[0][1]
        drop = [p for _, p in members[1:]]
        result.append(DuplicateGroup(canonical=canonical, keep=keep, drop=drop))
    return result


def delete_duplicates(groups: List[DuplicateGroup]) -> List[Path]:
    """Delete every file listed in DuplicateGroup.drop. Returns the paths
    successfully removed; failures are silently ignored (caller can compare
    against the input to detect them)."""
    deleted: List[Path] = []
    for g in groups:
        for p in g.drop:
            try:
                p.unlink()
                deleted.append(p)
            except OSError:
                pass
    return deleted
