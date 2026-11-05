"""PDF statistics reports: the whole project, or one contributor.

The report is built as an HTML page (with charts drawn as SVG) and printed to PDF by Microsoft Edge
or Google Chrome in headless mode, so Bengali and other scripts are shaped correctly. The project
figures are the ones the dashboard shows (read from dashboard.html); a contributor's report adds
details worked out from the saved data files. No requests go to Wikimedia, apart from the browser
loading a few thumbnails from Commons.

    python report.py                      project report, saved in your Downloads folder
    python report.py --user "Name"        one contributor's report
"""

import argparse
import html
import json
import math
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
ACCENT, HUMAN, MUTED = "#2f6b4f", "#7fb397", "#8a867d"
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


def month_name(m):
    return datetime.strptime(m, "%Y-%m").strftime("%b %Y")


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
        parts.append(f'<line x1="{left}" x2="{width - right}" y1="{y(v):.1f}" y2="{y(v):.1f}" stroke="#e2dfd6"/>'
                     f'<text x="{left - 6}" y="{y(v) + 4:.1f}" text-anchor="end">{axis_label(v)}</text>')
        v += step
    years = sorted({m[:4] for m in months})
    every = max(1, math.ceil(len(years) / 12))
    for i, m in enumerate(months):
        # Label each January (and the first month, unless a January follows too closely to fit both).
        if m.endswith("-01") or (i == 0 and not any(x.endswith("-01") for x in months[1:max(2, len(months) // 15)])):
            if (int(m[:4]) - int(years[0])) % every == 0:
                parts.append(f'<line x1="{x(i):.1f}" x2="{x(i):.1f}" y1="{top + h}" y2="{top + h + 4}" stroke="#8a867d"/>'
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
    parts.append(f'<line x1="{left}" x2="{width - right}" y1="{top + h}" y2="{top + h}" stroke="#8a867d"/></svg>')
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
    return f'<section><h2>{esc(title)}</h2>{f"<p class=intro>{intro}</p>" if intro else ""}{body}</section>'


def thumbs(titles):
    cells = "".join(
        f'<figure><img src="https://commons.wikimedia.org/wiki/Special:FilePath/{urllib.parse.quote(short(t).replace(" ", "_"))}?width=320" '
        f'alt="" onerror="this.replaceWith(Object.assign(document.createElement(\'div\'),{{className:\'noimg\',textContent:\'No preview\'}}))">'
        f'<figcaption>{esc(short(t))}</figcaption></figure>' for t in titles)
    return f'<div class="thumbs">{cells}</div>'


CSS = """
@page { size: A4; margin: 16mm 14mm 18mm;
  @bottom-left { content: "Project Korikath statistics"; font: 8pt 'Segoe UI', 'Nirmala UI', sans-serif; color: #8a867d; }
  @bottom-right { content: "Page " counter(page) " of " counter(pages); font: 8pt 'Segoe UI', sans-serif; color: #8a867d; } }
* { box-sizing: border-box; }
body { margin: 0; color: #1d1c1a; font: 9.5pt/1.45 'Segoe UI', 'Nirmala UI', 'Noto Sans Bengali', sans-serif;
  -webkit-print-color-adjust: exact; print-color-adjust: exact; }
a { color: #2d5a8c; text-decoration: none; }
header { border-bottom: 3px solid #2f6b4f; padding-bottom: 10px; margin-bottom: 14px; }
header .kicker { color: #2f6b4f; font-weight: 700; letter-spacing: .06em; text-transform: uppercase; font-size: 8.5pt; }
h1 { font-size: 22pt; margin: 2px 0 6px; line-height: 1.15; }
h2, h3 { break-after: avoid; }
h2 { font-size: 13pt; margin: 0 0 6px; color: #2f6b4f; border-bottom: 1px solid #e2dfd6; padding-bottom: 3px; }
h3 { font-size: 10.5pt; margin: 12px 0 4px; }
.meta { display: grid; grid-template-columns: max-content 1fr; gap: 1px 12px; font-size: 9pt; }
.meta b { color: #6b675f; font-weight: 600; }
section { margin: 16px 0 0; }
section.break { break-before: page; }
.intro, .sub { color: #6b675f; }
.intro { margin: 0 0 8px; }
.sub { font-size: 8.5pt; margin: 4px 0; }
.kpis { display: grid; grid-template-columns: repeat(4, 1fr); gap: 6px; margin: 6px 0 4px; }
.kpi { border: 1px solid #e2dfd6; border-radius: 6px; padding: 6px 8px; background: #faf9f6; break-inside: avoid; }
.kpi .label { color: #6b675f; font-size: 8pt; }
.kpi .value { font-size: 14pt; font-weight: 650; line-height: 1.2; font-variant-numeric: tabular-nums; }
.kpi .note { color: #6b675f; font-size: 7.5pt; line-height: 1.3; margin-top: 2px; }
table { width: 100%; border-collapse: collapse; font-size: 8.5pt; margin: 4px 0; }
thead { display: table-header-group; }
tr { break-inside: avoid; }
th { text-align: left; color: #6b675f; font-weight: 600; border-bottom: 1.5px solid #cfcbc0; padding: 3px 5px; }
td { border-bottom: 1px solid #eeebe4; padding: 2.5px 5px; vertical-align: top; overflow-wrap: anywhere; }
.num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
.cols { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
.cols > div { min-width: 0; }
svg.chart { display: block; margin: 4px 0; font: 7.5pt 'Segoe UI', sans-serif; fill: #6b675f; }
.legend { font-size: 8pt; color: #6b675f; display: flex; gap: 14px; }
.legend i { display: inline-block; width: 9px; height: 9px; border-radius: 2px; margin-right: 4px; vertical-align: -1px; }
.chartbox { break-inside: avoid; }
.thumbs { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin: 6px 0; }
.thumbs figure { margin: 0; break-inside: avoid; }
.thumbs img, .noimg { width: 100%; height: 95px; object-fit: cover; border-radius: 4px; background: #f0eee8; display: block; }
.noimg { display: flex; align-items: center; justify-content: center; color: #8a867d; font-size: 8pt; }
.thumbs figcaption { font-size: 7.5pt; color: #6b675f; line-height: 1.25; margin-top: 2px;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.removed { color: #a33a2b; }
.pill { display: inline-block; font-size: 7.5pt; padding: 0 5px; border-radius: 8px; background: #e3efe8; color: #2f6b4f; }
"""


def page(title, kicker, meta_rows, body):
    meta = "".join(f"<b>{esc(k)}</b><span>{v}</span>" for k, v in meta_rows)
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{esc(title)}</title>'
            f"<style>{CSS}</style></head><body><header><div class=kicker>{esc(kicker)}</div><h1>{esc(title)}</h1>"
            f'<div class="meta">{meta}</div></header>{body}</body></html>')


# ------------------------------------------------------------------------ data


def dashboard_data():
    """The data the dashboard shows, as embedded in dashboard.html (built if it doesn't exist yet)."""
    if not tracker.DASHBOARD_FILE.exists():
        tracker.build_dashboard()
    text = tracker.DASHBOARD_FILE.read_text(encoding="utf-8")
    start = text.index("const DATA = ") + len("const DATA = ")
    data, _ = json.JSONDecoder().raw_decode(text[start:])
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


def project_report(D, generated):
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
        meta.append(("Views counted", f"to {esc(day(V['checked_through']))} · {fmt(V['with_history'])} of {fmt(V['files'])} files"
                     + (" (still being collected)" if V["with_history"] < V["files"] else "")))
    body = []

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
        cards.append(("All-time views" if V["with_history"] >= V["files"] else "Views so far", big(V["all"]),
                      f"{big(V['human'])} by people ({pct(V['human'], V['all'])})"))
    if U:
        cards += [("Files used on other wikis", fmt(U["all"]["files_used"]), f"{pct(U['all']['files_used'], total)} of all files"),
                  ("Pages using the files", fmt(U["all"]["pages"]), f"on {fmt(U['all']['wikis'])} wikis · {fmt(U['all']['usages'])} uses")]
    if C:
        cut = datetime.fromisoformat(snap["taken_at"]).timestamp() - 30 * 86400
        new = sum(1 for u in C["users"] if datetime.fromisoformat(u[2].replace("Z", "+00:00")).timestamp() >= cut)
        cards.append(("Contributors", fmt(len(C["users"])), f"{fmt(new)} new in the last 30 days"))
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
        months = [(m, a, h) for m, a, h, _ in V["months"]]
        years = {}
        for m, a, h in months:
            y = years.setdefault(m[:4], [0, 0])
            y[0] += a
            y[1] += h
        partial = V["with_history"] < V["files"]
        body.append(section("Views", kpis([
            ("All views" + (" so far" if partial else ""), big(V["all"]), fmt(V["all"])),
            ("Views by people", big(V["human"]), pct(V["human"], V["all"]) + " of all views"),
            ("Views by bots and crawlers", big(V["all"] - V["human"]), pct(V["all"] - V["human"], V["all"])),
            ("Files counted", fmt(V["with_history"]), f"of {fmt(V['files'])}, to {day(V['checked_through'])}")])
            + '<div class="chartbox"><h3>Views per month</h3>' + chart(months, colors=(MUTED, ACCENT), legend=["All views", "By people"])
            + "<h3>All-time views (running total)</h3>" + chart(running_total([(m, a) for m, a, _ in months]), "area") + "</div>"
            + table(["Year", "All views", "By people", "Share by people"],
                    [[y, fmt(a), fmt(h), pct(h, a)] for y, (a, h) in sorted(years.items())], num=(1, 2, 3))
            + "<h3>Most viewed files</h3>" + thumbs([r[0] for r in V["top_files"][:8]])
            + table(["File", "Uploaded by", "All views", "By people"],
                    [[link(t), esc(u or "—"), fmt(a), fmt(h)] for t, a, h, u in V["top_files"][:25]], num=(2, 3))
            + "<h3>Most viewed categories</h3>"
            + table(["Category", "Files", "All views", "By people"],
                    [[link(t), fmt(n), fmt(a), fmt(h)] for t, a, h, n in V["categories"][:20]], num=(1, 2, 3)),
            "How often each file itself was loaded (Wikimedia media requests), from January 2015. Months are complete months."
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
                ["Uploader", "Files used", "Uses"], [[esc(r[0]), fmt(r[2]), fmt(r[3])] for r in sorted(a["by_uploader"], key=lambda r: -r[3])[:20]], num=(1, 2))
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
            + "<h3>Promotions per month</h3>" + table(
                ["Month", "Quality", "Featured", "Valued", "Featured media"],
                [[month_name(m), *(fmt(monthly[m].get(k, 0)) for k in ("qi", "fp", "vi", "fm"))] for m in sorted(monthly, reverse=True)],
                num=(1, 2, 3, 4))
            + "<h3>Contributors with assessed files</h3>" + table(
                ["Contributor", "Quality", "Featured", "Valued", "Featured media", "POTD", "MOTD"],
                [[esc(u), *(fmt(n.get(k, 0)) for k in ("qi", "fp", "vi", "fm", "potd", "motd"))] for u, n in by_user[:30]],
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

    # Contributors
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
        body.append(section("Contributors", kpis([
            ("Contributors", fmt(len(users)), None),
            ("New in the last 30 days", fmt(sum(1 for u in users if ts(u[2]) >= cut)), "first upload in that time"),
            ("Active in the last 30 days", fmt(sum(1 for u in users if ts(u[3]) >= cut)), "uploaded at least one file"),
            ("Top 10 uploaded", pct(top10, total), "of all files"),
            ("Median per contributor", fmt(median), "files")])
            + '<div class="chartbox"><h3>Contributors over time (running total)</h3>'
            + chart(running_total(sorted(new_by.items())), "area")
            + "<h3>People uploading each month</h3>" + chart(sorted(active_by.items())) + "</div>"
            + "<h3>How much people upload</h3>" + table(["Uploaded", "Contributors", "Share", "Files", "Share of files"], group_rows, num=(1, 2, 3, 4))
            + "<h3>Top contributors</h3>" + table(
                ["#", "Contributor", "Files", "Share", "First upload", "Latest upload", "Used on wikis", "Quality", "Views"],
                [[str(i + 1), esc(u[0]), fmt(u[1]), pct(u[1], total), day(u[2]), day(u[3]), fmt(u[4]),
                  fmt(ab.get(u[0], {}).get("qi", 0)), big(vb[u[0]][0]) if u[0] in vb else "—"] for i, u in enumerate(users[:40])],
                num=(0, 2, 3, 6, 7, 8), note=f"Top 40 of {fmt(len(users))}. Each contributor's own report has the full statistics."),
            f"People who uploaded the {fmt(total)} files now in the project (the original uploader of each file). Months are in UTC."))
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
    return title, page(title, "Wikimedia Commons · Project Korikath", meta, "".join(body))


# ----------------------------------------------------------- contributor report


def contributor_details(name):
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
            out["views"][title] = [sum(m[0] for m in v[2].values()), sum(m[1] for m in v[2].values())]
            for m, (a, h) in v[2].items():
                row = out["view_months"].setdefault(m, [0, 0])
                row[0] += a
                row[1] += h
    out["largest"].sort(reverse=True)
    out["used"].sort(key=lambda r: (-r[1], r[0]))
    return out


def user_report(D, name, generated):
    C = D.get("contributors")
    if not C:
        raise RuntimeError("Contributor statistics aren't available yet.")
    users = C["users"]
    u = next((x for x in users if x[0] == name), None)
    if u is None:
        raise KeyError(f"No contributor named {name!r} in the project.")
    snap, langs = D["snapshot"], D.get("languages") or {}
    A, V, P, U = (D.get(k) for k in ("assessments", "views", "places", "usage"))
    total = snap["total_files"]
    n, nfiles, first, last, used, uses, per_month, cats_top, ncats = u
    rank = users.index(u) + 1
    X = contributor_details(name)
    user_url = "https://commons.wikimedia.org/wiki/User:" + urllib.parse.quote(name.replace(" ", "_"))
    list_url = "https://commons.wikimedia.org/wiki/Special:ListFiles/" + urllib.parse.quote(name.replace(" ", "_"))
    hist = D.get("history") or []
    latest = hist[-1] if hist else {}
    meta = [("Report generated", esc(fmt_time(generated))),
            ("Built on the crawl of", f"{esc(fmt_time(snap['taken_at']))}" + (f" ({esc(latest['mode'])} crawl)" if latest.get("taken_at") == snap["taken_at"] else "")),
            ("Commons", f'<a href="{esc(user_url)}">User page</a> · <a href="{esc(list_url)}">All this member&#39;s uploads</a>')]
    if V:
        meta.append(("Views counted", f"to {esc(day(V['checked_through']))}"
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
             ("Rank by files", f"#{fmt(rank)}", f"of {fmt(len(users))} contributors"),
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
        cards.append(("Views" + (" so far" if V["with_history"] < V["files"] else ""), big(tot_views[0]),
                      f"{big(tot_views[1])} by people ({pct(tot_views[1], tot_views[0])})"
                      + (f" · #{fmt(views_rank)} by views" if views_rank else "")))
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
        body.append(section("Views", '<div class="chartbox"><h3>Views per month</h3>'
                            + chart(vm, colors=(MUTED, ACCENT), legend=["All views", "By people"])
                            + "<h3>All-time views (running total)</h3>" + chart(running_total([(m, a) for m, a, _ in vm]), "area") + "</div>"
                            + "<h3>Most viewed files</h3>" + thumbs([t for t, _ in top[:8]])
                            + table(["File", "All views", "By people"], [[link(t), fmt(a), fmt(h)] for t, (a, h) in top[:25]], num=(1, 2)),
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
                             + table(["File", "Promoted"], [[link(t), day(ts) if ts else "—"] for t, ts in items], num=(1,)))
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

    title = f"{name}: contributor statistics"
    return title, page(title, "Project Korikath · contributor report", meta, "".join(body))


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


def make_report(user=None, out_dir=OUT_DIR):
    """Build a report PDF and return its path. The file name holds the time it was generated (UTC)."""
    generated = datetime.now(timezone.utc)
    D = dashboard_data()
    if user:
        _, page_html = user_report(D, user, generated)
        name = f"Korikath_contributor_{safe_name(user)}_{file_stamp(generated)}.pdf"
    else:
        _, page_html = project_report(D, generated)
        name = f"Korikath_statistics_report_{file_stamp(generated)}.pdf"
    pdf = html_to_pdf(page_html, Path(out_dir) / name)
    # Keep the PDF and everything it was built from (the data embedded in dashboard.html and all data files):
    # `python archive.py find <pdf>` later tells which state an issued report came from.
    import archive
    import hashlib
    archive.safe_record("report", include_dashboard=True, extra_files={f"issued_reports/{name}": pdf},
                        extra={"pdf": name, "pdf_sha256": hashlib.sha256(pdf.read_bytes()).hexdigest(),
                               "report_type": "contributor" if user else "project", "contributor": user,
                               "generated_at": generated.strftime("%Y-%m-%dT%H:%M:%SZ")})
    return pdf


def main():
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Make a PDF statistics report of Project Korikath.")
    ap.add_argument("--user", help="a contributor's report instead of the whole project")
    ap.add_argument("--out", default=str(OUT_DIR), help="folder to save the PDF in")
    args = ap.parse_args()
    path = make_report(args.user, args.out)
    print(f"Report saved: {path}")


if __name__ == "__main__":
    main()
