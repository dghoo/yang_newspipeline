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

import urllib.request, urllib.parse, ssl, re, json, html, os, sys, time, datetime

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

# 宏观关键词（主）：命中才算"宏观事件"
MACRO_KEYWORDS = ["降准", "降息", "社融", "CPI", "PPI", "GDP", "央行", "货币政策",
                  "美联储", "逆回购", "MLF", "流动性", "国债", "利率", "宏观", "经济数据", "财联社"]
# 宏观扩展（兜底搜索用，更宽，尽量挖到未发过的较新宏观资讯）
MACRO_KEYWORDS_BROAD = MACRO_KEYWORDS + ["经济", "政策", "数据", "汇率", "美债", "A股",
                                         "证监会", "财政部", "统计局", "PMI", "出口", "进口",
                                         "就业", "通胀", "衰退"]


# ---------- 网络工具 ----------
def get_text(u, timeout=10, n=4, ref=None, headers=None):
    last = None
    for _ in range(n):
        try:
            h = {"User-Agent": UA}
            if ref:
                h["Referer"] = ref
            if headers:
                h.update(headers)
            req = urllib.request.Request(u, headers=h)
            return urllib.request.urlopen(req, timeout=timeout, context=ctx).read().decode("utf-8", "ignore")
        except Exception as e:
            last = e
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
                            "publishedAt": it.get("publishedAt", "")})
            pg = d.get("page", {})
            nc = pg.get("nextCursor")
            if not nc or not pg.get("hasMore"):
                break
            url = "https://aihot.virxact.com/api/v1/items?cursor=" + nc
    except Exception as e:
        print("[AI] fetch failed:", e, file=sys.stderr)
    return out


# ---------- 2. GitHub Trending（日/周/月 + 多语言变体池）----------
def fetch_trending(since="daily", lang=None):
    repos = []
    try:
        params = []
        if since and since != "daily":
            params.append("since=" + since)
        if lang:
            params.append("spoken_language_code=" + lang)
        q = ("?" + "&".join(params)) if params else ""
        h = get_text("https://github.com/trending" + q)
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
        "科创50": "1.000688", "中证红利": "1.000922", "红利低波": "1.930998",
        "恒生指数": "100.HSI", "纳斯达克100": "100.NDX", "标普500": "100.SPX",
    }
    out = []
    try:
        s = ",".join(secids.values())
        u = f"https://push2delay.eastmoney.com/api/qt/ulist.np/get?fltt=2&invt=2&fields=f12,f14,f2,f3&secids={s}"
        d = get_json(u)
        name_by_sec = {v: k for k, v in secids.items()}
        for x in d["data"]["diff"]:
            sec = x.get("f12")
            name = x.get("f14") or name_by_sec.get(sec, "")
            val, chg = x.get("f2"), x.get("f3")
            if val in (None, "-", "--"):
                continue
            out.append({"name": name, "value": val, "chg": chg})
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
def _mymemory(text, sl="en", tl="zh-CN"):
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
        return t
    except Exception as e:
        print(f"[TR] mymemory failed: {e}", file=sys.stderr)
        return ""


def _google_gtx(text, sl="en", tl="zh-CN"):
    u = "https://translate.googleapis.com/translate_a/single?client=gtx&sl=" + sl + "&tl=" + tl + "&dt=t&q=" + urllib.parse.quote(text)
    try:
        req = urllib.request.Request(u, headers={"User-Agent": UA})
        raw = urllib.request.urlopen(req, timeout=12, context=ctx).read().decode("utf-8", "ignore")
        data = json.loads(raw)
        return "".join(seg[0] for seg in data[0] if seg[0])
    except Exception as e:
        print(f"[TR] google failed: {e}", file=sys.stderr)
        return ""


def has_cjk(s):
    return any('一' <= ch <= '鿿' for ch in (s or ""))


