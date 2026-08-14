"""规则 + LLM 判断层（StarGrave 只读判定，LLM_API_KEY 仅在此模块使用）。"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from gh_data import RepoInfo


@dataclass
class Verdict:
    repo: str
    verdict: str  # keep/unstar/revisit/unknown
    score: int  # 保留分 0-100，越低越建议清理
    reason: str
    source: str  # rule/llm


RULE_EXPIRED_DAYS = 730
RULE_ARCHIVED_DAY = 1
RULE_MIN_STARS_ACTIVE = 30

_DEFAULT_LLM_URL = "https://api.openai.com/v1/chat/completions"
_DEFAULT_LLM_MODEL = "gpt-4o-mini"
_VALID_VERDICTS = ("keep", "unstar", "revisit")


def rule_verdict(repo: RepoInfo) -> Verdict | None:
    """按本地硬规则给出确定性判断，无法确定时返回 None。"""
    now = datetime.now(timezone.utc)
    pushed = repo.pushed_at
    if pushed.tzinfo is None:
        pushed = pushed.replace(tzinfo=timezone.utc)
    days = max(0, (now - pushed).days)
    if repo.archived:
        if days > RULE_EXPIRED_DAYS:
            return Verdict(
                repo.full_name, "unstar", 15,
                f"已归档且 {days} 天无 push（2 年无活动 + archived）", "rule",
            )
        return Verdict(repo.full_name, "unstar", 35, "仓库已归档（archived）", "rule")
    if days > RULE_EXPIRED_DAYS:
        if repo.stars < RULE_MIN_STARS_ACTIVE:
            return Verdict(
                repo.full_name, "unstar", 45,
                f"仅 {repo.stars} star 且 {days} 天无 push", "rule",
            )
        return Verdict(
            repo.full_name, "revisit", 60,
            f"{days} 天无 push，仍有 {repo.stars} star，建议复查", "rule",
        )
    if days <= 90:
        return Verdict(
            repo.full_name, "keep", 90,
            f"{days} 天内有 push，仍在维护", "rule",
        )
    return None


def llm_verdict(repo: RepoInfo) -> Verdict:
    """调用 LLM 对单个仓库给出判断，失败或畸形时降级为 unknown。"""
    api_key = os.environ.get("LLM_API_KEY")
    if not api_key:
        return Verdict(repo.full_name, "unknown", 50, "未配置 LLM_API_KEY，跳过 LLM", "llm")
    prompt = _build_prompt(repo)
    for _ in range(2):
        try:
            text = _call_llm(prompt, api_key)
        except (HTTPError, URLError, OSError, ValueError):
            continue
        parsed = _parse_llm_json(text)
        if parsed is not None:
            return Verdict(
                repo.full_name, parsed["verdict"], parsed["score"],
                parsed["reason"], "llm",
            )
    return Verdict(repo.full_name, "unknown", 50, "LLM 返回畸形 JSON，无法判断", "llm")


def combined(repo: RepoInfo) -> Verdict:
    """规则优先，规则未命中时走 LLM；LLM 不可用时默认保留。"""
    rule = rule_verdict(repo)
    if rule is not None:
        return rule
    if os.environ.get("LLM_API_KEY"):
        return llm_verdict(repo)
    return Verdict(repo.full_name, "keep", 70, "规则未命中且未配置 LLM_API_KEY，默认保留", "rule")


def summarize(candidates: list[Verdict]) -> str:
    """将判定列表生成为 Markdown 报告。"""
    lines = [
        "# Star 仓库清理建议",
        "",
        "| 仓库 | verdict | score | 来源 | 理由 |",
        "|---|---:|---:|---|---|",
    ]
    for v in sorted(candidates, key=lambda x: x.score):
        lines.append(f"| {v.repo} | {v.verdict} | {v.score} | {v.source} | {v.reason} |")
    lines.append("")
    lines.append("> 本报告由本地规则与 LLM 自动生成，仅供清理参考，最终决定请人工确认。")
    return "\n".join(lines)


def _build_prompt(repo: RepoInfo) -> str:
    desc = (repo.description or "")[:200]
    return (
        "判断以下 GitHub 仓库是否还值得保留 star，只返回 JSON："
        '{"score": 0-100 整数, "verdict": "keep|unstar|revisit", '
        '"reason": "一句话理由"}。score 越高越值得保留，越低越建议清理。\n'
        f"full_name: {repo.full_name}\n"
        f"stars: {repo.stars}\n"
        f"language: {repo.language}\n"
        f"pushed_at: {repo.pushed_at.isoformat()}\n"
        f"archived: {repo.archived}\n"
        f"open_issues: {repo.open_issues}\n"
        f"description: {desc}"
    )


def _call_llm(prompt: str, api_key: str) -> str:
    url = os.environ.get("LLM_API_URL", _DEFAULT_LLM_URL)
    model = os.environ.get("LLM_MODEL", _DEFAULT_LLM_MODEL)
    payload = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
        }
    ).encode("utf-8")
    req = Request(
        url,
        data=payload,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    with urlopen(req, timeout=60) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return body["choices"][0]["message"]["content"]


def _parse_llm_json(text: str) -> dict | None:
    try:
        obj = json.loads(text)
    except (ValueError, TypeError):
        return None
    if isinstance(obj, list):
        obj = obj[0] if obj else None
    if not isinstance(obj, dict):
        return None
    verdict = obj.get("verdict")
    if verdict not in _VALID_VERDICTS:
        return None
    try:
        score = int(obj.get("score"))
    except (TypeError, ValueError):
        return None
    score = max(0, min(100, score))
    reason = str(obj.get("reason", "")).strip() or "LLM 未给出理由"
    return {"verdict": verdict, "score": score, "reason": reason}
