# Project Korikath tracker

Counts every file under [Category:Project Korikath](https://commons.wikimedia.org/wiki/Category:Project_Korikath)
on Wikimedia Commons and reports what changed since the last crawl.

Requires Python 3.9+ and nothing else (standard library only).

## Open it

Use the **Korikath Tracker** shortcut on the Desktop or in the Start menu (to pin it to the taskbar, right-click it in
the Start menu → *Pin to taskbar*). It opens the dashboard in your browser. Opening it doesn't start a crawl.

**Issued reports: IDs, status, checks and verification.** Every PDF the tracker makes is *issued*:

- **ID and status.** It gets a report ID (e.g. `KRK-P-20261105-0815Z-352ffc6d`) and a status:
  - **FINAL** when every check passed
  - **PROVISIONAL** otherwise, with a watermark, the reasons on its cover and `_PROVISIONAL` in its file name

  Only FINAL reports are meant for donors, evaluators or the public.
- **Checks** (`python validate.py` runs them on their own):
  - totals add up, the data files match their metadata, the archive is intact
  - the code is committed
  - there was a recent full crawl and the data is complete
  - an issuer is named in `config.json`
  - cross-checks: an independent PetScan count (run `python validate.py --petscan` once a month) and a live spot
    check of 30 files
- **Register.** The report is recorded in `published/register.jsonl` (with `published/register.csv` to publish) by
  its PDF's SHA-256 fingerprint. The PDF itself is not stored; the archive keeps the data and code it was made from,
  so it can be regenerated.
- **Commands:**

  ```
  python report.py --verify FILE.pdf       was this PDF issued by the tracker? is it still current?
  python report.py --rebuild REPORT_ID     regenerate an issued report from its recorded data and code, and compare
  python report.py --register              list issued reports
  python report.py --supersede OLD_ID --by NEW_ID --reason "..."   record a correction (published/ERRATA.md)
  ```

- **What the PDF contains.** It opens with:
  - "How to rely on this report"
  - the checks and their results
  - a "Data used" table

  Every section ends with the data it rests on, and appendices give the methods, definitions, limitations,
  provenance, licences and credits, a glossary and how to verify. Thumbnails show their author and licence; images
  marked as showing identifiable people are not reproduced.

**PDF reports.** **Download PDF report** (top right of the Overview) saves a statistics report of the whole project:
headline numbers, growth, kinds of media, the category tree, changes and saved crawls, views, on-wiki usage, quality /
featured / valued files and main-page features, contributors, devices, take-to-upload gaps and collection size, with
charts. On a contributor's page, **Download this member's PDF report** saves the same for one member: files, rank,
uploads per month, categories, views and most viewed files, wikis using the files, assessed and main-page
files, devices, gaps and largest files. Each report states when it was generated and the crawl it is built on, and
its file name holds the generation time, e.g. `Korikath_statistics_report_2026-10-30_1729Z.pdf` or
`Korikath_contributor_Wasiul_Bahar_2026-10-30_1729Z.pdf` (UTC). Reports go straight to your browser's downloads,
and the archive records each PDF's fingerprint (SHA-256) with the exact data it was built from, so it can later tell
whether a PDF was issued by the tracker (see **Archive** below); the PDF itself isn't kept. Every figure is computed
afresh from the saved data, and the report opens with a **Data used** table: how current each kind of data is and
how many files it covers. Making one takes
5–15 seconds; it needs Microsoft Edge or Google Chrome (used in the background to print the PDF) and the dashboard
opened with the shortcut.

The dashboard has three pages (the browser's Back button works between them):

- **Overview** (home): a red **Removed** box listing everything taken away in the latest crawl and usage check
  (files that left the project tree, deleted or moved outside; categories removed from the tree; file uses removed
  from wikis), each with links to act on it, or a green line when nothing was removed. Then the statistics of the latest crawl (total files, categories, files removed/added/moved,
  category changes), links to the other two pages, **Korikath media over time** (a chart of how many of today's
  files had been uploaded by each date, or uploads per month), and **Explore Korikath Category Tree** with three tabs:
  **Tree map** (zoomable sunburst), **Tree diagram** (expandable tree, the style of osintframework.com) and **List**
  (searchable). A view is drawn the first time you open its tab, and the dashboard remembers the tab you used last.
  While a crawl runs, a line here shows its progress and time left.
- **Crawls**: **Start quick check** (about 3 min) and **Start full crawl** (about 13 min) buttons, live progress
  with time left and the expected finish time, the **scheduled crawls** (next run and how the last one went), and
  **Data status**: how current each kind of data is ("as of", UTC) and how many of the latest crawl's files it
  covers, with the tool version, method version and code commit that built the dashboard. Then **Saved crawls**:
  every crawl stored on this PC with its type, totals, changes, how long it took and its size.
  A crawl runs in the background with no window, so you can close the page and it keeps going. When it's done the
  page refreshes itself with the new results.
- **On-wiki usage**: where Korikath files are used on other Wikimedia wikis (Wikipedias, Wikidata, Wikivoyage and
  so on; Commons itself isn't counted). Totals (files used and their share of the project, pages, wikis), every usage
  **added or removed** since the previous check with links, and percentage breakdowns: **wikis** using the most
  files, the most used **categories**, the **uploaders** whose files are used most, and the most used **files**.
  Switch between **All pages** and **Articles only** (main-namespace pages: articles, Wikidata items). The **Download usage changes** panel downloads
  the change log (every usage added or removed, with date, file, wiki, page, uploader and links) for any date range
  (From/To in UTC, or Last 7 days / Last 30 days / All time), named with the range and the time of download, e.g.
  `korikath_usage_changes_2026-10-01_to_2026-10-26_downloaded_2026-10-26_1432.csv`; the red Removed box links to it.
  **Download current usage** gives every current use (`usage_current.csv`). Both open in Excel with Bengali text shown correctly.
- **Contributors**: the people who uploaded the files now in Korikath (each file's original uploader). Totals
  (contributors, new and active in the last 30 days, the top 10's share of files, the median per person), a chart
  of contributors over time (total, active per month, new per month), how many people uploaded 1 file, 2–10, and so
  on, and a sortable, searchable table of everyone (files, share, first and latest upload, active months, files used
  on other wikis, categories). Pick a contributor (search box, or click a name) to see the member's monthly uploads chart,
  files by category, and on-wiki usage; each contributor has a shareable link
  (`#/contributors?u=Name`). The category breakdown leaves out the catch-all categories (any category directly
  holding more than a quarter of all files: All images, Uploaded via Campaign, Unreviewed). These statistics come
  from the cached uploaders and upload dates, so they need no extra requests to Commons.
- **Files by contributor in a category** (the "Files by contributor in a category" link): choose any category of the
  tree (suggestions as you type) and type a contributor's user name to get the list of files that person uploaded
  there (each file's original uploader) and the total, with **Include subcategories** on (the default) or off (only
  files placed directly in the category). The list is sortable, has previews, and **Download list (CSV)** saves it.
  Leave the name empty to see everyone who uploaded to the category with their counts; click a name there to list
  their files. Names match without regard to case or underscores ("mrb_rafi" finds "Mrb Rafi"). Each lookup has a
  shareable link (`#/user-files?c=Category&u=Name`). It's worked out from the latest crawl and the cached uploaders,
  so it makes no requests to Commons, and it needs the dashboard opened with the shortcut.
- **Views**: how often Korikath files are loaded (in articles, on Commons, elsewhere; thumbnails included), from
  Wikimedia's media request statistics, which start in January 2015: all views and views by people (automated
  traffic such as crawlers left out), monthly and daily charts, and the most viewed files, categories and
  contributors (also a Views column on the Contributors page). A separate daily job (11:00 AM, its own lock so it
  never blocks a crawl) first collects each file's monthly history (one time, ~24 hours for all files, saved as it
  goes), then each day brings a thirtieth of the files up to date with daily figures, so every file is updated once
  a month. **All view totals run to one common cutoff**: complete months, from January 2015 to the last month every
  file has been counted through (shown with every total, e.g. "Jan 2015 – Sep 2026"); what has been counted after it
  is partial and left out until every file has caught up. It makes one request at a time and never more than
  180 a minute (Wikimedia allows identified tools 200). Data in `views.json.gz`. While it runs, the Views page shows
  live progress and time left (from `views_status.js`); the figures are rebuilt every 500 files and a "Refresh"
  prompt appears when newer ones are ready.
- **Map, devices & metadata**: how many files are **geocoded** (have a camera location on their Commons page), how many have
  a GPS position in their own metadata but no location on Commons yet, and an interactive **map** of the geocoded
  files (pins cluster when zoomed out; click one for the photo; map © OpenStreetMap contributors). Below it the
  **devices** the photos were taken with, from each file's metadata: by type (camera, phone, drone, ...), by brand
  and by model, with how many files and contributors each. A file's camera metadata is fetched once; its location is
  re-checked over a 30-day cycle (a thirtieth of the files with each daily crawl), since one can be added any time.
  **From shot to upload**: how long after a photo was taken (the date its camera recorded) it was uploaded: the
  typical wait, how many waited a day, a week, a month, a year or longer, the typical wait by upload year, the
  longest waits and the oldest photo; photos whose camera clock was clearly wrong are counted apart. **Collection
  size**: the total size of the collection (current version of each file), by kind of file, the largest files, and
  its growth over time (also a view of the home page's chart). Contributors get a "Median days to upload" column.
- **Changes** (the "Explore changes" link): everything that changed with a crawl, removals first: files removed,
  category changes, file uses removed from wikis, files moved, renamed and added, and file uses added. The file-usage
  changes are those found by the usage check that ran after the crawl. The other changes in detail: files removed (with thumbnails, marked Deleted or Moved
  outside), category changes, files moved inside the tree, renamed files and files added. Pick an earlier crawl
  from the list to see its changes.

How the buttons work: a page opened straight from disk can't start programs, so the shortcut starts a small local
server (`server.py`, no window) and opens the dashboard through it at <http://127.0.0.1:8777/>. Only this PC can
connect to it, and it closes itself after 30 minutes with no dashboard open. If you open `dashboard.html` directly
from the folder, you can still read everything but the buttons and schedule are unavailable.

While any tracker job runs (crawl, usage check, lookups, view counts), it asks Windows not to go to sleep; the
screen can still turn off, closing the lid or the power button still sleep the PC, and normal sleep returns when the
job ends. A sleeping PC would otherwise stall the job until it wakes.

**All times are UTC** (Coordinated Universal Time), everywhere: the dashboard ("23 Oct 2026, 16:28 UTC"), the CSV
files ("2026-10-23 16:28 UTC"), the PDF reports and their file names, and the saved data. Days and months (uploads per
month, "new in the last 30 days", ...) are UTC days and months. Until 5 Nov 2026 the tracker showed Bangladesh time
(UTC+6); the change is described in `CHANGELOG.md`. The scheduled tasks still run at 9:00 and 11:00 by the PC's own
clock, since that is how Windows schedules them; the dashboard shows their times in UTC.

## Versions, methods and where every number comes from

These make every figure traceable and checkable (see `RELIABILITY_PLAN.md` for the full plan and its status):

- **Versions.** `version.py` holds the tool version (now 2.0.0). `methodology.py` holds the method version (now M1):
  what is counted and how. Every rule and threshold the tracker counts by is in `methodology.py`. That covers what
  counts as a project file, a use, a view or a wrong camera clock, the brand list, the assessment categories, and
  the definitions (D01–D18) and known limitations (L01–L17) that the reports print. Changing any of them is a new
  method version; `python methodology.py --check` fails until the version is bumped and recorded. Every change is
  listed in `CHANGELOG.md`.
- **Metadata of every data file.** Next to every data file is a `.meta` file (JSON, e.g. `usage.json.gz.meta`). It
  says:
  - what the file holds and which Wikimedia API its data comes from
  - its format version, and the tool and method versions that wrote it
  - the run that wrote it, and when its data is "as of"
  - whether that job finished, and how many of the project's files it covers
  - which crawl it is based on (with that crawl's SHA-256)
  - the SHA-256 of the data file itself, so a `.meta` that no longer matches its file is detectable

  `python provenance.py` checks every data file against its `.meta`.
- **Run records.** Every job (scheduled, button or command line) writes `runs/<time>-<job>.json`. It records:
  - the job, how it was started, its start and end (UTC), and whether it finished (with the error if not)
  - the versions and code commit
  - the API requests it made, per wiki
  - the fingerprint (SHA-256) of every data file it read and of every one it wrote

  The archive state recorded right after it names the run.
- **Tests.** `python -m unittest discover -s tests` checks these guarantees (UTC everywhere, the register and its
  version, the metadata of every data file).

## Archive

Most data files (usage, views, assessments, locations, camera data, upload dates, ...) are overwritten by each job.
So that every number the tracker has ever shown can be traced back to the exact data it came from, every job
(scheduled or started by a button: crawls, usage checks, the views job, lookups) and every PDF report ends by
recording the **state** of all data files, crawl snapshots, crawl reports, CSV files, logs and the code in
`archive\`. Each distinct file content is stored once, named by its SHA-256 fingerprint, so a file that hasn't
changed takes no extra space (roughly 6–12 MB a day in all). A report's state records the PDF's fingerprint, a
fingerprint of all its figures, and the state of the data it was computed from; the PDF itself isn't stored. The register `archive\register.jsonl` lists every state, each line chained to the one before
it by its fingerprint, so a later edit or deletion is detectable. Times in the archive are UTC. Archiving began on
4 Nov 2026 (07:47 UTC); the data's earlier states weren't kept, except the crawl snapshots and crawl reports.

Never edit or delete anything in `archive\` by hand, and include it in your backups. If archiving ever fails, the job
still completes and its log shows a warning.

```
python archive.py list                    every recorded state: when, which job, how much space it added
python archive.py verify                  check the whole archive (chain, states, every stored file)
python archive.py restore 12 D:\restored  rebuild the data files of state 12 (or "latest") in an empty folder
python archive.py find report.pdf         was this PDF made by the tracker, and from which data?
python archive.py snapshot --note "..."   record the current state by hand
```

## Automatic crawls

Two Windows scheduled tasks run in the background (no window):

| Task | When | What |
|---|---|---|
| Korikath Tracker (weekly full crawl) | Every Monday, 9:00 AM | Full crawl, then on-wiki usage check |
| Korikath Tracker (daily quick check) | Every other day, 9:00 AM | Quick check, then on-wiki usage check |
| Korikath Tracker (daily views) | Every day, 11:00 AM | View counts (1/30 of the files a day) |

The usage check asks Commons where each file is used (50 files per request, about 1,950 requests, roughly 20
minutes). Commons has no feed of usage changes, so every check looks at every file. The **Check on-wiki usage**
button on the Crawls page runs one on its own.

If the PC is off or asleep at that time, the crawl runs as soon as it's next on (it waits up to 20 minutes for the
internet connection). They run only while you're logged in to Windows. Only one crawl can run at a time; if two would
overlap (a button press, or both catching up after the PC was off), the second one is skipped. Scheduled crawls log
to `logs\scheduled.log`, button-started ones to `logs\manual.log`.

Change or remove them in **Task Scheduler** (Start menu → Task Scheduler → Task Scheduler Library).

## Quick check and full crawl

A **full crawl** works the way PetScan does with unlimited depth: it starts at the mother category, walks every
subcategory breadth-first, and collects each file once by its page id (a file in several categories counts once).

A **quick check** gets the same result with far fewer requests. First it reads Commons' own file and subcategory
counters for every category (about 7 requests for the whole tree). Then for each category it asks what was added
since the last crawl. If the counter changed by exactly the number of additions, nothing was removed, so the last
crawl's member list plus the additions is used. If anything doesn't add up (a removal, a deletion, a move out, an odd
counter), that category is re-read in full, so changes aren't missed. A quick check can't see file renames; the weekly
full crawl picks those up. When there's no earlier full crawl to build on, a quick check runs as a full crawl.

All requests go one at a time over a single reused connection, with gzip compression and maxlag (backing off when
Wikimedia's servers are busy), so a quick check is lighter on their servers as well as faster.

## What each crawl does

1. **Crawls the tree** (quick or full, above).
2. **Saves a snapshot** to `snapshots/<UTC time>.json.gz` with every category (its parents in the tree and its member
   count) and every file (which tree categories it sits in).
3. **Compares it with the previous snapshot** and writes `reports/<UTC time>.json`:
   - files that left the tree, each marked **Deleted** (with who deleted it and why, from the deletion log) or
     **Moved outside** (with the categories it is in now)
   - files added to the tree
   - files moved between categories inside the tree
   - renamed files and categories (tracked by page id, so a rename isn't reported as removed + added)
   - categories removed from the tree, added, or moved to a different parent
4. **Looks up upload dates** for files it hasn't seen before and keeps them in `uploads.json.gz`. A file's
   original upload date never changes, so each file is looked up only once (50 files per request); a normal crawl
   only looks up the new uploads. The date used is the file's earliest version, so a file overwritten later still
   counts from its first upload. These dates feed the "media over time" chart.
5. **Checks quality, featured and valued images, featured media and main-page features.** Never uses Korikath's
   own hand-kept subcategories. Quality / featured / valued / featured media come from the Commons-wide categories
   the assessment templates maintain (Category:Quality images, Category:Featured pictures on Wikimedia Commons,
   Category:Valued images sorted by promotion date, Category:Featured media). Picture / Media of the Day on Commons
   comes from the day templates that show the file on the main page (Template:Potd/2023-03-05,
   Template:Motd/2023-03-05), with exact dates. Picture / media of the day on other wikis is recognised, without extra
   requests, from the names of the pages that use the file in the usage data ("Template:Potd/…", "আজকের নির্বাচিত
   ছবি", "A nap képe", "오늘의 그림" and others), so a wiki with an unusual name for it may be missed. The dashboard
   adds Commons and the other wikis together ("On the main page" card, "Main page features" on the On-wiki usage page).
   It also counts **times on a main page**: every appearance, i.e. each distinct day a file was on a wiki's main page
   (dates read from the page names in any script; a wiki that only lists the month counts once per month; at least
   once per wiki), so a file featured once on 10 wikis counts 10. Candidate and proposal pages are left out. The
   Main page features section lists every wiki that has featured Korikath media by readable name ("Bangla
   Wikipedia", "Wikimedia Commons"); language names come from Commons and are cached in `languages.json`. The first check reads those categories in full
   (about 1,100 requests, once). Every later check takes a few dozen requests: what was added to them since the last
   check (promotions), a direct check of the files already assessed (demotions, delistings) and of files new to the
   project. Promotions appear on the Changes page and removals in the red box; the home page shows the totals and
   the media chart shows them over time.
6. **Finds who made each change** (see below).
7. **Rebuilds `dashboard.html`**.

The first crawl only records a baseline. Changes appear from the second crawl on.

## Who made each change

For every change, the tracker looks up the user who made it and links to the edit (a diff) or to the log entry. The
Changes page, the On-wiki usage page and the red Removed box show this. So do the PDF report, the usage-change and
removed-files downloads, and `usage_log.csv` (columns `changed_by`, `changed_at_utc`, `how`, `edit_link`).

- **Files and categories (on Commons).** The tracker reads the edits made to the file's or category's page between
  the two crawls and finds the one whose diff adds or removes that category. A new file is credited to its upload.
  A renamed file or category is credited to the move log, and a deleted one to the deletion log. A file that left
  the tree because its whole category did is credited to that category's change.
- **On-wiki usage (on the wiki that uses the file).** The tracker checks these sources in order, and the first one
  that explains the change is used:
  1. The edits of the page itself between the two usage checks, whose diff adds or removes the file's name.
  2. The edits, in that time, of the templates, modules and other pages the page includes (an infobox, a navbox, a
     rotating "picture of the day" subpage).
  3. The edits of the page's Wikidata item, since many infoboxes take their image from Wikidata.
  4. The same three, a week further back. A wiki updates its usage records in the background, so an edit can show
     up a check later. These are marked "recorded late".
- **What caused the change.** The tool says it plainly whenever the cause is not an ordinary edit:
  - **Page deleted by …**, with the deletion log. Many usage removals happen because the page using the file was
    deleted.
  - **Page moved by …**, with the move log. The file disappears from the old title and appears on the new one.
  - **Page turned into a redirect by …**.
  - **Edit of Template:…** or **Wikidata edit of Q…**, when the file came or went through those.
- **When no edit names the file.** For example, a gallery syntax fix, or a navbox that was removed from the page. The
  tool shows "no edit names the file", lists who edited the page in that time, and links to all those edits as one
  diff. It does not credit anyone in that case. A daily rotating page (such as a wiki's picture of the day) changes
  with the date, without any edit, so it shows "not found".

This costs a few requests per change, one at a time. A single check makes at most 1,500 such requests and spends at most 15 minutes; any changes
beyond that are marked "not looked up". To fill in changes from before this feature existed, or any whose lookup
failed, run `python tracker.py --attribute`.

## Command line

```
python tracker.py             quick check
python tracker.py --full      full crawl
python tracker.py --dashboard rebuild dashboard.html from saved data, without contacting Commons
python tracker.py --upload-dates  only look up upload dates/uploaders not cached yet, then rebuild the dashboard
python tracker.py --usage     add to a crawl: afterwards check on-wiki usage
python tracker.py --usage-only    only the on-wiki usage check, for the files of the latest crawl
python tracker.py --views     the view-count job (history for files without one, then today's daily update)
python tracker.py --version   the tool and method versions
python tracker.py --migrate   one-off conversion of saved data to the current formats (done on 5 Nov 2026)
python provenance.py          check every data file against its metadata
python methodology.py         the definitions, rules and limitations (--check: unchanged since recorded?)
python -m unittest discover -s tests   run the tests
python tracker.py --attribute find who made the changes of saved crawls and usage checks that don't have it yet
python install.py             recreate the shortcut and scheduled tasks (e.g. after moving this folder)
python archive.py list|verify|restore|find|snapshot   the data archive (see Archive above)
python report.py              PDF statistics report of the project, saved in your Downloads folder
python report.py --user "Name"    PDF report of one contributor (--out FOLDER to save elsewhere)
python user_files.py "Category" "Name"   list the files Name uploaded to Category and below it, with the total
python user_files.py "Category" "Name" --direct   only files directly in Category
python user_files.py "Category"          everyone who uploaded to Category, with their counts
```

## Settings (`config.json`)

| Key | Meaning |
|---|---|
| `root_category` | The mother category |
| `max_depth` | `null` = unlimited (PetScan depth). A number limits how deep subcategories are followed |
| `exclude_categories` | Category titles to skip (with their whole subtree), e.g. a sister project you don't want counted |
| `max_categories` | Safety stop if the tree suddenly grows huge (e.g. someone puts a big unrelated category inside) |
| `user_agent` | Sent to Commons. Wikimedia asks for contact info here; add an email or your user page if you like |
| `issuer` | Who issues the reports: `{"name": "...", "role": "...", "contact": "..."}`. Printed on every report's cover; a report without it is PROVISIONAL |
| `report_licence` | Licence of the reports' text and figures (default `CC BY 4.0`); images keep their own licences |
| `public_register_url` | Where `published/register.csv` is published, printed on every report so readers can check it |
| `thumbnails` | `safe` (default: only credited images, never ones marked as showing identifiable people), `all`, or `none` |
| `contributor_opt_out` | Uploaders who asked not to be named: counted in totals, named nowhere in reports, no report of their own |

## Files

- `tracker.py`: crawler, snapshot, diff, dashboard builder
- `methodology.py`, `methodology_versions.json`: every definition, rule, threshold and limitation, and the recorded
  fingerprint of each method version
- `version.py`, `CHANGELOG.md`: tool version and build information; what changed in each version
- `provenance.py`: the `.meta` file of every data file and the run records in `runs/`
- `validate.py`: the checks run before every report (FINAL or PROVISIONAL); `--petscan`: the monthly independent count
- `issuance.py`, `published/`: report IDs, the register of issued reports, their manifests, corrections
- `migrate.py`: the one-off 2.0.0 conversion (UTC, metadata)
- `tests/`: automated tests
- `attribution.py`: finds who made each change (see **Who made each change**)
- `server.py`: local server behind the dashboard's buttons
- `archive.py`: the data archive (see **Archive**); `archive\` holds it. Never edit by hand
- `RELIABILITY_PLAN.md`: what is planned so that every published number can be relied on and checked
- `user_files.py`: one contributor's files in one category (the "Files by contributor" page and command line)
- `report.py`: PDF reports (project and per contributor), from the dashboard's data and the saved data files
- `install.py`: creates the shortcut, its icon (`korikath.ico`) and the scheduled tasks
- `dashboard_template.html`: dashboard page; data is embedded into `dashboard.html` at build time
- `status.js`: live progress of a running crawl, read by the open dashboard
- `uploads.json.gz`: cached upload date and uploader of every file; delete it to look them all up again
- `assessments.json.gz`, `assessment_history.json`: which files are quality images, featured pictures and valued
  images, and every check's totals, promotions and removals
- `geo.json.gz`, `exif.json.gz`: each file's camera location on Commons, and the camera make, model, GPS
  position and time taken from its own metadata, plus its file size; `map_points.js`: the geocoded files for the dashboard map (loaded only on that page)
- `mediatypes.json`: cached media type (audio, video, document, ...) of files that aren't plain images; the
  home page's "Non-image files" card counts from it. Image extensions (jpg, png, svg, ...) are counted by name;
  everything else is looked up once, because a .webm or .ogg can be video or just audio
- `usage.json.gz`, `usage_history.json`: the latest on-wiki usage check and the totals of every check
- `removed_files.csv`: every file that has ever left the project tree, from all saved crawls (when, deleted or moved
  outside, who deleted it and why, the categories it was in and is in now, with links); rewritten by each dashboard
  build. The Changes page's **Download removed files** panel (also reachable from the red Removed box) downloads any
  date range of it (From/To in UTC, or Last 7 days / Last 30 days / All time), named with the range and the time of
  download, e.g. `korikath_removed_files_2026-10-01_to_2026-10-26_downloaded_2026-10-26_1432.csv`
- `usage_log.csv`, `usage_current.csv`: usage change log and current usage (downloadable from the dashboard)
- `snapshots/`, `reports/`, `history.json`: the saved crawls. Keep them; each crawl compares against the newest snapshot.
- `run.bat`: same as the shortcut
