"""unstar/star 执行层（StarGrave 唯一写 GitHub 的模块）。"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Callable

from github.GithubException import GithubException

from gh_data import make_github
from store import StarStore


class ActionError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


_store: StarStore | None = None


def set_store(store: StarStore) -> None:
    """注入 StarStore 实例（默认使用 ~/.stargrave.db）。"""
    global _store
    _store = store


def _get_store() -> StarStore:
    global _store
    if _store is None:
        _store = StarStore()
    return _store


def unstar(repo: str, token: str, *, dry_run: bool = True) -> bool:
    """对单个仓库执行 unstar；dry_run 时不发起任何网络请求。"""
    if dry_run:
        return True
    try:
        make_github(token).get_repo(repo).unstar()
        return True
    except GithubException as e:
        raise ActionError(str(e), status=e.status) from e


def restar(repo: str, token: str) -> bool:
    """对单个仓库重新 star（undo 回滚）。"""
    try:
        make_github(token).get_repo(repo).star()
        return True
    except GithubException as e:
        raise ActionError(str(e), status=e.status) from e


def unstar_many(
    repos: list[str],
    token: str,
    *,
    batch_size: int = 5,
    sleep_s: float = 0.5,
    confirm_fn: Callable[[list[str]], bool],
) -> dict[str, str]:
    """批量 unstar：先确认、跳过已处理、每请求间限速、403 急停。"""
    store = _get_store()
    already = set(store.list_unstarred())
    result = {r: "skipped" for r in repos if r in already}
    ready = [r for r in repos if r not in already]
    if not ready:
        return result
    if not confirm_fn(ready):
        result.update({r: "cancelled" for r in ready})
        return result
    for start in range(0, len(ready), batch_size):
        for repo in ready[start : start + batch_size]:
            try:
                unstar(repo, token, dry_run=False)
                store.mark_unstarred(repo, datetime.now(timezone.utc))
                result[repo] = "done"
            except ActionError as e:
                result[repo] = "failed"
                if e.status == 403:
                    for leftover in ready[start:]:
                        if leftover not in result:
                            result[leftover] = "failed"
                    print("遭遇 403 限流，急停")
                    return result
            time.sleep(sleep_s)
    return result
