"""PDF statistics reports: the whole project, or one uploader. Every report is issued with an ID, a status
(FINAL or PROVISIONAL, decided by the checks in validate.py) and a fingerprint, and is recorded in the register of
issued reports (issuance.py). The PDF itself is not kept; the data and code it was made from are (archive.py).

The report is built as an HTML page (with charts drawn as SVG) and printed to PDF by Microsoft Edge or Google
Chrome in headless mode, so Bengali and other scripts are shaped correctly. Its figures are computed from the
saved data files (tracker.build_payload). Requests to Wikimedia: a live spot check of 30 files, the authors and
licences of the images shown, and the browser loading those thumbnails.

    python report.py                       project report, saved in your Downloads folder
    python report.py --user "Name"         one uploader's report
    python report.py --verify FILE.pdf     was this PDF issued by the tracker, and what is its status?
    python report.py --rebuild REPORT_ID   regenerate an issued report from its archive state and compare
    python report.py --register            list issued reports
    python report.py --supersede OLD_ID --by NEW_ID --reason "..."   record a correction
"""

import argparse
import hashlib
import html
import json
import math
import re
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
import urllib.parse
from datetime import date, datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
import tracker  # noqa: E402
from tracker import UTC, fmt_time  # noqa: E402



OUT_DIR = Path.home() / "Downloads"  # where the command line saves reports; the dashboard sends them straight to the browser
NO_WINDOW = 0x08000000
ACCENT, HUMAN, MUTED = "#000000", "#5c5c5c", "#bdbdbd"  # Swiss Mono: black, dark grey, light grey
KIND_NAMES = {"qi": "Quality images", "fp": "Featured pictures", "vi": "Valued images", "fm": "Featured media",
              "potd": "Picture of the Day on Commons", "motd": "Media of the Day on Commons"}
SPECIAL_WIKIS = {"commons.wikimedia.org": "Wikimedia Commons", "meta.wikimedia.org": "Meta-Wiki",
                 "www.wikidata.org": "Wikidata", "species.wikimedia.org": "Wikispecies",
                 "incubator.wikimedia.org": "Wikimedia Incubator", "www.mediawiki.org": "MediaWiki.org",
                 "wikisource.org": "Multilingual Wikisource", "outreach.wikimedia.org": "Wikimedia Outreach",
                 "wikimania.wikimedia.org": "Wikimania", "foundation.wikimedia.org": "Wikimedia Foundation"}
PROJECTS = {"wikipedia": "Wikipedia", "wiktionary": "Wiktionary", "wikisource": "Wikisource", "wikiquote": "Wikiquote",
            "wikibooks": "Wikibooks", "wikinews": "Wikinews", "wikiversity": "Wikiversity",
            "wikivoyage": "Wikivoyage", "wikimedia": "Wikimedia"}


# ------------------------------------------------------------------- formatting


def esc(s):
    return html.escape(str(s), quote=True)


def fmt(n):
    if n is None:
        return "—"
    return f"{round(n):,}" if isinstance(n, float) else f"{n:,}"


def pct(a, b):
    if not b:
        return "—"
    return f"{100 * a / b:.{2 if a / b < 0.01 else 1}f}%"


def big(n):
    if n >= 1e9:
        return f"{n / 1e9:.2f} billion"
    if n >= 1e6:
        return f"{n / 1e6:.{1 if n >= 1e7 else 2}f} million"
    return fmt(n)


def nbytes(b):
    for limit, unit, digits in ((1e12, "TB", 2), (1e9, "GB", 1), (1e6, "MB", 1), (1e3, "KB", 0)):
        if b >= limit:
            return f"{b / limit:.{digits}f} {unit}"
    return f"{b} bytes"


def day(iso):
    """'2026-10-23T19:35:08Z' or '2026-10-23' -> '23 Oct 2026' (UTC)."""
    if not iso:
        return "—"
    if len(iso) == 10:
        d = date.fromisoformat(iso)
        return f"{d.day} {d:%b %Y}"
    t = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(UTC)
    return f"{t.day} {t:%b %Y}"


def views_period(V):
    """'Jan 2015 – Sep 2026': the complete months view totals cover (tracker.views_cutoff)."""
    if not V.get("cutoff_month"):
        return "no complete month yet"
    return f"{month_name(V.get('totals_from') or '2015-01')} – {month_name(V['cutoff_month'])}"


def month_name(m):
    return datetime.strptime(m, "%Y-%m").strftime("%b %Y")


def opted_out():
    """Uploaders who asked not to be named (config.json: contributor_opt_out). They are counted in every total
    but not named in any list, and have no report of their own."""
    return set((tracker.load_json(BASE / "config.json", {}) or {}).get("contributor_opt_out") or [])


def shown(name):
    return "(name withheld at the uploader's request)" if name in opted_out() else name


def short(title):
    return title.split(":", 1)[1] if title.startswith(("File:", "Category:")) else title


def commons(title):
    return "https://commons.wikimedia.org/wiki/" + urllib.parse.quote(title.replace(" ", "_"))


def link(title, text=None):
    return f'<a href="{esc(commons(title))}">{esc(text if text is not None else short(title))}</a>'


def edit_text(e):
    """Who made a change and its cause, with the diff or log link (attribution.py)."""
    if not e or not e.get("how"):
        return "—"
    how, user = e["how"], esc(e.get("user") or "")
    url = e.get("url")
    a = (lambda label: f' · <a href="{esc(url)}">{label}</a>') if url else (lambda label: "")
    if how.startswith("not looked up"):
        return "Not looked up" + a("history")
    if how.startswith("not found"):
        return "No edit names the file" + a("edits") if how.startswith("not found by name") else "Not found" + a("history")
    if how == "page deleted":
        return f"<b>Page deleted</b> by {user}" + a("log")
    if how == "deleted":
        return f"<b>Deleted</b> by {user}" + a("log")
    if how.startswith("page moved"):
        return f"<b>{esc(how[0].upper() + how[1:])}</b> by {user}" + a("log")
    if how == "uploaded":
        return f"Uploaded by {user}" + a("upload")
    if how == "page turned into a redirect":
        return f"<b>Page turned into a redirect</b> by {user}" + a("diff")
    via = "" if how == "page edit" else f" ({esc(how)})"
    return f"{user}{via}" + a("diff")


def usage_edit(row):
    """The attribution columns of a usage-log row (changed_by, changed_at_utc, how, edit_link)."""
    return {"user": row[9], "timestamp": row[10], "how": row[11], "url": row[12]} if len(row) > 12 and row[11] else None


def wiki_label(domain, languages):
    if domain in SPECIAL_WIKIS:
        return SPECIAL_WIKIS[domain]
    code, _, rest = domain.partition(".")
    project = rest.split(".")[0]
    lang = "Simple English" if code == "simple" else languages.get(code, code)
    return f"{lang} {PROJECTS.get(project, project)}"


def file_stamp(when):
    """Time for file names: 2026-10-30_1715Z (UTC)."""
    t = when.astimezone(UTC)
    return f"{t:%Y-%m-%d}_{t:%H%M}Z"


# ------------------------------------------------------------------------ charts


def nice_step(top, ticks=4):
    raw = max(top, 1) / ticks
    mag = 10 ** math.floor(math.log10(raw))
    return next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)


def axis_label(v):
    if v >= 1e6:
        return f"{v / 1e6:g}M"
    if v >= 1e3:
        return f"{v / 1e3:g}k"
    return f"{v:g}"


