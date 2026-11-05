"""Issued reports: their IDs, the register of every issued PDF, verification, corrections and rebuilding.

No PDF is stored. What makes a report checkable is kept instead, at almost no cost in space:

  - the archive state recorded just before the report was made (archive.py): every data file and every code
    file as they were, each stored once by its SHA-256, so the report can be regenerated at any time;
  - the report's manifest: report ID, type, subject, status and the reasons for it, the results of the checks,
    the issuer, the versions and code commit, the archive state and its fingerprint, and the fingerprints
    (SHA-256) of all the report's figures, of its HTML page and of the PDF;
  - one line per issued report in the register, published/register.jsonl. Each line holds the SHA-256 of the line
    before it, so a later edit or deletion of an entry is detectable. published/register.csv is the same list,
    readable, for publishing.

    python report.py --verify FILE.pdf                     was this PDF issued by the tracker? its status?
    python report.py --rebuild REPORT_ID                   regenerate the report from its archive state and
                                                           check its figures and page match what was issued
    python report.py --supersede OLD_ID --by NEW_ID --reason "..."   record a correction
    python report.py --register                            list issued reports
"""

import csv
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
PUBLISHED = BASE / "published"
REGISTER = PUBLISHED / "register.jsonl"
REGISTER_CSV = PUBLISHED / "register.csv"
MANIFESTS = PUBLISHED / "manifests"
ERRATA = PUBLISHED / "ERRATA.md"


def sha256(data):
    return hashlib.sha256(data if isinstance(data, bytes) else data.encode("utf-8")).hexdigest()


def canonical(obj):
    """Stable JSON bytes for fingerprinting."""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def figures_fingerprint(D):
    """SHA-256 of every figure of a report: the payload, without the build time, the build information and the data
    status (those depend on the moment and the machine, and are recorded in the manifest instead)."""
    return sha256(canonical({k: v for k, v in D.items() if k not in ("generated_at", "build", "data_status")}))


def report_id(kind, generated, core):
    """KRK-P-20261105-0731Z-3fa91c2e: kind (P project, C contributor), UTC time, first 8 hex of the fingerprint of
    the report's core manifest (its inputs, figures, checks and versions)."""
    return f"KRK-{'C' if kind == 'contributor' else 'P'}-{generated:%Y%m%d-%H%MZ}-{sha256(canonical(core))[:8]}"


def lines():
    if not REGISTER.exists():
        return []
    return [line for line in REGISTER.read_text(encoding="utf-8").splitlines() if line.strip()]


def entries():
    return [json.loads(line) for line in lines()]


def _append(entry):
    PUBLISHED.mkdir(exist_ok=True)
    prev = lines()
    entry = dict(entry, seq=len(prev) + 1, prev_sha256=sha256(prev[-1]) if prev else None)
    with REGISTER.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
    write_csv()
    return entry


def save_manifest(manifest):
    MANIFESTS.mkdir(parents=True, exist_ok=True)
    path = MANIFESTS / f"{manifest['report_id']}.json"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def load_manifest(rid):
    return json.loads((MANIFESTS / f"{rid}.json").read_text(encoding="utf-8"))


def issue(manifest):
    """Record an issued report in the register (its manifest must hold pdf_sha256)."""
    save_manifest(manifest)
    return _append({"event": "issued", "report_id": manifest["report_id"], "type": manifest["type"],
                    "subject": manifest["subject"], "generated_at": manifest["generated_at"],
                    "status": manifest["status"], "pdf_sha256": manifest["pdf_sha256"],
                    "html_sha256": manifest["html_sha256"], "figures_sha256": manifest["figures_sha256"],
                    "archive_state": manifest["archive_state"], "archive_state_sha256": manifest["archive_state_sha256"],
                    "manifest_sha256": sha256(canonical(manifest))})


def supersede(old_id, new_id, reason):
    known = {e["report_id"] for e in entries() if e["event"] == "issued"}
    for rid in (old_id, new_id):
        if rid not in known:
            raise SystemExit(f"{rid} is not an issued report.")
    entry = _append({"event": "superseded", "report_id": old_id, "superseded_by": new_id, "reason": reason,
                     "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
    write_errata()
    return entry


def state_of(rid):
    """(issued entry, superseded entry or None) of a report."""
    issued = superseded = None
    for e in entries():
        if e["report_id"] == rid:
            if e["event"] == "issued":
                issued = e
            elif e["event"] == "superseded":
                superseded = e
    return issued, superseded


def check_chain():
    """[problems] of the register's hash chain and of each manifest."""
    problems, prev = [], None
    for i, line in enumerate(lines(), 1):
        e = json.loads(line)
        if e.get("seq") != i:
            problems.append(f"entry {i}: sequence number {e.get('seq')}")
        if e.get("prev_sha256") != (sha256(prev) if prev else None):
            problems.append(f"entry {i} ({e.get('report_id')}): doesn't chain to the entry before it (edited or removed entry)")
        prev = line
        if e["event"] == "issued":
            try:
                if sha256(canonical(load_manifest(e["report_id"]))) != e["manifest_sha256"]:
                    problems.append(f"{e['report_id']}: its manifest was changed after it was issued")
            except FileNotFoundError:
                problems.append(f"{e['report_id']}: its manifest is missing")
    return problems


def verify_pdf(path):
    """A description of whether the PDF at `path` was issued, and its status. Returns (ok, lines)."""
    digest = sha256(Path(path).read_bytes())
    out = [f"SHA-256 of the file: {digest}"]
    match = [e for e in entries() if e["event"] == "issued" and e["pdf_sha256"] == digest]
    problems = check_chain()
    if problems:
        out += ["WARNING: the register itself fails its integrity check:"] + [f"  {p}" for p in problems]
    if not match:
        out.append("NOT ISSUED: no report in the register has this fingerprint. The file was not produced by the "
                   "tracker, or it was changed after it was issued.")
        return False, out
    e = match[0]
    _, sup = state_of(e["report_id"])
    out.append(f"ISSUED: {e['report_id']} · {e['type']} report · {e['subject']} · generated {e['generated_at']} · "
               f"status {e['status']}")
    if sup:
        out.append(f"SUPERSEDED by {sup['superseded_by']} on {sup['at']}: {sup['reason']}")
    return not sup and not problems, out


def write_csv():
    """published/register.csv: the register as a table, for publishing (one row per issued report)."""
    rows = {}
    for e in entries():
        if e["event"] == "issued":
            rows[e["report_id"]] = [e["report_id"], e["generated_at"], e["type"], e["subject"], e["status"], "",
                                    e["pdf_sha256"]]
        elif e["event"] == "superseded" and e["report_id"] in rows:
            rows[e["report_id"]][4] = "SUPERSEDED"
            rows[e["report_id"]][5] = e["superseded_by"]
    tmp = REGISTER_CSV.with_name("register.csv.tmp")
    with tmp.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["report_id", "generated_at_utc", "type", "subject", "status", "superseded_by", "pdf_sha256"])
        w.writerows(rows.values())
    os.replace(tmp, REGISTER_CSV)


def write_errata():
    items = [e for e in entries() if e["event"] == "superseded"]
    text = ["# Errata", "", "Corrections to issued reports. Each entry names the report that was wrong, why, and the",
            "report that replaces it. Generated from the register (published/register.jsonl).", ""]
    for e in items:
        text.append(f"- **{e['report_id']}** ({e['at']}): {e['reason']} Replaced by **{e['superseded_by']}**.")
    if not items:
        text.append("No corrections so far.")
    ERRATA.write_text("\n".join(text) + "\n", encoding="utf-8")
