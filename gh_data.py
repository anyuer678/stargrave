"""GitHub API 只读数据源（StarGrave 数据层）。"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterator

from github import Auth, Github


@dataclass
class RepoInfo:
    full_name: str
    html_url: str
    stars: int
    language: str | None
    pushed_at: datetime
    archived: bool
    open_issues: int
    description: str | None
    repo_created_at: datetime
    fetched_at: datetime


@dataclass
class FetchResult:
    repos: list[RepoInfo]
    rate_remaining: int
    rate_reset_at: datetime | None


def make_github(token: str | None = None, *, per_page: int = 100):
    """构造 GitHub 客户端：优先 auth=Auth.Token(token)（PyGithub 推荐用法）。"""
    kwargs: dict = {"per_page": per_page}
    if token:
        kwargs["auth"] = Auth.Token(token)
    return Github(**kwargs)


def fetch_error_classes() -> tuple[type[Exception], ...]:
    """返回 GitHub API 统一异常出口类型元组。"""
    from github import GithubException, RateLimitExceededException

    return (RateLimitExceededException, GithubException)


def get_starred(username: str, token: str | None, *, per_page: int = 100) -> Iterator[RepoInfo]:
    """分页迭代指定用户的 star 仓库，带进度打印与限流检查。"""
    gh = make_github(token, per_page=per_page)
    if not token:
        print("提示：未提供 Token，将以匿名方式访问（限流 60 次/小时），建议设置 GITHUB_TOKEN")
    starred = gh.get_user(username).get_starred()
    count = 0
    for repo in starred:
        count += 1
        yield _to_repo_info(repo)
        if count % per_page == 0:
            print(f"进度：已拉取 {count} 个仓库…")
        _rate_limit_wait(gh)


def fetch(username: str, token: str | None, *, per_page: int = 100) -> FetchResult:
    """拉取全部 star 仓库并附带本次拉取的限流状态。"""
    gh = make_github(token, per_page=per_page)
    repos = list(get_starred(username, token, per_page=per_page))
    remaining = 0
    reset_at = None
    try:
        rate = gh.get_rate_limit()
        remaining = rate.core.remaining
        reset_at = rate.core.reset
    except Exception:
        pass
    return FetchResult(repos=repos, rate_remaining=remaining, rate_reset_at=reset_at)


def list_starred(username: str, token: str | None, *, per_page: int = 100) -> list[RepoInfo]:
    """兼容别名：一次性返回全部 star 仓库列表。"""
    return list(get_starred(username, token, per_page=per_page))


def _to_repo_info(repo) -> RepoInfo:
    return RepoInfo(
        full_name=repo.full_name,
        html_url=repo.html_url,
        stars=repo.stargazers_count,
        language=repo.language,
        pushed_at=repo.pushed_at,
        archived=repo.archived,
        open_issues=repo.open_issues_count,
        description=repo.description,
        repo_created_at=repo.created_at,
        fetched_at=datetime.now(timezone.utc),
    )


def _rate_limit_wait(gh) -> None:
    try:
        remaining = gh.rate_limiting[0]
        if remaining is None or remaining >= 100:
            return
        reset_at = gh.rate_limiting_resettime
        wait = max(0, reset_at - time.time())
        if wait <= 60:
            print(f"限流余量 {remaining}，等待 {wait:.0f}s 至 reset")
            time.sleep(wait)
        else:
            print(f"限流余量 {remaining}，reset 尚需 {wait:.0f}s，建议使用 Token 提升配额")
    except Exception:
        pass
