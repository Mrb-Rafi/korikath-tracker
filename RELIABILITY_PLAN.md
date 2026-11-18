# Reoa: reliability plan

**Purpose.** This document says what has to change in Reoa, both its data files and its code, so
that every number it produces can be **relied on, checked and defended**. It also says how the PDF report must
present and explain those guarantees. The PDF is the one document that goes to the public: donors, evaluators,
project leadership, the Wikimedia community and the press.

**Status.** Written 4 Nov 2026 from a review of `tracker.py`, `report.py`, `server.py`, the data files in this
folder and the PDF `../Project_Korikath_Comprehensive_Statistics_Report.pdf`.

| Date (UTC) | Change to this plan |
|---|---|
| 2026-11-04 | First version. |
| 2026-11-04 | **Decision: UTC everywhere.** All times the tool stores *or shows* (dashboard, CSV files, PDF reports, file names, logs) are in UTC, written with an explicit "UTC" or ISO 8601 `Z`. No local time zone is shown. This replaces the earlier proposal of "Bangladesh time (UTC+06:00)". See WP8.5. Not implemented yet. |
| 2026-11-04 | **WP5 (archiving) implemented** in `archive.py`. The first state was recorded at 2026-11-04 07:47:57 UTC. See WP5 for what is done and what remains. |
| 2026-11-05 | **Phase 3 implemented (tool 2.2.0).** WP6, WP7, WP10, WP11 and Part 7 are done, and part of WP13 (issuer, statuses, corrections, register). See the status notes under each. Not done: PDF metadata beyond title, digital signatures, the public copy of the register (WP13.4/5, Phase 4), and the dashboard's own wording (WP11 covered the PDF only). |
| 2026-11-05 | **Phase 2 implemented (tool 2.1.0, method M2).** See the status notes under WP8, WP9, WP14 and WP15. **Decision on storage:** issued PDFs are not stored; the archive and (Phase 4) the public register keep only each PDF's SHA-256, plus a fingerprint of its figures and the archived data and code it was built from, which are enough to verify a PDF and to regenerate it. WP6's bundle will reference archived data by hash instead of copying it, and keep figures.json (small) rather than the PDF. |
| 2026-11-05 | **Phase 1 implemented (tool 2.0.0, method M1).** WP8.5 UTC everywhere; WP1 versioning (`version.py`, `CHANGELOG.md`, git repository); WP4 methodology register (`methodology.py`, with D01–D18 and L01–L17); WP2 data-file metadata; WP3 run records (`provenance.py`, `runs/`); first tests (`tests/`). Two deliberate changes from the plan's wording, both below: metadata are sidecar files (`<file>.meta`), not an envelope inside each data file, and run records rely on the archive's hash-chained register instead of a second register. |

**How to use it.** Work through Part 4 (work packages) in the order given in Part 8. Each work package has
**acceptance criteria**. A package is done only when all of its criteria pass. Part 9 is the final checklist
before the first public PDF.

---

## Contents

