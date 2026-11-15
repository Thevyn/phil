#!/bin/bash
# Local hourly tick (launchd, see mention/README.md): same steps as .github/workflows/mention-paper.yml.
# Run it from a dedicated clone, never a working checkout: it pulls, commits and pushes on its own.
set -uo pipefail
cd "$(dirname "$0")/.."
echo "== $(date -u +%Y-%m-%dT%H:%MZ)"
pull=0; git pull --rebase --quiet origin main || { pull=1; echo "pull failed"; }
python3 -m mention.paper run
status=$?
[ $pull -eq 0 ] || status=1
git add mention/journal  # always, so a crash mid-run keeps the rows already appended
if ! git diff --cached --quiet; then
  git commit --quiet -m "mention: paper tick $(date -u +%Y-%m-%dT%H:%MZ) (local)"
  git pull --rebase --quiet origin main && git push --quiet origin main
fi
# Alert like the Actions workflow: one open issue while ticks fail, closed on the next green tick.
if command -v gh >/dev/null; then
  open=$(gh issue list --state open --search "in:title Mention paper tick failing" --json number -q '.[].number' 2>/dev/null)
  if [ $status -ne 0 ]; then
    if [ -n "$open" ]; then gh issue comment "${open%%$'\n'*}" --body "Local tick failed again, exit $status" >/dev/null
    else gh issue create --title "Mention paper tick failing" --body "Local tick failed, exit $status. Log: ~/Library/Logs/phil-tick.log" >/dev/null; fi
  else
    for n in $open; do gh issue close "$n" --comment "Green again (local tick)" >/dev/null; done
  fi
fi
exit $status