def month_range(first, last):
    y, m = map(int, first.split("-"))
    y1, m1 = map(int, last.split("-"))
    out = []
    while (y, m) <= (y1, m1):
        out.append(f"{y}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def chart(series, kind="bars", height=170, width=720, colors=(ACCENT,), legend=None):
    """Monthly chart as inline SVG. series: [(YYYY-MM, value, [second value])], months in order, gaps
    filled with 0. kind "bars" draws a bar per month (a second value is drawn inside the first, for
    human views inside all views); "area" draws a running total."""
    if not series:
        return '<p class="sub">No data yet.</p>'
    values = dict((s[0], s[1:]) for s in series)
    months = month_range(series[0][0], series[-1][0])
    rows, prev = [], [0] * (len(series[0]) - 1)
    for m in months:
        # A month with nothing new is 0 bars, but keeps the running total where it was.
        prev = values.get(m) or (prev if kind == "area" else [0] * len(prev))
        rows.append([m, *prev])
    left, right, top, bottom = 48, 8, 10, 22
    w, h = width - left - right, height - top - bottom
    peak = max(r[1] for r in rows) or 1
    step = nice_step(peak)
    ymax = step * math.ceil(peak / step)
    x = lambda i: left + w * i / len(rows)  # noqa: E731
    y = lambda v: top + h * (1 - v / ymax)  # noqa: E731
    parts = [f'<svg class="chart" viewBox="0 0 {width} {height}" width="100%" role="img">']
    v = 0
    while v <= ymax + 1e-9:
        parts.append(f'<line x1="{left}" x2="{width - right}" y1="{y(v):.1f}" y2="{y(v):.1f}" stroke="#e0e0e0"/>'
                     f'<text x="{left - 6}" y="{y(v) + 4:.1f}" text-anchor="end">{axis_label(v)}</text>')
        v += step
    years = sorted({m[:4] for m in months})
    every = max(1, math.ceil(len(years) / 12))
    for i, m in enumerate(months):
        # Label each January (and the first month, unless a January follows too closely to fit both).
        if m.endswith("-01") or (i == 0 and not any(x.endswith("-01") for x in months[1:max(2, len(months) // 15)])):
            if (int(m[:4]) - int(years[0])) % every == 0:
                parts.append(f'<line x1="{x(i):.1f}" x2="{x(i):.1f}" y1="{top + h}" y2="{top + h + 4}" stroke="#000000"/>'
                             f'<text x="{x(i) + 2:.1f}" y="{height - 6}">{m[:4]}</text>')
    if kind == "bars":
        bw = max(w / len(rows) - (1 if len(rows) < 120 else 0.3), 0.6)
        for i, r in enumerate(rows):
            for j, val in enumerate(r[1:]):
                if val:
                    parts.append(f'<rect x="{x(i):.2f}" y="{y(val):.2f}" width="{bw:.2f}" height="{top + h - y(val):.2f}" '
                                 f'fill="{colors[min(j, len(colors) - 1)]}"/>')
    else:
        pts = " ".join(f"{x(i + 1):.1f},{y(r[1]):.1f}" for i, r in enumerate(rows))
        parts.append(f'<polygon points="{left},{top + h} {left},{y(rows[0][1]):.1f} {pts} {x(len(rows)):.1f},{top + h}" '
                     f'fill="{colors[0]}" fill-opacity="0.18"/>'
                     f'<polyline points="{left},{y(rows[0][1]):.1f} {pts}" fill="none" stroke="{colors[0]}" stroke-width="2"/>')
    parts.append(f'<line x1="{left}" x2="{width - right}" y1="{top + h}" y2="{top + h}" stroke="#000000"/></svg>')
    if legend:
        parts.append('<div class="legend">' + "".join(
            f'<span><i style="background:{c}"></i>{esc(t)}</span>' for c, t in zip(colors, legend)) + "</div>")
    return "".join(parts)


def running_total(series):
    total, out = 0, []
    for m, n in series:
        total += n
        out.append((m, total))
    return out


# ------------------------------------------------------------------ page parts


def kpis(items):
    """items: (label, value, note) with value and note as HTML."""
    return '<div class="kpis">' + "".join(
        f'<div class="kpi"><div class="label">{esc(label)}</div><div class="value">{value}</div>'
        f'{f"<div class=note>{note}</div>" if note else ""}</div>' for label, value, note in items if value is not None) + "</div>"


def table(head, rows, num=(), note=None):
    """head: column titles; rows: lists of cell HTML; num: indexes of right-aligned number columns."""
    if not rows:
        return '<p class="sub">None.</p>'
    th = "".join(f'<th{" class=num" if i in num else ""}>{esc(h)}</th>' for i, h in enumerate(head))
    body = "".join("<tr>" + "".join(f'<td{" class=num" if i in num else ""}>{c}</td>' for i, c in enumerate(r)) + "</tr>"
                   for r in rows)
    extra = f'<p class="sub">{note}</p>' if note else ""
    return f"<table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table>{extra}"


def section(title, body, intro=None):
    src = data_line(*SOURCES[title]) if title in SOURCES else ""
    return f'<section><h2>{esc(title)}</h2>{f"<p class=intro>{intro}</p>" if intro else ""}{body}{src}</section>'


RENDER = {"manifest": None, "credits": None, "wanted": None, "data": None}  # the report being rendered (render_html)


def thumbs(titles):
    """Thumbnails, each with its author and licence; a file without them, or marked as showing identifiable people,
    is shown as a title only (fetch_credits)."""
    titles = list(titles)
    if RENDER["wanted"] is not None:
        RENDER["wanted"].update(titles)
    credits = RENDER["credits"] or {}
    cells = []
    for t in titles:
        c = credits.get(t) or {}
        if c.get("show"):
            lic = (f'<a href="{esc(c["licence_url"])}">{esc(c["licence"])}</a>' if c.get("licence_url") else esc(c["licence"]))
            img = (f'<img src="https://commons.wikimedia.org/wiki/Special:FilePath/{urllib.parse.quote(short(t).replace(" ", "_"))}?width=320" '
                   f'alt="" onerror="this.replaceWith(Object.assign(document.createElement(\'div\'),{{className:\'noimg\',textContent:\'No preview\'}}))">')
            cap = f'{esc(short(t))}<span class="credit">{esc(c["author"])} · {lic}</span>'
        else:
            img = f'<div class="noimg">Image not shown<br>({esc(c.get("reason") or "credit not checked")})</div>'
            cap = esc(short(t))
        cells.append(f"<figure>{img}<figcaption>{cap}</figcaption></figure>")
    return f'<div class="thumbs">{"".join(cells)}</div>'


FONT_LINK = '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wght@400;600;700;800&display=swap">'
CSS = """
@page { size: A4; margin: 16mm 14mm 18mm;
  @bottom-left { content: "Project Korikath statistics"; font: 600 7.5pt 'Archivo', 'Helvetica Neue', Arial, sans-serif; color: #000000; text-transform: uppercase; letter-spacing: .06em; }
  @bottom-right { content: "Page " counter(page) " of " counter(pages); font: 7.5pt 'Archivo', 'Helvetica Neue', Arial, sans-serif; color: #000000; } }
* { box-sizing: border-box; border-radius: 0 !important; }
body { margin: 0; color: #000000; font: 9.5pt/1.45 'Archivo', 'Helvetica Neue', Arial, 'Nirmala UI', 'Noto Sans Bengali', sans-serif;
  -webkit-print-color-adjust: exact; print-color-adjust: exact; }
a { color: #000000; text-decoration: underline; text-decoration-thickness: .5pt; text-underline-offset: 1.5pt; }
header { border-bottom: 3pt solid #000000; padding-bottom: 10px; margin-bottom: 14px; }
header .kicker { color: #000000; font-weight: 700; letter-spacing: .12em; text-transform: uppercase; font-size: 7.5pt; }
h1 { font-size: 26pt; font-weight: 800; text-transform: uppercase; letter-spacing: -0.01em; margin: 4px 0 10px; line-height: 1; }
h2, h3 { break-after: avoid; }
h2 { font-size: 9pt; font-weight: 700; text-transform: uppercase; letter-spacing: .12em; margin: 0 0 8px; color: #000000;
  border-top: 2pt solid #000000; padding-top: 5px; }
h3 { font-size: 9.5pt; font-weight: 700; margin: 12px 0 4px; }
.meta { display: grid; grid-template-columns: max-content 1fr; gap: 1px 14px; font-size: 9pt; }
.meta b { color: #000000; font-weight: 700; text-transform: uppercase; letter-spacing: .05em; font-size: 7.5pt; padding-top: 1.5pt; }
section { margin: 18px 0 0; }
section.break { break-before: page; }
.intro, .sub { color: #5c5c5c; }
.intro { margin: 0 0 8px; }
.sub { font-size: 8.5pt; margin: 4px 0; }
.kpis { display: grid; grid-template-columns: repeat(4, 1fr); gap: 0; margin: 6px 0 4px; border-top: .75pt solid #000000; border-left: .75pt solid #000000; }
.kpi { border-right: .75pt solid #000000; border-bottom: .75pt solid #000000; padding: 6px 8px; background: #ffffff; break-inside: avoid; }
.kpi .label { color: #000000; font-size: 6.5pt; font-weight: 700; text-transform: uppercase; letter-spacing: .06em; }
.kpi .value { font-size: 16pt; font-weight: 800; line-height: 1.15; letter-spacing: -0.01em; font-variant-numeric: tabular-nums; }
.kpi .note { color: #5c5c5c; font-size: 7pt; line-height: 1.3; margin-top: 2px; }
table { width: 100%; border-collapse: collapse; font-size: 8.5pt; margin: 4px 0; }
thead { display: table-header-group; }
tr { break-inside: avoid; }
th { text-align: left; color: #000000; font-weight: 700; font-size: 7pt; text-transform: uppercase; letter-spacing: .05em;
  border-bottom: 1.25pt solid #000000; padding: 3px 5px; }
td { border-bottom: .5pt solid #d4d4d4; padding: 2.5px 5px; vertical-align: top; overflow-wrap: break-word; }
.nw { white-space: nowrap; }
.num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
.cols { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
.cols > div { min-width: 0; }
svg.chart { display: block; margin: 4px 0; font: 7pt 'Archivo', 'Helvetica Neue', Arial, sans-serif; fill: #5c5c5c; }
.legend { font-size: 7.5pt; color: #000000; display: flex; gap: 14px; }
.legend i { display: inline-block; width: 9px; height: 9px; margin-right: 4px; vertical-align: -1px; border: .5pt solid #000000; }
.chartbox { break-inside: avoid; }
.thumbs { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin: 6px 0; }
.thumbs figure { margin: 0; break-inside: avoid; }
.thumbs img, .noimg { width: 100%; height: 95px; object-fit: cover; background: #f0f0f0; display: block; }
.noimg { display: flex; align-items: center; justify-content: center; color: #5c5c5c; font-size: 7.5pt; text-align: center; border: .5pt solid #bdbdbd; }
.thumbs figcaption { font-size: 7pt; color: #000000; line-height: 1.25; margin-top: 3px;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.removed { color: #000000; }
h3.removed { text-decoration: underline; text-decoration-thickness: 1.5pt; }
.pill { display: inline-block; font-size: 7pt; padding: 0 5px; border: .5pt solid #000000; color: #000000; font-weight: 600; }
.badge { display: inline-block; font-weight: 800; padding: 1px 8px; letter-spacing: .1em; text-transform: uppercase; font-size: 8pt; }
.badge.final { background: #000000; color: #ffffff; }
.badge.provisional { background: #ffffff; color: #000000; border: 1.25pt solid #000000; }
.reasons { border: 1.5pt solid #000000; background: #ffffff; padding: 6px 10px; margin-top: 10px; font-size: 8.5pt; }
.reasons > b { display: inline-block; background: #000000; color: #ffffff; padding: 1px 6px; margin: -6px 0 4px -10px; text-transform: uppercase; letter-spacing: .06em; font-size: 7.5pt; }
.reasons ul { margin: 3px 0 0 16px; padding: 0; }
.mono { font-family: Consolas, 'Courier New', monospace; font-size: 8pt; overflow-wrap: anywhere; }
.src { color: #5c5c5c; font-size: 7pt; border-top: .5pt solid #000000; margin-top: 6px; padding-top: 3px; }
.src b { color: #000000; text-transform: uppercase; letter-spacing: .05em; font-size: 6.5pt; }
.credit { display: block; font-size: 6.5pt; color: #5c5c5c; }
.trust p { margin: 0 0 4px; }
.trust h3 { margin: 8px 0 2px; }
.res { text-transform: uppercase; letter-spacing: .05em; font-size: 7.5pt; }
.res.passed { color: #000000; font-weight: 600; }
.res.failed { background: #000000; color: #ffffff; font-weight: 800; padding: 0 4px; }
.res.notrun { color: #5c5c5c; text-decoration: line-through; }
.watermark { position: fixed; top: 42%; left: 0; right: 0; text-align: center; transform: rotate(-30deg);
  font-size: 54pt; font-weight: 800; color: rgba(0, 0, 0, .07); letter-spacing: .08em; z-index: -1; }
"""


def page(title, kicker, meta_rows, body, manifest=None):
    if manifest is None:  # a page without an issued report (only used by tests)
        meta = "".join(f"<b>{esc(k)}</b><span>{v}</span>" for k, v in meta_rows)
        return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{esc(title)}</title>'
                f"{FONT_LINK}<style>{CSS}</style></head><body><header><div class=kicker>{esc(kicker)}</div><h1>{esc(title)}</h1>"
                f'<div class="meta">{meta}</div></header>{body}</body></html>')
    m = manifest
    css = CSS.replace('content: "Project Korikath statistics"', f'content: "{m["report_id"]} · {m["status"]}"')
    mark = '<div class="watermark">PROVISIONAL</div>' if m["status"] != "FINAL" else ""
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{esc(title)} · {esc(m["report_id"])}</title>'
            f'<meta name="author" content="{esc((m.get("issuer") or {}).get("name") or "Project Korikath")}">'
            f'<meta name="description" content="{esc(m["report_id"])} {esc(m["status"])} {esc(m["fingerprint"])}">'
            f"{FONT_LINK}<style>{css}</style></head><body>{mark}<header><div class=kicker>{esc(kicker)}</div><h1>{esc(title)}</h1>"
            f"{cover(m, meta_rows)}</header>{trust_section(m)}{checks_section(m)}"
            f"{body}{appendices(m, RENDER['data'])}</body></html>")


# ------------------------------------------------------------------------ data


def dashboard_data():
    """Every figure, computed afresh from the saved data files (the same computation as the dashboard's), so a
    report never depends on when the dashboard was last rebuilt."""
    data = tracker.build_payload(write=False)
    if not data or not data.get("snapshot"):
        raise RuntimeError("There is no crawl yet, so there is nothing to report. Run a crawl first.")
    return data


def main_page_rows(A, titles=None):
    """Main-page features per file, Commons and other wikis together, as in the dashboard's mainPageSummary.
    Returns (rows, wikis) with rows: {title, commons: [days], wikis: {wiki: times}, times}."""
    per = {}
    for kind in ("potd", "motd"):
        for t, days in (A.get("days") or {}).get(kind, []):
            if titles is None or t in titles:
                per.setdefault(t, {"title": t, "commons": set(), "dates": {}})["commons"].update(days)
    for t, wiki, kind, pagename, d in A.get("elsewhere") or []:
        if titles is None or t in titles:
            per.setdefault(t, {"title": t, "commons": set(), "dates": {}})["dates"].setdefault(wiki, []).append(d or "")
    rows = []
    for r in per.values():
        r["wikis"] = {w: tracker.appearances(d) for w, d in r["dates"].items()}
        r["times"] = len(r["commons"]) + sum(r["wikis"].values())
        rows.append(r)
    wikis = {w for r in rows for w in r["wikis"]} | ({"commons.wikimedia.org"} if any(r["commons"] for r in rows) else set())
    rows.sort(key=lambda r: (-r["times"], r["title"]))
    return rows, wikis


def monthly_from_days(days):
    per = {}
    for d, n in days:
        per[d[:7]] = per.get(d[:7], 0) + n
    return sorted(per.items())


def per_year(series):
    out = {}
    for m, n in series:
        out[m[:4]] = out.get(m[:4], 0) + n
    return sorted(out.items())


# --------------------------------------------------------------- project report


def project_report(D, generated, manifest=None):
    snap, hist = D["snapshot"], D.get("history") or []
    latest = hist[-1] if hist else {}
    langs = D.get("languages") or {}
    M, A, V, U, C, P = (D.get(k) for k in ("media", "assessments", "views", "usage", "contributors", "places"))
    total = snap["total_files"]
    meta = [("Report generated", esc(fmt_time(generated))),
            ("Built on the crawl of", f"{esc(fmt_time(snap['taken_at']))} ({esc(latest.get('mode', ''))} crawl)"
             if latest.get("taken_at") == snap["taken_at"] else esc(fmt_time(snap["taken_at"]))),
            ("Root category", link(snap["root"], snap["root"]))]
    if U:
        meta.append(("On-wiki usage checked", esc(fmt_time(U["checked_at"]))))
    if A:
        meta.append(("Assessments checked", esc(fmt_time(A["checked_at"]))))
    if V:
        meta.append(("File requests counted", f"{esc(views_period(V))} (complete months) · {fmt(V['with_history'])} of {fmt(V['files'])} files"
                     + (" (still being collected)" if V["with_history"] < V["files"] else "")))
    body = []

    # How current and complete each dataset is (before any figure, so every figure can be read against it)
    if D.get("data_status"):
        body.append(section("Data used in this report", table(
            ["Data", "As of (UTC)", "Files covered", "Share", "Notes"],
            [[esc(r["label"]), esc(r["as_of"] if r["as_of"] and len(str(r["as_of"])) == 10 else fmt_time(r["as_of"]) if r["as_of"] else "—"),
              (fmt(r["covered"]) if r["covered"] is not None else "—") + (f" of {fmt(r['of'])}" if r["of"] is not None else ""),
              pct(r["covered"], r["of"]) if r["share"] is not None else "—", esc(r.get("note") or "")]
             for r in D["data_status"]], num=(2, 3)),
            "Each kind of data was collected at its own time. Where a dataset does not cover every file of the crawl, "
            "the figures built on it are based on the files it covers."
            + (f" Built by Korikath Tracker {esc(D['build']['tool_version'])}, method {esc(D['build']['method_version'])}"
               + (f", code commit {esc(D['build']['git_commit'])}" if D['build'].get('git_commit') else "") + "."
               if D.get("build") else "")))

    # Headline numbers
    cards = [("Total files", fmt(total), None), ("Categories in tree", fmt(snap["total_categories"]), None)]
    if M:
        non = M["audio"] + M["video"] + M["document"] + M["other"]
        cards.append(("Non-image files", fmt(non), " · ".join(f"{fmt(M[k])} {k}" for k in ("audio", "video", "document", "other") if M[k])))
    if A:
        c = A["counts"]
        cards.append(("Quality · Featured · Valued", f"{fmt(c.get('qi', 0))} · {fmt(c.get('fp', 0))} · {fmt(c.get('vi', 0))}",
                      f"{pct(A['any'], A['total_files'])} of files have one" + (f" · {fmt(c['fm'])} featured media" if c.get("fm") is not None else "")))
        rows, wikis = main_page_rows(A)
        if rows:
            cards.append(("Times on a main page", fmt(sum(r["times"] for r in rows)),
                          f"{fmt(len(rows))} files on the main pages of {fmt(len(wikis))} wikis"))
    if V:
        cards.append(("File requests" if V["with_history"] >= V["files"] else "File requests so far", big(V["all"]),
                      f"{big(V['human'])} not flagged as automated ({pct(V['human'], V['all'])}) · {esc(views_period(V))}"))
    if U:
        cards += [("Files used on other wikis", fmt(U["all"]["files_used"]), f"{pct(U['all']['files_used'], total)} of all files"),
                  ("Pages using the files", fmt(U["all"]["pages"]), f"on {fmt(U['all']['wikis'])} wikis · {fmt(U['all']['usages'])} uses")]
    if C:
        cut = datetime.fromisoformat(snap["taken_at"]).timestamp() - 30 * 86400
        new = sum(1 for u in C["users"] if datetime.fromisoformat(u[2].replace("Z", "+00:00")).timestamp() >= cut)
        cards.append(("Uploaders", fmt(len(C["users"])), f"{fmt(new)} new in the last 30 days"))
    if P:
        cards.append(("Geocoded files", fmt(P["geocoded"]), f"{pct(P['geocoded'], P['files'])} of files"))
        if P.get("size"):
            cards.append(("Collection size", nbytes(P["size"]["total"]), f"average {nbytes(P['size']['total'] / P['size']['files'])} per file"))
        if P.get("gap"):
            cards.append(("Median take-to-upload gap", f"{fmt(P['gap']['median'])} days", f"from {fmt(P['gap']['files'])} files with a camera date"))
    body.append(section("Headline numbers", kpis(cards)))

    # Growth
    G = D.get("growth")
    if G:
        monthly = monthly_from_days(G["days"])
        years = per_year(monthly)
        body.append(section("Growth of the collection",
                            '<div class="chartbox"><h3>Files in the project, by upload month (running total)</h3>'
                            + chart(running_total(monthly), "area") + "<h3>Files uploaded per month</h3>" + chart(monthly) + "</div>"
                            + table(["Year", "Files uploaded", "Running total"],
                                    [[y, fmt(n), fmt(sum(v for yy, v in years if yy <= y))] for y, n in years], num=(1, 2)),
                            "Files now in the project, by the date they were first uploaded (UTC)."))
    if M:
        kinds = [(k, M[k]) for k in ("image", "audio", "video", "document", "other") if M[k]]
        sizes = (P or {}).get("size") or {}
        rows = [[k.capitalize(), fmt(n), pct(n, total), nbytes(sizes["by_kind"][k][1]) if k in sizes.get("by_kind", {}) else "—"] for k, n in kinds]
        body.append(section("Kinds of media", table(["Kind", "Files", "Share", "Total size"], rows, num=(1, 2, 3))))

    # Category tree
    cats = snap["categories"]
    root = next((c for c in cats.values() if c["depth"] == 0), None)
    top = sorted(((cid, c) for cid, c in cats.items() if c["depth"] == 1), key=lambda kv: -kv[1]["total"])
    body.append(section("Category tree", table(
        ["Category", "Files below it", "Share", "Subcategories directly in it"],
        [[link(c["title"]), fmt(c["total"]), pct(c["total"], total), fmt(sum(1 for x in cats.values() if cid in x["parents"]))]
         for cid, c in top], num=(1, 2, 3)),
        f"{fmt(snap['total_categories'])} categories under {link(root['title'] if root else snap['root'])}. "
        "The main branches below it; a file in several branches counts in each."))

    # Changes
    reps = D.get("reports") or []
    rep = reps[-1] if reps and reps[-1]["taken_at"] == snap["taken_at"] else None
    parts = []
    if rep:
        ch = rep["changes"]
        labels = [("files_removed", "Files removed"), ("files_added", "Files added"), ("files_moved", "Files moved inside the tree"),
                  ("files_renamed", "Files renamed"), ("categories_removed", "Categories removed"), ("categories_added", "Categories added"),
                  ("categories_moved", "Categories moved"), ("categories_renamed", "Categories renamed")]
        parts.append(f"<h3>In this crawl, compared with {esc(fmt_time(rep['compared_with']))}</h3>"
                     + table(["Change", "Count"], [[t, fmt(len(ch[k]))] for k, t in labels], num=(1,)))
        if ch["files_removed"]:
            parts.append("<h3 class=removed>Files removed in this crawl</h3>" + table(
                ["File", "What happened", "Was in", "By"],
                [[link(f["title"]), "deleted" if f.get("status") == "deleted" else "moved outside the tree",
                  esc("; ".join(short(x) for x in f.get("was_in", []))), edit_text(f.get("by"))] for f in ch["files_removed"][:60]],
                note=f"First 60 of {fmt(len(ch['files_removed']))}." if len(ch["files_removed"]) > 60 else None))
    rf = D.get("removed_files") or {}
    parts.append(f'<p class="sub">Files that have left the project across all saved crawls: <b>{fmt(len(rf.get("rows") or []))}</b>.</p>')
    if hist:
        parts.append("<h3>Saved crawls</h3>" + table(
            ["Crawl", "Kind", "Files", "Categories", "Removed", "Added", "Moved"],
            [[esc(fmt_time(h["taken_at"])), esc(h.get("mode", "")), fmt(h["total_files"]), fmt(h["total_categories"]),
              fmt((h.get("counts") or {}).get("files_removed")), fmt((h.get("counts") or {}).get("files_added")),
              fmt((h.get("counts") or {}).get("files_moved"))] for h in reversed(hist[-15:])], num=(2, 3, 4, 5, 6)))
    body.append(section("Changes", "".join(parts)))

    # Views
    if V:
        months = [(m, a, h) for m, a, h, _ in V["months"] if V.get("cutoff_month") and m <= V["cutoff_month"]]
        years = {}
        for m, a, h in months:
            y = years.setdefault(m[:4], [0, 0])
            y[0] += a
            y[1] += h
        partial = V["with_history"] < V["files"]
        body.append(section("File requests", kpis([
            ("All file requests" + (" so far" if partial else ""), big(V["all"]), fmt(V["all"])),
            ("Not flagged as automated", big(V["human"]), pct(V["human"], V["all"]) + " of all requests"),
            ("Flagged as automated", big(V["all"] - V["human"]), pct(V["all"] - V["human"], V["all"])),
            ("Files counted", fmt(V["with_history"]), f"of {fmt(V['files'])}, through {esc(views_period(V))}")])
            + '<div class="chartbox"><h3>File requests per month</h3>' + chart(months, colors=(MUTED, ACCENT), legend=["All file requests", "Not flagged as automated"])
            + "<h3>File requests (running total)</h3>" + chart(running_total([(m, a) for m, a, _ in months]), "area") + "</div>"
            + table(["Year", "All file requests", "Not flagged as automated", "Share not flagged"],
                    [[y, fmt(a), fmt(h), pct(h, a)] for y, (a, h) in sorted(years.items())], num=(1, 2, 3))
            + "<h3>Most requested files</h3>" + thumbs([r[0] for r in V["top_files"][:8]])
            + table(["File", "Uploaded by", "All file requests", "Not flagged as automated"],
                    [[link(t), esc(shown(u) if u else "—"), fmt(a), fmt(h)] for t, a, h, u in V["top_files"][:25]], num=(2, 3))
            + "<h3>Most requested categories</h3>"
            + table(["Category", "Files", "All file requests", "Not flagged as automated"],
                    [[link(t), fmt(n), fmt(a), fmt(h)] for t, a, h, n in V["categories"][:20]], num=(1, 2, 3)),
            f"How often each file itself was loaded (Wikimedia media requests). All figures cover {esc(views_period(V))}: complete "
            "months that every file has been counted through."
            + (f" The {fmt(V['after_cutoff'][0])} file requests counted after that are partial and not included." if (V.get("after_cutoff") or [0])[0] else "")
            + (f" Still collecting: {fmt(V['with_history'])} of {fmt(V['files'])} files counted so far." if partial else "")))
        body[-1] = body[-1].replace("<section>", '<section class="break">', 1)

    # On-wiki usage
    if U:
        a, cn = U["all"], U["content"]
        body.append(section("On-wiki usage", kpis([
            ("Files used", fmt(a["files_used"]), f"{pct(a['files_used'], total)} of all files"),
            ("Uses", fmt(a["usages"]), f"{fmt(cn['usages'])} in articles and other content pages"),
            ("Pages", fmt(a["pages"]), f"{fmt(cn['pages'])} content pages"),
            ("Wikis", fmt(a["wikis"]), f"{fmt(cn['wikis'])} with content-page uses")])
            + "<h3>Wikis using the most files</h3>" + table(
                ["Wiki", "Uses", "Files used"],
                [[esc(wiki_label(w, langs)), fmt(n), fmt(f)] for w, n, f in a["by_wiki"][:25]], num=(1, 2),
                note=f"Top 25 of {fmt(len(a['by_wiki']))} wikis.")
            + '<div class="cols"><div><h3>Most used files</h3>' + table(
                ["File", "Uses", "Wikis"], [[link(r[0]), fmt(r[2]), fmt(r[3])] for r in a["top_files"][:20]], num=(1, 2))
            + "</div><div><h3>Uploaders whose files are used most</h3>" + table(
                ["Uploader", "Files used", "Uses"], [[esc(shown(r[0])), fmt(r[2]), fmt(r[3])] for r in sorted(a["by_uploader"], key=lambda r: -r[3])[:20]], num=(1, 2))
            + "</div></div><h3>Categories with the most used files</h3>" + table(
                ["Category", "Files", "Files used", "Share used", "Uses"],
                [[link(r[0]), fmt(r[1]), fmt(r[2]), pct(r[2], r[1]), fmt(r[3])] for r in a["by_category"][:20]], num=(1, 2, 3, 4)),
            "Pages on other Wikimedia wikis (not Commons) that show the project's files."))
        checks = U.get("checks") or []
        if checks and (checks[-1]["removed"] or checks[-1]["added"]):
            ck = checks[-1]
            rows_ = [[esc(r[1]), link(r[2], short(r[2])), esc(wiki_label(r[4], langs)), esc(r[6]), edit_text(usage_edit(r))]
                     for r in (ck["removed"] + ck["added"])[:60]]
            n = ck["removed_n"] + ck["added_n"]
            body.append(section("Latest usage changes", table(
                ["Change", "File", "Wiki", "Page", "Changed by / cause"], rows_,
                note=f"First 60 of {fmt(n)}." if n > 60 else None),
                f"Uses added or removed since the previous check, found by the check of {esc(fmt_time(ck['checked_at']))}. "
                "For each, the edit that made the change (the page itself, a template it uses, or its Wikidata item), "
                "or the deletion or move of the page."))
        hist_u = U.get("history") or []
        if hist_u:
            body.append(section("Usage checks", table(
                ["Checked", "Files used", "Uses", "Pages", "Wikis", "Added", "Removed"],
                [[esc(fmt_time(h["checked_at"])), fmt(h["files_used"]), fmt(h["usages"]), fmt(h.get("pages")), fmt(h["wikis"]),
                  fmt(h.get("added")), fmt(h.get("removed"))] for h in reversed(hist_u[-12:])], num=(1, 2, 3, 4, 5, 6))))

    # Assessments and main pages
    if A:
        c = A["counts"]
        monthly = {}
        for k, rows_ in A["monthly"].items():
            for m, n in rows_:
                monthly.setdefault(m, {})[k] = n
        by_user = sorted(A["by_user"].items(), key=lambda kv: (-sum(kv[1].get(k, 0) for k in ("qi", "fp", "vi", "fm")), kv[0]))
        mp_rows, mp_wikis = main_page_rows(A)
        wiki_times = {}
        for r in mp_rows:
            for w, n in r["wikis"].items():
                wiki_times[w] = wiki_times.get(w, 0) + n
            if r["commons"]:
                wiki_times["commons.wikimedia.org"] = wiki_times.get("commons.wikimedia.org", 0) + len(r["commons"])
        body.append(section("Quality, featured and valued", kpis([
            (KIND_NAMES[k], fmt(c.get(k)), None) for k in ("qi", "fp", "vi", "fm", "potd", "motd") if k in c])
            + f'<p class="sub">{fmt(A["any"])} files ({pct(A["any"], A["total_files"])}) are quality, featured or valued. '
              "Counted from the Commons-wide categories, not the project's own.</p>"
            + "<h3>Files entering the assessment categories, per month</h3>"
            + '<p class="sub">By the month each file was added to the Commons-wide category, which is usually, but not '
              'always, the month it was promoted (limitation L12).</p>' + table(
                ["Month", "Quality", "Featured", "Valued", "Featured media"],
                [[month_name(m), *(fmt(monthly[m].get(k, 0)) for k in ("qi", "fp", "vi", "fm"))] for m in sorted(monthly, reverse=True)],
                num=(1, 2, 3, 4))
            + "<h3>Uploaders with assessed files</h3>" + table(
                ["Uploader", "Quality", "Featured", "Valued", "Featured media", "POTD", "MOTD"],
                [[esc(shown(u)), *(fmt(n.get(k, 0)) for k in ("qi", "fp", "vi", "fm", "potd", "motd"))] for u, n in by_user[:30]],
                num=(1, 2, 3, 4, 5, 6), note=f"Top 30 of {fmt(len(by_user))}." if len(by_user) > 30 else None)))
        if mp_rows:
            body.append(section("On the main page", kpis([
                ("Times on a main page", fmt(sum(r["times"] for r in mp_rows)), "each wiki and day counts once"),
                ("Files", fmt(len(mp_rows)), None), ("Wikis", fmt(len(mp_wikis)), None)])
                + "<h3>Wikis that have featured the project's media</h3>"
                + f"<p>Total {fmt(len(mp_wikis))} wikis: " + esc(", ".join(sorted(wiki_label(w, langs) for w in mp_wikis))) + ".</p>"
                + table(["Wiki", "Times"], [[esc(wiki_label(w, langs)), fmt(n)] for w, n in sorted(wiki_times.items(), key=lambda kv: (-kv[1], kv[0]))], num=(1,))
                + "<h3>Files</h3>" + table(
                    ["File", "Times", "Where"],
                    [[link(r["title"]), fmt(r["times"]),
                      esc(", ".join(([f"Commons ({len(r['commons'])})"] if r["commons"] else [])
                                    + [f"{wiki_label(w, langs)} ({n})" for w, n in sorted(r["wikis"].items())]))] for r in mp_rows],
                    num=(1,)),
                "Picture or media of the day on Commons (exact) and on other wikis (recognised from page names)."))

    # Uploaders
    if C:
        users = C["users"]
        counts = sorted(u[1] for u in users)
        median = counts[len(counts) // 2] if len(counts) % 2 else (counts[len(counts) // 2 - 1] + counts[len(counts) // 2]) / 2
        cut = datetime.fromisoformat(snap["taken_at"]).timestamp() - 30 * 86400
        ts = lambda s: datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()  # noqa: E731
        new_by, active_by = {}, {}
        for u in users:
            first = min(u[6])
            new_by[first] = new_by.get(first, 0) + 1
            for m in u[6]:
                active_by[m] = active_by.get(m, 0) + 1
        top10 = sum(u[1] for u in users[:10])
        groups = [(1, 1, "1 file"), (2, 10, "2–10 files"), (11, 100, "11–100 files"), (101, 1000, "101–1,000 files"),
                  (1001, 10000, "1,001–10,000 files"), (10001, 10 ** 9, "More than 10,000 files")]
        group_rows = []
        for lo, hi, label in groups:
            g = [u for u in users if lo <= u[1] <= hi]
            if g:
                n = sum(u[1] for u in g)
                group_rows.append([label, fmt(len(g)), pct(len(g), len(users)), fmt(n), pct(n, total)])
        vb = (V or {}).get("by_user", {})
        ab = (A or {}).get("by_user", {})
        gb = ((P or {}).get("gap") or {}).get("by_user", {})
        body.append(section("Uploaders", kpis([
            ("Uploaders", fmt(len(users)), None),
            ("New in the last 30 days", fmt(sum(1 for u in users if ts(u[2]) >= cut)), "first upload in that time"),
            ("Active in the last 30 days", fmt(sum(1 for u in users if ts(u[3]) >= cut)), "uploaded at least one file"),
            ("Top 10 uploaded", pct(top10, total), "of all files"),
            ("Median per uploader", fmt(median), "files")])
            + '<div class="chartbox"><h3>Uploaders over time (running total)</h3>'
            + chart(running_total(sorted(new_by.items())), "area")
            + "<h3>People uploading each month</h3>" + chart(sorted(active_by.items())) + "</div>"
            + "<h3>How much people upload</h3>" + table(["Uploaded", "Uploaders", "Share", "Files", "Share of files"], group_rows, num=(1, 2, 3, 4))
            + "<h3>Top uploaders</h3>" + table(
                ["#", "Uploader", "Files", "Share", "First upload", "Latest upload", "Used on wikis", "Quality", "File requests"],
                [[str(i + 1), esc(shown(u[0])), fmt(u[1]), pct(u[1], total), day(u[2]), day(u[3]), fmt(u[4]),
                  fmt(ab.get(u[0], {}).get("qi", 0)), big(vb[u[0]][0]) if u[0] in vb else "—"] for i, u in enumerate(users[:40])],
                num=(0, 2, 3, 6, 7, 8), note=f"Top 40 of {fmt(len(users))}. Each uploader's own report has the full statistics."),
            f"Accounts that first uploaded the {fmt(total)} files now in the project (definition D05; an uploader may not be the "
            "author or photographer, limitation L09). Months are in UTC."))
        body[-1] = body[-1].replace("<section>", '<section class="break">', 1)

    # Places, devices, timing and size
    if P:
        kinds = {}
        for name, brand, kind, n, people in P["models"]:
            k = kinds.setdefault(kind, [0, 0])
            k[0] += n
            k[1] += 1
        parts = [kpis([("Geocoded files", fmt(P["geocoded"]), f"{pct(P['geocoded'], P['files'])} of files"),
                       ("Device models", fmt(len(P["models"])), None),
                       ("No device in metadata", fmt(P["no_device_info"]), pct(P["no_device_info"], P["files"])),
                       ("More with GPS only in metadata", fmt(P["gps_only"]), None)])]
        parts.append('<div class="cols"><div><h3>Most used devices</h3>' + table(
            ["Device", "Kind", "Files", "People"], [[esc(n), esc(k), fmt(c), fmt(p)] for n, b, k, c, p in P["models"][:25]], num=(2, 3))
            + "</div><div><h3>Kinds of device</h3>" + table(
                ["Kind", "Files", "Models"], [[esc(k.capitalize()), fmt(v[0]), fmt(v[1])] for k, v in sorted(kinds.items(), key=lambda kv: -kv[1][0])], num=(1, 2)))
        Gp = P.get("gap")
        if Gp:
            parts.append("<h3>Time from taking a photo to uploading it</h3>" + table(
                ["Gap", "Files", "Share"], [[esc(g), fmt(n), pct(n, Gp["files"])] for g, n in Gp["groups"]], num=(1, 2),
                note=f"Median {fmt(Gp['median'])} days, from {fmt(Gp['files'])} files with a camera date; "
                     f"{fmt(Gp['wrong_clock'])} with a wrong camera clock are left out.")
                + table(["Upload year", "Median gap (days)", "Files"], [[str(y), fmt(m), fmt(n)] for y, m, n in Gp["by_year"]], num=(1, 2)))
        parts.append("</div></div>")
        Z = P.get("size")
        if Z:
            parts.append("<h3>Collection size</h3>" + kpis([
                ("Total size", nbytes(Z["total"]), f"{fmt(Z['files'])} files"),
                ("Average file", nbytes(Z["total"] / Z["files"]), None), ("Median file", nbytes(Z["median"]), None)])
                + "<h3>Largest files</h3>" + table(["File", "Size"], [[link(t), nbytes(s)] for t, s in Z["largest"][:10]], num=(1,)))
        body.append(section("Places, devices and size", "".join(parts),
                            "From each file's location on Commons and the camera metadata (EXIF) of the current version."))

    title = "Project Korikath statistics report"
    return title, page(title, "Wikimedia Commons · Project Korikath", meta, "".join(body), manifest)


# ----------------------------------------------------------- contributor report


def contributor_details(name, cutoff=None):
    """A contributor's files and the details of them from the saved data files."""
    snap = tracker.load_json(tracker.latest_snapshot())
    files = snap["files"]
    uploads = tracker.load_json(tracker.UPLOADS_FILE, {})
    mine = {pid: files[pid]["title"] for pid in files if tracker.upload_info(uploads, pid)[1] == name}
    exif = tracker.load_json(tracker.EXIF_FILE, {}) or {}
    geo = tracker.load_json(tracker.GEO_FILE, {}) or {}
    kinds_raw = tracker.load_json(tracker.MEDIATYPES_FILE, {})
    usage = (tracker.load_json(tracker.USAGE_FILE) or {}).get("files", {})
    assess = tracker.load_json(tracker.ASSESS_FILE) or {}
    views = (tracker.load_json(tracker.VIEWS_FILE) or {}).get("files", {})

    out = {"files": mine, "kinds": {}, "size": 0, "sized": 0, "largest": [], "geocoded": 0, "devices": {},
           "gaps": [], "wrong_clock": 0, "used": [], "wiki_uses": {}, "pages": set(), "assessed": {},
           "views": {}, "view_months": {}, "uploads": {}}
    for pid, title in mine.items():
        kind = "image" if tracker.extension(title) in tracker.IMAGE_EXTS else tracker.MEDIA_KINDS.get(kinds_raw.get(pid), "other")
        out["kinds"][kind] = out["kinds"].get(kind, 0) + 1
        ts = tracker.upload_info(uploads, pid)[0]
        e = exif.get(pid)
        if isinstance(e, list) and len(e) >= 6 and e[5]:
            out["size"] += e[5]
            out["sized"] += 1
            out["largest"].append((e[5], title))
        if geo.get(pid):
            out["geocoded"] += 1
        if e and (e[0] or e[1]):
            dev = tracker.device_of(e[0], e[1])
            d = out["devices"].setdefault(dev[0], [dev[0], dev[2], 0])
            d[2] += 1
        if ts:
            up = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(UTC)
            out["uploads"][up.strftime("%Y-%m")] = out["uploads"].get(up.strftime("%Y-%m"), 0) + 1
            if isinstance(e, list) and len(e) >= 6 and e[4]:
                taken = datetime.fromisoformat(e[4]).date()
                gap = (up.date() - taken).days
                if gap < -1 or taken.year < 2005:
                    out["wrong_clock"] += 1
                else:
                    out["gaps"].append(max(gap, 0))
        us = usage.get(pid)
        if us:
            out["used"].append((title, len(us), len({u[0] for u in us})))
            for wiki, ns, page_title in us:
                out["wiki_uses"][wiki] = out["wiki_uses"].get(wiki, 0) + 1
                out["pages"].add((wiki, page_title))
        for k in tracker.ASSESS_CATS:
            if pid in assess.get(k, {}):
                out["assessed"].setdefault(k, []).append((title, assess[k][pid]))
        v = views.get(pid)
        if v:
            # The same common cutoff as the project totals, so a member's views add up to the project's.
            months = {m: x for m, x in v[2].items() if cutoff and m <= cutoff}
            out["views"][title] = [sum(m[0] for m in months.values()), sum(m[1] for m in months.values())]
            for m, (a, h) in months.items():
                row = out["view_months"].setdefault(m, [0, 0])
                row[0] += a
                row[1] += h
    out["largest"].sort(reverse=True)
    out["used"].sort(key=lambda r: (-r[1], r[0]))
    return out


def user_report(D, name, generated, manifest=None):
    C = D.get("contributors")
    if not C:
        raise RuntimeError("Uploader statistics aren't available yet.")
    users = C["users"]
    u = next((x for x in users if x[0] == name), None)
    if u is None:
        raise KeyError(f"No uploader named {name!r} in the project.")
    if name in opted_out():
        raise KeyError(f"{name} has asked not to be named in reports, so there is no report for this uploader.")
    snap, langs = D["snapshot"], D.get("languages") or {}
    A, V, P, U = (D.get(k) for k in ("assessments", "views", "places", "usage"))
    total = snap["total_files"]
    n, nfiles, first, last, used, uses, per_month, cats_top, ncats = u
    rank = users.index(u) + 1
    X = contributor_details(name, (D.get("views") or {}).get("cutoff_month"))
    user_url = "https://commons.wikimedia.org/wiki/User:" + urllib.parse.quote(name.replace(" ", "_"))
    list_url = "https://commons.wikimedia.org/wiki/Special:ListFiles/" + urllib.parse.quote(name.replace(" ", "_"))
    hist = D.get("history") or []
    latest = hist[-1] if hist else {}
    meta = [("Report generated", esc(fmt_time(generated))),
            ("Built on the crawl of", f"{esc(fmt_time(snap['taken_at']))}" + (f" ({esc(latest['mode'])} crawl)" if latest.get("taken_at") == snap["taken_at"] else "")),
            ("Commons", f'<a href="{esc(user_url)}">User page</a> · <a href="{esc(list_url)}">All this member&#39;s uploads</a>')]
    if V:
        meta.append(("File requests counted", f"{esc(views_period(V))} (complete months)"
                     + (f" · {fmt(V['with_history'])} of {fmt(V['files'])} project files so far" if V["with_history"] < V["files"] else "")))

    counts = (A or {}).get("by_user", {}).get(name, {})
    mp_rows, mp_wikis = main_page_rows(A, set(X["files"].values())) if A else ([], set())
    views_rank = None
    if V and name in V["by_user"]:
        views_rank = 1 + sum(1 for other in V["by_user"].values() if other[0] > V["by_user"][name][0])
    tot_views = [sum(v[0] for v in X["views"].values()), sum(v[1] for v in X["views"].values())]
    gaps = sorted(X["gaps"])
    gap_median = (gaps[len(gaps) // 2] if len(gaps) % 2 else (gaps[len(gaps) // 2 - 1] + gaps[len(gaps) // 2]) / 2) if gaps else None
    non_image = sum(v for k, v in X["kinds"].items() if k != "image")

    cards = [("Files in the project", fmt(nfiles), f"{pct(nfiles, total)} of all files"),
             ("Rank by files", f"#{fmt(rank)}", f"of {fmt(len(users))} uploaders"),
             ("First upload", day(first), None), ("Latest upload", day(last), None),
             ("Active months", fmt(len(per_month)), "months with at least one upload"),
             ("Categories", fmt(ncats), "this member's files are filed in"),
             ("Non-image files", fmt(non_image), " · ".join(f"{fmt(v)} {k}" for k, v in sorted(X["kinds"].items()) if k != "image") or None)]
    if C["usage_checked"]:
        cards += [("Files used on other wikis", fmt(used), f"{pct(used, nfiles)} of this member's files"),
                  ("Uses on other wikis", fmt(uses), f"on {fmt(len(X['pages']))} pages of {fmt(len(X['wiki_uses']))} wikis")]
    if A:
        cards.append(("Quality · Featured · Valued", f"{fmt(counts.get('qi', 0))} · {fmt(counts.get('fp', 0))} · {fmt(counts.get('vi', 0))}",
                      f"{fmt(counts.get('fm', 0))} featured media" if counts.get("fm") else None))
        cards.append(("Times on a main page", fmt(sum(r["times"] for r in mp_rows)),
                      f"{fmt(len(mp_rows))} files on {fmt(len(mp_wikis))} wikis" if mp_rows else None))
    if V:
        cards.append(("File requests" + (" so far" if V["with_history"] < V["files"] else ""), big(tot_views[0]),
                      f"{big(tot_views[1])} not flagged as automated ({pct(tot_views[1], tot_views[0])})"
                      + (f" · #{fmt(views_rank)} by file requests" if views_rank else "")))
    if X["sized"]:
        cards.append(("Size of this member's files", nbytes(X["size"]), f"average {nbytes(X['size'] / X['sized'])}"))
    cards.append(("Geocoded files", fmt(X["geocoded"]), pct(X["geocoded"], nfiles) + " of this member's files"))
    if gap_median is not None:
        cards.append(("Median take-to-upload gap", f"{fmt(gap_median)} days", f"from {fmt(len(gaps))} files with a camera date"))
    if X["devices"]:
        cards.append(("Devices used", fmt(len(X["devices"])), None))
    body = [section("Headline numbers", kpis(cards))]

    uploads = sorted(X["uploads"].items())
    years = per_year(uploads)
    body.append(section("Uploads", '<div class="chartbox"><h3>Files uploaded per month</h3>' + chart(uploads)
                        + "<h3>Running total</h3>" + chart(running_total(uploads), "area") + "</div>"
                        + table(["Year", "Files uploaded", "Running total"],
                                [[y, fmt(c), fmt(sum(v for yy, v in years if yy <= y))] for y, c in years], num=(1, 2)),
                        "Files now in the project uploaded by this member, by upload month (UTC)."))

    body.append(section("Files by category", table(
        ["Category", "Files", "Share of this member's files"], [[link(c), fmt(k), pct(k, nfiles)] for c, k in cats_top], num=(1, 2),
        note=(f"Top {len(cats_top)} of {fmt(ncats)} categories. " if ncats > len(cats_top) else "")
        + "A file in several categories counts in each. Catch-all categories ("
        + esc(", ".join(short(c) for c in C["catch_all"])) + ") are left out.")))

    if V and X["views"]:
        vm = sorted((m, a, h) for m, (a, h) in X["view_months"].items())
        top = sorted(X["views"].items(), key=lambda kv: -kv[1][0])
        body.append(section("File requests", '<div class="chartbox"><h3>File requests per month</h3>'
                            + chart(vm, colors=(MUTED, ACCENT), legend=["All file requests", "Not flagged as automated"])
                            + "<h3>File requests (running total)</h3>" + chart(running_total([(m, a) for m, a, _ in vm]), "area") + "</div>"
                            + "<h3>Most requested files</h3>" + thumbs([t for t, _ in top[:8]])
                            + table(["File", "All file requests", "Not flagged as automated"], [[link(t), fmt(a), fmt(h)] for t, (a, h) in top[:25]], num=(1, 2)),
                            f"How often this member's files were loaded, from January 2015; {fmt(len(X['views']))} of "
                            f"{fmt(nfiles)} files counted so far."))

    if X["used"]:
        body.append(section("On-wiki usage", "<div class=\"cols\"><div><h3>Wikis using this member's files</h3>" + table(
            ["Wiki", "Uses"], [[esc(wiki_label(w, langs)), fmt(c)] for w, c in sorted(X["wiki_uses"].items(), key=lambda kv: (-kv[1], kv[0]))[:30]], num=(1,),
            note=f"Top 30 of {fmt(len(X['wiki_uses']))}." if len(X["wiki_uses"]) > 30 else None)
            + "</div><div><h3>Most used files</h3>" + table(
                ["File", "Uses", "Wikis"], [[link(t), fmt(c), fmt(w)] for t, c, w in X["used"][:30]], num=(1, 2),
                note=f"Top 30 of {fmt(len(X['used']))}." if len(X["used"]) > 30 else None) + "</div></div>",
            f"From the usage check of {esc(fmt_time(U['checked_at']))}." if U else None))

    if X["assessed"] or mp_rows:
        parts = []
        for k in ("fp", "vi", "fm", "qi"):
            items = sorted(X["assessed"].get(k, []), key=lambda r: r[1] or "", reverse=True)
            if items:
                parts.append(f"<h3>{KIND_NAMES[k]} <span class=pill>{fmt(len(items))}</span></h3>"
                             + table(["File", "In the category since"], [[link(t), day(ts) if ts else "—"] for t, ts in items], num=(1,)))
        if mp_rows:
            parts.append("<h3>On the main page</h3>" + table(
                ["File", "Times", "Where"],
                [[link(r["title"]), fmt(r["times"]),
                  esc(", ".join(([f"Commons ({len(r['commons'])})"] if r["commons"] else [])
                                + [f"{wiki_label(w, langs)} ({c})" for w, c in sorted(r["wikis"].items())]))] for r in mp_rows], num=(1,)))
        body.append(section("Quality, featured and valued", "".join(parts)))

    parts = []
    if X["devices"]:
        devs = sorted(X["devices"].values(), key=lambda d: -d[2])
        parts.append("<div><h3>Devices</h3>" + table(["Device", "Kind", "Files"], [[esc(d[0]), esc(d[1]), fmt(d[2])] for d in devs[:20]], num=(2,),
                                                     note=f"Top 20 of {fmt(len(devs))}." if len(devs) > 20 else None) + "</div>")
    if gaps:
        groups = [("Same day", 0, 0), ("1–7 days", 1, 7), ("8–30 days", 8, 30), ("1–12 months", 31, 365),
                  ("1–2 years", 366, 730), ("2–5 years", 731, 1826), ("More than 5 years", 1827, 10 ** 6)]
        parts.append("<div><h3>Time from taking to uploading</h3>" + table(
            ["Gap", "Files", "Share"], [[g, fmt(c), pct(c, len(gaps))] for g, lo, hi in groups if (c := sum(1 for x in gaps if lo <= x <= hi))],
            num=(1, 2), note=f"Median {fmt(gap_median)} days." + (f" {fmt(X['wrong_clock'])} files with a wrong camera clock are left out." if X["wrong_clock"] else ""))
            + "</div>")
    extra = ""
    if X["largest"]:
        extra = "<h3>Largest files</h3>" + table(["File", "Size"], [[link(t), nbytes(s)] for s, t in X["largest"][:10]], num=(1,))
    if parts or extra:
        body.append(section("Devices, timing and size", f'<div class="cols">{"".join(parts)}</div>{extra}'))

    title = f"{name}: uploader statistics"
    return title, page(title, "Project Korikath · uploader report", meta, "".join(body), manifest)


# ------------------------------------------------------------------------- PDF


def find_browser():
    candidates = []
    for env in ("ProgramFiles(x86)", "ProgramFiles", "LocalAppData"):
        root = os.environ.get(env)
        if root:
            candidates += [Path(root) / "Microsoft/Edge/Application/msedge.exe", Path(root) / "Google/Chrome/Application/chrome.exe"]
    for c in candidates:
        if c.exists():
            return str(c)
    for name in ("msedge", "google-chrome", "chromium", "chromium-browser", "chrome"):
        if shutil.which(name):
            return shutil.which(name)
    raise RuntimeError("Making a PDF needs Microsoft Edge or Google Chrome, and neither was found.")


def html_to_pdf(page_html, out):
    """Print an HTML page to a PDF file with a headless browser (its own profile, so an open browser
    window doesn't get in the way)."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        src = Path(tmp) / "report.html"
        src.write_text(page_html, encoding="utf-8")
        tmp_pdf = Path(tmp) / "report.pdf"
        subprocess.run([find_browser(), "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
                        "--disable-extensions", f"--user-data-dir={Path(tmp) / 'profile'}", "--no-pdf-header-footer",
                        "--run-all-compositor-stages-before-draw", "--virtual-time-budget=15000",
                        f"--print-to-pdf={tmp_pdf}", src.as_uri()],
                       capture_output=True, timeout=180, creationflags=NO_WINDOW if os.name == "nt" else 0)
        # Edge's launcher can return before the browser it started has written the file, so wait for
        # the file to appear and stop growing.
        size, deadline = -1, time.monotonic() + 180
        while time.monotonic() < deadline:
            now = tmp_pdf.stat().st_size if tmp_pdf.exists() else -1
            if now > 0 and now == size:
                break
            size = now
            time.sleep(1)
        else:
            raise RuntimeError("The browser didn't produce a PDF.")
        shutil.move(str(tmp_pdf), out)
    return out


def safe_name(name):
    # Letters, vowel signs and digits of any script stay (so Bengali names read correctly).
    keep = "".join(ch if unicodedata.category(ch)[0] in "LMN" or ch in "-." else "_" for ch in name).strip("_.")
    return keep[:60] or "contributor"


# ------------------------------------------------------------------ reliability pages
# Everything below is generated from the report's manifest (issuance.py) and the methodology register, so the
# explanation of how far the report can be relied on always matches what was actually done.

GLOSSARY = [
    ("Wikimedia Commons", "The Wikimedia project that hosts freely licensed media files used by Wikipedia and other wikis."),
    ("Category tree", "The category Category:Project Korikath and every category below it, at any depth."),
    ("Page ID", "Commons' permanent number for a page. It stays the same when a file is renamed, so files are matched by it."),
    ("Crawl", "One complete reading of the category tree by the tracker. A full crawl re-reads every category; a quick check "
              "re-reads only what changed (limitation L02)."),
    ("Snapshot", "The saved result of one crawl: every category and every file in the tree at that time."),
    ("On-wiki usage", "A file being shown on a page of another Wikimedia wiki, as recorded by that wiki (GlobalUsage)."),
    ("File request", "One request to Wikimedia's servers for a file or a thumbnail of it. Counted by Wikimedia's media request "
                     "statistics; not the same as a person viewing it (limitation L04)."),
    ("Not flagged as automated", "File requests Wikimedia classifies as coming from a user agent rather than a known bot or "
                                 "crawler (agent type 'user')."),
    ("Uploader", "The account that uploaded the first version of a file."),
    ("Quality / Featured / Valued image", "Files that passed the Commons community's assessment processes, as recorded by the "
                                          "Commons-wide categories that the assessment templates maintain."),
    ("POTD / MOTD", "Picture / Media of the Day: a file shown on a wiki's main page for a day."),
    ("EXIF", "Camera metadata stored inside an image file: device, time taken, sometimes GPS position."),
    ("SHA-256 fingerprint", "A 64-character code computed from the exact bytes of a file or record. Any change, however "
                            "small, gives a completely different fingerprint."),
    ("Archive state", "A record of every data file and code file of the tracker at one moment, each identified by its "
                      "SHA-256 fingerprint and stored once."),
    ("FINAL / PROVISIONAL", "FINAL: every error and freshness check passed. PROVISIONAL: at least one did not; the reasons are on "
                            "the cover. Only FINAL reports are meant to be cited."),
]


def _source_of(dataset):
    import provenance
    for _pattern, name, source, _desc in provenance.DATASETS:
        if name == dataset:
            return source
    return ""


def data_line(datasets, defs):
    """A section's source line: which data it rests on (as of when, how complete) and which definitions apply."""
    m = RENDER.get("manifest")
    if not m:
        return ""
    rows = {r["dataset"]: r for r in m.get("data_status") or []}
    parts = []
    for ds in datasets:
        r = rows.get(ds)
        if not r:
            continue
        when = r["as_of"] if r["as_of"] and len(str(r["as_of"])) == 10 else fmt_time(r["as_of"]) if r["as_of"] else "—"
        cov = f", {fmt(r['covered'])} of {fmt(r['of'])} files" if r.get("of") is not None and r.get("share") is not None and r["share"] < 1 else ""
        parts.append(f"{esc(r['label'])} as of {esc(when)}{cov}")
    sources = sorted({_source_of(ds) for ds in datasets} - {""})
    return (f'<p class="src"><b>Data:</b> {"; ".join(parts) or "—"}. <b>Definitions:</b> {", ".join(defs) or "—"} '
            f'(appendix B). <b>Source:</b> {esc("; ".join(sources))}.</p>')


SOURCES = {  # section title -> (datasets it rests on, definitions that apply)
    "Headline numbers": (["snapshot", "uploads", "mediatypes", "assessments", "usage", "views", "geo", "exif"],
                         ["D01", "D02", "D05", "D06", "D07", "D08", "D09", "D12", "D13", "D15", "D16", "D17"]),
    "Growth of the collection": (["snapshot", "uploads"], ["D01", "D04"]),
    "Kinds of media": (["snapshot", "mediatypes", "exif"], ["D06", "D17"]),
    "Category tree": (["snapshot"], ["D01", "D02"]),
    "Changes": (["snapshot"], ["D03", "D11"]),
    "File requests": (["views"], ["D12", "D13", "D18"]),
    "On-wiki usage": (["usage"], ["D09"]),
    "Latest usage changes": (["usage"], ["D10", "D11"]),
    "Usage checks": (["usage"], ["D09", "D10"]),
    "Quality, featured and valued": (["assessments"], ["D07", "D08"]),
    "On the main page": (["assessments", "usage"], ["D08"]),
    "Uploaders": (["uploads", "views", "assessments"], ["D04", "D05", "D18"]),
    "Places, devices and size": (["geo", "exif"], ["D14", "D15", "D16", "D17", "D18"]),
    "Uploads": (["uploads"], ["D04"]),
    "Files by category": (["snapshot"], ["D01"]),
    "Devices, timing and size": (["exif"], ["D14", "D16", "D17"]),
}


def cover(manifest, meta_rows):
    m = manifest
    status = m["status"]
    issuer = m.get("issuer") or {}
    rows = [("Report ID", f"<b>{esc(m['report_id'])}</b>"),
            ("Status", f'<span class="badge {status.lower()}">{status}</span>'
                       + ("" if status == "FINAL" else " Not for citation: see the reasons below.")),
            ("Fingerprint", f'<span class="mono">{esc(m["fingerprint"])}</span>'),
            ("Issued by", esc(", ".join(x for x in (issuer.get("name"), issuer.get("role"), issuer.get("contact")) if x)
                              or "not named (config.json: issuer)"))]
    rows += meta_rows
    rows += [("Made with", f"Korikath Tracker {esc(m['build']['tool_version'])}, method {esc(m['build']['method_version'])}"
                           + (f", code commit {esc(m['build']['git_commit'])}" if m["build"].get("git_commit") else "")),
             ("Verify", "This PDF's SHA-256 fingerprint is recorded in the tracker's register of issued reports"
                        + (f", published at <a href=\"{esc(m['register_url'])}\">{esc(m['register_url'])}</a>" if m.get("register_url") else "")
                        + ". Appendix H explains how to check it."),
             ("Times", "All times in this report are UTC (Coordinated Universal Time).")]
    out = "".join(f"<b>{esc(k)}</b><span>{v}</span>" for k, v in rows)
    reasons = ""
    if status != "FINAL":
        reasons = ('<div class="reasons"><b>Why this report is provisional:</b><ul>'
                   + "".join(f"<li>{esc(r)}</li>" for r in m.get("reasons") or []) + "</ul></div>")
    return f'<div class="meta">{out}</div>{reasons}'


def trust_section(m):
    checks = m.get("checks") or []
    passed = sum(1 for c in checks if c["result"] == "passed")
    spot = next((c for c in checks if c["id"] == "W02"), None)
    pet = next((c for c in checks if c["id"] == "W01"), None)
    lic = esc(m.get("licence") or "CC BY 4.0")
    p = [
        ("Where the numbers come from",
         "Every figure in this report was computed by the Korikath Tracker from public data published by Wikimedia: the "
         "Wikimedia Commons API (files, categories, uploaders, usage on other wikis, assessments, file metadata) and the "
         "Wikimedia Analytics API (file requests). No figure was typed in by hand. All text comes from fixed templates in "
         "the tracker's code, filled in with the figures; nothing is written or edited by hand for an individual report."),
        ("A fixed point in time",
         "Commons changes every minute. This report describes the project as it was at the times in the table "
         "\"Data used in this report\": each kind of data was collected at its own time, and the table says when and how "
         "completely. Later changes are not included."),
        ("Can it be reproduced?",
         f"Yes. Just before this report was made, the tracker recorded every data file and every code file it used "
         f"(archive state {esc(m['archive_state'])}, fingerprint {esc(m['archive_state_sha256'][:16])}…). "
         "From that record the whole report can be regenerated at any time and compared, figure by figure, with this "
         "one (the tracker's --rebuild command does this). Running the same queries against Wikimedia today would give "
         "slightly different numbers, because the wikis have changed since; that is why the data is kept."),
        ("Has it been altered?",
         f"This report's fingerprint is {esc(m['fingerprint'][:16])}…, computed from its data, figures, checks and "
         "versions. The PDF file's own SHA-256 fingerprint is recorded in the tracker's register of issued reports, a "
         "list in which every entry is chained to the one before it, so entries can't be changed or removed unnoticed. "
         "If a copy's fingerprint is not in the register, it is not an issued report (appendix H)."),
        ("What exactly is counted",
         f"Every figure follows a written definition (appendix B). This report uses method version "
         f"{esc(m['build']['method_version'])}; when a definition changes, the method version changes and the change is "
         "recorded (appendix E)."),
        ("How complete it is",
         "Where a figure does not cover every file of the project, it says so, and each section ends with a line naming "
         "the data it rests on, as of when, and the definitions that apply."),
        ("How it was checked",
         f"Before this report was issued, the tracker ran {len(checks)} checks ({passed} passed; section 2): totals add up, "
         "every data file matches its recorded fingerprint, the rules match the recorded method version, the archive "
         "is intact, the data is recent and complete"
         + (f"; {esc(spot['detail'])} in a live spot check" if spot and spot["result"] == "passed" else "")
         + (f"; and an independent count with PetScan: {esc(pet['detail'])}" if pet and pet["result"] == "passed" else "")
         + f". The status is <b>{m['status']}</b>" + (": every error and freshness check passed." if m["status"] == "FINAL"
                                                       else ": see the reasons on the cover.")),
        ("What it cannot tell you",
         "Some things are inferred or not visible to the tracker. For example, file requests include thumbnails and "
         "cannot fully separate people from automated traffic, and the uploader of a file is not always the person who "
         "took the photo. Appendix C lists every known limitation."),
        ("Licences and privacy",
         f"This report is published under {lic}. Images are shown only with their author and licence, and never when "
         "they are marked as showing identifiable people. Uploader names are public Wikimedia usernames; an uploader can "
         "ask not to be named. This report is produced independently by Project Korikath and is not produced or "
         "endorsed by the Wikimedia Foundation (appendix F)."),
        ("Found a mistake?",
         "Contact the issuer named on the cover, quoting the report ID. Corrections are recorded in the register, and a "
         "corrected report replaces this one; checking a superseded report says so."),
    ]
    body = "".join(f"<h3>{esc(t)}</h3><p>{x}</p>" for t, x in p)
    return f'<section class="trust"><h2>1. How to rely on this report</h2>{body}</section>'


def checks_section(m):
    rows = [[f'<span class="nw">{esc(c["id"])}</span>', esc({"error": "Error", "freshness": "Freshness", "warning": "Cross-check"}[c["level"]]),
             esc(c["name"]), f'<span class="nw res {c["result"].replace(" ", "")}">{esc(c["result"])}</span>', esc(c["detail"])]
            for c in m.get("checks") or []]
    return section("2. Checks before issue", table(["ID", "Kind", "Check", "Result", "Detail"], rows),
                   "Run automatically just before this report was made. Any failed error or freshness check makes the report "
                   "PROVISIONAL; cross-checks are independent comparisons, reported whatever their result.")


def appendices(m, D):
    import methodology
    out = []
    # A. Methods
    meth = [
        "The tracker reads the category tree below Category:Project Korikath on Wikimedia Commons at unlimited depth, "
        "and counts each file once by its page ID (definition D01). A full crawl re-reads every category; a quick check "
        "reads Commons' category counters and re-reads in full every category whose counts don't add up (limitation L02).",
        "Each crawl is saved as a snapshot and compared with the previous one; differences are matched by page ID, and "
        "the edit that caused each change is looked up (D03, D11).",
        "Upload dates, uploaders, media types, locations and camera metadata are looked up once per file and re-checked "
        f"on a rolling basis (each day 1/{methodology.ROTATION_DAYS} of the files, so every file once a month). On-wiki usage and assessments are "
        "checked after every crawl. File requests are collected per file from Wikimedia's media request statistics and "
        "totalled to a common cutoff month (D12).",
        "All figures in this report are computed from those saved data files by the tracker's code at the version on the "
        "cover; the same code and data regenerate them exactly.",
        "Every data file carries a record of where it came from, when, how complete it is and its fingerprint; every run "
        "of the tracker is recorded; every state of the data is archived.",
    ]
    out.append(section("Appendix A. How this report was made", "".join(f"<p>{esc(x)}</p>" for x in meth)))
    # B. Definitions
    out.append(section("Appendix B. Definitions", table(
        ["ID", "Name", "Definition", "Unit", "Known limitations"],
        [[f'<span class="nw">{esc(d["id"])}</span>', esc(d["name"]), esc(d["definition"] + (f" Excludes: {d['excludes']}" if d.get("excludes") else "")),
          esc(d["unit"]), esc(", ".join(d.get("limits") or []))] for d in methodology.DEFINITIONS]),
        f"Method version {esc(m['build']['method_version'])}. The source of each definition's data is in appendix D."))
    # C. Limitations
    notes = limitation_notes(D)
    out.append(section("Appendix C. Known limitations", table(
        ["ID", "Limitation", "In this report"],
        [[f'<span class="nw">{esc(x["id"])}</span>', esc(x["text"]), esc(notes.get(x["id"], ""))] for x in methodology.LIMITATIONS])))
    # D. Provenance
    import provenance
    state = m.get("state_files") or {}
    rows = [[esc(rel), f'<span class="mono">{esc(h[:16])}…</span>'] for rel, h in sorted(state.items())
            if not rel.endswith((".py", ".html", ".md", ".bat")) and not rel.startswith(("logs/", "runs/", "snapshots/", "reports/"))]
    srcs = [[esc(name), esc(src)] for _p, name, src, _d in provenance.DATASETS]
    out.append(section("Appendix D. Provenance", f"<p>Archive state <b>{esc(m['archive_state'])}</b>, SHA-256 "
        f'<span class="mono">{esc(m["archive_state_sha256"])}</span>. Figures fingerprint <span class="mono">'
        f'{esc(m["figures_sha256"])}</span>. Code: Korikath Tracker {esc(m["build"]["tool_version"])}, commit '
        f'{esc(m["build"].get("git_commit") or "—")}, Python {esc(m["build"]["python"])}. Requests to Wikimedia were sent '
        f'one at a time, identified as: {esc(m.get("user_agent") or "")}.</p>'
        + "<h3>Data files used</h3>" + table(["File", "SHA-256 (first 16 characters)"], rows)
        + "<h3>Where each kind of data comes from</h3>" + table(["Data", "Source"], srcs)))
    # E. Versions and corrections
    versions = methodology.recorded()
    import issuance
    errata = [e for e in issuance.entries() if e["event"] == "superseded"]
    out.append(section("Appendix E. Method versions and corrections",
        table(["Method version", "Since", "Fingerprint of its rules"],
              [[esc(k), esc(v["date"]), f'<span class="mono">{esc(v["fingerprint"][:16])}…</span>'] for k, v in versions.items()])
        + "<p>Each version's changes, and which figures they affect, are described in the tracker's CHANGELOG.md.</p>"
        + "<h3>Corrections to earlier reports</h3>"
        + (table(["Report", "Replaced by", "Reason"], [[esc(e["report_id"]), esc(e["superseded_by"]), esc(e["reason"])] for e in errata])
           if errata else "<p>None.</p>")))
    # F. Licences, credits, privacy
    credits = [(t, c) for t, c in (m.get("credits") or {}).items() if c.get("show")]
    out.append(section("Appendix F. Licences, credits and privacy",
        f"<p><b>This report</b> is published under {esc(m.get('licence') or 'CC BY 4.0')}. Images in it remain under their "
        "own licences, credited below and under each image.</p>"
        "<p><b>Data.</b> File, category, usage and assessment data come from Wikimedia Commons and the other Wikimedia "
        "wikis through their public APIs; file-request statistics come from the Wikimedia Analytics API. They are used "
        "under the terms Wikimedia publishes them with.</p>"
        "<p><b>Not affiliated.</b> This report is produced independently by Project Korikath. It is not produced or "
        "endorsed by the Wikimedia Foundation. \"Wikimedia Commons\" and \"Wikipedia\" are named only to refer to them.</p>"
        "<p><b>Privacy.</b> Uploaders are named by their public Wikimedia usernames, with figures from public data only. "
        "No locations of individual uploaders' photos are shown. An uploader can ask the issuer not to be named; they are "
        "then counted in every total but named nowhere. Images marked as showing identifiable people (personality "
        "rights) are not reproduced.</p>"
        + ("<h3>Image credits</h3>" + table(["File", "Author", "Licence"],
                                           [[link(t, short(t)), esc(c["author"]), f'<a href="{esc(c["licence_url"])}">{esc(c["licence"])}</a>'
                                             if c.get("licence_url") else esc(c["licence"])] for t, c in credits]) if credits else "")))
    # G. Glossary
    out.append(section("Appendix G. Glossary", table(["Term", "Meaning"], [[esc(a), esc(b)] for a, b in GLOSSARY])))
    # H. How to verify
    steps = [
        "Compute the PDF file's SHA-256 fingerprint. On Windows: <span class=mono>certutil -hashfile report.pdf SHA256</span>; "
        "on macOS or Linux: <span class=mono>shasum -a 256 report.pdf</span>.",
        "Look it up in the register of issued reports" + (f" (<a href=\"{esc(m['register_url'])}\">{esc(m['register_url'])}</a>)"
                                                          if m.get("register_url") else ", which the issuer can provide")
        + f". It must be listed with this report's ID, {esc(m['report_id'])}, and status. If it is missing, the file is not "
          "an issued report or has been changed.",
        "Check the status there: a report replaced by a correction is marked SUPERSEDED, with the ID of the report that "
        "replaces it.",
        "Spot-check any figure: every file, category and page is linked to Wikimedia, and the definitions are in "
        "appendix B. Small differences are expected for figures that have changed since the dates in \"Data used\".",
        "To check every figure, the issuer can regenerate the report from its archive state "
        "(<span class=mono>python report.py --rebuild " + esc(m["report_id"]) + "</span>), which recomputes all figures "
        "from the recorded data and code and compares them with the issued ones.",
    ]
    out.append(section("Appendix H. How to verify this report", "<ol>" + "".join(f"<li>{x}</li>" for x in steps) + "</ol>"))
    out[0] = out[0].replace("<section>", '<section class="break">', 1)
    return "".join(out)


def limitation_notes(D):
    """What some limitations amount to in this report's own data."""
    notes = {}
    V, P, M = D.get("views"), D.get("places"), D.get("media")
    full = [h for h in D.get("history") or [] if h.get("mode") == "full"]
    if full:
        notes["L02"] = f"Latest full crawl: {fmt_time(full[-1]['taken_at'])}."
    if V:
        notes["L06"] = (f"{fmt(V['with_history'])} of {fmt(V['files'])} files counted; totals to {views_period(V)}; "
                        f"{fmt((V.get('after_cutoff') or [0])[0])} later requests left out.")
    if P:
        notes["L10"] = (f"{fmt(P['no_device_info'])} files without device data"
                        + (f"; {fmt(P['gap']['wrong_clock'])} with a wrong camera clock" if P.get("gap") else "") + ".")
    if M:
        notes["L14"] = f"{fmt(M.get('other', 0))} files of another type."
    C = D.get("contributors")
    if C:
        notes["L09"] = f"{fmt(C.get('unknown', 0))} files with no known uploader."
    U = D.get("usage")
    if U and U.get("checks"):
        ck = U["checks"][-1]
        rows = ck["removed"] + ck["added"]
        if rows:
            found = sum(1 for r in rows if len(r) > 11 and r[9])
            notes["L17"] = f"Latest usage check: the edit was found for {found} of {len(rows)} changes."
    return notes


def fetch_credits(titles, config):
    """{title: {author, licence, licence_url, show, reason}} for the files whose thumbnails a report shows, from each
    file's description page (extmetadata). A thumbnail is shown only with an author and a licence, and, in the default
    "safe" mode, never for a file marked as showing identifiable people (personality rights)."""
    mode = (config.get("thumbnails") or "safe").lower()
    out = {t: {"show": False, "reason": "thumbnails switched off"} for t in titles} if mode == "none" else {}
    if mode == "none" or not titles:
        return out
    api = tracker.CommonsAPI(config["user_agent"])
    titles = sorted(titles)
    for i in range(0, len(titles), 50):
        batch = titles[i:i + 50]
        for data in api.query_all(prop="imageinfo", titles="|".join(batch), iiprop="extmetadata",
                                  iiextmetadatafilter="Artist|LicenseShortName|LicenseUrl|Restrictions|Credit"):
            names = {n["to"]: n["from"] for n in data["query"].get("normalized", [])}
            for page in data["query"]["pages"]:
                t = names.get(page.get("title"), page.get("title"))
                em = ((page.get("imageinfo") or [{}])[0]).get("extmetadata") or {}
                val = lambda k: html.unescape(re.sub(r"<[^>]+>", "", str((em.get(k) or {}).get("value") or ""))).strip()  # noqa: E731
                author, lic, url, restr = val("Artist"), val("LicenseShortName"), val("LicenseUrl"), val("Restrictions").lower()
                if "personality" in restr and mode == "safe":
                    out[t] = {"show": False, "reason": "shows identifiable people"}
                elif not author or not lic:
                    out[t] = {"show": False, "reason": "author or licence not stated on Commons"}
                else:
                    out[t] = {"show": True, "author": author[:120], "licence": lic, "licence_url": url}
    for t in titles:
        out.setdefault(t, {"show": False, "reason": "not found on Commons"})
    return out


def normalised(D):
    """The figures, without what depends on the moment rather than the data (whether a job happens to be running)."""
    if D.get("views"):
        D["views"] = {k: v for k, v in D["views"].items() if k != "running"}
    return D


def render_html(D, manifest):
    """The report page for figures D and an issued report's manifest. Deterministic: the same data, code and
    manifest give the same page, byte for byte (which is what --rebuild checks)."""
    D = dict(D, data_status=manifest["data_status"], build=manifest["build"])
    RENDER.update(manifest=manifest, credits=manifest.get("credits") or {}, wanted=None, data=D)
    generated = datetime.fromisoformat(manifest["generated_at"].replace("Z", "+00:00"))
    if manifest["type"] == "contributor":
        _, page_html = user_report(D, manifest["subject"], generated, manifest)
    else:
        _, page_html = project_report(D, generated, manifest)
    return page_html


def wanted_thumbnails(D, kind, subject, generated, skeleton):
    """The files whose thumbnails the report will show (a first, uncredited rendering collects them)."""
    RENDER.update(manifest=None, credits={}, wanted=set(), data=D)
    try:
        if kind == "contributor":
            user_report(D, subject, generated)
        else:
            project_report(D, generated)
        return sorted(RENDER["wanted"])
    finally:
        RENDER["wanted"] = None


def make_report(user=None, out_dir=OUT_DIR):
    """Check, record, render, print and register a report. Returns the PDF's path. The file name holds the time it
    was generated (UTC) and the end of its report ID."""
    import archive
    import issuance
    import validate
    import version
    config = tracker.load_json(BASE / "config.json", {})
    kind, subject = ("contributor", user) if user else ("project", tracker.normalize_category(config["root_category"]))
    if user and user in opted_out():
        raise KeyError(f"{user} has asked not to be named in reports, so there is no report for this uploader.")
    for attempt in range(2):
        generated = datetime.now(timezone.utc).replace(microsecond=0)
        # 1. The inputs: every data and code file as they are now, recorded in the archive (stored once each).
        state_name, _n, _new, _size = archive.record_state("report-inputs", extra={"report_type": kind, "subject": subject})
        state = json.loads((archive.STATES / state_name).read_text(encoding="utf-8"))
        state_sha = hashlib.sha256((archive.STATES / state_name).read_bytes()).hexdigest()
        # 2. The figures, from those files.
        D = normalised(dashboard_data())
        wanted = wanted_thumbnails(D, kind, subject, generated, None)  # also fails early for an unknown uploader
        # 3. The checks, which decide the status.
        figures_sha = issuance.figures_fingerprint(D)
        checks = validate.run(D, now=generated, live=True, config=config, sample_seed=figures_sha)
        # The data must not have changed while the report was being made (a job finishing meanwhile).
        import provenance
        now_hashes = provenance.hashes()
        changed = [rel for rel, h in now_hashes.items() if state["files"].get(rel, {}).get("sha256") != h]
        if not changed:
            break
        print(f"Data changed while the report was being made ({', '.join(changed[:3])}); starting again.")
    else:
        checks.append({"id": "E07", "level": "error", "name": "Data unchanged while the report was made",
                       "result": "failed", "detail": f"changed: {', '.join(changed[:5])}"})
    status, reasons = validate.status_of(checks)
    try:
        credits = fetch_credits(wanted, config)
    except Exception as e:  # noqa: BLE001  without credits no image is shown
        credits = {t: {"show": False, "reason": f"credits could not be checked ({type(e).__name__})"} for t in wanted}
    core = {"type": kind, "subject": subject, "generated_at": generated.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "archive_state": state_name, "archive_state_sha256": state_sha, "figures_sha256": figures_sha,
            "build": version.build_info(), "status": status, "reasons": reasons, "checks": checks,
            "credits": credits, "data_status": D.get("data_status"), "issuer": config.get("issuer") or {},
            "licence": config.get("report_licence") or "CC BY 4.0", "register_url": config.get("public_register_url"),
            "user_agent": config.get("user_agent"),
            "state_files": {rel: f["sha256"] for rel, f in state["files"].items()}}
    rid = issuance.report_id(kind, generated, core)
    manifest = {"report_id": rid, "fingerprint": issuance.sha256(issuance.canonical(core)), **core}
    # 4. The page and the PDF.
    page_html = render_html(D, manifest)
    manifest["html_sha256"] = issuance.sha256(page_html)
    stem = f"Korikath_contributor_{safe_name(user)}" if user else "Korikath_statistics_report"
    name = f"{stem}_{file_stamp(generated)}_{rid[-8:]}{'' if status == 'FINAL' else '_PROVISIONAL'}.pdf"
    pdf = html_to_pdf(page_html, Path(out_dir) / name)
    manifest["pdf_sha256"] = hashlib.sha256(pdf.read_bytes()).hexdigest()
    manifest["pdf_file_name"] = name
    # 5. The register.
    issuance.issue(manifest)
    print(f"Issued {rid} ({status}); PDF SHA-256 {manifest['pdf_sha256']}")
    return pdf


def rebuild(rid):
    """Regenerate an issued report from its archive state, with the code recorded in that state, and compare."""
    import archive
    import issuance
    m = issuance.load_manifest(rid)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        folder = Path(tmp) / "state"
        archive.restore(m["archive_state"], folder)
        (folder / "manifest.json").write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")
        out = subprocess.run([sys.executable, "report.py", "--render-html", "page.html", "--manifest", "manifest.json"],
                             cwd=folder, capture_output=True, text=True, encoding="utf-8",
                             creationflags=NO_WINDOW if os.name == "nt" else 0)
        if out.returncode:
            raise SystemExit(f"The recorded code could not regenerate the report:\n{out.stderr[-2000:]}")
        got = json.loads(out.stdout.strip().splitlines()[-1])
    ok_fig = got["figures_sha256"] == m["figures_sha256"]
    ok_html = got["html_sha256"] == m["html_sha256"]
    print(f"{rid}: regenerated from archive state {m['archive_state']} with the code recorded in it.")
    print(f"  figures: {'identical' if ok_fig else 'DIFFERENT'} ({got['figures_sha256'][:16]}…)")
    print(f"  page:    {'identical' if ok_html else 'DIFFERENT'} ({got['html_sha256'][:16]}…)")
    print("  (The PDF's own bytes also hold the browser's print time, so the page, not the PDF, is compared.)")
    return ok_fig and ok_html


def main():
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Make a PDF statistics report of Project Korikath.")
    ap.add_argument("--user", help="an uploader's report instead of the whole project")
    ap.add_argument("--out", default=str(OUT_DIR), help="folder to save the PDF in")
    ap.add_argument("--verify", metavar="PDF", help="was this PDF issued by the tracker? its status?")
    ap.add_argument("--rebuild", metavar="REPORT_ID", help="regenerate an issued report from its archive state and compare")
    ap.add_argument("--register", action="store_true", help="list issued reports")
    ap.add_argument("--supersede", metavar="OLD_ID", help="record that a report was replaced by a corrected one")
    ap.add_argument("--by", metavar="NEW_ID")
    ap.add_argument("--reason")
    ap.add_argument("--render-html", help=argparse.SUPPRESS)  # used by --rebuild, inside a restored archive state
    ap.add_argument("--manifest", help=argparse.SUPPRESS)
    args = ap.parse_args()
    import issuance
    if args.verify:
        ok, lines = issuance.verify_pdf(args.verify)
        print("\n".join(lines))
        raise SystemExit(0 if ok else 1)
    if args.rebuild:
        raise SystemExit(0 if rebuild(args.rebuild) else 1)
    if args.register:
        problems = issuance.check_chain()
        for e in issuance.entries():
            print(f"{e['seq']:>4} {e['event']:<10} {e['report_id']}  " + (f"{e['status']:<11} {e['subject']}" if e["event"] == "issued"
                                                                        else f"by {e['superseded_by']}: {e['reason']}"))
        print("Register intact." if not problems else "REGISTER PROBLEMS:\n  " + "\n  ".join(problems))
        return
    if args.supersede:
        if not (args.by and args.reason):
            raise SystemExit("--supersede needs --by NEW_ID and --reason")
        issuance.supersede(args.supersede, args.by, args.reason)
        print(f"Recorded: {args.supersede} is superseded by {args.by}.")
        return
    if args.render_html:
        m = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
        D = normalised(dashboard_data())
        page_html = render_html(D, m)
        Path(args.render_html).write_text(page_html, encoding="utf-8")
        print(json.dumps({"figures_sha256": issuance.figures_fingerprint(D), "html_sha256": issuance.sha256(page_html)}))
        return
    path = make_report(args.user, args.out)
    print(f"Report saved: {path}")


if __name__ == "__main__":
    main()
