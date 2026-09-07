# GitHub 部署全流程（手把手版）

本地代码已全部就绪（已 `git init` 并完成 4 次提交，工作区干净）。
这份文档只讲**从零到手机能收到推送**的完整操作，每一步都给出具体点击路径。

> 全程只需做 4 件事：**建仓库 → 生成令牌 → 推送 → 开两个开关**。

---

## 第 0 步：确认本地状态

在本机终端（Git Bash）执行：

```bash
cd /e/yang_news/news-pipeline
git log --oneline -1
git status --short
```

应看到类似：

```
6e5d252 perf+fix: 运行耗时 6m41s(超时) -> 76s ...
```

且 `git status --short` **无输出**（工作区干净）。这说明代码已打包好，可以直接推。

---

## 第 1 步：创建 GitHub 仓库（网页）

1. 打开 <https://github.com> 并登录（没有账号先注册，免费）。
2. 点右上角 **`+`** → **New repository**。
3. 按下面填写：

| 字段 | 填什么 | 为什么 |
|---|---|---|
| Repository name | `news-pipeline` | 会直接出现在订阅网址里 |
| Description | 可留空或写"每日资讯聚合推送" | 不影响功能 |
| **Public / Private** | **必须选 Public** | 免费版只有公开仓库才能用 GitHub Pages 免费托管 + Actions 免费额度 |
| Add a README file | **不勾选** | 本地已有 README.md，勾选会冲突 |
| Add .gitignore | **不勾选** | 本地已有 |
| Choose a license | **不勾选** | 不影响功能，勾了反而多一次冲突处理 |

4. 点 **Create repository**。
5. 建好后页面会显示一个仓库地址，形如：
   `https://github.com/你的用户名/news-pipeline.git`
   **把这行记下来**（下一步要用）。

> ⚠️ 页面此时会显示一堆"Quick setup"命令。**先别复制它的命令**——那些是给空仓库用的，
> 我们本地已经有提交历史，用第 3 步的命令即可。

---

## 第 2 步：生成访问令牌 Token（最容易卡住的一步）

**为什么需要它**：GitHub 自 2021 年 8 月起**不再接受用账号密码 push 代码**，
必须用「个人访问令牌（Personal Access Token）」或 SSH 密钥。这里用令牌，最简单。

### 2.1 进入令牌页面

1. 点右上角**头像** → **Settings**。
2. 滚动到页面最左侧菜单的**最底部** → **Developer settings**。
3. 点 **Personal access tokens** → **Tokens (classic)**。
4. 点右上角 **Generate new token** → 选 **Generate new token (classic)**。

### 2.2 配置令牌

| 字段 | 填什么 |
|---|---|
| Note（备注） | `news-pipeline`（随便写，方便日后辨认） |
| Expiration（有效期） | 选 **90 days** 或 **No expiration**（选 90 天更安全；过期后重新生成一个即可） |
| 权限勾选 | 只勾 **`repo`**（点一下会自动勾上它下面全部子项） |

> `repo` 权限的作用：允许推送代码、读写仓库内容。够用了，不要多勾。

5. 滚到页面底部点 **Generate token**。

### 2.3 保存令牌（关键）

生成后会显示一串以 `ghp_` 开头的字符串，**这是唯一一次显示机会**，刷新页面就再也看不到了。

**立刻复制到本地一个临时文本文件里**（推完就可以删掉）。

> 如果忘了复制：只能删掉这个令牌重新生成一个，不麻烦，别慌。

---

## 第 3 步：推送代码

在本机终端执行（把 `<你的用户名>` 换成真实用户名）：

```bash
cd /e/yang_news/news-pipeline
git remote add origin https://github.com/<你的用户名>/news-pipeline.git
git branch -M main
git push -u origin main
```

### 认证时怎么填

执行 `git push` 后，Windows 通常会弹出 **Git Credential Manager 登录窗口**：

- **Username（用户名）**：填你的 **GitHub 用户名**（不是邮箱）
- **Password（密码）**：**粘贴刚才那串 `ghp_` 开头的令牌**（不是你的 GitHub 登录密码）

如果弹的是浏览器登录页，按提示授权即可。

### 成功标志

看到类似输出即成功：

```
Enumerating objects: 30, done.
...
To https://github.com/xxx/news-pipeline.git
 * [new branch]      main -> main
```

此时刷新 GitHub 仓库页面，应能看到 11 个文件（`digester.py`、`README.md`、`docs/`、`output/` 等）。

### 如果 push 失败，对照这张表

