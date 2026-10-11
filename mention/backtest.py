"""Backtest the base-rate model on closed mention markets.

    python3 -m mention.backtest collect   # network: events, transcripts, prices -> mention/.cache/backtest.jsonl
    python3 -m mention.backtest report    # offline analysis of the collected rows

Each row is scored at a pre-event snapshot (start - 1h, or midnight ET of the
event day when the rules give no time), using only transcripts dated before
the event day.
"""
import datetime as dt
import json
import random
import sys

from . import events, model, rules, sources
from .corpus import Corpus

ROWS = sources.CACHE / "backtest.jsonl"


def snapshot_time(event):
    start = event["start"]
    return start if (start.hour, start.minute) == (0, 0) else start - dt.timedelta(hours=1)


def collect():
    parsed = [p for p in map(events.parse, events.fetch_events(closed=True)) if p]
    parsed.sort(key=lambda p: p["start"])
    since = parsed[0]["start"].date().isoformat()
    corpus = Corpus(since)
    done = set()
    if ROWS.exists():
        done = {json.loads(line)["event_id"] for line in ROWS.open()}
    with ROWS.open("a") as out:
        for i, ev in enumerate(parsed):
            if ev["id"] in done:
                continue
            try:
                comps = corpus.comparables(ev)
                actual = corpus.actual(ev)
            except RuntimeError as e:
                print(f"skip {ev['title']}: {e}", file=sys.stderr)
                continue
            snap = snapshot_time(ev)
            for m in ev["markets"]:
                if m["outcome"] is None or not m["yes_token"]:
                    continue
                price = events.pre_event_price(m, snap)
                counts = [rules.count(m["terms"], t) for t in comps]
                out.write(json.dumps({
                    "event_id": ev["id"], "title": ev["title"], "kind": ev["kind"], "start": ev["start"].isoformat(),
                    "market_id": m["id"], "question": m["question"], "threshold": m["threshold"],
                    "outcome": m["outcome"], "market_p": price, "fee_rate": m["fee_rate"],
                    "counts": counts, "model_p": model.p_hit(counts, m["threshold"]) if counts else None,
                    "actual_count": rules.count(m["terms"], actual) if actual else None}) + "\n")
            out.flush()
            print(f"[{i + 1}/{len(parsed)}] {ev['start'].date()} {ev['kind']:12} comps={len(comps):2} "
                  f"actual={'y' if actual else 'n'} {ev['title'][:70]}", flush=True)


# ------------------------------------------------------------------ report

KS = [i / 20 for i in range(21)]
WARMUP_EVENTS = 20


def brier(rows, key):
    return sum((r[key] - r["outcome"]) ** 2 for r in rows) / len(rows)


def by_event(rows):
    groups = {}
    for r in rows:
        groups.setdefault(r["event_id"], []).append(r)
    return list(groups.values())


def event_ci(rows, fn, n=2000):
    """95% CI of fn's per-row values averaged per event, resampling whole events."""
    vals = [sum(fn(r) for r in g) / len(g) for g in by_event(rows)]
    random.seed(0)
    means = sorted(sum(random.choices(vals, k=len(vals))) / len(vals) for _ in range(n))
    return means[int(0.025 * n)], means[int(0.975 * n)]


def pooled_ci(bets, n=2000):
    """95% CI of pooled ROI (total PnL / bets), resampling whole events."""
    groups = {}
    for r, x in bets:
        groups.setdefault(r["event_id"], []).append(x)
    g = list(groups.values())
    random.seed(0)
    rois = sorted(sum(map(sum, s)) / sum(map(len, s)) for s in (random.choices(g, k=len(g)) for _ in range(n)))
    return rois[int(0.025 * n)], rois[int(0.975 * n)]


def walk_forward(rows):
    """Blend weight k refit before each event on all earlier events only."""
    events_ = sorted(by_event(rows), key=lambda g: g[0]["start"])
    out, seen = [], []
    for i, g in enumerate(events_):
        if i >= WARMUP_EVENTS:
            k = min(KS, key=lambda k: sum((model.shrink(r["market_p"], r["model_p"], k) - r["outcome"]) ** 2
                                          for r in seen))
            out += [{**r, "k": k, "blend_p": model.shrink(r["market_p"], r["model_p"], k)} for r in g]
        seen += g
    return out


def trade(r, p, half_spread, maker):
    """PnL per $1 staked on the side p favours, if edge beats costs; None if no trade."""
    m = r["market_p"]
    for side_p, price, won in ((p, m, r["outcome"]), (1 - p, 1 - m, 1 - r["outcome"])):
        fill = price if maker else price + half_spread
        if not 0.02 <= fill <= 0.95:
            continue
        fee = 0.0 if maker else model.taker_fee(fill, r["fee_rate"])
        if side_p - fill - fee > 0.02:
            return (won - fill - fee) / (fill + fee)
    return None


