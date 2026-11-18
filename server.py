"""Small local web server for the dashboard (started by the Reoa shortcut).

A page opened straight from disk can't start programs, so the dashboard is served from
here instead, and its "Start quick check" / "Start full crawl" buttons ask this server to
start tracker.py in the background. Only this computer can connect (127.0.0.1). The
server closes itself after IDLE_MINUTES with no dashboard open.

    pythonw server.py      start (or reuse) the server and open the dashboard
"""

import json
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
import report  # noqa: E402
import tracker  # noqa: E402
import user_files  # noqa: E402

PORT = 8777
URL = f"http://127.0.0.1:{PORT}/"
IDLE_MINUTES = 30
TASK_PREFIX = "Reoa"
NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW: don't flash a console for helper processes
DETACHED = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP: crawl outlives the server
STATIC = {"/": ("dashboard.html", "text/html; charset=utf-8"),
          "/dashboard.html": ("dashboard.html", "text/html; charset=utf-8"),
          "/status.js": ("status.js", "text/javascript; charset=utf-8"),
          "/map_points.js": ("map_points.js", "text/javascript; charset=utf-8"),
          "/views_status.js": ("views_status.js", "text/javascript; charset=utf-8"),
          "/favicon.ico": ("reoa.ico", "image/x-icon"),
          "/usage_log.csv": ("usage_log.csv", "text/csv; charset=utf-8"),
          "/usage_current.csv": ("usage_current.csv", "text/csv; charset=utf-8"),
          "/removed_files.csv": ("removed_files.csv", "text/csv; charset=utf-8")}
CRAWL_ARGS = {"quick": ["--quick"], "full": ["--full"], "usage": ["--usage-only"]}

last_request = time.monotonic()
schedule_cache = {"at": 0.0, "data": None}


def pythonw():
    exe = Path(sys.executable)
    w = exe.with_name("pythonw.exe")
    return str(w if w.exists() else exe)


def scheduled_tasks():
    """Next and last run of the Windows scheduled tasks (cached for a minute)."""
    if time.monotonic() - schedule_cache["at"] < 60 and schedule_cache["data"] is not None:
        return schedule_cache["data"]
    ps = (f"Get-ScheduledTask -TaskName '{TASK_PREFIX}*' -ErrorAction SilentlyContinue | ForEach-Object {{ "
          "$i = $_ | Get-ScheduledTaskInfo; [pscustomobject]@{ name = $_.TaskName; state = [string]$_.State; "
          "args = [string]$_.Actions[0].Arguments; "
          "next = if ($i.NextRunTime) { $i.NextRunTime.ToUniversalTime().ToString('o') } else { $null }; "
          "last = if ($i.LastRunTime -and $i.LastRunTime.Year -gt 2000) { $i.LastRunTime.ToUniversalTime().ToString('o') } else { $null }; "
          "result = $i.LastTaskResult } } | ConvertTo-Json -Compress")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True,
                             timeout=30, creationflags=NO_WINDOW).stdout.strip()
        data = json.loads(out) if out else []
        if isinstance(data, dict):
            data = [data]
        for t in data:
            t["mode"] = "full" if "--full" in (t.get("args") or "") else "quick"
            t["usage"] = "--usage" in (t.get("args") or "")
    except Exception:
        data = []
    schedule_cache.update(at=time.monotonic(), data=data)
    return data


