"""store 模块测试：缓存、幂等与状态持久化。"""

from datetime import datetime, timedelta, timezone

import pytest

from gh_data import RepoInfo
from store import StarStore, _repo_from_json, _repo_to_json

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


@pytest.fixture
def store(tmp_path):
    return StarStore(str(tmp_path / "test.db"), username="octocat")


def test_upsert_and_get_cached(store):
    repos = [make_repo("a/one"), make_repo("b/two")]
    store.upsert_repos(repos)
    cached = store.get_cached("octocat")
    assert len(cached) == 2
    assert {r.full_name for r in cached} == {"a/one", "b/two"}


def test_get_cached_expired_returns_empty(store):
    store.upsert_repos([make_repo("a/one")])
    old = int((NOW - timedelta(hours=25)).timestamp())
    store._conn.execute("UPDATE stars SET fetched_at=? WHERE full_name='a/one'", (old,))
    store._conn.commit()
    assert store.get_cached("octocat") == []


def test_get_cached_ignores_other_user(store):
    store.upsert_repos([make_repo("a/one")])
    assert store.get_cached("someone-else") == []


def test_upsert_preserves_verdict_and_unstarred(store):
    from analyze import Verdict

    repo = make_repo("a/one", days_ago=800, archived=True)
    store.upsert_repos([repo])
    store.save_verdicts([Verdict("a/one", "unstar", 15, "死仓库", "rule")])
    store.mark_unstarred("a/one", NOW)
    store.upsert_repos([repo])
    verdicts = store.get_verdicts("octocat")
    assert verdicts["a/one"].verdict == "unstar"
    assert "a/one" in store.list_unstarred()


def test_mark_and_list_unstarred(store):
    store.upsert_repos([make_repo("a/one"), make_repo("b/two")])
    assert store.list_pending() == ["a/one", "b/two"]
    store.mark_unstarred("a/one", NOW)
    assert store.list_unstarred() == ["a/one"]
    assert store.list_pending() == ["b/two"]


def test_clear_unstarred(store):
    store.upsert_repos([make_repo("a/one")])
    store.mark_unstarred("a/one", NOW)
    store.clear_unstarred("a/one")
    assert store.list_unstarred() == []


def test_verdicts_roundtrip(store):
    from analyze import Verdict

    store.upsert_repos([make_repo("a/one"), make_repo("b/two")])
    store.save_verdicts(
        [
            Verdict("a/one", "unstar", 15, "死仓库", "rule"),
            Verdict("b/two", "keep", 90, "活跃", "rule"),
        ]
    )
    verdicts = store.get_verdicts("octocat")
    assert verdicts["a/one"].verdict == "unstar"
    assert verdicts["a/one"].score == 15
    assert verdicts["b/two"].source == "rule"


def test_repo_json_roundtrip():
    repo = make_repo("a/one", days_ago=800, archived=True)
    restored = _repo_from_json(_repo_to_json(repo))
    assert restored == repo
    assert restored.pushed_at.tzinfo is not None
