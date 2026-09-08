# 资讯聚合推送管道（news-pipeline）

每日自动抓取 AI 资讯 / 优质开源 / Docker 容器 / 财经估值，生成 Markdown 日报与 RSS feed，供手机 RSS 阅读器订阅。

- **运行环境**：Python 3.10+，**纯标准库、零第三方依赖**（GitHub Actions 免安装）
- **定时**：GitHub Actions 每日 UTC 00:00（= 北京时间 08:00）
- **产物**：`output/YYYY-MM-DD.md`（日报，归档在仓库）+ `docs/feed.xml`（RSS，手机订阅）

---

## 一、本地运行

```bash
cd news-pipeline
python digester.py
```

输出：
- `output/<今天>.md` —— 当日日报
- `docs/feed.xml` —— RSS（最新一期）
- `state/sent_history.json` —— 已发历史（7 天去重依据）
- `state/trans_cache.json` —— 英文简介的中文译文缓存

> 说明：同一天重复运行会因去重机制导致文字栏为空（这是设计如此，不是故障）。
> 需要重新产出完整日报时，先清掉历史里当天的条目再跑。

---

## 二、部署到 GitHub（手机订阅）

**完整手把手教程见 [`DEPLOY.md`](DEPLOY.md)**（含令牌生成、每步点击路径、故障排查表）。

极简版 4 步：

```bash
# 1) GitHub 网页建 public 仓库（不要勾 README/.gitignore）
# 2) 生成令牌：Settings → Developer settings → Personal access tokens
#    → Tokens (classic) → Generate new token (classic) → 只勾 repo
# 3) 推送（密码栏粘贴 ghp_ 开头的令牌，不是登录密码）
cd news-pipeline
git remote add origin https://github.com/<你的用户名>/news-pipeline.git
git branch -M main
git push -u origin main
```

4) 仓库 Settings 里开两个开关：

| 位置 | 设置 |
|---|---|
| **Settings → Actions → General → Workflow permissions** | 改为 **Read and write**（否则每日产物写不回仓库） |
| **Settings → Pages** | Source 选 `main` 分支 + **`/docs`** 目录 |

订阅地址：`https://<你的用户名>.github.io/news-pipeline/feed.xml`

> 本地已完成 `git init` 与多次提交，工作区干净，可直接推送。
> 也可以直接用一键脚本（Git Bash 运行）：`bash tools/push_github.sh <你的用户名>`，
> 它会自动完成"登记远端 → 统一分支名 → 推送"，并在最后打印还需在网页上做的两步设置。

---

## 三、手机 RSS 阅读器

| 系统 | 推荐 | 说明 |
|---|---|---|
| Android | **Feeder**、NetNewsWire、ReadYou | F-Droid / 应用商店可装 |
| iOS | **NetNewsWire**、Reeder | App Store |

订阅时填上面的 `feed.xml` 地址即可（必须 https，本地文件路径手机取不到）。

---

## 四、内容与数据源

| 栏目 | 数据源 | 更新策略 |
|---|---|---|
| AI 资讯 | aihot `/api/v1/items`（游标分页，7 天池） | 只发未发过的新增 |
| 优质开源 | GitHub Trending（限时补充） + **官方 Search API**（稳定主源） | 只发未发过 + 生态多样性去重 |
| Docker 容器 | **官方 Search API**：`topic:self-hosted`/`docker`/`homelab` | 只发未发过的新增 |
| 宏观要闻 | 新浪滚动 API | 事件驱动，不足 8 条则深翻页+宽关键词补足 |
| 指数估值 | 蛋卷 `djapi/index_eva/dj`（PE/PB 历史分位） | 每日完整表（9 个指数） |
| 板块 / 黄金 / QDII | 东方财富 + 天天基金净值 | 每日实采 |
| 项目简介中译 | MyMemory 免密钥翻译（备 Google） | 译文缓存复用 + 术语校正表 |

**两道防重**：① 7 天已发历史持久化去重；② 各文字栏只发"上次运行以来未发过的较新条目"，无新增则扩大搜索范围补齐，**不留空、绝不重发**。

**关于 7 天窗口（重要，避免误解）**：窗口是**滚动**的——一条内容发出后 7 天内不会再现，
但**第 8 天起它会重新进入可发池**。这是刻意设计：既避免短期重复刷屏，又保证池子不会被永久耗尽。
当前池容量（实测）：AI 87 条、开源 71 条、Docker 83 条，按每天 10/10/8 条消耗，
配合源本身的每日更新，**不会出现空栏**（已用"模拟次日运行"验证：四栏均可满额输出）。
若日后希望"发过就永不重发"，把 `DEDUP_WINDOW` 调大即可（代价是池子终会耗尽，需相应扩大搜索）。

**为什么开源栏改用 Search API**：`github.com` 的 HTML 页（trending / topics）在部分网络环境极慢或直接
`IncompleteRead`（实测 trending 页耗时 86 秒后失败），而 `api.github.com` 稳定且快（3.5s/30 条）、
自带 description。现策略：HTML trending 只取 daily 一页作补充（提供"今日新增 stars"，12 秒预算快速失败），
Search API 作为稳定主源始终并入。Docker 池则完全走 Search API（原 topics HTML 单次 30 秒 × 3 次）。

**运行耗时**：优化后约 50～90 秒（优化前曾因慢源拖到 6 分 41 秒被超时杀掉）。

---

## 五、故障降级与维护

| 现象 | 原因 | 处理 |
|---|---|---|
| 某指数点位/涨跌幅为 `—` | 行情代码（secid）失效 | 日志会打印 `[IDX] 无行情返回：<名称> (<secid>)`；去东财搜索正确 QuoteID 后改 `fetch_indices()` 映射 |
| 项目简介是英文 | 翻译源（MyMemory）当天不可用 | 已做熔断+重试；恢复后执行 `python tools/retranslate_md.py output/<日期>.md` 回填中文 |
| Actions 日志出现 `[FATAL] 核心栏为空` | 当天数据源异常 | 已自动终止、不提交残缺日报、不消费去重历史，直接 Re-run 即可 |
| 想预判明天会不会空栏 | — | `python tools/dryrun_tomorrow.py`（只读演练次日各栏可发量） |

**翻译源说明**：主源 MyMemory、备源 Google。两者都失败时**保留英文原文**，不输出错译
（实测备用端点 `mymemory.translated.net/api/get` 会给出严重错误的译文，故未接入）。
单次运行有熔断保护（Google 一次失败即停用、MyMemory 连续 5 次失败停用），避免拖到数分钟。

---

## 六、关于 config.yaml

`config.yaml` **当前不被代码读取**。管道为保持零依赖，运行参数直接以常量写在 `digester.py` 内；
`config.yaml` 仅作为信息源与参数登记的**说明文档**，修改它不会改变运行行为。
若后续需要它真正生效，需引入 PyYAML 并增加解析逻辑（会打破零依赖设计）。
