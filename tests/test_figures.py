"""Tests of Phase 2: view totals to a common cutoff, coverage, retries and refreshes, reports read the data files.

    python -m unittest discover -s tests       (from the Reoa folder)
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import provenance  # noqa: E402
import report  # noqa: E402
import tracker  # noqa: E402


class FakeAPI:
    """Answers prop=imageinfo queries from a table {page id: [(timestamp, user, size)]} (oldest first)."""

    def __init__(self, table):
        self.table, self.requests = table, 0

    def query_all(self, **params):
        self.requests += 1
        pages = []
        for pid in params["pageids"].split("|"):
            versions = self.table.get(pid)
            if versions is None:
                pages.append({"pageid": int(pid), "missing": True})
            else:
                pages.append({"pageid": int(pid), "imageinfo": [
                    {"timestamp": t, "user": u, "size": s} for t, u, s in reversed(versions)]})
        yield {"query": {"pages": pages}}


class ViewsCutoffTests(unittest.TestCase):
    def test_cutoff(self):
        self.assertEqual(tracker.views_cutoff(["2026-09-30", "2026-11-02"]), "2026-09")
        self.assertEqual(tracker.views_cutoff(["2026-10-15", "2026-11-02"]), "2026-09")
        self.assertEqual(tracker.views_cutoff(["2026-10-31"]), "2026-10")
        self.assertEqual(tracker.views_cutoff(["2015-01-10"]), None)
        self.assertEqual(tracker.views_cutoff([]), None)

    def test_totals_stop_at_the_cutoff_for_every_file(self):
        snap = tracker.load_json(tracker.latest_snapshot())
        V = tracker.views_summary(snap, tracker.load_json(tracker.UPLOADS_FILE, {}))
        if not V:
            self.skipTest("no view counts yet")
        vf = tracker.load_json(tracker.VIEWS_FILE)["files"]
        cutoff = V["cutoff_month"]
        expected = sum(v[0] for pid in snap["files"] if pid in vf for m, v in vf[pid][2].items() if m <= cutoff)
        self.assertEqual(V["all"], expected)
        self.assertGreaterEqual(V["all"], V["human"])
        later = sum(v[0] for pid in snap["files"] if pid in vf for m, v in vf[pid][2].items() if m > cutoff)
        self.assertEqual(V["after_cutoff"][0], later)


class RetryAndRefreshTests(unittest.TestCase):
    def test_failed_lookup_is_retried_and_rename_refreshed(self):
        with tempfile.TemporaryDirectory() as tmp:
            uploads = Path(tmp) / "uploads.json.gz"
            # File 30 failed before (None); file 60 is cached with an uploader who has since been renamed.
            tracker.save_json(uploads, {"30": None, "60": ["2020-01-01T00:00:00Z", "OldName"]})
            api = FakeAPI({"30": [("2021-05-05T00:00:00Z", "Alice", 10)], "60": [("2020-01-01T00:00:00Z", "NewName", 5)]})
            with mock.patch.object(tracker, "UPLOADS_FILE", uploads), mock.patch.object(tracker.time, "time", return_value=0):
                n = tracker.update_upload_dates(api, ["30", "60"], refresh=True)  # day 0: ids divisible by 30 refreshed
            cache = tracker.load_json(uploads)
            self.assertEqual(n, 1)
            self.assertEqual(cache["30"], ["2021-05-05T00:00:00Z", "Alice"])
            self.assertEqual(cache["60"], ["2020-01-01T00:00:00Z", "NewName"])

    def test_current_sizes(self):
        api = FakeAPI({"1": [("2020-01-01T00:00:00Z", "A", 100), ("2024-01-01T00:00:00Z", "B", 250)]})
        self.assertEqual(tracker.current_sizes(api, ["1", "2"]), {"1": 250, "2": None})


class PayloadTests(unittest.TestCase):
    def test_report_data_is_computed_without_writing(self):
        before = tracker.REMOVED_CSV.stat().st_mtime_ns if tracker.REMOVED_CSV.exists() else None
        D = report.dashboard_data()
        after = tracker.REMOVED_CSV.stat().st_mtime_ns if tracker.REMOVED_CSV.exists() else None
        self.assertEqual(before, after)
        self.assertTrue(D["snapshot"])
        self.assertTrue(D["data_status"])
        self.assertEqual(D["data_status"][0]["dataset"], "snapshot")

    def test_data_status_covers_every_dataset(self):
        snap_path = tracker.latest_snapshot()
        rows = provenance.data_status(tracker.load_json(snap_path), snap_path.name)
        self.assertEqual({r["dataset"] for r in rows},
                         {"snapshot"} | {name for _, name, _ in provenance.STATUS_ROWS})
        for r in rows:
            if r["share"] is not None:
                self.assertLessEqual(r["covered"], r["of"])


if __name__ == "__main__":
    unittest.main()
