"""Batch scheduling of the snippet renderer (no LibreOffice needed: the process steps are faked)."""

from __future__ import annotations

import threading
import time
from pathlib import Path

from patent_converter.conversion import snippets
from patent_converter.conversion.snippets import SnippetJob, SnippetRenderer


def _jobs(n: int) -> list[SnippetJob]:
    return [SnippetJob(f"a{i}", b"", None, {}, (914400, 914400), "exact") for i in range(n)]


LOCK = threading.Lock()
ACTIVE = [0, 0]  # current, peak (across all renderers)


class FakeRenderer(SnippetRenderer):
    """Batches containing a 'bad' job fail the page-count check, like a figure that spills onto
    an extra page; every other job renders to a path named after its key."""

    def __init__(self, tmp: Path, bad: set[str], **kw):
        super().__init__(tmp, "soffice", "pdftocairo", 192, 60, kw.pop("batch_size", 100), **kw)
        self.bad = bad
        self.runs: list[int] = []

    def _run_batches(self, batches, source, part_limit, deadline_check, prefix):
        self._batch_ok = {}
        results = {}

        def process(idx):
            batch = batches[idx]
            with snippets.renderer_slot(self.max_processes, deadline_check):
                with LOCK:
                    ACTIVE[0] += 1
                    ACTIVE[1] = max(ACTIVE[1], ACTIVE[0])
                time.sleep(0.01)
                with LOCK:
                    ACTIVE[0] -= 1
            self.runs.append(len(batch))
            if any(j.key in self.bad for j in batch):
                if len(batch) > 1:
                    self._batch_ok[idx] = False
                    return {}
                return {batch[0].key: "PAGE_COUNT_MISMATCH"}
            self._batch_ok[idx] = True
            return {j.key: Path(j.key) for j in batch}

        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=self.parallelism) as pool:
            for part in pool.map(process, range(len(batches))):
                results.update(part)
        return results


def test_one_bad_figure_is_isolated_by_halving(tmp_path):
    r = FakeRenderer(tmp_path, bad={"a37"}, parallelism=1)
    out = r.render(_jobs(100), None, 0, lambda: None)
    assert out["a37"] == "PAGE_COUNT_MISMATCH"
    assert all(out[f"a{i}"] == Path(f"a{i}") for i in range(100) if i != 37)
    assert len(out) == 100
    # 1 full batch + 2 per halving level (100 -> 50 -> 25 -> 13 -> 7 -> 4 -> 2 -> 1): far fewer than 100 singles.
    assert len(r.runs) <= 1 + 2 * 7
    assert 1 <= r.stats["fallback_batches"] <= 7  # one per halving level (ceil(log2(100)))


def test_all_good_needs_no_retry(tmp_path):
    r = FakeRenderer(tmp_path, bad=set(), parallelism=4, batch_size=10)
    out = r.render(_jobs(40), None, 0, lambda: None)
    assert len(out) == 40 and r.stats["fallback_batches"] == 0
    assert sorted(r.runs) == [10, 10, 10, 10]


def test_process_limit_is_shared_by_concurrent_jobs(tmp_path):
    ACTIVE[:] = [0, 0]
    renderers = [FakeRenderer(tmp_path / str(i), bad=set(), parallelism=4, batch_size=1, max_processes=3)
                 for i in range(3)]
    threads = [threading.Thread(target=r.render, args=(_jobs(12), None, 0, lambda: None)) for r in renderers]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # 3 jobs x 4 threads each would be 12 at once; the shared limit holds it at 3.
    assert 2 <= ACTIVE[1] <= 3
    assert snippets._slots is not None and snippets._slots_size == 3


def test_waiting_for_a_slot_honours_the_deadline():
    class Expired(Exception):
        pass

    def check():
        raise Expired

    with snippets.renderer_slot(1, lambda: None):
        try:
            with snippets.renderer_slot(1, check):
                raise AssertionError("slot acquired while held")
        except Expired:
            pass
