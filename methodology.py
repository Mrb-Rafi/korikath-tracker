"""The methodology register: every definition, rule, threshold and known limitation the tracker counts by.

This is the single source for them. tracker.py, attribution.py and report.py import the constants from
here, and the PDF report prints the definitions and limitations from here, so what the code does and what the
report says it does cannot drift apart.

Any change to a definition or a rule below is a change of method: bump METHOD_VERSION and add an entry to
CHANGELOG.md saying which figures it affects. `python methodology.py --check` (run by the tests) fails when the
rules changed but the version didn't: each version's fingerprint is recorded in methodology_versions.json.

    python methodology.py            print the definitions, parameters and limitations
    python methodology.py --check    fail if the rules changed without a new METHOD_VERSION
    python methodology.py --record   record the fingerprint of the current METHOD_VERSION (after bumping it)
"""

import hashlib
import json
import re
import sys
from datetime import timedelta
from pathlib import Path

METHOD_VERSION = "M1"
METHOD_DATE = "2026-11-05"

# ------------------------------------------------------------------------------------------------ parameters

TIME_ZONE = "UTC"                        # every stored and shown time; days and months are UTC days and months
QUICK_CHECK_OVERLAP = timedelta(hours=1)  # a quick check / assessment check also reads what was added this long
                                          # before the previous run started, so nothing added around then is missed
ROTATION_DAYS = 30                        # locations and daily view counts: 1/30 of the files are re-checked each day
CATCH_ALL_SHARE = 0.25                    # a category directly holding more than this share of all files is a
                                          # "catch-all" (All images, Uploaded via Campaign, ...): left out of
                                          # per-uploader category lists only
WRONG_CLOCK_EARLIEST_GAP_DAYS = -1        # a photo "taken" more than this many days after its upload: wrong camera clock
WRONG_CLOCK_BEFORE_YEAR = 2005            # a photo "taken" before this year: camera clock never set
ATTRIBUTION_OVERLAP = timedelta(hours=1)  # who-made-it lookups also read edits this long before the previous check
ATTRIBUTION_LATE = timedelta(days=7)      # ... and, for what is still unexplained, this far further back
ATTRIBUTION_MAX_REQUESTS = 1500           # per check, across all wikis
ATTRIBUTION_MAX_SECONDS = 15 * 60         # per check; changes left over are marked "not looked up"
ATTRIBUTION_MAX_REVISIONS = 50            # revisions of the page itself looked at
ATTRIBUTION_MAX_OTHER_REVISIONS = 15      # revisions of a template or Wikidata item looked at
ATTRIBUTION_MAX_TEMPLATES = 8             # recently edited templates/modules checked per page


# ------------------------------------------------------------------------------------- rules moved from tracker.py

# Kinds of media
IMAGE_EXTS = {"jpg", "jpeg", "png", "gif", "svg", "tif", "tiff", "webp", "xcf", "bmp", "avif", "heic"}
MEDIA_KINDS = {"BITMAP": "image", "DRAWING": "image", "AUDIO": "audio", "VIDEO": "video",
               "MULTIMEDIA": "video", "OFFICE": "document", "TEXT": "document"}

# Device brands as recorded in EXIF ("NIKON CORPORATION", "samsung", ...) -> one spelling, and what kind
# of device each brand (or model prefix) is.
BRANDS = [("nikon", "Nikon"), ("canon", "Canon"), ("sony", "Sony"), ("fujifilm", "Fujifilm"), ("fuji", "Fujifilm"),
          ("olympus", "Olympus"), ("om digital", "OM System"), ("panasonic", "Panasonic"), ("pentax", "Pentax"),
          ("ricoh", "Ricoh"), ("leica", "Leica"), ("hasselblad", "Hasselblad"), ("sigma", "Sigma"), ("gopro", "GoPro"),
          ("dji", "DJI"), ("apple", "Apple"), ("samsung", "Samsung"), ("xiaomi", "Xiaomi"), ("redmi", "Xiaomi"),
          ("poco", "Xiaomi"), ("oneplus", "OnePlus"), ("oppo", "Oppo"), ("vivo", "Vivo"), ("realme", "Realme"),
          ("huawei", "Huawei"), ("honor", "Honor"), ("google", "Google"), ("motorola", "Motorola"), ("nokia", "Nokia"),
          ("hmd", "Nokia"), ("tecno", "Tecno"), ("infinix", "Infinix"), ("itel", "itel"), ("walton", "Walton"),
          ("symphony", "Symphony"), ("lg", "LG"), ("htc", "HTC"), ("asus", "Asus"), ("lenovo", "Lenovo"),
          ("nothing", "Nothing"), ("blackberry", "BlackBerry"), ("kodak", "Kodak"), ("casio", "Casio"),
          ("insta360", "Insta360"), ("epson", "Epson"), ("hp", "HP"), ("plustek", "Plustek")]
