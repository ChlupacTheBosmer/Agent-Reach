# Maintenance

Read when an update fails, the browser will not start, a channel reports an error, or the user wants to uninstall.

- Update: `agent-reach update` upgrades Agent Reach, the tools of added channels and the skill; `agent-reach update --check` only checks for a new version. Run it only after the user agrees.
- Status: `agent-reach doctor --text` reads local state without going online; `agent-reach check <channel> --text` runs one live test (`--search` tests only search/jobs). A successful install is not a passed live test.
- Browser: `agent-reach browser status` shows the state, `browser setup` prepares the engine and extension, `browser stop` closes it; after a failed CloakBrowser download, fix the network and run `browser setup --retry-cloak`.
- Tell the failing stage apart: dependency download, browser connection, login, page load and platform rate limits are separate; never call a network timeout an expired login.
- Upstream tool errors: note the error and version, and run `agent-reach update` once after the user agrees; never edit the upstream tool's installed files, never retry in a loop.
- `PLATFORM_BLOCKED`, `RATE_LIMITED`, `NEED_LOGIN` and `BUSY` stop this attempt; continue after the user has dealt with it, and do not keep opening windows.
- Keys and proxy: `agent-reach configure groq-key | openai-key | proxy | github-token`; the user provides the values themselves.
- Port conflict (`Port <n> belongs to an unmanaged browser`): another program holds the dedicated browser's port (9222 by default). Do not close the user's program; after the user agrees, run `agent-reach configure browser-port <free port>`.
- Uninstall: show the scope with `agent-reach uninstall --dry-run` first, then run `agent-reach uninstall` once the user confirms. It removes only what Reach itself installed; the user's other files and browsers are kept.
