"""Checks run before every report, which decide whether it is FINAL or PROVISIONAL.

Every check has an ID, a level and a result (passed / failed / not run), and the PDF prints them all:

  error      the figures can't be trusted as they are (totals that don't add up, data files that don't match
             their metadata, a damaged archive, rules changed without a new method version). Any failure makes the
             report PROVISIONAL.
  freshness  the data is too old or incomplete for a report to cite (no full crawl in 8 days, a dataset checked
             long before the crawl, a dataset covering less than 99% of the files, code with uncommitted changes, no
             issuer set). Any failure makes the report PROVISIONAL.
  warning    something worth knowing that doesn't stop a FINAL report (an independent cross-check that disagrees
             or couldn't run). Printed with the result.

A PROVISIONAL report carries a watermark and lists the failed checks on its cover. Only FINAL reports should go to
donors, evaluators or the public.

    python validate.py              run every check now and print the results
    python validate.py --petscan    the monthly independent count with PetScan (saved in validation/petscan/)
"""

import json
import random
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import methodology
import provenance
import tracker
import version

BASE = Path(__file__).resolve().parent
PETSCAN_DIR = BASE / "validation" / "petscan"
MAX_FULL_CRAWL_AGE = timedelta(days=8)
MAX_CRAWL_AGE = timedelta(days=3)
MAX_DATASET_LAG = timedelta(days=2)
MIN_COVERAGE = 0.99
MAX_PETSCAN_AGE = timedelta(days=35)
MAX_FULL_ASSESSMENT_AGE = timedelta(days=35)
SPOT_SAMPLE = 30


def _t(iso):
    return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone(timezone.utc)


class Checks:
    def __init__(self):
        self.items = []

    def add(self, cid, level, name, ok, detail):
        """ok: True passed, False failed, None not run."""
        self.items.append({"id": cid, "level": level, "name": name,
                           "result": "passed" if ok else "not run" if ok is None else "failed", "detail": detail})

    def run(self, cid, level, name, fn):
        try:
            ok, detail = fn()
        except Exception as e:  # noqa: BLE001  a check that crashes counts as failed (or not run, for warnings)
            ok, detail = (None if level == "warning" else False), f"could not run: {type(e).__name__}: {e}"
        self.add(cid, level, name, ok, detail)


def status_of(items):
    """(FINAL or PROVISIONAL, [reasons])."""
    reasons = [f"{c['id']} {c['name']}: {c['detail']}" for c in items
               if c["level"] in ("error", "freshness") and c["result"] != "passed"]
    return ("PROVISIONAL" if reasons else "FINAL"), reasons


