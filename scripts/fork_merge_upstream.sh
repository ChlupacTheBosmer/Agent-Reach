#!/usr/bin/env bash
# Merge an upstream release tag into the fork's `custom` branch, then lint and test. See FORK.md.
# Usage: scripts/fork_merge_upstream.sh v1.7.0
set -euo pipefail
tag="${1:?usage: $0 <upstream release tag, e.g. v1.7.0>}"
cd "$(git rev-parse --show-toplevel)"

[ -z "$(git status --porcelain)" ] || { echo "working tree not clean; commit or stash first"; exit 1; }
git remote get-url upstream >/dev/null 2>&1 || git remote add upstream https://github.com/Panniantong/Agent-Reach.git
git remote set-url --push upstream DISABLED
git config rerere.enabled true
git fetch upstream --tags --quiet
git rev-parse --verify --quiet "refs/tags/$tag" >/dev/null || { echo "no upstream tag $tag"; exit 1; }
git checkout --quiet custom

if ! git merge --no-ff --no-edit "$tag"; then
  echo
  echo "Conflicts to resolve (rules in FORK.md):"
  git diff --name-only --diff-filter=U
  echo "Then: git add -A && git commit, and rerun the tests."
  exit 1
fi

py=.venv/bin/python
[ -x "$py" ] || py=python3
"$py" -m ruff check . && "$py" -m pytest -q -p no:cacheprovider
echo "Merged $tag cleanly and tests pass. Review, then: git push origin custom"
