# -*- coding: utf-8 -*-
"""简介回填翻译脚本（翻译源临时故障后的补救）

用途：MyMemory 等翻译源某天整体不可用时，日报里的项目简介会降级为英文原文。
      等翻译源恢复后，用本脚本把指定 md 中仍是英文的「- 简介：」行回填为中文，
      **不重跑管道、不消耗去重历史**（那批项目已标记为已发，重跑不会再出现）。

用法：
    python _backup/retranslate_md.py output/2026-09-08.md
    python _backup/retranslate_md.py            # 默认处理 output/ 下最新一份日报

行为：
    - 只处理汉字占比 <12% 的简介行；已是中文的不动。
    - 任一项目翻译失败 → 保留原文，不影响其它行。
    - 成功后原地覆盖 md，并打印回填条数。
"""
import os, sys, io, re, glob

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import digester as D


def cjk_ratio(s):
    t = re.sub(r"\s+", "", s or "")
    return sum(1 for c in t if "一" <= c <= "鿿") / len(t) if t else 0.0


def main():
    md = sys.argv[1] if len(sys.argv) > 1 else sorted(glob.glob(
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output", "*.md")))[-1]
    D._trans_cache.clear()
    D._trans_cache.update(D.load_trans_cache())

    lines = io.open(md, encoding="utf-8").read().splitlines()
    fixed, failed = 0, 0
    for i, ln in enumerate(lines):
        if not ln.startswith("- 简介："):
            continue
        src = ln[len("- 简介："):].strip()
        if not src or cjk_ratio(src) >= 0.12:
            continue
        zh = D.translate_zh(src)
        if zh and zh != src and cjk_ratio(zh) >= 0.12:
            lines[i] = f"- 简介：{zh}"
            fixed += 1
        else:
            failed += 1
    if fixed:
        io.open(md, "w", encoding="utf-8").write("\n".join(lines) + "\n")
        D.save_trans_cache()
    print(f"回填 {fixed} 条，仍失败 {failed} 条 -> {md}")


if __name__ == "__main__":
    main()
