"""Archive of every state of the tracker's data, so nothing a number was computed from is ever lost.

Most data files (usage, views, assessments, locations, camera data, upload dates, ...) are overwritten in place
by each job. After every job, this module records the exact state of all data files, the code and the logs:

    archive/objects/<aa>/<sha256>[.gz]   each distinct file content, stored once, named by its SHA-256
    archive/states/<seq>_<UTC time>_<job>.json
                                         one per job: every file's path, SHA-256 and size, plus the job, its
                                         outcome, the Python version and a fingerprint of the code
    archive/register.jsonl               one line per state; each line holds the SHA-256 of the line before
                                         it, so editing or deleting a past entry is detectable

A file that hasn't changed costs nothing (same content, same object). Nothing is ever deleted from the archive.
All times are UTC.

Standard library only. Usage:
    python archive.py list                     the recorded states
    python archive.py snapshot [--note TEXT]   record the current state by hand
    python archive.py restore STATE FOLDER     rebuild the data files of a state (seq number, state file name,
                                               or "latest") into an empty FOLDER, checking every hash
    python archive.py verify                   check the register chain, every state and every object
    python archive.py find FILE                which state(s) recorded this exact file (e.g. a PDF report)
"""

import argparse
import gzip
import hashlib
import json
import os
import platform
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
ARCHIVE = BASE / "archive"
OBJECTS = ARCHIVE / "objects"
STATES = ARCHIVE / "states"
REGISTER = ARCHIVE / "register.jsonl"
LOCK = ARCHIVE / "archive.lock"
FORMAT = 1

# What a state holds, relative to BASE. Generated or transient files (dashboard.html, map_points.js, status
# files, locks, temporary files) are left out; dashboard.html is added for report states, since a PDF report
# is built from the data embedded in it.
DATA_PATTERNS = ["*.json", "*.json.gz", "*.csv", "snapshots/*.json.gz", "*.meta", "snapshots/*.meta", "reports/*.json", "reports/*.meta",
                 "logs/*.log", "runs/*.json"]
CODE_PATTERNS = ["*.py", "dashboard_template.html", "run.bat", "*.md", "methodology_versions.json"]
EXCLUDE_SUFFIXES = (".tmp", ".lock")


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def read_bytes(path, attempts=10):
    """Read a file, retrying briefly if another job is replacing it at that moment (Windows locks)."""
    for i in range(attempts):
        try:
            return Path(path).read_bytes()
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(0.5)


def tracked_files(include_dashboard=False):
    """{relative path: absolute path} of everything a state records."""
    out = {}
    for pattern in DATA_PATTERNS + CODE_PATTERNS:
        for p in BASE.glob(pattern):
            if p.is_file() and not p.name.endswith(EXCLUDE_SUFFIXES):
                out[p.relative_to(BASE).as_posix()] = p
    if include_dashboard and (BASE / "dashboard.html").exists():
        out["dashboard.html"] = BASE / "dashboard.html"
    return dict(sorted(out.items()))


def is_code(rel):
    return "/" not in rel and any(Path(rel).match(p) for p in CODE_PATTERNS)


def object_path(digest):
    d = OBJECTS / digest[:2]
    return d / digest, d / (digest + ".gz")


def store_object(data):
    """Store content once. Returns (sha256, bytes newly written to disk)."""
    digest = sha256_bytes(data)
    raw, packed = object_path(digest)
    if raw.exists() or packed.exists():
        return digest, 0
    raw.parent.mkdir(parents=True, exist_ok=True)
    # Already-compressed files are stored as they are; everything else is gzip-compressed.
    target, payload = (raw, data) if data[:2] == b"\x1f\x8b" else (packed, gzip.compress(data, mtime=0))
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_bytes(payload)
    os.replace(tmp, target)
    return digest, len(payload)