def run(D, now=None, live=True, config=None, sample_seed="0"):
    """Every check, for the figures D (tracker.build_payload). With `live`, also the checks that ask Commons."""
    now = now or datetime.now(timezone.utc)
    config = config or tracker.load_json(BASE / "config.json", {})
    C = Checks()
    snap_path = tracker.latest_snapshot()
    full = tracker.load_json(snap_path)
    S = D["snapshot"]

    # ---------------------------------------------------------------- errors: the figures themselves
    C.run("E01", "error", "Crawl snapshot is consistent", lambda: (
        full["total_files"] == len(full["files"]) == S["total_files"]
        and full["total_categories"] == len(full["categories"])
        and all(c in full["categories"] for f in full["files"].values() for c in f["categories"]),
        f"{len(full['files']):,} unique files, {len(full['categories']):,} categories; every file's categories are in the tree"))

    def totals_add_up():
        bad = []
        M = D.get("media")
        if M and sum(M.values()) != S["total_files"]:
            bad.append(f"kinds of media add up to {sum(M.values()):,}, not {S['total_files']:,}")
        Cn = D.get("contributors")
        if Cn and sum(u[1] for u in Cn["users"]) + Cn.get("unknown", 0) != S["total_files"]:
            bad.append("uploaders' files plus unknown don't add up to the total")
        U = D.get("usage")
        if U:
            for scope in ("all", "content"):
                a = U[scope]
                if sum(r[1] for r in a["by_wiki"]) != a["usages"]:
                    bad.append(f"usage by wiki doesn't add up ({scope})")
                if a["files_used"] > S["total_files"]:
                    bad.append("more files used than in the project")
        V = D.get("views")
        if V and not (0 <= V["human"] <= V["all"]):
            bad.append("views not flagged as automated exceed all views")
        A = D.get("assessments")
        if A and A["any"] > S["total_files"]:
            bad.append("more assessed files than files")
        return not bad, "; ".join(bad) or "media kinds, uploaders, usage by wiki, views and assessments add up to their totals"
    C.run("E02", "error", "Totals add up", totals_add_up)

    def views_cutoff():
        V = D.get("views")
        if not V:
            return True, "no view counts"
        vf = tracker.load_json(tracker.VIEWS_FILE)["files"]
        cut = V["cutoff_month"]
        total = sum(v[0] for pid in full["files"] if pid in vf for m, v in vf[pid][2].items() if cut and m <= cut)
        return total == V["all"], f"recomputed total to {cut}: {total:,} (report: {V['all']:,})"
    C.run("E03", "error", "View totals recomputed independently", views_cutoff)

    def metadata():
        problems = provenance.check()
        return not problems, (f"{len(provenance.data_files())} data files match their metadata" if not problems
                              else "; ".join(f"{f}: {p}" for f, p in problems[:5]))
    C.run("E04", "error", "Every data file matches its metadata", metadata)

    def method():
        ok, msg = methodology.check()
        return ok, msg
    C.run("E05", "error", "Rules match the recorded method version", method)

    def archive_ok():
        import archive
        problems = archive.problems()
        return not problems, ("register chain intact, every stored file undamaged" if not problems else "; ".join(problems[:3]))
    C.run("E06", "error", "Archive intact", archive_ok)

    # ---------------------------------------------------------------- freshness
    def code():
        info = version.build_info()
        if not info["git_commit"]:
            return False, "the code is not under version control"
        return not info["code_modified"], (f"commit {info['git_commit']}" + (" has uncommitted changes" if info["code_modified"] else ""))
    C.run("F01", "freshness", "Code is committed", code)

    def full_crawl():
        fulls = [h for h in D["history"] if h.get("mode") == "full"]
        if not fulls:
            return False, "no full crawl saved"
        age = now - _t(fulls[-1]["taken_at"])
        return age <= MAX_FULL_CRAWL_AGE, f"latest full crawl {tracker.fmt_time(fulls[-1]['taken_at'])} ({age.days} days ago; at most 8)"
    C.run("F02", "freshness", "Recent full crawl", full_crawl)

    def crawl_age():
        age = now - _t(S["taken_at"])
        return age <= MAX_CRAWL_AGE, f"crawl of {tracker.fmt_time(S['taken_at'])} ({age.days} days old; at most 3)"
    C.run("F03", "freshness", "Recent crawl", crawl_age)

    def datasets():
        bad = []
        crawl = _t(S["taken_at"])
        for r in D.get("data_status") or []:
            if r["share"] is not None and r["share"] < MIN_COVERAGE:
                bad.append(f"{r['label']} covers {r['covered']:,} of {r['of']:,} files")
            if r["dataset"] in ("usage", "assessments") and r["as_of"] and _t(r["as_of"]) < crawl - MAX_DATASET_LAG:
                bad.append(f"{r['label']} was checked {tracker.fmt_time(r['as_of'])}, more than 2 days before the crawl")
        return not bad, "; ".join(bad) or "every dataset covers at least 99% of the files and was checked within 2 days of the crawl"
    C.run("F04", "freshness", "Datasets complete and current", datasets)

    def issuer():
        i = config.get("issuer") or {}
        ok = bool(i.get("name") and i.get("contact"))
        return ok, (", ".join(x for x in (i.get("name"), i.get("role"), i.get("contact")) if x) if ok
                    else "no issuer (name and contact) in config.json, so readers can't report errors")
    C.run("F05", "freshness", "Issuer named", issuer)

    # ---------------------------------------------------------------- warnings: cross-checks
    def petscan():
        runs = sorted(PETSCAN_DIR.glob("*.json"))
        if not runs:
            return None, "never run (python validate.py --petscan)"
        r = json.loads(runs[-1].read_text(encoding="utf-8"))
        age = now - _t(r["checked_at"])
        if age > MAX_PETSCAN_AGE:
            return None, f"last run {tracker.fmt_time(r['checked_at'])}, more than 35 days ago"
        diff = r["petscan_files"] - r["tracker_files"]
        return diff == 0, (f"PetScan counted {r['petscan_files']:,} files on {tracker.fmt_time(r['checked_at'])}; the "
                           f"tracker's crawl of that day: {r['tracker_files']:,}" + ("" if not diff else f" (difference {diff:+,})"))
    C.run("W01", "warning", "Independent count (PetScan)", petscan)

    def spot():
        if not live:
            return None, "not run (offline)"
        return spot_check(full, sample_seed, config)
    C.run("W02", "warning", f"Live spot check of {SPOT_SAMPLE} random files", spot)

    def assessments_full():
        hist = tracker.load_json(tracker.ASSESS_HISTORY, [])
        fulls = [h for h in hist if h.get("full")]
        if not fulls:
            return None, "no full check recorded"
        age = now - _t(fulls[-1]["checked_at"])
        return age <= MAX_FULL_ASSESSMENT_AGE, (f"last full re-read {tracker.fmt_time(fulls[-1]['checked_at'])}"
                                                 f" ({age.days} days ago; incremental checks since)")
    C.run("W03", "warning", "Recent full re-read of the assessment categories", assessments_full)

    return C.items


