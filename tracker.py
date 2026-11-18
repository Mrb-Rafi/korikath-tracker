"""Reoa: Project Korikath category tracker.

Reoa is a Bangla word for a short yearly summary drawn from the ledger: income and spending, what is owed and what is due.


Each run crawls the whole category tree under the project's mother category on
Wikimedia Commons, counts the unique files in it (the same way PetScan does:
walk subcategories, de-duplicate by page id), saves a snapshot, compares it
with the previous snapshot and regenerates dashboard.html.

Standard library only. Usage:
    python tracker.py [--quick]       quick check, snapshot, diff, rebuild dashboard
    python tracker.py --full          same, but a full crawl that re-reads every category
    python tracker.py --dashboard     only rebuild dashboard.html from saved data
    python tracker.py --upload-dates  only fetch upload dates not cached yet, then rebuild the dashboard
    python tracker.py --usage         add to a crawl: then check where files are used on other wikis
    python tracker.py --usage-only    only that usage check, for the files of the latest crawl
"""

import argparse
import csv
import re
import gzip
import hashlib
import http.client
import io
import json
import sys
import time
import unicodedata
import urllib.parse
from collections import deque
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

API_HOST = "commons.wikimedia.org"
API_PATH = "/w/api.php"
BASE = Path(__file__).resolve().parent
SNAPSHOT_DIR = BASE / "snapshots"
REPORT_DIR = BASE / "reports"
HISTORY_FILE = BASE / "history.json"
TEMPLATE_FILE = BASE / "dashboard_template.html"
DASHBOARD_FILE = BASE / "dashboard.html"
STATUS_FILE = BASE / "status.js"  # read by the open dashboard to show live progress
LOCK_FILE = BASE / "crawl.lock"
VIEWS_FILE = BASE / "views.json.gz"  # view counts: per file monthly history, and daily totals for the project
VIEWS_LOCK = BASE / "views.lock"     # the views job has its own lock, so it never blocks the crawls
VIEWS_STATUS_FILE = BASE / "views_status.js"  # live progress of the views job, read by the open dashboard
USAGE_FILE = BASE / "usage.json.gz"          # the latest on-wiki usage check
USAGE_LOG = BASE / "usage_log.csv"           # every usage added or removed, one row each, growing
USAGE_CURRENT = BASE / "usage_current.csv"   # all current usages, rewritten by each check
USAGE_HISTORY = BASE / "usage_history.json"  # totals of each usage check
REMOVED_CSV = BASE / "removed_files.csv"     # every file that has left the project, rewritten by each build
ASSESS_FILE = BASE / "assessments.json.gz"  # quality / featured / valued status of project files
ASSESS_HISTORY = BASE / "assessment_history.json"  # totals and changes of each assessment check
GEO_FILE = BASE / "geo.json.gz"    # page id -> [lat, lon] camera location on Commons, or None
EXIF_FILE = BASE / "exif.json.gz"  # page id -> [make, model, gps lat, gps lon] from the file's own metadata, or None
MAP_POINTS_FILE = BASE / "map_points.js"  # geocoded files for the dashboard map, loaded only on that page
LANGS_FILE = BASE / "languages.json"  # language code -> English name, for readable wiki names
MEDIATYPES_FILE = BASE / "mediatypes.json"  # page id -> Commons media type, for files that aren't plain images
UPLOADS_FILE = BASE / "uploads.json.gz"  # page id -> [original upload time, uploader], fetched once per file
REPORTS_IN_DASHBOARD = 30

UTC = timezone.utc  # every time Reoa stores or shows is UTC

# Every definition, rule and threshold lives in the methodology register (methodology.py).
from methodology import (  # noqa: E402
    IMAGE_EXTS, MEDIA_KINDS, BRANDS, PHONE_BRANDS, CAMERA_BRANDS, SCANNER_BRANDS,
    ASSESS_CATS, MAIN_PAGE_KINDS, ASSESS_NAMES, DAY_TEMPLATE, MOTD_PAGE, POTD_PAGE,
    NOT_MAIN_PAGE, MONTH_NAMES, VIEWS_FROM, QUICK_CHECK_OVERLAP, ROTATION_DAYS, CATCH_ALL_SHARE,
    WRONG_CLOCK_EARLIEST_GAP_DAYS, WRONG_CLOCK_BEFORE_YEAR, ATTRIBUTION_OVERLAP)

NS_FILE = 6
NS_CATEGORY = 14


# --------------------------------------------------------------------------- API


class CommonsAPI:
    """Talks to the Commons API over one reused HTTPS connection: faster for us and fewer
    TLS handshakes for Wikimedia than opening a new connection per request."""

    def __init__(self, user_agent, host=API_HOST):
        self.user_agent = user_agent
        self.host = host  # another wiki's API (e.g. "en.wikipedia.org") works the same way
        self.requests = 0
        self.bytes = 0
        self.conn = None

    def wait_for_network(self, max_wait=20 * 60):
        """After a missed scheduled run the PC may have just booted and Wi-Fi may not be up
        yet, so keep trying for a while before giving up."""
        deadline = time.monotonic() + max_wait
        while True:
            try:
                self.get(action="query", meta="siteinfo")
                return
            except NETWORK_ERRORS as e:
                if time.monotonic() > deadline:
                    raise
                print(f"  no connection to Commons yet ({e}); trying again in 60s", file=sys.stderr)
                time.sleep(60)

    def _fetch(self, path):
        if self.conn is None:
            self.conn = http.client.HTTPSConnection(self.host, timeout=60)
        try:
            self.conn.request("GET", path, headers={"User-Agent": self.user_agent, "Accept-Encoding": "gzip"})
            resp = self.conn.getresponse()
            body = resp.read()
        except BaseException:
            self.conn.close()
            self.conn = None
            raise
        if resp.status != 200:
            raise ServerBusy(resp.status, resp.getheader("Retry-After"))
        self.bytes += len(body)  # as sent over the network
        if resp.getheader("Content-Encoding") == "gzip":
            body = gzip.decompress(body)
        return json.loads(body.decode("utf-8"))

    def get(self, **params):
        # Requests are made one at a time (never in parallel), with maxlag so we back off whenever
        # the servers are busy, and gzip to save bandwidth: the API etiquette Wikimedia asks for.
        params = {"format": "json", "formatversion": "2", "maxlag": "5", **params}
        path = API_PATH + "?" + urllib.parse.urlencode(params)
        for attempt in range(6):
            try:
                data = self._fetch(path)
                self.requests += 1
                REQUEST_COUNTS[self.host] = REQUEST_COUNTS.get(self.host, 0) + 1
                err = data.get("error")
                if err and err.get("code") == "maxlag":
                    time.sleep(5 * (attempt + 1))
                    continue
                if err:
                    raise RuntimeError(f"API error: {err.get('code')}: {err.get('info')}")
                return data
            except ServerBusy as e:
                if attempt == 5:
                    raise
                wait = int(e.retry_after) if (e.retry_after or "").isdigit() else 10 * (attempt + 1)
                print(f"  Commons answered HTTP {e.status}; waiting {wait}s", file=sys.stderr)
                time.sleep(wait)
            except NETWORK_ERRORS as e:
                if attempt == 5:
                    raise
                wait = 2 ** attempt
                print(f"  network error ({e}); retrying in {wait}s", file=sys.stderr)
                time.sleep(wait)
        raise RuntimeError("Commons API kept reporting maxlag; try again later")

    def query_all(self, **params):
        """Yield every result page of a query, following continuation."""
        cont = {}
        while True:
            data = self.get(action="query", **params, **cont)
            yield data
            if "continue" not in data:
                return
            cont = data["continue"]


class ServerBusy(Exception):
    def __init__(self, status, retry_after):
        super().__init__(f"HTTP {status}")
        self.status, self.retry_after = status, retry_after


NETWORK_ERRORS = (OSError, http.client.HTTPException)  # OSError covers DNS, refused, reset, timeout
REQUEST_COUNTS = {}  # host -> API requests made by this process, for the run record (provenance.py)


def _dt(when):
    if isinstance(when, str):
        when = datetime.fromisoformat(when.replace("Z", "+00:00"))
    return when.astimezone(UTC)


def fmt_time(when):
    """A time for people: '23 Oct 2026, 16:28 UTC'."""
    t = _dt(when)
    return f"{t.day} {t:%b %Y}, {t:%H:%M} UTC"


def csv_time(when):
    """A time for spreadsheets (sorts correctly): '2026-10-23 16:28 UTC'."""
    return f"{_dt(when):%Y-%m-%d %H:%M} UTC"


def iso_utc(when):
    """A time for machines: '2026-10-23T16:28:12Z'."""
    return f"{_dt(when):%Y-%m-%dT%H:%M:%SZ}"


bst = fmt_time  # old name, kept so older callers keep working


def normalize_category(title):
    title = title.strip().replace("_", " ")
    if not title.lower().startswith("category:"):
        title = "Category:" + title
    return "Category:" + title.split(":", 1)[1].strip()


# ------------------------------------------------------------------------- crawl


def fetch_counts(api, cat_ids):
    """Files + subcategories in each category, per Commons' own counters (50 per request)."""
    out = {}
    cat_ids = list(cat_ids)
    for i in range(0, len(cat_ids), 50):
        batch = cat_ids[i:i + 50]
        for data in api.query_all(prop="categoryinfo", pageids="|".join(batch)):
            for p in data["query"]["pages"]:
                ci = p.get("categoryinfo") or {}
                out[str(p["pageid"])] = ci.get("files", 0) + ci.get("subcats", 0)
        for cid in batch:
            out.setdefault(cid, 0)  # deleted or empty category
    return out


def previous_members(prev):
    """Rebuild each category's member list from a snapshot: {cat id: [(page id, ns, title)]}."""
    members = {cid: [] for cid in prev["categories"]}
    for fid, f in prev["files"].items():
        for cid in f["categories"]:
            members.setdefault(cid, []).append((fid, NS_FILE, f["title"]))
    for sid, c in prev["categories"].items():
        for cid in c["parents"]:
            members.setdefault(cid, []).append((sid, NS_CATEGORY, c["title"]))
    return members


