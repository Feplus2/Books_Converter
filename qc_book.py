#!/usr/bin/env python3
"""qc_book.py — 结构体检表：对一本书的转换产物做客观质检。

用法:
    python qc_book.py <work_dir> [<work_dir2> ...]
    python qc_book.py "D:\\My_Library\\民法总论\\民法总论 (杨代雄)..."

检查项（按用户定的优先级）：
  🔴 内容完整（绝对不可容忍丢失）：content_list 非噪声块是否全部进入
     popo blocks；页码覆盖是否有无法解释的空洞
  🔴 结构正确：目录条目是否都有正文标题锚上（标题不能丢）、幻影合成块、
     空章、层级断跳
  🟡 次级：重复标题、页脚注、降级 metadata、无目录条目
退出码：有红牌 = 1，仅黄牌 = 0（供批量跑书器汇总）。
"""

import glob
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from stage2_common import _build_anchors, _match_anchor, _normalize_title  # noqa

_SKIP_TYPES = {"header", "footer", "page_number", "aside_text",
               "discarded", "chart"}


def _load_json(path: Path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _find_artifacts(work_dir: Path):
    arts = {"work_dir": work_dir}
    sp = work_dir / "structure.json"
    arts["structure"] = _load_json(sp) if sp.exists() else None
    pp = work_dir / "popo_blocks.json"
    arts["popo_blocks"] = _load_json(pp) if pp.exists() else []

    # content_list 可能与 popo_blocks 不配套（同一工作目录先后跑过两个引擎，
    # structure/popo_blocks 来自最后一次运行）。逐个引擎目录算"非噪声块
    # 覆盖率"，取缺失最少的为配套产物。
    best = None
    src_ids = set()
    n_engines = 0
    for clf in sorted(glob.glob(str(work_dir / "*" / "*content_list*.json"))):
        n_engines += 1
        cl = _load_json(Path(clf))
        engine = Path(clf).parent.name
        if arts["popo_blocks"]:
            src_ids = set()
            for b in arts["popo_blocks"]:
                m = re.match(r".*:(\d+)(?:\.\w+)?$", str(b.get("source_id") or ""))
                if m:
                    src_ids.add(int(m.group(1)))
            missing = sum(1 for i, b in enumerate(cl)
                          if b.get("type") not in _SKIP_TYPES and i not in src_ids)
        else:
            missing = 0
        if best is None or missing < best[2]:
            best = (cl, engine, missing, src_ids)
    if best:
        arts["content_list"], arts["engine"], _, arts["src_ids"] = best
    else:
        arts["content_list"], arts["engine"], arts["src_ids"] = [], None, set()
    arts["n_engines"] = n_engines
    return arts


def check_book(work_dir: str) -> dict:
    work_dir = Path(work_dir)
    arts = _find_artifacts(work_dir)
    cl, structure, popo = arts["content_list"], arts["structure"], arts["popo_blocks"]
    red, yellow, notes = [], [], []

    if not cl:
        return {"book": work_dir.name, "verdict": "ERROR",
                "red": ["无 content_list，未运行 Stage 1"], "yellow": [],
                "stats": {}}

    total_pages = max((b.get("page_idx", 0) for b in cl), default=0) + 1
    n_cl_real = sum(1 for b in cl if b.get("type") not in _SKIP_TYPES)
    stats = {"pages": total_pages, "cl_blocks": len(cl),
             "cl_real": n_cl_real, "engine": arts["engine"]}
    if arts["n_engines"] > 1:
        yellow.append(f"工作目录有 {arts['n_engines']} 套引擎产物，"
                      f"按覆盖率自动配套为 {arts['engine']} 版")

    # ── 内容完整：非噪声、非空块必须全部进入 popo blocks ──
    # 合法解释：重页丢弃（缺失集中在 ≤3 页）、标题去重（缺失块文本与某
    # 标题同文，或去标点/前导编号后是其前缀——缩写去重移除的块）。
    if popo:
        src_ids = arts["src_ids"]

        def _core(text: str) -> str:
            t = _normalize_title(text or "")
            t = re.sub(r"^\d+[.、|]?\s*", "", t)
            t = re.sub(r"^第[一二三四五六七八九十百零〇0-9]+[章节编篇卷部]", "", t)
            return re.sub(r"[^\w]", "", t)

        titled_keys = {_normalize_title(b.get("content") or "")
                       for b in popo if b.get("type") == "title"}
        titled_cores = {_core(b.get("content") or "")
                        for b in popo if b.get("type") == "title"}
        titled_cores.discard("")

        def _explained(b) -> bool:
            k = _normalize_title(b.get("text") or "")
            if k and k in titled_keys:
                return True
            # 碎块被锚点富化成完整标题（'7 JANUARY 1976' → 'one 7 JANUARY 1976'）
            # 后被去重：缺失块文本是某标题的子串即可解释
            if len(k) >= 4 and any(k in tk for tk in titled_keys):
                return True
            c = _core(b.get("text") or "")
            return bool(c) and any(tc.startswith(c) for tc in titled_cores)

        missing_real = [b for i, b in enumerate(cl)
                        if b.get("type") not in _SKIP_TYPES
                        and i not in src_ids
                        and ((b.get("text") or "").strip()
                             or b.get("type") in ("image", "table"))]
        unexplained = [b for b in missing_real if not _explained(b)]
        stats["missing_real"] = len(unexplained)
        pages_involved = {b.get("page_idx") for b in unexplained}
        if len(unexplained) > 10 and len(pages_involved) > 3:
            red.append(f"内容丢失: {len(unexplained)} 个非空内容块未进入结构处理"
                       f"（类型: {sorted({b.get('type') for b in unexplained})}）")
        elif unexplained:
            yellow.append(f"{len(unexplained)} 个非空块未进入结构处理"
                          f"（重页丢弃/标题去重可解释）")
    else:
        yellow.append("无 popo_blocks.json（Stage 2 未运行？）")

    # ── 结构正确 ──
    if structure:
        toc = structure.get("toc_entries", [])
        meta = structure.get("metadata", {})
        stats["toc_entries"] = len(toc)
        stats["engine_s2"] = structure.get("engine")

        if not toc:
            red.append("无目录条目（轻量兜底降级？）→ 锚点体系失效，"
                       "结构只能靠首票")
        if meta.get("title") == work_dir.name and not meta.get("authors"):
            yellow.append("metadata 为降级值（书名=文件名且无作者）")

        titled = [b for b in popo
                  if b.get("type") == "title" and b.get("level", -1) > 0]
        stats["titled"] = len(titled)

        # 标题不能丢：每个目录条目都应有正文标题锚上（目录页自身除外）。
        # 锚点按条目分组（同印刷页同层级的完整/剥尾形态视为同一条目，
        # 任一形态命中即算锚上）。
        if toc and titled:
            anchors = _build_anchors(toc)
            groups = {}
            for a in anchors:
                groups.setdefault((a[3], a[1]), []).append(a)
            unanchored = []
            fuzzy_rescued = [b for b in popo if b.get("rescued") == "page_fuzzy"]
            for (pg, _lv), forms in groups.items():
                hit = any(
                    _match_anchor((b.get("content") or "").strip(), forms)
                    for b in titled)
                if not hit and fuzzy_rescued:
                    # 系列块位置晋升（'答学友问1' 之于 '答学友问'）：
                    # 严格匹配被系列守卫拦截，但页码救援已按位置验证晋升
                    from stage2_common import _edit_distance_le
                    hit = any(
                        any(_edit_distance_le(
                                _normalize_title(b.get("content") or ""),
                                f[0], 2) <= 2 for f in forms)
                        for b in fuzzy_rescued)
                if not hit:
                    unanchored.append(forms[0][2])
            stats["anchor_hit"] = f"{len(groups) - len(unanchored)}/{len(groups)}"
            if unanchored:
                red.append(f"目录条目未锚上正文标题 {len(unanchored)} 条"
                           f"（可能丢标题）: {unanchored[:5]}")

        # 幻影合成块（页码救援合成，非晋升已有块）
        synth = [b for b in popo if b.get("rescued") == "toc_page"]
        stats["synth_titles"] = len(synth)
        if synth:
            red.append(f"幻影合成标题 {len(synth)} 个: "
                       f"{[(b.get('content') or '')[:20] for b in synth[:5]]}")

        # 重复标题（同文、同级、相邻页 ±1）
        seen, dups = {}, 0
        for b in titled:
            k = _normalize_title(b.get("content") or "")
            if not k:
                continue
            for pg in (b.get("page", 0) - 1, b.get("page", 0), b.get("page", 0) + 1):
                if (k, b.get("level")) in seen.get(pg, set()):
                    dups += 1
                    break
            else:
                seen.setdefault(b.get("page", 0), set()).add((k, b.get("level")))
        stats["dup_titles"] = dups
        if dups:
            yellow.append(f"重复标题 {dups} 个（同文同级相邻页）")

        # 空章（标题节点：自身无内容、无正文后代、无子标题）
        tree = structure.get("tree") or {}

        def _node_has_content(node) -> bool:
            if (node.get("content") or "").strip():
                return True
            for g in node.get("children", []):
                if not isinstance(g, dict):
                    continue
                if g.get("type") == "page_footnote":
                    continue
                if g.get("level", -1) <= 0 and (g.get("content") or "").strip():
                    return True
                if _node_has_content(g):
                    return True
            return False

        def _empty_chapters(node, out):
            for c in node.get("children", []):
                if not isinstance(c, dict):
                    continue
                if c.get("level", -1) > 0 and not _node_has_content(c):
                    out.append(c.get("title") or c.get("type"))
                _empty_chapters(c, out)
        empties = []
        _empty_chapters(tree, empties)
        stats["empty_chapters"] = len(empties)
        if empties:
            yellow.append(f"空章 {len(empties)} 个: "
                          f"{[e[:20] for e in empties[:5]]}")

        # 页码覆盖：popo blocks 实际覆盖的页 + 前后页范围之外的连续空洞
        # （≥4 连续页无块才报，防空白页/插图页误报；≥10 页红牌）
        covered = set(b.get("page", 0) for b in popo)
        for rng in (structure.get("front_matter", [])
                    + structure.get("back_matter", [])):
            for p in range(rng.get("page_start", 0), rng.get("page_end", 0) + 1):
                covered.add(p)
        runs, run = [], []
        for p in range(1, total_pages + 1):
            if p not in covered:
                run.append(p)
            elif run:
                runs.append(run)
                run = []
        if run:
            runs.append(run)
        big_gaps = [r for r in runs if len(r) >= 4]
        if any(len(r) >= 10 for r in big_gaps):
            red.append(f"连续覆盖空洞: {['%d-%d' % (r[0], r[-1]) for r in big_gaps]}")
        elif big_gaps:
            yellow.append(f"连续覆盖空洞: "
                          f"{['%d-%d' % (r[0], r[-1]) for r in big_gaps]}")
    else:
        red.append("无 structure.json（Stage 2 未运行或失败）")

    n_footnotes = sum(1 for b in cl if b.get("type") == "page_footnote")
    stats["footnotes"] = n_footnotes

    verdict = "RED" if red else ("YELLOW" if yellow else "GREEN")
    return {"book": work_dir.name, "engine": arts["engine"],
            "verdict": verdict, "red": red, "yellow": yellow, "stats": stats}


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    worst = 0
    for wd in sys.argv[1:]:
        r = check_book(wd)
        icon = {"GREEN": "🟢", "YELLOW": "🟡", "RED": "🔴"}.get(r["verdict"], "❓")
        name = r["book"][:40]
        print(f"\n{icon} {name}  [engine={r.get('engine')}]")
        for k, v in r.get("stats", {}).items():
            print(f"    {k}: {v}")
        for msg in r.get("red", []):
            print(f"    🔴 {msg}")
        for msg in r.get("yellow", []):
            print(f"    🟡 {msg}")
        if r["verdict"] == "RED":
            worst = 1
    sys.exit(worst)


if __name__ == "__main__":
    main()
