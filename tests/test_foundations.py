"""Tests of the reliability foundations: UTC times, the methodology register, versions and data-file metadata.

    python -m unittest discover -s tests       (from the tracker folder)
"""

import re
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import methodology  # noqa: E402
import migrate  # noqa: E402
import provenance  # noqa: E402
import tracker  # noqa: E402
import version  # noqa: E402


class TimeTests(unittest.TestCase):
    def test_formats_are_utc(self):
        t = "2026-11-04T04:59:13+00:00"
        self.assertEqual(tracker.fmt_time(t), "4 Nov 2026, 04:59 UTC")
        self.assertEqual(tracker.csv_time(t), "2026-11-04 04:59 UTC")
        self.assertEqual(tracker.iso_utc("2026-11-04T10:59:13+06:00"), "2026-11-04T04:59:13Z")

    def test_upload_month_is_utc(self):
        # 2026-01-31 20:00 UTC is 1 Feb in Bangladesh; it must count in January.
        files = {"1": {"title": "File:A.jpg", "categories": []}}
        days = tracker.growth_series(files, {"1": ["2026-01-31T20:00:00Z", "X"]})
        self.assertIn("2026-01-31", str(days))

    def test_old_log_times_convert(self):
        self.assertEqual(migrate.old_to_utc("26 Oct 2026, 10:33 AM BST (UTC+6)"), "2026-10-26 04:33 UTC")
        self.assertEqual(migrate.old_to_utc("1 Nov 2026, 12:05 AM BST (UTC+6)"), "2026-10-31 18:05 UTC")
        self.assertIsNone(migrate.old_to_utc("2026-10-26 04:33 UTC"))

    def test_no_local_time_in_code(self):
        for name in ("tracker.py", "report.py", "attribution.py", "server.py", "user_files.py", "dashboard_template.html"):
            text = (BASE / name).read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"\bBST\b|UTC\+6|Asia/Dhaka", text), name)


class MethodologyTests(unittest.TestCase):
    def test_version_matches_rules(self):
        ok, msg = methodology.check()
        self.assertTrue(ok, msg)

    def test_definitions_and_limitations_are_complete(self):
        ids = {x["id"] for x in methodology.LIMITATIONS}
        for d in methodology.DEFINITIONS:
            for key in ("id", "name", "definition", "unit", "source"):
                self.assertTrue(d.get(key), (d.get("id"), key))
            for lim in d.get("limits", []):
                self.assertIn(lim, ids, d["id"])

    def test_tracker_uses_the_register(self):
        self.assertIs(tracker.ASSESS_CATS, methodology.ASSESS_CATS)
        text = (BASE / "tracker.py").read_text(encoding="utf-8")
        for pattern in (r"^IMAGE_EXTS =", r"^BRANDS =", r"^ASSESS_CATS =", r"^POTD_PAGE =", r"0\.25 \*", r"< 2005"):
            self.assertIsNone(re.search(pattern, text, re.M), pattern)

    def test_rules_behave_as_before(self):
        self.assertEqual(tracker.device_of("NIKON CORPORATION", "NIKON D3500"), ("Nikon D3500", "Nikon", "camera"))
        self.assertEqual(tracker.page_date("Template:Potd/2023-03-05"), "2023-03-05")
        self.assertEqual(tracker.appearances(["2023-03-05", "2023-03-06", "2023-04"]), 3)


class VersionTests(unittest.TestCase):
    def test_build_info(self):
        info = version.build_info()
        self.assertEqual(info["tool_version"], version.TOOL_VERSION)
        self.assertEqual(info["method_version"], methodology.METHOD_VERSION)
        self.assertEqual(len(info["method_fingerprint"]), 64)


class ProvenanceTests(unittest.TestCase):
    def test_every_data_file_has_current_metadata(self):
        problems = provenance.check()
        self.assertEqual(problems, [], problems)

    def test_metadata_fields(self):
        for path in provenance.data_files():
            meta = provenance.read_meta(path)
            for key in ("dataset", "source", "tool_version", "method_version", "written_by_run", "as_of", "complete",
                        "data_sha256"):
                self.assertIn(key, meta, (path.name, key))


if __name__ == "__main__":
    unittest.main()
