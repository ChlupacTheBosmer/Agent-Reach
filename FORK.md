# About this fork

This is [ChlupacTheBosmer/Agent-Reach](https://github.com/ChlupacTheBosmer/Agent-Reach), a fork of
[Panniantong/Agent-Reach](https://github.com/Panniantong/Agent-Reach) based on upstream release v1.6.1.

## What differs from upstream

| Change | Where |
|---|---|
| English only: no Chinese text in code, messages, docs or the skill | everywhere; `SKILL.md` is the only skill file |
| Chinese platforms removed: Bilibili, Xiaohongshu, BOSS Zhipin, Xueqiu, Xiaoyuzhou, V2EX (and their tools bili-cli, boss-agent-cli and the Xiaoyuzhou transcription script) | `agent_reach/channels/`, `runtime/tools.py`, tests |
| New read-only actions for your own lists: `instagram saved [--limit] [--collection]`, `twitter bookmarks [--limit]`, `reddit saved [--limit]` | `channels/instagram.py`, `twitter.py`, `reddit.py` |
| `agent-reach update` never installs code by itself. It still reports upstream releases and refreshes helper tools to their tested versions | `runtime/manage.py` (`FORK_REPO`, `FORK_UPDATE_HINT`) |
| Fork invariants as tests | `tests/test_fork_policy.py` |

Kept channels: github, youtube, web, twitter (X), reddit, facebook, instagram, linkedin.

## Branches and remotes

| Name | What it is |
|---|---|
| `custom` | **This fork's code** and the default branch. Install from a commit on it. |
| `main` | Untouched mirror of upstream `main`. GitHub's "Sync fork" button updates it. Never commit to it. |
| remote `origin` | this fork |
| remote `upstream` | Panniantong/Agent-Reach (fetch only; push is disabled with `git remote set-url --push upstream DISABLED`) |

## Taking an upstream update

Merge upstream **release tags**, not upstream `main` (unreleased code from many contributors).

```bash
scripts/fork_merge_upstream.sh v1.7.0     # fetch, merge into custom, run lint + tests
# resolve conflicts (rules below), then:
git add -A && git commit                  # finishes the merge
.venv/bin/python -m pytest -q             # must be green, including test_fork_policy.py
git push origin custom
```

Then install the new commit through your pinned-install guard (on Petr's machine:
`python3 ~/.agent-reach-guard/guard.py review <commit>`, read the report, then `guard.py apply`).

`git config rerere.enabled true` is set in the clone, so git remembers how a conflict was resolved and reapplies it
the next time the same conflict appears.

## Conflict rules

| Conflict | Resolution |
|---|---|
| Upstream changed a file this fork deleted (a Chinese channel, `SKILL_en.md`, `docs/README_en.md`, the QR image) | keep it deleted: `git rm <file>` |
| Upstream added a new Chinese platform | do not register it: delete the module, its tests and its tool entry |
| Upstream changed `SKILL.md` (Chinese) or `SKILL_en.md` | port the meaning of the English change into our `SKILL.md` |
| New Chinese text anywhere (CHANGELOG section, messages, comments, tests) | translate it; `test_fork_policy.py` lists every line |
| `runtime/onboarding.py` | keep ours (English); port any structural change |
| `runtime/manage.py` `update()` / `_source()` | keep ours: no self-install, `FORK_REPO` for archive URLs |
| `.github/release-acceptance.json` | take upstream's, then drop the removed channels (snippet below) |
| `README.md`, `docs/*.md` | keep ours, port relevant upstream content in English |

```bash
python3 - <<'EOF'
import json; p = ".github/release-acceptance.json"; d = json.load(open(p))
gone = {"bilibili", "xiaohongshu", "boss", "xueqiu", "xiaoyuzhou", "v2ex"}
for k in ("pending", "waived", "results"):
    d[k] = [e for e in d[k] if e.get("channel") not in gone]
open(p, "w").write(json.dumps(d, indent=1, ensure_ascii=False) + "\n")
EOF
```

Upstream's release workflow (`.github/workflows/release.yml`) is not used by this fork.