def load_object(digest):
    raw, packed = object_path(digest)
    if raw.exists():
        data = raw.read_bytes()
    elif packed.exists():
        data = gzip.decompress(packed.read_bytes())
    else:
        raise FileNotFoundError(f"object {digest} is missing from the archive")
    if sha256_bytes(data) != digest:
        raise ValueError(f"object {digest} is damaged (its content no longer matches its SHA-256)")
    return data


class _Lock:
    """Only one process writes to the archive at a time (a crawl and the views job can end together)."""

    def __enter__(self):
        ARCHIVE.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + 120
        while True:
            try:
                self.fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self.fd, str(os.getpid()).encode())
                return self
            except FileExistsError:
                try:
                    if time.time() - LOCK.stat().st_mtime > 600:  # left behind by a process that died
                        LOCK.unlink()
                        continue
                except FileNotFoundError:
                    continue
                if time.monotonic() > deadline:
                    raise TimeoutError("the archive is locked by another process")
                time.sleep(0.5)

    def __exit__(self, *exc):
        os.close(self.fd)
        try:
            LOCK.unlink()
        except FileNotFoundError:
            pass


def register_lines():
    if not REGISTER.exists():
        return []
    return [line for line in REGISTER.read_text(encoding="utf-8").splitlines() if line.strip()]


def record_state(job, status="ok", error=None, extra=None, include_dashboard=False, extra_files=None):
    """Store every tracked file and write a state + register line. extra_files: {name in state: path}
    for files kept outside the tracker folder (e.g. a PDF report). Returns the state's file name."""
    with _Lock():
        files, new_objects, new_bytes = {}, 0, 0
        sources = {rel: p for rel, p in tracked_files(include_dashboard).items()}
        for name, p in (extra_files or {}).items():
            sources[name] = Path(p)
        for rel, p in sources.items():
            try:
                data = read_bytes(p)
            except FileNotFoundError:
                continue  # removed between listing and reading
            digest, written = store_object(data)
            new_objects += bool(written)
            new_bytes += written
            files[rel] = {"sha256": digest, "size": len(data),
                          "modified": datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
        code = {rel: f["sha256"] for rel, f in files.items() if is_code(rel)}
        lines = register_lines()
        seq = len(lines) + 1
        created = utc_now()
        state = {"format": FORMAT, "seq": seq, "created_at": created, "job": job, "status": status,
                 "error": error, "argv": sys.argv, "python": platform.python_version(),
                 "platform": platform.platform(), "code_fingerprint": sha256_bytes(json.dumps(code, sort_keys=True).encode()),
                 "extra": extra or {}, "new_objects": new_objects, "new_bytes": new_bytes, "files": files}
        name = f"{seq:06d}_{created.replace(':', '').replace('-', '')}_{job}.json"
        STATES.mkdir(parents=True, exist_ok=True)
        body = json.dumps(state, ensure_ascii=False, indent=1).encode("utf-8")
        tmp = STATES / (name + ".tmp")
        tmp.write_bytes(body)
        os.replace(tmp, STATES / name)
        entry = {"seq": seq, "created_at": created, "job": job, "status": status, "state": name,
                 "state_sha256": sha256_bytes(body), "prev_sha256": sha256_bytes(lines[-1].encode("utf-8")) if lines else None}
        if extra and extra.get("pdf_sha256"):
            entry["pdf_sha256"] = extra["pdf_sha256"]
        with REGISTER.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
        return name, len(files), new_objects, new_bytes


def safe_record(job, **kw):
    """record_state for use at the end of a job: never lets an archive problem break the job itself."""
    try:
        name, n, new, size = record_state(job, **kw)
        print(f"Archived state {name}: {n} files, {new} new ({size / 1e6:.1f} MB added).")
        return name
    except Exception as e:  # noqa: BLE001
        print(f"WARNING: could not archive this state: {e}", file=sys.stderr)
        return None


def resolve_state(ref):
    states = sorted(STATES.glob("*.json"))
    if not states:
        raise SystemExit("The archive has no states yet.")
    if ref == "latest":
        return states[-1]
    if ref.isdigit():
        for s in states:
            if int(s.name.split("_", 1)[0]) == int(ref):
                return s
    for s in states:
        if s.name == ref or s.stem == ref:
            return s
    raise SystemExit(f"No state {ref!r}. See: python archive.py list")


# ------------------------------------------------------------------ commands


def cmd_list(_args):
    total = 0
    for line in register_lines():
        e = json.loads(line)
        s = json.loads((STATES / e["state"]).read_text(encoding="utf-8"))
        total += s["new_bytes"]
        pdf = f"  pdf {e['pdf_sha256'][:12]}" if e.get("pdf_sha256") else ""
        print(f"{e['seq']:>5}  {e['created_at']}  {e['job']:<14} {e['status']:<7} {len(s['files']):>4} files"
              f"  +{s['new_bytes'] / 1e6:6.1f} MB{pdf}")
    print(f"Archive size: {total / 1e6:.1f} MB in {len(register_lines())} states.")


def cmd_snapshot(args):
    safe_record("manual", extra={"note": args.note} if args.note else None)


def cmd_restore(args):
    path = resolve_state(args.state)
    state = json.loads(path.read_text(encoding="utf-8"))
    out = Path(args.folder).resolve()
    if out == BASE:
        raise SystemExit("Restore into a separate, empty folder, not into the tracker's own folder.")
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"{out} is not empty.")
    for rel, f in state["files"].items():
        target = out / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(load_object(f["sha256"]))
    print(f"Restored state {path.name} ({len(state['files'])} files, every hash checked) into {out}")


