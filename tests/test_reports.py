"""Tests of Phase 3: issued reports (IDs, register, verification, corrections), checks, wording, credits, privacy.

    python -m unittest discover -s tests
    set REOA_SLOW_TESTS=1 to also make a real PDF and rebuild it from its archive state (about a minute)
"""

import json
import os
import re
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import issuance  # noqa: E402
import report  # noqa: E402
import validate  # noqa: E402

BANNED = r"\b(exceptional|remarkable|outstanding|massive|flawless|rigorous|verified human|audited|accredit\w*|guarantee\w*|proves)\b"


class RegisterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        pub = Path(self.tmp.name)
        self.patches = [mock.patch.object(issuance, "PUBLISHED", pub),
                        mock.patch.object(issuance, "REGISTER", pub / "register.jsonl"),
                        mock.patch.object(issuance, "REGISTER_CSV", pub / "register.csv"),
                        mock.patch.object(issuance, "MANIFESTS", pub / "manifests"),
                        mock.patch.object(issuance, "ERRATA", pub / "ERRATA.md")]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def manifest(self, rid, pdf):
        return {"report_id": rid, "type": "project", "subject": "Category:X", "generated_at": "2026-11-05T08:00:00Z",
                "status": "FINAL", "pdf_sha256": issuance.sha256(pdf), "html_sha256": "h", "figures_sha256": "f",
                "archive_state": "s", "archive_state_sha256": "a"}

    def test_issue_verify_supersede(self):
        pdf1, pdf2 = Path(self.tmp.name) / "a.pdf", Path(self.tmp.name) / "b.pdf"
        pdf1.write_bytes(b"%PDF one")
        pdf2.write_bytes(b"%PDF two")
        issuance.issue(self.manifest("KRK-P-1", pdf1.read_bytes()))
        issuance.issue(self.manifest("KRK-P-2", pdf2.read_bytes()))
        ok, lines = issuance.verify_pdf(pdf1)
        self.assertTrue(ok, lines)
        issuance.supersede("KRK-P-1", "KRK-P-2", "A wrong total.")
        ok, lines = issuance.verify_pdf(pdf1)
        self.assertFalse(ok)
        self.assertTrue(any("SUPERSEDED by KRK-P-2" in x for x in lines))
        changed = Path(self.tmp.name) / "c.pdf"
        changed.write_bytes(pdf2.read_bytes() + b"x")
        self.assertFalse(issuance.verify_pdf(changed)[0])
        self.assertEqual(issuance.check_chain(), [])

    def test_tampering_is_detected(self):
        pdf = Path(self.tmp.name) / "a.pdf"
        pdf.write_bytes(b"%PDF")
        issuance.issue(self.manifest("KRK-P-1", pdf.read_bytes()))
        issuance.issue(self.manifest("KRK-P-2", b"other"))
        text = issuance.REGISTER.read_text(encoding="utf-8").replace('"status": "FINAL"', '"status": "FINAL "', 1)
        issuance.REGISTER.write_text(text, encoding="utf-8")
        self.assertTrue(issuance.check_chain())
        # A changed manifest is detected too.
        m = json.loads((issuance.MANIFESTS / "KRK-P-2.json").read_text(encoding="utf-8"))
        m["status"] = "PROVISIONAL"
        (issuance.MANIFESTS / "KRK-P-2.json").write_text(json.dumps(m), encoding="utf-8")
        self.assertTrue(any("manifest was changed" in p for p in issuance.check_chain()))

    def test_report_id(self):
        rid = issuance.report_id("project", datetime(2026, 11, 5, 8, 15, tzinfo=timezone.utc), {"a": 1})
        self.assertRegex(rid, r"^KRK-P-20261105-0815Z-[0-9a-f]{8}$")


class StatusTests(unittest.TestCase):
    def test_status(self):
        items = [{"id": "E01", "level": "error", "name": "x", "result": "passed", "detail": ""},
                 {"id": "W01", "level": "warning", "name": "y", "result": "failed", "detail": ""}]
        self.assertEqual(validate.status_of(items)[0], "FINAL")
        items.append({"id": "F02", "level": "freshness", "name": "z", "result": "failed", "detail": "old"})
        status, reasons = validate.status_of(items)
        self.assertEqual(status, "PROVISIONAL")
        self.assertEqual(len(reasons), 1)