def crawl(api, root_title, max_depth=None, exclude=(), max_categories=5000, progress=None, prev=None, since=None):
    """Breadth-first walk of the category tree, like PetScan's depth search.

    Returns (categories, files, stats):
      categories: {pageid: {"title", "depth", "parents": [pageid, ...], "count": n}}
      files:      {pageid: {"title", "categories": [category pageid, ...]}}
    Page ids are used as keys so renames are detected as renames, not as a
    removal plus an addition.

    Quick check: when `prev` (the last snapshot) and `since` are given, a category
    is not listed in full if nothing but additions happened to it: Commons' member
    counter must have changed by exactly the number of new members added since
    then. Then its previous member list plus the additions is used. Anything else
    (removals, deletions, a counter that doesn't add up) means a full listing, so
    a change is never missed. Each category's counter is read before its members
    are, so a change made mid-run also shows up as a mismatch next time.
    File renames aren't visible to the quick check; a periodic full crawl catches them.
    """
    exclude = {normalize_category(c) for c in exclude}
    info = api.get(action="query", titles=root_title)["query"]["pages"][0]
    if info.get("missing"):
        raise SystemExit(f"Root category does not exist: {root_title}")
    root_id = str(info["pageid"])
    root_title = info["title"]

    categories = {root_id: {"title": root_title, "depth": 0, "parents": []}}
    files = {}
    queue = deque([root_id])
    stats = {"reused": 0, "listed": 0}

    quick = prev is not None and since is not None
    prev_cats = prev["categories"] if quick else {}
    prev_members = previous_members(prev) if quick else {}
    counts = fetch_counts(api, prev_cats) if quick else {}

    def list_all(cat_id):
        for data in api.query_all(list="categorymembers", cmpageid=cat_id, cmtype="file|subcat",
                                  cmprop="ids|title|type", cmlimit="max"):
            if progress:
                progress(len(categories), len(files), categories[cat_id]["title"])
            for m in data["query"]["categorymembers"]:
                yield str(m["pageid"]), m["ns"], m["title"]

    def try_quick(cat_id):
        old = prev_cats.get(cat_id)
        if not old or "count" not in old:
            return None
        known = {pid for pid, _, _ in prev_members.get(cat_id, ())}
        added = []
        for data in api.query_all(list="categorymembers", cmpageid=cat_id, cmsort="timestamp", cmdir="newer",
                                  cmstart=since, cmprop="ids|title|type", cmlimit="max"):
            for m in data["query"]["categorymembers"]:
                if m["ns"] in (NS_FILE, NS_CATEGORY) and str(m["pageid"]) not in known:
                    added.append((str(m["pageid"]), m["ns"], m["title"]))
        if counts[cat_id] - old["count"] != len(added):
            return None
        if progress:
            progress(len(categories), len(files), categories[cat_id]["title"])
        return prev_members.get(cat_id, []) + added

    while queue:
        cat_id = queue.popleft()
        cat = categories[cat_id]
        if cat_id not in counts:
            # Read the counter before the members; batch it with other queued categories that need one.
            need = [cat_id] + [q for q in queue if q not in counts][:49]
            counts.update(fetch_counts(api, need))
        members = try_quick(cat_id) if quick else None
        how = "unchanged/additions only" if members is not None else "full listing"
        if members is None:
            members = list_all(cat_id)
            stats["listed"] += 1
        else:
            stats["reused"] += 1
        cat["count"] = counts[cat_id]
        print(f"  [{len(categories):>4} cats | {len(files):>6} files] depth {cat['depth']}: {cat['title']} ({how})")
        for pid, ns, title in members:
            if ns == NS_FILE:
                f = files.setdefault(pid, {"title": title, "categories": []})
                f["categories"].append(cat_id)
            elif ns == NS_CATEGORY:
                if title in exclude:
                    continue
                if pid in categories:
                    if cat_id not in categories[pid]["parents"] and pid != root_id:
                        categories[pid]["parents"].append(cat_id)
                    continue
                if max_depth is not None and cat["depth"] + 1 > max_depth:
                    continue
                if len(categories) >= max_categories:
                    raise SystemExit(
                        f"Stopped: more than {max_categories} categories in the tree. "
                        "Something may be leaking into the tree; check the snapshot or "
                        "raise max_categories / set max_depth / add exclude_categories in config.json."
                    )
                categories[pid] = {"title": title, "depth": cat["depth"] + 1, "parents": [cat_id]}
                queue.append(pid)
    return categories, files, stats


# ---------------------------------------------------------------- upload dates


