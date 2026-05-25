from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional


class JobStatus(str, Enum):
    PENDING = "pending"
    APPROVAL = "approval"
    FETCHING_METADATA = "fetching metadata"
    NAVIGATING = "navigating"
    WAITING_TIMER = "waiting timer"
    WAITING_CAPTCHA = "waiting CAPTCHA"
    DOWNLOADING = "downloading"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class FileMeta:
    file_code: str
    name: Optional[str] = None
    size: Optional[int] = None


@dataclass
class DownloadJob:
    url: str
    file_code: str
    status: JobStatus = JobStatus.PENDING
    meta: Optional[FileMeta] = None
    dest_path: Optional[Path] = None
    bytes_done: int = 0
    total_bytes: int = 0
    speed_bps: float = 0.0
    eta_s: float = 0.0
    error: Optional[str] = None