def report():
    rows = [json.loads(line) for line in ROWS.open()]
    rows = [r for r in rows if r["market_p"] is not None and r["model_p"] is not None and len(r["counts"]) >= 3]
    checked = [r for r in rows if r["actual_count"] is not None]
    agree = sum((r["actual_count"] >= r["threshold"]) == bool(r["outcome"]) for r in checked)
    print(f"Matcher check: own-transcript count agrees with resolution on {agree}/{len(checked)} markets "
          f"({agree / max(len(checked), 1):.1%})")
    for kind in ("trump-rally", "trump-other", "earnings", None):
        sub = [r for r in rows if kind in (None, r["kind"])]
        if len(by_event(sub)) <= WARMUP_EVENTS + 5:
            print(f"\n{kind}: {len(by_event(sub))} events, too few")
            continue
        wf = walk_forward(sub)
        lo, hi = event_ci(wf, lambda r: (r["blend_p"] - r["outcome"]) ** 2 - (r["market_p"] - r["outcome"]) ** 2)
        print(f"\n{kind or 'ALL'}: {len(by_event(wf))} events / {len(wf)} markets scored out-of-sample, "
              f"final k={wf[-1]['k']}")
        print(f"  Brier  market {brier(wf, 'market_p'):.4f}  model {brier(wf, 'model_p'):.4f}  "
              f"blend {brier(wf, 'blend_p'):.4f}   blend-market 95% CI [{lo:+.4f}, {hi:+.4f}]")
        # Live books (Oct 2026): median half-spread 1.5c on rallies, 2c on earnings, p75 3.5c.
        for label, hs, maker in (("taker 1c", 0.01, False), ("taker 2c", 0.02, False), ("taker 3.5c", 0.035, False),
                                 ("taker 5c", 0.05, False), ("maker*", 0.0, True)):
            bets = [(r, x) for r in wf if (x := trade(r, r["blend_p"], hs, maker)) is not None]
            if not bets:
                print(f"  {label:9} no trades")
                continue
            roi = sum(x for _, x in bets) / len(bets)
            lo, hi = pooled_ci(bets)
            print(f"  {label:10} {len(bets):4} bets on {len(by_event([r for r, _ in bets])):3} events  "
                  f"ROI {roi:+.1%}  95% CI [{lo:+.1%}, {hi:+.1%}]")
    wf = walk_forward(rows)
    print("\nALL, taker 2c, by slice (midnight = no start time in the rules, price may be stale):")
    slices = {"timed T-1h": lambda r: r["start"][11:16] != "00:00", "midnight": lambda r: r["start"][11:16] == "00:00"}
    slices |= {h: (lambda h: lambda r: r["start"][:4] + ("H1" if r["start"][5:7] <= "06" else "H2") == h)(h)
               for h in sorted({r["start"][:4] + ("H1" if r["start"][5:7] <= "06" else "H2") for r in wf})}
    for name, keep in slices.items():
        model_ = [x for r in wf if keep(r) and (x := trade(r, r["blend_p"], 0.02, False)) is not None]
        no = [x for r in wf if keep(r) and (x := trade(r, 0.0, 0.02, False)) is not None]
        print(f"  {name:11} model {len(model_):4} bets ROI {sum(model_) / max(len(model_), 1):+.1%}   "
              f"always-No {len(no):4} bets ROI {sum(no) / max(len(no), 1):+.1%}")
    print("\n* maker assumes every resting order at the snapshot price fills: an upper bound.")

    print("\nBaseline without the model: buy No on every market (taker, 1c half-spread + fee).")
    wf = walk_forward(rows)
    bets = [(r, x) for r in wf if (x := trade(r, 0.0, 0.01, False)) is not None]  # p=0: always the No side
    lo, hi = pooled_ci(bets)
    print(f"  ALL out-of-sample markets: {len(bets)} bets, ROI {sum(x for _, x in bets) / len(bets):+.1%}"
          f"  95% CI [{lo:+.1%}, {hi:+.1%}]")
    print("\nCalibration of the pre-event market price:")
    for lo_, hi_ in ((0, .1), (.1, .3), (.3, .5), (.5, .7), (.7, .9), (.9, 1.01)):
        s = [r for r in rows if lo_ <= r["market_p"] < hi_]
        if s:
            print(f"  price {lo_:.1f}-{min(hi_, 1):.1f}: n={len(s):5}  avg price {sum(r['market_p'] for r in s) / len(s):.3f}"
                  f"  resolved Yes {sum(r['outcome'] for r in s) / len(s):.3f}")


if __name__ == "__main__":
    {"collect": collect, "report": report}[sys.argv[1]]()