def fetch_upload_dates(api, pageids, progress=None, save_every=None):
    """Original upload time and uploader of each file, from its earliest version, so a file that was
    later overwritten with a new version still counts from its first upload, by its first uploader.
    50 files per request. Files with no upload info (e.g. deleted meanwhile) map to None so they
    aren't asked for again."""
    out = {}
    for i in range(0, len(pageids), 50):
        batch = pageids[i:i + 50]
        for data in api.query_all(prop="imageinfo", pageids="|".join(batch), iiprop="timestamp|user", iilimit="max"):
            for page in data["query"]["pages"]:
                pid = str(page.get("pageid", ""))
                for ii in page.get("imageinfo", ()):
                    if out.get(pid) is None or ii["timestamp"] < out[pid][0]:
                        out[pid] = [ii["timestamp"], ii.get("user", "")]
        for pid in batch:
            out.setdefault(pid, None)
        if progress:
            progress(min(i + 50, len(pageids)), len(pageids))
        if save_every and (i // 50 + 1) % save_every == 0:
            merge_into_cache(out)
    return out


def merge_into_cache(fetched):
    cache = load_json(UPLOADS_FILE, {})
    cache.update(fetched)
    save_json(UPLOADS_FILE, cache, compact=True)


def update_upload_dates(api, file_ids, progress=None, refresh=False):
    """Fetch upload dates for files not in the cache yet (normally just the new uploads), and again for files
    whose lookup found nothing before (retried at every run until it succeeds). Entries from before uploaders
    were stored (a bare date) are looked up again once. With `refresh`, also a fresh look at one thirtieth of the
    files (a different thirtieth each day): an uploader's account can be renamed, and an early version of a file
    can be hidden or restored, so every cached entry is checked once a month (methodology: ROTATION_DAYS)."""
    cache = load_json(UPLOADS_FILE, {})
    missing = [pid for pid in file_ids if pid not in cache or cache[pid] is None or isinstance(cache[pid], str)]
    if missing:
        print(f"Fetching upload dates for {len(missing)} file(s) not seen before or not found before ...")
        # Long first fills save their progress every 100 requests, so an interruption loses little.
        cache.update(fetch_upload_dates(api, missing, progress, save_every=100))
        save_json(UPLOADS_FILE, cache, compact=True)
    if refresh:
        day = int(time.time() // 86400) % ROTATION_DAYS
        todo = [pid for pid in file_ids if pid not in missing and int(pid) % ROTATION_DAYS == day]
        fresh = fetch_upload_dates(api, todo) if todo else {}
        changed = {pid: v for pid, v in fresh.items() if v is not None and v != cache.get(pid)}
        if changed:
            cache.update(changed)
            save_json(UPLOADS_FILE, cache, compact=True)
        print(f"Upload dates and uploaders re-checked for {len(todo):,} file(s); {len(changed):,} changed.")
    return len(missing)


# ------------------------------------------------------------------ media types

# Extensions that are always still images. Anything else (mp3, webm, ogg, pdf, ...) is asked of
# Commons once, because the extension alone can mislead: a .webm or .ogg can be video or just audio.


def extension(title):
    return title.rsplit(".", 1)[-1].lower() if "." in title else ""


def update_media_types(api, files):
    """Look up the media type of files that aren't plain images and aren't cached yet."""
    cache = load_json(MEDIATYPES_FILE, {})
    missing = [pid for pid, f in files.items() if extension(f["title"]) not in IMAGE_EXTS and pid not in cache]
    for i in range(0, len(missing), 50):
        batch = missing[i:i + 50]
        for data in api.query_all(prop="imageinfo", pageids="|".join(batch), iiprop="mediatype"):
            for page in data["query"]["pages"]:
                info = page.get("imageinfo")
                if info:
                    cache[str(page["pageid"])] = info[0].get("mediatype", "UNKNOWN")
    if missing:
        save_json(MEDIATYPES_FILE, cache)
        print(f"Looked up the media type of {len(missing):,} non-image file(s).")
    return cache


def media_counts(files, cache):
    """Files by kind: image, audio, video, document, other."""
    counts = {"image": 0, "audio": 0, "video": 0, "document": 0, "other": 0}
    for pid, f in files.items():
        if extension(f["title"]) in IMAGE_EXTS:
            counts["image"] += 1
        else:
            counts[MEDIA_KINDS.get(cache.get(pid), "other")] += 1
    return counts


def upload_info(uploads, pid):
    """(upload time, uploader) from the cache; either may be None."""
    v = uploads.get(pid)
    if isinstance(v, list):
        return v[0], v[1] or None
    return v, None  # older cache entry: date only


def growth_series(files, uploads):
    """Files now in the tree, counted by the day (UTC) they were uploaded: [[YYYY-MM-DD, n], ...]."""
    per_day, unknown = {}, 0
    for pid in files:
        ts = upload_info(uploads, pid)[0]
        if not ts:
            unknown += 1
            continue
        day = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(UTC).strftime("%Y-%m-%d")
        per_day[day] = per_day.get(day, 0) + 1
    return {"days": sorted(per_day.items()), "unknown": unknown}


# --------------------------------------------------------------- on-wiki usage


def fetch_usage(api, pageids, progress=None):
    """Where each file is shown on other Wikimedia wikis (Commons itself left out):
    {page id: [[wiki, namespace, page title], ...]} for files used anywhere. 50 files per request."""
    out = {}
    for i in range(0, len(pageids), 50):
        batch = pageids[i:i + 50]
        for data in api.query_all(prop="globalusage", pageids="|".join(batch), gulimit="max",
                                  gufilterlocal="1", guprop="namespace"):
            for page in data["query"]["pages"]:
                for u in page.get("globalusage", ()):
                    out.setdefault(str(page["pageid"]), []).append([u["wiki"], int(u["ns"]), u["title"]])
        if progress:
            progress(min(i + 50, len(pageids)), len(pageids))
    return out


def update_language_names(api, wikis):
    """English names of the languages of these wikis ("bn.wikipedia.org" -> "Bangla"), cached; only
    languages not seen before are looked up."""
    cache = load_json(LANGS_FILE, {})
    codes = sorted({w.split(".")[0] for w in wikis} - set(cache) - {"www", "commons", "meta", "species", "incubator"})
    for i in range(0, len(codes), 50):
        data = api.get(action="query", meta="languageinfo", liprop="name", licode="|".join(codes[i:i + 50]), uselang="en")
        for code, info in data["query"]["languageinfo"].items():
            cache[code] = info.get("name") or code
        for code in codes[i:i + 50]:
            cache.setdefault(code, code)  # unknown to Commons: keep the code
    if codes:
        save_json(LANGS_FILE, cache)


def wiki_page_url(wiki, title):
    return f"https://{wiki}/wiki/" + urllib.parse.quote(title.replace(" ", "_"), safe=":/")


def commons_url(title):
    return "https://commons.wikimedia.org/wiki/" + urllib.parse.quote(title.replace(" ", "_"), safe=":/")


USAGE_COLUMNS = ["file", "file_url", "wiki", "namespace", "page", "page_url", "uploader"]
# Who made each change (attribution.py): the user, the edit's time (UTC), how it was found, and the diff or log
# link (or the page history when no edit was found).
ATTRIB_COLUMNS = ["changed_by", "changed_at_utc", "how", "edit_link"]
USAGE_LOG_COLUMNS = ["checked_at", "change"] + USAGE_COLUMNS + ATTRIB_COLUMNS


def attrib_cells(edit):
    edit = edit or {}
    return [edit.get("user") or "", edit.get("timestamp") or "", edit.get("how") or "", edit.get("url") or ""]


def read_usage_log():
    """(header, rows) of the usage change log, rows padded to the current columns."""
    if not USAGE_LOG.exists():
        return list(USAGE_LOG_COLUMNS), []
    with USAGE_LOG.open(encoding="utf-8-sig", newline="") as fh:
        all_rows = list(csv.reader(fh))
    rows = [r + [""] * (len(USAGE_LOG_COLUMNS) - len(r)) for r in all_rows[1:]]
    return list(USAGE_LOG_COLUMNS), rows


def write_usage_log(rows):
    # utf-8-sig so Excel shows Bengali and other scripts correctly.
    tmp = USAGE_LOG.with_name(USAGE_LOG.name + ".tmp")
    with tmp.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(USAGE_LOG_COLUMNS)
        w.writerows(rows)
    tmp.replace(USAGE_LOG)


def attribute_usage(user_agent, rows, start, end):
    """Fill in who made each usage change in `rows` (log rows), looking at edits in [start, end]."""
    import attribution
    finder = attribution.Finder(user_agent)
    try:
        items = [(i, r[4], r[6], r[2], r[1]) for i, r in enumerate(rows)]
        print(f"Finding who made {len(items):,} usage change(s) ...")
        found = finder.usage_changes(items, start, end)
        for i, r in enumerate(rows):
            r[len(r) - len(ATTRIB_COLUMNS):] = attrib_cells(found.get(i))
        named = sum(1 for e in found.values() if e.get("user"))
        print(f"  found the edit for {named:,} of {len(items):,} ({finder.requests:,} requests)")
    finally:
        finder.close()


def usage_rows(pid, usages, files, uploads):
    f = files.get(pid, {})
    title = f.get("title", f"(page id {pid})")
    uploader = upload_info(uploads, pid)[1] or ""
    for wiki, ns, page in usages:
        yield [title, commons_url(title), wiki, ns, page.replace("_", " "), wiki_page_url(wiki, page), uploader]


def check_usage(api, snapshot, progress=None):
    """Look up the on-wiki usage of every file in the snapshot, log what was added or removed since
    the last check, and save the result. Files that weren't in the project at the last check are
    taken as they are (their existing usage isn't logged as "added")."""
    files = snapshot["files"]
    checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"Checking on-wiki usage of {len(files):,} files ...")
    current = fetch_usage(api, list(files), progress)
    update_language_names(api, {u[0] for v in current.values() for u in v})
    uploads = load_json(UPLOADS_FILE, {})
    prev = load_json(USAGE_FILE)

    added = removed = 0
    if prev:
        before = set(prev["checked"])
        old_usage = prev["files"]
        stamp = csv_time(checked_at)
        rows = []
        for pid in files:
            if pid not in before:
                continue
            old = {tuple(u) for u in old_usage.get(pid, ())}
            new = {tuple(u) for u in current.get(pid, ())}
            for change, items in (("added", new - old), ("removed", old - new)):
                for row in usage_rows(pid, sorted(items), files, uploads):
                    rows.append([stamp, change] + row)
            added += len(new - old)
            removed += len(old - new)
        rows = [r + [""] * len(ATTRIB_COLUMNS) for r in rows]
        if rows:
            start = datetime.fromisoformat(prev["checked_at"]) - ATTRIBUTION_OVERLAP
            try:
                attribute_usage(api.user_agent, rows, start, datetime.now(timezone.utc))
            except Exception as e:  # noqa: BLE001  the usage check itself must still be saved
                print(f"WARNING: could not find who made the usage changes ({e}); "
                      "run 'python tracker.py --attribute' later to fill them in.", file=sys.stderr)
        _, old_rows = read_usage_log()
        write_usage_log(old_rows + rows)

    # utf-8-sig so Excel shows Bengali and other scripts correctly.
    with USAGE_CURRENT.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(USAGE_COLUMNS)
        for pid in sorted(current, key=lambda p: files[p]["title"]):
            w.writerows(usage_rows(pid, current[pid], files, uploads))

    save_json(USAGE_FILE, {"checked_at": checked_at, "checked": list(files), "files": current}, compact=True)
    n_usages = sum(len(v) for v in current.values())
    history = load_json(USAGE_HISTORY, [])
    history.append({"checked_at": checked_at, "files_used": len(current), "usages": n_usages,
                    "pages": len({(u[0], u[2]) for v in current.values() for u in v}),
                    "wikis": len({u[0] for v in current.values() for u in v}),
                    "added": added if prev else None, "removed": removed if prev else None})
    save_json(USAGE_HISTORY, history)
    print(f"On-wiki usage: {len(current):,} files used, {n_usages:,} uses on other wikis"
          + (f"; since last check {added:,} added, {removed:,} removed" if prev else " (first check: baseline)"))


def usage_summary(snapshot, usage, uploads):
    """Aggregates for the dashboard, for all pages and for content pages only (namespace 0:
    Wikipedia articles, Wikidata items and so on)."""
    files, cats = snapshot["files"], snapshot["categories"]
    children, by_cat = {}, {}
    for cid, c in cats.items():
        for par in c["parents"]:
            children.setdefault(par, []).append(cid)
    for fid, f in files.items():
        for c in f["categories"]:
            by_cat.setdefault(c, []).append(fid)

    def below(cid):
        seen, found, stack = set(), set(), [cid]
        while stack:
            x = stack.pop()
            if x not in seen:
                seen.add(x)
                found.update(by_cat.get(x, ()))
                stack.extend(children.get(x, ()))
        return found

    subtree = {cid: below(cid) for cid in cats}
    uploader_of = {pid: upload_info(uploads, pid)[1] for pid in files}
    files_by_uploader = {}
    for pid, u in uploader_of.items():
        if u:
            files_by_uploader[u] = files_by_uploader.get(u, 0) + 1

    def summarise(only_content):
        use = {}
        for pid, us in usage["files"].items():
            if pid not in files:
                continue
            kept = [u for u in us if not only_content or u[1] == 0]
            if kept:
                use[pid] = kept
        n_usages = sum(len(v) for v in use.values())
        wikis = {}
        for pid, us in use.items():
            for wiki, _, _ in us:
                w = wikis.setdefault(wiki, [0, set()])
                w[0] += 1
                w[1].add(pid)
        by_uploader = {}
        for pid, us in use.items():
            u = uploader_of.get(pid) or "(unknown)"
            b = by_uploader.setdefault(u, [0, 0])
            b[0] += 1
            b[1] += len(us)
        categories = []
        for cid, fset in subtree.items():
            used = [p for p in fset if p in use]
            if used:
                categories.append([cats[cid]["title"], len(fset), len(used), sum(len(use[p]) for p in used)])
        # The top 40 by pages and the top 40 by wikis, so the dashboard can sort either way.
        by_pages = sorted(use.items(), key=lambda kv: (-len(kv[1]), files[kv[0]]["title"]))[:40]
        by_wikis = sorted(use.items(), key=lambda kv: (-len({u[0] for u in kv[1]}), files[kv[0]]["title"]))[:40]
        top_files = list(dict(by_pages + by_wikis).items())
        return {
            "files_used": len(use), "usages": n_usages, "wikis": len(wikis),
            "pages": len({(u[0], u[2]) for us in use.values() for u in us}),
            "by_wiki": sorted([[w, n, len(f)] for w, (n, f) in wikis.items()], key=lambda r: -r[1]),
            "by_uploader": sorted([[u, files_by_uploader.get(u, 0), n_files, n]
                                   for u, (n_files, n) in by_uploader.items()], key=lambda r: (-r[2], -r[3])),
            "by_category": sorted(categories, key=lambda r: (-r[2], -r[3])),
            "top_files": [[files[pid]["title"], uploader_of.get(pid), len(us), len({u[0] for u in us}),
                           [[u[0], u[2]] for u in us[:6]]] for pid, us in top_files],
        }

    header, rows = read_usage_log()
    history = load_json(USAGE_HISTORY, [])
    # Each log row carries its check's time as csv_time() wrote it; map it back to the exact time so the
    # dashboard can download any date range of the log.
    when_checked = {csv_time(h["checked_at"]): h["checked_at"] for h in history}

    # The changes of each recent check, so the dashboard can show them next to the crawl they followed.
    # Log rows carry the check's time as written by csv_time(), which identifies the check.
    by_stamp = {}
    for r in rows:
        by_stamp.setdefault(r[0], []).append(r)
    checks = []
    for h in history[-30:]:
        if h.get("added") is None:
            continue  # the first check is the baseline: nothing to compare with
        mine = by_stamp.get(csv_time(h["checked_at"]), [])
        checks.append({"checked_at": h["checked_at"], "added_n": h["added"], "removed_n": h["removed"],
                       "removed": [r for r in mine if r[1] == "removed"][:500],
                       "added": [r for r in mine if r[1] == "added"][:500]})
    return {"checked_at": usage["checked_at"], "total_files": len(files),
            "all": summarise(False), "content": summarise(True),
            "recent": rows[-500:][::-1], "log_rows": len(rows),  # newest first
            "log": {"columns": header, "rows": rows, "found": [when_checked.get(r[0]) for r in rows]},
            "history": history, "checks": checks}


# ------------------------------------------------------- locations and devices


def _number(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _taken(md):
    """When the photo was taken, from its metadata ("2023:06:21 14:22:05" -> "2023-06-21T14:22:05"); the
    camera's own clock, usually local time. None if missing or impossible."""
    for key in ("DateTimeOriginal", "DateTimeDigitized"):
        m = re.match(r"(\d{4})[:\-](\d{2})[:\-](\d{2})[ T](\d{2}):(\d{2}):(\d{2})", str(md.get(key) or ""))
        if m and 1990 <= int(m.group(1)) <= 2100 and 1 <= int(m.group(2)) <= 12 and 1 <= int(m.group(3)) <= 31:
            return "{}-{}-{}T{}:{}:{}".format(*m.groups())
    return None


def fetch_file_details(api, pageids, with_exif, progress=None, save_every=None):
    """Camera location on Commons (the primary coordinates of the file page, from {{Location}}) and,
    with `with_exif`, the file's size and, from its own metadata, the camera make, model, GPS position
    and the time the photo was taken. 50 files per request. Returns (geo, exif) for the files asked
    about; exif records are [make, model, gps lat, gps lon, taken, size in bytes]."""
    geo, exif = {}, {}
    for i in range(0, len(pageids), 50):
        batch = pageids[i:i + 50]
        params = dict(prop="coordinates|imageinfo" if with_exif else "coordinates", pageids="|".join(batch),
                      coprimary="primary", colimit="max")
        if with_exif:
            params["iiprop"] = "metadata|size"
        for data in api.query_all(**params):
            for page in data["query"]["pages"]:
                pid = str(page.get("pageid", ""))
                for c in page.get("coordinates", ()):
                    if c.get("primary") and c.get("globe", "earth") == "earth":
                        geo[pid] = [round(c["lat"], 6), round(c["lon"], 6)]
                if with_exif and page.get("imageinfo"):
                    info = page["imageinfo"][0]
                    md = {m["name"]: m["value"] for m in info.get("metadata") or []
                          if isinstance(m, dict) and not isinstance(m.get("value"), (list, dict))}
                    make, model = (str(md.get(k) or "").strip() or None for k in ("Make", "Model"))
                    lat, lon = _number(md.get("GPSLatitude")), _number(md.get("GPSLongitude"))
                    gps = [round(lat, 6), round(lon, 6)] if lat is not None and lon is not None and (lat or lon) else [None, None]
                    exif[pid] = [make, model, *gps, _taken(md), info.get("size")]
        for pid in batch:
            geo.setdefault(pid, None)
            if with_exif:
                exif.setdefault(pid, None)
        if progress:
            progress(min(i + 50, len(pageids)), len(pageids))
        if save_every and (i // 50 + 1) % save_every == 0:
            _merge_details(geo, exif)
    return geo, exif


def _merge_details(geo, exif):
    for path, new in ((GEO_FILE, geo), (EXIF_FILE, exif)):
        if new:
            cache = load_json(path, {})
            cache.update(new)
            save_json(path, cache, compact=True)


def update_file_details(api, files, progress=None):
    """Location and metadata of files not seen before, plus a fresh look at the location of one
    thirtieth of the files (a different thirtieth each day), since a location can be added to a file
    page at any time. The camera metadata of a file doesn't change, so it is fetched only once."""
    exif = load_json(EXIF_FILE, {})
    # Records from before the time taken and file size were kept (shorter, or None) are looked up again once.
    new = [pid for pid in files if not (isinstance(exif.get(pid), list) and len(exif[pid]) >= 6)]
    if new:
        print(f"Looking up location and camera of {len(new):,} file(s) ...")
        g, e = fetch_file_details(api, new, True, progress, save_every=100)
        _merge_details(g, e)  # everything, including the last batches since the previous save
    day = int(time.time() // 86400) % ROTATION_DAYS
    geo = load_json(GEO_FILE, {})
    recheck = [pid for pid in files if pid in geo and int(pid) % ROTATION_DAYS == day]
    if recheck:
        g, _ = fetch_file_details(api, recheck, False)
        _merge_details(g, {})
        # A file can be overwritten with a new version (new size, new camera data). The same thirtieth gets a cheap
        # look at its current size; files whose size changed have their camera data and size looked up again.
        exif = load_json(EXIF_FILE, {})
        sizes = current_sizes(api, recheck)
        changed = [pid for pid, size in sizes.items()
                   if isinstance(exif.get(pid), list) and len(exif[pid]) >= 6 and size is not None and size != exif[pid][5]]
        if changed:
            g, e = fetch_file_details(api, changed, True)
            _merge_details(g, e)
        print(f"Locations re-checked for {len(recheck):,} file(s); {len(changed):,} with a new version looked up again.")
    return len(new), len(recheck)


def current_sizes(api, pageids):
    """{page id: size in bytes of the current version}, 50 files per request."""
    out = {}
    for i in range(0, len(pageids), 50):
        for data in api.query_all(prop="imageinfo", pageids="|".join(pageids[i:i + 50]), iiprop="size"):
            for page in data["query"]["pages"]:
                info = page.get("imageinfo")
                out[str(page.get("pageid", ""))] = info[0].get("size") if info else None
    return out




def device_of(make, model):
    """(device name, brand, kind) from EXIF make and model, e.g. ("NIKON CORPORATION", "NIKON D3500")
    -> ("Nikon D3500", "Nikon", "camera"). Kinds: camera, phone, drone, action camera, scanner, other."""
    make, model = (make or "").strip(), (model or "").strip()
    text = f"{make} {model}".lower()
    brand = next((b for key, b in BRANDS if re.search(rf"(?<![a-z]){re.escape(key)}(?![a-z])", text)), None)
    if not brand:
        brand = make.split()[0].title() if make else (model.split()[0] if model else "Unknown")
    name = model or make or "Unknown"
    # "NIKON D3500" -> "Nikon D3500"; "SM-G998B" -> "Samsung SM-G998B"
    if name.lower().startswith(brand.lower()):  # only the brand's own spelling; "Redmi ..." stays "Redmi ..."
        name = brand + name[len(brand):]
    if not name.lower().startswith(brand.lower()):
        name = f"{brand} {name}"
    m = model.lower()
    if brand == "DJI":
        kind = "drone"
    elif brand in ("GoPro", "Insta360"):
        kind = "action camera"
    elif brand in SCANNER_BRANDS:
        kind = "scanner"
    elif brand == "Sony":
        kind = "phone" if re.match(r"(xperia|so-|sov|xq-|[a-z]\d{4})", m) else "camera"
    elif brand in PHONE_BRANDS:
        kind = "phone"
    elif brand in CAMERA_BRANDS:
        kind = "camera"
    else:
        kind = "other"
    return re.sub(r"\s+", " ", name), brand, kind


def places_and_devices(snapshot, uploads):
    """Dashboard summary of locations and devices, and the map points file."""
    geo, exif = load_json(GEO_FILE), load_json(EXIF_FILE)
    if not geo and not exif:
        return None
    files = snapshot["files"]
    geo, exif = geo or {}, exif or {}
    points, gps_only, checked = [], 0, 0
    for pid, f in files.items():
        if pid in exif:
            checked += 1
        c = geo.get(pid)
        if c:
            points.append([c[0], c[1], f["title"][5:]])
        elif (exif.get(pid) or [None, None, None])[2] is not None:
            gps_only += 1
    # The map's points go in their own file, loaded only when the map is opened.
    MAP_POINTS_FILE.write_text("window.MAP_POINTS = " + json.dumps(points, ensure_ascii=False, separators=(",", ":"))
                               .replace("</", "<\\/") + ";\n", encoding="utf-8")

    devices, people = {}, {}
    no_info = 0
    for pid in files:
        e = exif.get(pid)
        if pid in exif and not (e and (e[0] or e[1])):
            no_info += 1
            continue
        if not e:
            continue
        name, brand, kind = device_of(e[0], e[1])
        d = devices.setdefault(name, [name, brand, kind, 0, set()])
        d[3] += 1
        user = upload_info(uploads, pid)[1]
        if user:
            d[4].add(user)
    models = sorted(([n, b, k, c, len(u)] for n, b, k, c, u in devices.values()), key=lambda r: -r[3])
    return {"files": len(files), "checked": checked, "geocoded": len(points), "gps_only": gps_only,
            "no_device_info": no_info, "models": models,
            "gap": upload_gap_summary(files, exif, uploads), "size": size_summary(files, exif, uploads)}


def upload_gap_summary(files, exif, uploads):
    """How long after a photo was taken it was uploaded, in days: the upload date (UTC) minus the date the
    camera recorded. Camera clocks are usually local time, so a gap of a day either way counts as the same
    day; a photo "taken" more than a day after its upload has a wrong camera clock and is counted apart."""
    gaps, wrong, oldest, by_year, by_user, longest = [], 0, None, {}, {}, []
    for pid, f in files.items():
        e = exif.get(pid)
        ts, user = upload_info(uploads, pid)
        if not (isinstance(e, list) and len(e) >= 6 and e[4] and ts):
            continue
        taken = datetime.fromisoformat(e[4]).date()
        uploaded = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(UTC).date()
        gap = (uploaded - taken).days
        # Before 2005 means a camera whose clock was never set (it starts at a factory date like 2000-01-01):
        # e.g. a 2013 Nikon D7100 reporting 2000, or "bookfair 2023" photos dated 2002.
        if gap < WRONG_CLOCK_EARLIEST_GAP_DAYS or taken.year < WRONG_CLOCK_BEFORE_YEAR:
            wrong += 1
            continue
        gap = max(gap, 0)
        gaps.append(gap)
        by_year.setdefault(uploaded.year, []).append(gap)
        if user:
            by_user.setdefault(user, []).append(gap)
        if oldest is None or e[4] < oldest[1]:
            oldest = [f["title"], e[4], ts, gap]
        if gap > 365:
            longest.append([f["title"], e[4], ts, gap])
    if not gaps:
        return None

    def median(v):
        v = sorted(v)
        return v[len(v) // 2] if len(v) % 2 else (v[len(v) // 2 - 1] + v[len(v) // 2]) / 2

    groups = [("Same day", 0, 0), ("1–7 days", 1, 7), ("8–30 days", 8, 30), ("1–12 months", 31, 365),
              ("1–2 years", 366, 730), ("2–5 years", 731, 1826), ("More than 5 years", 1827, 10 ** 6)]
    longest.sort(key=lambda r: -r[3])
    return {"files": len(gaps), "wrong_clock": wrong, "median": median(gaps),
            "groups": [[label, sum(1 for g in gaps if lo <= g <= hi)] for label, lo, hi in groups],
            "by_year": [[y, median(v), len(v)] for y, v in sorted(by_year.items())],
            "by_user": {u: [median(v), len(v)] for u, v in by_user.items()},
            "longest": longest[:30], "oldest": oldest}


def size_summary(files, exif, uploads):
    """Total size of the collection (the current version of each file), by kind of file, the largest
    files, and the running total by upload month (UTC)."""
    kinds = load_json(MEDIATYPES_FILE, {})
    total, by_kind, sizes, by_month = 0, {}, [], {}
    for pid, f in files.items():
        e = exif.get(pid)
        size = e[5] if isinstance(e, list) and len(e) >= 6 else None
        if not size:
            continue
        kind = "image" if extension(f["title"]) in IMAGE_EXTS else MEDIA_KINDS.get(kinds.get(pid), "other")
        total += size
        k = by_kind.setdefault(kind, [0, 0])
        k[0] += 1
        k[1] += size
        sizes.append((size, f["title"]))
        ts = upload_info(uploads, pid)[0]
        if ts:
            m = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(UTC).strftime("%Y-%m")
            by_month[m] = by_month.get(m, 0) + size
    if not sizes:
        return None
    sizes.sort(reverse=True)
    return {"total": total, "files": len(sizes), "by_kind": by_kind,
            "median": sorted(s for s, _ in sizes)[len(sizes) // 2],
            "largest": [[t, s] for s, t in sizes[:20]], "by_month": sorted(by_month.items())}


# ----------------------------------------------------------------- assessments

# The assessment categories and main-page rules are in methodology.py (ASSESS_CATS, DAY_TEMPLATE, ...).


def list_assessed(api, kind, since=None, progress=None):
    """(page id, time added to the category) for files in an assessment category; with `since`,
    only those added after it."""
    params = dict(list="categorymembers", cmtitle=ASSESS_CATS[kind], cmtype="file", cmprop="ids|timestamp",
                  cmlimit="max", cmsort="timestamp", cmdir="newer")
    if since:
        params["cmstart"] = since
    for data in api.query_all(**params):
        if progress:
            progress()
        for m in data["query"]["categorymembers"]:
            yield str(m["pageid"]), m["timestamp"]


def check_assessments(api, files, full, crawl_taken_at, progress=None):
    """Which project files are quality images, featured pictures and valued images.

    Full check (only the first time, or on request): reads the three Commons-wide categories completely
    (about 1,100 requests) and keeps the project's files. Every later check is cheap (a few dozen
    requests): promotions can only happen by a file being added to a category, so it reads what was
    added since the last check; demotions and delistings can only happen to files already assessed, so
    it re-checks just those files; and files new to the project are checked directly, since they may
    have been promoted long ago. Logs promotions and removals for files present in both checks."""
    prev = load_json(ASSESS_FILE)
    checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if full or not prev:
        print("Checking quality, featured and valued images and featured media (full) ...")
        status = {k: {} for k in ASSESS_CATS}
        for kind in ASSESS_CATS:
            for pid, ts in list_assessed(api, kind, progress=progress):
                if pid in files:
                    status[kind][pid] = ts
    else:
        status = {k: {pid: ts for pid, ts in prev.get(k, {}).items() if pid in files} for k in ASSESS_CATS}
        since = (datetime.fromisoformat(prev["checked_at"]) - QUICK_CHECK_OVERLAP).strftime("%Y-%m-%dT%H:%M:%SZ")
        for kind in ASSESS_CATS:
            # A kind added after the first check (e.g. featured media) is read in full once.
            for pid, ts in list_assessed(api, kind, since=None if kind not in prev else since, progress=progress):
                if pid in files:
                    status[kind][pid] = ts
        # Ask directly about files already assessed (to catch demotions and delistings) and files new to
        # the project (which may have been promoted long ago). Their status is replaced by the answer.
        before = set(prev["checked"])
        assessed = set().union(*[set(v) for v in status.values()])
        recheck = sorted(assessed | {pid for pid in files if pid not in before})
        for kind in ASSESS_CATS:
            for pid in recheck:
                status[kind].pop(pid, None)
        by_title = {v: k for k, v in ASSESS_CATS.items()}
        for i in range(0, len(recheck), 50):
            for data in api.query_all(prop="categories", pageids="|".join(recheck[i:i + 50]), clprop="timestamp",
                                      clcategories="|".join(ASSESS_CATS.values()), cllimit="max"):
                if progress:
                    progress()
                for page in data["query"]["pages"]:
                    for c in page.get("categories", ()):
                        status[by_title[c["title"]]][str(page["pageid"])] = c.get("timestamp")

    # Picture / Media of the Day on Commons. Only featured pictures become Picture of the Day, and Media
    # of the Day is for video and sound, so only those files are asked about (a few hundred at most).
    candidates = sorted(set(status["fp"]) | {pid for pid, f in files.items()
                                             if extension(f["title"]) not in IMAGE_EXTS or extension(f["title"]) == "gif"})
    for kind in MAIN_PAGE_KINDS:
        status[kind] = {}
    for i in range(0, len(candidates), 50):
        for data in api.query_all(prop="fileusage", pageids="|".join(candidates[i:i + 50]), funamespace="10",
                                  fulimit="max", fuprop="title"):
            if progress:
                progress()
            for page in data["query"]["pages"]:
                for u in page.get("fileusage", ()):
                    m = DAY_TEMPLATE.match(u["title"])
                    if m:
                        days = status[m.group(1).lower()].setdefault(str(page["pageid"]), [])
                        if m.group(2) not in days:
                            days.append(m.group(2))
    for kind in MAIN_PAGE_KINDS:
        for days in status[kind].values():
            days.sort()

    promoted, removed = [], []
    kinds = list(ASSESS_CATS) + list(MAIN_PAGE_KINDS)
    if prev:
        before = set(prev["checked"])
        for kind in kinds:
            if kind not in prev:
                continue  # first time this kind is tracked: nothing to compare with
            old, now = set(prev[kind]), set(status[kind])
            promoted += [[kind, pid, files[pid]["title"]] for pid in sorted(now - old) if pid in before]
            removed += [[kind, pid, files[pid]["title"]] for pid in sorted(old - now) if pid in files]
    save_json(ASSESS_FILE, {"checked_at": checked_at, "full": bool(full or not prev), "checked": list(files),
                            **status}, compact=True)
    history = load_json(ASSESS_HISTORY, [])
    history.append({"checked_at": checked_at, "taken_at": crawl_taken_at, "full": bool(full or not prev),
                    **{k: len(v) for k, v in status.items()},
                    "promoted": promoted[:500] if prev else None, "removed": removed[:500] if prev else None,
                    "promoted_n": len(promoted) if prev else None, "removed_n": len(removed) if prev else None})
    save_json(ASSESS_HISTORY, history)
    print("Assessments: " + ", ".join(f"{ASSESS_NAMES[k]}: {len(status[k]):,}" for k in kinds)
          + (f"; {len(promoted)} promoted, {len(removed)} removed since last check" if prev else " (first check)"))


def assessment_summary(snapshot, uploads):
    """Totals, promotions per month (UTC), per-contributor counts and recent changes."""
    a = load_json(ASSESS_FILE)
    if not a:
        return None
    files = snapshot["files"]
    kinds = [k for k in list(ASSESS_CATS) + list(MAIN_PAGE_KINDS) if k in a]
    status = {k: {pid: v for pid, v in a[k].items() if pid in files} for k in kinds}
    monthly = {}
    for kind in ASSESS_CATS:
        items, per = status.get(kind, {}), {}
        for ts in items.values():
            if ts:
                m = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(UTC).strftime("%Y-%m")
                per[m] = per.get(m, 0) + 1
        monthly[kind] = sorted(per.items())
    by_user = {}
    for kind, items in status.items():
        for pid in items:
            u = upload_info(uploads, pid)[1]
            if u:
                counts = by_user.setdefault(u, {})
                counts[kind] = counts.get(kind, 0) + 1
    history = load_json(ASSESS_HISTORY, [])
    any_cat = set().union(*[set(status.get(k, {})) for k in ASSESS_CATS])
    return {"checked_at": a["checked_at"], "total_files": len(files),
            "counts": {k: len(v) for k, v in status.items()},
            "any": len(any_cat), "monthly": monthly, "by_user": by_user,
            # [file title, [days]] for Picture / Media of the Day on Commons, newest first
            "days": {k: sorted([[files[pid]["title"], d] for pid, d in status.get(k, {}).items()],
                               key=lambda r: r[1][-1], reverse=True) for k in MAIN_PAGE_KINDS},
            "checks": [h for h in history[-30:] if h.get("promoted") is not None],
            "history": [{k: h.get(k) for k in ("checked_at", "taken_at", "full", *ASSESS_CATS, *MAIN_PAGE_KINDS)}
                        for h in history]}




def page_date(title):
    """The day ("YYYY-MM-DD") or month ("YYYY-MM") a main-page page is for, read from its name; "" if none.
    Handles any script's digits (Bengali ৩ = 3) and forms like 2025-08-21, 2026/07/24, 2025年8月21日,
    2023년 6월, "১৭ সেপ্টেম্বর ২০২৩" and "17 September 2023"."""
    text = "".join(str(unicodedata.digit(ch)) if ch.isdigit() else ch for ch in title)
    numbers = [int(n) for n in re.findall(r"\d+", text)]
    years = [n for n in numbers if 1990 <= n <= 2100]
    if not years:
        return ""
    year = years[0]
    rest = [n for n in numbers if n != year]
    month = next((m for w, m in MONTH_NAMES.items() if re.search(rf"(?<![^\W\d_]){re.escape(w)}(?![^\W\d_])", text, re.I)), None)
    if month:
        day = next((n for n in rest if 1 <= n <= 31), None)
    elif rest and 1 <= rest[0] <= 12:
        month, day = rest[0], (rest[1] if len(rest) > 1 and 1 <= rest[1] <= 31 else None)
    else:
        return ""
    return f"{year}-{month:02d}-{day:02d}" if day else f"{year}-{month:02d}"


def main_page_elsewhere(usage, files):
    """Files shown as picture or media of the day on other wikis' main pages, from the usage data
    (no extra requests): [[file title, wiki, kind, page title, date], ...], date as read by page_date().
    Talk, user, candidate and proposal pages are skipped."""
    out = []
    for pid, us in (usage or {}).get("files", {}).items():
        if pid not in files:
            continue
        for wiki, ns, title in us:
            if ns % 2 == 1 or ns == 2:
                continue
            name = title.replace("_", " ")
            if NOT_MAIN_PAGE.search(name):
                continue
            kind = "motd" if MOTD_PAGE.search(name) else "potd" if POTD_PAGE.search(name) else None
            if kind:
                out.append([files[pid]["title"], wiki, kind, name, page_date(name)])
    return out


def appearances(dates):
    """How many times a file was on one wiki's main page, from the dates of the pages showing it:
    each distinct day once, each month without a day page once, at least one."""
    days = {d for d in dates if len(d) == 10}
    months = {d for d in dates if len(d) == 7} - {d[:7] for d in days}
    return max(1, len(days) + len(months))


# ------------------------------------------------------------------ view counts

AQS_HOST = "wikimedia.org"


class ViewsAPI:
    """Wikimedia's media request statistics (how often each file is loaded). Its access policy asks for
    one request at a time, and allows an identified tool 200 a minute; this never goes faster than 180."""

    def __init__(self, user_agent):
        self.user_agent = user_agent
        self.conn = None
        self.requests = 0
        self.last = 0.0

    def items(self, path):
        offline_since, refusals = None, 0
        while True:
            wait = self.last + 1 / 3 - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self.last = time.monotonic()
            try:
                if self.conn is None:
                    self.conn = http.client.HTTPSConnection(AQS_HOST, timeout=60)
                self.conn.request("GET", path, headers={"User-Agent": self.user_agent, "Accept-Encoding": "gzip"})
                resp = self.conn.getresponse()
                body = resp.read()
            except NETWORK_ERRORS as e:
                if self.conn:
                    self.conn.close()
                    self.conn = None
                # The internet can drop for a while (Wi-Fi, the PC waking up): wait it out for up to an hour,
                # trying once a minute, before giving up. Network errors don't use up the attempts.
                offline_since = offline_since or time.monotonic()
                if time.monotonic() - offline_since > 3600:
                    raise
                print(f"  network error ({e}); trying again in a minute", file=sys.stderr)
                time.sleep(60)
                continue
            offline_since = None
            self.requests += 1
            REQUEST_COUNTS[AQS_HOST] = REQUEST_COUNTS.get(AQS_HOST, 0) + 1
            if resp.status == 404:
                return []  # no requests for this file in that period
            if resp.status in (429, 500, 502, 503, 504):
                refusals += 1  # busy or rate-limited: back off, and give up after 8 refusals in a row
                if refusals >= 8:
                    raise RuntimeError("The Analytics API kept refusing requests; try again later")
                wait = resp.getheader("Retry-After")
                time.sleep(int(wait) if wait and wait.isdigit() else 15 * refusals)
                continue
            if resp.status != 200:
                raise RuntimeError(f"Analytics API answered HTTP {resp.status} for {path}")
            if resp.getheader("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
            return json.loads(body).get("items", [])

    def file_requests(self, title, agent, granularity, start, end):
        """[(timestamp, requests)] for one file: agent "all-agents" (everything) or "user" (people)."""
        name = title[5:].replace(" ", "_")
        h = hashlib.md5(name.encode()).hexdigest()
        path = urllib.parse.quote(f"/wikipedia/commons/{h[0]}/{h[:2]}/{name}", safe="")
        return [(i["timestamp"], i["requests"]) for i in self.items(
            f"/api/rest_v1/metrics/mediarequests/per-file/all-referers/{agent}/{path}/{granularity}/{start}/{end}")]


def write_views_status(**status):
    """Progress of the views job for the open dashboard (like status.js for crawls)."""
    status["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    tmp = VIEWS_STATUS_FILE.with_name(VIEWS_STATUS_FILE.name + ".tmp")
    tmp.write_text("window.VIEWS_STATUS = " + json.dumps(status) + ";\n", encoding="utf-8")
    for _ in range(5):
        try:
            tmp.replace(VIEWS_STATUS_FILE)
            return
        except PermissionError:
            time.sleep(0.2)


def update_views(files, user_agent, progress=None, rebuild_every=500):
    """Bring view counts up to date. Files without a history get their monthly history since 2015, up to
    the end of last month (the one-time fill, also for new and renamed files). Then the files of today's
    thirtieth (by page id) get their daily counts since they were last updated, so every file is brought
    up to date once in 30 days and the project gets per-day totals. Two requests per file: all views and
    human views (all = human + automated). Saves as it goes, so it can stop and continue any time.

    views.json.gz: {"files": {page id: [title used, counted up to (YYYY-MM-DD), {month: [all, human]}]},
                    "daily": {day: [all, human, files counted that day]}}"""
    data = load_json(VIEWS_FILE, {"files": {}, "daily": {}})
    vf, daily = data["files"], data["daily"]
    v = ViewsAPI(user_agent)
    today = datetime.now(UTC).date()
    hist_end = today.replace(day=1) - timedelta(days=1)
    upto = today - timedelta(days=2)  # a day's counts are complete a day or two later
    history = [pid for pid in files if pid not in vf or vf[pid][0] != files[pid]["title"]]
    in_history = set(history)
    slice_ = int(time.time() // 86400) % ROTATION_DAYS
    rotation = [pid for pid in files if pid in vf and pid not in in_history and int(pid) % ROTATION_DAYS == slice_
                and vf[pid][1] < upto.isoformat()]
    total = len(history) + len(rotation)
    print(f"View counts: {len(history):,} file(s) need their history, {len(rotation):,} in today's daily update.")
    done, built, last = 0, None, [0.0]
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def status(running=True):
        write_views_status(running=running, started_at=started, done=done, total=total,
                           history=len(history), daily=len(rotation), built=built)

    def tick():
        nonlocal done, built
        done += 1
        if progress:
            progress(done, total)
        if done % 500 == 0:
            save_json(VIEWS_FILE, data, compact=True)
            # Rebuild with every save, so new counts show up on the dashboard as they come in.
            if rebuild_every and done % rebuild_every == 0:
                built = build_dashboard()
        if time.monotonic() - last[0] >= 5:
            last[0] = time.monotonic()
            status()

    status()
    try:
        _fetch_views(v, files, vf, daily, history, rotation, hist_end, upto, tick)
    except BaseException:
        # Each file's figures are added only once both its requests have succeeded, so what's in memory is
        # consistent: keep it, so the next run continues from here instead of from the last 500-file save.
        save_json(VIEWS_FILE, data, compact=True)
        status(running=False)
        raise

    save_json(VIEWS_FILE, data, compact=True)
    built = build_dashboard() if rebuild_every else built
    status(running=False)
    print(f"View counts updated: {len(history):,} histories, {len(rotation):,} daily updates, {v.requests:,} requests.")
    return len(history), len(rotation)


def _fetch_views(v, files, vf, daily, history, rotation, hist_end, upto, tick):
    for pid in history:
        title = files[pid]["title"]
        old = vf.get(pid)
        months = old[2] if old else {}  # a renamed file keeps the months counted under its old name
        for k, agent in ((0, "all-agents"), (1, "user")):
            for ts, n in v.file_requests(title, agent, "monthly", VIEWS_FROM, hist_end.strftime("%Y%m%d")):
                m = f"{ts[:4]}-{ts[4:6]}"
                if not old or m > old[1][:7]:
                    months.setdefault(m, [0, 0])[k] += n
        vf[pid] = [title, max(hist_end.isoformat(), old[1]) if old else hist_end.isoformat(), months]
        tick()

    for pid in rotation:
        title, until, months = vf[pid]
        start = date.fromisoformat(until) + timedelta(days=1)
        per_day = {}
        for k, agent in ((0, "all-agents"), (1, "user")):
            for ts, n in v.file_requests(title, agent, "daily", start.strftime("%Y%m%d"), upto.strftime("%Y%m%d")):
                per_day.setdefault(f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}", [0, 0])[k] += n
        d = start
        while d <= upto:
            key = d.isoformat()
            row = daily.setdefault(key, [0, 0, 0])
            row[2] += 1
            a, h = per_day.get(key, (0, 0))
            row[0] += a
            row[1] += h
            mm = months.setdefault(key[:7], [0, 0])
            mm[0] += a
            mm[1] += h
            d += timedelta(days=1)
        vf[pid][1] = upto.isoformat()
        tick()


def category_subtrees(snapshot):
    """{category id: set of file ids anywhere below it} (a file in several subcategories counts once)."""
    cats, files = snapshot["categories"], snapshot["files"]
    children, by_cat = {}, {}
    for cid, c in cats.items():
        for par in c["parents"]:
            children.setdefault(par, []).append(cid)
    for fid, f in files.items():
        for c in f["categories"]:
            by_cat.setdefault(c, []).append(fid)

    def below(cid):
        seen, found, stack = set(), set(), [cid]
        while stack:
            x = stack.pop()
            if x not in seen:
                seen.add(x)
                found.update(by_cat.get(x, ()))
                stack.extend(children.get(x, ()))
        return found

    return {cid: below(cid) for cid in cats}


def views_cutoff(counted_through):
    """The last month ("YYYY-MM") whose every day has been counted for all files, from each file's
    counted-through date ("YYYY-MM-DD"); None if no file has been counted through a whole month."""
    if not counted_through:
        return None
    first = date.fromisoformat(min(counted_through))
    month_end = (first.replace(day=1) + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    if first == month_end:
        return f"{first:%Y-%m}"
    prev = first.replace(day=1) - timedelta(days=1)
    return f"{prev:%Y-%m}" if prev.isoformat() >= VIEWS_FROM[:4] + "-" + VIEWS_FROM[4:6] + "-01" else None


def views_summary(snapshot, uploads):
    """All-time and monthly totals (all and human), daily totals, and the most viewed files, categories
    and contributors, for the files now in the project."""
    data = load_json(VIEWS_FILE)
    if not data or not data.get("files"):
        return None
    files, vf = snapshot["files"], data["files"]
    have = [pid for pid in files if pid in vf]
    # Totals run to one common cutoff: the last complete month that every counted file has been counted through
    # (the daily updates reach different files on different days). Months after it are partial and are shown
    # apart, never added to the totals (methodology: VIEWS_TOTALS).
    cutoff = views_cutoff([vf[pid][1] for pid in have])
    in_total = (lambda m: m <= cutoff) if cutoff else (lambda m: False)
    per_file = {pid: [sum(v[0] for m, v in vf[pid][2].items() if in_total(m)),
                      sum(v[1] for m, v in vf[pid][2].items() if in_total(m))] for pid in have}
    after = [sum(v[0] for pid in have for m, v in vf[pid][2].items() if not in_total(m)),
             sum(v[1] for pid in have for m, v in vf[pid][2].items() if not in_total(m))]
    months = {}
    for pid in have:
        for m, (a, h) in vf[pid][2].items():
            row = months.setdefault(m, [0, 0])
            row[0] += a
            row[1] += h
    # A month is complete for a file once it has been counted to the month's end.
    ends = sorted(vf[pid][1] for pid in have)

    def counted_through(day):
        lo, hi = 0, len(ends)
        while lo < hi:
            mid = (lo + hi) // 2
            if ends[mid] < day:
                lo = mid + 1
            else:
                hi = mid
        return len(ends) - lo

    month_rows = []
    for m in sorted(months):
        y, mo = int(m[:4]), int(m[5:])
        last = (date(y + (mo == 12), mo % 12 + 1, 1) - timedelta(days=1)).isoformat()
        month_rows.append([m, months[m][0], months[m][1], counted_through(last)])
    by_user = {}
    for pid in have:
        u = upload_info(uploads, pid)[1]
        if u:
            b = by_user.setdefault(u, [0, 0])
            b[0] += per_file[pid][0]
            b[1] += per_file[pid][1]
    cats = snapshot["categories"]
    cat_rows = []
    for cid, fset in category_subtrees(snapshot).items():
        a = sum(per_file[p][0] for p in fset if p in per_file)
        if a:
            cat_rows.append([cats[cid]["title"], a, sum(per_file[p][1] for p in fset if p in per_file), len(fset)])
    top = sorted(have, key=lambda p: -per_file[p][0])[:100]
    return {"files": len(files), "with_history": len(have), "checked_through": ends[0] if ends else None,
            "totals_from": VIEWS_FROM[:4] + "-" + VIEWS_FROM[4:6], "cutoff_month": cutoff, "after_cutoff": after,
            "running": not _lock_free(VIEWS_LOCK),
            "all": sum(x[0] for x in per_file.values()), "human": sum(x[1] for x in per_file.values()),
            "months": month_rows, "daily": sorted([d, r[0], r[1], r[2]] for d, r in data["daily"].items()),
            "top_files": [[files[p]["title"], per_file[p][0], per_file[p][1], upload_info(uploads, p)[1]] for p in top],
            "categories": sorted(cat_rows, key=lambda r: -r[1])[:100], "by_user": by_user}


def _lock_free(path):
    lock = try_lock(path)
    if lock is None:
        return False
    lock.close()
    return True


# ---------------------------------------------------------------- contributors


def contributor_summary(snapshot, uploads, usage):
    """Who uploaded the files now in the project, from the cached uploader and upload time of each
    file: no extra requests. Months are in UTC. Categories per contributor leave out the catch-all
    categories (any category directly holding more than a quarter of all files, e.g. "All images"),
    which would look the same for everyone."""
    files, cats = snapshot["files"], snapshot["categories"]
    direct = {}
    for f in files.values():
        for c in f["categories"]:
            direct[c] = direct.get(c, 0) + 1
    catch_all = {c for c, n in direct.items() if n > CATCH_ALL_SHARE * len(files)}

    used = usage["files"] if usage else {}
    people, unknown = {}, 0
    for pid, f in files.items():
        ts, user = upload_info(uploads, pid)
        if not ts or not user:
            unknown += 1
            continue
        when = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(UTC)
        p = people.setdefault(user, {"files": 0, "first": ts, "last": ts, "months": {}, "cats": {},
                                     "used": 0, "uses": 0})
        p["files"] += 1
        p["first"] = min(p["first"], ts)
        p["last"] = max(p["last"], ts)
        month = when.strftime("%Y-%m")
        p["months"][month] = p["months"].get(month, 0) + 1
        for c in f["categories"]:
            if c not in catch_all:
                p["cats"][c] = p["cats"].get(c, 0) + 1
        if pid in used:
            p["used"] += 1
            p["uses"] += len(used[pid])

    users = []
    for name, p in people.items():
        top_cats = sorted(p["cats"].items(), key=lambda kv: -kv[1])[:40]
        users.append([name, p["files"], p["first"], p["last"], p["used"], p["uses"], p["months"],
                      [[cats[c]["title"], n] for c, n in top_cats], len(p["cats"])])
    users.sort(key=lambda u: -u[1])
    return {"users": users, "unknown": unknown, "total_files": len(files),
            "catch_all": sorted(cats[c]["title"] for c in catch_all), "usage_checked": bool(usage)}


# -------------------------------------------------------------------------- diff


def diff_snapshots(prev, cur):
    pc, cc = prev["categories"], cur["categories"]
    pf, cf = prev["files"], cur["files"]

    def cat_title(cid):
        return (cc.get(cid) or pc.get(cid) or {}).get("title", f"(page id {cid})")

    def titles(ids):
        return sorted(cat_title(i) for i in ids)

    out = {
        "files_removed": [],
        "files_added": [],
        "files_moved": [],
        "files_renamed": [],
        "categories_removed": [],
        "categories_added": [],
        "categories_moved": [],
        "categories_renamed": [],
    }

    for pid in pf.keys() - cf.keys():
        out["files_removed"].append({"pageid": pid, "title": pf[pid]["title"], "was_in": titles(pf[pid]["categories"])})
    for pid in cf.keys() - pf.keys():
        out["files_added"].append({"pageid": pid, "title": cf[pid]["title"], "now_in": titles(cf[pid]["categories"])})
    for pid in cf.keys() & pf.keys():
        old, new = pf[pid], cf[pid]
        if old["title"] != new["title"]:
            out["files_renamed"].append({"pageid": pid, "old_title": old["title"], "title": new["title"]})
        o, n = set(old["categories"]), set(new["categories"])
        if o != n:
            out["files_moved"].append({
                "pageid": pid,
                "title": new["title"],
                "removed_from": titles(o - n),
                "added_to": titles(n - o),
                "now_in": titles(n),
            })

    for cid in pc.keys() - cc.keys():
        out["categories_removed"].append({
            "pageid": cid,
            "title": pc[cid]["title"],
            "was_under": titles(pc[cid]["parents"]),
            "files_lost": sum(1 for f in pf.values() if cid in f["categories"]),
        })
    for cid in cc.keys() - pc.keys():
        out["categories_added"].append({
            "pageid": cid,
            "title": cc[cid]["title"],
            "now_under": titles(cc[cid]["parents"]),
        })
    for cid in cc.keys() & pc.keys():
        old, new = pc[cid], cc[cid]
        if old["title"] != new["title"]:
            out["categories_renamed"].append({"pageid": cid, "old_title": old["title"], "title": new["title"]})
        o, n = set(old["parents"]), set(new["parents"])
        if o != n:
            out["categories_moved"].append({
                "pageid": cid,
                "title": new["title"],
                "removed_from": titles(o - n),
                "added_to": titles(n - o),
                "now_under": titles(n),
            })

    for key, items in out.items():
        items.sort(key=lambda x: x["title"])
    return out


def attribute_crawl(user_agent, changes, start, end):
    """Find who made each change of a crawl (attribution.py). Never stops the crawl."""
    import attribution
    finder = attribution.Finder(user_agent)
    try:
        print("Finding who made the changes ...")
        finder.crawl_changes(changes, start, end)
        print(f"  done ({finder.requests:,} requests)")
    except Exception as e:  # noqa: BLE001
        print(f"WARNING: could not find who made the changes ({e}); "
              "run 'python tracker.py --attribute' later to fill them in.", file=sys.stderr)
    finally:
        finder.close()


def attribute_backfill(config):
    """Fill in who made the changes for saved crawl reports and usage-log rows that don't have it yet
    (those from before this existed, or whose lookup failed). No crawl."""
    for path in sorted(REPORT_DIR.glob("*.json")):
        rep = load_json(path)
        ch = rep["changes"]
        todo = [x for items in ch.values() for x in items if "by" not in x and "by_category" not in x]
        if todo:
            print(f"Crawl of {fmt_time(rep['taken_at'])}: {len(todo)} change(s)")
            attribute_crawl(config["user_agent"], ch, datetime.fromisoformat(rep["compared_with"]),
                            datetime.fromisoformat(rep["taken_at"]) + ATTRIBUTION_OVERLAP)
            save_json(path, rep)
    _, rows = read_usage_log()
    history = load_json(USAGE_HISTORY, [])
    times = [h["checked_at"] for h in history]
    by_stamp = {csv_time(t): i for i, t in enumerate(times)}
    groups = {}
    for r in rows:
        if not r[USAGE_LOG_COLUMNS.index("how")]:
            groups.setdefault(r[0], []).append(r)
    for stamp, grp in groups.items():
        i = by_stamp.get(stamp)
        if not i:
            print(f"  usage check {stamp}: not in usage_history.json, skipped")
            continue
        print(f"Usage check of {stamp}:")
        try:
            attribute_usage(config["user_agent"], grp, datetime.fromisoformat(times[i - 1]) - ATTRIBUTION_OVERLAP,
                            datetime.fromisoformat(times[i]) + ATTRIBUTION_OVERLAP)
        except Exception as e:  # noqa: BLE001
            print(f"WARNING: {e}", file=sys.stderr)
    write_usage_log(rows)
    build_dashboard()


def enrich_removed(api, changes):
    """Find out where removed files/categories went: deleted, or moved outside
    the tree (and if so, which categories they are in now)."""
    targets = changes["files_removed"] + changes["categories_removed"]
    by_id = {t["pageid"]: t for t in targets}
    ids = list(by_id)
    for i in range(0, len(ids), 50):
        batch = ids[i:i + 50]
        found = {}
        for data in api.query_all(pageids="|".join(batch), prop="categories", cllimit="max", clprop="hidden"):
            for p in data["query"]["pages"]:
                entry = found.setdefault(str(p["pageid"]), {"missing": p.get("missing", False), "title": p.get("title"), "cats": []})
                for c in p.get("categories", []):
                    entry["cats"].append(c["title"])
        for pid in batch:
            t = by_id[pid]
            info = found.get(pid)
            if info is None or info["missing"]:
                t["status"] = "deleted"
                t["current_categories"] = []
            else:
                t["status"] = "moved_out"
                t["current_title"] = info["title"]
                t["current_categories"] = sorted(info["cats"])

    # Deletion details (who/why) for deleted items; capped to stay polite.
    for t in [t for t in targets if t["status"] == "deleted"][:100]:
        data = api.get(action="query", list="logevents", letype="delete", letitle=t["title"], lelimit="1",
                       leprop="user|timestamp|comment")
        ev = data["query"]["logevents"]
        if ev:
            t["deletion"] = {k: ev[0].get(k) for k in ("user", "timestamp", "comment")}


# --------------------------------------------------------------------- storage


def latest_snapshot():
    snaps = sorted(SNAPSHOT_DIR.glob("*.json.gz"))
    return snaps[-1] if snaps else None


def _open(path, mode):
    path = Path(path)
    if path.name.endswith(".gz"):
        return gzip.open(path, mode + "t", encoding="utf-8")
    return path.open(mode, encoding="utf-8")


def load_json(path, default=None):
    try:
        with _open(path, "r") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default


def save_json(path, data, compact=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("wb") as raw:
        # No time stamp or file name in the gzip header, so the same data always gives the same bytes and the
        # archive (archive.py) stores an unchanged file only once.
        binary = gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) if path.name.endswith(".gz") else None
        with io.TextIOWrapper(binary or raw, encoding="utf-8") as fh:
            if compact:
                json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
            else:
                json.dump(data, fh, ensure_ascii=False, indent=1)
    for attempt in range(10):
        try:
            tmp.replace(path)
            return
        except PermissionError:
            # On Windows the target can't be replaced while another process (e.g. the archive) is reading it.
            if attempt == 9:
                raise
            time.sleep(0.5)


# ------------------------------------------------------------------- dashboard


def write_status(**status):
    """Write status.js. The dashboard loads it with a <script> tag (which works
    when the page is opened straight from disk, unlike fetch) and polls it."""
    status["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    text = "window.TRACKER_STATUS = " + json.dumps(status, ensure_ascii=False).replace("</", "<\\/") + ";\n"
    tmp = STATUS_FILE.with_name(STATUS_FILE.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    for _ in range(5):
        try:
            tmp.replace(STATUS_FILE)
            return
        except PermissionError:  # browser reading the file at that instant
            time.sleep(0.2)


def tree_summary(snapshot):
    """Category tree with file counts, small enough to embed in the dashboard
    (the full snapshot lists every file and is too big for a web page)."""
    cats, files = snapshot["categories"], snapshot["files"]
    children, by_cat = {}, {}
    for cid, c in cats.items():
        for p in c["parents"]:
            children.setdefault(p, []).append(cid)
    for fid, f in files.items():
        for c in f["categories"]:
            by_cat.setdefault(c, []).append(fid)

    def total(cid):
        # Unique files anywhere below this category; a file in several subcategories counts once.
        seen, found, stack = set(), set(), [cid]
        while stack:
            x = stack.pop()
            if x in seen:
                continue
            seen.add(x)
            found.update(by_cat.get(x, ()))
            stack.extend(children.get(x, ()))
        return len(found)

    tree = {cid: {"title": c["title"], "depth": c["depth"], "parents": c["parents"],
                  "direct": len(by_cat.get(cid, ())), "total": total(cid)} for cid, c in cats.items()}
    return {k: snapshot[k] for k in ("root", "taken_at", "total_files", "total_categories")} | {"categories": tree}


def write_removed_files_csv(write=True):
    """All files that have ever left the project tree, from every saved report (oldest crawl first),
    as a spreadsheet with links. Returns (columns, rows, crawl time of each row); the dashboard uses
    these to download any date range."""
    columns = ["found_in_crawl", "compared_with", "status", "file", "file_url", "was_in_categories",
               "now_in_categories", "deleted_by", "deleted_at", "deletion_reason", "deletion_log_url", "title_now"] + ATTRIB_COLUMNS
    rows, found = [], []
    for path in sorted(REPORT_DIR.glob("*.json")):
        rep = load_json(path)
        for f in rep["changes"]["files_removed"]:
            found.append(rep["taken_at"])
            deleted = f.get("status") == "deleted"
            d = f.get("deletion") or {}
            rows.append([csv_time(rep["taken_at"]), csv_time(rep["compared_with"]), "deleted" if deleted else "moved outside",
                         f["title"], commons_url(f["title"]), "; ".join(f.get("was_in", [])),
                         "; ".join(f.get("current_categories", [])), d.get("user", ""),
                         csv_time(d["timestamp"]) if d.get("timestamp") else "", d.get("comment", ""),
                         "https://commons.wikimedia.org/w/index.php?title=Special:Log&page=" + urllib.parse.quote(f["title"]),
                         f.get("current_title", "") if not deleted else ""] + attrib_cells(f.get("by")))
    if write:
        # utf-8-sig so Excel shows Bengali and other scripts correctly.
        with REMOVED_CSV.open("w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(columns)
            w.writerows(rows)
    return columns, rows, found


def data_status_rows(full_snapshot, snap_path):
    """How current and complete each dataset is (provenance.data_status); never stops a build."""
    if not full_snapshot:
        return None
    try:
        import provenance
        return provenance.data_status(full_snapshot, snap_path.name)
    except Exception as e:  # noqa: BLE001
        print(f"WARNING: could not work out the data status: {e}", file=sys.stderr)
        return None


def build_info_safe():
    try:
        import version
        return version.build_info()
    except Exception:  # noqa: BLE001
        return None


def build_dashboard():
    payload = build_payload()
    blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    html = TEMPLATE_FILE.read_text(encoding="utf-8").replace("/*__DATA__*/null", blob)
    DASHBOARD_FILE.write_text(html, encoding="utf-8")
    print(f"Dashboard written: {DASHBOARD_FILE}")
    return payload["generated_at"]  # an open dashboard reloads when this changes


def build_payload(write=True):
    """Every figure the dashboard and the PDF reports show, computed from the saved data files. With `write`,
    also rewrites removed_files.csv (the dashboard build does; a report doesn't touch any file)."""
    history = load_json(HISTORY_FILE, [])
    snap_path = latest_snapshot()
    full_snapshot = load_json(snap_path) if snap_path else None
    snapshot = tree_summary(full_snapshot) if full_snapshot else None
    uploads = load_json(UPLOADS_FILE, {})
    growth = growth_series(full_snapshot["files"], uploads) if full_snapshot and uploads else None
    media = media_counts(full_snapshot["files"], load_json(MEDIATYPES_FILE, {})) if full_snapshot else None
    usage_raw = load_json(USAGE_FILE)
    usage = usage_summary(full_snapshot, usage_raw, uploads) if full_snapshot and usage_raw else None
    contributors = contributor_summary(full_snapshot, uploads, usage_raw) if full_snapshot and uploads else None
    assessments = assessment_summary(full_snapshot, uploads) if full_snapshot else None
    if assessments is not None:
        assessments["elsewhere"] = main_page_elsewhere(usage_raw, full_snapshot["files"])
    places = places_and_devices(full_snapshot, uploads) if full_snapshot else None
    reports = []
    for path in sorted(REPORT_DIR.glob("*.json"))[-REPORTS_IN_DASHBOARD:]:
        reports.append(load_json(path))
    removed_columns, removed_rows, removed_found = write_removed_files_csv(write)
    if write:
        try:  # the removed-files list is rebuilt here, outside any job: keep its metadata current
            import provenance
            provenance.refresh_if_stale([REMOVED_CSV] + list(provenance.data_files()), "dashboard build")
        except Exception as e:  # noqa: BLE001
            print(f"WARNING: could not update removed_files.csv metadata: {e}", file=sys.stderr)
    sizes = {p.name: p.stat().st_size for p in SNAPSHOT_DIR.glob("*.json.gz")}
    for h in history:
        h["size"] = sizes.get(h.get("snapshot"))  # None if that snapshot was deleted
    payload = {"history": history, "snapshot": snapshot, "reports": reports,
               "snapshot_file": snap_path.name if snap_path else None, "growth": growth, "usage": usage, "contributors": contributors, "media": media,
               "assessments": assessments, "languages": load_json(LANGS_FILE, {}), "places": places,
               "removed_files": {"columns": removed_columns, "rows": removed_rows, "found": removed_found},
               "views": views_summary(full_snapshot, uploads) if full_snapshot else None,
               "data_status": data_status_rows(full_snapshot, snap_path),
               "build": build_info_safe(),
               "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    return payload


# ------------------------------------------------------------------------ main


def expected_requests(history, prev, mode):
    """How many API requests this run will probably need, for the dashboard's time-left estimate:
    the average of the last few runs of the same kind, or else a guess from the previous tree."""
    same = [h["requests"] for h in history if h.get("mode", "full") == mode and h.get("requests")][-3:]
    if same:
        return round(sum(same) / len(same))
    if prev:
        if mode == "quick":
            return len(prev["categories"]) + 10
        # A full listing takes one request per 500 members.
        return sum(max(1, -(-len(m) // 500)) for m in previous_members(prev).values()) + 10
    return None


def try_lock(path=LOCK_FILE):
    """Only one crawl at a time (a button press, the daily and the weekly schedule could overlap).
    Returns an open handle holding the lock, or None if another crawl holds it. The OS releases
    the lock when the process ends, even if it crashes. The views job uses its own lock file."""
    fh = open(path, "a+")
    try:
        fh.seek(0)
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fh
    except OSError:
        fh.close()
        return None


def crawl_running():
    lock = try_lock()
    if lock is None:
        return True
    lock.close()  # closing releases it
    return False


def usage_phase(api, snapshot, base):
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    last = [0.0]

    def progress(done, total):
        if time.monotonic() - last[0] >= 2:
            last[0] = time.monotonic()
            write_status(**base, running=True, phase="usage", usage_started_at=started,
                         usage_done=done, usage_total=total)

    write_status(**base, running=True, phase="usage", usage_started_at=started, usage_done=0,
                 usage_total=len(snapshot["files"]))
    check_usage(api, snapshot, progress)


def run_usage_only(config, base):
    """The "Check on-wiki usage" button: no crawl, just the usage of the files in the latest snapshot."""
    snap_path = latest_snapshot()
    if not snap_path:
        raise SystemExit("No crawl yet: run a crawl first.")
    api = CommonsAPI(config["user_agent"])
    write_status(**base, running=True, phase="connecting")
    api.wait_for_network()
    usage_phase(api, load_json(snap_path), base)
    built = build_dashboard()
    write_status(**base, running=False, phase="done", finished_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 last_snapshot=snap_path.name, dashboard_built=built)


def run(config, force_full=False, usage=False, usage_only=False):
    lock = try_lock()
    if lock is None:
        print("Another crawl is already running; not starting a second one.")
        return "skipped"
    started = datetime.now(timezone.utc)
    base = {"started_at": started.isoformat(timespec="seconds")}
    try:
        if usage_only:
            base["mode"] = "usage"
            run_usage_only(config, base)
        else:
            _run(config, started, base, force_full, usage)
    except BaseException as e:
        write_status(**base, running=False, phase="failed",
                     error=f"{type(e).__name__}: {e}" if str(e) else type(e).__name__)
        raise
    finally:
        lock.close()


def _run(config, started, base, force_full, usage=False):
    api = CommonsAPI(config["user_agent"])
    root = normalize_category(config["root_category"])
    stamp = started.strftime("%Y-%m-%dT%H%M%SZ")

    prev_path = latest_snapshot()
    prev = load_json(prev_path) if prev_path else None
    if prev and prev.get("root") != root:
        print(f"Previous snapshot is for {prev.get('root')}, not {root}; not comparing.")
        prev = None

    # A quick check unless a full crawl was asked for (the weekly schedule does that; it also picks
    # up file renames) or there's nothing to build on. Snapshots from before the quick check
    # existed have no per-category counts to compare against.
    history = load_json(HISTORY_FILE, [])
    no_counts = prev is not None and any("count" not in c for c in prev["categories"].values())
    full = force_full or prev is None or no_counts
    since = None
    if not full:
        # Small overlap so nothing added around the moment the last run started slips through.
        since = (datetime.fromisoformat(prev["taken_at"]) - QUICK_CHECK_OVERLAP).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"{fmt_time(started)}: {'full crawl' if full else 'quick check'} of {root} ...")
    base.update(mode="full" if full else "quick", requests_expected=expected_requests(history, prev, "full" if full else "quick"))
    write_status(**base, running=True, phase="connecting", categories=0, files=0)
    api.wait_for_network()
    base["crawl_started_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    requests_before = api.requests
    write_status(**base, running=True, phase="crawling", categories=0, files=0, current=root, requests_done=0)
    last = [0.0]

    def progress(n_cats, n_files, current):
        if time.monotonic() - last[0] >= 2:
            last[0] = time.monotonic()
            write_status(**base, running=True, phase="crawling", categories=n_cats, files=n_files, current=current,
                         requests_done=api.requests - requests_before)

    categories, files, stats = crawl(api, root, config.get("max_depth"), config.get("exclude_categories", []),
                                     config.get("max_categories", 5000), progress,
                                     prev=None if full else prev, since=since)
    crawl_finished = datetime.now(timezone.utc).isoformat(timespec="seconds")
    def dates_progress(done, total):
        if time.monotonic() - last[0] >= 2:
            last[0] = time.monotonic()
            write_status(**base, running=True, phase="dates", categories=len(categories), files=len(files),
                         dates_done=done, dates_total=total)

    update_upload_dates(api, list(files), dates_progress, refresh=True)
    media = media_counts(files, update_media_types(api, files))

    def details_progress(done, total):
        if time.monotonic() - last[0] >= 2:
            last[0] = time.monotonic()
            write_status(**base, running=True, phase="details", categories=len(categories), files=len(files),
                         requests_done=api.requests - requests_before, details_done=done, details_total=total)

    update_file_details(api, files, details_progress)

    def assess_progress():
        if time.monotonic() - last[0] >= 2:
            last[0] = time.monotonic()
            write_status(**base, running=True, phase="assessments", categories=len(categories), files=len(files),
                         requests_done=api.requests - requests_before)

    # Full read only when there is no earlier check to build on; every other check is the cheap kind.
    check_assessments(api, files, False, started.isoformat(timespec="seconds"), assess_progress)
    write_status(**base, running=True, phase="comparing", categories=len(categories), files=len(files))

    snapshot = {
        "schema": 1,
        "root": root,
        "taken_at": started.isoformat(timespec="seconds"),  # when the crawl started (the snapshot's name)
        # The tree was read between these two times (limitation L03: a change made meanwhile may or may not be in it).
        "crawl_started_at": base.get("crawl_started_at"),
        "crawl_finished_at": crawl_finished,
        "max_depth": config.get("max_depth"),
        "total_files": len(files),
        "total_categories": len(categories),
        "categories": categories,
        "files": files,
    }

    save_json(SNAPSHOT_DIR / f"{stamp}.json.gz", snapshot, compact=True)

    summary = {
        "taken_at": snapshot["taken_at"],
        "snapshot": f"{stamp}.json.gz",
        "total_files": len(files),
        "total_categories": len(categories),
        "compared_with": prev["taken_at"] if prev else None,
        "crawl_started_at": base.get("crawl_started_at"),
        "crawl_finished_at": crawl_finished,
        "mode": "full" if full else "quick",
        "media": media,
        "requests": api.requests - requests_before,  # same measure as requests_done in status.js
        "seconds": round((datetime.now(timezone.utc) - started).total_seconds()),
    }
    if prev:
        changes = diff_snapshots(prev, snapshot)
        if changes["files_removed"] or changes["categories_removed"]:
            print("Checking where removed items went ...")
            enrich_removed(api, changes)
        if any(changes.values()):
            attribute_crawl(config["user_agent"], changes, datetime.fromisoformat(prev["taken_at"]), datetime.now(timezone.utc))
        report = {"taken_at": snapshot["taken_at"], "compared_with": prev["taken_at"],
                  "previous_total_files": prev["total_files"], "total_files": len(files),
                  "previous_total_categories": prev["total_categories"],
                  "total_categories": len(categories), "changes": changes}
        save_json(REPORT_DIR / f"{stamp}.json", report)
        summary["counts"] = {k: len(v) for k, v in changes.items()}

    history.append(summary)
    save_json(HISTORY_FILE, history)

    print()
    print(f"Total unique files: {len(files)}")
    print(f"Categories in tree: {len(categories)}")
    if prev:
        c = summary["counts"]
        print(f"Since {fmt_time(prev['taken_at'])}: files {len(files) - prev['total_files']:+d}  "
              f"(removed {c['files_removed']}, added {c['files_added']}, moved {c['files_moved']}, "
              f"renamed {c['files_renamed']}); categories removed {c['categories_removed']}, "
              f"added {c['categories_added']}, moved {c['categories_moved']}")
    else:
        print("First run: saved as the baseline. Changes will be reported from the next run.")
    print(f"{'Full crawl' if full else 'Quick check'}: {stats['listed']} categories listed in full, "
          f"{stats['reused']} confirmed unchanged or additions-only")
    size = f"{api.bytes / 1e6:.1f} MB" if api.bytes >= 1e6 else f"{api.bytes / 1e3:.0f} KB"
    print(f"API requests: {api.requests} ({size} downloaded) in {summary['seconds'] // 60} min {summary['seconds'] % 60} s")
    if usage:
        usage_phase(api, snapshot, base)
    built = build_dashboard()
    write_status(**base, running=False, phase="done", finished_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 last_snapshot=f"{stamp}.json.gz", files=len(files), categories=len(categories), dashboard_built=built)


def keep_awake():
    """Ask Windows not to go to sleep while this job runs (a sleeping PC stalls crawls and the long
    view-count fill). The screen may still turn off, closing the lid or pressing the power button still
    sleeps the PC, and the request ends by itself when the process does."""
    if sys.platform == "win32":
        import ctypes
        ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)


def main():
    # Category names are often in Bengali and other scripts; don't crash on a cp1252 console.
    for stream in (sys.stdout, sys.stderr):
        if stream is not None:  # None under pythonw.exe
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dashboard", action="store_true", help="only rebuild dashboard.html from saved data")
    ap.add_argument("--config", default=str(BASE / "config.json"))
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--full", action="store_true", help="full crawl: re-read every category")
    mode.add_argument("--quick", action="store_true", help="quick check (the default)")
    mode.add_argument("--upload-dates", action="store_true",
                      help="only fetch missing upload dates and uploaders for the latest snapshot, then rebuild the dashboard")
    mode.add_argument("--views", action="store_true",
                      help="bring view counts up to date (history for files without one, then today's daily update)")
    mode.add_argument("--details-only", action="store_true",
                      help="only look up location and camera of files not seen before, then rebuild the dashboard")
    mode.add_argument("--assessments-only", action="store_true",
                      help="only a full check of quality, featured and valued images for the latest crawl")
    mode.add_argument("--migrate", action="store_true",
                      help="one-off: convert saved data to the current formats (UTC times, metadata sidecars)")
    mode.add_argument("--version", action="store_true", help="print the tool and method versions and exit")
    mode.add_argument("--attribute", action="store_true",
                      help="only find who made the changes of saved crawls and usage checks that don't have it yet")
    mode.add_argument("--usage-only", action="store_true",
                      help="only check where the files of the latest crawl are used on other wikis")
    ap.add_argument("--usage", action="store_true", help="after the crawl, also check on-wiki usage")
    ap.add_argument("--log", help="append all output to this file (used by the scheduled task)")
    args = ap.parse_args()
    if args.version:
        import version
        print(version.describe())
        return
    if not args.dashboard:
        keep_awake()
    if args.log:
        log = Path(args.log)
        if not log.is_absolute():
            log = BASE / log
        log.parent.mkdir(parents=True, exist_ok=True)
        sys.stdout = sys.stderr = log.open("a", encoding="utf-8", buffering=1)
        print(f"===== {fmt_time(datetime.now(timezone.utc))} =====")
        try:
            archived_dispatch(args)
        except BaseException as e:
            import traceback
            traceback.print_exc()
            raise SystemExit(1) from e
        print("finished OK")
        return
    archived_dispatch(args)


def job_name(args):
    for flag in ("dashboard", "views", "details_only", "assessments_only", "upload_dates", "attribute", "usage_only", "full",
                 "migrate"):
        if getattr(args, flag, False):
            return flag.replace("_", "-") + ("+usage" if flag == "full" and args.usage else "")
    return "quick" + ("+usage" if args.usage else "")


def archived_dispatch(args):
    """Run the job inside a run record (provenance.py: what ran, with which code, what it read and wrote, and a
    metadata sidecar for every data file it changed), then record the state of all data files in the archive
    (archive.py), whether the job succeeded or not, so every state the data has ever been in can be restored. A
    plain dashboard rebuild changes no data and isn't recorded; a job that didn't start because another one was
    running is recorded as skipped, without an archive state."""
    if args.dashboard:
        return dispatch(args)
    import archive
    import provenance
    job = job_name(args)
    log = str(args.log or "")
    trigger = ("scheduled task" if "scheduled" in log or "views" in log else "dashboard button" if "manual" in log
               else "command line")
    run = provenance.Run(job, trigger=trigger)
    with run:
        try:
            result = dispatch(args)
        except BaseException as e:
            rec = run.finish("failed", f"{type(e).__name__}: {e}" if str(e) else type(e).__name__)
            archive.safe_record(job, status="failed", error=rec["error"], extra={"run_id": rec["run_id"]})
            raise
        rec = run.finish("skipped" if result == "skipped" else "ok")
    if rec["status"] != "skipped":
        archive.safe_record(job, extra={"run_id": rec["run_id"]})
    print(f"Run record: runs/{rec['run_id']}.json")


def dispatch(args):
    if args.dashboard:
        build_dashboard()
        return
    if args.views:
        lock = try_lock(VIEWS_LOCK)
        if lock is None:
            print("The views job is already running.")
            return "skipped"
        with lock:
            snap = load_json(latest_snapshot())
            config = load_json(args.config)
            CommonsAPI(config["user_agent"]).wait_for_network()
            last = [0.0]

            def progress(done, total):
                if time.monotonic() - last[0] >= 60 or done == total:
                    last[0] = time.monotonic()
                    print(f"  {done:,} / {total:,} files")

            update_views(snap["files"], config["user_agent"], progress)
        return
    if args.details_only:
        lock = try_lock()
        if lock is None:
            print("A crawl is running; try again when it has finished.")
            return "skipped"
        with lock:
            snap = load_json(latest_snapshot())
            api = CommonsAPI(load_json(args.config)["user_agent"])
            last = [0.0]

            def progress(done, total):
                if time.monotonic() - last[0] >= 30 or done == total:
                    last[0] = time.monotonic()
                    print(f"  {done:,} / {total:,} files")

            new, rechecked = update_file_details(api, snap["files"], progress)
            print(f"Done: {new:,} new, {rechecked:,} locations re-checked, {api.requests:,} requests.")
            build_dashboard()
        return
    if args.assessments_only:
        lock = try_lock()
        if lock is None:
            print("A crawl is running; try again when it has finished.")
            return "skipped"
        with lock:
            snap = load_json(latest_snapshot())
            api = CommonsAPI(load_json(args.config)["user_agent"])
            check_assessments(api, snap["files"], True, snap["taken_at"])
            print(f"{api.requests:,} requests.")
            build_dashboard()
        return
    if args.migrate:
        import migrate
        return migrate.run()
    if args.attribute:
        lock = try_lock()
        if lock is None:
            print("A crawl is running; try again when it has finished.")
            return "skipped"
        with lock:
            attribute_backfill(load_json(args.config))
        return
    if args.upload_dates:
        lock = try_lock()
        if lock is None:
            print("A crawl is running; try again when it has finished.")
            return "skipped"
        with lock:
            config = load_json(args.config)
            snap = load_json(latest_snapshot())
            api = CommonsAPI(config["user_agent"])
            last = [0.0]

            def progress(done, total):
                if time.monotonic() - last[0] >= 30 or done == total:
                    last[0] = time.monotonic()
                    print(f"  {done:,} / {total:,} files")

            n = update_upload_dates(api, list(snap["files"]), progress)
            update_media_types(api, snap["files"])
            print(f"Done: {n:,} file(s) looked up in {api.requests:,} requests.")
            build_dashboard()
        return
    return run(load_json(args.config), force_full=args.full, usage=args.usage, usage_only=args.usage_only)


if __name__ == "__main__":
    main()
