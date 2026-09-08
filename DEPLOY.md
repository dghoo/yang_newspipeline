# GitHub 部署全流程（零基础完整手册）

> 目标：把本地 `yang_newspipeline` 推到 GitHub，让它**每天北京时间 08:00 自动跑一次**，
> 生成当天日报 + 更新 RSS，并给你一个手机可直接订阅的地址。
> 全文按"先做什么 → 这条命令在干什么 → 怎么算成功 → 失败怎么办"组织。

---

## 第 0 步：确认本地状态（先看一眼，心里有底）

在本机终端（Git Bash）执行：

```bash
cd /e/yang_news/yang_newspipeline
git status          # 看工作区是否干净
git log --oneline -3  # 看最近的提交
git remote -v       # 看有没有配置远端（现在应该是空的）
```

| 命令 | 作用 |
|---|---|
| `cd /e/yang_news/yang_newspipeline` | 进入项目目录（后面的命令都在这里执行） |
| `git status` | 查看哪些文件被改过、有没有没提交的 |
| `git log --oneline -3` | 看最近 3 条提交，确认代码已在本地入版本库 |
| `git remote -v` | 查看已关联的远端仓库地址（**没输出 = 还没关联 GitHub，正是现在的状态**） |

期望结果：`git status` 显示 `nothing to commit, working tree clean`；`git remote -v` 无输出。

---

## 第 1 步：创建 GitHub 仓库（网页操作）

1. 登录 <https://github.com> → 右上角 **+** → **New repository**。
2. **Repository name** 填：`yang_newspipeline`（**必须和后面命令里的名字一致**）。
3. **Public**（公开）必选：私有仓库用不了免费的 GitHub Pages，RSS 地址也就打不开。
4. **这三个一律不勾**：`Add a README file`、`Add .gitignore`、`Choose a license`。
   - 原因：勾了会在远端产生一次初始提交，与你本地已有的提交历史冲突，push 会被拒绝（`Updates were rejected`）。
5. 点 **Create repository**。

创建后会看到一个"快速设置"页面，上面有仓库地址，形如：

```
https://github.com/<你的用户名>/yang_newspipeline.git
```

把这个地址记住（下一步要用）。

---

## 第 2 步：生成访问令牌 Token（最容易卡住的一步）

**为什么需要**：2021 年 8 月起 GitHub 不再接受"账号密码"推送代码，必须改用 **Personal Access Token（PAT）**。
这个令牌就是你 push 时填在"密码"栏里的东西。

### 生成步骤

1. 右上角头像 → **Settings** → 左侧最下方 **Developer settings** → **Personal access tokens** → **Tokens (classic)**。
2. 点 **Generate new token** → 选 **Generate new token (classic)**。
3. 按下面填：

| 字段 | 怎么填 | 为什么 |
|---|---|---|
| Note（备注） | 随便，如 `yang_newspipeline push` | 只是给你自己认名字 |
| Expiration（有效期） | 选 **No expiration**（或 90 天） | 过期后 push 会突然失败 |
| `repo` | ✅ **勾选** | 这是唯一必选项，包含读写仓库代码的所有权限 |
| 其余全部 | 不勾 | 越少越安全 |

4. 拉到最底点 **Generate token**。
5. **立刻复制那串以 `ghp_` 开头的字符串并保存到本地**（关掉页面就再也看不到了，只能重新生成）。

---

## 第 3 步：推送代码（本机终端）

把 `<你的用户名>` 换成真实用户名，三条命令逐条执行：

```bash
cd /e/yang_news/yang_newspipeline
git remote add origin https://github.com/<你的用户名>/yang_newspipeline.git
git branch -M main
git push -u origin main
```

### 每条命令在做什么

| 命令 | 作用 |
|---|---|
| `git remote add origin <地址>` | 给远端仓库起个别名叫 `origin`，以后用 `origin` 就代表那个 GitHub 地址。**只是本地配置，不会改动 GitHub 上的任何东西** |
| `git branch -M main` | 把当前分支重命名为 `main`（GitHub 默认分支名是 `main`，本地若是 `master` 需统一） |
| `git push -u origin main` | 把本地 `main` 分支的**所有提交**上传到 GitHub；`-u` 表示记住对应关系，以后只需输入 `git push` |

### 认证时怎么填

执行 `git push` 后，Windows 通常弹出 **Git Credential Manager** 登录窗口：

