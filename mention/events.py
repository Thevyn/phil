"""Polymarket mention-market events: discovery, parsing, prices."""
import datetime as dt
import gzip
import json
import re
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "core"))
import pmapi  # noqa: E402

from . import rules, sources  # noqa: E402

ET = dt.timezone(dt.timedelta(hours=-4))  # ponytail: fixed EDT offset; EST months are off by 1h, fine for a T-1h snapshot


def fetch_events(closed):
    out, off = [], 0
    while True:
        page = pmapi.get_json(pmapi.GAMMA + "/events", {"tag_slug": "mention-markets", "closed": str(closed).lower(),
                                                        "limit": 100, "offset": off})
        out += page
        if len(page) < 100:
            return out
        off += 100


def kind(title):
    t = title.lower()
    if "earnings call" in t:
        return "earnings"
    if not t.startswith("what will trump say") or re.search(r"this week|in (january|february|march|april|may|june|july|"
                                                            r"august|september|october|november|december)\b", t):
        return None  # weekly/monthly windows and other speakers: not modelled yet
    return "trump-rally" if "rally" in t else "trump-other"


def start_time(event):
    """Scheduled start from the rules text ("on October 5, 2026 at 7:00 PM ET").
    Date without a time -> midnight ET that day, which is still before the event."""
    text = " ".join([event.get("description") or ""] + [m.get("description") or "" for m in event["markets"][:1]])
    m = re.search(r"on (?:\w+day, )?(\w+\.? \d{1,2}(?:st|nd|rd|th)?, \d{4})(?:,? (?:at|from) (\d{1,2}(?::\d{2})?)\s*([AP]M))?",
                  text)
    if not m:
        return None
    hm, ampm = (m.group(2), m.group(3)) if m.group(2) else ("12:00", "AM")
    hm = hm if ":" in hm else hm + ":00"
    day = re.sub(r"(\d)(st|nd|rd|th)", r"\1", m.group(1)).replace(".", "").replace("Sept ", "Sep ")
    stamp = f"{day} {hm} {ampm}"
    for fmt in ("%B %d, %Y %I:%M %p", "%b %d, %Y %I:%M %p"):
        try:
            return dt.datetime.strptime(stamp, fmt).replace(tzinfo=ET)
        except ValueError:
            pass
    return None


def company(title):
    """Company for whole-call markets; None for speaker-specific ones ("What will Elon Musk say during ...")."""
    m = (re.match(r"What will (.+?) say (?:during|on) (?:their|its|the) (?:next )?(?:\w+ )?earnings call", title)
         or re.match(r"What will be said during (.+?) Q\d \d{4} earnings call", title))
    return m.group(1) if m else None


def parse(event):
    """Event record with parsed markets, or None if out of scope."""
    k = kind(event["title"])
    start = start_time(event)
    if not k or not start or (k == "earnings" and not company(event["title"])):
        return None
    markets = []
    for m in event["markets"]:
        spec = rules.parse_question(m["question"])
        if not spec:
            continue
        tokens = json.loads(m.get("clobTokenIds") or "[]")
        prices = json.loads(m.get("outcomePrices") or "[]")
        outcome = None
        if m.get("closed") and prices in (["1", "0"], ["0", "1"]):
            outcome = int(prices[0] == "1")
        markets.append({**spec, "id": m["id"], "question": m["question"], "condition_id": m.get("conditionId"), "yes_token": tokens[0] if tokens else None,
                        "no_token": tokens[1] if len(tokens) > 1 else None, "outcome": outcome,
                        "fee_rate": (m.get("feeSchedule") or {}).get("rate", 0.0)})
    return {"id": event["id"], "title": event["title"], "kind": k, "start": start, "company": company(event["title"]),
            "markets": markets}


def price_before(token, when):
    """Last traded/mid price at or before `when` from CLOB hourly history (cached per token)."""
    path = sources.CACHE / f"prices/{token}.json.gz"
    if path.exists():
        with gzip.open(path, "rt") as f:
            hist = json.load(f)
    else:
        hist = pmapi.get_json(pmapi.CLOB + "/prices-history", {"market": token, "interval": "max", "fidelity": 60})
        hist = hist.get("history", [])
        if hist:  # an empty answer can be throttling; never cache it
            path.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(path, "wt") as f:
                json.dump(hist, f)
    ts = when.timestamp()
    before = [p for p in hist if p["t"] <= ts]
    if before and ts - before[-1]["t"] <= 6 * 3600:
        return before[-1]["p"]
    return None


def trades(condition_id):
    """All trades of a market as [timestamp, asset, price, size], newest first (cached once closed)."""
    path = sources.CACHE / f"trades/{condition_id}.json.gz"
    if path.exists():
        with gzip.open(path, "rt") as f:
            return json.load(f)
    out = []
    for offset in range(0, 10000, 1000):  # data-api caps offset; enough for pre-event trading
        page = pmapi.get_json("https://data-api.polymarket.com/trades",
                              {"market": condition_id, "limit": 1000, "offset": offset})
        out += [[t["timestamp"], t["asset"], float(t["price"]), float(t["size"])] for t in page]
        if len(page) < 1000:
            break
    if out:
        path.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(path, "wt") as f:
            json.dump(out, f)
    return out


def last_trade_before(market, when, max_age=24 * 3600):
    """Yes price of the last trade within `max_age` before `when` (No-token trades as 1 - price)."""
    ts = when.timestamp()
    for t, asset, price, _ in trades(market["condition_id"]):  # newest first
        if t <= ts:
            if ts - t > max_age:
                return None
            return price if asset == market["yes_token"] else 1 - price
    return None


def pre_event_price(market, when):
    p = price_before(market["yes_token"], when)
    return p if p is not None else last_trade_before(market, when)


def demo():
    e = {"title": "What will Trump say during his Midterm Rally in Ohio?",
         "description": "Donald Trump is scheduled to give remarks during his Midterm Rally in Youngstown, Ohio on "
                        "October 12, 2026 at 6:00 PM ET.", "id": "1",
         "markets": [{"id": "2", "question": 'Will Trump say "Border" 10+ times during his Midterm Rally in Ohio?',
                      "clobTokenIds": '["a", "b"]', "outcomePrices": '["1", "0"]', "closed": True,
                      "feeSchedule": {"rate": 0.04}},
                     {"id": "3", "question": "Will Trump's Midterm Rally in Ohio not air?"}]}
    p = parse(e)
    assert p["kind"] == "trump-rally" and p["start"].hour == 18 and len(p["markets"]) == 1
    assert p["markets"][0]["outcome"] == 1 and p["markets"][0]["threshold"] == 10
    assert company("What will JPMorgan say during their next earnings call?") == "JPMorgan"
    assert company("What will be said during Meta Q2 2025 earnings call?") == "Meta"
    assert company("What will Elon Musk say during Tesla Q1 2025 earnings call?") is None
    assert kind("What will Trump say this week? (October 5 - October 11)") is None


if __name__ == "__main__":
    demo()
    print("ok")
