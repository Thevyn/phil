#!/bin/bash
# Shadow test helper for the Claude routine (see mention/shadow.py). No-login sources only, nothing traded.
#   bash mention/shadow.sh research "QUERY"   # last30days brief of the past 7 days (stdout)
#   bash mention/shadow.sh commit             # commit and push mention/journal/shadow.jsonl
set -uo pipefail
cd "$(dirname "$0")/.."
SHA=c567827c3d25865fafa6b1ad33a173346da8cdcb  # pinned: third-party code, review before bumping
DIR=/tmp/last30days-$SHA
case "${1:-}" in
research)
  if [ ! -d "$DIR" ]; then
    git init -q "$DIR" && git -C "$DIR" fetch -q --depth 1 https://github.com/mvanhorn/last30days-skill "$SHA" \
      && git -C "$DIR" checkout -q FETCH_HEAD || { rm -rf "$DIR"; exit 1; }
  fi
  PY=$(for p in python3.14 python3.13 python3.12 python3; do
    command -v $p >/dev/null && $p -c 'import sys; sys.exit(sys.version_info < (3, 12))' && { echo $p; break; }; done)
  [ -n "$PY" ] || { echo "need python >= 3.12" >&2; exit 1; }
  if ! command -v yt-dlp >/dev/null; then  # YouTube search and transcripts, keyless
    [ -x /tmp/last30days-venv/bin/yt-dlp ] || { "$PY" -m venv /tmp/last30days-venv \
      && /tmp/last30days-venv/bin/pip install -q yt-dlp==2026.10.21; } >&2 || echo "yt-dlp unavailable, no YouTube" >&2
    PATH="/tmp/last30days-venv/bin:$PATH"
  fi
  # Empty env: no API keys or cookies exist, so every source that needs no login runs (Reddit, HN, Polymarket,
  # YouTube, GitHub, StockTwits for tickers, keyless web) plus opt-in DripStack newsletters.
  env -i HOME=/tmp/last30days-home PATH="$PATH" LAST30DAYS_MEMORY_DIR=/tmp/last30days-out INCLUDE_SOURCES=dripstack \
    "$PY" -I "$DIR/skills/last30days/scripts/last30days.py" --research-only --search reddit,hackernews,polymarket,youtube,dripstack,grounding \
    --web-backend keyless --no-browser-cookies --days 7 --emit compact --save-dir "" "$2" 2>/tmp/last30days-err.txt
  grep -E "Research complete|Sources:" /tmp/last30days-err.txt >&2
  ;;
commit)
  git add mention/journal/shadow.jsonl
  git diff --cached --quiet && { echo "no shadow changes"; exit 0; }
  git commit --quiet -m "mention: shadow estimates $(date -u +%Y-%m-%dT%H:%MZ)"
  git pull --rebase --quiet origin main && git push --quiet origin main
  ;;
*) echo "usage: $0 research QUERY | commit" >&2; exit 2 ;;
esac
