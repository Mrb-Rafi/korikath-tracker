"""One-off conversion of the saved data to the formats of tool 2.0.0 (see CHANGELOG.md). Run with
`python tracker.py --migrate`, which records it as a run and archives the state before and after.

1. usage_log.csv: the check times written in Bangladesh time ("26 Oct 2026, 10:33 AM BST (UTC+6)") become UTC
   ("2026-10-26 04:33 UTC"), the format every new row uses.
2. removed_files.csv: rebuilt in the new format by the dashboard build.
3. Every data file gets its metadata sidecar. For data written before 2.0.0, the run that wrote it wasn't
   recorded, and the sidecar says so.

Safe to run again: rows already in UTC are left as they are.
"""

import re
import sys
from datetime import datetime, timedelta, timezone

import archive
import provenance
import tracker

OLD = re.compile(r"^(\d{1,2} \w{3} \d{4}), (\d{1,2}):(\d{2}) (AM|PM) BST \(UTC\+6\)$")


def old_to_utc(text):
    m = OLD.match(text)
    if not m:
        return None
    d = datetime.strptime(f"{m.group(1)} {m.group(2)}:{m.group(3)} {m.group(4)}", "%d %b %Y %I:%M %p")
    return tracker.csv_time((d - timedelta(hours=6)).replace(tzinfo=timezone.utc))


def run():
    crawl = tracker.try_lock()
    if crawl is None:
        print("A crawl is running; run the migration when it has finished.")
        return "skipped"
    # The views job may keep running: it only writes views.json.gz (whose metadata it refreshes itself from the next
    # run on; run `python tracker.py --dashboard` after it ends to refresh it now).
    views = tracker.try_lock(tracker.VIEWS_LOCK)
    if views is None:
        print("The views job is running; its file's metadata will be refreshed when it has finished.")
    try:
        archive.safe_record("before-migration", extra={"note": "State before the 2.0.0 migration (UTC, metadata)."})
        _, rows = tracker.read_usage_log()
        converted = 0
        for r in rows:
            new = old_to_utc(r[0])
            if new:
                r[0] = new
                converted += 1
        if converted:
            tracker.write_usage_log(rows)
        print(f"usage_log.csv: {converted} row(s) converted to UTC.")
        tracker.build_dashboard()  # rewrites removed_files.csv in the new format
        rec = provenance.CURRENT.record if provenance.CURRENT else {"run_id": "migration", "status": "ok"}
        rec = dict(rec, status="ok", finished_at=provenance.utc_now())
        provenance.write_sidecars(list(provenance.data_files()), rec, extra_notes=[
            "Metadata added by the 2.0.0 migration. Data written before 2.0.0 has no run record; "
            "when it was written can be seen in the archive (archive.py list)."])
        print(f"Metadata written for {len(provenance.data_files())} data files.")
        problems = provenance.check()
        print("All data files match their metadata." if not problems else f"{len(problems)} problem(s): {problems}")
    finally:
        if views is not None:
            views.close()
        crawl.close()


if __name__ == "__main__":
    sys.exit("Run it as: python tracker.py --migrate")
