"""Forward paper trading of mention markets against the live Polymarket book.

    python3 -m mention.paper run      # forecast new events, settle finished ones
    python3 -m mention.paper report   # forward-test scoreboard

Each market is forecast once, on the first run within LEAD of the scheduled
start. Two simulated orders per market:
- taker: buy at the best ask now if the edge beats the ask plus taker fee;
- maker: a resting limit at or below fair - MARGIN, cancelled at the start.
  It counts as filled only by trades printing strictly below the limit before
  the start (queue position unknown, so trades at the limit are ignored).
"""
import datetime as dt
import json
import math
import pathlib
import sys

from . import events, model, rules
from .corpus import Corpus
from .backtest import by_event, pooled_ci

JOURNAL = pathlib.Path(__file__).parent / "journal" / "paper.jsonl"
PARAMS = json.loads((pathlib.Path(__file__).parent / "params.json").read_text())
LEAD = dt.timedelta(hours=24)
STAKE = 10.0
MARGIN = 0.03
MIN_EDGE = 0.02
MAX_SPREAD = 0.10  # wider Yes book: the mid is not a market estimate, so no orders
TRADES = "https://data-api.polymarket.com/trades"


def now():
    return dt.datetime.now(dt.timezone.utc)


def load():
    return first_per_market([json.loads(line) for line in JOURNAL.open()] if JOURNAL.exists() else [])


def first_per_market(rows):
    """Local and GitHub runners may both journal a market (union-merged); the first row of each type counts."""
    seen = set()
    return [r for r in rows if (key := (r["type"], r["market_id"])) not in seen and not seen.add(key)]


def append(rows):
    JOURNAL.parent.mkdir(exist_ok=True)
    with JOURNAL.open("a") as f:
        f.writelines(json.dumps(r) + "\n" for r in rows)


def book(token):
    b = events.pmapi.clob_book(token)
    bids = sorted(((float(x["price"]), float(x["size"])) for x in b.get("bids", [])), reverse=True)
    asks = sorted((float(x["price"]), float(x["size"])) for x in b.get("asks", []))
    return (bids[0] if bids else (0.0, 0.0)), (asks[0] if asks else (1.0, 0.0))


def plan(p, side_books, fee_rate):
    """Taker and maker orders for fair probability p of Yes. side_books: {"Yes": (bid, ask), "No": ...}."""
    taker = maker = None
    for side, fair in (("Yes", p), ("No", 1 - p)):
        (bid, _), (ask, ask_size) = side_books[side]
        fee = model.taker_fee(ask, fee_rate)
        if taker is None and 0.02 <= ask <= 0.95 and fair - ask - fee > MIN_EDGE and ask_size > 0:
            shares = min(STAKE / (ask + fee), ask_size)
            taker = {"side": side, "price": ask, "fee": round(fee * shares, 4), "shares": round(shares, 4)}
        limit = min(math.floor(round((fair - MARGIN) * 100, 6)) / 100, round(bid + 0.01, 2))
        # Rest only on the side we think is cheap (fair above its mid); never both sides.
        if maker is None and fair > (bid + ask) / 2 and 0.02 <= limit <= 0.95 and limit < ask:
            maker = {"side": side, "limit": limit, "shares": round(STAKE / limit, 4)}
    return taker, maker


def forecast(corpus, ev):
    comps = corpus.comparables(ev)
    if len(comps) < 3:
        return []
    k = PARAMS["k"].get(ev["kind"], 0.0)
    rows = []
    for m in ev["markets"]:
        if not m["yes_token"] or not m["no_token"]:
            continue
        counts = [rules.count(m["terms"], t) for t in comps]
        model_p = model.p_hit(counts, m["threshold"])
        books = {"Yes": book(m["yes_token"]), "No": book(m["no_token"])}
        (ybid, _), (yask, _) = books["Yes"]
        mid = (ybid + yask) / 2
        blend = model.shrink(mid, model_p, k)
        taker, maker = plan(blend, books, m["fee_rate"]) if yask - ybid <= MAX_SPREAD else (None, None)
        rows.append({"type": "forecast", "ts": now().isoformat(), "event_id": ev["id"], "title": ev["title"],
                     "kind": ev["kind"], "start": ev["start"].isoformat(), "market_id": m["id"],
                     "condition_id": m["condition_id"], "tokens": {"Yes": m["yes_token"], "No": m["no_token"]},
                     "question": m["question"], "threshold": m["threshold"], "counts": counts,
                     "model_p": round(model_p, 4), "k": k, "market_p": round(mid, 4), "blend_p": round(blend, 4),
                     "books": {s: {"bid": b[0], "bid_size": b[1], "ask": a[0], "ask_size": a[1]}
                               for s, (b, a) in books.items()},
                     "fee_rate": m["fee_rate"], "taker": taker, "maker": maker})
    return rows


