# 交付清单 — Star 仓库清理建议器（StarGrave）

## 一、文件清单

```
main.py        CLI 入口（scan/report/unstar/undo 子命令，--token 仅 env:VAR）
gh_data.py     GitHub API 只读数据源（分页/进度/限流等待，PyGithub Auth.Token）
store.py       SQLite 缓存与判定历史（24h 缓存、幂等、unstarred_at 记录）
analyze.py     规则 + LLM 判定层（LLM_API_KEY 仅此处使用，畸形 JSON 降级 unknown）
actions.py     唯一写 GitHub 的模块（403 急停、限速 0.5s、幂等、undo 恢复）
web/index.html 纯静态 Web 版（浏览器直连 api.github.com，无后端）
requirements.txt  PyGithub + rich
tests/
  test_analyze.py  14 项：规则判定/LLM 降级/JSON 容错/score 钳制
  test_main.py     14 项：CLI 子命令/退出码/token 校验
  test_actions.py   9 项：unstar/restar/批量限速/403 急停/幂等
  test_store.py     8 项：缓存/upsert/判定持久化/回滚记录
  test_gh_data.py   6 项：分页/限流/异常出口
```

## 二、验证结果（本机 Windows / Python 3.12.3）

- `python -m pytest tests/ -q` → **51 passed**（全 mock，不触网）
- 规则判定实测（构造 RepoInfo）：
  - 活跃仓库（5 天前 push，120★）→ `keep` / score 90
  - 死亡仓库（2 年无 push，5★）→ `unstar` / score 45
  - 归档仓库 → `unstar` / score 35
  - 观望仓库（2 年无 push，800★）→ `revisit` / score 60
- 安全约定实测：`--token` 仅接受 `env:VAR`，明文拒绝

## 三、接口核对清单（架构设计 §接口，全部通过）

- [x] `rule_verdict(RepoInfo) -> Verdict | None`（本地硬规则确定性判定）
- [x] `llm_verdict(RepoInfo) -> Verdict`（失败/畸形降级 unknown，不执行任何动作）
- [x] `unstar_many(repos, token, confirm_fn, ...)` 403 急停 + 限速 + 幂等跳过已处理
- [x] `undo` 用 `restar` 恢复并清除记录
- [x] 退出码：0 成功 / 1 参数错误 / 2 无 token 且未指定 --user / 3 API 异常

## 四、本轮交付（前端改造 + 协议统一）

**前端改造（web/index.html）**

- 设计：kb-ui 风格设计令牌（CSS 变量），单一「极简」主题（黑白 + 语义色徽章保留）
- 交互保留：清点条可视化（清理/再看/保留三段）、判定筛选 chip、仓库搜索、Markdown 报告导出
- 功能与 CLI 判定规则完全一致（archived → 清理；>730 天且 <30★ → 清理；>730 天 → 再看；≤90 天 → 保留）

**协议**

- LICENSE 统一为 GPL-3.0（Copyright (C) 2026 anyuer678）；pyproject 声明 `license = GPL-3.0`

## 五、安全与隐私

- `GITHUB_TOKEN` 只从环境变量读取（`--token env:VAR`），不写命令行历史/配置/日志
- 仓库数据只缓存在本地 SQLite（默认 `~/.starclean.db`）；LLM 仅收到公开元数据摘要
- 本工具不收集、不上传任何使用数据；unstar/undo 均有 `--yes` 安全门

## 六、与文档的偏差

- `fetch()` 数据源用 PyGithub `Auth.Token`（推荐用法），非 `login(token)` 旧接口
- Web 版为纯静态演示（不做清理操作），token 存浏览器 localStorage，只发往 api.github.com
- 匿名扫描（无 token）限流 60 次/小时，输出中提示建议设置 token
