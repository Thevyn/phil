#!/bin/bash
# Guard for the routine's automatic plumbing fixes (see "Autofix" in mention/README.md).
# Only data fetching and parsing may change; model, rules, params and the journal never do.
#   bash mention/autofix.sh commit "what broke and what changed"   # check, demo, commit, push
#   bash mention/autofix.sh revert                                 # undo the last autofix and push
set -uo pipefail
cd "$(dirname "$0")/.."
ALLOWED='^mention/(sources|events)\.py$|^mention/README\.md$'
MAX_PER_DAY=3

case "${1:-}" in
commit)
  [ -n "${2:-}" ] || { echo "need a message" >&2; exit 2; }
  changed=$(git status --porcelain | cut -c4-)
  [ -n "$changed" ] || { echo "nothing to commit" >&2; exit 1; }
  bad=$(echo "$changed" | grep -vE "$ALLOWED")
  [ -z "$bad" ] || { echo "REFUSED, outside the autofix allowlist:" >&2; echo "$bad" >&2; exit 1; }
  recent=$(git log --since=24.hours --oneline --grep '^mention: autofix:' | wc -l)
  [ "$recent" -lt $MAX_PER_DAY ] || { echo "REFUSED, $recent autofixes in 24h already" >&2; exit 1; }
  for m in paper events shadow; do python3 -m mention.$m demo >/dev/null || { echo "demo $m failed" >&2; exit 1; }; done
  git add -- $changed
  git commit --quiet -m "mention: autofix: $2"
  git pull --rebase --quiet origin main && git push --quiet origin main && git log --oneline -1
  ;;
revert)
  sha=$(git log -1 --format=%H --grep '^mention: autofix:')
  [ -n "$sha" ] || { echo "no autofix to revert" >&2; exit 1; }
  git revert --no-edit "$sha" && git pull --rebase --quiet origin main && git push --quiet origin main
  ;;
*) echo "usage: $0 commit MSG | revert" >&2; exit 2 ;;
esac
