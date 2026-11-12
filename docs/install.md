# Install or update Agent Reach

For the AI doing the work. This one guide covers a fresh install, an update, and an upgrade from 1.5 or earlier. Run every command yourself. Each `agent-reach` command (1.6 and later) prints one JSON object; read `ok`, `result` and `error.hint`.

This guide installs the fork [ChlupacTheBosmer/Agent-Reach](https://github.com/ChlupacTheBosmer/Agent-Reach) (English only, no Chinese platforms, read-only access to your saved posts and bookmarks). Every install command pins `<commit-or-tag>`: replace it with a commit hash or tag of this fork that you have reviewed, never a moving `latest` link or branch name.

> ⚠️ The PyPI package named `agent-reach` is NOT this project. Never install it from PyPI; only use the GitHub URL below.

## 0. Before you start

- You must be able to run terminal commands. If you can't, tell the user: "Please turn on command execution for me in my settings, then ask again."
- Is it already installed? Run `agent-reach version`:
  - it prints JSON (1.6 and later) → go to [Update](#update).
  - it prints a plain line like `Agent Reach v1.5.0` → go to [Upgrade from 1.5 or earlier](#upgrade-from-15-or-earlier).
  - `agent-reach` is not found → continue below.
- `uv` missing (`uv --version` fails)? Tell the user: "I'm installing uv, a small tool that installs command-line programs. It needs no admin password." Then run:
  - macOS / Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`
  - Windows: `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`
  - Still `uv: command not found`? Open a new shell and try again.
- Installing from a git URL needs `git` (`git --version`).

## 1. Install and set up

```bash
uv tool install git+https://github.com/ChlupacTheBosmer/Agent-Reach@<commit-or-tag>
agent-reach setup
```

`agent-reach` not found after install? Run `uv tool update-shell` and open a new shell.

`setup` turns on the default channels and installs the agent skill.

## 2. Report to the user

Show `result.installation_report.markdown` to the user as is. The program generates it; do not rewrite, reorder or add to it.

## Update

Reinstall the fork at the newer reviewed commit or tag, then line the channel tools and the skill up with it:

```bash
uv tool install --force git+https://github.com/ChlupacTheBosmer/Agent-Reach@<commit-or-tag>
agent-reach update --sync-only
```

It upgrades every added channel's tools and the skill. Tell the user each line of `result.user_actions`, and show `result.installation_report.markdown` as is when it is present. If it fails, `error.hint` holds the exact command to run.

## Upgrade from 1.5 or earlier

1.6 is installed with uv; 1.5 was installed with pipx or pip. Tell the user: "This is a big upgrade. Your keys and settings are kept; I'm moving Agent Reach to the new installer." Then:

1. Install uv if it is missing (see step 0).
2. Install this fork:
   ```bash
   uv tool install --force git+https://github.com/ChlupacTheBosmer/Agent-Reach@<commit-or-tag>
   ```
3. Open a new shell and check `agent-reach version` prints JSON. If it still prints the old line, run `uv tool update-shell`, open a new shell and check again.
4. Run `agent-reach setup`. It keeps `~/.agent-reach/config.yaml`, removes the old Agent Reach program (only Agent Reach itself, never the user's other tools) and refreshes the skill for every AI.
5. Show `result.installation_report.markdown` to the user as is. It explains what changed and which old channels they can add back.

## Later

- The user just says what they want, e.g. "add Reddit" → `agent-reach add <channel>`; "remove YouTube" → `agent-reach remove <channel>`.
- Keys are entered by the user, e.g. a Groq key for YouTube speech-to-text: `agent-reach configure groq-key`.
- Everything else (usage, login, browser, saved posts and bookmarks) is covered by the installed skill.

Problems and rolling back: [troubleshooting.md](troubleshooting.md).
