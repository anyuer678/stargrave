"""actions 模块测试：dry-run、确认门、幂等与 403 急停。"""

from datetime import datetime, timezone

import pytest
from github.GithubException import GithubException

import actions
from actions import ActionError, set_store
from store import StarStore


@pytest.fixture
def store(tmp_path, monkeypatch):
    s = StarStore(str(tmp_path / "t.db"), username="octocat")
    set_store(s)
    yield s
    set_store(None)


class FakeGithubRepo:
    def __init__(self):
        self.unstarred = 0
        self.starred = 0

    def unstar(self):
        self.unstarred += 1

    def star(self):
        self.starred += 1


class FakeGithub:
    def __init__(self):
        self.repos = {}

    def get_repo(self, name):
        if name not in self.repos:
            self.repos[name] = FakeGithubRepo()
        return self.repos[name]


def test_unstar_dry_run_no_network(monkeypatch):
    fake = FakeGithub()

    def boom(token):
        raise AssertionError("dry_run 不应构造 Github 客户端")

    monkeypatch.setattr(actions, "make_github", boom)
    assert actions.unstar("a/one", "tok", dry_run=True) is True
    assert actions.unstar("a/one", "tok") is True
    assert fake.repos == {}


def test_unstar_many_confirm_called_before_execution(monkeypatch, store):
    order = []
    monkeypatch.setattr(
        actions, "unstar",
        lambda repo, token, dry_run=False: order.append("unstar") or True,
    )
    store.upsert_repos([_repo("a/one")])

    def confirm(repos):
        order.append("confirm")
        return True

    result = actions.unstar_many(["a/one"], "tok", sleep_s=0, confirm_fn=confirm)
    assert result == {"a/one": "done"}
    assert order == ["confirm", "unstar"]
    assert "a/one" in store.list_unstarred()


def test_unstar_many_confirm_false_cancels(monkeypatch, store):
    calls = []

    def fake_unstar(repo, token, dry_run=False):
        calls.append(repo)
        return True

    monkeypatch.setattr(actions, "unstar", fake_unstar)
    store.upsert_repos([_repo("a/one")])
    result = actions.unstar_many(
        ["a/one"], "tok", sleep_s=0, confirm_fn=lambda repos: False
    )
    assert result == {"a/one": "cancelled"}
    assert calls == []
    assert store.list_unstarred() == []


def test_unstar_many_skips_already(store):
    store.upsert_repos([_repo("a/one")])
    store.mark_unstarred("a/one", datetime.now(timezone.utc))
    confirmed = []

    def confirm(repos):
        confirmed.append(repos)
        return True

    result = actions.unstar_many(["a/one"], "tok", sleep_s=0, confirm_fn=confirm)
    assert result == {"a/one": "skipped"}
    assert confirmed == []


def test_unstar_many_idempotent_second_run(monkeypatch, store):
    calls = []

    def fake_unstar(repo, token, dry_run=False):
        calls.append(repo)
        return True

    monkeypatch.setattr(actions, "unstar", fake_unstar)
    store.upsert_repos([_repo("a/one")])
    first = actions.unstar_many(["a/one"], "tok", sleep_s=0, confirm_fn=lambda r: True)
    second = actions.unstar_many(["a/one"], "tok", sleep_s=0, confirm_fn=lambda r: True)
    assert first == {"a/one": "done"}
    assert second == {"a/one": "skipped"}
    assert calls == ["a/one"]


def test_unstar_many_403_halts(monkeypatch, store):
    calls = []

    def fake_unstar(repo, token, dry_run=False):
        calls.append(repo)
        raise ActionError("403 rate limit", status=403)

    monkeypatch.setattr(actions, "unstar", fake_unstar)
    store.upsert_repos([_repo("a/one"), _repo("b/two"), _repo("c/three")])
    result = actions.unstar_many(
        ["a/one", "b/two", "c/three"], "tok", sleep_s=0, batch_size=2,
        confirm_fn=lambda r: True,
    )
    assert result["a/one"] == "failed"
    assert result["b/two"] == "failed"
    assert result["c/three"] == "failed"
    assert calls == ["a/one"]


def test_unstar_many_batches_with_sleep(monkeypatch, store):
    sleeps = []

    def fake_sleep(s):
        sleeps.append(s)

    monkeypatch.setattr(actions.time, "sleep", fake_sleep)
    monkeypatch.setattr(actions, "unstar", lambda repo, token, dry_run=False: True)
    store.upsert_repos([_repo("a/one"), _repo("b/two"), _repo("c/three")])
    actions.unstar_many(
        ["a/one", "b/two", "c/three"], "tok", sleep_s=0.5, batch_size=2,
        confirm_fn=lambda r: True,
    )
    assert len(sleeps) == 3


def test_restar(monkeypatch):
    fake = FakeGithub()
    monkeypatch.setattr(actions, "make_github", lambda token: fake)
    assert actions.restar("a/one", "tok") is True
    assert fake.repos["a/one"].starred == 1


def test_unstar_wraps_github_errors(monkeypatch):
    def boom(token):
        raise GithubException(404, {"message": "not found"})

    monkeypatch.setattr(actions, "make_github", boom)
    with pytest.raises(ActionError) as exc:
        actions.unstar("a/one", "tok", dry_run=False)
    assert exc.value.status == 404


def _repo(name):
    from datetime import timedelta

    from gh_data import RepoInfo

    pushed = datetime.now(timezone.utc) - timedelta(days=800)
    return RepoInfo(
        full_name=name,
        html_url="https://github.com/" + name,
        stars=1,
        language="Python",
        pushed_at=pushed,
        archived=True,
        open_issues=0,
        description="d",
        repo_created_at=pushed,
        fetched_at=datetime.now(timezone.utc),
    )
