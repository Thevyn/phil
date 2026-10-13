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
