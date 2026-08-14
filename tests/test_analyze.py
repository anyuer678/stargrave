"""analyze 模块测试：规则判定、LLM 解析、降级与汇总。"""

from datetime import datetime, timedelta, timezone

import pytest

import analyze
from analyze import Verdict
from gh_data import RepoInfo

NOW = datetime.now(timezone.utc)


def make_repo(name="a/one", days_ago=30, stars=100, archived=False):
    pushed = NOW - timedelta(days=days_ago)
    return RepoInfo(
        full_name=name,
        html_url="https://github.com/" + name,
        stars=stars,
        language="Python",
        pushed_at=pushed,
        archived=archived,
        open_issues=0,
        description="desc",
        repo_created_at=pushed,
        fetched_at=NOW,
    )


def test_rule_verdict_archived_expired():
    v = analyze.rule_verdict(make_repo(days_ago=800, archived=True))
    assert v is not None
    assert v.verdict == "unstar"
    assert v.source == "rule"


def test_rule_verdict_archived_recent():
    v = analyze.rule_verdict(make_repo(days_ago=10, archived=True))
    assert v is not None
    assert v.verdict == "unstar"


def test_rule_verdict_expired_small_stars():
    v = analyze.rule_verdict(make_repo(days_ago=800, stars=5))
    assert v is not None
    assert v.verdict == "unstar"


def test_rule_verdict_expired_large_stars():
    v = analyze.rule_verdict(make_repo(days_ago=800, stars=500))
    assert v is not None
    assert v.verdict == "revisit"


def test_rule_verdict_recent_keep():
    v = analyze.rule_verdict(make_repo(days_ago=30))
    assert v is not None
    assert v.verdict == "keep"
    assert v.score == 90


def test_rule_verdict_mid_returns_none():
    v = analyze.rule_verdict(make_repo(days_ago=300, stars=100))
    assert v is None


def test_llm_verdict_valid(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setattr(
        analyze,
        "_call_llm",
        lambda prompt, key: '{"score": 30, "verdict": "unstar", "reason": "dead"}',
    )
    v = analyze.llm_verdict(make_repo())
    assert v.verdict == "unstar"
    assert v.score == 30
    assert v.source == "llm"


def test_llm_verdict_malformed_unknown(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setattr(analyze, "_call_llm", lambda prompt, key: "not json at all")
    v = analyze.llm_verdict(make_repo())
    assert v.verdict == "unknown"
    assert "畸形" in v.reason


def test_llm_verdict_invalid_enum(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setattr(
        analyze,
        "_call_llm",
        lambda prompt, key: '{"score": 80, "verdict": "dead", "reason": "x"}',
    )
    v = analyze.llm_verdict(make_repo())
    assert v.verdict == "unknown"


def test_llm_verdict_no_key(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    calls = []
    monkeypatch.setattr(analyze, "_call_llm", lambda prompt, key: calls.append(key))
    v = analyze.llm_verdict(make_repo())
    assert v.verdict == "unknown"
    assert calls == []


def test_llm_verdict_retries_once(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    responses = iter(["broken", '{"score": 60, "verdict": "revisit", "reason": "x"}'])
    monkeypatch.setattr(analyze, "_call_llm", lambda prompt, key: next(responses))
    v = analyze.llm_verdict(make_repo())
    assert v.verdict == "revisit"


def test_combined_rule_first(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-key")

    def boom(prompt, key):
        raise AssertionError("规则命中时不应调用 LLM")

    monkeypatch.setattr(analyze, "_call_llm", boom)
    v = analyze.combined(make_repo(days_ago=800, archived=True))
    assert v.verdict == "unstar"
    assert v.source == "rule"


def test_combined_no_key_default_keep(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    v = analyze.combined(make_repo(days_ago=300, stars=100))
    assert v.verdict == "keep"
    assert v.score == 70


def test_summarize():
    verdicts = [
        Verdict("a/one", "unstar", 15, "死仓库", "rule"),
        Verdict("b/two", "keep", 90, "活跃", "rule"),
    ]
    md = analyze.summarize(verdicts)
    assert "# Star 仓库清理建议" in md
    assert "| 仓库 | verdict | score | 来源 | 理由 |" in md
    assert md.index("a/one") < md.index("b/two")
