# Changelog

All notable changes to this project will be documented in this file.

Entries up to 1.6.1 are upstream history (Panniantong/Agent-Reach), translated to English. They still mention platforms this fork has removed.

---

## [1.6.1] - 2026-11-01

- Several 1.6.0 cases where web search, Reddit, Xiaohongshu or Bilibili failed although the content was reachable, or reported success with empty content, are fixed.
- When another program on your computer uses the browser port, Agent Reach can use a different one; you don't have to close that program.
- A login copy that fails now says so instead of reporting success.
- On Windows, Xiaohongshu links with special characters open correctly.

Technical details:
- Web search: every request identifies itself as `agent-reach/<version>`, so Exa's Cloudflare front no longer blocks it (#877).
- Reddit: when the site tool fails transiently (for example it returns HTML instead of data), search and read fall back to the public web (#880).
- Xiaohongshu: OpenCLI 2.0 results are mapped, so read no longer succeeds with empty fields; search and comments no longer return one empty row when there are no results (#861, #895).
- Bilibili: a QR login that saved no usable credential is no longer reported as success, and the hint now says `agent-reach login bilibili` (#866, #883).
- The dedicated browser's port is configurable with `agent-reach configure browser-port <port>`, for when another tool holds 9222 (#848).
- Login reuse: no "copied 0 cookies" record when the browser bridge is down; an explicit `login <channel> --import-existing` that copies nothing now fails clearly (#879).
- Windows: Xiaohongshu note links containing `&` can be read and their comments loaded (#860).



## [1.6.0] - 2026-10-23

- Upgrading from an older version: just send your AI the "update Agent Reach" sentence. Your keys and settings are kept, and the old program is removed for you.
- Adding a platform takes one sentence. Everything it needs is installed for you, with no admin password; you only log in or enter a key.
- Platforms that need a login, such as Xiaohongshu and X, now use Agent Reach's own browser in the background, never yours, and several AIs can search at once.
- RSS and Exa search are now part of "web". When a new version is out, your AI tells you in plain words what changed and updates only if you say yes.

1.6 is a rebuild. Agents call one command, `agent-reach`; everything Agent Reach installs or stores lives under `~/.agent-reach/`.

Technical details:
- 28 of 40 actions passed real acceptance before release; these shipped untested with maintainer approval: Reddit (test network blocked), Instagram (rate limited), Bilibili subtitles, LinkedIn, Xueqiu hot posts/stocks (no login), Xiaoyuzhou (no key).
- Fixed: a logged-out `gh` blocking public API calls, public quotes launching the browser, wrong authentication hints, and cleanup of unrecorded caches; temporary audio and subtitle files stay inside Reach. BOSS in-site search prefers the official OpenCLI, and failures of the upstream JD adapter are reported separately; PRs and releases both check real acceptance and pending status.
- For declared public search/read actions, the CLI first reuses web tools and does not prepare the browser when they succeed; results state their source and content scope, and login is suggested only when the in-site route explicitly requires authentication. A backend can be chosen explicitly to keep the existing in-site capabilities.
- Browser channels share one profile, close after ten idle minutes and reopen on demand.
- Adding a channel prepares its tools; removal clears only that site's login. Default platforms can be removed and updates won't add them back.
- Updating keeps tools that have a tested version at that version; other tools get the newest version inside their supported range.
- Public content remains public-first, with no forced login, and a compact table after install shows what can be fetched.
- Keep Xiaoyuzhou transcription; remove the extra model configuration and duplicate site extraction.
- The Bridge is managed together with the dedicated browser; idle time is measured from real page activity and logins are saved periodically. A failed cleanup keeps the channel; uninstall deletes only files in the ledger. A release requires real acceptance of the channels.
- Reuse existing local site sessions read-only; queries default to headless, with a visible window for human login that shares the same profile. Bilibili subtitles and the Xueqiu community sync their login automatically, and BOSS JD uses the upstream browser interface. Missing interfaces and rate limits are no longer misreported as needing a new login.
- The dedicated browser is identified by the id its extension saves inside its own profile, so the user's own Chrome is never mistaken for it; a query no longer closes an open login window.
- Browser channels run queries in parallel: up to 2 per platform and 4 in total, with the rest queued instead of failing BUSY at once; login windows, logout and browser setup wait for running queries.
- Report NEED_LOGIN instead of UPSTREAM_BROKEN when not logged in: Xueqiu community (error 400016), Bilibili subtitles (no Bilibili login), LinkedIn (no LinkedIn login in the dedicated browser).
- Smoother upgrade from 1.5: install and update share one guide, install.md, which checks the version first (update.md, where 1.5 sends users, points to it); `agent-reach update` moves a pip/pipx install to uv by itself; `setup` removes the old Agent Reach program (only Agent Reach itself, never other tools) and the install report explains what was kept, what changed and which old channels can be added back. Install URLs always point at the latest release.
- Upstream errors reach the agent unchanged (the original message and help); the agent tells the user which platform is temporarily unavailable, by platform name. Reach no longer replaces them with its own advice (NEED_LOGIN still gives Reach's dedicated-browser login steps).

### ✨ New

- **Unified entry**: `agent-reach <channel> <action>`, `agent-reach read <url>`, `agent-reach channels`. One JSON envelope per call, unified error codes with hints, per-call timeout, automatic fallback to the next backend.
- **Management**: `setup`, `add` / `remove <channel>`, `login` / `logout <channel>`, `doctor`, `check`, `skill`, `skill link <dir>`, `update`, `configure`, `uninstall`.
- **One-step setup**: `setup` adds every default channel through the same path as `add` (bili-cli goes into `~/.agent-reach/tools/`) and reports what works, which AIs can read the skill, optional channels and optional logins.
- **Skill placement**: one real copy at `~/.agents/skills/agent-reach/SKILL.md` (read directly by Codex, OpenCode, Cline, Warp, Zed); a link to it (a shortcut pointing at the original file) in the skill folder of Claude Code, OpenClaw, Hermes, Cursor, Gemini CLI, GitHub Copilot and Windsurf when installed (a copy where links are not allowed, e.g. Windows). `agent-reach skill link <dir>` covers any other AI.
- **Private upstream installs, tested versions**: tools go into `~/.agent-reach/tools/`; the user's own copies are untouched. Tools with a declared tested version install at that version, and `update` keeps them there; other tools install the newest version inside `floor` and `exclude`, which also define acceptable existing tools. `state.json` records the version actually installed and `doctor` shows it. Single-instance tools (OpenCLI) reuse the user's copy; if it is outside the supported versions, it is upgraded only with `--allow-upgrade`.
- **`add` gives a finished result**: missing prerequisites are installed without an admin password: uv (official script), ffmpeg (`imageio-ffmpeg` in a private venv), Node.js (official release under `~/.agent-reach/tools/node/` when the system has none), CloakBrowser (falling back to installed Chrome on download failure). git is no longer needed (Boss installs from a GitHub archive). The user only logs in or enters a key.
- **Login state under `~/.agent-reach/`**: CLI tool logins in `sessions/`, browser logins in a dedicated Agent Reach browser. The browser opens by itself when a channel needs it and closes after 10 minutes without use. The OpenCLI extension is loaded automatically on each CloakBrowser launch; Windows system-Chrome fallback may need one Web Store click. Only the requested site's cookies are copied read-only from an existing connected local browser; the source remains unchanged.
- **remove cleans up**: deletes the channel's private tools nobody else uses, its CLI session, its login keys and its site cookies in the Agent Reach browser. The shared browser profile is retained for explicit logout/reuse; unused private tools are pruned.
- **Removable default channels**: `state.json` records `removed_defaults`; `setup` and `update` never add them back; `add` brings one back.
- **doctor states**: not_added, needs_setup, needs_login, unverified, ready, broken; plus `regressed` per channel (passed in the previous check, fails in the latest).
- **update** runs `uv tool install --force` on the latest release, reinstalls every added channel's tools at their declared versions (tested version when set, otherwise the newest version inside `floor`/`exclude`; also when Agent Reach itself has no new release), prunes tools no added channel uses, drops channels no longer in the registry, and refreshes the skill. `result.user_actions` lists in plain sentences what the user must do afterwards. Rollback: `uv tool install --force <previous release zip>`, then `agent-reach update --sync-only`. uv is the only supported installer. 1.5 and earlier installs are migrated on first `setup` / `update`; `config.yaml` keys are kept.
- **Update notice**: explicit `update --check` refreshes the release cache. Ordinary queries never start a background version check or update dependencies. Cached newer releases appear in `meta.update`; updating requires the user's agreement.
- **Release notes**: every CHANGELOG version section starts with a plain summary for users in Chinese and English; it becomes the GitHub release body the AI shows users.
- Xueqiu quotes and stock search via Tencent public quotes (no login); Xueqiu community data uses a session synced from the dedicated browser with `agent-reach login xueqiu --sync`.
- YouTube `transcribe`; LinkedIn reuses OpenCLI search and job-detail in the shared dedicated browser.

### 🔄 Changed

- Default channels: web (fallback only), YouTube, GitHub, V2EX, Bilibili. Optional: Xiaohongshu, Reddit, LinkedIn, Boss Zhipin, Xiaoyuzhou, Xueqiu. Experimental: Twitter, Facebook, Instagram.
- The agent skill is generated from the channel list and replaced on every update.

### 🗑️ Removed

- RSS channel (agents read feeds themselves).
- xhs-cli, rdt-cli, twitter-cli backends: they read browser cookies by themselves.
- `configure --from-browser`, `twitter-cookies`, `xhs-cookies`, the old `install --channels`, the old `skill --install`, `format`, `check-update`, MCP server entry.
- `watch` (use `doctor`, which now reports `regressed`), `doctor --live` (use `check`), `browser start` (the browser opens by itself), pipx installs.

### 🐛 Bug Fixes

#### 🔐 Boss Zhipin: wrong login state (two credential stores)

- **Root cause:** Boss has two login stores that do not stand for each other: the local `~/.boss-agent/auth/session.enc`
  and the browser cookies inside the dedicated Chrome profile. `boss status` / `status --live` **only checked the first**
  (the legacy credential store from the Bridge/httpx era), while search in strict CDP `existing-browser` mode uses the browser cookies.
  With "old local credentials + browser not logged in", `boss status` reported `logged_in: true`, misleading the agent
  into skipping login and searching straight away, which ended in `AUTH_EXPIRED`; the old runbook also forbade treating `_security_check`
  as logged out, and the two rules together pushed the agent down the wrong "anti-bot slider" branch.
- **Fix:** `check()` gains a fourth read-only probe, `_cdp_zhipin_login_cookie()`, which uses a minimal WebSocket client written
  with the standard library only to ask the CDP browser itself (`Storage.getCookies`) whether it holds the zhipin
  `wt2` cookie. **The browser is the source of truth**; no new dependency, no browser launch, no search.
  No wt2 → it clearly reports "browser not logged in, search will report AUTH_EXPIRED" and points to "user logs in +
  `login --cdp`"; a failed probe → "login state unknown"; the security-check page hint also includes the browser cookie state.
- **Runbook fix:** removed the misleading rule "judge the login state only by `boss status`"; the doctor's
  browser cookie probe now decides. After launching the dedicated Chrome, **always pause so the user can confirm the login state by eye**;
  `AUTH_EXPIRED` is the ground truth (go straight to the login flow, never interpret it as a security check);
  `_security_check` is treated as a slider only when there is no `AUTH_EXPIRED`.

### ✨ Features

#### 🎯 Boss Zhipin channel

- New `boss` channel: searches jobs and fetches full JDs through boss-agent-cli + a real Chrome over CDP.
- `check()` runs four read-only probes (is boss-agent-cli installed → is port 9222 reachable → is there a zhipin tab
  → does the browser hold the `wt2` login cookie).
- Fetching uses the public API (`search_jobs` + `job_card_browser` + `browser_source="existing-browser"`).
- `agent-reach install --system --channels=boss` installs the backend pinned to a fixed commit of upstream boss-agent-cli
  (#403–#407 merged into master); it switches back to a version constraint once upstream publishes a release.
- The skill and install guide support "set up Boss Zhipin for me": the agent starts a dedicated Chrome listening only on the loopback address,
  the user only logs in by hand, and finally the agent verifies the login state and the CDP link.
- The pinned dependency moves from fork snapshot `8ff6bd3` to upstream commit `4c991b7` (#403–#407 merged into
  master): a logged-in CDP session can be reused directly, and search can use `--browser-source existing-browser`
  to forbid a headless downgrade.
- Code 37 is classified by its original message: an environment anomaly is `ENVIRONMENT_RISK` and stops at once; only an explicit
  token/stoken expiry allows one refresh. The dedicated Chrome profile should be reused long-term, at a lower request rate.

## [1.3.1] - 2026-03-27

### 🐛 Bug Fixes

#### 📈 Xueqiu: full repair

- **Fixed the root cause of the 400 error:** `_ensure_cookies()` only visited the home page, which yields only `acw_tc` (an anti-DDoS token); `xq_a_token` is generated dynamically by Xueqiu's front-end JS and cannot be obtained with plain HTTP requests. Added a three-level cookie loading strategy: ① read the config file (saved by `--from-browser`) → ② extract automatically from the local Chrome browser (needs browser-cookie3 installed) → ③ homepage fallback
- **Fixed User-Agent:** `"agent-reach/1.0"` was recognised and refused by Xueqiu's anti-bot system; changed to a real Chrome UA
- **Fixed missing `Referer` header:** all API requests now send `Referer: https://xueqiu.com/`
- **Fixed the `get_hot_posts()` endpoint:** the old endpoint `/statuses/hot/listV3.json` is deprecated (returns an empty body); changed to `/v4/statuses/public_timeline_by_category.json`, correctly parsing the `item.data` JSON string to get author/likes/text
- **Fixed `urllib.request.quote` → `urllib.parse.quote`:** the correct module is now used explicitly
- **Fixed `configure --from-browser` not extracting Xueqiu cookies:** Xueqiu added to `PLATFORM_SPECS`; saved only when `xq_a_token` is present
- **Corrected misleading docs:** "no configuration needed" / "public API, no login required" in README/SKILL.md → now accurately says a browser cookie is needed
- **Improved error messages:** when `check()` fails it suggests `configure --from-browser chrome` instead of "a proxy may be needed"

---

## [1.3.0] - 2026-03-12

### 🆕 New Channels

#### 💻 V2EX
- Hot topics, node topics, topic detail + replies, user profile via public JSON API
- Zero config — no auth, no proxy, no API key required
- `get_hot_topics(limit)`, `get_node_topics(node_name, limit)`, `get_topic(id)`, `get_user(username)`

### 📈 Improvements

- Channel count: 14 → 15

---

## [1.1.0] - 2025-02-25

### 🆕 New Channels

#### ~~📷 Instagram~~ (removed — upstream blocked)
- ~~Read public posts and profiles via [instaloader](https://github.com/instaloader/instaloader)~~
- **Removed:** Instagram's aggressive anti-scraping measures broke all available open-source tools (instaloader, etc.). See [instaloader#2585](https://github.com/instaloader/instaloader/issues/2585). Will re-add when upstream recovers.

#### 💼 LinkedIn
- Read person profiles, company pages, and job details via [linkedin-scraper-mcp](https://github.com/stickerdaniel/linkedin-mcp-server)
- Search people and jobs via MCP, with Exa fallback
- Fallback to Jina Reader when MCP is not configured

#### 🏢 Boss Zhipin
- QR code login via [mcp-bosszp](https://github.com/mucsbr/mcp-bosszp)
- Job search and recruiter greeting via MCP
- Fallback to Jina Reader for reading job pages

### 📈 Improvements

- Channel count: 9 → 12
- `agent-reach doctor` now detects all 12 channels
- CLI: added `search-linkedin`, `search-bosszhipin` subcommands
- Updated install guide with setup instructions for new channels

---

## [1.0.0] - 2025-02-24

### 🎉 Initial Release

- 9 channels: Web, Twitter/X, YouTube, Bilibili, GitHub, Reddit, XiaoHongShu, RSS, Exa Search
- CLI with `read`, `search`, `doctor`, `install` commands
- Unified channel interface — each platform is a single pluggable Python file
- Auto-detection of local vs server environments
- Built-in diagnostics via `agent-reach doctor`
- Skill registration for Claude Code / OpenClaw / Cursor
