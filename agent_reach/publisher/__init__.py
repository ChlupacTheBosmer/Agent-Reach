"""Fork: the V4AIR publisher service. One backend that posts and messages on every network for a team app.

The app composes and shows a preview; the author confirms the exact content (its hash); the publisher queues it,
runs each post in its account's own Agent Reach home (browser profile, cookies, proxy, pacing) and logs the result.
Design and API: v4air-social docs/publisher.md.
"""
