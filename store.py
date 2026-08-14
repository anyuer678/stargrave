"""SQLite 缓存与状态（StarGrave 唯一持久层）。"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import asdict
from datetime import datetime, timezone

from gh_data import RepoInfo

SCHEMA = """
CREATE TABLE IF NOT EXISTS stars(
  full_name TEXT PRIMARY KEY,
  username TEXT NOT NULL DEFAULT '',
  fetched_at INTEGER,           -- 缓存时间（epoch 秒）
  data_json TEXT,               -- RepoInfo 序列化（供离线重算）
  verdict TEXT,                 -- keep/unstar/revisit/unknown/null
  reason TEXT,
  score REAL,                   -- 保留分 0-100，越低越建议清理
  source TEXT DEFAULT '',       -- rule/llm
  unstarred_at INTEGER,         -- null=未处理
  decision_user TEXT            -- keep/unstar/''（人工确认结果）
)
"""

_CACHE_SECONDS = 24 * 3600


def _to_ts(dt: datetime) -> int:
    return int(dt.timestamp())


def _repo_to_json(repo: RepoInfo) -> str:
    data = asdict(repo)
    for key, value in data.items():
        if isinstance(value, datetime):
            data[key] = value.isoformat()
    return json.dumps(data)


def _repo_from_json(text: str) -> RepoInfo:
    data = json.loads(text)
    for key, value in data.items():
        if value is not None and isinstance(value, str) and key.endswith("_at"):
            data[key] = datetime.fromisoformat(value)
    return RepoInfo(**data)


class StarStore:
    def __init__(self, path: str = "~/.starclean.db", username: str | None = None):
        self.path = os.path.expanduser(path)
        self.username = username or ""
        self._conn = sqlite3.connect(self.path)
        self._conn.executescript(SCHEMA)

    def get_cached(self, username: str) -> list[RepoInfo]:
        """返回 24 小时内缓存的仓库，超期或缺失返回空列表。"""
        cutoff = int(datetime.now(timezone.utc).timestamp()) - _CACHE_SECONDS
        rows = self._conn.execute(
            "SELECT data_json FROM stars WHERE username=? AND fetched_at>=?",
            (username, cutoff),
        ).fetchall()
        return [_repo_from_json(r[0]) for r in rows]

    def upsert_repos(self, repos: list[RepoInfo]) -> None:
        """写入或刷新仓库数据，保留已有的判定与 unstar 记录。"""
        now = _to_ts(datetime.now(timezone.utc))
        for repo in repos:
            self._conn.execute(
                "INSERT INTO stars(full_name, username, fetched_at, data_json) VALUES(?,?,?,?) "
                "ON CONFLICT(full_name) DO UPDATE SET "
                "username=excluded.username, fetched_at=excluded.fetched_at, "
                "data_json=excluded.data_json",
                (repo.full_name, self.username, now, _repo_to_json(repo)),
            )
        self._conn.commit()

    def save_verdicts(self, verdicts: list) -> None:
        """写入判定结果（verdict+reason+score），幂等覆盖。"""
        for v in verdicts:
            if self.username:
                self._conn.execute(
                    "UPDATE stars SET verdict=?, reason=?, score=?, source=? "
                    "WHERE full_name=? AND username=?",
                    (v.verdict, v.reason, v.score, v.source, v.repo, self.username),
                )
            else:
                self._conn.execute(
                    "UPDATE stars SET verdict=?, reason=?, score=?, source=? WHERE full_name=?",
                    (v.verdict, v.reason, v.score, v.source, v.repo),
                )
        self._conn.commit()

    def mark_unstarred(self, repo: str, at: datetime) -> None:
        """记录某仓库已 unstar 的时间，用于幂等去重。"""
        if self.username:
            self._conn.execute(
                "UPDATE stars SET unstarred_at=? WHERE full_name=? AND username=?",
                (_to_ts(at), repo, self.username),
            )
        else:
            self._conn.execute(
                "UPDATE stars SET unstarred_at=? WHERE full_name=?",
                (_to_ts(at), repo),
            )
        self._conn.commit()

    def clear_unstarred(self, repo: str) -> None:
        """清除 unstar 标记（undo 恢复 star 后调用）。"""
        if self.username:
            self._conn.execute(
                "UPDATE stars SET unstarred_at=NULL WHERE full_name=? AND username=?",
                (repo, self.username),
            )
        else:
            self._conn.execute(
                "UPDATE stars SET unstarred_at=NULL WHERE full_name=?", (repo,)
            )
        self._conn.commit()

    def list_unstarred(self) -> list[str]:
        """返回已 unstar 的仓库名列表。"""
        if self.username:
            rows = self._conn.execute(
                "SELECT full_name FROM stars WHERE username=? AND unstarred_at IS NOT NULL",
                (self.username,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT full_name FROM stars WHERE unstarred_at IS NOT NULL"
            ).fetchall()
        return [r[0] for r in rows]

    def list_pending(self) -> list[str]:
        """返回尚未 unstar 的仓库名列表。"""
        if self.username:
            rows = self._conn.execute(
                "SELECT full_name FROM stars WHERE username=? AND unstarred_at IS NULL",
                (self.username,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT full_name FROM stars WHERE unstarred_at IS NULL"
            ).fetchall()
        return [r[0] for r in rows]

    def get_verdicts(self, username: str | None = None) -> dict:
        """返回已有判定结果，键为仓库全名，值为 Verdict。"""
        from analyze import Verdict

        user = username if username is not None else self.username
        if user:
            rows = self._conn.execute(
                "SELECT full_name, verdict, reason, score, source FROM stars "
                "WHERE username=? AND verdict IS NOT NULL",
                (user,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT full_name, verdict, reason, score, source FROM stars "
                "WHERE verdict IS NOT NULL"
            ).fetchall()
        result = {}
        for full_name, verdict, reason, score, source in rows:
            result[full_name] = Verdict(
                repo=full_name,
                verdict=verdict,
                score=int(score or 0),
                reason=reason or "",
                source=source or "",
            )
        return result
