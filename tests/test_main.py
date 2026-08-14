"""main 模块测试：CLI 编排、安全门与缓存行为（mock 网络，不跑长链路）。"""

from datetime import datetime, timedelta, timezone

import pytest

import main as main_module
from analyze import Verdict
from gh_data import RepoInfo
from store import StarStore

NOW = datetime.now(timezone.utc)


def make_repo(name, days_ago=30, stars=100, archived=False):
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


def seed_unstar_target(db, name="a/one"):
    s = StarStore(db, username="octocat")
    s.upsert_repos([make_repo(name, days_ago=800, archived=True)])
    s.save_verdicts([Verdict(name, "unstar", 15, "死仓库", "rule")])
    return s


def test_scan_no_token_no_user_prints_guide(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    rc = main_module.main(["scan", "--db", str(tmp_path / "t.db")])
    assert rc == 2
    assert "Token" in capsys.readouterr().out


def test_scan_invalid_token_without_user_graceful(monkeypatch, tmp_path, capsys):
    """回归：无效 token + 无 --user 时 _current_user 抛 GithubException 不应崩溃。"""
    from github.GithubException import GithubException

    monkeypatch.setenv("GITHUB_TOKEN", "bad_token")

    def boom(token):
        raise GithubException(401, {"message": "Bad credentials"})

    monkeypatch.setattr(main_module, "_current_user", boom)
    rc = main_module.main(["scan", "--db", str(tmp_path / "t.db")])
    assert rc == 2
    out = capsys.readouterr().out
    assert "Token" in out
    assert "Traceback" not in out


def test_scan_fetch_then_cache(monkeypatch, tmp_path, capsys):
    db = str(tmp_path / "t.db")
    fetched = []

    def fake_get_starred(username, token):
        fetched.append(username)
        return [make_repo("a/one"), make_repo("b/two", days_ago=800, archived=True)]

    monkeypatch.setattr(main_module.gh_data, "get_starred", fake_get_starred)
    rc1 = main_module.main(["scan", "--user", "octocat", "--db", db, "--no-llm"])
    assert rc1 == 0
    assert len(fetched) == 1
    rc2 = main_module.main(["scan", "--user", "octocat", "--db", db, "--no-llm"])
    assert rc2 == 0
    assert len(fetched) == 1, "24h 缓存内不应二次拉取 API"
    assert "命中 24h 缓存" in capsys.readouterr().out


def test_scan_refresh_forces_fetch(monkeypatch, tmp_path):
    db = str(tmp_path / "t.db")
    fetched = []

    def fake_get_starred(username, token):
        fetched.append(username)
        return [make_repo("a/one")]

    monkeypatch.setattr(main_module.gh_data, "get_starred", fake_get_starred)
    main_module.main(["scan", "--user", "octocat", "--db", db, "--no-llm"])
    main_module.main(["scan", "--user", "octocat", "--db", db, "--no-llm", "--refresh"])
    assert len(fetched) == 2


def test_scan_no_llm_skips_combined(monkeypatch, tmp_path):
    db = str(tmp_path / "t.db")
    monkeypatch.setenv("LLM_API_KEY", "some-key")
    monkeypatch.setattr(
        main_module.gh_data, "get_starred",
        lambda username, token: [make_repo("a/one", days_ago=300)],
    )
    combined_calls = []
    monkeypatch.setattr(
        main_module.analyze, "combined",
        lambda repo: combined_calls.append(repo) or None,
    )
    rc = main_module.main(["scan", "--user", "octocat", "--db", db, "--no-llm"])
    assert rc == 0
    assert combined_calls == []


def test_scan_uses_combined_by_default(monkeypatch, tmp_path):
    db = str(tmp_path / "t.db")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setattr(
        main_module.gh_data, "get_starred",
        lambda username, token: [make_repo("a/one", days_ago=300)],
    )
    combined_calls = []
    monkeypatch.setattr(
        main_module.analyze, "combined",
        lambda repo: combined_calls.append(repo) or Verdict(repo.full_name, "keep", 70, "d", "rule"),
    )
    rc = main_module.main(["scan", "--user", "octocat", "--db", db])
    assert rc == 0
    assert len(combined_calls) == 1


def test_unstar_no_yes_no_request(monkeypatch, tmp_path):
    db = str(tmp_path / "t.db")
    seed_unstar_target(db)
    monkeypatch.setenv("GITHUB_TOKEN", "secret")
    calls = []
    monkeypatch.setattr(
        main_module.actions, "unstar_many",
        lambda *a, **k: calls.append(a) or {},
    )
    rc = main_module.main(
        ["unstar", "--dead", "--db", db, "--token", "env:GITHUB_TOKEN"]
    )
    assert rc == 0
    assert calls == [], "无 --yes 时绝不发起 unstar 请求"


def test_unstar_yes_calls_unstar_many(monkeypatch, tmp_path):
    db = str(tmp_path / "t.db")
    seed_unstar_target(db)
    monkeypatch.setenv("GITHUB_TOKEN", "secret")
    calls = []
    monkeypatch.setattr(
        main_module.actions, "unstar_many",
        lambda repos, token, **kw: calls.append((repos, kw)) or {r: "done" for r in repos},
    )
    rc = main_module.main(
        ["unstar", "--dead", "--db", db, "--yes", "--token", "env:GITHUB_TOKEN"]
    )
    assert rc == 0
    assert len(calls) == 1
    assert calls[0][0] == ["a/one"]
    assert calls[0][1]["confirm_fn"] is not None


def test_unstar_requires_filter(monkeypatch, tmp_path):
    db = str(tmp_path / "t.db")
    seed_unstar_target(db)
    monkeypatch.setenv("GITHUB_TOKEN", "secret")
    rc = main_module.main(
        ["unstar", "--db", db, "--yes", "--token", "env:GITHUB_TOKEN"]
    )
    assert rc == 2


def test_unstar_dry_run_no_execution(monkeypatch, tmp_path):
    db = str(tmp_path / "t.db")
    seed_unstar_target(db)
    monkeypatch.setenv("GITHUB_TOKEN", "secret")
    calls = []
    monkeypatch.setattr(
        main_module.actions, "unstar_many",
        lambda *a, **k: calls.append(a) or {},
    )
    rc = main_module.main(
        ["unstar", "--dead", "--db", db, "--dry-run", "--token", "env:GITHUB_TOKEN"]
    )
    assert rc == 0
    assert calls == []


def test_report_writes_markdown(tmp_path):
    db = str(tmp_path / "t.db")
    seed_unstar_target(db)
    out = str(tmp_path / "report.md")
    rc = main_module.main(["report", "--db", db, "--to", out])
    assert rc == 0
    with open(out, encoding="utf-8") as f:
        content = f.read()
    assert "Star 仓库清理建议" in content


def test_token_rejects_plaintext():
    with pytest.raises(SystemExit):
        main_module.resolve_token("ghp_plaintext_token")


def test_token_env_prefix_accepted(monkeypatch):
    monkeypatch.setenv("MY_GH_TOKEN", "abc")
    assert main_module.resolve_token("env:MY_GH_TOKEN") == "abc"


def test_token_reads_env_default(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "abc")
    assert main_module.resolve_token(None) == "abc"