PHONE_BRANDS = {"Apple", "Samsung", "Xiaomi", "OnePlus", "Oppo", "Vivo", "Realme", "Huawei", "Honor", "Google",
                "Motorola", "Nokia", "Tecno", "Infinix", "itel", "Walton", "Symphony", "LG", "HTC", "Asus", "Lenovo",
                "Nothing", "BlackBerry"}
CAMERA_BRANDS = {"Nikon", "Canon", "Fujifilm", "Olympus", "OM System", "Panasonic", "Pentax", "Ricoh", "Leica",
                 "Hasselblad", "Sigma", "Kodak", "Casio"}
SCANNER_BRANDS = {"Epson", "HP", "Plustek"}

# Quality / featured / valued images: Commons-wide categories that the assessment templates ({{QualityImage}}, {{Assessments|featured=1}},
# {{VI}}) add to a file as soon as it is promoted, and remove when it is demoted or delisted. Used
# instead of Korikath's own "Quality images of Project Korikath" etc., which are updated by hand.
# Nothing here reads Korikath's own categories.
ASSESS_CATS = {"qi": "Category:Quality images",
               "fp": "Category:Featured pictures on Wikimedia Commons",
               "vi": "Category:Valued images sorted by promotion date",
               "fm": "Category:Featured media"}
# Picture / Media of the Day on Commons' main page: the day's template (Template:Potd/2023-03-05,
# Template:Motd/2023-03-05, with language versions like "Template:Motd/2023-03-05 (en)") shows the file.
MAIN_PAGE_KINDS = ("potd", "motd")
ASSESS_NAMES = {"qi": "Quality image", "fp": "Featured picture", "vi": "Valued image", "fm": "Featured media",
                "potd": "Picture of the Day on Commons", "motd": "Media of the Day on Commons"}
DAY_TEMPLATE = re.compile(r"^Template:(Potd|Motd)/(\d{4}-\d{2}-\d{2})(?: \([\w-]+\))?$")

# Pages on other wikis that put a picture or media of the day on their main page, recognised by their
# names (there is no common marker, so this is a best-effort list; Commons has its own, exact check).
MOTD_PAGE = re.compile(r"\bmotd\b|media of the day|медиа дня|медіа дня|média du jour|medium des tages", re.I)
POTD_PAGE = re.compile("|".join([
    r"\bpotd\b", r"picture of the day", r"pic of the day", r"image of the day", r"photo of the day",
    "আজকের নির্বাচিত ছবি", "आज का चित्र", "Hình ảnh chọn lọc", "A nap képe", "오늘의 그림",
    "Bild des Tages", "Image du jour", "Imagen del día", "Immagine del giorno", "Foto do dia",
    "Imagem do dia", "Изображение дня", "Зображення дня", "Obrázek dne", "Ilustracja dnia",
    "Fotografia dnia", "今日の一枚", "每日图片", "每日圖片", "Afbeelding van de dag", "Dagens bild", "Dagens billede",
    "Päivän kuva", "Günün resmi", "Günün seçilmiş", "صورة اليوم", "تصویر روز", "Picha ya siku", "Gambar hari ini",
    "Gambar pilihan", "Larawan ng araw", "Слика дана", "Slika dana", "Imaginea zilei", "Тәулік суреті",
]), re.I)


