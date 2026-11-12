"""Fork: human pace, daily caps and pauses after blocks (runtime/pacing.py), and the page-view readers."""
from unittest.mock import Mock

import pytest

from agent_reach.channels import instagram, linkedin
from agent_reach.runtime import pacing, page
from agent_reach.runtime.dispatch import run_action
from agent_reach.runtime.result import ReachError


@pytest.fixture
def paced(monkeypatch):
    monkeypatch.setenv("AGENT_REACH_PACING", "on")
    monkeypatch.setattr(pacing.random, "uniform", lambda a, b: 0)
    clock = {"t": 1_000_000.0}
    slept = []

    def sleep(s):
        slept.append(round(s))
        clock["t"] += s
    return (lambda: clock["t"]), sleep, slept, clock


def test_second_request_waits_the_sites_gap(paced):
    now, sleep, slept, _ = paced
    pacing.before("instagram", now=now, sleep=sleep)
    pacing.before("instagram", now=now, sleep=sleep)
    assert slept == [pacing.LIMITS["instagram"][0]]
    pacing.before("reddit", now=now, sleep=sleep)  # other sites have their own gap
    assert slept == [pacing.LIMITS["instagram"][0]]


def test_daily_cap_refuses_instead_of_waiting(paced, monkeypatch):
    now, sleep, _, _ = paced
    monkeypatch.setitem(pacing.LIMITS, "linkedin", (0, 0, 2))
    pacing.before("linkedin", now=now, sleep=sleep)
    pacing.before("linkedin", now=now, sleep=sleep)
    with pytest.raises(ReachError, match="budget") as exc:
        pacing.before("linkedin", now=now, sleep=sleep)
    assert exc.value.code == "RATE_LIMITED"


def test_a_block_pauses_the_whole_site_for_hours(paced):
    now, sleep, _, clock = paced
    assert pacing.failed("reddit", 'Reddit blocked /r/x with its "blocked by network security" page', now=now)
    with pytest.raises(ReachError, match="paused until"):
        pacing.before("reddit", now=now, sleep=sleep)
    clock["t"] += pacing.PAUSE_HOURS * 3600 + 1
    pacing.before("reddit", now=now, sleep=sleep)


def test_ordinary_errors_do_not_pause(paced):
    now, _, _, _ = paced
    assert not pacing.failed("instagram", "User not found: someone", now=now)


def test_instagram_profile_reads_the_page(monkeypatch):
    seen = {}

    def fake(site, url, js, **kw):
        seen["site"], seen["url"] = site, url
        return {"url": url, "header": "nasa", "posts": ["https://www.instagram.com/nasa/p/A/"],
                "description": 'Sledující (104M), Sleduji (95), Příspěvky (5,009) – NASA (@nasa) na Instagramu: „Bio“'}
    monkeypatch.setattr(page, "read", fake)
    out = run_action(instagram.InstagramChannel(), "profile", {"username": "@nasa"})
    assert out["ok"] and out["backend"] == "page" and seen == {"site": "instagram", "url": "https://www.instagram.com/nasa/"}
    item = out["items"][0]
    assert item["title"] == "NASA" and item["raw"]["followers"] == "104M" and item["raw"]["recent_posts"]


def test_instagram_search_opens_each_hit_for_its_creator(monkeypatch):
    def fake(site, url, js, **kw):
        if "/explore/search/" in url:
            return {"posts": ["https://www.instagram.com/p/A/", "https://www.instagram.com/p/B/"]}
        return {"url": url, "og": '10 likes, 2 comments - scicomm.cz on May 1, 2026: "Science for all"', "time": None}
    monkeypatch.setattr(page, "read", Mock(side_effect=fake))
    out = run_action(instagram.InstagramChannel(), "search", {"query": "science", "limit": 1})
    assert out["ok"] and len(out["items"]) == 1
    assert out["items"][0]["author"] == "scicomm.cz" and out["items"][0]["text"] == "Science for all"
    assert page.read.call_count == 2  # the results page and one post: nothing more than asked for


def test_linkedin_person_returns_cv_sections(monkeypatch):
    def fake(site, url, js, **kw):
        assert site == "linkedin" and url == "https://www.linkedin.com/in/someone/"
        return {"cards": {"Topcard": "Jana Nováková\nData scientist", "Experience": "CESNET\n2020 - now",
                          "Education": "Masaryk University"}}
    monkeypatch.setattr(page, "read", fake)
    out = run_action(linkedin.LinkedInChannel(), "person", {"target": "https://www.linkedin.com/in/someone/?trk=x"})
    item = out["items"][0]
    assert out["ok"] and item["title"] == "Jana Nováková"
    assert "## Experience\nCESNET" in item["text"] and item["raw"]["sections"]["Education"] == "Masaryk University"


def test_linkedin_people_and_company_use_opencli(monkeypatch):
    from agent_reach.channels import _opencli_site
    call = Mock(return_value=[{"name": "X", "profile_url": "https://www.linkedin.com/in/x/"}])
    monkeypatch.setattr(_opencli_site, "opencli_call", call)
    assert run_action(linkedin.LinkedInChannel(), "people", {"keywords": "ml brno", "limit": 3})["ok"]
    assert call.call_args.args == ("linkedin", ["people-search", "ml brno", "--limit", "3"])
    run_action(linkedin.LinkedInChannel(), "company", {"target": "https://www.linkedin.com/company/cesnet/about/"})
    assert call.call_args.args == ("linkedin", ["company", "https://www.linkedin.com/company/cesnet/"])
