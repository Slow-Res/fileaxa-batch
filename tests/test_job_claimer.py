"""JobClaimer must hand out each PENDING row to exactly one worker, even
under concurrency. Without the lock, two threads can both observe the same
PENDING row before either flips its status, and we'd download the same file
twice."""
import threading

from fileaxa_batch.core.models import DownloadJob, JobStatus
from fileaxa_batch.worker.worker import JobClaimer


def test_returns_none_when_no_pending():
    jobs = [DownloadJob(url="u", file_code="c", status=JobStatus.COMPLETED)]
    assert JobClaimer(jobs).claim_next() is None


def test_flips_pending_to_navigating():
    jobs = [DownloadJob(url="u", file_code="c")]
    idx = JobClaimer(jobs).claim_next()
    assert idx == 0
    assert jobs[0].status == JobStatus.NAVIGATING


def test_concurrent_claims_are_unique():
    n = 200
    jobs = [DownloadJob(url=f"u{i}", file_code=f"c{i}") for i in range(n)]
    claimer = JobClaimer(jobs)

    claimed: list[int] = []
    lock = threading.Lock()

    def worker():
        while True:
            idx = claimer.claim_next()
            if idx is None:
                return
            with lock:
                claimed.append(idx)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(claimed) == list(range(n))
    assert all(j.status == JobStatus.NAVIGATING for j in jobs)