# Candidate / nomination / proposal pages mention "picture of the day" too but aren't the main page.
NOT_MAIN_PAGE = re.compile(r"usulan|candidat|kandidat|nominat|proposal|propuesta|vot(e|ing|ación)|предлож|пропозиц", re.I)
MONTH_NAMES = {}
for _i, _names in enumerate([
        ("january", "jan", "জানুয়ারি", "জানুয়ারী"), ("february", "feb", "ফেব্রুয়ারি", "ফেব্রুয়ারী"),
        ("march", "mar", "মার্চ"), ("april", "apr", "এপ্রিল"), ("may", "মে"), ("june", "jun", "জুন"),
        ("july", "jul", "জুলাই"), ("august", "aug", "আগস্ট", "অগাস্ট"), ("september", "sep", "sept", "সেপ্টেম্বর"),
        ("october", "oct", "অক্টোবর"), ("november", "nov", "নভেম্বর"), ("december", "dec", "ডিসেম্বর")], 1):
    for _n in _names:
        MONTH_NAMES[_n] = _i

# Media request statistics
VIEWS_FROM = "20150101"  # Wikimedia's media request statistics start in January 2015



# ----------------------------------------------------------------------------------------------- definitions
# Printed in the PDF report's definitions appendix. "limits" are the IDs of LIMITATIONS that apply.

