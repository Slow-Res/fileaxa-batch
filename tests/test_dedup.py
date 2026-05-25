"""Disk duplicate detection — covers the 'foo (1).rar next to foo.rar'
leftovers from the old _unique_path suffixing behavior."""
from pathlib import Path

import pytest

from fileaxa_batch.core.dedup import (
    DuplicateGroup,
    delete_duplicates,
    find_duplicates,
)


def _touch(d: Path, *names: str) -> list[Path]:
    paths = []
    for n in names:
        p = d / n
        p.write_bytes(b"x")
        paths.append(p)
    return paths


def test_no_duplicates_when_all_unique(tmp_path: Path):
    _touch(tmp_path, "a.rar", "b.rar", "c.zip")
    assert find_duplicates(tmp_path) == []


def test_keeps_unsuffixed_drops_numbered(tmp_path: Path):
    base, dup1, dup2 = _touch(
        tmp_path,
        "movie.rar",
        "movie (1).rar",
        "movie (2).rar",
    )
    groups = find_duplicates(tmp_path)
    assert len(groups) == 1
    assert groups[0].canonical == "movie.rar"
    assert groups[0].keep == base
    assert set(groups[0].drop) == {dup1, dup2}


def test_keeps_lowest_n_when_no_unsuffixed(tmp_path: Path):
    """If only suffixed versions exist, the lowest-numbered one wins."""
    dup1, dup2 = _touch(tmp_path, "song (1).mp3", "song (2).mp3")
    groups = find_duplicates(tmp_path)
    assert len(groups) == 1
    assert groups[0].keep == dup1
    assert groups[0].drop == [dup2]


def test_handles_extensionless_files(tmp_path: Path):
    base, dup = _touch(tmp_path, "README", "README (1)")
    groups = find_duplicates(tmp_path)
    assert len(groups) == 1
    assert groups[0].keep == base
    assert groups[0].drop == [dup]


def test_multipart_archives_are_NOT_duplicates(tmp_path: Path):
    """part1.rar / part2.rar / part3.rar belong to one archive — different
    canonical names, not duplicates."""
    _touch(tmp_path, "movie.part1.rar", "movie.part2.rar", "movie.part3.rar")
    assert find_duplicates(tmp_path) == []


def test_delete_duplicates_actually_removes_files(tmp_path: Path):
    base, dup1, dup2 = _touch(tmp_path, "x.bin", "x (1).bin", "x (2).bin")
    groups = find_duplicates(tmp_path)
    deleted = delete_duplicates(groups)
    assert set(deleted) == {dup1, dup2}
    assert base.exists()
    assert not dup1.exists()
    assert not dup2.exists()


def test_subdirectories_are_ignored(tmp_path: Path):
    (tmp_path / "subdir").mkdir()
    (tmp_path / "subdir" / "thing (1).rar").write_bytes(b"x")
    (tmp_path / "thing.rar").write_bytes(b"x")
    # Only one file matches the top-level scan; no dup group formed.
    assert find_duplicates(tmp_path) == []


def test_missing_directory_returns_empty():
    assert find_duplicates(Path("/nonexistent/anywhere")) == []