def maker_fill(f):
    """Shares of the maker order filled by trades strictly below the limit between forecast and start."""
    o = f["maker"]
    token = f["tokens"][o["side"]]
    t0 = dt.datetime.fromisoformat(f["ts"]).timestamp()
    t1 = dt.datetime.fromisoformat(f["start"]).timestamp()
    trades = events.pmapi.get_json(TRADES, {"market": f["condition_id"], "limit": 10000})
    traded = sum(float(t["size"]) for t in trades
                 if t["asset"] == token and t0 < t["timestamp"] < t1 and float(t["price"]) < o["limit"])
    return min(o["shares"], traded)


def settle(f, outcome):
    won = {"Yes": outcome, "No": 1 - outcome}
    row = {"type": "settle", "ts": now().isoformat(), "market_id": f["market_id"], "outcome": outcome}
    if f["taker"]:
        t = f["taker"]
        row["taker_pnl"] = round(t["shares"] * (won[t["side"]] - t["price"]) - t["fee"], 4)
    if f["maker"]:
        o = f["maker"]
        filled = maker_fill(f)
        row["maker_filled"] = round(filled, 4)
        row["maker_pnl"] = round(filled * (won[o["side"]] - o["limit"]), 4)
    return row


def run():
    rows = load()
    forecasted = {r["market_id"] for r in rows if r["type"] == "forecast"}
    settled = {r["market_id"] for r in rows if r["type"] == "settle"}

    # Settle finished markets.
    out = []
    for f in (r for r in rows if r["type"] == "forecast" and r["market_id"] not in settled):
        if dt.datetime.fromisoformat(f["start"]) > now():
            continue
        m = events.pmapi.gamma_market(f["market_id"])
        prices = json.loads(m.get("outcomePrices") or "[]")
        if m.get("closed") and prices in (["1", "0"], ["0", "1"]):
            out.append(settle(f, int(prices[0] == "1")))
    append(out)
    print(f"settled {len(out)}")

    # Forecast events starting within LEAD.
    due = [p for p in map(events.parse, events.fetch_events(closed=False))
           if p and now() < p["start"] <= now() + LEAD and any(m["id"] not in forecasted for m in p["markets"])]
    if not due:
        print("no events due")
        return
    today = now().date()
    corpus = Corpus(today.isoformat())
    for ev in due:
        new = [r for r in forecast(corpus, ev) if r["market_id"] not in forecasted]
        append(new)
        print(f"{ev['title']}: {len(new)} forecasts, {sum(1 for r in new if r['taker'])} taker, "
              f"{sum(1 for r in new if r['maker'])} maker")


def report():
    rows = load()
    fc = {r["market_id"]: r for r in rows if r["type"] == "forecast"}
    st = [{**fc[r["market_id"]], **r} for r in rows if r["type"] == "settle"]
    print(f"{len(fc)} forecasts, {len(st)} settled on {len(by_event(st)) if st else 0} events")
    if not st:
        return
    for key in ("market_p", "model_p", "blend_p"):
        print(f"  Brier {key:9} {sum((r[key] - r['outcome']) ** 2 for r in st) / len(st):.4f}")
    for kind in ("taker", "maker"):
        legs = [r for r in st if r.get(kind) and (kind == "taker" or r.get("maker_filled"))]
        if not legs:
            print(f"  {kind}: no fills")
            continue
        cost = {id(r): (r["taker"]["shares"] * r["taker"]["price"] + r["taker"]["fee"]) if kind == "taker"
                else r["maker_filled"] * r["maker"]["limit"] for r in legs}
        pnl = sum(r[f"{kind}_pnl"] for r in legs)
        lo, hi = pooled_ci([(r["event_id"], r[f"{kind}_pnl"], cost[id(r)]) for r in legs])
        print(f"  {kind}: {len(legs)} fills on {len(by_event(legs))} events, PnL ${pnl:+.2f} on "
              f"${sum(cost.values()):.2f} ({pnl / sum(cost.values()):+.1%}), 95% CI [{lo:+.1%}, {hi:+.1%}]")


def demo():
    books = {"Yes": ((0.40, 100), (0.42, 50)), "No": ((0.58, 100), (0.60, 100))}
    taker, maker = plan(0.50, books, 0.04)
    assert taker["side"] == "Yes" and taker["price"] == 0.42 and maker == {"side": "Yes", "limit": 0.41, "shares": 24.3902}
    taker, maker = plan(0.41, books, 0.04)  # inside the spread: no taker edge
    assert taker is None
    assert plan(0.952, {"Yes": ((0.96, 9), (0.98, 9)), "No": ((0.01, 9), (0.04, 9))}, 0.04)[1] is None  # 0.018 floors below the 0.02 min
    assert [r["n"] for r in first_per_market([{"type": "forecast", "market_id": "1", "n": 1},
                                              {"type": "forecast", "market_id": "1", "n": 2},
                                              {"type": "settle", "market_id": "1", "n": 3}])] == [1, 3]
    f = {"taker": {"side": "No", "price": 0.6, "shares": 10, "fee": 0.1}, "maker": None, "market_id": "1"}
    assert settle(f, 0)["taker_pnl"] == 3.9


if __name__ == "__main__":
    {"run": run, "report": report, "demo": lambda: (demo(), print("ok"))}[sys.argv[1]]()