DEFINITIONS = [
    {"id": "D01", "name": "Project files",
     "definition": "Every file in the category tree below Category:Project Korikath on Wikimedia Commons, at any "
                   "depth, counted once by its page ID even when it is in several categories (the way PetScan counts).",
     "unit": "files", "source": "Commons API: list=categorymembers, prop=categoryinfo",
     "excludes": "Categories listed in exclude_categories in config.json, with everything below them.",
     "limits": ["L01", "L02", "L03"]},
    {"id": "D02", "name": "Categories in the tree",
     "definition": "Every category reached from the root category through subcategories, counted once.",
     "unit": "categories", "source": "Commons API: list=categorymembers", "limits": ["L01"]},
    {"id": "D03", "name": "Files removed / added / moved / renamed",
     "definition": "Differences between two crawls, matched by page ID: removed = in the earlier crawl only; added = "
                   "in the later only; moved = in both, in different tree categories; renamed = same page ID, new title. "
                   "A removed file is 'deleted' if its page no longer exists, otherwise 'moved outside'.",
     "unit": "files", "source": "Saved crawl snapshots; Commons deletion log", "limits": ["L02", "L03"]},
    {"id": "D04", "name": "Upload date and uploader",
     "definition": "The time and user of the earliest version of the file, so a file overwritten later still counts "
                   "from its first upload, by its first uploader.",
     "unit": "UTC time; user name", "source": "Commons API: prop=imageinfo", "limits": ["L09"]},
    {"id": "D05", "name": "Uploaders (contributors)",
     "definition": "Accounts that first uploaded at least one file now in the project.",
     "unit": "accounts", "source": "D04", "excludes": "Files whose uploader is hidden or unknown.",
     "limits": ["L09"]},
    {"id": "D06", "name": "Kind of media",
     "definition": "Image if the file extension is a common image type (IMAGE_EXTS); otherwise Commons' own media "
                   "type: audio, video, document or other.",
     "unit": "files", "source": "File name; Commons API: prop=imageinfo&iiprop=mediatype", "limits": ["L14"]},
    {"id": "D07", "name": "Quality, featured and valued images, featured media",
     "definition": "Files in the Commons-wide categories the assessment templates maintain (ASSESS_CATS). The "
                   "project's own hand-kept subcategories are not used.",
     "unit": "files", "source": "Commons API: list=categorymembers", "limits": ["L12"]},
    {"id": "D08", "name": "Picture / Media of the Day",
     "definition": "On Commons: the file is shown by the day's template (Template:Potd/YYYY-MM-DD, Template:Motd/...). "
                   "On other wikis: a page using the file has a name recognised as a picture- or media-of-the-day page "
                   "(POTD_PAGE, MOTD_PAGE), excluding candidate and proposal pages (NOT_MAIN_PAGE).",
     "unit": "files; appearances", "source": "Commons API: prop=templates; on-wiki usage (D09)",
     "limits": ["L11"]},
    {"id": "D09", "name": "On-wiki usage",
     "definition": "Pages on Wikimedia wikis other than Commons that show a project file, as recorded by those wikis. "
                   "A 'use' is one file on one page. 'Content pages' are main-namespace pages (articles, Wikidata items).",
     "unit": "uses; pages; wikis; files", "source": "Commons API: prop=globalusage", "limits": ["L08"]},
    {"id": "D10", "name": "Usage added / removed",
     "definition": "Uses present in one usage check and not the previous one (added), or the other way round "
                   "(removed), for files in the project at both checks.",
     "unit": "uses", "source": "Successive usage checks", "limits": ["L08"]},
    {"id": "D11", "name": "Who made a change",
     "definition": "The edit whose diff adds or removes the category or file name, found among the edits made between "
                   "the two checks to the page itself, then to pages it transcludes, then to its Wikidata item, then "
                   "the same a week further back; or the page's deletion or move log. When no edit names the file, no "
                   "one is credited.",
     "unit": "user; edit", "source": "MediaWiki API of each wiki: prop=revisions, action=compare, list=logevents",
     "limits": ["L17"]},
    {"id": "D12", "name": "File requests (shown as 'views')",
     "definition": "How often the file itself or a thumbnail of it was requested from Wikimedia's servers, from any "
                   "page or app, per Wikimedia's media request statistics, from January 2015.",
     "unit": "requests", "source": "Wikimedia Analytics API: mediarequests/per-file, agent all-agents",
     "limits": ["L04", "L06", "L07"]},
    {"id": "D13", "name": "Requests not flagged as automated (shown as 'by people')",
     "definition": "The part of D12 that Wikimedia classifies as agent type 'user'.",
     "unit": "requests", "source": "Wikimedia Analytics API: mediarequests/per-file, agent user",
     "limits": ["L05", "L06", "L07"]},
    {"id": "D14", "name": "Camera device",
     "definition": "Make and model from the file's own metadata (EXIF), normalised to a brand (BRANDS) and a kind: "
                   "phone, camera, drone, action camera, scanner or other.",
     "unit": "files", "source": "Commons API: prop=imageinfo&iiprop=metadata", "limits": ["L10"]},
    {"id": "D15", "name": "Geocoded files",
     "definition": "Files whose Commons page has primary coordinates on Earth ({{Location}}). 'GPS only in metadata' "
                   "are files with GPS in EXIF but no coordinates on the page.",
     "unit": "files", "source": "Commons API: prop=coordinates", "limits": ["L10"]},
    {"id": "D16", "name": "Take-to-upload gap",
     "definition": "Upload date (UTC) minus the date the camera recorded, in days. A gap of a day either way counts as "
                   "0. A photo 'taken' more than a day after its upload, or before 2005, has a wrong camera clock and "
                   "is counted apart.",
     "unit": "days", "source": "D04 and D14", "limits": ["L10"]},
    {"id": "D17", "name": "Collection size",
     "definition": "The byte size of the current version of each file.", "unit": "bytes",
     "source": "Commons API: prop=imageinfo&iiprop=size", "limits": ["L13"]},
    {"id": "D18", "name": "Medians and percentages",
     "definition": "Median: the middle value; for an even count, the mean of the two middle values. Percentages are "
                   "rounded to one decimal (more below 1%), so parts may not add up to exactly 100%.",
     "unit": "—", "source": "—", "limits": ["L16"]},
]