| 报错信息 | 原因 | 解决办法 |
|---|---|---|
| `Authentication failed` / `Invalid username or password` | 密码栏填了登录密码，或令牌复制不完整/已过期 | 密码栏必须填 `ghp_` 令牌；注意复制时别带空格 |
| `remote: Repository not found` | 用户名写错，或仓库是 Private 而令牌没有 repo 权限 | 核对用户名拼写；确认令牌勾了 `repo` |
| `Failed to connect to github.com port 443: Timed out` | 国内网络连 GitHub 不稳定 | 重试几次；或开启代理后重试；或改用 SSH（见文末"备选方案"） |
| `fatal: remote origin already exists` | 之前加过远端 | 先执行 `git remote remove origin`，再重新 add |
| `Updates were rejected` | 建仓库时勾选了 README/gitignore，本地与远端历史不一致 | 删掉仓库重建（这次别勾那三项），或执行 `git pull --rebase origin main` 后再 push |

---

## 第 4 步：开启 Actions 写权限（**不做这步每日推送会失效**）

**为什么**：GitHub 出于安全，默认不给工作流写仓库的权限。
而我们的管道每天跑完要把新日报和 feed **写回仓库**，没有写权限就会静默失败——
表现是"Actions 显示成功，但 feed 永远不更新"。

操作路径：

1. 打开仓库页面 → 顶部 **Settings** 标签。
2. 左侧菜单 **Actions** → **General**。
3. 滚到页面底部 **Workflow permissions** 区域。
4. 选择 **Read and write permissions**（默认是 Read only）。
5. 点 **Save**。

---

## 第 5 步：开启 Pages（生成手机订阅地址）

1. 仓库页面 → **Settings** → 左侧 **Pages**。
2. **Build and deployment** → **Source** 选 **Deploy from a branch**。
3. **Branch** 选 `main`，**目录选 `/docs`**（这一步很多人漏，选 `/root` 会导致 404）。
4. 点 **Save**。

等待 **1～3 分钟**（首次部署可能到 10 分钟），页面顶部会出现：

```
Your site is live at https://<你的用户名>.github.io/news-pipeline/
```

### 你的订阅地址就是：

```
https://<你的用户名>.github.io/news-pipeline/feed.xml
```

可以先把这个地址粘到电脑浏览器打开验证——能看到 XML 源码（一堆 `<item>` 标签）就说明成功了。

---

## 第 6 步：手动触发一次，验证全流程

不用等到明天 8 点，现在就能验证：

1. 仓库页面 → 顶部 **Actions** 标签。
2. 左侧工作流列表点 **Daily News Digest**（或 `daily.yml` 里定义的名称）。
3. 右侧点 **Run workflow** → 再点 **Run workflow**。
4. 等 1～3 分钟，会出现一条运行记录。点进去能看到运行日志（含 `[STAT]` 统计行）。
5. 运行结束后刷新仓库，应看到 `output/` 下多出当天日期的 md、`docs/feed.xml` 更新时间变化。

> 如果这一步失败，点进运行记录看红色步骤的报错日志，对照文末"常见故障表"。

### 验收清单（手动触发后逐条核对）

| # | 检查项 | 期望结果 |
|---|---|---|
| 1 | 运行结果 | 绿色 ✅，日志末行有 `[OK] 生成完成` 与 `[OK] RSS` |
| 2 | `[STAT]` 统计行 | `AI=10 开源=10 Docker=8 宏观=8 指数=8 板块=40 黄金=Y 518880=Y QDII净值=6` |
| 3 | 仓库变动 | `output/YYYY-MM-DD.md` 新增、`docs/feed.xml` 更新、`state/*.json` 更新 |
| 4 | 日报内容 | 各栏不为空；开源/Docker 的「简介」全部为中文，无英文残留 |
| 5 | 订阅地址 | 浏览器打开 feed.xml 能看到 38 个 `<item>` |

**关于栏位为空的处理（health_check 护栏）**：

- AI / 开源 / Docker 任一为空 → 判定数据源异常，进程以**退出码 2** 结束。
  效果：Actions 该次运行标红、**不会提交残缺日报**、去重历史也不会被消费，可直接原样重跑。
- 宏观 / 指数 / 估值 / 板块 / QDII 为空 → 仅打印 `::warning::` 告警，仍正常出日报
  （这几栏依赖外部行情接口，偶发抖动不代表整体失败）。

> 云端与本地的两点差异，属正常现象：
> ① 云端 TZ=UTC，`cron 0 0 * * *` 触发时 UTC 日期与北京当天日期一致，日报文件名不会错位；
> ② `translate.googleapis.com` 在本机沙箱不可达（走 MyMemory），云端通常两者都可达，译文质量只会更好。

---

## 第 7 步：手机订阅

在手机 RSS 阅读器里添加订阅，地址填：

```
https://<你的用户名>.github.io/news-pipeline/feed.xml
```

| 系统 | 推荐 App | 获取方式 |
|---|---|---|
| Android | **Feeder** | F-Droid 或 Google Play |
| Android | **Read You** | GitHub Releases 下载 APK |
| iOS | **NetNewsWire** | App Store 免费 |
| iOS | **Reeder** | App Store（付费） |

添加时 App 会自动抓取标题列表。之后每天早晨刷新，就能看到新一期。

---

## 关键机制说明（你需要知道的几件事）

### 1. 每天几点跑？

