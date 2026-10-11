"""Transcript sources, cached on disk under mention/.cache (gitignored; transcripts
are third-party copyrighted text, so they are never committed).

- Trump: Roll Call Factbase (speaker-labeled segments).
- Earnings calls: The Motley Fool transcripts, discovered via its monthly sitemaps.
"""
import datetime as dt
import gzip
import html
import json
import pathlib
import re
import time
import urllib.parse
import urllib.request

CACHE = pathlib.Path(__file__).parent / ".cache"
UA = {"User-Agent": "Mozilla/5.0 (compatible; phil-mention-research/0.1; +https://github.com/Thevyn/phil)"}
FACTBASE_SEARCH = "https://rollcall.com/wp-json/factbase/v1/search"
TRUMP = "Donald Trump"
_last_fetch = [0.0]


def fetch(url, params=None, retries=3):
    """Polite GET: at most one request per second, retries with backoff."""
    if params:
        url += "?" + urllib.parse.urlencode(params)
    for attempt in range(retries):
        wait = 1.0 - (time.time() - _last_fetch[0])
        if wait > 0:
            time.sleep(wait)
        _last_fetch[0] = time.time()
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001 - retry then surface
            err = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"GET {url} failed: {err}")


def _cached(path, build):
    path = CACHE / path
    if path.exists():
        with gzip.open(path, "rt") as f:
            return json.load(f)
    value = build()
    if not value:  # empty usually means a fetch or parse problem; retry next time
        return value
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with gzip.open(tmp, "wt") as f:
        json.dump(value, f)
    tmp.replace(path)
    return value


def _text(fragment):
    return html.unescape(re.sub(r"<[^>]+>", " ", fragment)).strip()


# ---------------------------------------------------------------- Factbase


def factbase_index(since):
    """Trump transcripts dated >= `since` (YYYY-MM-DD), newest first.

    The cached index records how far back it is complete. If that covers `since`,
    only new pages are fetched (until a known document appears); otherwise the
    index is rebuilt from page 1 back to `since`."""
    path = CACHE / "factbase-index.json"
    known = json.loads(path.read_text()) if path.exists() else {"docs": [], "complete_since": "9999"}
    topup = known["complete_since"] <= since
    ids = {d["id"] for d in known["docs"]} if topup else set()
    params = {"media": "", "type": "", "sort": "date", "location": "all", "place": "all", "q": ""}
    docs, page = [], 1
    while True:
        data = json.loads(fetch(FACTBASE_SEARCH, {**params, "page": page}))["data"]
        docs += [{"id": d["document_id"], "date": d["date"], "type": d["record_type"],
                  "title": d["record_title"], "url": d["factbase_url"]} for d in data]
        if not data or any(d["document_id"] in ids for d in data) or min(d["date"] for d in data) < since:
            break
        page += 1
    if topup:
        docs += known["docs"]
    docs = sorted({d["id"]: d for d in docs}.values(), key=lambda d: d["date"], reverse=True)
    CACHE.mkdir(parents=True, exist_ok=True)
    complete_since = min(since, known["complete_since"]) if topup else since
    path.write_text(json.dumps({"docs": docs, "complete_since": complete_since}))
    return [d for d in docs if d["date"] >= since and TRUMP in d["title"]]


def factbase_segments(doc):
    """[(speaker, text), ...] for one Factbase document."""
    def build():
        page = fetch(doc["url"])
        pairs = re.findall(r'<h2 class="text-md inline">(.*?)</h2>.*?'
                           r'<div class=" flex-auto text-md text-gray-600 leading-loose">(.*?)</div>', page, re.S)
        return [[_text(s), _text(t)] for s, t in pairs]
    return _cached(f"factbase/{doc['id']}.json.gz", build)


def trump_text(doc):
    return "\n".join(t for s, t in factbase_segments(doc) if s == TRUMP)


# ---------------------------------------------------------------- Motley Fool

FOOL_RE = re.compile(r"https://www\.fool\.com/earnings/call-transcripts/(\d{4})/(\d{2})/(\d{2})/"
                     r"([a-z0-9-]+?)-q(\d)-(\d{4})-earnings-call-transcript/")


def fool_index(since_month):
    """All transcript URLs from monthly sitemaps since `since_month` (YYYY-MM).
    Past months are cached forever; the current and previous month are refetched."""
    today = dt.date.today()
    recent = {today.strftime("%Y-%m"), (today.replace(day=1) - dt.timedelta(days=1)).strftime("%Y-%m")}
    y, m = map(int, since_month.split("-"))
    out = []
    while (y, m) <= (today.year, today.month):
        month = f"{y}-{m:02d}"
        path = CACHE / f"fool-sitemap/{month}.json.gz"
        if month in recent and path.exists():
            path.unlink()
        rows = _cached(f"fool-sitemap/{month}.json.gz", lambda: [
            {"url": u.group(0), "date": f"{u.group(1)}-{u.group(2)}-{u.group(3)}", "company": u.group(4)}
            for u in FOOL_RE.finditer(fetch(f"https://www.fool.com/sitemap/{y}/{m:02d}"))])
        out += rows
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return sorted({r["url"]: r for r in out}.values(), key=lambda r: r["date"])


def fool_text(row):
    """Whole-call text (everyone counts for earnings markets), speaker labels removed.

    Two page layouts: newer pages have a "Full Conference Call Transcript" section with
    inline "<strong>Name:</strong>" labels; older ones run "Prepared Remarks:" through
    "Questions & Answers:" with label paragraphs "<p><strong>Name</strong> -- <em>Title</em></p>"."""
    def build():
        page = fetch(row["url"])
        starts = [i for i in (page.find('id="full-conference-call-transcript"'), page.find(">Prepared Remarks:<")) if i >= 0]
        if not starts:
            return ""
        body = page[min(starts):]
        ends = [i for i in (body.find(">Call participants:<"), body.find(">Read Next<"), body.find("</article")) if i > 0]
        body = body[:min(ends, default=len(body))]
        body = re.sub(r"<p>\s*<strong>[^<]{0,80}</strong>\s*(?:--\s*<em>.*?</em>)?\s*</p>", " ", body, flags=re.S)
        body = re.sub(r"<strong>[^<]{0,80}:\s*</strong>", " ", body)
        return "\n".join(_text(p) for p in re.findall(r"<p>(.*?)</p>", body, re.S))
    return _cached(f"fool/{row['company']}-{row['date']}.json.gz", build)


ALIASES = {"google": "alphabet"}  # Polymarket brand name -> Fool company slug


def match_company(name, index):
    """Fool company slugs for a Polymarket company name. Fool spells one company several
    ways ("jpmorgan-jpm", "jpmorgan-chase-jpm", "apple"), so take the most common match,
    then every matching slug with its ticker suffix or that is a prefix of it."""
    name = re.sub(r"\b(?:co|inc|corp|corporation|company|holdings)\b\.?", "", name.lower())
    key = re.sub(r"[^a-z0-9]", "", name)
    key = ALIASES.get(key, key)
    counts = {}
    for r in index:
        if r["company"].replace("-", "").startswith(key):
            counts[r["company"]] = counts.get(r["company"], 0) + 1
    if not counts:
        return set()
    best = max(counts, key=counts.get)
    ticker = "-" + best.rsplit("-", 1)[-1]
    return {c for c in counts if c.endswith(ticker) or best.startswith(c)}