- **Username**：你的 **GitHub 用户名**（不是邮箱）
- **Password**：**粘贴 `ghp_` 开头的令牌**（不是 GitHub 登录密码）

如果弹的是浏览器页面，按提示授权即可。

### 成功标志

```
Enumerating objects: 30, done.
Writing objects: 100% (30/30), done.
To https://github.com/xxx/yang_newspipeline.git
 * [new branch]      main -> main
```

刷新仓库页面，应能看到：`digester.py`、`README.md`、`DEPLOY.md`、`config.yaml`、`docs/feed.xml`、`output/`（含 2026-09-02/05/08 三份日报）、`state/`（两个 json）、`tools/`。

### push 失败对照表

| 报错 | 原因 | 解决 |
|---|---|---|
| `Authentication failed` | 密码栏填了登录密码，或令牌复制不完整 | 密码栏必须填 `ghp_` 令牌，别带空格 |
| `remote: Repository not found` | 用户名拼错，或令牌没勾 `repo` | 核对用户名；重新生成令牌并勾 `repo` |
| `Failed to connect ... 443: Timed out` | 网络连 GitHub 不稳 | 重试几次，或开代理后重试 |
| `fatal: remote origin already exists` | 之前加过远端 | 先 `git remote remove origin` 再 add |
| `Updates were rejected` | 建仓库时勾了 README/gitignore | 删库重建（这次别勾），或 `git pull --rebase origin main` 后再 push |

### 令牌填错了怎么改（Windows）

控制面板 → 凭据管理器 → Windows 凭据 → 找到 `git:https://github.com` → 编辑/删除，下次 push 会重新问。

---

## 第 4 步：开启 Actions 写权限（**不做这步每天都不会更新**）

**为什么**：GitHub 出于安全默认不给工作流写仓库的权限。我们的管道每天跑完要把新日报和 feed **写回仓库**，
没写权限的表现是"Actions 显示成功，但 feed 永远不更新"——很隐蔽。

路径：仓库页面 → **Settings** → 左侧 **Actions** → **General** → 页面底部 **Workflow permissions** →
选 **Read and write permissions** → **Save**。

---

## 第 5 步：开启 Pages（生成手机订阅地址）

**为什么**：手机 RSS 阅读器需要能公开访问的网址，Pages 就是把仓库里的 `docs/` 目录变成一个静态网站。

路径：仓库页面 → **Settings** → 左侧 **Pages** → **Build and deployment** → Source 选 **Deploy from a branch** →
Branch 选 **`main`**、目录选 **`/docs`** → **Save**。

约 1～2 分钟后，订阅地址生效：

```
https://<你的用户名>.github.io/yang_newspipeline/feed.xml
```

> 仓库名如果叫别的，把 `yang_newspipeline` 换成你的仓库名即可。

---

## 第 6 步：手动触发一次，验证全流程

不用等到明天 8 点：

1. 仓库页面 → **Actions** 标签。
2. 左侧点 **daily-news-digest**。
3. 右侧 **Run workflow** → 再点 **Run workflow**。
4. 等 1～3 分钟，点进运行记录可看到日志（含 `[STAT]` 统计行）。

### 验收清单（逐条核对）

| # | 检查项 | 期望结果 |
|---|---|---|
| 1 | 运行结果 | 绿色 ✅，日志有 `[OK] 生成完成` 与 `[OK] RSS` |
| 2 | `[STAT]` 行 | `AI=10 开源=10 Docker=8 宏观=8 指数=9 板块=40 黄金=Y 518880=Y QDII净值=6` |
| 3 | 仓库变动 | `output/YYYY-MM-DD.md` 新增、`docs/feed.xml` 更新、`state/*.json` 更新 |
| 4 | 日报内容 | 各栏不为空；开源/Docker 简介为中文 |
| 5 | 订阅地址 | 浏览器打开 feed.xml 能看到 38 个 `<item>` |

**关于栏位为空（health_check 护栏）**：

- AI / 开源 / Docker 任一为空 → 进程**退出码 2**，Actions 标红，**不提交残缺日报、去重历史不被消费**，可原样重跑。
- 宏观 / 指数 / 估值 / 板块 / QDII 为空 → 仅 `::warning::` 告警，正常出日报。

**云端与本地的两个差异（正常现象）**：

