"""Shadow test: a news-informed probability per market, logged but never traded.

The Claude routine researches each upcoming forecast event with last30days (keyless sources only),
writes its own probability per market, and `add` journals it before the event starts. `report`
compares its Brier with the market and our blend on settled markets. Nothing here places orders.

    python3 -m mention.shadow pending          # markets still open for a shadow estimate (JSON)
    python3 -m mention.shadow add FILE.json    # [{"market_id": ..., "p": 0.0-1.0, "note": ...}, ...]
    python3 -m mention.shadow report
"""
import datetime as dt
import json
import pathlib
import sys

from . import paper
from .backtest import by_event, event_ci

JOURNAL = pathlib.Path(__file__).parent / "journal" / "shadow.jsonl"
MARGIN = dt.timedelta(minutes=10)  # estimates must be journaled well before the event starts (no lookahead)


def load():
    rows = [json.loads(line) for line in JOURNAL.open()] if JOURNAL.exists() else []
    seen = set()  # union-merged journal: first estimate per market counts
    return [r for r in rows if r["market_id"] not in seen and not seen.add(r["market_id"])]


def pending(rows, done, now):
    return {r["market_id"]: r for r in rows if r["type"] == "forecast" and r["market_id"] not in done
            and dt.datetime.fromisoformat(r["start"]) > now + MARGIN}


def valid(estimates, open_markets):
    """Estimates for open markets with 0 < p < 1; anything else is dropped."""
    return [e for e in estimates if e.get("market_id") in open_markets
            and isinstance(e.get("p"), (int, float)) and 0 < e["p"] < 1]


def cmd_pending():
    open_ = pending(paper.load(), {r["market_id"] for r in load()}, paper.now())
    events = {}
    for f in open_.values():
        ev = events.setdefault(f["event_id"], {"event_id": f["event_id"], "title": f["title"], "kind": f["kind"],
                                               "start": f["start"], "markets": []})
        hits = sum(c >= f["threshold"] for c in f["counts"])
        ev["markets"].append({"market_id": f["market_id"], "question": f["question"],
                              "past_hit_rate": f"{hits}/{len(f['counts'])}",
                              "market_p": f["market_p"], "blend_p": f["blend_p"]})
    print(json.dumps(list(events.values()), indent=1))


def cmd_add(path):
    open_ = pending(paper.load(), {r["market_id"] for r in load()}, paper.now())
    estimates = json.loads(pathlib.Path(path).read_text())
    ok = valid(estimates, open_)
    ts = paper.now().isoformat()
    with JOURNAL.open("a") as f:
        f.writelines(json.dumps({"ts": ts, "event_id": open_[e["market_id"]]["event_id"], "market_id": e["market_id"],
                                 "p": round(float(e["p"]), 4), "note": str(e.get("note", ""))[:300]}) + "\n"
                     for e in ok)
    print(f"journaled {len(ok)} of {len(estimates)} estimates")


def cmd_report():
    rows = paper.load()
    fc = {r["market_id"]: r for r in rows if r["type"] == "forecast"}
    st = {r["market_id"]: r["outcome"] for r in rows if r["type"] == "settle"}
    sh = [{**fc[s["market_id"]], "shadow_p": s["p"], "outcome": st[s["market_id"]]}
          for s in load() if s["market_id"] in st and s["market_id"] in fc]
    print(f"{len(load())} shadow estimates, {len(sh)} settled on {len(by_event(sh)) if sh else 0} events")
    if not sh:
        return
    for key in ("market_p", "blend_p", "shadow_p"):
        print(f"  Brier {key:9} {sum((r[key] - r['outcome']) ** 2 for r in sh) / len(sh):.4f}")
    lo, hi = event_ci(sh, lambda r: (r["shadow_p"] - r["outcome"]) ** 2 - (r["blend_p"] - r["outcome"]) ** 2)
    print(f"  shadow - blend Brier, 95% CI by event [{lo:+.4f}, {hi:+.4f}] (negative = shadow better)")


def demo():
    now = dt.datetime(2026, 11, 10, 12, tzinfo=dt.timezone.utc)
    f = lambda mid, h: {"type": "forecast", "market_id": mid, "start": (now + dt.timedelta(hours=h)).isoformat()}
    open_ = pending([f("a", 2), f("b", 2), f("c", -1), f("d", 0.1)], {"b"}, now)
    assert set(open_) == {"a"}, open_  # b already shadowed, c started, d inside the margin
    assert valid([{"market_id": "a", "p": 0.3}, {"market_id": "a", "p": 1}, {"market_id": "c", "p": 0.5},
                  {"market_id": "a", "p": "0.3"}], open_) == [{"market_id": "a", "p": 0.3}]
    print("shadow demo ok")


if __name__ == "__main__":
    {"pending": cmd_pending, "add": lambda: cmd_add(sys.argv[2]), "report": cmd_report, "demo": demo}[sys.argv[1]]()
