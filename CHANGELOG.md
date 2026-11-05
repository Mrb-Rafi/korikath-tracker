# Changelog

Every version of the tracker's code (tool version) and of what it counts (method version). Each entry says which
figures a change affects. Versions are recorded in every data file's metadata, every run record and every report.

## Tool 2.2.2 · Method M2 · 2026-11-05

- **Page addresses follow the new names:**
  - `#/file-requests` (was `#/views`)
  - `#/uploaders` and `#/uploaders?u=Name` (was `#/contributors`)
  - `#/uploader-files?c=…&u=…` (was `#/user-files`)

  The old addresses now open the overview.

## Tool 2.2.1 · Method M2 · 2026-11-05

- **The dashboard uses the same wording as the PDF reports:**
  - "File requests" for views, and "Not flagged as automated" for by people
  - "Uploaders" for contributors
  - "Most requested" for most viewed
  - "Files by uploader" for files by contributor

  Only the visible text changed: page addresses (`#/views`, `#/contributors?u=Name`) stay, so saved links keep
  working. "OpenStreetMap contributors" stays, as that is OpenStreetMap's required credit.
- A test keeps the old wording out of the dashboard.

## Tool 2.2.0 · Method M2 · 2026-11-05

No change to what is counted (the method stays M2). The PDF report becomes a document readers can rely on and check:

- **Every report is issued.** It has a report ID (`KRK-P-20261105-0815Z-352ffc6d`; `C` for an uploader report), a
  status (FINAL or PROVISIONAL) and a fingerprint, and is recorded in the register of issued reports
  (`published/register.jsonl`, hash-chained; `published/register.csv` for publishing). Its manifest
  (`published/manifests/<ID>.json`, a few KB) records the archive state of its inputs, the fingerprints of its figures,
  page and PDF, the checks, the image credits and the versions. **The PDF itself is not stored.**
- **Checks before issue** (`validate.py`):
  - errors: the snapshot is consistent, totals add up, view totals are recomputed independently, every data file
    matches its metadata, the rules match the method version, the archive is intact
  - freshness: the code is committed, there was a full crawl in the last 8 days and a crawl in the last 3, the
    datasets cover at least 99% of files and are current, an issuer is named
  - cross-checks: an independent count with PetScan (monthly, `python validate.py --petscan`), a live spot check of
    30 random files, a recent full re-read of the assessment categories

  Any failed error or freshness check makes the report PROVISIONAL: it gets a watermark, the reasons on its cover and
  `_PROVISIONAL` in its file name.
- **Commands:**
  - `python report.py --verify FILE.pdf` checks whether a PDF was issued and gives its status, including SUPERSEDED.
  - `python report.py --rebuild ID` regenerates a report from its archive state, with the code recorded in it, and
    compares figures and page.
  - `python report.py --supersede OLD --by NEW --reason "..."` records a correction, written to `published/ERRATA.md`.
  - `python report.py --register` lists issued reports.
- **New pages in the PDF:**
  - a cover with report ID, status, fingerprint, issuer, versions and how to verify
  - "1. How to rely on this report"
  - "2. Checks before issue"
  - a source line under every section (its data, as of when, how complete, definitions, API)
  - appendices A–H: methods, definitions, limitations (with what they amount to in this report), provenance, method
    versions and corrections, licences, credits and privacy, glossary, how to verify

  The footer of every page carries the report ID and status.
- **Images:** every thumbnail shows its author and licence from Commons. Files without them, or marked as showing
  identifiable people (personality rights), are not reproduced. `config.json` → `thumbnails`: `safe` (default), `all`
  or `none`.
- **Privacy:** `config.json` → `contributor_opt_out` lists uploaders who asked not to be named. They are counted in
  every total, named nowhere in reports, and get no uploader report.
- **Neutral wording:** "file requests" (not "views"), "not flagged as automated" (not "by people"), "uploaders" (not
  "contributors"). A test rejects promotional or overstated words in the report code.
- New `config.json` keys: `issuer` (name, role, contact), `report_licence` (default CC BY 4.0),
  `public_register_url`, `thumbnails`, `contributor_opt_out`.
- Tests: `tests/test_reports.py`.

## Tool 2.1.0 · Method M2 · 2026-11-05

**Method M2 (changes to what is counted):**

- **File requests ("views") are totalled to one common cutoff.** Totals cover complete months from January 2015 to
  the last month that *every* counted file has been counted through (the daily updates reach different files on
  different days). Requests counted after the cutoff are shown apart as partial, and never added. Before, each file's
  total ran to its own last update, so the project total mixed different end dates. This affects:
  - all view totals: project, per category, per file and per uploader, in the dashboard and both PDF reports
  - the yearly table in the report

  On 5 Nov 2026 the cutoff is September 2026. 948,504 requests counted in October and November are left out until
  every file has been counted through October.
- **A thirtieth of the files is re-checked every day** (every file once a month):
  - its original upload time and uploader, since accounts get renamed and early versions get hidden or restored
  - its current size; a file overwritten with a new version has its camera data and size looked up again
  - lookups that found nothing before are retried at every run, instead of never again

**Tool 2.1.0 (changes to the program):**

- **"Data used" table** in the PDF report and on the dashboard's Crawls page: how current each dataset is ("as of",
  UTC) and how many of the crawl's files it covers. The dashboard also shows which tool version, method version and
  code commit built it.
- **Reports compute every figure from the data files directly** (`tracker.build_payload`). Before, they read the
  copy embedded in `dashboard.html`, which could be older than the data.
- **PDFs are no longer stored in the archive.** Only their SHA-256 fingerprint is kept, with the data state they
  were built from, together with a fingerprint of all the figures in the report. `python archive.py find <pdf>` still
  says whether the tracker issued a PDF and from which data.
- **Crawls record when they started and finished** (`crawl_started_at`, `crawl_finished_at` in the snapshot and in
  `history.json`).
- **Label fix:** assessment dates are labelled "in the category since", not "promoted" (limitation L12).
- Tests for all of the above (`tests/test_figures.py`).

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
