"""Version of the tracker's code, method and data formats, recorded in every data file, run and report.

TOOL_VERSION follows semantic versioning: MAJOR when a data file format or a report changes incompatibly, MINOR when
a metric or feature is added, PATCH for fixes. The method version (what is counted and how) lives in the methodology
register, methodology.py. Each change is described in CHANGELOG.md.

    python version.py     print the build information
"""

import hashlib
import json
import platform
import subprocess
import sys
from pathlib import Path

import methodology

TOOL_VERSION = "2.4.1"
BASE = Path(__file__).resolve().parent

# Format version of each data file (bumped when its structure changes).
SCHEMA_VERSIONS = {
    "snapshot": 2, "crawl_report": 2, "history": 2, "uploads": 1, "mediatypes": 1, "usage": 1, "usage_history": 1,
    "usage_log": 2, "usage_current": 1, "assessments": 1, "assessment_history": 1, "geo": 1, "exif": 1, "views": 1,
    "languages": 1, "removed_files": 2,
}


def _git(*args):
    try:
        out = subprocess.run(["git", *args], cwd=BASE, capture_output=True, text=True, timeout=10,
                             creationflags=0x08000000 if sys.platform == "win32" else 0)
        return out.stdout.strip() if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def config_sha256():
    try:
        return hashlib.sha256((BASE / "config.json").read_bytes()).hexdigest()
    except FileNotFoundError:
        return None


_INFO = None


def build_info():
    """Tool and method version, code commit (and whether the code has uncommitted changes), Python, OS and the
    fingerprint of config.json. Computed once per process."""
    global _INFO
    if _INFO is None:
        commit = _git("rev-parse", "--short=12", "HEAD")
        status = _git("status", "--porcelain", "--untracked-files=no")
        _INFO = {
            "tool_version": TOOL_VERSION,
            "method_version": methodology.METHOD_VERSION,
            "method_fingerprint": methodology.fingerprint(),
            "git_commit": commit,
            "code_modified": None if status is None else bool(status),
            "python": platform.python_version(),
            "platform": platform.platform(),
            "config_sha256": config_sha256(),
        }
    return dict(_INFO)


def describe():
    i = build_info()
    code = (f"commit {i['git_commit']}" + (" with uncommitted changes" if i["code_modified"] else "")
            if i["git_commit"] else "not under version control")
    return f"Korikath Tracker {i['tool_version']} · method {i['method_version']} · {code}"


if __name__ == "__main__":
    print(describe())
    print(json.dumps(build_info(), indent=1))
