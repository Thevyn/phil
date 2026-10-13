#!/bin/bash
# Local hourly tick (launchd, see mention/README.md): same steps as .github/workflows/mention-paper.yml.
# Run it from a dedicated clone, never a working checkout: it pulls, commits and pushes on its own.
set -uo pipefail
cd "$(dirname "$0")/.."
echo "== $(date -u +%Y-%m-%dT%H:%MZ)"
git pull --rebase --quiet origin main || exit 1
python3 -m mention.paper run
status=$?
git add mention/journal  # always, so a crash mid-run keeps the rows already appended
if ! git diff --cached --quiet; then
  git commit --quiet -m "mention: paper tick $(date -u +%Y-%m-%dT%H:%MZ) (local)"
  git pull --rebase --quiet origin main && git push --quiet origin main
fi
exit $status
