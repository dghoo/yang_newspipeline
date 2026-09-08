#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
个人资讯聚合与推送 · 真实管道 (digester.py)
============================================
数据源（均为真实 HTTP 抓取，非手写样例）：
  - AI 资讯      : aihot.virxact.com /api/v1/items（带游标分页，7 天窗口，约 80 条池）
  - 优质开源      : github.com/trending（daily/weekly/monthly + 多语言变体）；英文简介经 MyMemory 翻译为中文
  - Docker/自托管 : 从 trending 池按关键词挖掘
  - 财经·指数实时 : 东方财富 push2 行情
  - 财经·行业板块 : 东方财富 push2delay 行业板块实时涨跌
  - 财经·黄金     : 东方财富 COMEX 微型黄金(MGC) 代理 + 518880 真实净值
  - 财经·QDII净值 : 天天基金 api.fund.eastmoney.com（真实 T+2 净值）
  - 财经·宏观     : 新浪滚动 API（事件驱动 + 深翻页扩展搜索）

两道防重机制（落地前设计核心）：
  机制1 · 跨日去重：state/sent_history.json 持久化已发展示项的键（URL/标题哈希），
        7 天内发过的不再发。每次仅标记"本次实际展示"的项，保证未展示的新项不被误消费。
  机制2 · 分层填充（不留空、绝不重发）：
        - AI/开源/容器：取"大池子"（AI=7天全量API；开源=日/周/月+多语言 trending），
          只发 7 天窗口内"从未发过"的较新资讯；当日无新增也能从池里挖出未发过的，不留空。
        - 宏观：事件驱动（有新数据才出），无新则深翻页 + 宽关键词扩展搜索未发项，不强制每日。
        - 估值：每日完整估值表。
        - 板块/黄金/QDII：每日实采，天然新鲜。
        所有文本栏均"只发未发过的较新资讯"，从设计上消除连日相似，而非靠运气。

输出：
  - docs/feed.xml   : RSS 2.0（供手机 RSS 阅读器订阅）
  - output/YYYY-MM-DD.md : 可读资讯日报

