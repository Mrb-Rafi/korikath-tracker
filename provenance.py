"""Where every data file came from: a metadata sidecar per data file, and a record of every run.

Metadata sidecar, `<data file>.meta` (JSON), next to each data file (e.g. usage.json.gz.meta):
    what the file is and where its data comes from (which API, which module), its format version, the tool and
    method versions and code commit that wrote it, the run that wrote it, when its data is "as of", whether the job
    that wrote it finished, how many of the project's files it covers, which crawl it is based on, and the SHA-256
    of the data file itself, which binds the two: a sidecar whose data_sha256 doesn't match its file is stale.

Run record, `runs/<run id>.json`:
    the job, how it was started, when it started and ended (UTC), whether it finished, the error if not, the build
    information, the number of API requests per wiki, every data file it read (SHA-256 at the start) and every one it
    wrote (SHA-256 at the end). The archive state recorded right after it (archive.py) holds the run record too, and
    names the run (extra.run_id).

Sidecars are refreshed at the end of every job for the files that job changed, so the data writers themselves
don't need to know about them. `python provenance.py` checks every data file against its sidecar.
"""

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import methodology
import version

BASE = Path(__file__).resolve().parent
RUNS = BASE / "runs"
CURRENT = None  # the Run in progress in this process, if any
COMMONS_API = "https://commons.wikimedia.org/w/api.php"

