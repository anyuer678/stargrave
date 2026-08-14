# StarGrave —— Star 仓库清理建议器（CLI）

StarGrave 扫描你的 GitHub star 仓库，用本地规则（可选叠加 LLM）判断哪些仓库已死、哪些值得复查，并安全地执行 unstar / undo 回滚。

## 特性

- **只读扫描**：默认不写任何 GitHub 状态；unstar/undo 均有 `--yes` 安全门。
- **规则 + LLM 双重判断**：本地硬规则（2 年无 push、archived、低 star）兜底；无 `LLM_API_KEY` 时优雅降级为纯规则，JSON 解析失败标记 `unknown` 且不执行任何动作。
- **24h 缓存**：`store.get_cached` 命中即跳过 API 拉取（`--refresh` 强制刷新）。
- **幂等**：`unstarred_at` 记录在案，重复执行自动跳过；数据刷新不丢判定与历史。
- **可回滚**：`undo` 用 `restar` 恢复并清除记录。
- **批量限速**：批量 unstar 每请求间 sleep 0.5s，遇 403 限流立即急停。

## 安装

```bash
python -m pip install -r requirements.txt     # PyGithub + rich + pytest
python -m pip install -e .                    # 可选：安装 starclean / gh-star-clean 命令
```

> Python 3.10+；本机开发环境为 Python 3.12。

## 获取 GitHub Token

1. 打开 <https://github.com/settings/tokens>，点击 **Generate new token** → 选择 **classic**。
2. 勾选 **repo**（只读即可，本工具只读公开数据）后生成。
3. 设置环境变量：

```powershell
# PowerShell
$env:GITHUB_TOKEN = 'ghp_xxx'
```

```cmd
:: CMD
set GITHUB_TOKEN=ghp_xxx
```

**安全约定**：Token 只从环境变量读取，`--token` 参数仅接受 `env:VAR` 形式（例如 `--token env:GITHUB_TOKEN`），拒绝任何明文传入。

## 使用示例

```bash
# 扫描并生成建议（有 LLM_API_KEY 时叠加 LLM，否则纯规则）
starclean scan --user 你的用户名

# 纯规则判断，JSON 输出（适合演示/脚本）
starclean scan --user 你的用户名 --no-llm --json

# 忽略 24h 缓存强制重拉
starclean scan --user 你的用户名 --refresh

# 生成 Markdown 报告
starclean report --to report.md

# 按建议批量 unstar（仅处理判定为 unstar 的仓库；--yes 为最终确认，缺失则只展示不执行）
starclean unstar --dead --yes --token env:GITHUB_TOKEN

# 演练（不执行）
starclean unstar --all --dry-run --token env:GITHUB_TOKEN

# 撤销：恢复某个已 unstar 的仓库
starclean undo --repo owner/repo --yes --token env:GITHUB_TOKEN
```

### 示例输出（`scan --no-llm`）

```
命中 24h 缓存（3 个仓库），跳过 API 拉取
┌──────────────┬───────┬────────────┬─────────┬───────┬────────┬───────────────────────────┐
│ 仓库         │ stars │ last push  │ verdict │ score │ source │ reason                    │
├──────────────┼───────┼────────────┼─────────┼───────┼────────┼───────────────────────────┤
│ dead/demo    │ 5     │ 2022-01-01 │ unstar  │ 45    │ rule   │ 仅 5 star 且 1038 天无 push│
│ old/legacy   │ 800   │ 2021-03-01 │ revisit │ 60    │ rule   │ 1422 天无 push，建议复查    │
│ active/new   │ 120   │ 2026-01-01 │ keep    │ 90    │ rule   │ 7 天内有 push，仍在维护     │
└──────────────┴───────┴────────────┴─────────┴───────┴────────┴───────────────────────────┘
统计：keep: 1, unstar: 1, revisit: 1, unknown: 0；共 3 个仓库
```

## 无 Token 时

未提供 `GITHUB_TOKEN` 且未指定 `--user` 时，程序不会崩溃，而是打印获取 Token 的指引并退出（退出码 2）。有 `--user` 时允许匿名扫描（限流 60 次/小时），会在输出中提示建议设置 Token。

## 运行测试

```bash
pytest tests/ -q
```

测试全部使用 mock 假响应，不触网。

## 隐私说明

- `GITHUB_TOKEN` 只从环境变量读取，不写入命令行历史、配置文件或日志。
- 仓库数据只缓存在本地 SQLite（默认 `~/.starclean.db`，可用 `--db` 指定路径）。
- `LLM_API_KEY` 仅进入 analyze 模块；LLM 仅收到仓库的公开元数据摘要（名称、star 数、语言、最近 push 时间、描述截断），并明确要求只返回 JSON 判定。
- 本工具不收集、不上传任何使用数据。

## AI 免责声明

StarGrave 给出的"清理建议"由本地规则与 LLM 自动生成，可能存在误判（例如把维护缓慢但仍有价值的项目判为已死）。**所有 unstar/undo 操作均需人工通过 `--yes` 最终确认**；建议先查看建议理由再批量执行。LLM 的判定仅作参考，不代表对任何项目的价值评价。
