"""gh_data 模块测试：全部使用 mock 假响应，不触网。"""

import time
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import gh_data


class FakeRepo:
    def __init__(self, name, **kw):
        self.full_name = name
        self.html_url = "https://github.com/" + name
        self.stargazers_count = kw.get("stars", 1)
        self.language = kw.get("language", "Python")
        self.pushed_at = kw.get("pushed_at", datetime.now(timezone.utc))
        self.archived = kw.get("archived", False)
        self.open_issues_count = kw.get("open_issues", 0)
        self.description = kw.get("description", "desc")
        self.created_at = kw.get("created_at", datetime.now(timezone.utc))


class FakeUser:
    def __init__(self, repos):
        self._repos = repos

    def get_starred(self, per_page=100):
        return self._repos


class FakeGithub:
    def __init__(self, repos, remaining=5000):
        self._repos = repos
        self.rate_limiting = (remaining, 5000)
        self.rate_limiting_resettime = 0

    def get_user(self, username):
        return FakeUser(self._repos)

    def get_rate_limit(self):
        reset = datetime.now(timezone.utc)
        return SimpleNamespace(
            core=SimpleNamespace(remaining=self.rate_limiting[0], reset=reset)
        )


@pytest.fixture
def fake_github(monkeypatch):
    def _install(repos, remaining=5000):
        fake = FakeGithub(repos, remaining=remaining)
        monkeypatch.setattr(gh_data, "make_github", lambda token=None, per_page=100: fake)
        return fake

    return _install


def test_get_starred_pagination_and_fields(fake_github):
    fake_repos = [FakeRepo(f"u{i}/r{i}", stars=i, archived=(i % 2 == 0)) for i in range(150)]
    fake_github(fake_repos)
    repos = list(gh_data.get_starred("octocat", None, per_page=50))
    assert len(repos) == 150
    first = repos[0]
    assert first.full_name == "u0/r0"
    assert first.stars == 0
    assert first.archived is True
    assert first.html_url == "https://github.com/u0/r0"
    assert first.pushed_at.tzinfo is not None
    assert first.fetched_at.tzinfo is not None


def test_fetch_returns_fetch_result(fake_github):
    fake_github([FakeRepo("a/b"), FakeRepo("c/d")])
    result = gh_data.fetch("octocat", "tok", per_page=100)
    assert isinstance(result, gh_data.FetchResult)
    assert len(result.repos) == 2
    assert result.rate_remaining == 5000
    assert result.rate_reset_at is not None


def test_fetch_error_classes():
    from github import GithubException, RateLimitExceededException

    classes = gh_data.fetch_error_classes()
    assert RateLimitExceededException in classes
    assert GithubException in classes


def test_make_github_uses_auth_token(monkeypatch):
    captured = {}

    class Fake:
        def __init__(self, **kw):
            captured.update(kw)

    monkeypatch.setattr(gh_data, "Github", Fake)
    gh_data.make_github("tok123", per_page=50)
    assert captured["per_page"] == 50
    assert captured["auth"].token == "tok123"
    captured.clear()
    gh_data.make_github(None, per_page=100)
    assert "auth" not in captured, "匿名访问不应携带 auth"


def test_rate_limit_wait_skips_when_above_100(monkeypatch):
    slept = []
    monkeypatch.setattr(gh_data.time, "sleep", lambda s: slept.append(s))
    gh_data._rate_limit_wait(FakeGithub([], remaining=5000))
    assert slept == []


def test_rate_limit_wait_sleeps_below_100(monkeypatch):
    slept = []
    monkeypatch.setattr(gh_data.time, "sleep", lambda s: slept.append(s))
    fake = FakeGithub([], remaining=50)
    fake.rate_limiting_resettime = int(time.time()) + 10
    gh_data._rate_limit_wait(fake)
    assert len(slept) == 1
