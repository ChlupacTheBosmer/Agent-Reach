"""LinkedIn jobs, people, companies and profiles in the shared Reach browser.

Fork: people search and company pages use OpenCLI; a person's profile (the CV sections) is read from the profile page
a person would open. Every request is paced by runtime/pacing.py."""
import re
from urllib.parse import urlsplit

from agent_reach.runtime.result import BAD_INPUT, ReachError, make_item
from agent_reach.utils.url import host_matches

from ._opencli_site import OpenCLISiteChannel
from .base import Action, Param

PERSON_READY = ("!!document.querySelector('[id$=\"Topcard\"], main h1') || "
                "/\\/(login|checkpoint|authwall|uas)/.test(location.pathname)")
PERSON_JS = """(() => {
  if (/\\/(login|checkpoint|authwall|uas)/i.test(location.pathname)) return {_blocked: location.pathname};
  const cards = {};
  const top = document.querySelector('[id$="Topcard"]') || ((document.querySelector('main h1') || {}).closest || (() => null)).call(document.querySelector('main h1'), 'section');
  if (top) cards.Topcard = top.innerText.trim();
  // Every other section is a profile card with its own heading (About, Experience, Education, Skills...).
  for (const h of document.querySelectorAll('main h2, h2')) {
    const card = h.closest('[id*="profile.card"]') || h.closest('section[data-view-name], section.artdeco-card');
    const name = h.innerText.trim();
    if (!card || !name || cards[name] || card === top) continue;
    cards[name] = card.innerText.trim();
  }
  if (!cards.Topcard) return {_missing: 'LinkedIn profile not found or not rendered'};
  return {cards};
})()"""


class LinkedInChannel(OpenCLISiteChannel):
    name = site = "linkedin"
    description = "LinkedIn jobs, people, companies and profiles"
    domains = ("linkedin.com",)
    login_hint = "linkedin.com"
    login_url = "https://www.linkedin.com/login"
    login_cookie = "li_at"
    level = "optional"
    actions = (
        Action("jobs", "Search LinkedIn jobs", (
            Param("query", "job keywords"), Param("location", "city or country", required=False),
            Param("limit", "max results", required=False, type=int, default=5)),
            (("opencli", "_jobs"),), live_test={"query": "python", "limit": 2}, timeout=120,
            public_web=("linkedin.com/jobs/view/",)),
        Action("read", "Read a LinkedIn job description", (Param("target", "full job URL"),),
               (("opencli", "_read"),), timeout=90, public_web=("linkedin.com/jobs/view/",)),
        Action("people", "Search people (name, headline, location, profile URL)", (
            Param("keywords", "e.g. 'machine learning Brno'"),
            Param("limit", "max people", required=False, type=int, default=10)),
            (("opencli", "_people"),), timeout=180),
        Action("company", "A company page: industry, size, HQ, founded, website, followers, about",
               (Param("target", "company URL or its linkedin.com/company/<slug>"),), (("opencli", "_company"),), timeout=180),
        Action("person", "A person's profile: top card, about, experience, education, skills and the other sections",
               (Param("target", "profile URL (linkedin.com/in/<id>)"),), (("page", "_person"),), timeout=180),
    )

    def _people(self, *, keywords, limit, timeout):
        return self._opencli(["people-search", keywords, "--limit", str(limit)], timeout)[:limit]

    def _company(self, *, target, timeout):
        slug = str(target).strip().rstrip("/").split("/company/")[-1].split("/")[0]
        if not re.fullmatch(r"[\w%.-]+", slug or ""):
            raise ReachError(BAD_INPUT, "Expected a linkedin.com/company/<slug> URL or slug")
        return self._opencli(["company", f"https://www.linkedin.com/company/{slug}/"], timeout)[:1]

    def _person(self, *, target, timeout):
        from agent_reach.runtime import page
        m = re.search(r"linkedin\.com/in/([^/?#]+)", str(target)) or re.fullmatch(r"([\w%-]{2,100})", str(target).strip())
        if not m:
            raise ReachError(BAD_INPUT, "Expected a linkedin.com/in/<id> profile URL")
        url = f"https://www.linkedin.com/in/{m.group(1)}/"
        d = page.read("linkedin", url, PERSON_JS, ready=PERSON_READY, settle=3, scroll_steps=5)
        cards = d.get("cards") or {}
        top = (cards.get("Topcard") or "").splitlines()
        body = "\n\n".join(f"## {name}\n{text}" for name, text in cards.items() if name != "Topcard")
        return [make_item(title=top[0] if top else None, url=url, text=(cards.get("Topcard", "") + "\n\n" + body).strip(),
                          raw={"sections": cards})]

    def _jobs(self, *, query, location, limit, timeout):
        args = ["search", query, "--limit", str(limit)]
        if location:
            args += ["--location", location]
        return self._opencli(args, timeout)[:limit]

    def _read(self, *, target, timeout):
        if not host_matches(str(target), "linkedin.com"):
            raise ReachError(BAD_INPUT, "Expected a linkedin.com job URL")
        return self._opencli(["job-detail", self._job_url(str(target))], timeout)[:1]

    @staticmethod
    def _job_url(target: str) -> str:
        if not host_matches(target, "linkedin.com"):
            raise ReachError(BAD_INPUT, "Expected a linkedin.com job URL")
        match = re.fullmatch(r"/jobs/view/(?:[^/]+-)?([0-9]+)/?", urlsplit(target).path)
        if not match:
            raise ReachError(BAD_INPUT, "Expected a LinkedIn /jobs/view/<job-id> URL")
        # Public indexes use regional hosts and title slugs; upstream accepts IDs.
        return "https://www.linkedin.com/jobs/view/" + match.group(1)

    def validate_params(self, action, params):
        if action == "read":
            params["target"] = self._job_url(str(params["target"]))