1. [Principles](#1-principles)
2. [The reliability features](#2-the-reliability-features)
3. [Where the tool stands today](#3-where-the-tool-stands-today)
4. [Work packages: internal mechanisms](#4-work-packages-internal-mechanisms)
5. [Data file requirements, file by file](#5-data-file-requirements-file-by-file)
6. [Code file requirements, file by file](#6-code-file-requirements-file-by-file)
7. [What the PDF report must contain](#7-what-the-pdf-report-must-contain)
8. [Order of work](#8-order-of-work)
9. [Final acceptance checklist](#9-final-acceptance-checklist)
10. [Appendix A: problems found in the existing PDF](#appendix-a-problems-found-in-the-existing-pdf)
11. [Appendix B: draft text for the PDF's "How to rely on this report" section](#appendix-b-draft-text-for-the-pdfs-how-to-rely-on-this-report-section)
12. [Appendix C: known limitations to disclose](#appendix-c-known-limitations-to-disclose)

---

## 1. Principles

These apply to every change below.

1. **The PDF says nothing the data does not say.** Every number, chart and sentence in the report is computed
   from saved data by code. There is no hand-written or AI-written narrative, no adjectives of praise, and no
   claims about content ("folk music recordings") that the data does not hold.
2. **Every published number can be traced and recomputed.** Given a report's ID, anyone with the archive can get
   back the exact input data and the exact code version, and recompute every number.
3. **Nothing that a report was built on is ever overwritten or deleted.** Data used by an issued report is frozen
   and kept.
4. **Partial or stale data is never shown as complete.** Coverage and "as of" dates travel with every figure.
   When something is incomplete, the report says so, and in the worst cases the report is marked
   **PROVISIONAL**.
5. **Definitions are fixed, written down and versioned.** A change in how something is counted is a new method
   version, recorded in the report.
6. **Honest about limits.** The report lists what it cannot see, what is estimated or inferred, and how large the
   effect is where that can be measured.
7. **Legally clean.** Images are credited, licences are respected, people are treated fairly, and Wikimedia's
   terms are followed.

---

## 2. The reliability features

These are the features a statistical artifact needs before it can be relied on. Each one has an ID used in the
rest of this document.

| ID | Feature | What it means for this tool |
|---|---|---|
| **F1** | **Provenance** | For every figure: the source (which Wikimedia API, which endpoint), the query and parameters, when it was fetched, by which run, and with which tool version. |
| **F2** | **Fixed reference time** | Each dataset has an "as of" time, kept separate from the time the report was generated. When different parts of the data come from different times, all of those times are shown. |
| **F3** | **Reproducibility** | (a) *Recomputation*: the same frozen inputs and the same code version give the same numbers, every time. Required. (b) *Re-collection*: running the queries again against Wikimedia today gives the same numbers. This is impossible because Commons changes, so it is replaced by archiving the inputs and linking each one to its source and time. |
| **F4** | **Integrity** | Inputs and outputs have cryptographic fingerprints (SHA-256), so any later change is detectable. Issued reports are recorded in a tamper-evident register. Optionally, reports are digitally signed. |
| **F5** | **Versioning** | Code version (software) and method version (definitions and rules) are recorded in every data file, every run and every report. |
| **F6** | **Definitions** | Every metric has a written definition, including its unit, what is counted, what is excluded and how it is rounded. |
| **F7** | **Coverage and completeness** | For every metric: how many of the files it covers, and whether the job that produced it finished. |
| **F8** | **Validation** | Automatic checks (invariants, cross-checks against Commons, plausibility limits) run before a report is made. The results are recorded and shown. |
| **F9** | **Limitations and uncertainty** | Known blind spots, inferences and heuristics are listed and, where possible, quantified. |
| **F10** | **Internal consistency** | The same quantity has the same value everywhere in a report. Totals add up. Rounding rules are stated. |
| **F11** | **Neutral, accurate language** | Labels match what is measured: "file requests", not "readers"; "uploader", not "photographer". There is no marketing language. |
| **F12** | **Legal and ethical compliance** | Image attribution and licences, the licence of the data and of the report, Wikimedia trademark and API terms, privacy of named contributors, and a "not affiliated" statement. |
| **F13** | **Issuance and corrections** | Each issued report has a unique ID, an issuer, a status (FINAL / PROVISIONAL / SUPERSEDED), and an errata process. |
| **F14** | **Testing** | Automated tests show that each rule and computation does what its definition says. |
| **F15** | **Verifiability by outsiders** | A reader can check a report: the ID and fingerprint are printed, the register is published, and there are instructions to verify it and to spot-check figures on Commons. |

---

## 3. Where the tool stands today

| ID | Status | What exists | What is missing |
|---|---|---|---|
| F1 | ⚠️ Partial | Data comes only from public Wikimedia APIs (Commons Action API, Analytics/AQS `mediarequests`). The PDF links to files, categories and deletion logs. | No record per figure of endpoint, parameters, run and tool version. Data files carry no metadata header. |
| F2 | ✅ Mostly | The PDF states the generation time, the crawl time, and when usage, assessments and views were checked. | The crawl is recorded as one moment, but it actually takes 3–27 minutes; there is no end time. Times are shown in local time labelled "BST", which most international readers take as *British* Summer Time. Decision: show UTC everywhere (WP8.5). View totals sum each file up to a different date (see F10). |
| F3 | ⚠️ Partial (improved 4 Nov 2026) | Crawl snapshots, reports and `history.json` are kept. `python tracker.py --dashboard` recomputes everything offline. **Since 2026-11-04 07:47 UTC, every state of every data file is archived after each job (`archive.py`, WP5),** and each PDF report is stored with the exact data it was built from. | Data states from **before** 2026-11-04 07:47 UTC were overwritten and cannot be recovered (only snapshots and crawl reports go back to 23 Oct 2026). Thumbnails are still fetched live when the PDF is printed. No report bundle or `--rebuild` yet (WP6). |
| F4 | ⚠️ Partial (improved 4 Nov 2026) | Writes are atomic. The archive stores every file by SHA-256, and its register is hash-chained; `python archive.py verify` detects edited, missing or damaged entries. `python archive.py find <pdf>` says whether a PDF was produced by the tool and from which state. | No report ID or fingerprint printed in the PDF, no public register, no signatures. The newest register line can be changed undetected unless its hash has been published somewhere else (WP13.4). |
| F5 | ⚠️ Minimal | Each archive state records a `code_fingerprint` (SHA-256 over the code files) and the code files themselves, so the code behind any state can be recovered. | No version number. The folder is not a git repository. No method version. |
| F6 | ⚠️ Partial | Docstrings and the README explain most rules. Some PDF sections have one-line notes. | No single definitions list. The PDF lacks most definitions (see Appendix C). |
| F7 | ⚠️ Partial | Views coverage ("X of Y files") is shown. "Top N of M" notes exist. | No coverage for uploads, EXIF, geo or media types. No record of whether a job finished or was interrupted. |
| F8 | ❌ Missing | The quick check re-reads a category in full when counters don't add up (a good internal check). | No validation suite, no cross-check against an independent source, and no comparison of quick-check results with the following full crawl. |
| F9 | ❌ Missing in PDF | The README mentions some limits (quick check can't see renames; main-page detection may miss wikis). | No limitations section in the PDF. |
| F10 | ⚠️ Mostly | Both reports read one data build (`dashboard.html`). | The contributor report mixes the dashboard data with the newest snapshot (`contributor_details` loads `latest_snapshot()` directly). Views are summed over different per-file end dates. The data in `dashboard.html` can change mid-run (the views job rebuilds it every 500 files). |
| F11 | ✅ In `report.py` | `report.py` uses plain wording. | "Views" and "by people" overstate what is measured. Should be "file requests" and "requests not flagged as automated". |
| F12 | ⚠️ Gaps | Rate limits, maxlag and User-Agent are respected. | Thumbnails are printed **without author or licence**. No licence statement for the report or the data. No privacy notice for contributor reports. No "not affiliated with the Wikimedia Foundation" statement. The User-Agent has no contact address. |
| F13 | ❌ Missing | The file name holds the generation time. | No report ID, no issuer, no status, no errata process. |
| F14 | ❌ Missing | None. | No tests at all. |
| F15 | ❌ Missing | Links to Commons allow manual spot checks. | No ID or fingerprint, no public register, no verification instructions. |

**Separate, urgent:** the PDF at `E:\korikath_stats_tool\Project_Korikath_Comprehensive_Statistics_Report.pdf` was
**not made by this tool**. Its metadata says it was made with ReportLab, while `report.py` prints through headless
Edge/Chrome. It contains contradictions, invented descriptions and broken tables (Appendix A). It should be
withdrawn. If it has been shared, a correction should follow.

---

## 4. Work packages: internal mechanisms

### WP1: Version control and versioning (F5)

> **Status 2026-11-05: implemented.** `version.py` (TOOL_VERSION 2.0.0, SCHEMA_VERSIONS, `build_info()`), `CHANGELOG.md`,
> `python tracker.py --version`, and a git repository with `.gitignore`. Every data file's metadata, every run record and
> (in Phase 3) every report records `build_info()`. Still open: the rule "FINAL only from committed code" belongs to
> WP7 and WP6.

**Do:**

1. Turn the Reoa folder into a git repository (`git init`). Add a `.gitignore` covering data files, `logs/`,
   `*.lock`, `status.js`, `views_status.js`, `dashboard.html`, `map_points.js`, `*.tmp` and `published/`. Code,
   the template, `config.json`, the README, this plan and tests stay under version control.
2. Add `version.py` with:
   - `TOOL_VERSION = "2.0.0"` (semantic versioning: MAJOR changes output formats, MINOR adds metrics, PATCH fixes
     bugs).
   - `METHOD_VERSION = "M1"`. Bump it whenever **any definition or rule** in the methodology register (WP4)
     changes, even if the code change is tiny.
   - `SCHEMA_VERSIONS = {"snapshot": 2, "usage": 2, ...}`, one entry per data file type.
   - A function `build_info()` that returns: tool version, method version, git commit hash, a `dirty` flag (any
     uncommitted change), Python version, OS, and the hash of `config.json`.
3. Add `CHANGELOG.md` with one entry per tool or method version: what changed and which numbers it affects.
4. Rule: a report can only be **FINAL** if `dirty` is false, meaning it was built from committed code.

**Acceptance:** `python tracker.py --version` prints the tool version, method version, commit and dirty flag.
Every data file (WP2), run record (WP3) and report (WP6) contains `build_info()`.

---

### WP2: A standard header for every data file (F1, F2, F5, F7)

> **Status 2026-11-05: implemented, as sidecar files.** Wrapping each data file in an envelope would have changed the
> format every reader expects. Instead each data file has `<file>.meta` (JSON) next to it, with the fields below, plus
> `data_sha256`, the SHA-256 of the data file. That binds the two: a sidecar that no longer matches its file is
> detected (`python provenance.py`, and a test).
> - Sidecars are refreshed at the end of every job for the files it changed.
> - `based_on_snapshot` is the crawl that was current at the data's "as of" time.
> - Coverage is counted against that crawl.
> - The usage-log and removed-files CSV files carry UTC times (WP8.5). Separate machine columns were not needed,
>   since the new format ("2026-11-04 04:59 UTC") sorts correctly.
>
> Not done: a JSON-schema check of each dataset's content.

Today each data file is bare data. Wrap every JSON/JSON.gz data file in a standard envelope:

```json
{
  "meta": {
    "dataset": "usage",
    "schema_version": 2,
    "tool_version": "2.0.0",
    "method_version": "M1",
    "git_commit": "abc1234",
    "run_id": "2026-11-04T045524Z-quick",
    "source": {
      "api": "https://commons.wikimedia.org/w/api.php",
      "module": "prop=globalusage",
      "params": {"gulimit": "max", "gufilterlocal": "1"}
    },
    "started_at": "2026-11-04T05:10:02Z",
    "finished_at": "2026-11-04T05:30:41Z",
    "as_of": "2026-11-04T05:30:41Z",
    "complete": true,
    "coverage": {"files_expected": 96428, "files_covered": 96428, "files_failed": 0},
    "based_on_snapshot": "2026-11-04T045524Z.json.gz",
    "based_on_snapshot_sha256": "…",
    "records": 2509,
    "notes": []
  },
  "data": { "…": "…" }
}
```

Rules:

- `load_json` and `save_json` gain `load_dataset` and `save_dataset` variants. These fill in or check `meta`.
  Old files without `meta` are read through a one-off migration that adds `meta` with `"migrated": true` and
  whatever can be reconstructed.
- `complete` is `false` whenever a job ended early: an interrupted views run, network failures, the
  `max_categories` safety stop, or any batch that errored. The report reads this flag (WP8).
- `coverage` is filled in for every dataset that is per-file.
- **Timestamps:** store all timestamps in ISO 8601 UTC with `Z` (e.g. `2026-11-04T05:30:41Z`). Display them in UTC too (WP8.5).
- **CSV files** get the same information as a sidecar `<name>.meta.json`. They also get machine columns next to
  the human ones: `checked_at_utc` (ISO) beside `checked_at`, and `page_id` beside `file`.
- Fix stale documentation: the `EXIF_FILE` comment in `tracker.py` says `[make, model, gps lat, gps lon]`, but
  records are `[make, model, lat, lon, taken, size]`.

**Acceptance:** every data file in the folder opens with a `meta` block that passes a schema check. A test loads
each file and checks its `meta` fields.

---

### WP3: Run records and a tamper-evident register (F1, F4, F13)

> **Status 2026-11-05: implemented, except the request ledger (point 3).** `runs/<time>-<job>.json` records:
> - the job, how it was started, and its start and end times
> - whether it finished (ok, failed with the error, or skipped because another job was running)
> - `build_info()`
> - the API requests it made, per wiki
> - the SHA-256 of every data file it read and wrote
>
> The archive state recorded right after it holds the run record and names the run (`extra.run_id`), and the archive's
> register is hash-chained, so a second register was not added.

1. **Run records.** Every job writes `runs/<run_id>.json`. Jobs are crawls (quick or full), usage checks,
   assessment checks, upload-date lookups, file-detail lookups, view jobs, dashboard builds and report builds.
   Each record holds:
   - run ID, job type, trigger (scheduled, button or command line), and start and end times in UTC
   - `build_info()`
   - the `config.json` hash
   - the request count per API, retries, maxlag waits and errors, with a sample of the error messages
   - inputs read (path and SHA-256) and outputs written (path and SHA-256)
   - `complete` true or false, with the reason if false
   - for crawls: mode (quick or full), and the categories re-read in full by the quick check, with the reason for
     each
2. **Register.** Append one line per run to `runs/register.jsonl`:
   `{run_id, job, finished_at, outputs_sha256, prev_line_sha256}`. Each line contains the hash of the previous
   line (a hash chain), so deleting or editing a past entry breaks the chain and is detectable. Add
   `tracker.py --verify-register` to check the chain.
3. **Request ledger (optional).** For full crawls, record for each API response its endpoint, a hash of its
   parameters, a hash of the response, and Commons' `curtimestamp` / `servedby` (request
   `curtimestamp=1`). This costs a few hundred kilobytes per crawl. It proves which responses the numbers came
   from, without storing the responses themselves.

**Acceptance:** after a scheduled run, `runs/` holds its record. `--verify-register` passes. Deliberately editing
an old line makes it fail.

---

### WP4: A methodology register: one place for every definition and rule (F6, F9, F11)

> **Status 2026-11-05: implemented.** `methodology.py` holds:
> - every rule constant moved from `tracker.py` and `attribution.py`, unchanged in value
> - the named thresholds that were literals before: `CATCH_ALL_SHARE`, `WRONG_CLOCK_*`, `QUICK_CHECK_OVERLAP`,
>   `ROTATION_DAYS` and `ATTRIBUTION_*`
> - DEFINITIONS D01–D18 and LIMITATIONS L01–L17 (Appendix C, extended)
>
> `methodology_versions.json` records each method version's fingerprint. `python methodology.py --check`, which is
> also a test, fails when the rules change without a version bump. Report rendering of the definitions comes with
> Phase 3 (Part 7).

Create `methodology.py`, or `methodology.json` plus a loader, as the **single source** for every definition,
threshold and heuristic. Both the code and the PDF must take it from here. Code takes the constants. The PDF
prints the definitions and parameters.

For each metric, record: `id`, `name` (as printed), `definition`, `unit`, `source`, `includes`, `excludes`,
`rounding`, `known_limitations` (IDs from Appendix C), and the method version in which it last changed.

Rules that must move into the register, with their current values:

| Rule | Current value / location |
|---|---|
| Project membership | All files in the category tree below `Category:Project Korikath`, unlimited depth, de-duplicated by page ID (`crawl`). |
| Excluded categories / safety stop | `config.json` `exclude_categories`, `max_categories = 5000`. |
| Quick-check inference | Counter arithmetic plus additions since the previous crawl, with a 1-hour overlap. Categories are re-read in full on mismatch (`crawl`, `_run`). |
| Upload date / uploader | Earliest file revision's timestamp and user (`fetch_upload_dates`). |
| Media kind | By extension for images (`IMAGE_EXTS`), otherwise the Commons `mediatype` (`MEDIA_KINDS`). |
| Quality / featured / valued / featured media | Membership of the Commons-wide categories in `ASSESS_CATS`. Incremental checks after the first full one. |
| Promotion date | Category-membership timestamp. **Caveat:** this is the time the file was last added to the category, which may not be the promotion date. |
| POTD / MOTD on Commons | `DAY_TEMPLATE` regular expression on the templates that use the file. |
| Main page elsewhere | Page-name regular expressions `POTD_PAGE`, `MOTD_PAGE` and `NOT_MAIN_PAGE`, plus `page_date`. |
| "Times on a main page" | `appearances()`: each distinct day once, each month without day pages once, at least 1 per wiki. |
| On-wiki usage | `globalusage`, excluding Commons itself. "Content pages" means namespace 0. |
| File requests ("views") | AQS `mediarequests/per-file/all-referers`, `all-agents` and `user`, since 2015-01-01. Complete months only. The daily data is a rolling 1/30 of files. |
| "By people" | AQS agent type `user`, meaning requests not identified as automated. Not verified to be human. |
| Contributor | The original uploader of a file currently in the project. |
| Catch-all categories | Any category directly holding more than 25% of all files (`contributor_summary`). |
| Device kind / brand | `BRANDS`, `PHONE_BRANDS`, `CAMERA_BRANDS`, `SCANNER_BRANDS` and `device_of`. |
| Take-to-upload gap | Upload date (local time) minus EXIF date taken. Counted as wrong clock if the gap is under −1 day or the year taken is before 2005. Negative gaps of up to 1 day are counted as 0 (`upload_gap_summary`). |
| Geocoded | The file page has primary `{{Location}}` coordinates on Earth. "GPS only" means EXIF GPS present but no page coordinates. |
| Size | Byte size of the **current** file version (not the original upload). |
| Medians | Middle value. For an even count, the mean of the two middle values. |
| Percentages | Rounded to 1 decimal place (to 2 below 1%). Rounded parts may not sum to 100%. |
| Time zone | **UTC** for everything stored and shown. Monthly and daily groupings (uploads per month, file requests per day, "new in the last 30 days") use UTC calendar days and months. Today they use Bangladesh time, so a few files near midnight move to the neighbouring day or month when this changes; that change gets a new method version. |

**Acceptance:** `grep` finds no threshold, regular expression or category constant defined outside the register
(except imports). The PDF's definitions appendix is generated from the register. Changing a register value
without bumping `METHOD_VERSION` makes a test fail. The test stores a hash of the register per method version.

---

### WP5: Never overwrite. Dated, content-addressed archives (F3, F4): **IMPLEMENTED 2026-11-04**

**Problem:** eight data files were overwritten in place, so past reports could not be recomputed.

**What is implemented** (`archive.py`, hooked into `tracker.py` and `report.py`):

1. **Object store** `archive/objects/<aa>/<sha256>` (files that are already gzip, stored as they are) or
   `<sha256>.gz` (everything else, gzip-compressed). Each distinct content is stored once, named by the SHA-256
   of its original bytes, so an unchanged file costs nothing.
2. **States** `archive/states/<seq>_<UTC time>_<job>.json`. One is written after **every job**: quick or full
   crawl, usage check, views job, upload-date lookup, file-detail lookup, assessment check, and every PDF report.
   The state is written whether the job succeeded or failed (`status`, `error`). It lists every data file, crawl
   snapshot, crawl report, CSV, log and code file with its SHA-256, size and modification time (UTC). It also
   holds the job, the command line, Python version, OS and a `code_fingerprint`. A plain `--dashboard` rebuild
   changes no data and is not archived.
3. **Report states.** When a PDF is made (dashboard button or `report.py`), the state also stores
   `dashboard.html` (the data the report is built from) and **the PDF itself** (as
   `issued_reports/<file name>`), and records the PDF's SHA-256, report type, contributor and generation time.
4. **Register** `archive/register.jsonl`. One line per state with the state file's SHA-256 and the SHA-256 of the
   previous line (a hash chain).
5. **Commands:**
   - `python archive.py list` lists the states and the space each added.
   - `python archive.py verify` checks the chain, every state file and every object. It fails on any edit,
     deletion or damage.
   - `python archive.py restore <seq|state|latest> <empty folder>` rebuilds all files of a state, checking every
     hash.
   - `python archive.py find <file>` says which state(s) recorded that exact file. Use it to check whether a PDF
     was issued by the tool, and what it was built from.
   - `python archive.py snapshot --note "…"` records a state by hand.
6. **Deterministic gzip.** `tracker.save_json` now writes gzip files without a timestamp or file name in the
   header, so the same data always gives the same bytes and is stored once.
7. **Concurrency.** A lock file (`archive/archive.lock`) means only one process writes to the archive at a time.
   Reads retry while another job is replacing a file. `save_json` retries its final rename if a reader holds the
   file (Windows).
8. **Safety.** An archive problem never fails the job: it prints a warning to the job's log, and the job's own
   result stands.

**Verified on 2026-11-04:**

- The first state (seq 1, 62 files, 22.6 MB) was restored into an empty folder and was byte-identical to the
  live files.
- On a copy of the folder:
  - successful and failed jobs were each recorded
  - a report state stored its PDF, and `find` located it
  - `verify` detected an edited register line, a damaged object and an edited state file

**Known gaps:**

- Data from before 2026-11-04 07:47 UTC was never archived and is lost, except snapshots and crawl reports,
  which go back to 2026-10-23.
- The newest register line can be altered without detection. Publishing the latest line's hash (WP13.4) closes
  this.
- **Size.** About 6–7 MB per daily crawl state (views 5.1 MB, snapshot 0.9 MB, usage, geo, assessments and
  logs). Views-job states add about 5 MB more. That comes to roughly 4 GB a year. The E: drive had 156 GB free on
  4 Nov 2026.

**Still to do:**

1. **Backup.** Copy `archive/` (and later `published/`) to a second disk or cloud drive on a schedule. Add a
   `backup_dir` setting and an `archive.py backup` command that copies new objects, states and the register, then
   runs `verify` on the copy.
2. **Retention / prune.** Keep everything referenced by a report state forever, and full-crawl states for at least
   2 years. Add an `archive.py prune` that removes only other states' unreferenced objects, never a report's, and
   records the pruning in the register. Not needed until disk space is a concern.
3. **Links from data files.** Record `snapshot_sha256` in `history.json` entries, and the archive state of each
   run in WP3 run records and WP2 `meta` (`archive_state`).
4. **Dashboard.** Show the latest archive state and the result of the last `verify` on the Crawls page.
5. Add `archive/` to the backup and `.gitignore` when WP1 creates the git repository.

**Acceptance (met for 1–3, open for 4):**

1. After any job, `archive.py restore` rebuilds the exact files as they were after that job.
2. `archive.py verify` passes, and fails on tampering.
3. Every PDF made by the tool can be found with `archive.py find`.
4. A backup copy exists and passes `verify`.

---

### WP6: Report bundles and reproducible reports (F3, F4, F10, F13, F15)

> **Status 2026-11-05: implemented, without storing PDFs** (`issuance.py`, `report.py`).
> - **Inputs.** The "bundle" is an archive state recorded just before each report (all data and code, deduplicated),
>   plus a manifest (`published/manifests/<ID>.json`, a few KB).
> - **Fingerprints.** `figures.json` is replaced by a SHA-256 of all figures, since the figures can be regenerated
>   from the archived inputs.
> - **Report ID.** `KRK-<P|C>-<date>-<time>Z-<8 hex of the manifest fingerprint>`.
> - **Commands.** `--rebuild` restores the state into a temporary folder, runs the code recorded in it and compares
>   figures and HTML. Tested: both identical. `--verify` was tested on an issued PDF (ISSUED), a PDF changed by one
>   byte (NOT ISSUED) and a superseded report (SUPERSEDED).
> - **Consistency.** Data changing while a report is made is detected: the report is retried, then marked
>   PROVISIONAL.
> - **Not done:** PDF Author/Subject metadata. Chrome sets only the title; it includes the report ID.

**Problem:** reports are built from whatever `dashboard.html` currently contains. Nothing is kept. Thumbnails are
fetched at print time.

**Do:**

1. **Freeze first, then render.** Making a report:
   1. Wait for, or refuse to run during, any running crawl or views job. The data must not change mid-report.
   2. Create `published/<report_id>/`.
   3. Copy into it (or reference by hash from the archive) every input dataset, with its `meta`.
   4. Compute all report figures into `figures.json` (see 3 below).
   5. Fetch the thumbnail images **and their attribution metadata** (WP10) into `thumbs/`, with hashes.
   6. Render the HTML **only** from the bundle. Save `report.html`.
   7. Print the PDF and save its SHA-256.
   8. Write `manifest.json`: report ID, type, subject (project, or contributor name), generation time,
      `build_info()`, every input hash, the hashes of `figures.json`, `report.html` and the PDF, validation
      results (WP7), status (FINAL or PROVISIONAL), and the issuer.
   9. Append to the register (WP3) and to `published/register.csv` (human-readable).
2. **Report ID:** `KRK-<P|C>-<YYYYMMDD>-<HHMM>-<first 8 hex of bundle hash>`, for example
   `KRK-P-20261104-1055-3fa91c2e`. P is a project report, C a contributor report. The *bundle hash* is the SHA-256
   of `manifest.json` without the PDF hash.
3. **figures.json:** every number printed in the PDF, keyed by a figure ID (e.g. `S2.T1.r3.c2`, or named keys like
   `headline.total_files`). Each entry has its value, metric ID (WP4), coverage and as-of time. The HTML takes its
   numbers from `figures.json` only, never from fresh calculations. This makes "is this number right?" a lookup.
4. **Rebuild command:** `python report.py --rebuild <report_id>` re-renders from the bundle and must give a
   **byte-identical `report.html`** and an identical `figures.json`. The PDF bytes may differ because the browser
   embeds its own timestamps; that is expected and documented. Reproducibility is tested on the HTML and the
   figures, not on the PDF bytes.
5. **Verification command:** `python report.py --verify <file.pdf>` hashes the PDF, looks it up in the register,
   checks the chain, and prints: issued / not issued, status, superseded or not.
6. **Contributor reports** use the same bundle mechanism. `contributor_details` must read from the frozen bundle,
   not from `latest_snapshot()`.
7. **No partial data mid-run:** stop `update_views` from rebuilding `dashboard.html` from partly updated data
   without marking it partial, or have report-making ignore `dashboard.html` entirely (preferred, since the bundle
   replaces it).
8. **Server endpoint** (`server.py` `/api/report.pdf`): keep the browser download, but the bundle is always written
   to `published/` first, so no report leaves the PC without a record.
9. **PDF metadata:** set Title, Author (the issuer from config), Subject, Keywords (report ID, tool version, method
   version) and Creator ("Reoa 2.0.0"). Chrome's print-to-PDF ignores most of this, so add a small
   post-processing step. A pure-Python PDF metadata writer, or `pypdf` if one dependency is acceptable, can do it.
   If staying standard-library-only, write an incremental-update Info dictionary; document the choice.

**Acceptance:** generate a report, run `--rebuild`, and get identical HTML. `--verify` on the issued PDF reports
"issued, FINAL". `--verify` on a PDF edited by one byte reports "not issued". Deleting a working data file does not
affect `--rebuild`.

---

### WP7: Validation gates (F7, F8, F10)

> **Status 2026-11-05: implemented** (`validate.py`).
> - **Error checks:** E01–E06.
> - **Freshness checks:** F01–F05, including "issuer named" and "code committed".
> - **Cross-checks:**
>   - W01 PetScan: monthly. The first run on 2026-11-05 matched exactly, 96,428 files with the same page IDs.
>   - W02 live spot check: 30 seeded random files.
>   - W03 full assessment re-read.
> - The quick-check agreement history and the per-category counter comparison are not done.

Add `validate.py` with checks that run at the end of every job and again when a report is frozen. Results go into
the run record and the report manifest. Each check is **error** (blocks a FINAL report), **warning** (shown in the
report) or **info**.

**Invariants (error):**

- The snapshot's `total_files` equals the number of unique page IDs. Every file's categories are in the tree. No
  category is orphaned outside the root.
- The headline totals in `figures.json` equal the sums of their breakdowns. Media kinds sum to total files.
  Contributors' files sum to the files with a known uploader. Usage wikis sum to usages.
- No negative counts. `human ≤ all` for every views figure. Every percentage is between 0% and 100%.
- Every dataset's `based_on_snapshot` equals the report's snapshot. Otherwise the coverage is recomputed against
  the report's snapshot and the mismatch is listed.
- Every input has `complete: true`, or it is a warning plus PROVISIONAL (see below).

**Cross-checks (warning if they disagree beyond a tolerance):**

- **Quick check vs full crawl:** at each full crawl, compare its file set with what the preceding quick checks
  produced. Record the differences (missed renames, missed removals). Show the running "quick-check agreement"
  in the report, for example "the last 4 full crawls found 0 files the quick checks had missed".
- **Category counters:** Commons' own `categoryinfo` counts against the crawl's direct member counts, per
  category. Differences are expected (de-duplication, timing) but are recorded.
- **Independent tool:** monthly, or before a major public report, compare the total file count with
  [PetScan](https://petscan.wmcloud.org/) (depth unlimited, file namespace) for the same root. Record the PetScan
  query ID, time and result in `validation/petscan/<date>.json`. Explain any difference.
- **Spot check:** a reproducible random sample of 30 files (seed = report ID). For each, re-query its upload date,
  uploader and usage count live, and compare with the bundle. The disagreement rate is printed in the report.
- **Assessments:** a full re-read of the assessment categories at least monthly, compared with the incremental
  state. Any drift is a warning.

**Freshness (makes the report PROVISIONAL if failed):**

- The latest full crawl is no older than 8 days.
- The usage check, assessment check and EXIF/geo data are no older than 2 days before the snapshot used, and
  cover at least 99% of its files.
- View-request history covers 100% of files through the stated cutoff month (WP8).
- No error-level check failed.

**PROVISIONAL** reports carry a diagonal "PROVISIONAL – not for citation" watermark and a box on the cover
listing which conditions failed. Only FINAL reports should go to donors and evaluators.

**Acceptance:** each check has a unit test with a passing and a failing fixture. A report built while the views
history is at 38% coverage comes out PROVISIONAL with the reason printed.

---

### WP8: A single reference date and consistent time handling (F2, F10)

> **Status 2026-11-05:**
> - **Point 5 (UTC everywhere): implemented** (method M1). This covers the code, dashboard, CSV files, PDF and file
>   names; old usage-log rows were converted. A test checks that no local time zone remains in the code.
> - **Point 2: implemented** (tool 2.1.0). Crawls record `crawl_started_at` and `crawl_finished_at`, and the
>   "Data used" table shows them.
> - **Point 3: implemented** (method M2). See `tracker.views_cutoff`. Totals run January 2015 to the common cutoff,
>   which is stated with every total. Requests after the cutoff are shown apart. A test recomputes the totals
>   independently.
> - **Point 1** (one reference time per report) is part of the report bundle (WP6, Phase 3). The "Data used" table
>   already shows every dataset's time next to the crawl's.
> - **Point 4 (renamed files):** a renamed file already keeps the months counted under its old name. Requests made
>   under the old name after the rename are still missed, which stays limitation L07.

1. **Reference time.** Each report has one *reference time*: the end time of the crawl it is built on. All other
   datasets are shown relative to it in a "data currency" table on the cover page (WP-PDF §7.2).
2. **Crawl times.** Record both `started_at` and `finished_at` for every crawl, and print "crawl of
   2026-11-04, 04:55–05:01 UTC".
3. **File requests ("views") to a common cutoff.** Today each file's total runs to a different day: the rolling
   1/30 daily update means files are counted up to different dates. For the report:
   - The headline and all totals use **complete calendar months, up to the last month that every file has been
     counted through** (the minimum per-file end date, rounded down to a month end).
   - Daily figures beyond that cutoff are shown separately as "partial, recent days" and never added to the
     headline.
   - Print the cutoff, for example "File requests, January 2015 – September 2026".
4. **Renamed files.** `mediarequests` is keyed by file name. When a file is renamed, Reoa refetches
   history under the new name, so requests made under the old name are lost. Do one of:
   - (a) also fetch history under previous names, from the move log, and add it up
   - (b) count the affected files and print them as a limitation (Appendix C, L7)

   Option (a) is preferred.
5. **UTC everywhere (decided 2026-11-04).** Store and show every time in UTC.
   - **Code.** Remove the `BST` time zone constant and `bst()` formatting from `tracker.py`, `report.py`,
     `server.py` and `dashboard_template.html`. Use one `utc()` formatter, e.g. "4 Nov 2026, 04:55 UTC" for
     people and `2026-11-04T04:55:00Z` for machines.
   - **Groupings.** Daily, monthly and yearly groupings use UTC dates: uploads per month, file requests per
     month, the take-to-upload gap, "new in the last 30 days", and the views job's day boundaries (it uses
     `datetime.now(BST)` today). Record the change as a method version (WP4).
   - **File names.** Use UTC, e.g. `Korikath_statistics_report_2026-11-04_0455Z.pdf`, and in the usage and
     removed-files download names.
   - **CSV files.** `usage_log.csv` and `removed_files.csv` already hold rows with "BST (UTC+6)" text. Convert
     them once to UTC ISO columns. Keep the old text column for one version if anyone relies on it. The archive
     keeps the originals.
   - **Scheduled times.** The scheduled tasks stay at 09:00 and 11:00 local PC time (Windows schedules in local
     time). The dashboard should show their next run in UTC, e.g. "next run 03:00 UTC".
   - **README.** Replace the "All times are Bangladesh Standard Time" paragraph.
   - **PDF.** State once on the cover: "All times in this report are UTC (Coordinated Universal Time)."

**Acceptance:**

- The views headline in a report equals the sum of complete months to the cutoff for every file. The test
  computes it independently.
- No "BST", "UTC+6" or local time appears in any output. A test greps the dashboard, CSVs and a generated report.
- A file uploaded at 2026-01-31 20:00 UTC (02:00 on 1 Feb in Bangladesh) is counted in January.

---

### WP9: Coverage on every metric (F7)

> **Status 2026-11-05: implemented as a "Data used" table** (`provenance.data_status`), in the PDF before the first
> figure and on the dashboard's Crawls page. For every dataset it shows its "as of" time and how many of the crawl's
> files it covers, in red when below 100%. Figures that already showed their own base keep doing so: take-to-upload
> gap, devices, views. Still open for Phase 3: a per-figure "based on k of N" line in the PDF's source lines.
> Today every dataset covers 100% of the 96,428 files, so no figure is partial.

For every metric in `figures.json`, store `coverage = {"of": N, "covered": k}`, and show it in the report
wherever k < N. Today these are missing:

| Metric | Coverage to report |
|---|---|
| Upload date / uploader | Files with a known earliest revision; uploader hidden or revision-deleted shown as "unknown". |
| Media kind | Files with a known kind; unknown files are counted as "other" today, which hides them. List them separately. |
| EXIF device, date taken, GPS | Files with readable metadata; "no device info" is shown today, which is good, but it is also needed for date taken. |
| Take-to-upload gap | Files with a usable camera date, after the wrong-clock exclusion; both numbers printed. |
| Geocoded | Already a share of all files; keep it. |
| File size | Files with a size. |
| On-wiki usage | Files checked / files in the snapshot. |
| Views | Files with full history to the cutoff. |
| Assessments | Files checked, and whether the last check was full or incremental. |

**Acceptance:** every table and KPI in the PDF either covers 100% of files or shows "based on k of N files".

---

### WP10: Legal and ethical compliance (F12)

> **Status 2026-11-05: implemented, except the user-agent contact (8).**
> - Credits come from extmetadata. In "safe" mode (the default), images marked with personality rights are not
>   reproduced. Identifiable people in images *without* that mark can still appear; review the thumbnails of FINAL
>   reports for donor use.
> - The report licence (default CC BY 4.0), the data-source statement, the not-affiliated statement, the privacy note
>   and the opt-out (`contributor_opt_out`) are in place.
> - No locations are shown in uploader reports.
> - The data-source statement deliberately doesn't name a licence for Wikimedia's analytics data; confirm it before
>   naming one.

> Not legal advice. Before the first public distribution, have someone familiar with Creative Commons licensing
> and with the donor's data-protection requirements review the final PDF.

1. **Image attribution (required for CC BY / CC BY-SA images).** For every thumbnail in a report, fetch
   `prop=imageinfo&iiprop=extmetadata` (`Artist`, `LicenseShortName`, `LicenseUrl`, `AttributionRequired`,
   `Credit`, and `Attribution` where present). Print under each thumbnail: *Title, by Author, licence
   (with link), via Wikimedia Commons*. Store the metadata in the bundle. If the metadata can't be fetched, leave
   the thumbnail out; never print an image without credit. Note that the thumbnails are **unmodified, reduced
   copies**.
2. **Identifiable people.** Some top-viewed files show identifiable people, for example protest photographs.
   Donor-facing material can imply endorsement or create risk for the people shown. Rules:
   - Skip thumbnails for files with `{{Personality rights}}`, or in categories marking identifiable people,
     protests or minors, and show the title and link instead.
   - Add a config switch `thumbnails: "all" | "safe" | "none"`, with **"safe" as the default**.
3. **Licence of the report itself.** State it on the cover or in the colophon. Suggested: the report text, tables
   and charts are under **CC BY 4.0**, and images remain under their own licences.
4. **Licence and source of the data.** State that file metadata comes from Wikimedia Commons, and that file-request
   statistics come from the Wikimedia Analytics API. Name the licences under which Wikimedia publishes each, after
   checking them at implementation time (the Analytics data is published as public domain / CC0 at the time of
   writing; confirm and cite the page).
5. **Trademarks and affiliation.** Use the names "Wikimedia Commons" and "Wikipedia" only to refer to them. Don't
   use Wikimedia logos. Add: *"This report is produced independently by Project Korikath. It is not produced or
   endorsed by the Wikimedia Foundation."*
6. **Contributor privacy (contributor reports and named lists).**
   - Use only public data, and say so: Commons usernames and public file metadata.
   - **Never** print GPS coordinates or maps of individual contributors' photos in a contributor report. Today none
     are printed; keep it that way and add a test.
   - Add `config.json` `contributor_opt_out: []`. Opted-out users are counted in totals but not named in lists,
     and they get no contributor report.
   - Print a short privacy note: what data is used, that it is public, how to opt out, and a contact.
   - Usernames can be personal data under GDPR and similar laws when donors or evaluators are in those
     jurisdictions. Keep named lists to what serves the report's purpose (top contributors). Avoid per-person
     behavioural profiling beyond upload counts, usage and assessments.
7. **Deleted files.** Deletion reasons and administrators' names come from the public deletion log, and that is
   fine to cite with a link. Don't print thumbnails of deleted files. Keep titles only.
8. **Wikimedia API etiquette.**
   - Set the `config.json` `user_agent` to include a real contact (an on-wiki user page or an email):
     `Reoa/2.0 (https://commons.wikimedia.org/wiki/User:<operator>; <email>)`.
   - Keep the current limits: maxlag, one request at a time, and under the AQS rate limit.
   - Record the User-Agent in run records.

**Acceptance:** no image in any PDF lacks an author and licence line. The report has a licence, sources,
affiliation and privacy colophon. An opted-out test user never appears by name.

---

### WP11: Neutral language and terminology (F11)

> **Status 2026-11-05: implemented** for the PDF (tool 2.2.0) and the dashboard (tool 2.2.1), with tests for both.
> The page addresses were renamed to match in tool 2.2.2 (`#/file-requests`, `#/uploaders`, `#/uploader-files`).

1. Write `docs/STYLE.md` with the rules below. Add a test that searches `report.py` and the template for the
   banned words.
2. **Renames:**

   | Today / risky | Use |
   |---|---|
   | Views, readers, readership, human reads | **File requests** (thumbnails and full files included) |
   | By people, human, verified human | **Not flagged as automated** (Wikimedia "user" agent) |
   | Contributors, photographers | **Uploaders** (accounts that first uploaded a file now in the project) |
   | Accreditation, peer-reviewed | **Commons assessments** (Quality / Featured / Valued) |
   | Audit, audited | Don't use. The report is not an audit. |
   | "0 files removed: exceptional permanence" | State the count and the period: "0 files left the project between 23 Oct and 28 Oct 2026" |
   | Executive summary prose | Generated sentences only, from templates filled with figures |

3. **Banned words** in generated text: *exceptional, remarkable, outstanding, massive, flawless, rigorous,
   verified, audited, accredited, guaranteed, proves, impact* (unless a defined metric is named "impact").
4. **Optional generated summary:** a short paragraph built from templates such as "On {as_of}, the project held
   {total_files} files in {categories} categories, uploaded by {uploaders} accounts." No adjectives.

**Acceptance:** the language test passes, and every sentence in the PDF comes from a template in code.

---

### WP12: Tests (F14)

Add `tests/`, using `unittest` from the standard library so the "no dependencies" rule is kept:

1. **Unit tests** for each rule in the methodology register: `appearances`, `page_date`, the main-page regular
   expressions (with real titles in several scripts), `device_of`, the wrong-clock rule, `median`, the catch-all
   threshold, `diff_snapshots` (renames, moves, removals), and quick-check inference (simulated counter
   scenarios).
2. **Fixture dataset:** a small synthetic project (around 50 files, 8 categories) with hand-computed expected
   figures. A **golden test** builds a report bundle from it and compares `figures.json` with the expected file.
3. **Reproducibility test:** freeze, rebuild, compare HTML and figures.
4. **Integrity tests:** the register chain, `--verify` on issued and tampered PDFs, and a schema check of every
   dataset `meta`.
5. **Consistency tests:** the WP7 invariants run against the fixture and against the live data (read-only).
6. **Language and legal tests:** WP10 and WP11.
7. Add `run_tests.bat`. Document that tests must pass before tagging a version.

**Acceptance:** `python -m unittest` passes. Coverage of `methodology`, `diff_snapshots`, `crawl` inference and
`report` figure code is at least 80% (measured once with `coverage.py` during development; not a runtime
dependency).

---

### WP13: Issuance, corrections and governance (F13, F15)

> **Status 2026-11-05: points 1–3 implemented** (issuer in config, FINAL / PROVISIONAL / SUPERSEDED, `ERRATA.md`
> generated from the register). Points 4–6 (public register, signing, method-change box) are Phase 4.

1. **Issuer.** `config.json` gets `issuer: {"name": "...", "role": "...", "contact": "..."}`, printed on the cover.
2. **Status:** FINAL, PROVISIONAL or SUPERSEDED. Superseding is recorded in the register with the new report ID
   and a reason. `--verify` on a superseded PDF says so and names the replacement.
3. **Errata:** `published/ERRATA.md` lists each error found in an issued report, its effect, and the correcting
   report ID.
4. **Public register (recommended):** publish `published/register.csv` (report ID, date, type, status, PDF
   SHA-256) on a page that readers can reach, for example an on-wiki project page or the project website. The PDF
   prints that URL. This lets outsiders check a PDF without access to this PC.
5. **Signing (optional, strongest):** sign each FINAL PDF's hash with a project key (minisign, or GPG) and publish
   the public key on the same page. A PAdES signature inside the PDF is also possible but needs external tools.
   Document whichever is chosen.
6. **Method changes:** when `METHOD_VERSION` changes, the next report includes a "What changed since method Mx"
   box and, where possible, the old and new values of the affected headline figures.

**Acceptance:** superseding a test report and verifying the old PDF names the new one. The cover shows the issuer.

---

### WP14: Logging and error reporting (F1, F7)

> **Status 2026-11-05: point 2 implemented** (tool 2.1.0). Upload-date lookups that found nothing are retried at
> every run, as were camera-data lookups already. Errors and incomplete jobs are recorded in the run records (WP3).
> Structured JSON logs (point 1) and log rotation (point 3) are not done; the run records cover what they were for.

1. Write structured logs (one JSON object per line) alongside the human logs: `logs/<job>.jsonl`, with UTC time,
   run ID, level and event.
2. Every caught error that is skipped over is counted in the run record and leads to `complete: false` or a
   coverage gap. Reoa already marks files that fail lookup as `None` ("don't ask again"). Mark them
   instead with a status and a retry count, and retry them in later runs, so a temporary failure doesn't
   permanently remove a file from coverage.
3. Log rotation: keep 12 months.

---

### WP15: Fix data-correctness gaps found in review

> **Status 2026-11-05:**
> - **Points 1–2: implemented** (method M2). A thirtieth of the files is re-checked daily for uploader and upload
>   time, and for current size; a new version gets its camera data looked up again. This costs about 130 requests
>   and 1.5 minutes per crawl.
> - **Point 3:** media types are retried when missing (already so).
> - **Point 4:** language names are archived with every state.
> - **Point 5: the label is fixed** ("in the category since"). Reading the real promotion date is not done.
> - **Point 6:** covered by the "Data used" table.
> - **Point 7: implemented** (`tracker.build_payload`).

These affect accuracy directly:

1. **Uploads cache never refreshes.** The cache holds the upload time and uploader per page ID. Usernames can be
   renamed, and revisions can be deleted or restored. Re-check a rolling 1/30 of files each day, like geo, and
   record changes.
2. **EXIF and size fetched once.** If a file is overwritten with a new version, size and metadata change. Re-check
   files whose latest revision timestamp is newer than the cached record (one cheap `imageinfo` query per
   50 files).
3. **Media-type cache.** Same treatment as EXIF.
4. **Language names** (`languages.json`): include them in the bundle. Today it's only a display aid, which is
   fine.
5. **Assessment dates.** Label them "in category since", not "promotion date". Or, for FP, VI and QI, read the
   actual promotion date from the assessment template on the file page, as a method change.
6. **Quick checks** cannot see renames (README). The report should state when the last full crawl was. WP7's
   freshness rule ensures it's recent.
7. **Report data source.** `report.py` reads `const DATA` out of `dashboard.html` by string search. Replace it
   with a direct call that builds the same payload (`tracker.build_payload()`), so the report never depends on
   the HTML file. WP6 supersedes this anyway.

---

## 5. Data file requirements, file by file

| File | Today | Required |
|---|---|---|
| `config.json` | Settings | Add `issuer`, `contributor_opt_out`, `thumbnails`, `public_register_url`, `backup_dir`, `report_licence`. Real contact in `user_agent`. Its hash is recorded in every run. |
| `snapshots/*.json.gz` | Kept, not overwritten | Envelope `meta` (WP2) with `started_at` and `finished_at`, mode, completeness and the categories re-read in full. Archived by hash (WP5). |
| `reports/*.json` (crawl diffs) | Kept | `meta`, plus hashes of both snapshots compared. |
| `history.json` | Overwritten (appended, then rewritten) | Add `snapshot_sha256`, `run_id`, `finished_at`. Becomes reproducible through archive states. |
| `uploads.json.gz` | Overwritten | `meta` with coverage. Records keep `fetched_at`, so rolling refresh can work (WP15). Archived per run. |
| `usage.json.gz` | **Overwritten each check** | `meta`, coverage, `complete`. Archived per run. |
| `usage_history.json` | Appended totals | Add `run_id`, `snapshot_sha256`, `complete`. |
| `usage_log.csv` | Appended | Add `checked_at_utc`, `page_id`, `run_id` columns, plus a sidecar `.meta.json`. Never rewritten. |
| `usage_current.csv` | Rewritten | Generated output. Archive per run, or regenerate from the archived `usage.json.gz`. |
| `assessments.json.gz` | **Overwritten** | `meta` (full or incremental, coverage). Archived. |
| `assessment_history.json` | Appended | Add `run_id`, full or incremental, and drift found at monthly full re-checks. |
| `geo.json.gz`, `exif.json.gz` | **Overwritten / merged** | `meta`. Per-record `fetched_at` and source revision timestamp. Archived. Fix the stale comment. |
| `mediatypes.json` | Merged | `meta`. Archived. Unknown files recorded explicitly. |
| `languages.json` | Merged | `meta`. Archived. |
| `views.json.gz` | **Updated in place daily** | `meta` with the common cutoff (WP8), coverage and `complete`. Per file: `counted_through` and previous titles. Archived. |
| `removed_files.csv` | Rewritten from all snapshots | Fine (derived). Add `page_id`, ISO UTC columns and a sidecar `.meta.json`. |
| `dashboard.html` | Rewritten | Generated output. Show a provenance footer (tool and method version, snapshot hash, as-of times) the same way as the PDF. |
| `map_points.js`, `status.js`, `views_status.js`, `*.lock` | Generated / transient | No change. Excluded from git. |
| **New** `runs/<run_id>.json`, `runs/register.jsonl` | — | WP3 |
| `archive/objects/`, `archive/states/`, `archive/register.jsonl` | **Implemented 2026-11-04** | WP5. Never edit or delete by hand; use `archive.py`. |
| **New** `published/<report_id>/…`, `published/register.csv`, `published/ERRATA.md` | — | WP6, WP13 |
| **New** `validation/…` | — | WP7 (PetScan cross-checks, spot-check results) |

---

## 6. Code file requirements, file by file

| File | Changes |
|---|---|
| `version.py` (new) | WP1 |
| `methodology.py` (new) | WP4. All constants and definitions; imported by `tracker.py` and `report.py`. |
| `archive.py` | **Exists (2026-11-04):** WP5 object store, states, hash-chained register, `list` / `snapshot` / `restore` / `verify` / `find`. To add: backup, prune, and WP3 run records (or keep run records in `runs/` and link them to states). |
| `validate.py` (new) | WP7 checks, returning structured results. |
| `tracker.py` | Use `save_dataset` / `load_dataset` everywhere (WP2). Every job writes a run record (WP3). Move constants to `methodology` (WP4). Crawl `finished_at`. Views cutoff and old-title history (WP8). Retry state instead of permanent `None` (WP14). Rolling refresh of uploads, EXIF and media types (WP15). `--version`, `--verify-register`, `--restore`, `--backup-check`, `--prune`. Replace "BST" display labels (WP8). `build_payload()` separate from `build_dashboard()`. |
| `report.py` | Freeze bundle, then render from the bundle only (WP6). `figures.json`. Thumbnails with attribution and the safe filter (WP10). New sections (Part 7). PROVISIONAL watermark (WP7). PDF metadata (WP6.9). `--rebuild`, `--verify`. Contributor report from the bundle. Neutral terminology (WP11). |
| `server.py` | The report endpoint goes through the bundle and register. Refuse with a clear message while a crawl or views job is running, or wait for it. An endpoint to verify an uploaded PDF (optional). |
| `dashboard_template.html` | Same terminology as the PDF. Provenance footer. Coverage notes. UTC times (WP8.5). Link to `published/register.csv`. |
| `install.py` | A scheduled monthly validation job (PetScan cross-check reminder, full assessment re-read, spot check). Backup job if configured. |
| `README.md` | Document all of the above. Link to this plan, `CHANGELOG.md` and `docs/STYLE.md`. |
| `tests/` (new) | WP12 |

The "standard library only" rule should stay. The single exception to consider is PDF metadata and signing
(WP6.9, WP13.5). Decide and record the decision in `CHANGELOG.md`.

---

## 7. What the PDF report must contain

The PDF must **show** each reliability feature and **explain it in plain language**, so that a donor or evaluator
with no technical background understands why they can rely on it and how to check it.

### 7.1 Cover page

- Title, subject (whole project, or a named uploader), and status badge: **FINAL** or **PROVISIONAL** (with the
  reasons).
- **Report ID** and **SHA-256 fingerprint** of the bundle. The PDF's own hash can't be printed inside itself; it
  goes in the register.
- Issuer (name, role, contact) and generation time.
- **Data currency table**, one row per dataset:

  | Dataset | As of (UTC) | Covers | Complete |
  |---|---|---|---|
  | Project files (crawl, full/quick) | 4 Nov 2026, 04:55–05:01 | 96,428 files, 316 categories | Yes |
  | On-wiki usage | 4 Nov 2026, 05:10–05:30 | 96,428 / 96,428 files | Yes |
  | Commons assessments | … | … | … |
  | File requests | Jan 2015 – Sep 2026 (complete months) | 36,500 / 96,428 files | **No** |
  | Upload dates, uploaders | … | … | … |
  | Camera metadata and locations | … | … | … |

- Tool version, method version, git commit.
- A "How to check this report" line: the register URL, plus "Report ID, fingerprint and status can be verified
  there."

### 7.2 Section "How to rely on this report" (1–2 pages, right after the cover)

A plain-language explanation of each feature: what it is, what this tool does, and what the reader can do with
it. See the draft in **Appendix B**. It covers: where the numbers come from, the fixed point in time,
reproducibility, integrity and the fingerprint, versioning, definitions, coverage, validation results, limits,
licences and privacy, and how to report an error.

### 7.3 Validation summary (box, half a page)

- Results of every WP7 check: passed, warnings, errors.
- Spot-check result, e.g. "30 randomly chosen files re-checked live on Commons at 11:42: 30 of 30 matched."
- Independent cross-check, e.g. "PetScan query #1234567 on 1 Nov 2026 counted 96,428 files: matches."
- Quick-check agreement history.

### 7.4 Body sections (as today, with these additions)

For **every** section, a small grey "source line" under its heading:
*Source: … · As of: … · Covers: k of N files · Definitions: D-xx, D-yy · Figure IDs: S3.*

- Every table and chart has a figure ID matching `figures.json`.
- Partial figures are labelled in the headline itself ("File requests, 36,500 of 96,428 files so far"), not only
  in a footnote.
- Thumbnails come with author and licence, following the safe filter.
- Terminology from WP11.

### 7.5 Appendices (generated, never hand-written)

1. **Methods:** how membership is determined (tree crawl, de-duplication, quick vs full), how each dataset is
   collected (API module, parameters, frequency), and how figures are computed.
2. **Definitions:** generated from the methodology register. Each metric's definition, unit, inclusions,
   exclusions and rounding.
3. **Limitations:** generated from Appendix C. Each with its effect and, where measured, its size in this report
   (e.g. "L7: 12 files were renamed; requests made under their previous names are not counted").
4. **Provenance:** a list of every input dataset with its file name, as-of time, run ID and SHA-256. The tool and
   method versions with commit. The API endpoints used. The User-Agent.
5. **Change history:** method changes since the previous report, and errata affecting earlier reports.
6. **Licences, credits and privacy:** the report licence, data sources and their licences, image credits (if not
   under each image), the affiliation disclaimer, and the privacy and opt-out note.
7. **Glossary:** Commons, category, page ID, crawl, snapshot, quick check, global usage, AQS, user agent, Quality
   image, Featured picture, Valued image, POTD/MOTD, EXIF, SHA-256, and so on.
8. **How to verify:** step-by-step instructions:
   1. Get the PDF's SHA-256 (on Windows: `certutil -hashfile file.pdf SHA256`).
   2. Find it in the public register.
   3. Check its status.
   4. Optionally spot-check any linked file on Commons.
   5. Request the bundle from the issuer to recompute the figures.

### 7.6 Contributor reports

Same structure: cover, trust section (shorter), validation, body, appendices. Plus the privacy note, and no
locations.

### 7.7 Page furniture

- Footer on every page: report ID · status · page X of Y.
- PROVISIONAL watermark when applicable.
- Fonts that render Bengali and Arabic correctly. The current Chrome pipeline already does; keep it, and add a
  test that renders a Bengali title and an Arabic page name.

---

## 8. Order of work

| Phase | Work | Why first |
|---|---|---|
| **0 – Now (no code)** | Withdraw `Project_Korikath_Comprehensive_Statistics_Report.pdf`. If it was shared, send a short correction. Until Phase 3 is done, share only PDFs from the dashboard button, marked as provisional in the covering message. | It contains errors (Appendix A). |
| **1 – Foundations** | ~~WP5 archive~~ (done 2026-11-04; backup still open). WP8.5 UTC everywhere (do it early, before more local-time data accumulates). WP1 versioning, WP4 methodology register, WP2 data envelope (plus migration of existing files), WP3 run records. | Everything else records into these. Start archiving as early as possible: data that isn't archived now can never be recomputed later. |
| **2 – Correctness** | WP8 reference time and views cutoff, WP9 coverage, WP14 error and retry state, WP15 data gaps, WP12 unit tests for the rules. | Fixes what the numbers mean before they are presented. |
| **3 – Reports** | WP6 bundles and verification, WP7 validation gates, WP10 legal, WP11 language, Part 7 PDF content. | The public-facing guarantees. |
| **4 – Governance** | WP13 issuance, public register, optional signing. Monthly validation schedule. Backup. | Makes it verifiable by outsiders over time. |

Rough size: Phase 1 is the largest code change (touches every load and save). Phases 2–3 are mostly additive.
Phase 4 is small.

---

## 9. Final acceptance checklist

Before the first PDF goes to donors or evaluators, all of these must be true:

- [ ] Code is in git. The report was built from a clean, tagged commit. Tool and method versions are printed.
- [ ] Every data file has a valid `meta` header. Nothing used by the report is ever overwritten.
- [ ] The report bundle exists in `published/<report_id>/`. `report.py --rebuild` reproduces the HTML and figures
      exactly.
- [ ] `report.py --verify` confirms the PDF. The register chain is intact. The PDF hash is in the public register.
- [ ] All error-level validation checks pass. Warnings are printed. The status is FINAL.
- [ ] A full crawl no more than 8 days old. Usage, assessments and file details fresh and at least 99% coverage.
- [ ] File-request figures cut off at a common month for all files, with coverage shown. If coverage is below
      100%, the report is PROVISIONAL.
- [ ] An independent cross-check (PetScan) and a live spot check were done and printed.
- [ ] Every figure has a figure ID, source line, as-of time and coverage. Totals add up.
- [ ] The definitions, methods, limitations, provenance, glossary and verification appendices are present and
      generated from code.
- [ ] Every image has author and licence. The safe-thumbnail rule is applied. The report licence, data sources,
      affiliation disclaimer and privacy note are present.
- [ ] No banned words. Terminology follows WP11. All prose is template-generated.
- [ ] Every time is shown in UTC, and the cover says so.
- [ ] The report's PDF and its data are in the archive (`archive.py find` locates it), and `archive.py verify`
      passes, also on the backup copy.
- [ ] Tests pass.
- [ ] Someone other than the tool's operator has read the PDF against this checklist.

---

## Appendix A: problems found in the existing PDF

File: `E:\korikath_stats_tool\Project_Korikath_Comprehensive_Statistics_Report.pdf` (11 pages, produced by
"ReportLab PDF Library", created 28 Oct 2026 15:31 local time). **This tool did not create it**, so its numbers
have no provenance through this tool.

| # | Problem | Where |
|---|---|---|
| A1 | Contradictory totals: the cover says 69.54 M views / 67.18 M "human"; section 5 says 70,429,457 / 68,031,565. | p. 1 vs p. 5 |
| A2 | The headline "All media views" covers only 36,500 of ~96,429 files (38%). The headline has no caveat, and the text calls it an "Audited Traffic Footprint". | p. 1, p. 5 |
| A3 | "0 files have ever been removed … confirming an exceptional record of archival permanence": based on 5 days of tracking. A deletion was recorded on 1 Nov 2026 (`removed_files.csv`). | p. 1 |
| A4 | "640 unique photographers / contributing photographers and editors": the data counts **uploaders**. | p. 1, contributor section |
| A5 | "verified human views", "Human Reads": the data counts requests not flagged as automated. Nothing is verified, and a request is not a read. | p. 1, p. 5 |
| A6 | Invented content descriptions: "high-resolution field audio recordings of folk music and spoken word", "documentary video clips of cultural rituals", the "Significance / Subject" column, "Wikimedia MIME analyzer". None of these are in the data. | p. 1–2 |
| A7 | Wrong figure: POTD share 0.0010%. 5 / 96,429 = 0.0052%. | p. 3 |
| A8 | Misaligned tables: average file sizes shifted a row (audio "455.90 MB" is the video average); snapshot names offset from crawl rows; the second crawl labelled BASELINE although it was compared with the first; the top-15 views numbers don't line up with the file names; the assessment descriptions misaligned with their rows. | p. 1–3, p. 5 |
| A9 | Garbled Arabic and Bengali page names (the font has no shaping). | p. 5 |
| A10 | Promotional language: "rigorous peer-review", "accreditation", "technically flawless", "massive international utility", "outstanding". | p. 3–4 |
| A11 | No author, title or metadata. No data fingerprint, method, limitations, licences or image credits. | whole document |

**Recommendation:** withdraw it. If it reached anyone, send a one-paragraph correction: the figures were produced
outside Reoa, contain errors, and will be replaced by a verified report.

---

## Appendix B: draft text for the PDF's "How to rely on this report" section

*Generated by `report.py`. Values in {braces} are filled from the bundle.*

> **How to rely on this report**
>
> **Where the numbers come from.** Every figure in this report was computed by Reoa
> (version {tool_version}) from public data published by Wikimedia: the Wikimedia Commons API for files,
> categories, uploaders, usage on other wikis and assessments, and the Wikimedia Analytics API for file requests.
> No figure was typed in by hand, and no text in this report was written by hand or by an AI system. All text is
> produced by fixed templates from the data.
>
> **A fixed point in time.** Commons changes every minute. This report describes the project as it was at the
> times listed in the "Data currency" table on the cover. The main reference is the crawl that finished on
> {crawl_finished}. Later changes are not included.
>
> **Can it be reproduced?** Yes. All the data this report was built on is frozen and kept under report ID
> {report_id}, together with the exact program version used. Anyone with that archive can recompute every figure
> and get the same result. Running the same queries against Commons today would give slightly different numbers,
> because Commons has changed since. That is why the data is archived.
>
> **Has it been altered?** The archive has the fingerprint {bundle_sha256} (SHA-256). Any change to the data,
> however small, would change the fingerprint. This PDF's own fingerprint is listed in the public register at
> {register_url}. If your copy's fingerprint is not there, it is not an issued version. Instructions are in
> Appendix 8.
>
> **What exactly is counted.** Every metric has a fixed written definition (Appendix 2). This report uses method
> version {method_version}. If a definition changes, the method version changes and the report says what
> changed (Appendix 5).
>
> **How complete it is.** Where a figure does not cover every file in the project, it says so ("based on k of N
> files"). This report is **{status}**. {provisional_reasons}
>
> **How it was checked.** Before this report was issued, the data passed {n_checks} automatic checks (Section 3).
> These include re-checking {spot_n} randomly chosen files live on Commons ({spot_matched} matched) and comparing
> the total file count with the independent PetScan tool ({petscan_result}).
>
> **What it cannot tell you.** Some things are inferred or not visible to the tool. For example, file-request
> counts include thumbnails and cannot fully separate people from automated traffic, and the "uploader" of a file
> is not always the person who took the photo. Appendix 3 lists every known limitation and, where possible, how
> much it affects this report.
>
> **Licences and privacy.** The report is published under {report_licence}. Each image is credited with its author
> and licence. Contributor names are public Wikimedia Commons usernames. Contributors can ask not to be named.
> This report is produced independently by Project Korikath and is not produced or endorsed by the Wikimedia
> Foundation.
>
> **Found a mistake?** Contact {issuer_contact}, quoting report ID {report_id}. Corrections are published in the
> register, and a corrected report replaces this one.

---

## Appendix C: known limitations to disclose

Each item gets an ID, is printed in the PDF's limitations appendix, and is quantified where possible.

| ID | Limitation | Quantify in report? |
|---|---|---|
| L1 | Project membership is defined by categories. Files mis-categorised outside the tree are not counted, and unrelated files placed inside it are. | Count of files added or removed by category moves in the period. |
| L2 | Quick checks infer membership from counters and cannot see renames. Full crawls are weekly. | Date of the last full crawl, plus quick-check agreement (WP7). |
| L3 | A crawl takes minutes. Changes during the crawl may or may not be included. | Crawl start and end times. |
| L4 | "File requests" count every load of the file or its thumbnails, including on pages where a reader may never look at the image. A request is not a reader. | — |
| L5 | "Not flagged as automated" (Wikimedia "user" agent) still contains some undetected automated traffic. | — |
| L6 | File-request history may be incomplete while it is still being collected. | Coverage k of N and the cutoff month. |
| L7 | File-request statistics are keyed by file name. Requests under a file's earlier names may be missing (until WP8.4a is done). | Number of renamed files affected. |
| L8 | Usage on other wikis comes from Commons' GlobalUsage, which can lag after edits and excludes Commons itself. | — |
| L9 | "Uploader" is the account that first uploaded the file. It may differ from the author or photographer. Renamed accounts may show an old name until refreshed. | Files whose author field differs from the uploader (optional, via extmetadata). |
| L10 | Camera make, model, date and GPS come from the file's own metadata (EXIF), which can be missing, edited or wrong. Camera clocks may be unset. | Files with no device info, and files with a wrong clock. |
| L11 | Main-page appearances on other wikis are detected from page names in many languages. A wiki with an unusual page name may be missed. | — |
| L12 | Assessment dates are the time the file entered the assessment category, which may differ from the promotion date (until WP15.5 is done). | — |
| L13 | File size is the current version's size, not the original upload's. | — |
| L14 | Media kind for non-standard extensions comes from Commons' media type. Unknown types are listed as unknown. | Count of unknown. |
| L15 | The catch-all category rule (more than 25% of files) is a heuristic for per-uploader category lists only. It does not affect totals. | Names of the categories excluded. |
| L16 | Counts are complete enumerations, not samples, so there is no sampling error. Uncertainty comes only from the limitations above. | — |
