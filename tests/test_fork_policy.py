"""Fork invariants. Run after every upstream merge: they fail when a merge brings back
Chinese text, a removed platform or an upstream self-install path. See FORK.md."""
import re
from pathlib import Path

from agent_reach.channels import get_all_channels
from agent_reach.runtime import manage

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", ".venv", "dist", "build", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache"}
TEXT = {".py", ".md", ".txt", ".toml", ".json", ".yml", ".yaml", ".sh", ".svg", ".cfg", ".ini", ".html"}
# CJK ideographs, CJK punctuation, kana, hangul. Full-width ASCII (U+FF01-U+FF5E) is allowed:
# the URL security tests use full-width digits on purpose.
CJK = re.compile(r"[\u3000-\u303f\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uff61-\uffef]")
REMOVED = ("bilibili", "xiaohongshu", "boss", "xueqiu", "xiaoyuzhou", "v2ex")


def _files():
    for path in ROOT.rglob("*"):
        if path.is_file() and path.suffix in TEXT and not SKIP_DIRS & set(path.relative_to(ROOT).parts):
            yield path


def test_repository_is_english_only():
    hits = []
    for path in _files():
        for n, line in enumerate(path.read_text("utf-8", "ignore").splitlines(), 1):
            if CJK.search(line):
                hits.append(f"{path.relative_to(ROOT)}:{n}: {line.strip()[:80]}")
    assert not hits, "Chinese text came back (translate it):\n" + "\n".join(hits[:50])


def test_removed_platforms_stay_removed():
    names = {ch.name for ch in get_all_channels()}
    assert not names & set(REMOVED), names & set(REMOVED)
    modules = [p.name for p in (ROOT / "agent_reach" / "channels").glob("*.py") if p.stem in REMOVED]
    assert not modules, modules
    assert not (ROOT / "agent_reach" / "skill" / "SKILL_en.md").exists(), "SKILL.md is the only (English) skill"


def test_self_update_never_installs_upstream_code():
    assert manage.FORK_REPO in manage.upgrade_command("9.9.9")[-1]
    assert manage.REPO not in manage.upgrade_command("9.9.9")[-1]


def test_saved_item_actions_exist():
    actions = {ch.name: {a.name for a in ch.actions} for ch in get_all_channels()}
    assert "saved" in actions["instagram"] and "saved" in actions["reddit"] and "bookmarks" in actions["twitter"]