工作流里写的是 `cron: "0 0 * * *"`，这是 **UTC 时间 0 点 = 北京时间 08:00**。

但要注意：**GitHub Actions 的定时任务不保证准时**。官方明确说明在流量高峰时可能延迟，
实测常见延迟 5～30 分钟，极端情况可能更久。它保证的是"每天会跑一次"，不是"精确到 8:00:00"。

> 想改成其他时间？编辑 `.github/workflows/daily.yml` 里的 cron。
> 换算公式：**北京时间 − 8 = UTC**。例如想北京时间 7:30，就写 `30 23 * * *`（前一天 23:30 UTC）。

### 2. 日报存在哪？会占满吗？

每天生成的 `output/YYYY-MM-DD.md` 会 **commit 回 GitHub 仓库**，可以在网页上回看全部历史。

体积完全不用担心：
- 单份日报 ≈ 12 KB，`feed.xml` ≈ 20 KB
- GitHub 单文件上限 100 MB、单仓库推荐上限 1 GB
- **按每天 32 KB 算，一年约 12 MB**，跑 80 年才到上限

### 3. Actions 免费额度够吗？

够。免费账户每月 **2000 分钟** 运行额度（公开仓库更宽松，通常不计入）。
我们每天跑一次、耗时约 1～2 分钟，一个月约 45 分钟，占比不到 3%。

### 4. 一个隐藏坑：60 天无活动会停定时任务

如果仓库**连续 60 天没有任何提交或推送**，GitHub 会自动禁用该仓库的定时工作流。
解决办法：偶尔去 Actions 页面手动点一次 "Run workflow" 即可重新激活。
（我们每天都自动提交，正常不会触发这个问题。）

### 5. 后续改代码怎么更新？

```bash
cd /e/yang_news/news-pipeline
# ...修改代码...
git add -A
git commit -m "说明改了什么"
git push
```

推上去后，第二天 8 点自动用新代码跑。想立刻生效就去 Actions 手动触发一次。

---

## 常见故障排查表

| 现象 | 可能原因 | 排查/解决 |
|---|---|---|
| 订阅地址打不开、404 | Pages 目录选成了 `/root` | Settings → Pages 改成 `/docs` |
| feed 打开是旧的，好几天不更新 | 第 4 步的 Actions 写权限没开 | Settings → Actions → General → 设为 Read and write |
| Actions 运行失败（红叉） | 数据源临时不可达、或网络波动 | 点进运行记录看日志；多数情况第二天自动恢复 |
| 手机上能订阅但内容为空 | feed.xml 生成失败 | 先在电脑浏览器打开 feed.xml 看是否有 `<item>` |
| 定时任务到点没跑 | 仓库 60 天无活动被禁用 | 去 Actions 手动 Run workflow 激活 |
| 日报里某栏显示"暂不可达" | 对应数据源当次抓取失败 | 偶发网络问题，次日自动恢复；连续多天失败再排查 |
| push 时提示令牌过期 | 令牌设了有效期且已到期 | 重新生成一个令牌，或把本地凭据删掉重填 |

### Windows 上重新填写凭据（令牌填错时）

控制面板 → 凭据管理器 → Windows 凭据 → 找到 `git:https://github.com` → 删除。
下次 push 会重新弹窗要求填写。

---

## 备选方案：改用 SSH 推送（HTTPS 连不上时）

如果 HTTPS 因网络问题始终连不上，可改用 SSH（走 443 端口，通常更稳）：

```bash
# 1. 生成密钥（一路回车）
ssh-keygen -t ed25519 -C "你的邮箱"

# 2. 复制公钥内容
cat ~/.ssh/id_ed25519.pub

# 3. GitHub → Settings → SSH and GPG keys → New SSH key → 粘贴保存

# 4. 改用 SSH 地址推送
git remote set-url origin git@github.com:<你的用户名>/news-pipeline.git
git push -u origin main
```

---

## 附：整个系统的运行链路

```
每天 UTC 00:00（北京 08:00）
      ↓
GitHub Actions 启动一台临时云主机（Ubuntu + Python）
      ↓
执行 python digester.py
      ├─ 抓 AI 资讯 / 开源 / Docker（GitHub API）
      ├─ 抓 宏观要闻（新浪）
      ├─ 抓 指数·板块·黄金（东方财富）
      ├─ 抓 指数估值分位（蛋卷）
      ├─ 抓 QDII 净值（天天基金）
      ├─ 7 天去重 → 只发未发过的新内容
      └─ 英文简介译为中文
      ↓
生成 output/2026-09-06.md  +  docs/feed.xml
      ↓
自动 commit 回仓库（所以需要第 4 步的写权限）
      ↓
GitHub Pages 把 /docs 目录发布成网站
      ↓
手机 RSS 阅读器拉取 https://xxx.github.io/news-pipeline/feed.xml
      ↓
你在手机上看到当天日报
```

**全程不需要你的电脑开机**——所有抓取、生成、发布都在 GitHub 的云主机上完成。
