# mention: base-rate forecaster for Polymarket mention markets

A fork-only experiment next to Phil's agent loop, with no LLM calls.
It forecasts "Will X say Y (N+ times) during Z?" markets from how often the speaker used the term in comparable past transcripts, blends that with the market price, and paper-trades the result against the live order book.

## Scope

- Trump single events (rallies and other scheduled remarks), transcripts from Roll Call Factbase, Trump's own speaker segments only.
- Earnings calls, transcripts from The Motley Fool, everyone on the call counts (as the market rules say).
- Weekly or monthly windows, other speakers, and count-bracket markets are skipped for now.

Transcripts are cached in `mention/.cache/` and never committed.

## Pieces

- `rules.py` parses questions and counts mentions following Polymarket's Event Mentions Contract (plurals, possessives, compounds, number words, name components).
- `sources.py` fetches and caches transcripts, one request per second.
- `events.py` discovers and parses mention events and reads historical prices.
- `corpus.py` picks comparable past transcripts for an event.
- `model.py` turns per-transcript counts into a probability and blends it with the market.
- `backtest.py` scores the model on closed markets at a pre-event price snapshot, walk-forward.
- `paper.py` is the forward test, journal in `mention/journal/paper.jsonl`.
- `tick.sh` runs one forward-test tick and pushes the journal.
  It is for a runner outside GitHub Actions (a Claude routine), since GitHub skips many scheduled runs.
  The journal is union-merged (`.gitattributes`) and only the first row per market and type counts, so two runners cannot double-count.

## Honest-simulation rules

- Taker fills buy the best ask and pay Polymarket's mentions taker fee, `shares x 0.04 x p x (1 - p)`.
- Maker orders rest at or below fair value minus 3c, are cancelled at the scheduled start, and count as filled only by trades printing strictly below the limit before the start.
- Each market is forecast once, with the blend weight from `params.json`, which is set from the backtest and not tuned on forward data.
- Results are reported with confidence intervals that resample whole events, because words in the same speech win or lose together.

## Run

```bash
python3 -m mention.backtest collect
python3 -m mention.backtest report
python3 -m mention.paper run
python3 -m mention.paper report
```

## Decision rule (set 2026-10-13, before forward data)

Judge the forward test once 30 events have settled, with `python3 -m mention.paper report`.
- Continue if the blend's Brier beats the market's and taker ROI is positive.
- Stop if the blend's Brier is worse than the market's and taker ROI is negative.
- Mixed result: run 30 more events.
- Parameters stay frozen until then.

Plumbing fixes during the forward test (no parameter changes):
- 2026-11-08: trades from Data API v2 (v1 retired). 2026-11-15: Gamma events paged at 50 (limit 100 returns 406).
- 2026-11-17: Fool company slugs matched by ticker across renames ("walt-disney-dis" and "disney-dis"). Before this, renamed companies had fewer comparables and could be skipped; the backtest had the same gap.
- 2026-11-19: Roll Call retired the Factbase wp-json search and moved transcripts to client-side pages; index and segments now come from rollcall.com/api/factbase/transcripts (same documents and text). Ticks from 2026-11-18 00:44Z failed; one Trump event was missed, earnings kept running after the 2026-11-18 per-event isolation fix.

Autofix (from 2026-11-19): when a tick fails because a source changed (endpoint moved, format changed,
page size limit), the hourly routine fixes it and pushes on its own via `mention/autofix.sh`, which only
allows `mention/sources.py`, `mention/events.py` and this README, runs the demos, and caps it at 3 per
24h. If the next tick still fails, the routine reverts the fix. CI (`.github/scripts/boundary.sh`)
fails any `mention: autofix:` commit outside that allowlist. Each autofix adds a dated line to the list
above; fixes must reproduce what the source served before, never change what the model sees.

## Shadow test (started 2026-11-10, log only)

Does recent news improve on the blend?
After each tick the Claude routine runs `python3 -m mention.shadow pending`, researches each upcoming event with [last30days](https://github.com/mvanhorn/last30days-skill) (`mention/shadow.sh research`, pinned commit, every source that needs no login: Reddit, Hacker News, YouTube incl. transcripts, Polymarket, DripStack, StockTwits for tickers, keyless web; run in an empty env so no keys or cookies are used), and journals its own probability per market in `journal/shadow.jsonl` at least 10 minutes before the event starts.
Shadow estimates are never traded and do not change the forward test above.
`python3 -m mention.shadow report` compares the shadow Brier with the market and the blend on settled markets.
Judge at 30 settled events: worth a pre-registered variant only if shadow beats blend with the event CI below zero.