纯标准库实现，无第三方依赖，本地与 GitHub Actions 均可直接运行。
"""

import urllib.request, urllib.parse, ssl, re, json, html, os, sys, time, datetime, difflib

ROOT = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(ROOT, "state")
DOC_DIR = os.path.join(ROOT, "docs")
OUT_DIR = os.path.join(ROOT, "output")
HISTORY_FILE = os.path.join(STATE_DIR, "sent_history.json")

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

# 各文本栏单期展示上限（避免灌爆，且保证可读性）
AI_CAP, OS_CAP, DOCKER_CAP, MACRO_CAP = 10, 10, 8, 8
DEDUP_WINDOW = 7  # 天

# ---- AI 栏改为 4 个子项展示 ----
# 基础配额：各子项优先取到的条数；某子项供给不足时，其额度转给仍有余量的其他子项。
AI_QUOTA = {"行业资讯": 4, "模型发布": 4, "大模型测评": 3, "开源模型": 3}
AI_TOTAL = sum(AI_QUOTA.values())          # 目标总量；不足时按供给收缩，不跨类填充
# 子项展示顺序：(内部键, 日报小标题, 统计行短名)
AI_SECTIONS = (("行业资讯", "1.1 AI 行业资讯", "行业"),
               ("模型发布", "1.2 模型发布动态", "模型"),
               ("大模型测评", "1.3 大模型测评", "测评"),
               ("开源模型", "1.4 开源模型推荐", "开源模型"))

# 大模型测评：数据源无独立分类，靠强信号词跨类抽取（登顶/榜单/SOTA/Arena/基准等）
AI_BENCH_PAT = re.compile(
    r"(登顶|榜首|榜单|Arena|SOTA|ARC-AGI|MMLU|GPQA|SWE-bench|跑分|"
    r"评测中居首|超越人类|刷新纪录|世界第一|"
    # "基准"单独出现太宽泛（如"研究基准的训练中智能体"并非测评），须与结果词共现
    r"基准(?:测试|成绩|得分|表现|数据|结果|排名|分数|全面))", re.I)

# 同事件聚类用的模型实体（两条标题命中同一实体 → 视为同一事件的不同报道）
AI_MODEL_ENT = re.compile(
    r"(GPT[\s\-]?\d[\w.\-]*|Claude[\s\w.]*|Gemini[\s\w.]*|Qwen[\w.\-]*|"
    r"DeepSeek[\w.\-]*|GLM[\w.\-]*|Llama[\w.\-]*|Grok[\w.\-]*|K2[\w.\-]*|"
    r"MiniCPM[\w.\-]*|LongCat[\w.\-]*|Fable[\w.]*|Astra|ARC-AGI[\w.\-]*|"
    r"MMLU|GPQA|SWE-bench)", re.I)

# ---- 开源模型推荐（HuggingFace 镜像）----
# huggingface.co 主站在部分网络环境 TLS 不可达（实测握手超时），hf-mirror.com 国内镜像可用（实测 1.2s）。
HF_BASE = "https://hf-mirror.com"
HF_NEW_DAYS = 30        # 只推近 30 天新建的模型
# 二创/量化/去审查模型对使用者无参考价值，直接过滤
HF_BAD_PAT = re.compile(r"(uncensored|abliterat|obliterat|nsfw|(?:^|[-_/])erp(?:[-_/]|$)|"
                        r"roleplay|gguf|mlx|awq|gptq|imatrix|quant|"
                        r"text-to-speech|(?:^|[-_/])tts(?:[-_/]|$))", re.I)
HF_PARAM_PAT = re.compile(r"[-_](\d+(?:\.\d+)?)\s*[Bb](?:[-_]|$)")

# 宏观关键词（主）：命中才算"宏观事件"
MACRO_KEYWORDS = ["降准", "降息", "社融", "CPI", "PPI", "GDP", "央行", "货币政策",
                  "美联储", "逆回购", "MLF", "流动性", "国债", "利率", "宏观", "经济数据", "财联社"]
# 宏观扩展（兜底搜索用，更宽，尽量挖到未发过的较新宏观资讯）
MACRO_KEYWORDS_BROAD = MACRO_KEYWORDS + ["经济", "政策", "数据", "汇率", "美债", "A股",
                                         "证监会", "财政部", "统计局", "PMI", "出口", "进口",
                                         "就业", "通胀", "衰退"]


# ---------- 网络工具 ----------
def get_text(u, timeout=10, n=4, ref=None, headers=None, deadline=None):
    """抓取文本。

    timeout  : 单次 socket 操作超时（connect / 单次 read）
    deadline : 整个请求的总时间预算（秒），None 表示不限

    为什么需要 deadline：urlopen 的 timeout 只约束单次 socket 读写，而读取大页面会
    分多次 read，累计远超预期——实测 github trending 页设 timeout=8 仍耗时 41s。
    deadline 通过分块读 + 累计计时来真正中止慢连接。
    """
    last = None
    for _ in range(n):
        t0 = time.time()
        try:
            h = {"User-Agent": UA}
            if ref:
                h["Referer"] = ref
            if headers:
                h.update(headers)
            req = urllib.request.Request(u, headers=h)
            resp = urllib.request.urlopen(req, timeout=timeout, context=ctx)
            if deadline is None:
                return resp.read().decode("utf-8", "ignore")
            chunks = []
            while True:
                if time.time() - t0 > deadline:
                    raise TimeoutError(f"超过总时间预算 {deadline}s，放弃该请求")
                chunk = resp.read(65536)
                if not chunk:
                    break
                chunks.append(chunk)
            return b"".join(chunks).decode("utf-8", "ignore")
        except Exception as e:
            last = e
            # 预算已耗尽则不再重试，避免慢源把运行拖垮
            if deadline is not None and (time.time() - t0) > deadline:
                break
            time.sleep(1.5)
    raise last


def get_json(u, timeout=20, n=4, ref=None, headers=None):
    return json.loads(get_text(u, timeout, n, ref, headers))


def strip_tags(s):
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


# ---------- 1. AI 资讯（aihot /api/v1/items，游标分页全量池）----------
def fetch_ai_api(max_pages=5):
    """抓取 aihot 7 天窗口全量（默认约 80 条），作为"未发过较新资讯"的大池子。"""
    out = []
    try:
        url = "https://aihot.virxact.com/api/v1/items"
        for _ in range(max_pages):
            d = get_json(url, ref="https://aihot.virxact.com/")
            for it in d.get("items", []):
                sid = it.get("id", "")
                if not sid:
                    continue
                links = it.get("links", {}) or {}
                url_ai = links.get("aihot") or ("https://aihot.virxact.com/items/" + sid)
                src = it.get("source")
                srcname = src.get("name") if isinstance(src, dict) else (src or "aihot 聚合")
                title = (it.get("title") or "").strip()
                if not title:
                    continue
                out.append({"id": sid, "title": title,
                            "summary": (it.get("summary") or "").strip(),
                            "url": url_ai, "source": srcname,
                            "publishedAt": it.get("publishedAt", ""),
                            # 数据源自带分类与质量分：category 用于子项归类，score 用于同事件去重选代表
                            "category": it.get("category") or "",
                            "score": it.get("score") or 0})
            pg = d.get("page", {})
            nc = pg.get("nextCursor")
            if not nc or not pg.get("hasMore"):
                break
            url = "https://aihot.virxact.com/api/v1/items?cursor=" + nc
    except Exception as e:
        print("[AI] fetch failed:", e, file=sys.stderr)
    return out


# ---------- 1b. 开源模型推荐（HuggingFace 镜像 hf-mirror.com）----------
def fmt_num(n):
    """下载量/点赞数格式化：251611 -> 25.2万；7216 -> 7216。"""
    try:
        n = int(n)
    except Exception:
        return str(n)
    return f"{n / 10000:.1f}万" if n >= 10000 else str(n)


def fetch_hf_models(limit=8, days=HF_NEW_DAYS):
    """抓取近期新发布的开源文本生成模型。

    HuggingFace 主站 huggingface.co 在部分网络环境 TLS 握手超时不可达（实测），
    因此走国内镜像 hf-mirror.com（实测 1.2s 可达）。镜像不可用则返回空列表，
    上层降级为该栏"今日无新增"，不影响其他栏目。
    """
    out = []
    try:
        u = (HF_BASE + "/api/models?sort=likes7d&direction=-1&limit=60"
             "&filter=text-generation")
        d = get_json(u)
        if not isinstance(d, list):
            return []
        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
        for m in d:
            mid = m.get("modelId") or m.get("id") or ""
            if not mid or m.get("private"):
                continue
            tags = m.get("tags") or []
            # 去审查 / 量化二创模型对使用者无参考价值，直接剔除
            if HF_BAD_PAT.search((mid + " " + " ".join(tags)).lower()):
                continue
            ca = m.get("createdAt") or ""
            if ca:                              # 只要近期新发布的
                try:
                    if datetime.datetime.fromisoformat(ca.replace("Z", "+00:00")) < cutoff:
                        continue
                except Exception:
                    pass
            lic = ""
            for t in tags:
                if t.startswith("license:"):
                    lic = t.split(":", 1)[1]
                    break
            pm = HF_PARAM_PAT.search(mid)
            out.append({"id": mid, "likes": m.get("likes") or 0,
                        "downloads": m.get("downloads") or 0,
                        "license": lic, "createdAt": ca,
                        "param": (pm.group(1) + "B") if pm else "",
                        "url": HF_BASE + "/" + mid})
        out.sort(key=lambda x: x["likes"], reverse=True)
        return out[:limit]
    except Exception as e:
        print("[HF] 开源模型抓取失败，该栏降级为今日无新增:", e, file=sys.stderr)
        return out


# ---------- 1c. 开源模型「简介」：模型卡 README 首段（方案B，失败降级元数据 方案A）----------
def _first_readme_para(md):
    """从模型卡 README 原文提取首段有效正文（跳过 YAML frontmatter / 标题 / 图片 / 表格 / badge）。"""
    md = (md or "").strip()
    if not md:
        return ""
    # 去 YAML frontmatter（文件开头的 --- ... --- 块）
    if md.startswith("---"):
        parts = md.split("\n")
        for i in range(1, len(parts)):
            if parts[i].strip() == "---":
                md = "\n".join(parts[i + 1:])
                break
    for p in re.split(r"\n\s*\n", md):
        p = p.strip()
        if not p:
            continue
        if p.startswith("#") or p.startswith("!") or p.startswith("<") or p.startswith("|"):
            continue
        if re.match(r"^\[!\[[^\]]*\]\([^)]*\)\]\([^)]*\)", p):   # 整行 badge
            continue
        # 去掉行内图片/链接标记，保留文字；清掉 md 强调符号
        txt = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", p)
        txt = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", txt)
        txt = re.sub(r"[*_`>]", "", txt).strip()
        # 去掉 GitHub 警示块标记（> [!NOTE] 等）与行首残缺括号碎片（[。Note] / [!NOTE]）
        txt = re.sub(r"^[\t >]*\[[!！]?[A-Za-z·。．.]{0,20}\]\s*", "", txt, flags=re.I)
        txt = txt.strip(" >\t")
        if len(txt) < 20:
            continue
        return txt
    return ""


def fetch_hf_model_intro(mid):
    """抓取 HuggingFace 模型卡 README 首段作为简介候选（英文为主）。

    走 hf-mirror.com 镜像；main 分支取不到再试 master。失败或空返回 ''，
    上层会自动降级为元数据合成（方案A），保证该栏始终有中文简介、且运行不被拖垮。
    """
    for branch in ("main", "master"):
        try:
            u = f"{HF_BASE}/{mid}/raw/{branch}/README.md"
            txt = get_text(u, timeout=10, n=1, deadline=10)
        except Exception as e:
            txt = ""
            print(f"[HF] {mid} README({branch}) 抓取失败，降级方案A:", e, file=sys.stderr)
        para = _first_readme_para(txt) if txt else ""
        if para:
            return para[:600]          # 限长，避免超大卡片拖慢后续翻译
    return ""


def hf_intro_fallback(m):
    """方案A：用结构化元数据合成一句话中文简介（零额外请求、绝不会因网络失败）。"""
    bits = []
    if m.get("license"):
        bits.append(f"许可证 {m['license']}")
    if m.get("param"):
        bits.append(f"参数规模 {m['param']}")
    bits.append(f"近 7 天点赞 {fmt_num(m['likes'])}")
    bits.append(f"下载 {fmt_num(m['downloads'])}")
    return "文本生成类开源模型 · " + " · ".join(bits)


# ---------- 2. GitHub Trending（日/周/月 + 多语言变体池）----------
def fetch_trending(since="daily", lang=None, timeout=8, n=1, deadline=12):
    """抓取 GitHub Trending HTML。

    注意：github.com 的 HTML 页面在部分网络环境（含沙箱/国内网络）会极慢或直接
    IncompleteRead（实测 daily 页曾耗时 86s 后失败）。因此默认 timeout=8、n=1
    —— 只做"能拿到就补充今日新增 stars，拿不到就快速放弃"，绝不拖垮整体运行。
    """
    repos = []
    try:
        params = []
        if since and since != "daily":
            params.append("since=" + since)
        if lang:
            params.append("spoken_language_code=" + lang)
        q = ("?" + "&".join(params)) if params else ""
        h = get_text("https://github.com/trending" + q, timeout=timeout, n=n, deadline=deadline)
        arts = re.findall(r'<article class="Box-row">(.*?)</article>', h, re.S)
        for a in arts:
            rm = re.search(r'class="h3 lh-condensed">\s*<a [^>]*href="/([^"]+)"', a)
            if not rm:
                continue
            repo = rm.group(1).strip("/")
            if repo.count("/") != 1:
                continue
            dm = re.search(r'<p class="col-9 color-fg-muted my-1 pr-4">(.*?)</p>', a, re.S)
            desc = strip_tags(dm.group(1)) if dm else ""
            lm = re.search(r'<span itemprop="programmingLanguage">([^<]+)</span>', a)
            lang0 = lm.group(1) if lm else ""
            sm = re.search(r'href="/[^"]+/stargazers"[^>]*>.*?([\d,]+)\s*</a>', a, re.S)
            stars = sm.group(1) if sm else ""
            tm = re.search(r'([\d,]+)\s*stars today', a)
            today = tm.group(1) if tm else ""
            repos.append({"repo": repo, "desc": desc, "lang": lang0,
                          "stars": stars, "today": today, "url": "https://github.com/" + repo})
    except Exception as e:
        print("[OS] fetch failed:", e, file=sys.stderr)
    return repos


DOCKER_KEYWORDS = [r'docker', r'self[- ]?host', r'selfhosted', r'container', r'homelab',
                   r'\bnas\b', r'proxy', r'\bvpn\b', r'dashboard', r'monitor', r'caddy',
                   r'traefik', r'portainer', r'immich', r'vaultwarden', r'uptime', r'paperless']


def split_docker(repos):
    picked = []
    for r in repos:
        blob = (r["repo"] + " " + r["desc"]).lower()
        if any(re.search(k, blob) for k in DOCKER_KEYWORDS):
            picked.append(r)
    return picked


# ---------- 2b. GitHub Topics（容器/自托管专属真实源，实时轮换）----------
def fetch_topic_repos(topic):
    """抓取 github.com/topics/<topic> 页，返回该话题下的真实仓库池（天然是容器/自托管类）。"""
    repos = []
    try:
        h = get_text("https://github.com/topics/" + topic)
        parts = re.split(r'href="/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)"[^>]*class="Link text-bold wb-break-word"', h)
        for i in range(1, len(parts), 2):
            repo = parts[i].strip("/")
            if repo.count("/") != 1:
                continue
            body = parts[i + 1] if i + 1 < len(parts) else ""
            dm = re.search(r'<p class=" color-fg-muted mb-0 ">(.*?)</p>', body, re.S)
            desc = strip_tags(dm.group(1)) if dm else ""
            repos.append({"repo": repo, "desc": desc, "lang": "", "stars": "", "today": "",
                          "url": "https://github.com/" + repo})
    except Exception as e:
        print(f"[TOPIC {topic}] fetch failed:", e, file=sys.stderr)
    return repos


# ---------- 3. 财经·指数实时 ----------
def fetch_indices():
    secids = {
        "沪深300": "1.000300", "中证500": "1.000905", "创业板指": "0.399006",
        # 红利低波 = 中证红利低波动指数，东财 QuoteID 为 2.H30269
        # （旧值 1.930998 无效：接口不返回该条目，导致该行点位/涨跌幅长期空白）
        "科创50": "1.000688", "中证红利": "1.000922", "红利低波": "2.H30269",
        "恒生指数": "100.HSI", "纳斯达克100": "100.NDX", "标普500": "100.SPX",
    }
    out = []
    try:
        s = ",".join(secids.values())
        u = f"https://push2delay.eastmoney.com/api/qt/ulist.np/get?fltt=2&invt=2&fields=f12,f13,f14,f2,f3&secids={s}"
        d = get_json(u)
        name_by_sec = {v: k for k, v in secids.items()}
        got = set()
        for x in d["data"]["diff"]:
            sec = x.get("f12")
            name = x.get("f14") or name_by_sec.get(sec, "")
            val, chg = x.get("f2"), x.get("f3")
            got.add(f"{x.get('f13')}.{sec}")      # 以 secid 为准，避免名称差异造成误报
            if val in (None, "-", "--"):
                continue
            out.append({"name": name, "value": val, "chg": chg})
        # 行情缺失不再静默：明确指出是哪个指数取不到（多为 secid 失效或盘中停牌）
        for nm, sec in secids.items():
            if sec not in got:
                print(f"[IDX] 无行情返回：{nm} ({sec})，该行点位/涨跌幅将显示为 —", file=sys.stderr)
    except Exception as e:
        print("[IDX] fetch failed:", e, file=sys.stderr)
    return out


# ---------- 4. 财经·行业板块实时 ----------
def fetch_sectors():
    out = []
    try:
        u = "https://push2delay.eastmoney.com/api/qt/clist/get?pn=1&pz=40&fs=m:90+t:2&fields=f12,f14,f2,f3&fltt=2&po=1"
        d = get_json(u)
        diff = d["data"]["diff"]
        items = list(diff.values()) if isinstance(diff, dict) else diff
        for x in items:
            nm = x.get("f14")
            if nm is None:
                continue
            out.append({"name": nm, "chg": x.get("f3")})
    except Exception as e:
        print("[SEC] fetch failed:", e, file=sys.stderr)
    return out


# ---------- 5. 财经·黄金实时 ----------
def fetch_gold():
    try:
        u = "https://push2delay.eastmoney.com/api/qt/clist/get?pn=1&pz=100&fs=m:101+t:3&fields=f12,f14,f2,f3&fltt=2&po=1"
        d = get_json(u)
        diff = d["data"]["diff"]
        items = list(diff.values()) if isinstance(diff, dict) else diff
        for x in items:
            if x.get("f12") in ("MGC00Y", "MGC0"):
                return {"name": x.get("f14") or "COMEX微型黄金", "value": x.get("f2"), "chg": x.get("f3")}
        for x in items:
            if "金" in (x.get("f14") or ""):
                return {"name": x.get("f14"), "value": x.get("f2"), "chg": x.get("f3")}
    except Exception as e:
        print("[GOLD] fetch failed:", e, file=sys.stderr)
    return None


# ---------- 5b. 指数估值百分位（蛋卷基金 djapi，免登录全量估值表）----------
# 显示指数名 -> 蛋卷 index_code（已在 dj 全量表中核验存在）
DANJUAN_VAL_CODES = {
    "沪深300": "SH000300", "中证500": "SH000905", "创业板指": "SZ399006",
    "科创50": "SH000688", "中证红利": "SH000922", "红利低波": "CSIH30269",
    "恒生指数": "HKHSI", "纳斯达克": "NDX", "标普500": "SP500",
}


def fetch_valuation():
    """抓取蛋卷基金全量指数估值表（PE/PB 及历史分位），返回 {指数名: {...}}。"""
    out = {}
    try:
        d = get_json("https://danjuanfunds.com/djapi/index_eva/dj",
                     ref="https://danjuanfunds.com/")
        items = (d.get("data", {}) or {}).get("items", []) or []
        by_code = {it["index_code"]: it for it in items if it.get("index_code")}
        for name, code in DANJUAN_VAL_CODES.items():
            it = by_code.get(code)
            if not it:
                continue
            out[name] = {
                "pe": it.get("pe"), "pb": it.get("pb"),
                "pe_pct": it.get("pe_percentile"), "pb_pct": it.get("pb_percentile"),
                "roe": it.get("roe"), "date": it.get("date"),
            }
    except Exception as e:
        print("[VAL] fetch failed:", e, file=sys.stderr)
    return out


def val_level(p):
    """百分位(0~1) -> 低估/适中/高估。"""
    if p is None:
        return "—"
    try:
        f = float(p)
    except Exception:
        return "—"
    if f < 0.30:
        return "低估"
    if f <= 0.70:
        return "适中"
    return "高估"


def fmt_time(s):
    """统一把 时间戳(秒/毫秒) 或 ISO 串 格式化为 'YYYY-MM-DD HH:MM'。"""
    if not s:
        return "—"
    s = str(s).strip()
    if s.isdigit():
        v = int(s)
        if v > 1e12:
            v = v / 1000.0
        try:
            return datetime.datetime.fromtimestamp(v).strftime("%Y-%m-%d %H:%M")
        except Exception:
            return s
    for cand in (s, s.replace("Z", "+00:00")):
        try:
            dt = datetime.datetime.fromisoformat(cand)
            return dt.strftime("%Y-%m-%d %H:%M")
        except Exception:
            continue
    return s


# ---------- 5c. 开源项目介绍（GitHub API，真实 description）----------
_repo_desc_cache = {}


def fetch_repo_desc(repo):
    if repo in _repo_desc_cache:
        return _repo_desc_cache[repo]
    desc = ""
    try:
        d = get_json(f"https://api.github.com/repos/{repo}",
                     ref="https://github.com/", timeout=12, n=2,
                     headers={"Accept": "application/vnd.github+json"})
        desc = (d.get("description") or "").strip()
    except Exception as e:
        print(f"[DESC] {repo} failed:", e, file=sys.stderr)
    _repo_desc_cache[repo] = desc
    return desc


# ---------- 5d. 开源项目介绍中文翻译（MyMemory 免密钥；已含中文则保留）----------
# 翻译源熔断：某源连续失败后本轮不再重试，避免 18 条 × 20s 把运行拖到几分钟
_TR_STATE = {"google_down": False, "mm_fail": 0, "mm_down": False}
_TR_FAIL_LIMIT = 5


def _mymemory(text, sl="en", tl="zh-CN"):
    if _TR_STATE["mm_down"]:
        return ""
    u = "https://api.mymemory.translated.net/get?q=" + urllib.parse.quote(text) + "&langpair=" + sl + "|" + tl
    try:
        req = urllib.request.Request(u, headers={"User-Agent": UA})
        raw = urllib.request.urlopen(req, timeout=12, context=ctx).read().decode("utf-8", "ignore")
        d = json.loads(raw)
        t = (d.get("responseData", {}) or {}).get("translatedText", "") or ""
        if not t or t == text:
            return ""
        if "MYMEMORY" in t.upper() or "QUOTA" in t.upper() or "WARNING" in t.upper():
            return ""
        _TR_STATE["mm_fail"] = 0
        return t
    except Exception as e:
        print(f"[TR] mymemory failed: {e}", file=sys.stderr)
        _TR_STATE["mm_fail"] += 1
        if _TR_STATE["mm_fail"] >= _TR_FAIL_LIMIT:
            _TR_STATE["mm_down"] = True
            print("[TR] MyMemory 连续失败，本轮停用该源", file=sys.stderr)
        return ""


def _google_gtx(text, sl="en", tl="zh-CN"):
    if _TR_STATE["google_down"]:
        return ""
    u = "https://translate.googleapis.com/translate_a/single?client=gtx&sl=" + sl + "&tl=" + tl + "&dt=t&q=" + urllib.parse.quote(text)
    try:
        req = urllib.request.Request(u, headers={"User-Agent": UA})
        raw = urllib.request.urlopen(req, timeout=12, context=ctx).read().decode("utf-8", "ignore")
        data = json.loads(raw)
        return "".join(seg[0] for seg in data[0] if seg[0])
    except Exception as e:
        print(f"[TR] google failed: {e}", file=sys.stderr)
        _TR_STATE["google_down"] = True
        return ""


def has_cjk(s):
    return any('一' <= ch <= '鿿' for ch in (s or ""))


# 中文译后术语校正（解决 MyMemory 直译误译：座席/线束/集装箱/客服代表 等）
_ZH_GLOSSARY = [
    ("座席线束", "智能体框架"),
    ("线束", "框架"),                            # harness 在 AI/软件语境=框架，非汽车线束
    ("座席", "智能体"),
    ("客服代表", "智能体"),
    ("集装箱生态", "容器生态"),
    ("集装箱", "容器"),
    ("您已经使用的代理", "您已经使用的智能体"),    # 代理作 agent 义
    ("代理技能", "智能体技能"),
    ("可观察性", "可观测性"),                    # observability 通用译法
]

# 注意：不要替换裸「代理」——云原生 Application **Proxy** 的「代理」是正确的(proxy 义)。


def _zh_fix(s):
    for wrong, right in _ZH_GLOSSARY:
        if wrong in s:
            s = s.replace(wrong, right)
    return s


def _zh_compact(s, maxlen=150, minlen=16):
    """把机器直译的冗长长句精炼为 v3.2 样例风格的一句话简介。

    规则：按句末/分号切段 → 取首段（过短则补次段，保证信息量）→ 仅对异常长文本兜底截断。
    注：精炼主要靠「取首段」完成（GitHub 描述的冗余多在第 2 句起的营销套话），
        maxlen 只兜底极端情况——设太小会截掉 DevOps 这类关键词列表的有效信息。
    例：「开源、自托管的笔记记录工具，专为快速捕获而打造。Markdown原生、轻量级、完全属于您。」
        → 「开源、自托管的笔记记录工具，专为快速捕获而打造」
    """
    if not s:
        return s
    s = s.strip()
    parts = [p.strip() for p in re.split(r"[。；;!！\n]+", s) if p.strip()]
    if not parts:
        return s
    out = parts[0]
    for p in parts[1:]:
        if len(out) >= minlen:
            break
        out += "。" + p
    if len(out) > maxlen:
        cut = out[:maxlen]
        for sep in ("，", "、", " "):
            i = cut.rfind(sep)
            if i > maxlen // 2:
                cut = cut[:i]
                break
        out = cut.rstrip("，、 ") + "…"
    return out


_trans_cache = {}


def load_trans_cache():
    p = os.path.join(STATE_DIR, "trans_cache.json")
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            # 清洗历史污染：值里残留英文原文前缀（旧版 MyMemory 长句拼接）或主体非中文的条目
            clean = {}
            for k, v in d.items():
                if not isinstance(v, str) or not _ok_zh(v):
                    continue
                nk = re.sub(r"\s+", " ", k).strip().lower()
                nv = re.sub(r"\s+", " ", v).strip().lower()
                if nk and nv.startswith(nk):
                    continue
                if _split_mixed(k) != k:       # 键本身是「原文+译文」污染拼接 → 丢弃重译
                    continue
                if _dedup_concat(v) != v:      # 值是「同一句译两遍」拼接 → 丢弃重译
                    continue
                clean[k] = v
            return clean
        except Exception:
            pass
    return {}


def save_trans_cache():
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(os.path.join(STATE_DIR, "trans_cache.json"), "w", encoding="utf-8") as f:
        json.dump(_trans_cache, f, ensure_ascii=False, indent=2)


def _cjk_ratio(s):
    """中文字符占非空白字符的比例，用于判断文本主体语言。"""
    t = re.sub(r"\s+", "", s or "")
    if not t:
        return 0.0
    return sum(1 for ch in t if "一" <= ch <= "鿿") / len(t)


def _strip_src(zh, src):
    """MyMemory 偶发把英文原文原样拼接在译文前（长句时），剥离该前缀。"""
    if not zh or not src:
        return zh
    n_src = re.sub(r"\s+", " ", src).strip().lower()
    n_zh = re.sub(r"\s+", " ", zh).strip().lower()
    if n_zh.startswith(n_src):
        head = n_src[:40]
        i = zh.lower().find(head)
        if i >= 0:
            return zh[i + len(head):].strip(" —-，,。")
        return zh[len(src):].strip(" —-，,。")
    return zh


def _split_mixed(s):
    """输入若是「英文原文 + 中文译文」的污染拼接（历史缓存键/源数据偶发），剥离英文前半段，只留中文。"""
    if not s:
        return s
    r = _cjk_ratio(s)
    if r >= 0.30 or r < 0.05:
        return s
    idx = next((i for i, ch in enumerate(s) if "一" <= ch <= "鿿"), -1)
    if idx <= 0:
        return s
    head, tail = s[:idx].strip(), s[idx:].strip()
    if len(re.sub(r"\s+", "", head)) >= 15 and _cjk_ratio(tail) >= 0.30:
        return tail
    return s


def _dedup_concat(s):
    """MyMemory 偶发把同一句重复译两遍拼在一起（A+B 且 A≈B），去掉冗余的一半。"""
    n = len(s or "")
    if n < 24:
        return s
    # 定位「同一句译两遍」的拼接点：找重复出现的连续片段，其第二次出现处即拼接点
    for k in (8, 6):
        seen = set()
        for i in range(n - k + 1):
            g = s[i:i + k]
            if g in seen:
                # 重复片段起点通常晚于真正的拼接点，在其前方回搜「前后两段整体最相似」的切点
                best_j, best_r = None, 0.0
                for j in range(max(8, i - 10), i + 1):
                    a, b = s[:j], s[j:]
                    if len(a) < 8 or len(b) < 8:
                        continue
                    r = difflib.SequenceMatcher(None, a, b).ratio()
                    if r > best_r:
                        best_j, best_r = j, r
                if best_j and best_r > 0.5:
                    return s[:best_j].strip()
            else:
                seen.add(g)
    return s


def _ok_zh(zh):
    """译文合格判定：主体须为中文（≥12% 汉字），否则视为翻译失败。"""
    return _cjk_ratio(zh) >= 0.12


def _truncate_for_api(s, maxlen=200):
    """MyMemory 对超长 q 容易 504；简介最终只取首句，这里先按首句截断再翻译，成功率与速度都更好。"""
    if len(s) <= maxlen:
        return s
    for sep in ("\n", ". ", "。", "! ", "? ", "; ", "；"):
        i = s.find(sep)
        if 0 < i <= maxlen:
            return s[:i + 1].strip()
    return s[:maxlen].rsplit(" ", 1)[0].strip()


def translate_zh(text):
    """英文项目介绍翻译成中文；主体为中文则只做术语校正+精炼；全部翻译源失败则退回纯净原文（绝不拼接原文+译文）。"""
    if not text:
        return text
    text = _split_mixed(text)          # 先剥离可能存在的「原文+译文」污染
    if _cjk_ratio(text) >= 0.30:
        # 原生中文描述（如 macrozheng/mall）不翻译，但同样要术语校正 + 精炼，
        # 否则整段营销文案会原样超长输出。
        return _zh_compact(_zh_fix(_dedup_concat(text)))
    key = text.strip()
    cached = _trans_cache.get(key)
    if cached and _ok_zh(cached):
        return cached
    q = _truncate_for_api(key)          # 超长描述先按首句截断，降低 504 概率
    zh = ""
    for src_fn in (_mymemory, _google_gtx):
        for attempt in (1, 2):          # 瞬时抖动（504/超时）重试一次，间隔 1.2s
            raw = src_fn(q)
            if raw:
                cand = _dedup_concat(_strip_src(raw, q))
                if _ok_zh(cand):
                    zh = cand
                    break
                if not zh:
                    zh = cand if _cjk_ratio(cand) > _cjk_ratio(key) else ""
            if _TR_STATE["mm_down"] and _TR_STATE["google_down"]:
                break
            if attempt == 1:
                time.sleep(1.2)
        if _ok_zh(zh):
            break
    failed = not _ok_zh(zh)
    if failed:
        zh = key                      # 纯净原文，不做任何拼接/截断
    else:
        zh = _zh_fix(zh)
        zh = _zh_compact(zh)          # 精炼为 v3.2 样例风格的一句话简介
        zh = _dedup_concat(zh)        # 兜底：去掉「同一句译两遍」的冗余
    if not failed:
        _trans_cache[key] = zh        # 失败不写缓存，下次仍重试
    return zh



# ---------- 6. QDII 净值 / 基金净值（天天基金 API，真实 T+2 净值）----------
QDII_FUNDS = {
    "000834": "大成纳斯达克100(QDII)",
    "270042": "广发纳斯达克100指数(QDII)",
    "050025": "博时标普500ETF联接(QDII)",
    "161125": "易方达标普500(QDII-LOF)",
    "013402": "华夏恒生科技ETF发起式(QDII)",
    "164906": "交银中证海外中国互联网(QDII)",
}
GOLD_ETF = "518880"  # 华夏黄金ETF


def fetch_fund_nav(codes):
    result = {}
    for code in codes:
        try:
            u = f"https://api.fund.eastmoney.com/f10/lsjz?fundCode={code}&pageIndex=1&pageSize=1&_={int(time.time() * 1000)}"
            d = get_json(u, ref="http://fundf10.eastmoney.com/")
            lst = d.get("Data", {}).get("LSJZList", [])
            if lst:
                row = lst[0]
                result[code] = {"nav": row.get("DWJZ"), "date": row.get("FSRQ")}
        except Exception as e:
            print(f"[NAV] {code} fetch failed:", e, file=sys.stderr)
    return result


# ---------- 7. 宏观/财经要闻（新浪滚动 API，事件驱动 + 扩展搜索）----------
def fetch_macro(page=1, keywords=None):
    kw = keywords or MACRO_KEYWORDS
    out = []
    try:
        u = f"https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2509&num=50&page={page}&_={int(time.time() * 1000)}"
        d = get_json(u, ref="https://finance.sina.com.cn/")
        items = d.get("result", {}).get("data", [])
        for it in items:
            title = it.get("title", "")
            if any(k in title for k in kw):
                out.append({"title": title, "url": it.get("url", ""), "ctime": it.get("ctime", "")})
    except Exception as e:
        print("[MACRO] fetch failed:", e, file=sys.stderr)
    return out


_github_html_ok = None


def github_html_ok():
    """探测 github.com 的 HTML 页是否可达。

    沙箱/部分网络下 github.com TLS 握手会超时，而 api.github.com 正常。
    若不先探测，每个池子都要把重试次数耗完（4 次 × 15s）才走兜底，一次运行会白等数分钟。
    结果缓存，全程只探测一次。
    """
    global _github_html_ok
    if _github_html_ok is None:
        try:
            req = urllib.request.Request("https://github.com/trending", headers={"User-Agent": UA})
            urllib.request.urlopen(req, timeout=8, context=ctx).read(2048)
            _github_html_ok = True
        except Exception:
            _github_html_ok = False
            print("[NET] github.com HTML 不可达，改用官方 Search API", file=sys.stderr)
    return _github_html_ok


def fetch_search_repos(q, per_page=30):
    """GitHub 官方 Search API（结构化 JSON）。

    为什么需要它：github.com 的 HTML 页（trending / topics）在部分网络环境（含本沙箱）
    会 TLS 握手超时，而 api.github.com 稳定可达。用官方 API 可同时拿到
    repo / description / stars / language，无需逐仓库再请求，也不依赖脆弱的正则解析。
    注意：Search API 不提供「今日新增 stars」，故 HTML trending 仍作为主源（有今日+）。
    """
    out = []
    u = ("https://api.github.com/search/repositories?q=" + urllib.parse.quote(q)
         + "&sort=stars&order=desc&per_page=" + str(per_page))
    try:
        d = get_json(u, ref="https://github.com/", timeout=15, n=3,
                     headers={"Accept": "application/vnd.github+json"})
        for it in d.get("items", []):
            out.append({
                "repo": it.get("full_name", ""),
                "url": it.get("html_url", ""),
                "desc": (it.get("description") or "").strip(),
                "lang": it.get("language") or "",
                "stars": format(it.get("stargazers_count", 0), ","),
                "today": "",          # Search API 无今日新增字段
            })
    except Exception as e:
        print(f"[SEARCH] {q} failed:", e, file=sys.stderr)
    return out


# ---------- 去重 ----------
def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_history(hist):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(hist, f, ensure_ascii=False, indent=2)


def is_recent(hist, key, today, window=DEDUP_WINDOW):
    if key not in hist:
        return False
    try:
        d = datetime.date.fromisoformat(hist[key])
        return (today - d).days < window
    except Exception:
        return False


def ai_bucket(it):
    """把一条 AI 资讯归入子项（开源模型栏走 HF 源，不在此分配）。

    优先级：测评强信号词 > 官方 category 映射。
    数据源 category 取值：ai-models / tip / ai-products / industry / paper
      · 行业资讯 ← industry（政策、融资、商业）+ ai-products（产品应用）+ tip（实践与观点）
      · 模型发布 ← ai-models（模型发布与更新）+ paper（研究进展）
    """
    # 只按标题判定：摘要里顺带提到基准成绩（如"IFM 发布 K2 Horizon…达到 SOTA"）
    # 的条目主旨是模型发布，纳入测评栏会归类失真。
    if AI_BENCH_PAT.search(it.get("title") or ""):
        return "大模型测评"
    cat = it.get("category") or ""
    if cat in ("ai-models", "paper"):
        return "模型发布"
    return "行业资讯"


def dedup_same_event(items, sim=0.62):
    """同一事件的多篇报道只保留一条（score 高者优先，同分取更新）。

    实测：测评类关键词命中 16 条，其中 13 条同属「GPT-6 Astra 基准成绩」一个事件，
    不去重会让同一件事占满整个子项。判定依据两条：
      1) 标题中提取的模型实体有交集（GPT-6 Astra / Claude Fable / ARC-AGI 等）；
      2) 标题字面相似度 > sim（difflib，覆盖实体提取漏网的措辞差异）。
    """
    picked_titles, picked_ents = [], set()
    for it in sorted(items, key=lambda x: (-(x.get("score") or 0),
                                           x.get("publishedAt") or "")):
        t = it.get("title") or ""
        ents = set(m.group(0).strip().lower() for m in AI_MODEL_ENT.finditer(t))
        if ents and (ents & picked_ents):
            continue
        if any(difflib.SequenceMatcher(None, t, pt).ratio() > sim for pt in picked_titles):
            continue
        picked_ents |= ents
        picked_titles.append(t)
        yield it


def allocate_ai_quota(pools):
    """按 AI_QUOTA 分配各子项条数；某子项供给不足时，省下的额度转给仍有余量的子项。

    注意：额度只做"数量转移"，绝不把 A 子项的内容塞进 B 子项（避免归类失真）。
    """
    picked = {k: list(v[:AI_QUOTA.get(k, 0)]) for k, v in pools.items()}
    left = AI_TOTAL - sum(len(v) for v in picked.values())
    while left > 0:
        cands = sorted(pools, key=lambda k: -(len(pools[k]) - len(picked[k])))
        for k in cands:
            if len(picked[k]) < len(pools[k]):
                picked[k].append(pools[k][len(picked[k])])
                left -= 1
                break
        else:
            break                      # 所有子项供给都取尽，收缩总量
    return picked


def dedupe(items, keyfn):
    seen, out = set(), []
    for it in items:
        k = keyfn(it)
        if k not in seen:
            seen.add(k)
            out.append(it)
    return out


# 生态多样性过滤：按星标排序时，同一热门生态（如某 LLM 的插件生态）会一次性霸榜，
# 实测出现过 10 条里 5 条同属一个生态。这里限制：同一 owner 最多 1 条、同一主题词最多 1 条。
_TOPIC_STOP = {"the", "app", "apps", "web", "core", "api", "kit", "lab", "labs",
               "io", "dev", "pro", "ui", "js", "py", "go", "org", "com", "net",
               "open", "source", "awesome", "list", "docs", "demo", "server", "client"}


def diversify(pool, cap_per_owner=1, cap_per_topic=1, limit=40):
    owner_cnt, topic_cnt, out = {}, {}, []
    for r in pool:
        repo = r.get("repo", "")
        owner = repo.split("/")[0] if "/" in repo else repo
        if owner_cnt.get(owner, 0) >= cap_per_owner:
            continue
        topics = [t for t in re.findall(r"[a-z]{3,}", repo.lower()) if t not in _TOPIC_STOP]
        if any(topic_cnt.get(t, 0) >= cap_per_topic for t in topics):
            continue
        owner_cnt[owner] = owner_cnt.get(owner, 0) + 1
        for t in topics:
            topic_cnt[t] = topic_cnt.get(t, 0) + 1
        out.append(r)
        if len(out) >= limit:
            break
    return out


def fmt_pct(v):
    if v is None or v == "":
        return "—"
    try:
        f = float(v)
        return f"{f:+.2f}%"
    except Exception:
        return str(v)


# ---------- 主流程 ----------
def main():
    today = datetime.date.today()
    today_str = today.isoformat()
    weekday = "一二三四五六日"[today.weekday()]
    hist = load_history()
    _trans_cache.clear()
    _trans_cache.update(load_trans_cache())

    # ===== 各文本栏：大池子 → 只发 7 天内未发过的较新资讯 =====

    # --- AI：7 天全量 API 池 → 按 4 个子项分组，各组只发 7 天内未发过的较新资讯 ---
    ai_all = fetch_ai_api()
    ai_fresh = [a for a in ai_all if not is_recent(hist, "ai:" + a["id"], today)]
    ai_fresh.sort(key=lambda x: x.get("publishedAt", ""), reverse=True)
    ai_pools = {"行业资讯": [], "模型发布": [], "大模型测评": []}
    for a in ai_fresh:
        ai_pools[ai_bucket(a)].append(a)
    # 测评类：同一事件的多篇报道只留 score 最高的一条（实测 16 条命中 → 4 个独立事件）
    ai_pools["大模型测评"] = list(dedup_same_event(ai_pools["大模型测评"]))
    # 开源模型：aihot 是资讯流、几乎没有开源模型供给（实测 99 条中仅 1 条），
    # 因此该栏改走 HuggingFace 镜像源；镜像不可达时为空列表，渲染层显示"今日无新增"。
    hf_all = fetch_hf_models(limit=12)
    ai_pools["开源模型"] = [m for m in hf_all if not is_recent(hist, "hf:" + m["id"], today)]
    # 配额：先按 AI_QUOTA 取，某栏不足则额度转给仍有余量的栏（只转数量，不跨栏塞内容）
    ai_groups = allocate_ai_quota(ai_pools)
    ai_shown = [x for k in ("行业资讯", "模型发布", "大模型测评") for x in ai_groups.get(k, [])]
    hf_shown = ai_groups.get("开源模型", [])
    # 开源模型：方案B（抓模型卡 README 首段当简介，译为中文）；抓取/翻译失败则降级方案A（元数据合成，保证中文）
    for m in hf_shown:
        raw = fetch_hf_model_intro(m["id"]) if m.get("id") else ""
        intro = translate_zh(raw) if raw else ""
        if not _ok_zh(intro):           # 抓取空 或 翻译失败（源故障/超限）→ 降级方案A
            intro = hf_intro_fallback(m)
        m["intro"] = intro

    # --- 开源：日/周/月 + 多语言变体池，筛未发项（即"上次运行以来新增 + 扩展搜索"）---
    # 说明：github.com 的 HTML 页在部分网络环境极慢/IncompleteRead（实测 daily 页 86s 后失败），
    # 而官方 Search API 稳定且快（3.5s/30 条）且自带简介。因此：
    #   · HTML trending 只取 daily 一页（唯一提供"今日新增 stars"），8s 快速失败，纯作补充；
    #   · Search API 作为稳定主源，始终并入。两者按 repo 去重，HTML 条目排在前面。
    repo_pool = []
    if github_html_ok():
        repo_pool = fetch_trending("daily", timeout=8, n=1)
    repo_pool = dedupe(
        repo_pool
        + fetch_search_repos("created:>" + (today - datetime.timedelta(days=30)).isoformat()
                             + " stars:>1000")   # 星标下限：滤掉近 30 天新建的三无小项目
        + fetch_search_repos("stars:>3000 pushed:>" + (today - datetime.timedelta(days=7)).isoformat()),
        lambda r: r["repo"])
    # 先筛 7 天内未发项 → 再做生态多样性 → 再截断（保证最终展示不出现同一生态霸榜）
    fresh_repos = [r for r in repo_pool if not is_recent(hist, "gh:" + r["repo"], today)]
    repos_shown = diversify(fresh_repos, limit=OS_CAP)

    # --- Docker：容器/自托管专属真实源，排除已进"开源"栏的，筛未发项 ---
    # 直接用 Search API 的 topic 查询（3 次约 10s）；不再抓 topics HTML（单次实测 30s，易拖垮运行）
    docker_topic_pool = dedupe(
        fetch_search_repos("topic:self-hosted") + fetch_search_repos("topic:docker")
        + fetch_search_repos("topic:homelab"),
        lambda r: r["repo"])
    docker_pool = dedupe(split_docker(repo_pool) + docker_topic_pool, lambda r: r["repo"])
    shown_repos = {r["repo"] for r in repos_shown}
    docker_shown = diversify(
        [r for r in docker_pool
         if (not is_recent(hist, "gh:" + r["repo"], today)) and r["repo"] not in shown_repos],
        limit=DOCKER_CAP)

    # --- 为展示中的开源/Docker 项目补全真实项目介绍（GitHub API），并译为中文 ---
    for r in repos_shown + docker_shown:
        if not r.get("desc"):        # Search API 结果已自带 description，无需重复请求
            d = fetch_repo_desc(r["repo"])
            if d:
                r["desc"] = d
        if r.get("desc"):
            r["desc"] = translate_zh(r["desc"])

    # --- 宏观：事件驱动；无新则深翻页 + 宽关键词扩展搜索未发项 ---
    macro_shown = [m for m in fetch_macro(1)
                   if not is_recent(hist, "macro:" + (m["url"] or m["title"]), today)]
    # 事件驱动，但按用户要求"不留空、宁可加大搜索也不发重复"：
    # 只要条数不足 MACRO_CAP 就继续深翻页 + 宽关键词扩展，而不是只在一条都没有时才扩展。
    # （原写法 `if not macro_shown` 会导致首屏只抓到 1 条就停止，信息量明显不足）
    seen = {m["url"] or m["title"] for m in macro_shown}
    for pg in (1, 2, 3, 4):
        if len(macro_shown) >= MACRO_CAP:
            break
        for m in fetch_macro(pg, MACRO_KEYWORDS_BROAD):
            if len(macro_shown) >= MACRO_CAP:
                break
            k = m["url"] or m["title"]
            if k not in seen and not is_recent(hist, "macro:" + k, today):
                seen.add(k)
                macro_shown.append(m)
    macro_shown = macro_shown[:MACRO_CAP]

    # --- 财经快照（每日实采，天然新鲜）---
    indices = fetch_indices()
    sectors = fetch_sectors()
    gold = fetch_gold()
    valuation = fetch_valuation()
    qdii_nav = fetch_fund_nav(list(QDII_FUNDS.keys()))
    gold_nav = fetch_fund_nav([GOLD_ETF]).get(GOLD_ETF)

    # ===== 标记已发：仅标记本次实际展示的项（未展示的新项保留给后续运行）=====
    for a in ai_shown:
        hist["ai:" + a["id"]] = today_str
    for m in hf_shown:
        hist["hf:" + m["id"]] = today_str
    for r in repos_shown:
        hist["gh:" + r["repo"]] = today_str
    for r in docker_shown:
        hist["gh:" + r["repo"]] = today_str
    for m in macro_shown:
        hist["macro:" + (m["url"] or m["title"])] = today_str
    # 清理超过窗口的旧键，控制历史体积
    for k in list(hist):
        if not is_recent(hist, k, today):
            del hist[k]
    save_history(hist)
    save_trans_cache()

    # 板块涨跌排行
    sec_sorted = sorted([s for s in sectors if s["chg"] is not None],
                        key=lambda x: float(x["chg"]) if str(x["chg"]).replace(".", "", 1).replace("-", "", 1).isdigit() else -999)
    top_up = sec_sorted[-6:][::-1]
    top_dn = sec_sorted[:6]

    # ============ 生成 Markdown ============
    L = []
    L.append(f"# 资讯日报 · {today_str}（周{weekday}）")
    L.append("")
    L.append(f"> 真实管道生成（digester.py 实跑）· 两道防重：7天去重 + 只发未发过的较新资讯（无新增则扩展搜索，不留空、不重发）")
    ai_break = " / ".join(f"{short} {len(ai_groups.get(k, []))}" for k, _, short in AI_SECTIONS)
    L.append(f"> AI {len(ai_shown) + len(hf_shown)} 条（{ai_break}）/ 开源 {len(repos_shown)} 条 / Docker {len(docker_shown)} 条 / 宏观 {len(macro_shown)} 条 / QDII净值 {len(qdii_nav)} / 财经实时快照见下")
    L.append("")

    L.append("## 一、AI 资讯")
    for key, sub, _short in AI_SECTIONS:
        items = ai_groups.get(key, [])
        L.append(f"### {sub}（{len(items)} 条）")
        if not items:
            L.append("> 今日无新增（7 天去重窗口内无可发条目，不重复展示，也不跨栏填充）。")
            L.append("")
            continue
        for i, it in enumerate(items, 1):
            if key == "开源模型":
                L.append(f"**{i}. {it['id']}**")
                bits = []
                if it.get("license"):
                    bits.append(f"许可：{it['license']}")
                if it.get("param"):
                    bits.append(f"参数：{it['param']}")
                bits.append(f"点赞 {fmt_num(it['likes'])}")
                bits.append(f"下载 {fmt_num(it['downloads'])}")
                L.append("- " + " · ".join(bits))
                if it.get("createdAt"):
                    L.append(f"- 发布：{it['createdAt'][:10]}")
                if it.get("intro"):
                    L.append(f"- 简介：{it['intro']}")
                L.append(f"- 链接：{it['url']}")
            else:
                L.append(f"**{i}. {it['title']}**")
                L.append(f"- 来源：{it['source']}  ·  发布：{fmt_time(it['publishedAt'])}")
                if it["summary"]:
                    L.append(f"- 摘要：{it['summary']}")
                L.append(f"- 链接：{it['url']}")
            L.append("")

    L.append("## 二、优质开源项目（GitHub Trending）")
    if repos_shown:
        for i, r in enumerate(repos_shown, 1):
            star_txt = f" ★{r['stars']}" if r["stars"] else ""
            today_txt = f" · 今日 +{r['today']}" if r["today"] else ""
            lang_txt = f" · {r['lang']}" if r["lang"] else ""
            L.append(f"**{i}. {r['repo']}{lang_txt}{star_txt}{today_txt}**")
            L.append(f"- 地址：{r['url']}")
            if r["desc"]:
                L.append(f"- 简介：{r['desc']}")
            L.append("")
    else:
        L.append("> 今日开源榜单暂不可达（网络/接口波动），或近 7 天可发项目均已推送。")
        L.append("")

    L.append("## 二之一、Docker / 自托管容器推荐")
    if docker_shown:
        for i, r in enumerate(docker_shown, 1):
            lang_txt = f" · {r['lang']}" if r.get("lang") else ""
            L.append(f"**{i}. {r['repo']}{lang_txt}**")
            L.append(f"- 地址：{r['url']}")
            if r.get("desc"):
                L.append(f"- 简介：{r['desc']}")
            L.append("")
    else:
        L.append("> 今日无新增容器类项目（近 7 天可发项均已推送，不重复展示）。")
        L.append("")

    # ---------- 财经 ----------
    L.append("## 三、财经资讯")
    L.append("")

    L.append("### [估值] 主要指数估值（点位·东方财富｜PE/PB 历史分位·蛋卷）")
    val_date = next((v["date"] for v in valuation.values() if v.get("date")), "")
    if indices or valuation:
        L.append("| 指数 | 点位 | 涨跌幅 | PE(TTM) | PE分位 | PB | PB分位 | 估值水位 |")
        L.append("|---|---|---|---|---|---|---|---|")
        idx_by_name = {x["name"]: x for x in indices}
        # 遍历「实时行情 ∪ 蛋卷估值」并集：红利低波等有估值但无实时行情 secid 的
        # 指数也必须列出（点位/涨跌幅显示 —），否则会被静默漏掉。
        for name in dict.fromkeys(list(idx_by_name) + list(valuation)):
            x = idx_by_name.get(name)
            point = x["value"] if x else "—"
            chg = fmt_pct(x["chg"]) if (x and x.get("chg") is not None) else "—"
            v = valuation.get(name)
            if v and v.get("pe") is not None and v.get("pe_pct") is not None:
                pe = f"{float(v['pe']):.2f}"
                pe_pct = f"{float(v['pe_pct']) * 100:.0f}%"
                pel = val_level(v["pe_pct"])
                pb = f"{float(v['pb']):.2f}"
                pb_pct = f"{float(v['pb_pct']) * 100:.0f}%"
                pbl = val_level(v["pb_pct"])
                if pel == pbl:
                    overall = pel
                elif "低估" in (pel, pbl):
                    overall = "偏低（PE/PB 分歧）" if "高估" not in (pel, pbl) else "分化"
                elif "高估" in (pel, pbl):
                    overall = "偏高（PE/PB 分歧）"
                else:
                    overall = "适中"
                L.append(f"| {name} | {point} | {chg} | {pe} | {pe_pct}·{pel} | {pb} | {pb_pct}·{pbl} | {overall} |")
            else:
                L.append(f"| {name} | {point} | {chg} | — | — | — | — | — |")
        L.append("")
        if val_date:
            L.append(f"> 估值数据来源：蛋卷基金（截至 {val_date}）。分位 = 当前 PE/PB 在历史时期中的相对位置；**<30% 低估、30–70% 适中、>70% 高估**。纳斯达克100/标普500 为境外指数，分位口径同蛋卷。")
        L.append("")
    else:
        L.append("> 指数实时行情暂不可达（网络/接口波动），重试或检查源。")
        L.append("")

    L.append("### [板块] 行业板块实时涨跌（东方财富）")
    if top_up or top_dn:
        up_str = "、".join(f"{s['name']}{fmt_pct(s['chg'])}" for s in top_up)
        dn_str = "、".join(f"{s['name']}{fmt_pct(s['chg'])}" for s in top_dn)
        L.append(f"- 领涨：{up_str}")
        L.append(f"- 领跌：{dn_str}")
        L.append("")
    else:
        L.append("> 行业板块行情暂不可达。")
        L.append("")

    L.append("### [黄金] 贵金属实时（东方财富 / SGE 映射）")
    if gold:
        L.append(f"- COMEX 微型黄金(国际金价方向)：{gold['value']}（{fmt_pct(gold['chg'])}）盘中实时")
    if gold_nav:
        try:
            per_gram = float(gold_nav["nav"]) * 100
            gram_txt = f"（≈ {per_gram:.0f} 元/克）"
        except Exception:
            gram_txt = ""
        L.append(f"- SGE Au99.99 现货金（经 518880 华夏黄金ETF 映射）：ETF 最新净值 {gold_nav['nav']}（净值日期 {gold_nav['date']}）{gram_txt}；该 ETF 直接持有 SGE Au99.99 实物，净值即现货金真实代理")
    L.append("- 关联：黄金股(山东黄金600547/中金黄金600489/紫金矿业601899) vs 黄金ETF——两类资产，非纯跟金价")
    if gold or gold_nav:
        L.append("> 说明：SGE 现货金无公开实时免费 API，以 518880（100% 持仓 SGE Au99.99 实物）最新净值作真实映射；COMEX 微型黄金提供国际金价盘中方向。")
        L.append("")
    else:
        L.append("> 黄金行情暂不可达。")
        L.append("")

    L.append("### [QDII/海外] 海外指数实时 + QDII 净值")
    overseas = [x for x in indices if x["name"] in ("纳斯达克", "标普500", "恒生指数")]
    if overseas:
        for x in overseas:
            L.append(f"- 海外指数 {x['name']}：{x['value']}（{fmt_pct(x['chg'])}）")
    if qdii_nav:
        for code, name in QDII_FUNDS.items():
            v = qdii_nav.get(code)
            if v:
                L.append(f"- {name}（{code}）：单位净值 {v['nav']}（净值日期 {v['date']}）")
        L.append("> 注：QDII 净值 T+2 披露（境外时差+跨境结算），展示最新可得净值并标净值日期；人民币计价受汇率影响。实时净值源接入见方案 1.3.7。")
    else:
        L.append("> QDII 净值源暂不可达。")
    L.append("")

    L.append("### [宏观] 财经要闻（新浪滚动，事件驱动）")
    if macro_shown:
        for m in macro_shown:
            L.append(f"- {m['title']}")
            extra = []
            if m.get("ctime"):
                extra.append(f"发布：{fmt_time(m['ctime'])}")
            if m.get("url"):
                extra.append(f"链接：{m['url']}")
            if extra:
                L.append(f"  - " + " ｜ ".join(extra))
        L.append("")
    else:
        L.append("> 近 7 天宏观要闻均已推送，暂无可发新增（事件驱动，无新事件不强制每日输出）。")
        L.append("")

    L.append("### [低估值提醒]（基于蛋卷 PE/PB 历史分位）")
    low = [(n, v) for n, v in valuation.items()
           if val_level(v.get("pe_pct")) == "低估" and val_level(v.get("pb_pct")) in ("低估", "适中")]
    if low:
        for n, v in sorted(low, key=lambda kv: float(kv[1].get("pe_pct") or 1)):
            L.append(f"- {n}：PE 分位 {float(v['pe_pct']) * 100:.0f}%（低估），PE {float(v['pe']):.2f}｜PB 分位 {float(v['pb_pct']) * 100:.0f}%，PB {float(v['pb']):.2f}")
        L.append("> 规则：PE、PB 双低（分位<30%）标为显著低估关注；分位<30% 低估、30–70% 适中、>70% 高估。")
    else:
        L.append("> 当前主要宽基/策略指数中暂无 PE、PB 双低（分位<30%）的显著低估标的；可关注适中偏低品种。")
    L.append("")

    md = "\n".join(L)

    os.makedirs(OUT_DIR, exist_ok=True)
    os.makedirs(DOC_DIR, exist_ok=True)
    md_path = os.path.join(OUT_DIR, f"{today_str}.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)

    # ============ 生成 RSS feed.xml ============
    # ============ 生成 RSS feed.xml ============
    # 每条 description 用 HTML（CDATA）包裹：阅读器渲染换行/加粗/链接，不再显示原始 Markdown 符号
    rss_items = []
    for key, _sub, _short in AI_SECTIONS:
        cat = _sub.split(" ", 1)[-1]        # "1.1 AI 行业资讯" -> "AI 行业资讯"
        for it in ai_groups.get(key, []):
            if key == "开源模型":
                bits = []
                if it.get("license"):
                    bits.append(f"许可 {_h(it['license'])}")
                if it.get("param"):
                    bits.append(f"参数 {_h(it['param'])}")
                bits.append(f"点赞 {fmt_num(it['likes'])}")
                bits.append(f"下载 {fmt_num(it['downloads'])}")
                bits.append(f"发布 {_h(it.get('createdAt', '')[:10])}")
                lines = [f"<b>{_h(it['id'])}</b>", " · ".join(bits)]
                if it.get("intro"):
                    lines.append(_h(it["intro"]))
                lines.append(f'<a href="{_h(it["url"])}">模型地址</a>')
                rss_items.append(rss_item(f"[开源模型] {it['id']}", it["url"], "<br>".join(lines), cat))
            else:
                lines = [f"<b>{_h(it['title'])}</b>",
                         f"来源：{_h(it['source'])} · 发布：{_h(fmt_time(it['publishedAt']))}"]
                if it["summary"]:
                    lines.append(_h(it["summary"]))
                lines.append(f'<a href="{_h(it['url'])}">查看原文</a>')
                rss_items.append(rss_item(it["title"], it["url"], "<br>".join(lines), cat))
    for r in repos_shown:
        lines = [f"<b>{_h(r['repo'])}</b> · {_h(r['lang'])} ★{_h(r['stars'])}"]
        if r["desc"]:
            lines.append(_h(r["desc"]))
        lines.append(f'<a href="{_h(r['url'])}">地址</a>')
        rss_items.append(rss_item(f"{r['repo']} ★{r['stars']}", r["url"], "<br>".join(lines), "开源"))
    for r in docker_shown:
        lines = [f"<b>{_h(r['repo'])}</b>"]
        if r.get("desc"):
            lines.append(_h(r["desc"]))
        lines.append(f'<a href="{_h(r['url'])}">地址</a>')
        rss_items.append(rss_item(f"[Docker] {r['repo']}", r["url"], "<br>".join(lines), "Docker"))
    for m in macro_shown:
        lines = [f"<b>{_h(m['title'])}</b>"]
        if m.get("url"):
            lines.append(f'<a href="{_h(m['url'])}">链接</a>')
        rss_items.append(rss_item(f"[宏观] {m['title']}", m.get("url") or "https://finance.sina.com.cn/", "<br>".join(lines), "宏观"))
    # 市场快照作为一条
    snap_lines = ["每日市场快照："]
    if indices:
        snap_lines.append("；".join(f"{x['name']}{fmt_pct(x['chg'])}" for x in indices) + "。")
    if gold:
        snap_lines.append(f"{gold['name']}{fmt_pct(gold['chg'])}。")
    rss_items.append(rss_item(f"市场快照 {today_str}", "https://eastmoney.com", "<br>".join(snap_lines), "财经"))
    # 估值快照作为一条
    if valuation and indices:
        vlines = []
        for x in indices:
            v = valuation.get(x["name"])
            if v and v.get("pe_pct") is not None:
                vlines.append(f"{x['name']} PE分位{float(v['pe_pct']) * 100:.0f}%({val_level(v['pe_pct'])}) PB分位{float(v['pb_pct']) * 100:.0f}%({val_level(v['pb_pct'])})")
        if vlines:
            rss_items.append(rss_item(f"指数估值 {today_str}", "https://danjuanfunds.com/",
                                     "<br>".join(vlines) + f"<br>（蛋卷，截至 {_h(val_date)}）", "估值"))

    feed = build_rss(rss_items, today_str)
    with open(os.path.join(DOC_DIR, "feed.xml"), "w", encoding="utf-8") as f:
        f.write(feed)

    print(f"[OK] 生成完成 -> {md_path}")
    print(f"[OK] RSS -> {os.path.join(DOC_DIR, 'feed.xml')}")
    ai_stat = " ".join(f"{k}={len(ai_groups.get(k, []))}" for k, _, _ in AI_SECTIONS)
    print(f"[STAT] AI总={len(ai_shown) + len(hf_shown)}（{ai_stat}）开源={len(repos_shown)} Docker={len(docker_shown)} 宏观={len(macro_shown)} 指数={len(indices)} 板块={len(sectors)} 黄金={'Y' if gold else 'N'} 518880={'Y' if gold_nav else 'N'} QDII净值={len(qdii_nav)}")
    health_check(ai_shown, repos_shown, docker_shown, macro_shown,
                 indices, valuation, sectors, qdii_nav)


def health_check(ai, repos, docker, macro, indices, valuation, sectors, qdii_nav):
    """产出健康校验：核心栏空 = 数据源异常，非 0 退出（Actions 标红且不会提交残缺日报）；
    次级栏缺失仅告警，不阻断。"""
    fatal = [n for n, v in (("AI", ai), ("开源", repos), ("Docker", docker)) if not v]
    warn = [n for n, v in (("宏观", macro), ("指数行情", indices),
                           ("估值", valuation), ("板块", sectors), ("QDII净值", qdii_nav)) if not v]
    for n in warn:
        print(f"::warning::{n} 栏为空（数据源可能临时不可用），本次仍生成日报", file=sys.stderr)
    if fatal:
        for n in fatal:
            print(f"::error::{n} 栏为空，判定数据源异常", file=sys.stderr)
        print(f"[FATAL] 核心栏为空：{'、'.join(fatal)}；已终止，不提交残缺日报（去重历史未消费，可原样重跑）",
              file=sys.stderr)
        sys.exit(2)


def _h(txt):
    """CDATA 内文本节点转义：& < > 转实体，使 HTML 渲染层正确还原（不影响我们手动插入的 <b>/<br>/<a> 结构标签）。"""
    return (txt or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def cdata(s):
    """把内容包进 CDATA（RSS 阅读器据此按 HTML 渲染换行/加粗/链接，而非显示原始 Markdown 符号）。"""
    return f"<![CDATA[{(s or '').replace(']]>', ']]&gt;')}]]>"


def rss_item(title, link, html_desc, cat):
    title = esc(title)
    link = esc(link)
    cat = esc(cat)
    return (f"    <item>\n      <title>{title}</title>\n"
            f"      <link>{link}</link>\n"
            f"      <description>{cdata(html_desc)}</description>\n"
            f"      <category>{cat}</category>\n    </item>")


def esc(s):
    if s is None:
        return ""
    s = str(s)
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_rss(items, today_str):
    now = datetime.datetime.now().strftime("%a, %d %b %Y %H:%M:%S +0800")
    body = "\n".join(items)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>个人资讯日报</title>
    <link>https://eastmoney.com</link>
    <description>AI / 开源 / Docker / 财经 每日聚合（真实管道生成）</description>
    <language>zh-CN</language>
    <lastBuildDate>{now}</lastBuildDate>
    <pubDate>{now}</pubDate>
{body}
  </channel>
</rss>
"""


if __name__ == "__main__":
    main()
