# Sessions and the dedicated browser

Read when the user wants to log in, log out or switch accounts, or when a result returns `NEED_LOGIN`.

## Dedicated browser

- All browser channels share one dedicated browser profile, separate from the user's everyday Chrome; the program never closes or changes the user's everyday browser.
- CloakBrowser is the preferred engine; if download or install fails, the installed Chrome is used and that fallback is remembered, so it is not downloaded again every time. Once the network is fixed, `agent-reach browser setup --retry-cloak` retries.
- `agent-reach add <channel>` prepares Node, OpenCLI, the engine and the extension automatically. Tools are installed at the versions this release was tested with. On Windows with the Chrome fallback, the user may need to click "Add to Chrome" once.
- Headless by default, no windows. Only `agent-reach login <channel>` shows a window; while it is open, queries use that window and do not close it. While the user is logging in, wait until they say they are done before querying. After the window closes, later queries go back to headless.
- Closes automatically after 10 idle minutes and reopens on the next call; real page activity and Reach calls reset the timer. The login is saved periodically while the user logs in, so they do not need to close the window.
- Calls may run in parallel: up to 2 at once per platform and 4 across all platforms; the rest wait in line for up to 2 minutes, and only then return `BUSY`. Two searches on the same platform start at least 10 seconds apart. Opening a login window, logging out and reinstalling the browser wait for running queries to finish.
- `agent-reach browser status` shows the state, `browser setup` prepares the engine and extension, `browser stop` closes the browser the program manages.

## Reusing an existing login automatically

1. The first time a site is used, if the user's Chrome is connected to the OpenCLI extension, the program copies that site's login (that site's cookies only) into the dedicated browser once. It never copies the whole browser folder, other sites or the password store, and never changes the source browser.
2. After a successful copy, the saved session is reused; it is not re-copied on a timer. Cookies being present does not mean the login is valid; the result of a real action decides.
3. If the user's Chrome is not connected to the extension, do not promise automatic reuse, and do not conclude the user is logged out; use the session Reach has already saved.
4. If several browsers on this machine are logged in to the site: `agent-reach browser profiles` lists the sources and `agent-reach browser use <ID>` picks one. Never switch at random.
5. `agent-reach login <channel> --import-existing` copies that site's login again on demand, without opening a window. If no login to copy is found, or the OpenCLI Bridge is not running, it returns `NEED_SETUP` and the saved session stays as it was.
6. Once the user has logged in themselves in the dedicated window, or has run `logout` / `remove`, the login is no longer copied automatically, so a logged-out session does not come back.

## When the user must log in

- Ask the user to log in only when a result actually returns `NEED_LOGIN` and the rest of the task really needs that route. A login button next to the body text is not a login requirement.
- Reading the user's own saved items (`twitter bookmarks`, `reddit saved`, `instagram saved`) always needs a login to that site.
- `agent-reach login <channel>` opens the dedicated browser at the site's login page, and the user logs in themselves. You never log in for them, never ask for a password, never print cookies.
- If the result's `next` lists follow-up steps, follow them.
- When the user is done, run the original action again to verify.

## Logging out and removing

- `agent-reach logout <channel>`: clears that site's login and the channel's own state in the dedicated browser; other sites are kept.
- `agent-reach remove <channel>`: removes the channel and also clears that site's login and storage; other sites are kept. If cleanup fails, the channel record is kept and an error is returned, so the user can retry.
- After either, that site's login is no longer copied automatically.
