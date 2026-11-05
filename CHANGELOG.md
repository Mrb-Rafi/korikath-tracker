# Changelog

Every version of the tracker's code (tool version) and of what it counts (method version). Each entry says which
figures a change affects. Versions are recorded in every data file's metadata, every run record and every report.

## Tool 2.0.0 · Method M1 · 2026-11-05

The first versioned release. Everything before it is "version 1" (unversioned, 23 Oct – 4 Nov 2026).

**Method M1 (changes to what is counted):**

- **All times are UTC.** Before this, times were shown in Bangladesh Standard Time (UTC+6), labelled "BST". Days and
  months used for grouping are now UTC days and months. This affects:
  - uploads per day and month (the growth charts)
  - promotions per month
  - contributors' first and latest upload and their active months
  - "new in the last 30 days"
  - the take-to-upload gap
  - the views job's day boundary

  A file uploaded between 00:00 and 06:00 Bangladesh time now falls on the previous day (and, at a month's start, in
  the previous month). Totals are unchanged.
- **One methodology register.** Every rule and threshold now lives in `methodology.py` (definitions D01–D18,
  limitations L01–L17), unchanged in value. The only change is that the who-made-it lookup's limits moved there too.

**Tool 2.0.0 (changes to the program):**

- `version.py`: tool version, method version, code commit and build information, recorded everywhere.
- Every data file has a metadata sidecar (`<file>.meta`, JSON): what it is, its format version, the tool and method
  version, when and by which run it was written, how complete it is, and the SHA-256 of the data file it describes.
- Every job writes a run record in `runs/` (what ran, when, with which code, how many requests, whether it finished,
  what it read and wrote), linked to its archive state.
- `usage_log.csv` and `removed_files.csv` times are UTC (`2026-11-04 04:59 UTC`); the old "BST (UTC+6)" texts were
  converted once. The archive keeps the original files.
- PDF file names end in UTC time, e.g. `Korikath_statistics_report_2026-11-05_0523Z.pdf`.

## Before 2.0.0 (unversioned)

- 2026-11-04: who made each change (`attribution.py`): user and diff or log link for category and usage changes.
- 2026-11-04: data archive (`archive.py`): every state of every data file, with a hash-chained register.
- 2026-11-04: "Files by contributor" page (`user_files.py`).
- 2026-10-23 – 2026-11-03: crawler, dashboard, usage, views, assessments, devices, PDF reports.
