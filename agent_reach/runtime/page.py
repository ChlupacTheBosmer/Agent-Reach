"""Fork: read a page the way a person views it, in the logged-in Agent Reach browser.

For sites whose data endpoints refuse lookups, a normal page view still shows what the user would see. One call =
one paced page load in a new tab: wait until it has rendered, run one read-only expression, close the tab.
The caller (dispatch) already holds the site's browser queue slot.
"""
from __future__ import annotations

import json
import time

from .result import NOT_FOUND, UPSTREAM_BROKEN, ReachError


def read(site: str, url: str, expression: str, *, ready: str = "document.readyState === 'complete'",
         settle: float = 3.0, timeout: float = 45.0):
    """Open `url`, wait for `ready` (a JS boolean), then return the JSON value of `expression`."""
    from . import browser, cdp, pacing

    pacing.before(site)
    browser.ensure_running()
    old_timeout, cdp._TIMEOUT = cdp._TIMEOUT, 30
    target = None
    try:
        target = (cdp.call("Target.createTarget", {"url": url}) or {}).get("targetId")
        if not target:
            raise ReachError(UPSTREAM_BROKEN, f"could not open {url} in the Agent Reach browser")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            time.sleep(1.5)
            ok = cdp.call("Runtime.evaluate", {"expression": f"!!({ready})", "returnByValue": True}, target_id=target)
            if ((ok or {}).get("result") or {}).get("value"):
                break
        else:
            raise ReachError(UPSTREAM_BROKEN, f"{url} did not finish loading within {timeout:.0f}s")
        time.sleep(settle)  # let lazily rendered parts appear, as a reader would wait
        res = cdp.call("Runtime.evaluate", {"expression": f"JSON.stringify(({expression}))", "returnByValue": True},
                       target_id=target)
        raw = ((res or {}).get("result") or {}).get("value")
        if raw is None:
            raise ReachError(UPSTREAM_BROKEN, f"could not read {url}")
        data = json.loads(raw)
    finally:
        if target:
            cdp.call("Target.closeTarget", {"targetId": target})
        cdp._TIMEOUT = old_timeout
    browser.save_session()
    if isinstance(data, dict) and data.get("_blocked"):
        pacing.failed(site, f"checkpoint {data['_blocked']}")
        raise ReachError(UPSTREAM_BROKEN, f"{site} showed a login or verification page ({data['_blocked']})",
                         hint=f"Do not retry. Ask the user to check the account with `agent-reach login {site}`.")
    if isinstance(data, dict) and data.get("_missing"):
        raise ReachError(NOT_FOUND, data["_missing"])
    return data