def start_crawl(mode):
    if tracker.crawl_running():
        return False
    # Mark it as starting at once so the page reacts before tracker.py has loaded.
    tracker.write_status(running=True, phase="starting", mode=mode, categories=0, files=0,
                         started_at=time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()))
    subprocess.Popen([pythonw(), "-u", str(BASE / "tracker.py"), *CRAWL_ARGS[mode], "--log", "logs\\manual.log"],
                     cwd=BASE, creationflags=DETACHED | NO_WINDOW, close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return True


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        global last_request
        last_request = time.monotonic()
        path = self.path.split("?", 1)[0]
        if path == "/api/ping":
            return self.send(200, {"app": "reoa"})
        if path == "/api/info":
            return self.send(200, {"running": tracker.crawl_running(), "schedule": scheduled_tasks()})
        if path == "/api/report.pdf":
            return self.send_report()
        if path == "/api/user-files":
            return self.send_user_files()
        if path in STATIC:
            name, ctype = STATIC[path]
            f = BASE / name
            if not f.exists():
                if name in ("status.js", "views_status.js"):
                    return self.send(200, b"", ctype)
                return self.send(404, {"error": "not found"})
            body = f.read_bytes()
            if name.endswith(".csv"):
                # Download with a recognisable name instead of showing it in the browser.
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Disposition", f'attachment; filename="korikath_{name}"')
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                return self.wfile.write(body)
            return self.send(200, body, ctype)
        self.send(404, {"error": "not found"})

    def send_report(self):
        """Make a PDF report (?user=Name for one contributor) and send it as a download. It is made in
        a temporary folder and nothing is kept here."""
        query = urllib.parse.parse_qs(self.path.partition("?")[2])
        user = (query.get("user") or [None])[0]
        try:
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
                pdf = report.make_report(user, tmp)
                body = pdf.read_bytes()
        except KeyError as e:
            return self.send(404, {"error": str(e.args[0])})
        except Exception as e:
            return self.send(500, {"error": str(e)})
        ascii_name = pdf.name.encode("ascii", "replace").decode().replace("?", "_")
        self.send_response(200)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Disposition",
                         f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{urllib.parse.quote(pdf.name)}")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_user_files(self):
        """Files one contributor uploaded to one category: ?category=...&user=...[&direct=1]. Without a
        user, everyone who uploaded to the category with their counts (user_files.py)."""
        query = urllib.parse.parse_qs(self.path.partition("?")[2])
        category = (query.get("category") or [""])[0]
        if not category.strip():
            return self.send(400, {"error": "Enter a category."})
        try:
            r = user_files.lookup(category, (query.get("user") or [None])[0], (query.get("direct") or ["0"])[0] != "1")
        except KeyError as e:
            return self.send(404, {"error": str(e.args[0])})
        except Exception as e:
            return self.send(500, {"error": str(e)})
        self.send(200, json.dumps(r, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def do_POST(self):
        global last_request
        last_request = time.monotonic()
        # The custom header can't be sent by another website without a CORS preflight, which this
        # server never approves, so only the dashboard itself can start crawls.
        if self.headers.get("X-Reoa") != "1":
            return self.send(403, {"error": "forbidden"})
        path = self.path.split("?", 1)[0]
        if path in ("/api/crawl/quick", "/api/crawl/full", "/api/crawl/usage"):
            mode = path.rsplit("/", 1)[1]
            if start_crawl(mode):
                return self.send(202, {"started": mode})
            return self.send(409, {"error": "A crawl is already running."})
        self.send(404, {"error": "not found"})


def already_running():
    try:
        with urllib.request.urlopen(URL + "api/ping", timeout=2) as r:
            return json.load(r).get("app") == "reoa"
    except Exception:
        return False


def watch_idle(server):
    while True:
        time.sleep(60)
        if time.monotonic() - last_request > IDLE_MINUTES * 60:
            server.shutdown()
            return


def main():
    open_browser = "--no-browser" not in sys.argv
    if already_running():
        if open_browser:
            webbrowser.open(URL)
        return
    if not tracker.DASHBOARD_FILE.exists():
        tracker.build_dashboard()
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    threading.Thread(target=watch_idle, args=(server,), daemon=True).start()
    if open_browser:
        threading.Timer(0.5, webbrowser.open, args=(URL,)).start()
    server.serve_forever()


if __name__ == "__main__":
    if sys.stdout is None:  # pythonw: nowhere to print, so keep errors in a log
        (BASE / "logs").mkdir(exist_ok=True)
        sys.stdout = sys.stderr = open(BASE / "logs" / "server.log", "a", encoding="utf-8", buffering=1)
    main()
