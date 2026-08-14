"""StarGrave CLI 编排入口（starclean 命令）。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter

import gh_data
import actions
import analyze
from analyze import Verdict
from store import StarStore

_DEFAULT_DB = "~/.starclean.db"


def resolve_token(arg: str | None) -> str | None:
    """从环境变量解析 token，仅接受 env:VAR 形式，拒绝明文。"""
    if arg is None:
        return os.environ.get("GITHUB_TOKEN")
    if isinstance(arg, str) and arg.startswith("env:"):
        name = arg[4:]
        if not name:
            raise SystemExit("--token 需带变量名（--token env:GITHUB_TOKEN）")
        value = os.environ.get(name)
        if value is None:
            raise SystemExit(f"环境变量 {name} 未设置")
        return value
    raise SystemExit("--token 仅接受 env:VAR 形式（如 --token env:GITHUB_TOKEN），禁止传入明文")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="starclean", description="Star 仓库清理建议器（StarGrave）"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_scan = sub.add_parser("scan", help="扫描 star 仓库并生成清理建议")
    p_scan.add_argument("--user", help="GitHub 用户名")
    p_scan.add_argument("--token", help="仅接受 env:VAR 形式")
    p_scan.add_argument("--refresh", action="store_true", help="忽略 24h 缓存强制重新拉取")
    p_scan.add_argument("--no-llm", action="store_true", help="跳过 LLM 判断，仅用本地规则")
    p_scan.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    p_scan.add_argument("--db", default=_DEFAULT_DB, help="SQLite 数据库路径")

    p_report = sub.add_parser("report", help="从缓存生成 Markdown 报告")
    p_report.add_argument("--to", dest="to", default="report.md", help="报告输出路径")
    p_report.add_argument("--db", default=_DEFAULT_DB)

    p_unstar = sub.add_parser("unstar", help="按建议批量 unstar 仓库")
    p_unstar.add_argument("--dead", action="store_true", help="仅处理判定为 unstar 的仓库")
    p_unstar.add_argument("--stale", action="store_true", help="仅处理判定为 revisit 的仓库")
    p_unstar.add_argument("--all", action="store_true", help="处理全部按建议的仓库（unstar+revisit）")
    p_unstar.add_argument(
        "--yes", action="store_true",
        help="确认执行；无此参数时绝不发起 unstar 请求",
    )
    p_unstar.add_argument("--dry-run", action="store_true", help="仅演练，不执行")
    p_unstar.add_argument("--token", help="仅接受 env:VAR 形式")
    p_unstar.add_argument("--db", default=_DEFAULT_DB)

    p_undo = sub.add_parser("undo", help="恢复已 unstar 的仓库（重新 star）")
    p_undo.add_argument("--repo", help="仅恢复指定仓库")
    p_undo.add_argument("--yes", action="store_true", help="确认执行 restar")
    p_undo.add_argument("--token", help="仅接受 env:VAR 形式")
    p_undo.add_argument("--db", default=_DEFAULT_DB)

    return parser


def cmd_scan(args) -> int:
    try:
        token = resolve_token(args.token)
    except SystemExit as e:
        print(e)
        return 2
    username = args.user
    if not username:
        if token:
            try:
                username = _current_user(token)
            except gh_data.fetch_error_classes() as e:
                print(f"无法从 Token 解析当前用户（token 可能无效）：{e}")
                return 2
        else:
            _print_token_guide()
            return 2
    store = StarStore(args.db, username=username)
    repos = []
    if not args.refresh:
        repos = store.get_cached(username)
        if repos:
            print(f"命中 24h 缓存（{len(repos)} 个仓库），跳过 API 拉取")
    if not repos:
        try:
            repos = list(gh_data.get_starred(username, token))
        except gh_data.fetch_error_classes() as e:
            print(f"GitHub API 拉取失败：{e}")
            return 1
        store.upsert_repos(repos)
        print(f"已拉取并缓存 {len(repos)} 个仓库")
    prev = store.get_verdicts(username)
    verdicts = []
    for repo in repos:
        if not args.refresh and repo.full_name in prev:
            verdicts.append(prev[repo.full_name])
            continue
        if args.no_llm:
            v = analyze.rule_verdict(repo) or Verdict(
                repo.full_name, "keep", 70, "规则未命中，--no-llm 默认保留", "rule"
            )
        else:
            v = analyze.combined(repo)
        verdicts.append(v)
    store.save_verdicts(verdicts)
    if args.json:
        print(json.dumps(
            [_verdict_to_json(v) for v in sorted(verdicts, key=lambda x: x.score)],
            ensure_ascii=False, indent=2,
        ))
    else:
        _print_table(verdicts, {r.full_name: r for r in repos})
        _print_summary(verdicts)
    return 0


def cmd_report(args) -> int:
    store = StarStore(args.db)
    verdicts = store.get_verdicts()
    if not verdicts:
        print("没有已保存的建议，请先运行 starclean scan")
        return 1
    md = analyze.summarize(list(verdicts.values()))
    try:
        with open(args.to, "w", encoding="utf-8") as f:
            f.write(md)
    except OSError as e:
        print(f"写入报告失败：{e}")
        return 1
    print(f"报告已保存到 {args.to}")
    return 0


def cmd_unstar(args) -> int:
    try:
        token = resolve_token(args.token)
    except SystemExit as e:
        print(e)
        return 2
    if not token:
        print("执行 unstar 需要 GITHUB_TOKEN（--token env:GITHUB_TOKEN）")
        return 2
    store = StarStore(args.db)
    verdicts = store.get_verdicts()
    try:
        targets = _select_targets(verdicts, args)
    except SystemExit as e:
        print(e)
        return 2
    if not targets:
        print("没有符合筛选条件的候选仓库")
        return 0
    print("以下仓库将被 unstar：")
    for repo in targets:
        v = verdicts[repo]
        print(f"  - {repo} [{v.verdict}, score={v.score}] {v.reason}")
    if args.dry_run:
        print(f"dry-run：将 unstar {len(targets)} 个仓库（未发起任何请求）")
        return 0
    if not args.yes:
        print(f"未提供 --yes，仅展示清单，未发起任何 unstar 请求（共 {len(targets)} 个）")
        return 0
    actions.set_store(store)
    result = actions.unstar_many(targets, token, confirm_fn=lambda repos: True)
    for repo, status in result.items():
        print(f"  {repo}: {status}")
    return 0


def cmd_undo(args) -> int:
    try:
        token = resolve_token(args.token)
    except SystemExit as e:
        print(e)
        return 2
    if not token:
        print("执行 undo 需要 GITHUB_TOKEN（--token env:GITHUB_TOKEN）")
        return 2
    store = StarStore(args.db)
    targets = [args.repo] if args.repo else store.list_unstarred()
    if not targets:
        print("没有可恢复的 unstar 记录")
        return 0
    if not args.yes:
        print("以下仓库将被重新 star（restar）：")
        for repo in targets:
            print(f"  - {repo}")
        print("未提供 --yes，仅展示清单，未执行")
        return 0
    actions.set_store(store)
    ok = fail = 0
    for repo in targets:
        try:
            actions.restar(repo, token)
            store.clear_unstarred(repo)
            print(f"  {repo}: restared")
            ok += 1
        except actions.ActionError as e:
            print(f"  {repo}: failed（{e}）")
            fail += 1
    print(f"恢复完成：成功 {ok}，失败 {fail}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "scan":
        return cmd_scan(args)
    if args.command == "report":
        return cmd_report(args)
    if args.command == "unstar":
        return cmd_unstar(args)
    if args.command == "undo":
        return cmd_undo(args)
    parser.print_help()
    return 0


def _current_user(token: str) -> str:
    return gh_data.make_github(token).get_user().login


def _print_token_guide() -> None:
    print("未提供 GITHUB_TOKEN 且未指定 --user，无法拉取 star 仓库。")
    print("获取 Token 指引：")
    print("  1. 打开 https://github.com/settings/tokens 创建 Personal Access Token")
    print("  2. 创建经典 Token，勾选 repo 只读权限即可（本工具只读公开数据）")
    print("  3. 设置环境变量：$env:GITHUB_TOKEN='xxx'（PowerShell）")
    print("之后运行：starclean scan --user 你的用户名 --token env:GITHUB_TOKEN")


def _select_targets(verdicts: dict, args) -> list[str]:
    if not (args.dead or args.stale or args.all):
        raise SystemExit("必须指定 --dead、--stale 或 --all 之一")
    names = list(verdicts.keys())
    if args.all:
        return [n for n in names if verdicts[n].verdict in ("unstar", "revisit")]
    picks = []
    if args.dead:
        picks += [n for n in names if verdicts[n].verdict == "unstar"]
    if args.stale:
        picks += [n for n in names if verdicts[n].verdict == "revisit"]
    return picks


def _verdict_to_json(v: Verdict) -> dict:
    return {
        "repo": v.repo,
        "verdict": v.verdict,
        "score": v.score,
        "reason": v.reason,
        "source": v.source,
    }


def _print_table(verdicts: list[Verdict], repo_by: dict) -> None:
    from rich.console import Console
    from rich.table import Table

    console = Console()
    table = Table(title="Star 仓库清理建议（按 score 升序，最该清理在前）")
    for col in ("仓库", "stars", "last push", "verdict", "score", "source", "reason"):
        table.add_column(col)
    for v in sorted(verdicts, key=lambda x: x.score):
        repo = repo_by.get(v.repo)
        pushed = repo.pushed_at.date().isoformat() if repo else "-"
        stars = repo.stars if repo else "-"
        table.add_row(
            v.repo, str(stars), pushed, v.verdict, str(v.score), v.source, v.reason
        )
    console.print(table)


def _print_summary(verdicts: list[Verdict]) -> None:
    counts = Counter(v.verdict for v in verdicts)
    parts = ", ".join(f"{k}: {counts[k]}" for k in ("keep", "unstar", "revisit", "unknown"))
    print(f"统计：{parts}；共 {len(verdicts)} 个仓库")


if __name__ == "__main__":
    sys.exit(main())
