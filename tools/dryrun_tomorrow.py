# -*- coding: utf-8 -*-
"""明日供给量演练（只读）：不写任何 state，只统计『明日可发的未发项』是否够满额。"""
import os, sys, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import digester as D

hist = D.load_history()
tomorrow = datetime.date.today() + datetime.timedelta(days=1)
print(f"演练日期 = {tomorrow}（当前历史 {len(hist)} 条，7天窗口）\n")

# --- AI ---
ai_all = D.fetch_ai_api()
ai_sorted = sorted(ai_all, key=lambda x: x.get("publishedAt", ""), reverse=True)
ai_fresh = [a for a in ai_sorted if not D.is_recent(hist, "ai:" + a["id"], tomorrow)]
print(f"AI      池={len(ai_all):3d}  未发={len(ai_fresh):3d}  取={min(len(ai_fresh), D.AI_CAP)}/{D.AI_CAP}")

# --- 开源 ---
repo_pool = D.dedupe(
    D.fetch_search_repos("created:>" + (tomorrow - datetime.timedelta(days=30)).isoformat() + " stars:>1000")
    + D.fetch_search_repos("stars:>3000 pushed:>" + (tomorrow - datetime.timedelta(days=7)).isoformat()),
    lambda r: r["repo"])
fresh = [r for r in repo_pool if not D.is_recent(hist, "gh:" + r["repo"], tomorrow)]
shown = D.diversify(fresh, limit=D.OS_CAP)
print(f"开源    池={len(repo_pool):3d}  未发={len(fresh):3d}  多样性后={len(shown)}/{D.OS_CAP}")

# --- Docker ---
docker_pool = D.dedupe(
    D.fetch_search_repos("topic:self-hosted")
    + D.fetch_search_repos("topic:docker")
    + D.fetch_search_repos("topic:homelab"),
    lambda r: r["repo"])
docker_all = D.dedupe(D.split_docker(repo_pool) + docker_pool, lambda r: r["repo"])
dfresh = [r for r in docker_all
          if not D.is_recent(hist, "gh:" + r["repo"], tomorrow)
          and r["repo"] not in {x["repo"] for x in shown}]
dshown = D.diversify(dfresh, limit=D.DOCKER_CAP)
print(f"Docker  池={len(docker_all):3d}  未发={len(dfresh):3d}  多样性后={len(dshown)}/{D.DOCKER_CAP}")

# --- 宏观 ---
macro = D.fetch_macro(1)
mfresh = [m for m in macro if not D.is_recent(hist, "macro:" + (m["url"] or m["title"]), tomorrow)]
print(f"宏观    首屏={len(macro):3d}  首屏未发={len(mfresh):3d}  上限={D.MACRO_CAP}（不足会自动深翻页+宽词扩展）")

print("\n结论：", "各栏供给充足，明日不会空栏" if len(ai_fresh) >= D.AI_CAP
      and len(shown) >= D.OS_CAP and len(dshown) >= D.DOCKER_CAP else "有栏位不足，需检查")