def cmd_verify(_args):
    problems, prev = [], None
    lines = register_lines()
    for i, line in enumerate(lines, 1):
        e = json.loads(line)
        if e["seq"] != i:
            problems.append(f"line {i}: sequence number {e['seq']}, expected {i}")
        if e["prev_sha256"] != (sha256_bytes(prev.encode("utf-8")) if prev else None):
            problems.append(f"line {i}: does not chain to the line before it (edited or removed entry)")
        prev = line
        sp = STATES / e["state"]
        if not sp.exists():
            problems.append(f"line {i}: state file {e['state']} is missing")
            continue
        body = sp.read_bytes()
        if sha256_bytes(body) != e["state_sha256"]:
            problems.append(f"line {i}: state file {e['state']} was changed after it was recorded")
        for rel, f in json.loads(body)["files"].items():
            try:
                load_object(f["sha256"])
            except (FileNotFoundError, ValueError) as err:
                problems.append(f"line {i}: {rel}: {err}")
    recorded = {s.name for s in STATES.glob("*.json")}
    for extra in sorted(recorded - {json.loads(x)["state"] for x in lines}):
        problems.append(f"state file {extra} is not in the register")
    if problems:
        print("ARCHIVE CHECK FAILED:")
        for p in problems:
            print("  " + p)
        raise SystemExit(1)
    print(f"Archive OK: {len(lines)} states, register chain intact, every object present and undamaged.")


def cmd_find(args):
    digest = sha256_bytes(Path(args.file).read_bytes())
    print(f"SHA-256 {digest}")
    found = False
    for line in register_lines():
        e = json.loads(line)
        state = json.loads((STATES / e["state"]).read_text(encoding="utf-8"))
        for rel, f in state["files"].items():
            if f["sha256"] == digest:
                found = True
                print(f"  state {e['seq']} ({e['created_at']}, {e['job']}): recorded as {rel}")
    if not found:
        print("  Not in the archive: no job or report recorded this exact file.")
        raise SystemExit(1)


def main():
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list").set_defaults(fn=cmd_list)
    p = sub.add_parser("snapshot")
    p.add_argument("--note")
    p.set_defaults(fn=cmd_snapshot)
    p = sub.add_parser("restore")
    p.add_argument("state")
    p.add_argument("folder")
    p.set_defaults(fn=cmd_restore)
    sub.add_parser("verify").set_defaults(fn=cmd_verify)
    p = sub.add_parser("find")
    p.add_argument("file")
    p.set_defaults(fn=cmd_find)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
