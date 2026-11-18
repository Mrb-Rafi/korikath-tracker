"""The files one contributor uploaded to one category of the Korikath tree.

Read from the latest saved crawl and the cached uploaders (uploads.json.gz), so no requests go to
Commons. The uploader is each file's original uploader, as everywhere else in Reoa. The
dashboard's "Files by contributor" page asks the local server (server.py) for this.

    python user_files.py "Category:Project Korikath-Bangladesh" "Wasiul Bahar"
    python user_files.py "Project Korikath-Food" "Mrb Rafi" --direct      only files directly in the category
    python user_files.py "Project Korikath-Food"                          everyone who uploaded to it, with counts
"""

import argparse
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
import tracker  # noqa: E402

_cache = {"key": None, "snapshot": None, "uploads": None, "names": None}


def saved_data():
    """(snapshot, uploads), loaded again only when a newer crawl or upload cache has been saved."""
    snap_path = tracker.latest_snapshot()
    if snap_path is None:
        raise RuntimeError("There is no crawl yet. Run a crawl first.")
    up = tracker.UPLOADS_FILE
    key = (snap_path.name, up.stat().st_mtime if up.exists() else None)
    if _cache["key"] != key:
        snapshot, uploads = tracker.load_json(snap_path), tracker.load_json(up, {})
        names = {tracker.upload_info(uploads, pid)[1] for pid in snapshot["files"]} - {None}
        _cache.update(key=key, snapshot=snapshot, uploads=uploads, names=names)
    return _cache["snapshot"], _cache["uploads"], _cache["names"]


def normalize_user(name):
    """'user:mrb_rafi ' -> 'Mrb rafi': MediaWiki user names have a capital first letter and spaces."""
    name = name.strip().replace("_", " ")
    if name.lower().startswith("user:"):
        name = name[5:].strip()
    return " ".join(name.split())[:1].upper() + " ".join(name.split())[1:]


def find_category(snapshot, category):
    """Category id of a title (with or without "Category:", underscores or a Commons link), or KeyError."""
    title = category.strip()
    if "/wiki/" in title:
        from urllib.parse import unquote
        title = unquote(title.split("/wiki/", 1)[1].split("?")[0].split("#")[0])
    title = tracker.normalize_category(title)
    cats = snapshot["categories"]
    for cid, c in cats.items():
        if c["title"] == title:
            return cid
    for cid, c in cats.items():
        if c["title"].lower() == title.lower():
            return cid
    raise KeyError(f"{title} is not in the Korikath category tree (as of the latest crawl).")


def files_in_category(snapshot, cid, subcategories=True):
    """Ids of the files in a category, and in every category below it when subcategories is true."""
    cats, files = snapshot["categories"], snapshot["files"]
    wanted = {cid}
    if subcategories:
        children = {}
        for x, c in cats.items():
            for par in c["parents"]:
                children.setdefault(par, []).append(x)
        stack = [cid]
        while stack:
            for k in children.get(stack.pop(), ()):
                if k not in wanted:
                    wanted.add(k)
                    stack.append(k)
    return [pid for pid, f in files.items() if wanted.intersection(f["categories"])], len(wanted) - 1


def lookup(category, user=None, subcategories=True):
    """The files `user` uploaded to `category` (and below it, unless subcategories is false).

    Returns {category, user, subcategories, category_files, files: [[title, uploaded]], uploaders:
    [[name, files]], known_user, taken_at}. Without a user, files is empty and uploaders lists everyone
    who uploaded to the category. Raises KeyError for a category outside the tree."""
    snapshot, uploads, names = saved_data()
    cid = find_category(snapshot, category)
    ids, n_sub = files_in_category(snapshot, cid, subcategories)
    per_user = {}
    for pid in ids:
        per_user.setdefault(tracker.upload_info(uploads, pid)[1] or "(unknown)", []).append(pid)
    name = normalize_user(user) if user and user.strip() else None
    if name and name not in names:
        # Fall back to a match that ignores case, e.g. "wasiul bahar".
        name = next((u for u in sorted(names) if u.lower() == name.lower()), name)
    files = sorted(([snapshot["files"][pid]["title"], tracker.upload_info(uploads, pid)[0]] for pid in per_user.get(name, ())),
                   key=lambda f: f[0].lower()) if name else []
    known = bool(name) and name in names
    return {"category": snapshot["categories"][cid]["title"], "user": name, "subcategories": subcategories,
            "subcategory_count": n_sub, "category_files": len(ids), "files": files, "known_user": known,
            "uploaders": sorted(([u, len(p)] for u, p in per_user.items()), key=lambda r: (-r[1], r[0].lower())),
            "taken_at": snapshot["taken_at"]}


def main():
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("category", help='category title, e.g. "Category:Project Korikath-Food" ("Category:" is optional)')
    ap.add_argument("user", nargs="?", help="uploader's user name; leave out to list everyone who uploaded to the category")
    ap.add_argument("--direct", action="store_true", help="only files directly in the category, not in its subcategories")
    args = ap.parse_args()
    try:
        r = lookup(args.category, args.user, not args.direct)
    except (KeyError, RuntimeError) as e:
        sys.exit(e.args[0])
    scope = "directly in it" if args.direct else f"in it and its {r['subcategory_count']:,} subcategories"
    print(f"Latest crawl: {tracker.fmt_time(r['taken_at'])}")
    print(f"{r['category']}: {r['category_files']:,} files {scope}")
    if not r["user"]:
        print(f"Uploaded by {len(r['uploaders']):,} people:\n")
        for name, n in r["uploaders"]:
            print(f"{n:>8,}  {name}")
        return
    print(f"{r['user']} uploaded {len(r['files']):,} of them"
          + ("" if r["known_user"] else f" (no files by {r['user']} anywhere in Korikath; check the spelling)") + "\n")
    for i, (title, uploaded) in enumerate(r["files"], 1):
        print(f"{i:>6}. {title}  ({tracker.fmt_time(uploaded) if uploaded else 'upload date unknown'})")
    print(f"\nTotal: {len(r['files']):,}")


if __name__ == "__main__":
    main()
