"""Who made each change: the user and the edit (with a diff link) behind every change the tracker reports.

Crawl changes (on Commons):
  - a file added to, removed from or moved between the project's categories, a category added, moved or
    taken out of the tree: the edit of that file or category page, between the two crawls, whose diff adds or
    removes that category. A new upload is credited to the upload itself.
  - a file or category renamed: the move log; deleted: the deletion log (already looked up by the crawl).
  - a file that left the tree because its whole category did: credited to that category's change.

On-wiki usage changes (on the wiki that uses the file):
  1. the edits of the page itself between the two usage checks, whose diff adds or removes the file name;
  2. if none: the edits, in that time, of the templates, modules and other pages the page transcludes
     (an infobox, a navbox, a rotating "picture of the day" subpage);
  3. if none: the edits of the page's Wikidata item (infoboxes that take their image from Wikidata);
  4. if none: the same, a week further back, since wikis update their usage records in the background and an
     edit can show up a check later (marked "recorded late");
  5. a page that no longer exists: its deletion log; a page that was renamed: its move log, which then also
     explains the file appearing on the new title.
  When none of these shows the file name, the change is marked "not found" with a link to the page history;
  that happens when the file came in through a template edited outside the time window, a gallery or list
  page built by a bot, and similar indirect routes.

A match means the diff's added lines contain the name more often than its removed lines (for an addition), or
the other way round (for a removal); names are compared without "File:"/"Category:", case-insensitively, with
"_" read as a space. Requests are made one at a time with maxlag, like the rest of the tracker, and capped per
run; changes beyond the cap are marked "not looked up".

Each result is a dict: {"user", "timestamp" (UTC), "comment", "url" (diff or log link), "how", "page"} or, when
nothing was found, {"how": "not found", "url": history link}.
"""

import html
import re
import sys
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

import tracker

COMMONS = "commons.wikimedia.org"
WIKIDATA = "www.wikidata.org"
from methodology import (  # noqa: E402
    ATTRIBUTION_LATE as LATE, ATTRIBUTION_MAX_OTHER_REVISIONS as MAX_OTHER_REVISIONS,
    ATTRIBUTION_MAX_REQUESTS as MAX_REQUESTS, ATTRIBUTION_MAX_REVISIONS as MAX_REVISIONS,
    ATTRIBUTION_MAX_SECONDS as MAX_SECONDS, ATTRIBUTION_MAX_TEMPLATES as MAX_TEMPLATES)

TAG = re.compile(r"<[^>]+>")
CELL = re.compile(r'<td class="diff-(added|deleted)line[^"]*"[^>]*>(.*?)</td>', re.S)
SPACES = re.compile(r"\s+")
REDIRECT = re.compile(r"(?:^|\s)#\s*(?:redirect|перенаправление|weiterleitung|redirection|redirección|পুনর্নির্দেশ|تحويل)")


