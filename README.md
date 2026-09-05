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

### 1. 建仓库
在 GitHub 新建 **public** 仓库（例如 `news-pipeline`），**不要**勾选 README/.gitignore（本地已有）。

### 2. 推送代码
```bash
cd news-pipeline
git init
git add .
git commit -m "init news pipeline"
git branch -M main
git remote add origin https://github.com/<你的用户名>/news-pipeline.git
git push -u origin main
```

### 3. 开启 Actions
推送后到仓库 **Settings → Actions → General**，
把 *Workflow permissions* 设为 **Read and write**（允许每日把产物 commit 回仓库），保存。

> 也可手动触发验证：**Actions → daily-news-digest → Run workflow**。

### 4. 开启 Pages（手机订阅地址）
**Settings → Pages → Source** 选 `Deploy from a branch`，
分支 `main`、目录 **`/docs`**，保存。

等待约 1 分钟，订阅地址即为：

```
https://<你的用户名>.github.io/news-pipeline/feed.xml
```

把这个地址填进手机 RSS 阅读器即可。

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
| 优质开源 | GitHub Trending（daily/weekly/monthly） | 只发未发过的新增 |
| Docker 容器 | GitHub Topics `docker`/`self-hosted`/`homelab` | 只发未发过的新增 |
| 宏观要闻 | 新浪滚动 API | 事件驱动，有新才出 |
| 指数估值 | 蛋卷 `djapi/index_eva/dj`（PE/PB 历史分位） | 每日完整表 |
| 板块 / 黄金 / QDII | 东方财富 + 天天基金净值 | 每日实采 |
| 项目简介中译 | MyMemory 免密钥翻译（备 Google） | 译文缓存复用 |

**两道防重**：① 7 天已发历史持久化去重；② 各文字栏只发"上次运行以来未发过的较新条目"，无新增则扩大搜索范围补齐，**不留空、绝不重发**。

---

## 五、关于 config.yaml

`config.yaml` **当前不被代码读取**。管道为保持零依赖，运行参数直接以常量写在 `digester.py` 内；
`config.yaml` 仅作为信息源与参数登记的**说明文档**，修改它不会改变运行行为。
若后续需要它真正生效，需引入 PyYAML 并增加解析逻辑（会打破零依赖设计）。
