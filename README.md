# StarGrave —— Star 仓库清理建议器

> 扫描你的 GitHub star 仓库，用本地规则（可选叠加 LLM）判断哪些仓库已死、哪些值得复查，并安全地执行 unstar / undo 回滚。

[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-51%20passed-brightgreen)](tests/)
[![Deps](https://img.shields.io/badge/deps-PyGithub%20%2B%20rich-blueviolet)](requirements.txt)

「你 4,000 个 Star 里有 30% 是死仓库」——帮你把 star 库清点一遍，决定留谁、清谁。

## 功能特性

| 能力 | 说明 |
|---|---|
| 只读扫描 | 默认不写任何 GitHub 状态；unstar/undo 均有 `--yes` 安全门 |
| 规则 + LLM 双重判断 | 本地硬规则兜底（2 年无 push / archived / 低 star）；无 LLM key 时优雅降级纯规则 |
| 24h 缓存 | SQLite 本地缓存，命中即跳过 API 拉取（`--refresh` 强制刷新） |
| 幂等 + 可回滚 | `unstarred_at` 记录在案，重复执行自动跳过；`undo` 一键恢复 |
| 批量限速 | 每请求间 sleep 0.5s，遇 403 限流立即急停 |
| 纯静态 Web 版 | 浏览器直连 GitHub API，判定规则与 CLI 完全一致，支持清点条/筛选/导出报告 |

## 快速开始

```bash
pip install -r requirements.txt

# 获取 Token：github.com/settings/tokens → Generate new token → 勾选 repo（只读）
$env:GITHUB_TOKEN = 'ghp_xxx'    # PowerShell

# 扫描并生成建议（有 LLM_API_KEY 时叠加 LLM，否则纯规则）
starclean scan --user 你的用户名

# 纯规则判断，JSON 输出
starclean scan --user 你的用户名 --no-llm --json

# 生成 Markdown 报告
starclean report --to report.md

# 按建议批量 unstar（--yes 为最终确认）
starclean unstar --dead --yes --token env:GITHUB_TOKEN

# 撤销：恢复某个已 unstar 的仓库
starclean undo --repo owner/repo --yes --token env:GITHUB_TOKEN
```

**安全约定**：Token 只从环境变量读取，`--token` 参数仅接受 `env:VAR` 形式（如 `--token env:GITHUB_TOKEN`），拒绝明文传入。

## Web 版

打开 `web/index.html`（纯静态，无需后端）即可在浏览器中使用：输入用户名 → 扫描 → 清点条可视化（清理/再看/保留）→ 按判定筛选 → 导出 Markdown 报告。Token 仅保存在浏览器 `localStorage`，只发送到 `api.github.com`。

## 判定规则

| 条件 | 判定 |
|---|---|
| archived 且 >730 天无 push | 清理 |
| 已归档（archived） | 清理 |
| >730 天无 push 且 <30 star | 清理 |
| >730 天无 push 但有关注度 | 值得再看 |
| ≤90 天内有 push | 保留 |
| 其余 | 交由你判断（可叠加 LLM） |

## 项目结构

```
main.py        CLI 入口（scan/report/unstar/undo）
gh_data.py     GitHub API 只读数据源（分页/限流/进度）
store.py       SQLite 缓存与判定历史（24h）
analyze.py     规则 + LLM 判定层（LLM_API_KEY 仅此处使用）
actions.py     唯一写 GitHub 的模块（403 急停/幂等/回滚）
web/index.html 纯静态 Web 版
tests/         pytest 测试（51 例，全 mock 不触网）
```

## 测试

```bash
pytest tests/ -q
```

## 隐私与免责

- `GITHUB_TOKEN` 只从环境变量读取，不写入命令行历史、配置文件或日志；仓库数据只缓存在本地 SQLite。
- 若配置 `LLM_API_KEY`，LLM 仅收到仓库公开元数据摘要（名称/star/语言/最近 push/描述截断），且只返回 JSON 判定。
- 清理建议由规则与 LLM 自动生成，可能存在误判；**所有 unstar/undo 均需人工通过 `--yes` 最终确认**。

本项目仅供学习交流与演示用途，不构成任何形式的商业服务或技术承诺。软件按「现状」提供，不作任何明示或暗示的保证，包括但不限于适销性、特定用途适用性与非侵权性。
您理解并同意：使用本项目即表示您自行承担全部风险。如您在使用过程中发现缺陷或问题，欢迎通过 GitHub Issues 反馈，但作者不因使用本软件所直接或间接产生的任何损失（包括但不限于数据丢失、业务中断、第三方索赔）承担责任。
本项目以功能演示与学习交流为主要目的，其架构设计、安全基线、容错机制与性能表现均未按生产级标准进行验证与加固，不适用于实际生产环境或关键业务场景。任何将本项目部署于生产系统、对外提供服务、或将其接入真实业务工作流的做法，均属使用者的自主决策行为；由此产生的任何直接或间接不良后果，包括但不限于服务中断、数据损坏或泄露、业务损失、合规风险、以及因依赖本软件而引发的第三方纠纷，**开发者均不承担任何责任**。若您确有生产级使用需求，请在充分评估与自行加固（包括但不限于安全审计、压力测试、代码审查）后，自行承担相应风险。

## License

[GPL-3.0](LICENSE) — Copyright (C) 2026 anyuer678