# Every data file: (path relative to BASE or glob, dataset name, source of its data, what it holds).
DATASETS = [
    ("snapshots/*.json.gz", "snapshot", f"{COMMONS_API}: list=categorymembers, prop=categoryinfo",
     "One crawl: every category of the tree (parents, member counts) and every file (its tree categories)."),
    ("reports/*.json", "crawl_report", "Two saved snapshots compared; Commons deletion and move logs; edits found by attribution.py",
     "What changed between two crawls, and who made each change."),
    ("history.json", "history", "Saved crawls", "Totals and change counts of every crawl."),
    ("uploads.json.gz", "uploads", f"{COMMONS_API}: prop=imageinfo&iiprop=timestamp|user",
     "Original upload time and uploader of each file (page id -> [time, user])."),
    ("mediatypes.json", "mediatypes", f"{COMMONS_API}: prop=imageinfo&iiprop=mediatype",
     "Commons media type of files that aren't plain images."),
    ("usage.json.gz", "usage", f"{COMMONS_API}: prop=globalusage", "The latest on-wiki usage check: every use of every file."),
    ("usage_history.json", "usage_history", "Successive usage checks", "Totals and changes of every usage check."),
    ("usage_log.csv", "usage_log", "Successive usage checks; edits found by attribution.py",
     "Every use added or removed, with who made the change."),
    ("usage_current.csv", "usage_current", f"{COMMONS_API}: prop=globalusage", "Every current use (spreadsheet)."),
    ("assessments.json.gz", "assessments", f"{COMMONS_API}: list=categorymembers (assessment categories), prop=templates",
     "Quality, featured, valued, featured-media and picture/media-of-the-day status of project files."),
    ("assessment_history.json", "assessment_history", "Successive assessment checks", "Totals and changes of every assessment check."),
    ("geo.json.gz", "geo", f"{COMMONS_API}: prop=coordinates", "Camera location on each file's Commons page."),
    ("exif.json.gz", "exif", f"{COMMONS_API}: prop=imageinfo&iiprop=metadata|size",
     "Camera make, model, GPS position, time taken and file size, from each file's own metadata."),
    ("views.json.gz", "views", "https://wikimedia.org/api/rest_v1/metrics/mediarequests/per-file (all-agents, user)",
     "File requests per file per month, and per day for the project."),
    ("languages.json", "languages", f"{COMMONS_API}: meta=languageinfo", "Language names, for readable wiki names."),
    ("removed_files.csv", "removed_files", "Saved crawl reports", "Every file that has ever left the project tree."),
]


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def modified(path):
    """When a file was last written (UTC, ISO)."""
    return datetime.fromtimestamp(Path(path).stat().st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def meta_path(path):
    return Path(str(path) + ".meta")


def data_files():
    """{absolute path: (dataset, source, description)} of every data file that exists."""
    out = {}
    for pattern, name, source, desc in DATASETS:
        for p in sorted(BASE.glob(pattern)):
            if p.is_file() and not p.name.endswith((".tmp", ".meta")):
                out[p] = (name, source, desc)
    return out


def hashes():
    out = {}
    for p in data_files():
        try:
            out[p.relative_to(BASE).as_posix()] = sha256_file(p)
        except FileNotFoundError:
            pass
    return out


def read_meta(path):
    try:
        return json.loads(meta_path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return None


def write_json(path, data):
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, path)


# ------------------------------------------------------------------------------- what each data file covers


def describe(path, name, snapshot, snap_name):
    """(as_of, coverage, notes) of one data file, read from its own content."""
    import tracker
    files = (snapshot or {}).get("files") or {}
    n = len(files)
    as_of, coverage, notes = None, None, []
    try:
        if name == "snapshot":
            d = tracker.load_json(path)
            as_of = d.get("taken_at")
            coverage = {"files": d.get("total_files"), "categories": d.get("total_categories")}
        elif name == "crawl_report":
            d = tracker.load_json(path)
            as_of = d.get("taken_at")
            items = [x for v in d["changes"].values() for x in v]
            coverage = {"changes": len(items), "with_who": sum(1 for x in items if x.get("by") or x.get("by_category"))}
        elif name == "uploads":
            d = tracker.load_json(path, {})
            coverage = {"of": n, "covered": sum(1 for p in files if d.get(p))}
        elif name == "usage":
            d = tracker.load_json(path)
            as_of = d.get("checked_at")
            checked = set(d.get("checked") or ())
            coverage = {"of": n, "covered": sum(1 for p in files if p in checked)}
        elif name == "assessments":
            d = tracker.load_json(path)
            as_of = d.get("checked_at")
            checked = set(d.get("checked") or ())
            coverage = {"of": n, "covered": sum(1 for p in files if p in checked), "last_check": "full" if d.get("full") else "incremental"}
        elif name in ("geo", "exif"):
            d = tracker.load_json(path, {})
            ok = (lambda v: isinstance(v, list) and len(v) >= 6) if name == "exif" else (lambda v: True)
            coverage = {"of": n, "covered": sum(1 for p in files if p in d and (d[p] is None or ok(d[p])))}
        elif name == "views":
            d = tracker.load_json(path, {})
            vf = d.get("files") or {}
            have = [p for p in files if p in vf]
            ends = [vf[p][1] for p in have]
            as_of = min(ends) if ends else None
            coverage = {"of": n, "covered": len(have), "counted_through_earliest": as_of,
                        "counted_through_latest": max(ends) if ends else None}
            if len(have) < n:
                notes.append("File-request history is still being collected for some files (limitation L06).")
        elif name == "mediatypes":
            d = tracker.load_json(path, {})
            non_image = [p for p, f in files.items() if tracker.extension(f["title"]) not in tracker.IMAGE_EXTS]
            coverage = {"of": len(non_image), "covered": sum(1 for p in non_image if p in d)}
    except Exception as e:  # noqa: BLE001  a description problem must not stop a job
        notes.append(f"Could not read the file to describe it: {type(e).__name__}: {e}")
    if coverage and "of" in coverage and coverage["of"]:
        coverage["share"] = round(coverage["covered"] / coverage["of"], 6)
    return as_of, coverage, notes


_SNAPSHOTS = {}


def _snapshot_at(as_of):
    """(snapshot, file name) of the crawl that was the latest at time `as_of` (ISO), or the latest crawl."""
    import tracker
    snaps = sorted(tracker.SNAPSHOT_DIR.glob("*.json.gz"))
    if as_of:
        stamp = tracker.iso_utc(as_of).replace("-", "").replace(":", "")  # 20261104T045913Z
        older = [x for x in snaps if x.name.split(".")[0].replace("-", "") <= stamp]
        snaps = older or snaps[:1]
    if not snaps:
        return None, None
    path = snaps[-1]
    if path.name not in _SNAPSHOTS:
        _SNAPSHOTS.clear()  # keep at most one in memory
        _SNAPSHOTS[path.name] = tracker.load_json(path)
    return _SNAPSHOTS[path.name], path.name


def write_sidecars(changed, run, extra_notes=()):
    """Write the metadata sidecar of each data file in `changed` (absolute paths). A file's coverage is counted
    against the crawl that was the latest when its data was taken ("as of"), which is the crawl it is based on."""
    import tracker
    known = data_files()
    for path in changed:
        if path not in known:
            continue
        name, source, desc = known[path]
        latest, latest_name = _snapshot_at(None)
        as_of, coverage, notes = describe(path, name, latest, latest_name)
        snapshot, snap_name = latest, latest_name
        if as_of and "T" in str(as_of) and name not in ("snapshot", "crawl_report"):  # a check time, not a cutoff date
            snapshot, snap_name = _snapshot_at(as_of)
            if snap_name != latest_name:
                as_of, coverage, notes = describe(path, name, snapshot, snap_name)
        based = name not in ("snapshot", "crawl_report", "history", "usage_history", "assessment_history", "languages",
                             "removed_files", "usage_log")
        meta = {
            "dataset": name, "file": path.relative_to(BASE).as_posix(), "description": desc, "source": source,
            "schema_version": version.SCHEMA_VERSIONS.get(name), **version.build_info(),
            "written_by_run": run["run_id"], "written_at": utc_now(),
            "as_of": tracker.iso_utc(as_of) if as_of and "T" in str(as_of) else (as_of or modified(path)),
            "complete": run["status"] == "ok", "coverage": coverage,
            "based_on_snapshot": snap_name if based else None,
            "based_on_snapshot_sha256": sha256_file(tracker.SNAPSHOT_DIR / snap_name) if based and snap_name else None,
            "notes": list(extra_notes) + notes, "data_sha256": sha256_file(path), "data_bytes": path.stat().st_size,
        }
        write_json(meta_path(path), meta)


def refresh_if_stale(paths, label):
    """Rewrite the sidecar of each file whose content no longer matches it (a file rebuilt outside a job)."""
    stale = [p for p in paths if p.exists() and (read_meta(p) or {}).get("data_sha256") != sha256_file(p)]
    if stale:
        write_sidecars(stale, {"run_id": label, "status": "ok", "finished_at": utc_now()})


STATUS_ROWS = [  # (data file, dataset, label shown in the dashboard and reports)
    ("uploads.json.gz", "uploads", "Upload dates and uploaders"),
    ("mediatypes.json", "mediatypes", "Kinds of non-image media"),
    ("usage.json.gz", "usage", "On-wiki usage"),
    ("assessments.json.gz", "assessments", "Quality, featured and valued images"),
    ("geo.json.gz", "geo", "Locations on Commons"),
    ("exif.json.gz", "exif", "Camera metadata and file sizes"),
    ("views.json.gz", "views", "File requests (views)"),
]


def _when(t):
    import tracker
    return tracker.fmt_time(t) if t else "?"


def data_status(snapshot, snap_name):
    """How current and complete each dataset is, relative to the crawl `snapshot`: one row per dataset,
    {dataset, label, as_of, covered, of, share, note}. Read from the data files themselves, so it is right even
    in the middle of a job (before the .meta sidecars are refreshed)."""
    rows = [{"dataset": "snapshot", "label": "Project files (crawl)", "as_of": snapshot.get("crawl_finished_at") or snapshot.get("taken_at"),
             "covered": snapshot.get("total_files"), "of": snapshot.get("total_files"), "share": 1.0,
             "note": f"{snapshot.get('total_categories')} categories; crawl started {_when(snapshot.get('taken_at'))}"
                     + (f", finished {_when(snapshot['crawl_finished_at'])}" if snapshot.get("crawl_finished_at") else "")}]
    for rel, name, label in STATUS_ROWS:
        path = BASE / rel
        if not path.exists():
            rows.append({"dataset": name, "label": label, "as_of": None, "covered": 0, "of": None, "share": None,
                         "note": "not collected yet"})
            continue
        as_of, cov, notes = describe(path, name, snapshot, snap_name)
        cov = cov or {}
        note = "; ".join(notes)
        if name == "views" and cov.get("counted_through_earliest"):
            note = (f"each file counted through {cov['counted_through_earliest']} to {cov['counted_through_latest']}; " + note).strip("; ")
        if name == "assessments" and cov.get("last_check"):
            note = (f"last check: {cov['last_check']}; " + note).strip("; ")
        if as_of is None:  # a cache that is added to over time: as of its last update
            as_of = modified(path)
        rows.append({"dataset": name, "label": label, "as_of": as_of, "covered": cov.get("covered"), "of": cov.get("of"),
                     "share": cov.get("share"), "note": note})
    return rows


# ---------------------------------------------------------------------------------------------- run records


class Run:
    """with Run(job, argv) as run: ... — records the run and refreshes the sidecars of what it changed."""

    def __init__(self, job, argv=None, trigger=None):
        started = datetime.now(timezone.utc)
        self.record = {
            "run_id": f"{started:%Y%m%dT%H%M%SZ}-{job}", "job": job, "argv": list(argv or sys.argv),
            "trigger": trigger or ("scheduled" if "--log" in (argv or sys.argv) and "scheduled" in " ".join(argv or sys.argv) else "manual"),
            "started_at": started.strftime("%Y-%m-%dT%H:%M:%SZ"), "finished_at": None, "status": "running",
            "error": None, **version.build_info(), "requests": {}, "read": {}, "wrote": {},
        }

    def __enter__(self):
        import tracker
        global CURRENT
        CURRENT = self
        self._requests0 = dict(tracker.REQUEST_COUNTS)
        self.before = hashes()
        self.record["read"] = self.before
        return self

    def finish(self, status, error=None):
        import tracker
        self.record["finished_at"] = utc_now()
        self.record["status"] = status
        self.record["error"] = error
        self.record["requests"] = {h: n - self._requests0.get(h, 0) for h, n in tracker.REQUEST_COUNTS.items()
                                   if n - self._requests0.get(h, 0)}
        after = hashes()
        wrote = {k: v for k, v in after.items() if self.before.get(k) != v}
        self.record["wrote"] = wrote
        self.record["removed"] = sorted(set(self.before) - set(after))
        try:
            write_sidecars([BASE / k for k in wrote], self.record)
        except Exception as e:  # noqa: BLE001
            self.record["sidecar_error"] = f"{type(e).__name__}: {e}"
            print(f"WARNING: could not write data-file metadata: {e}", file=sys.stderr)
        RUNS.mkdir(exist_ok=True)
        write_json(RUNS / f"{self.record['run_id']}.json", self.record)
        return self.record

    def __exit__(self, exc_type, exc, tb):
        if self.record["status"] == "running":
            if exc_type is None:
                self.finish("ok")
            else:
                self.finish("failed", f"{exc_type.__name__}: {exc}")
        return False


# ------------------------------------------------------------------------------------------------- checking


def check():
    """[(file, problem)] for data files whose sidecar is missing or stale."""
    problems = []
    for path in data_files():
        meta = read_meta(path)
        rel = path.relative_to(BASE).as_posix()
        if meta is None:
            problems.append((rel, "no metadata sidecar"))
        elif meta.get("data_sha256") != sha256_file(path):
            problems.append((rel, "changed since its metadata was written"))
    return problems


def main():
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    files = data_files()
    problems = check()
    print(f"{len(files)} data files, method {methodology.METHOD_VERSION}, {version.describe()}")
    for rel, problem in problems:
        print(f"  {rel}: {problem}")
    print("All data files match their metadata." if not problems else f"{len(problems)} problem(s).")
    raise SystemExit(1 if problems else 0)


if __name__ == "__main__":
    main()