# 中文译后术语校正（解决 MyMemory 直译误译：座席/线束/集装箱/客服代表 等）
_ZH_GLOSSARY = [
    ("座席线束", "智能体框架"),
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
                return json.load(f)
        except Exception:
            pass
    return {}


def save_trans_cache():
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(os.path.join(STATE_DIR, "trans_cache.json"), "w", encoding="utf-8") as f:
        json.dump(_trans_cache, f, ensure_ascii=False, indent=2)


def translate_zh(text):
    """英文项目介绍翻译成中文；已含中文则原样保留；全部翻译源失败则退回原文（不空）。"""
    if not text:
        return text
    if has_cjk(text):
        # 原生中文描述（如 macrozheng/mall）不翻译，但同样要术语校正 + 精炼，
        # 否则整段营销文案会原样超长输出。
        return _zh_compact(_zh_fix(text))
    key = text.strip()
    if key in _trans_cache:
        return _trans_cache[key]
    zh = _mymemory(key) or _google_gtx(key)
    if not zh:
        zh = key
    zh = _zh_fix(zh)
    zh = _zh_compact(zh)          # 精炼为 v3.2 样例风格的一句话简介
    _trans_cache[key] = zh
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


def dedupe(items, keyfn):
    seen, out = set(), []
    for it in items:
        k = keyfn(it)
        if k not in seen:
            seen.add(k)
            out.append(it)
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

    # --- AI：7 天全量 API 池，按发布时间倒序取未发项 ---
    ai_all = fetch_ai_api()
    ai_sorted = sorted(ai_all, key=lambda x: x.get("publishedAt", ""), reverse=True)
    ai_shown = [a for a in ai_sorted if not is_recent(hist, "ai:" + a["id"], today)][:AI_CAP]

    # --- 开源：日/周/月 + 多语言变体池，筛未发项（即"上次运行以来新增 + 扩展搜索"）---
    repo_pool = []
    if github_html_ok():          # HTML 可达才爬 trending；否则直接走 API，避免长时间重试
        repo_pool = dedupe(
            fetch_trending("daily") + fetch_trending("weekly") + fetch_trending("monthly")
            + fetch_trending("daily", "en") + fetch_trending("daily", "zh"),
            lambda r: r["repo"])
    if not repo_pool:
        # HTML trending 不可达 → 官方 Search API 兜底：近 30 天新锐高星 + 近期活跃高星
        repo_pool = dedupe(
            fetch_search_repos("created:>" + (today - datetime.timedelta(days=30)).isoformat())
            + fetch_search_repos("stars:>3000 pushed:>" + (today - datetime.timedelta(days=7)).isoformat()),
            lambda r: r["repo"])
    repos_shown = [r for r in repo_pool if not is_recent(hist, "gh:" + r["repo"], today)][:OS_CAP]

    # --- Docker：容器/自托管专属真实源（trending 关键词挖 + topics 实时池），排除已进"开源"栏的，筛未发项 ---
    docker_topic_pool = []
    if github_html_ok():
        docker_topic_pool = fetch_topic_repos("docker") + fetch_topic_repos("self-hosted") + fetch_topic_repos("homelab")
    if not docker_topic_pool:
        # topics HTML 不可达 → Search API 按 topic 取高星自托管/容器项目
        docker_topic_pool = dedupe(
            fetch_search_repos("topic:self-hosted") + fetch_search_repos("topic:docker")
            + fetch_search_repos("topic:homelab"),
            lambda r: r["repo"])
    docker_pool = dedupe(split_docker(repo_pool) + docker_topic_pool, lambda r: r["repo"])
    shown_repos = {r["repo"] for r in repos_shown}
    docker_shown = [r for r in docker_pool
                    if (not is_recent(hist, "gh:" + r["repo"], today)) and r["repo"] not in shown_repos][:DOCKER_CAP]

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
    if not macro_shown:
        seen = {m["url"] or m["title"] for m in macro_shown}
        for pg in (1, 2, 3, 4):
            for m in fetch_macro(pg, MACRO_KEYWORDS_BROAD):
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
    L.append(f"> AI {len(ai_shown)} 条 / 开源 {len(repos_shown)} 条 / Docker {len(docker_shown)} 条 / 宏观 {len(macro_shown)} 条 / QDII净值 {len(qdii_nav)} / 财经实时快照见下")
    L.append("")

    L.append("## 一、AI 资讯")
    if ai_shown:
        for i, a in enumerate(ai_shown, 1):
            L.append(f"**{i}. {a['title']}**")
            L.append(f"- 来源：{a['source']}  ·  发布：{fmt_time(a['publishedAt'])}")
            if a["summary"]:
                L.append(f"- 摘要：{a['summary']}")
            L.append(f"- 链接：{a['url']}")
            L.append("")
    else:
        L.append("> AI 热榜近 7 天可发条目均已在窗口内推送过，今日无新增可发（不重复展示）。")
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
    if indices:
        L.append("| 指数 | 点位 | 涨跌幅 | PE(TTM) | PE分位 | PB | PB分位 | 估值水位 |")
        L.append("|---|---|---|---|---|---|---|---|")
        for x in indices:
            name = x["name"]
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
                L.append(f"| {name} | {x['value']} | {fmt_pct(x['chg'])} | {pe} | {pe_pct}·{pel} | {pb} | {pb_pct}·{pbl} | {overall} |")
            else:
                L.append(f"| {name} | {x['value']} | {fmt_pct(x['chg'])} | — | — | — | — | — |")
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
    rss_items = []
    for a in ai_shown:
        desc = a["summary"] or a["title"]
        desc = f"[{fmt_time(a['publishedAt'])}] {desc}"
        rss_items.append(rss_item(a["title"], a["url"], desc, "AI资讯"))
    for r in repos_shown:
        desc = f"{r['desc']} （{r['lang']} ★{r['stars']}）" if r["desc"] else f"（{r['lang']} ★{r['stars']}）"
        rss_items.append(rss_item(f"{r['repo']} ★{r['stars']}", r["url"], desc, "开源"))
    for r in docker_shown:
        desc = r.get("desc") or ""
        rss_items.append(rss_item(f"[Docker] {r['repo']}", r["url"], desc, "Docker"))
    for m in macro_shown:
        t = fmt_time(m.get("ctime"))
        desc = f"[{t}] {m['title']}"
        rss_items.append(rss_item(f"[宏观] {m['title']}", m.get("url") or "https://finance.sina.com.cn/", desc, "宏观"))
    # 市场快照作为一条
    snap = "每日市场快照："
    if indices:
        snap += "；".join(f"{x['name']}{fmt_pct(x['chg'])}" for x in indices) + "。"
    if gold:
        snap += f" {gold['name']}{fmt_pct(gold['chg'])}。"
    rss_items.append(rss_item(f"市场快照 {today_str}", "https://eastmoney.com", snap, "财经"))
    # 估值快照作为一条
    if valuation and indices:
        vlines = []
        for x in indices:
            v = valuation.get(x["name"])
            if v and v.get("pe_pct") is not None:
                vlines.append(f"{x['name']} PE分位{float(v['pe_pct']) * 100:.0f}%({val_level(v['pe_pct'])}) PB分位{float(v['pb_pct']) * 100:.0f}%({val_level(v['pb_pct'])})")
        if vlines:
            rss_items.append(rss_item(f"指数估值 {today_str}", "https://danjuanfunds.com/",
                                     "；".join(vlines) + f"（蛋卷，截至 {val_date}）", "估值"))

    feed = build_rss(rss_items, today_str)
    with open(os.path.join(DOC_DIR, "feed.xml"), "w", encoding="utf-8") as f:
        f.write(feed)

    print(f"[OK] 生成完成 -> {md_path}")
    print(f"[OK] RSS -> {os.path.join(DOC_DIR, 'feed.xml')}")
    print(f"[STAT] AI={len(ai_shown)} 开源={len(repos_shown)} Docker={len(docker_shown)} 宏观={len(macro_shown)} 指数={len(indices)} 板块={len(sectors)} 黄金={'Y' if gold else 'N'} 518880={'Y' if gold_nav else 'N'} QDII净值={len(qdii_nav)}")


def rss_item(title, link, desc, cat):
    title = esc(title)
    desc = esc(desc)
    cat = esc(cat)
    return f"    <item>\n      <title>{title}</title>\n      <link>{esc(link)}</link>\n      <description>{desc}</description>\n      <category>{cat}</category>\n    </item>"


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