def spot_check(full, seed, config):
    """Re-ask Commons, now, about SPOT_SAMPLE files chosen at random (seeded, so the same report picks the same
    files): does each still exist, with the same title, and the same original upload time and uploader?"""
    uploads = tracker.load_json(tracker.UPLOADS_FILE, {})
    pids = sorted(full["files"])
    sample = random.Random(seed).sample(pids, min(SPOT_SAMPLE, len(pids)))
    api = tracker.CommonsAPI(config["user_agent"])
    fresh = tracker.fetch_upload_dates(api, sample)
    titles = {}
    for data in api.query_all(pageids="|".join(sample)):
        for p in data["query"]["pages"]:
            titles[str(p.get("pageid"))] = None if p.get("missing") else p.get("title")
    bad = []
    for pid in sample:
        if titles.get(pid) != full["files"][pid]["title"]:
            bad.append(f"{full['files'][pid]['title']}: now {titles.get(pid) or 'missing'}")
        elif fresh.get(pid) != uploads.get(pid):
            bad.append(f"{full['files'][pid]['title']}: upload record differs")
    return not bad, (f"{len(sample) - len(bad)} of {len(sample)} matched on Commons ({tracker.fmt_time(datetime.now(timezone.utc))})"
                     + (": " + "; ".join(bad[:5]) if bad else ""))


def petscan_run(config=None):
    """Count the project's files with PetScan (an independent tool), and save the result next to the tracker's own
    count from its latest crawl."""
    config = config or tracker.load_json(BASE / "config.json", {})
    root = tracker.normalize_category(config["root_category"]).split(":", 1)[1]
    params = {"language": "commons", "project": "wikimedia", "categories": root, "depth": "99", "ns[6]": "1",
              "format": "json", "doit": "1"}
    url = "https://petscan.wmcloud.org/?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": config["user_agent"]})
    with urllib.request.urlopen(req, timeout=600) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    pages = data["*"][0]["a"]["*"]
    ids = {str(p["id"]) for p in pages}
    snap = tracker.load_json(tracker.latest_snapshot())
    mine = set(snap["files"])
    result = {"checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "query": url,
              "petscan_files": len(ids), "tracker_files": len(mine), "tracker_crawl": snap["taken_at"],
              "only_in_petscan": sorted(ids - mine)[:200], "only_in_tracker": sorted(mine - ids)[:200]}
    PETSCAN_DIR.mkdir(parents=True, exist_ok=True)
    path = PETSCAN_DIR / f"{result['checked_at'][:10]}.json"
    path.write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
    return result, path


def main():
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if "--petscan" in sys.argv:
        r, path = petscan_run()
        print(f"PetScan: {r['petscan_files']:,} files; tracker's latest crawl: {r['tracker_files']:,}. Saved {path.name}")
        return
    items = run(tracker.build_payload(write=False))
    for c in items:
        print(f"{c['id']} [{c['level']}] {c['result'].upper():8} {c['name']}: {c['detail']}")
    status, reasons = status_of(items)
    print(f"\nA report made now would be {status}.")


if __name__ == "__main__":
    main()