class WordingTests(unittest.TestCase):
    def test_no_promotional_or_overstated_words(self):
        for name in ("report.py", "validate.py", "issuance.py"):
            text = (BASE / name).read_text(encoding="utf-8")
            self.assertIsNone(re.search(BANNED, text, re.I), name)

    def test_neutral_labels(self):
        text = (BASE / "report.py").read_text(encoding="utf-8")
        for old in ('"Views"', "by people", "By people", '"Contributors"', "All-time views", "Most viewed", "views counted"):
            self.assertFalse(old in text, f"report still says {old!r}")

    def test_dashboard_uses_the_same_wording(self):
        text = (BASE / "dashboard_template.html").read_text(encoding="utf-8")
        for old in ("by people", "By people", "All-time views", "Views so far", "Most viewed", 'label: "Views"',
                    '"Contributors"', '"Contributor"', 'label: "Contributor', ">Views<", ">Contributors<", "Views · Korikath",
                    "Contributors · Korikath", "Files by contributor"):
            self.assertFalse(old in text, f"dashboard still says {old!r}")
        # OpenStreetMap's credit line is the one place "contributors" must stay.
        self.assertIn("OpenStreetMap contributors", text)


class DashboardStructureTests(unittest.TestCase):
    def test_five_pages_and_every_link_leads_somewhere(self):
        text = (BASE / "dashboard_template.html").read_text(encoding="utf-8")
        pages = re.findall(r'<div id="page-([a-z]+)"', text)
        self.assertEqual(pages, ["home", "collection", "reach", "uploaders", "activity"])
        sections = {"collection": {"growth", "tree", "map"}, "reach": {"usage", "file-requests"},
                    "uploaders": {"files"}, "activity": {"changes", "usage-changes", "crawls"}}
        for page, parts in sections.items():
            for part in parts:
                self.assertTrue(re.search(rf'"?{re.escape(part)}"?: "sec-', text), f"no section {page}/{part} in the router")
        for link in set(re.findall(r'#/([a-z-]*)(?:/([a-z-]+))?', text)):
            page, part = link
            if not page:
                continue
            self.assertIn(page, sections, f"link to unknown page #/{page}")
            if part:
                self.assertIn(part, sections[page], f"link to unknown section #/{page}/{part}")
        for sid in ("sec-growth", "sec-tree", "sec-map", "sec-usage", "sec-requests", "sec-files", "sec-changes",
                    "sec-usage-changes", "sec-crawls"):
            self.assertTrue(f'id="{sid}"' in text, f"no element {sid}")


class CreditTests(unittest.TestCase):
    def fake(self, metadata):
        class API:
            def __init__(self, *a, **k):
                pass

            def query_all(self, **params):
                yield {"query": {"pages": [{"title": t, "imageinfo": [{"extmetadata": {k: {"value": v} for k, v in m.items()}}]}
                                           for t, m in metadata.items()]}}
        return API

    def test_credits_and_safe_filter(self):
        meta = {"File:A.jpg": {"Artist": "<a href='x'>Ann</a>", "LicenseShortName": "CC BY-SA 4.0", "LicenseUrl": "u"},
                "File:B.jpg": {"Artist": "Bo", "LicenseShortName": "CC BY 4.0", "Restrictions": "personality"},
                "File:C.jpg": {"LicenseShortName": "CC0"}}
        with mock.patch.object(report.tracker, "CommonsAPI", self.fake(meta)):
            c = report.fetch_credits(list(meta) + ["File:D.jpg"], {"user_agent": "x", "thumbnails": "safe"})
        self.assertEqual((c["File:A.jpg"]["show"], c["File:A.jpg"]["author"]), (True, "Ann"))
        self.assertFalse(c["File:B.jpg"]["show"])
        self.assertFalse(c["File:C.jpg"]["show"])
        self.assertFalse(c["File:D.jpg"]["show"])

    def test_uncredited_image_is_not_shown(self):
        report.RENDER.update(credits={"File:A.jpg": {"show": False, "reason": "shows identifiable people"}}, wanted=None)
        html = report.thumbs(["File:A.jpg"])
        self.assertNotIn("<img", html)
        self.assertIn("identifiable people", html)


class PrivacyTests(unittest.TestCase):
    def test_opted_out_uploader_is_withheld(self):
        with mock.patch.object(report, "opted_out", return_value={"Someone"}):
            self.assertNotIn("Someone", report.shown("Someone"))
            self.assertEqual(report.shown("Other"), "Other")


@unittest.skipUnless(os.environ.get("REOA_SLOW_TESTS"), "set REOA_SLOW_TESTS=1 to run")
class EndToEndTests(unittest.TestCase):
    def test_issue_then_rebuild_identically(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = report.make_report(None, tmp)
            ok, lines = issuance.verify_pdf(pdf)
            self.assertIn("ISSUED", "\n".join(lines))
            rid = issuance.entries()[-1]["report_id"]
            self.assertTrue(report.rebuild(rid))


if __name__ == "__main__":
    unittest.main()