1. 云端 TZ=UTC，`cron 0 0 * * *`（UTC 00:00 = 北京 08:00）触发时 UTC 日期与北京当天一致，日报文件名不会错位。
2. `translate.googleapis.com` 在本机沙箱不可达（走 MyMemory），云端通常可达，译文质量只会更好。

---

## 第 7 步：手机订阅

手机 RSS 阅读器（如 Feedly、Inoreader、Reeder、ReadYou）里添加订阅源，地址填：

```
https://<你的用户名>.github.io/yang_newspipeline/feed.xml
```

**验证是否订阅成功**：添加后应立刻拉取到约 38 条；第二天 8 点后刷新，应出现新一批内容。
若一直空白：检查 Pages 是否已生效（浏览器能打开就行），以及仓库是否 Public。

---

## 第 8 步：确认"每天自动跑"真的生效

| 检查项 | 方法 |
|---|---|
| 定时任务存在 | Actions 页面顶部会显示 "This workflow has a schedule" |
| 下次运行时间 | 把鼠标悬停在 schedule 上，显示 `0 0 * * *`（UTC）= 北京 08:00 |
| 第二天确认 | 次日 8 点后看 Actions 历史是否多了一条，且仓库有新 commit（`daily 2026-09-09` 之类） |
| **60 天停用坑** | 仓库连续 60 天无任何提交/活动，GitHub 会**自动停掉定时任务**。表现是突然不更新了。解决：随便 push 一次即可自动恢复 |

---

## 第 9 步：以后改代码怎么更新

```bash
cd /e/yang_news/yang_newspipeline
# ...修改代码...
git add -A
git commit -m "说明改了什么"
git push
```

| 命令 | 作用 |
|---|---|
| `git add -A` | 把所有改动（新增/修改/删除）加入待提交列表 |
| `git commit -m "..."` | 在本地生成一条提交记录 |
| `git push` | 上传到 GitHub（因为第 3 步用了 `-u`，这里不用再写 origin main） |

推送后云端下次运行自动用新代码，无需其它操作。

---

## 第 10 步：常见问题与自查

| 现象 | 原因 | 处理 |
|---|---|---|
| Actions 成功但 feed 不更新 | 第 4 步写权限没开 | Settings → Actions → General → Read and write |
| 订阅地址 404 | Pages 没开 / 目录没选 `/docs` / 仓库是 Private | 重做第 5 步；确认 Public |
| Actions 日志里 `[FATAL] 核心栏为空` | 当天数据源异常 | 直接 **Re-run** 该次运行即可（去重历史未被消费） |
| 日报里某指数点位/涨跌幅是 `—` | 该指数行情代码失效 | 日志会打印 `[IDX] 无行情返回：<名称> (<secid>)`；按提示去东财搜索正确 QuoteID 后改 `fetch_indices()` 的映射 |
| 项目简介是英文 | 翻译源（MyMemory）当天不可用 | 已做熔断+重试，属外部故障；恢复后执行 `python tools/retranslate_md.py output/YYYY-MM-DD.md` 回填中文 |
| push 报认证失败 | 令牌过期或被改 | 重做第 2 步；Windows 需清凭据管理器里的旧条目 |
| 每天跑两次或时间不对 | cron 用 UTC | 想改时间就改 `.github/workflows/daily.yml` 里的 cron（减 8 得北京时间） |

---

## 附：整个系统的运行链路

```
GitHub Actions（每天 UTC 00:00 = 北京 08:00）
        │
        ├─ 抓取：aihot(AI) / GitHub Search API(开源·Docker) / 东财(指数·板块·黄金) / 蛋卷(估值) / 天天基金(QDII) / 新浪(宏观)
        ├─ 两道防重：7 天已发历史 + 只发未发过的较新项（无新增则扩搜，不留空、不重发）
        ├─ 翻译：英文项目简介 → 中文（MyMemory 主、Google 备，均失败则保留纯净原文）
        └─ 产出：output/YYYY-MM-DD.md（可读日报）+ docs/feed.xml（RSS）
                │
                ├─ 自动 commit 回仓库（含 state/ 去重历史与翻译缓存）
                └─ GitHub Pages 发布 /docs → 手机 RSS 阅读器订阅
```

## 附：本地两个辅助脚本

```bash
python tools/dryrun_tomorrow.py                    # 只读演练：模拟明日各栏可发量，确认不会空栏
python tools/retranslate_md.py output/2026-09-08.md  # 翻译源恢复后，回填指定日报里仍是英文的简介
```
