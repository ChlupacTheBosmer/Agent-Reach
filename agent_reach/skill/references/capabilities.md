# Capability scope

Read when the user asks what can be fetched or whether it is complete, or when you need to judge how complete a result is. The table lists declared capabilities; it does not mean every network, account and piece of content has been tested.

## Three layers

- **Public, worth trying**: the agent's own web tools, native tools or public interfaces may already cover the task; no browser install or login is required. Answer from the evidence you actually got.
- **Reach adds**: the actions the current release declares, see `agent-reach channels`. When setup is needed, first reuse the session already saved in the dedicated browser; the user logs in themselves only when the chosen route actually requires it.
- **Verified on this machine**: only the specific actions that recently passed acceptance on this machine. Installing, saving a session and reaching the network are not the same as passing acceptance.

## Channels in this release

| Channel | Public route worth trying | What Reach adds, and its limits |
|---|---|---|
| GitHub | public repos, README, issues/PRs | repo search, reading repos/issues/PRs and the comments returned; private access is judged separately |
| YouTube | search, titles, descriptions; native tools may get some comments and subtitle text | search/read/subtitle/transcribe; no unified comments action yet; a track list is not subtitle text; without subtitles, transcription depends on the matching tool |
| Reddit | search, post text, some comments; some public JSON includes parent/child IDs | browser search/read/subreddit; `saved` reads the user's own saved posts (`agent-reach reddit saved [--limit N]`, needs login); whether login is needed depends on the chosen route; no guarantee of the full comment tree or all replies; no commenting |
| X/Twitter | single public posts found by search; some official public embeds return the full text | search/profile/tweets/thread; `bookmarks` reads the user's own bookmarks (`agent-reach twitter bookmarks [--limit N]`, needs login); a readable single post does not mean the reply stream is readable; no guarantee of the full comment tree |
| Instagram | some index and shallow profile data; the HTML app shell is not a post | search finds users, profile reads a profile, posts reads a given user's post descriptions; `saved` reads the user's own saved posts (`agent-reach instagram saved [--limit N] [--collection NAME]`, needs login); no unified comments action; never claim to understand images/video |
| Facebook | some index or HTML snippets; snippets may be truncated | search/profile; no unified detail or comments action; never treat a search snippet as full text |
| LinkedIn | search public jobs, read public job descriptions | jobs/read try the public web first; only an explicit opencli backend goes in-site; no guarantee every job is readable; no saved-items action |
| Web | the agent's own search/read | Reach web search/read is the anonymous fallback; snippets, page shells and verification pages are not full body text |

All actions are read-only: nothing here posts, likes, comments, bookmarks or saves. After login, say the browser actions above "can be enabled / tried", never "login gives all comments". If the user skips login, deliver the public evidence you already have.