LIMITATIONS = [
    {"id": "L01", "text": "Membership is defined by categories: a project file categorised outside the tree is not "
                          "counted, and an unrelated file placed inside it is."},
    {"id": "L02", "text": "A quick check infers the members of unchanged categories from Commons' counters and cannot see "
                          "file renames; the weekly full crawl re-reads everything."},
    {"id": "L03", "text": "A crawl takes minutes; a change made while it runs may or may not be included."},
    {"id": "L04", "text": "A file request is a load of the file or a thumbnail, including on pages where no one looks "
                          "at the image. A request is not a reader."},
    {"id": "L05", "text": "Requests 'not flagged as automated' can still include automated traffic Wikimedia did not "
                          "detect."},
    {"id": "L06", "text": "File-request history is collected gradually; until every file is counted, totals are partial "
                          "and say so."},
    {"id": "L07", "text": "File-request statistics are kept per file name; requests made under a file's earlier names "
                          "may be missing."},
    {"id": "L08", "text": "Wikis update their usage records in the background, so a use can appear or disappear a check "
                          "late. Usage on Commons itself is not counted."},
    {"id": "L09", "text": "The uploader is the account that first uploaded the file, which may differ from its author or "
                          "photographer."},
    {"id": "L10", "text": "Camera make, model, date and GPS come from the file's own metadata, which can be missing, "
                          "edited or wrong."},
    {"id": "L11", "text": "Main-page appearances on other wikis are recognised from page names in many languages; a wiki "
                          "with an unusual name for that page may be missed."},
    {"id": "L12", "text": "Assessment dates are the time the file entered the assessment category, which may differ "
                          "from the promotion date."},
    {"id": "L13", "text": "File size is the current version's size, not the original upload's."},
    {"id": "L14", "text": "Files of an unusual type are classified by Commons' media type, which is looked up once."},
    {"id": "L15", "text": "The catch-all category rule (more than 25% of files) only affects per-uploader category lists, "
                          "not totals."},
    {"id": "L16", "text": "The counts are complete enumerations, not samples, so there is no sampling error; the "
                          "uncertainty comes from the limitations above."},
    {"id": "L17", "text": "Who made a usage change is found by searching diffs for the file name; changes that came "
                          "through a template removed from the page, a date-driven page or a gallery fix may not be "
                          "found, and are then shown as not found rather than guessed."},
]


# ------------------------------------------------------------------------------------------------- fingerprint

def _rules():
    """Everything that defines the method, in a stable, comparable form."""
    out = {}
    for name, value in sorted(globals().items()):
        if not name.isupper() or name in ("METHOD_VERSION", "METHOD_DATE", "VERSIONS_FILE"):
            continue
        if isinstance(value, re.Pattern):
            value = {"pattern": value.pattern, "flags": value.flags}
        elif isinstance(value, timedelta):
            value = value.total_seconds()
        elif isinstance(value, (set, frozenset)):
            value = sorted(value)
        out[name] = value
    return out


def fingerprint():
    return hashlib.sha256(json.dumps(_rules(), sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


VERSIONS_FILE = Path(__file__).resolve().parent / "methodology_versions.json"


def recorded():
    try:
        return json.loads(VERSIONS_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def check():
    """(ok, message): the rules match the fingerprint recorded for METHOD_VERSION."""
    rec = recorded().get(METHOD_VERSION)
    if rec is None:
        return False, f"Method version {METHOD_VERSION} has no recorded fingerprint. Run: python methodology.py --record"
    if rec["fingerprint"] != fingerprint():
        return False, (f"The rules changed but METHOD_VERSION is still {METHOD_VERSION}. Bump METHOD_VERSION, add a "
                       "CHANGELOG.md entry, then run: python methodology.py --record")
    return True, f"Method {METHOD_VERSION} ({METHOD_DATE}) matches its recorded fingerprint."


def main():
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if "--record" in sys.argv:
        rec = recorded()
        if METHOD_VERSION in rec and rec[METHOD_VERSION]["fingerprint"] != fingerprint():
            raise SystemExit(f"{METHOD_VERSION} is already recorded with different rules; bump METHOD_VERSION first.")
        rec[METHOD_VERSION] = {"date": METHOD_DATE, "fingerprint": fingerprint()}
        VERSIONS_FILE.write_text(json.dumps(rec, indent=1) + "\n", encoding="utf-8")
        print(f"Recorded {METHOD_VERSION}: {fingerprint()}")
        return
    if "--check" in sys.argv:
        ok, msg = check()
        print(msg)
        raise SystemExit(0 if ok else 1)
    print(f"Method {METHOD_VERSION} ({METHOD_DATE}), fingerprint {fingerprint()[:16]}\n")
    for d in DEFINITIONS:
        print(f"{d['id']} {d['name']}: {d['definition']}")
    print()
    for item in LIMITATIONS:
        print(f"{item['id']} {item['text']}")


if __name__ == "__main__":
    main()