def utc(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def bare(title):
    """'File:Foo_bar.jpg' -> 'foo bar.jpg' (namespace dropped, compared case-insensitively)."""
    name = title.split(":", 1)[1] if ":" in title else title
    return SPACES.sub(" ", name.replace("_", " ")).strip().lower()


def norm(text):
    return SPACES.sub(" ", html.unescape(TAG.sub(" ", text)).replace("_", " ")).lower()


def count(needle, text):
    # Not part of a longer name: "x.jpg" doesn't match "x.jpg.jpg", "Cat-A" doesn't match "Cat-A-B".
    return len(re.findall(r"(?<![\w\-])" + re.escape(needle) + r"(?![\w\-]|\.\w)", text))


def page_url(host, title):
    return f"https://{host}/wiki/" + urllib.parse.quote(title.replace(" ", "_"), safe=":/()',")


def history_url(host, title):
    return f"https://{host}/w/index.php?" + urllib.parse.urlencode({"title": title, "action": "history"})


class OutOfBudget(Exception):
    pass


class Finder:
    def __init__(self, user_agent):
        self.user_agent = user_agent
        self.apis = {}
        self.revs = {}       # (host, title, start, end) -> page info
        self.diffs = {}      # (host, revid) -> (added text, removed text)
        self.templates = {}  # (host, title, start, end) -> [(template title, revisions)]
        self.requests = 0
        self.errors = 0
        self.started = time.monotonic()

    def api(self, host):
        if host not in self.apis:
            self.apis[host] = tracker.CommonsAPI(self.user_agent, host)
        return self.apis[host]

    def get(self, host, **params):
        if self.requests >= MAX_REQUESTS or time.monotonic() - self.started > MAX_SECONDS:
            raise OutOfBudget()
        self.requests += 1
        return self.api(host).get(**params)

    def close(self):
        for a in self.apis.values():
            if a.conn:
                a.conn.close()

    # ------------------------------------------------------------- building blocks

    def page(self, host, title, start, end, limit=MAX_REVISIONS):
        """Revisions of a page in [start, end], newest first, plus whether it exists and its Wikidata item."""
        key = (host, title, start, end, limit)
        if key not in self.revs:
            data = self.get(host, action="query", prop="revisions|pageprops", titles=title, ppprop="wikibase_item",
                            rvprop="ids|user|timestamp|comment|size", rvstart=iso(end), rvend=iso(start),
                            rvlimit=str(limit))
            p = data["query"]["pages"][0]
            self.revs[key] = {"missing": bool(p.get("missing") or p.get("invalid")), "title": p.get("title", title),
                              "revisions": p.get("revisions", []),
                              "item": (p.get("pageprops") or {}).get("wikibase_item")}
        return self.revs[key]

    def diff(self, host, rev):
        """(added text, removed text) of one revision, normalised for matching."""
        key = (host, rev["revid"])
        if key not in self.diffs:
            added = removed = ""
            try:
                if rev.get("parentid"):
                    data = self.get(host, action="compare", fromrev=str(rev["parentid"]), torev=str(rev["revid"]), prop="diff")
                    for side, cell in CELL.findall((data.get("compare") or {}).get("body", "")):
                        if side == "added":
                            added += " \n " + cell
                        else:
                            removed += " \n " + cell
                else:  # the page's first revision: everything in it was added
                    data = self.get(host, action="query", prop="revisions", revids=str(rev["revid"]),
                                    rvprop="content", rvslots="main")
                    r = data["query"]["pages"][0].get("revisions", [{}])[0]
                    added = ((r.get("slots") or {}).get("main") or {}).get("content", "")
            except OutOfBudget:
                raise
            except Exception as e:  # noqa: BLE001  hidden or deleted revisions, odd content models
                self.errors += 1
                print(f"  could not read diff {host} r{rev['revid']}: {e}", file=sys.stderr)
            self.diffs[key] = (norm(added), norm(removed))
        return self.diffs[key]

    def edit(self, host, rev, title, how):
        user = rev.get("user") or ("(hidden)" if rev.get("userhidden") else "(unknown)")
        if rev.get("parentid"):
            url = f"https://{host}/w/index.php?diff={rev['revid']}&oldid={rev['parentid']}"
        else:
            url = f"https://{host}/w/index.php?oldid={rev['revid']}"
        return {"user": user, "timestamp": rev.get("timestamp"), "comment": rev.get("comment", ""),
                "url": url, "how": how, "page": title, "wiki": host}

    def match(self, host, title, revisions, needles, how):
        """needles: {key: (name, "added"|"removed")} still unresolved. Returns {key: edit} for those whose name
        a revision adds (or removes); the newest such revision wins."""
        found = {}
        for rev in revisions:
            if len(found) == len(needles):
                break
            added, removed = self.diff(host, rev)
            if not added and not removed:
                continue
            for key, (name, direction) in needles.items():
                if key in found:
                    continue
                a, r = count(name, added), count(name, removed)
                if (direction == "added" and a > r) or (direction == "removed" and r > a):
                    found[key] = self.edit(host, rev, title, how)
        return found

    def log(self, host, kind, title, start, end):
        """The newest deletion or move log entry for a page in [start, end]."""
        data = self.get(host, action="query", list="logevents", letype=kind, letitle=title, lestart=iso(end),
                        leend=iso(start), lelimit="1", leprop="ids|user|timestamp|comment|details|title")
        ev = (data["query"].get("logevents") or [None])[0]
        if not ev:
            return None
        how = "page deleted" if kind == "delete" else f"page moved to {(ev.get('params') or {}).get('target_title', '?')}"
        return {"user": ev.get("user") or "(hidden)", "timestamp": ev.get("timestamp"), "comment": ev.get("comment", ""),
                "url": f"https://{host}/wiki/Special:Redirect/logid/{ev['logid']}", "how": how, "page": title, "wiki": host,
                "target": (ev.get("params") or {}).get("target_title")}

    def recent_templates(self, host, title, start, end):
        """Pages transcluded by a page (templates, modules, subpages) that were edited in [start, end], with those edits."""
        key = (host, title, start, end)
        if key not in self.templates:
            edited = []
            cont = {}
            while True:
                data = self.get(host, action="query", generator="templates", titles=title,
                                gtllimit="max", prop="revisions", rvprop="timestamp", **cont)
                for p in (data.get("query") or {}).get("pages", []):
                    r = (p.get("revisions") or [{}])[0]
                    if r.get("timestamp") and start <= utc(r["timestamp"]):
                        edited.append(p["title"])
                if "continue" not in data:
                    break
                cont = data["continue"]
            out = []
            for t in sorted(edited)[:MAX_TEMPLATES]:
                out.append((t, self.page(host, t, start, end, MAX_OTHER_REVISIONS)["revisions"]))
            self.templates[key] = out
        return self.templates[key]

    # ------------------------------------------------------------------- the two jobs

    def usage_changes(self, rows, start, end):
        """rows: [(row id, wiki, page title, file title, "added"|"removed")] from one usage check.
        Returns {row id: edit}."""
        out = {}
        by_page = {}
        info = {}
        for rid, wiki, page, file_title, change in rows:
            by_page.setdefault((wiki, page), {})[rid] = (bare(file_title), change)
            info[rid] = (wiki, page, bare(file_title), change)
        try:
            for n, ((wiki, page), needles) in enumerate(sorted(by_page.items()), 1):
                try:
                    out.update(self._usage_page(wiki, page, needles, start, end))
                except OutOfBudget:
                    raise
                except Exception as e:  # noqa: BLE001  one wiki failing must not stop the rest
                    self.errors += 1
                    print(f"  {wiki} {page}: {e}", file=sys.stderr)
                if n % 25 == 0:
                    print(f"  attributed {n:,} / {len(by_page):,} pages ({self.requests:,} requests)")
            # A page that was moved: its old title loses the file and the new title gains it. The move log of
            # the old title credits both.
            moved = {}
            for rid, (wiki, page, name, change) in info.items():
                if change == "removed" and rid not in out:
                    ev = self.log(wiki, "move", page, start - LATE, end)
                    if ev:
                        out[rid] = ev
                        moved[(wiki, ev["target"], name)] = ev
            for rid, (wiki, page, name, change) in info.items():
                if change == "added" and rid not in out and (wiki, page, name) in moved:
                    ev = moved[(wiki, page, name)]
                    out[rid] = dict(ev, how=f"page moved here from {ev['page']}")
        except OutOfBudget:
            for rid, (wiki, page, _, _) in info.items():
                out.setdefault(rid, {"how": "not looked up (too many changes in one check)",
                                     "url": history_url(wiki, page), "page": page, "wiki": wiki})
        for rid, (wiki, page, _, _) in info.items():
            if rid not in out:
                out[rid] = self._unexplained(wiki, page, start, end)
        return out

    def _unexplained(self, wiki, page, start, end):
        """No edit names the file. If the page was edited in that time, link to all those edits as one diff
        and name who made them, without crediting anyone (the file may have come or gone with a template)."""
        try:
            revs = self.page(wiki, page, start, end)["revisions"]
        except Exception:  # noqa: BLE001
            revs = []
        if not revs:
            return {"how": "not found", "url": history_url(wiki, page), "page": page, "wiki": wiki}
        users = list(dict.fromkeys(r.get("user") or "(hidden)" for r in revs))
        newest, oldest = revs[0], revs[-1]
        url = (f"https://{wiki}/w/index.php?diff={newest['revid']}&oldid={oldest['parentid']}" if oldest.get("parentid")
               else history_url(wiki, page))
        more = f" and {len(users) - 5} more" if len(users) > 5 else ""
        return {"how": f"not found by name; the page was edited in that time by {', '.join(users[:5])}{more}",
                "url": url, "page": page, "wiki": wiki}

    def _usage_page(self, wiki, page, needles, start, end):
        found = {}
        info = self.page(wiki, page, start, end)
        if info["missing"]:
            ev = self.log(wiki, "delete", page, start - LATE, end)
            return {rid: ev for rid in needles} if ev else found
        # First the time between the two checks; then, for what is still unexplained, a week further back:
        # wikis update their usage records in the background, so an edit can show up in a later check.
        for since, late in ((start, False), (start - LATE, True)):
            info = self.page(wiki, page, since, end)
            sources = [(wiki, info["title"], info["revisions"], "page edit")]
            sources += [(wiki, t, revs, f"edit of {t}, which the page uses")
                        for t, revs in self.recent_templates(wiki, info["title"], since, end)]
            if info["item"] and wiki != WIKIDATA:
                sources.append((WIKIDATA, info["item"], self.page(WIKIDATA, info["item"], since, end, MAX_OTHER_REVISIONS)["revisions"],
                                f"Wikidata edit of {info['item']}, which the page's infobox reads"))
            for host, title, revs, how in sources:
                left = {k: v for k, v in needles.items() if k not in found}
                if not left:
                    return found
                for k, e in self.match(host, title, revs, left, how).items():
                    found[k] = dict(e, how=e["how"] + " (made before the previous check; recorded late by the wiki)") if late else e
            # A page turned into a redirect loses everything it showed.
            left = [k for k, (_, d) in needles.items() if k not in found and d == "removed"]
            if left:
                for rev in info["revisions"]:
                    if not rev.get("parentid"):
                        continue  # a redirect created by a page move: the move log explains it (usage_changes)
                    added, _ = self.diff(wiki, rev)
                    if REDIRECT.search(added):
                        e = self.edit(wiki, rev, info["title"], "page turned into a redirect")
                        found.update({k: e for k in left})
                        break
        return found

    def crawl_changes(self, changes, start, end):
        """Adds "by" (one edit) or "by_category" ({category: edit}) to the items of a crawl's changes."""
        ws = start - timedelta(hours=1)

        def page_edits(title, needles):
            info = self.page(COMMONS, title, ws, end)
            return info, self.match(COMMONS, info["title"], info["revisions"], needles, "page edit")

        def creation(info):
            first = [r for r in info["revisions"] if not r.get("parentid")]
            return first[0] if first else None

        for f in changes.get("files_added", []):
            info, found = page_edits(f["title"], {c: (bare(c), "added") for c in f["now_in"]})
            first = creation(info)
            if first:
                f["by"] = self.edit(COMMONS, first, info["title"], "uploaded")
            elif found:
                f["by_category"] = found
                f["by"] = next(iter(found.values()))
            else:
                f["by"] = {"how": "not found", "url": history_url(COMMONS, f["title"])}

        for f in changes.get("files_moved", []):
            needles = {c: (bare(c), "removed") for c in f["removed_from"]}
            needles.update({c: (bare(c), "added") for c in f["added_to"]})
            _, found = page_edits(f["title"], needles)
            f["by_category"] = {c: found.get(c) or {"how": "not found", "url": history_url(COMMONS, f["title"])} for c in needles}

        for kind, old_key in (("files_renamed", "old_title"), ("categories_renamed", "old_title")):
            for f in changes.get(kind, []):
                f["by"] = self.log(COMMONS, "move", f[old_key], ws, end) or {"how": "not found", "url": history_url(COMMONS, f["title"])}

        for c in changes.get("categories_added", []):
            info, found = page_edits(c["title"], {p: (bare(p), "added") for p in c["now_under"]})
            first = creation(info)
            c["by"] = (self.edit(COMMONS, first, info["title"], "category created") if first
                       else next(iter(found.values()), None)) or {"how": "not found", "url": history_url(COMMONS, c["title"])}

        for c in changes.get("categories_moved", []):
            needles = {p: (bare(p), "removed") for p in c["removed_from"]}
            needles.update({p: (bare(p), "added") for p in c["added_to"]})
            _, found = page_edits(c["title"], needles)
            c["by_category"] = {p: found.get(p) or {"how": "not found", "url": history_url(COMMONS, c["title"])} for p in needles}

        gone_cats = {}
        for c in changes.get("categories_removed", []):
            if c.get("status") == "deleted":
                c["by"] = self._deletion(c, ws, end)
            else:
                title = c.get("current_title") or c["title"]
                _, found = page_edits(title, {p: (bare(p), "removed") for p in c["was_under"]})
                c["by"] = next(iter(found.values()), None) or {"how": "not found", "url": history_url(COMMONS, title)}
            gone_cats[c["title"]] = c["by"]

        for f in changes.get("files_removed", []):
            if f.get("status") == "deleted":
                f["by"] = self._deletion(f, ws, end)
                continue
            title = f.get("current_title") or f["title"]
            _, found = page_edits(title, {c: (bare(c), "removed") for c in f["was_in"]})
            if found:
                f["by"] = next(iter(found.values()))
            else:
                via = [c for c in f["was_in"] if c in gone_cats]
                f["by"] = (dict(gone_cats[via[0]], how=f"its category {via[0]} left the tree") if via
                           else {"how": "not found", "url": history_url(COMMONS, title)})

    def _deletion(self, item, start, end):
        d = item.get("deletion")
        if d:
            return {"user": d.get("user"), "timestamp": d.get("timestamp"), "comment": d.get("comment", ""),
                    "url": f"https://{COMMONS}/w/index.php?" + urllib.parse.urlencode({"title": "Special:Log", "page": item["title"]}),
                    "how": "deleted", "page": item["title"], "wiki": COMMONS}
        return self.log(COMMONS, "delete", item["title"], start, end) or {"how": "not found", "url": history_url(COMMONS, item["title"])}
