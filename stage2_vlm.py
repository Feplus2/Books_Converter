"""
Stage 2 (VLM 引擎专用薄编排器): 信任优先 + 校验导向

设计稿 wiki/09-stage2-vlm.md（用户已批准）。VLM 的 stage1 多模态逐页直读
已给出 text_level / 图文配对 / 目录先验（vlm_state.db），结构分析不再需要
DeepSeek 四项标注与 OCR 时代的目录检测/页码救援家族——那套规则的预设是
"输入缺页漏标、结构靠猜"，对 VLM 输入只有副作用面（"反向救援"误伤）。

编排（复用 stage2_common 共享层，本模块只做调度与少量规则收口）：
  1. content_list → 标注 blocks（本地映射，零 LLM）+ contd 规则版
  2. 目录先验（vlm_state.db toc_entries）直接作锚点表
  3. _drop_duplicate_pages → _light_metadata_pass（全书唯一 LLM 调用点）
  4. _calibrate_levels → _dedup_anchored_titles → _veto_junk_titles
     → _sink_unanchored_plain → 规则版层级收口（替代 _global_level_pass）
     → _anchor_frontmatter_lexicon → popo.build_tree
  5. 目录先验 ↔ 正文标题交叉校验 + printed_page 偏移校验（只报告不动作）

铁律 0：本模块不存在任何"合成"动作——不合成标题块、不合成页码、不编造
锚点（_rescue_by_page 严禁调用）；校验不通过的去向是"报告"，不是"修补"。
产物与 finish_structure 完全同构（engine="vlm-hybrid"），stage3/QC 零感知。
"""

import json
import logging
import re
import sqlite3
from collections import Counter
from pathlib import Path

import popo
from popo import inference as pi
from stage1_vlm import _toc_region_pages
from stage2_common import (
    _SHAPE_PATTERNS,
    _anchor_frontmatter_lexicon,
    _build_anchors,
    _calibrate_levels,
    _dedup_anchored_titles,
    _drop_duplicate_pages,
    _light_metadata_pass,
    _match_anchor,
    _normalize_title,
    _sink_unanchored_plain,
    _title_shape,
    _veto_junk_titles,
    save_structure,  # noqa: F401  (re-export)
)

logger = logging.getLogger(__name__)


# ── content_list → 标注 blocks（本地映射，零 LLM） ──────────────────────

def _norm_bbox(bbox) -> list | None:
    """千分位 xyxy → 0..1 浮点（与 popo.convert 同约定）；非法返回 None。"""
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        return None
    try:
        vals = [float(v) / 1000.0 for v in bbox]
    except (TypeError, ValueError):
        return None
    vals = [min(max(v, 0.0), 1.0) for v in vals]
    if vals[2] < vals[0] or vals[3] < vals[1]:
        return None
    return vals


def _mk_block(btype, content, page, bbox, source_id, block_id, level=-1):
    return {
        "id": block_id,
        "page": page,
        "type": btype,
        "level": level,
        "content": content,
        "contd": -1,
        "image": -1,
        "bbox": bbox,
        "source_label": btype,
        "source_id": source_id,
    }


def _annotated_blocks(content_list: list) -> list:
    """stage1_vlm content_list → popo 标注 blocks（结构判断已随 text_level 给出）。

    - text 块带 text_level 1–5 → title/level（编=1 章=2 节=3 小节=4 更细=5）；
    - page_footnote 保留——stage3 的章末尾注锚定（$^{marker}$ 内联标记 ↔
      page_footnote 块配对）从 popo blocks 收集，丢弃=脚注全丢；
    - header / page_number 丢弃（stage3 渲染层本就跳过；printed_page 留在
      content_list 供交叉校验，不进 blocks）；
    - image 透传 + caption 块直接挂图文关联（stage1 已配对，无需 LLM）；
    - table 透传（table_body 已含结构）。
    """
    blocks = []
    next_id = 1
    for idx, b in enumerate(content_list):
        btype = str(b.get("type") or "")
        try:
            page = int(b.get("page_idx")) + 1
        except (TypeError, ValueError):
            continue
        bbox = _norm_bbox(b.get("bbox"))
        sid = f"t:{idx}"

        if btype == "text":
            text = (b.get("text") or "").strip()
            if not text:
                continue
            try:
                level = int(b.get("text_level"))
            except (TypeError, ValueError):
                level = -1
            if 1 <= level <= 5:
                blocks.append(_mk_block("title", text, page, bbox, sid,
                                        next_id, level=level))
            else:
                blocks.append(_mk_block("text", text, page, bbox, sid, next_id))
            next_id += 1
        elif btype == "image":
            img = _mk_block("image", None, page, bbox, sid, next_id)
            next_id += 1
            blocks.append(img)
            caps = b.get("image_caption")
            if isinstance(caps, list):
                for n, cap in enumerate(caps):
                    cap = str(cap).strip()
                    if not cap:
                        continue
                    cbbox = [bbox[0], bbox[3], bbox[2], bbox[3]] if bbox else None
                    cb = _mk_block("image_caption", cap, page, cbbox,
                                   f"{sid}.cap{n}", next_id)
                    cb["image"] = img["id"]
                    blocks.append(cb)
                    next_id += 1
        elif btype == "table":
            tb = _mk_block("table", b.get("table_body") or "",
                           page, bbox, sid, next_id)
            tb["table_merge"] = -1
            blocks.append(tb)
            next_id += 1
        elif btype == "page_footnote":
            text = (b.get("text") or "").strip()
            if not text:
                continue
            blocks.append(_mk_block("page_footnote", text, page, bbox,
                                    sid, next_id))
            next_id += 1
        # header / page_number / 其余噪声类型：不进 blocks
    return blocks


# ── contd 规则版（跨页段落拼接，零 LLM） ────────────────────────────────

# 终止形态：终止标点 / 省略号 / 收束引号括号 / 行间公式 / 行尾脚注标记
_END_TERMINAL_RE = re.compile(
    r"(?:[。！？；：.!?;:]|…+|[’”'\"）】」』\)\]]|\$\$?"
    r"|\$\^\{[^{}]*\}\$|[①-⑳㉑-㉟])+\s*$"
)


def _ends_complete(text: str) -> bool:
    """段尾是否已完结（完结=不拼）。"""
    t = (text or "").rstrip()
    if not t:
        return True
    return bool(_END_TERMINAL_RE.search(t))


def _rule_contd(blocks: list) -> int:
    """跨页段落拼接（规则版）：页 p 末个正文 text 块未以终止形态结尾，
    且页 p+1 首个正文 text 块不以列表/编号形状开头 → 页 p 末块 contd 指向
    页 p+1 首块（contd 语义与 stage2_hybrid 一致：值=续块 id，stage3/tree
    据此把续块并入上一段）。宁缺毋滥：拿不准不拼。"""
    pages: dict[int, list] = {}
    for b in blocks:
        if b.get("type") != "page_footnote":
            pages.setdefault(b.get("page", 0), []).append(b)

    n = 0
    for p in sorted(pages):
        nxt = pages.get(p + 1)
        if not nxt:
            continue
        last, first = pages[p][-1], nxt[0]
        if last["type"] != "text" or first["type"] != "text":
            continue
        c1, c2 = last["content"], first["content"]
        if len(c1) < 10 or "\t" in c1 or "\t" in c2:
            continue
        if _ends_complete(c1):
            continue
        if pi.is_list_item(c2):
            continue
        if c1[0].isdigit() and c2[0].isdigit():
            continue
        last["contd"] = first["id"]
        n += 1
    if n:
        logger.info(f"  跨页段落拼接（规则版）: {n} 处")
    return n


def _rule_table_merge(blocks: list) -> int:
    """跨页表格合并（规则版）：popo 六道规则筛查全过的相邻页表对互设
    table_merge。cell_list 留空 = 不做单元格级缝合（那是 LLM 票决的活），
    merge_table_html 仍完成拼行与重复表头剔除。"""
    try:
        candidates = pi.filter_table_merge(blocks)
    except Exception as e:
        logger.warning(f"  跨页表筛查异常（不动作）: {e}")
        return 0
    n = 0
    for mi in candidates:
        t1, t2 = blocks[mi["table1_idx"]], blocks[mi["table2_idx"]]
        t1["table_merge"] = t2["id"]
        t2["table_merge"] = t1["id"]
        t1["cell_list"] = []
        t2["cell_list"] = []
        n += 1
    if n:
        logger.info(f"  跨页表格合并（规则版）: {n} 对")
    return n


# ── 目录先验（vlm_state.db） ────────────────────────────────────────────

def _read_vlm_prior(db_path) -> tuple[list, dict]:
    """vlm_state.db → (toc_entries, pages_json)；不可用 → ([], {})。

    只读打开（mode=ro，绝不动 stage1 缓存）。pages_json 供 _toc_region_pages
    重算目录区（stage1 自己用过的同一函数，非 OCR 时代目录检测家族）。
    """
    if not db_path:
        return [], {}
    path = Path(db_path)
    if not path.exists():
        return [], {}
    try:
        uri = "file:" + path.resolve().as_posix() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        try:
            row = conn.execute(
                "SELECT value FROM book_state WHERE key='toc_entries'").fetchone()
            toc = json.loads(row[0]) if row and row[0] else []
            pages_json = {}
            for p, raw in conn.execute(
                    "SELECT page_idx, raw_json FROM pages"
                    " WHERE status='ok' AND raw_json IS NOT NULL"):
                try:
                    pages_json[int(p)] = json.loads(raw)
                except (TypeError, ValueError):
                    continue
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"  vlm_state.db 读取失败（回退轻量兜底目录）: {e}")
        return [], {}
    toc = [e for e in toc
           if isinstance(e, dict) and (e.get("text") or "").strip()]
    return toc, pages_json


# 索引词条形状：'Weber, Max, 287' / 'Law of Opposites, 54'——词条+逗号+页码串。
# 模型可能把书末索引页当目录页读，先验被索引污染（must_defend 368 条先验里
# 约 350 条索引、Feeling Great 569 条里约 460 条，锚定/映射后制造数百个
# 幻影章）。
_INDEX_ENTRY_RE = re.compile(r",\s*\d[\d\s,–—-]*$")
# 显式索引节边界条目（两书先验实测均含 'Index' 条目且索引词条紧随其后）。
_INDEX_HEADER_RE = re.compile(r"^(index|索引|general index)$", re.I)
# 真目录条目形状（边界后的救生索）：数字/罗马数字/章节前缀、词数（one–
# twelve）、含年份日期、前后页家族词。must_defend 的 four–eight 讲次
# （'four 28 JANUARY 1976 …'）与 FG 的 '20. Should Statements' 实测。
_REAL_ENTRY_RE = re.compile(
    r"^(?:\d+\s*[.、．]|第[一二三四五六七八九十百零〇0-9]+[章节编篇卷部]"
    r"|[IVXLC]+[.、\s]"
    r"|\S+\s+\d{1,2}\s+(?:JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|"
    r"SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER)\b|\S.*\b\d{4}\b"
    r"|(?:foreword|preface|introduction|prologue|epilogue|appendix|bibliography|"
    r"references|conclusion|afterword|glossary|序|前言|序言|后记|附录|参考文献|"
    r"绪论|引言|跋|结语|目录|索引)\b)",
    re.I)


def _alpha_key(s: str) -> str:
    """首字母键（索引排序键）：跳过引号/空白取首个字母；非字母起首返回空。"""
    for ch in (s or "").strip().casefold():
        if ch.isalpha():
            return ch
        if ch.isalnum():
            return ""          # 数字起首：真目录编号，不参与字母序判定
    return ""


def _cut_index_tail(entries: list) -> tuple[list, int]:
    """目录先验的索引污染切除（两级判别）：
    1. 显式边界：最后一个 'Index'/'索引' 条目之后，只保留**真目录形状**的
       条目（数字/罗马数字/章节前缀、one–twelve 词数、日期、前后页家族词；
       且不带页码串尾巴）——must_defend 的 four–eight 讲次（词数+日期）
       落在边界后也必须保下；其余全切。
    2. 无显式边界时回退：字母升序/页码串形状连续尾 ≥8。
    铁律 0：切除数 <8 一律不动作（宁可不切也不错切）。"""
    if not entries:
        return entries, 0
    boundary = -1
    for i, e in enumerate(entries):
        if _INDEX_HEADER_RE.match((e.get("text") or "").strip()):
            boundary = i
    if boundary >= 0:
        kept, n = [], 0
        for i, e in enumerate(entries):
            if i > boundary:
                txt = (e.get("text") or "").strip()
                if not (_REAL_ENTRY_RE.match(txt)
                        and not _INDEX_ENTRY_RE.search(txt)):
                    n += 1
                    continue
            kept.append(e)
        if n >= 8:
            return kept, n
        return entries, 0
    alpha_run = 1
    for i in range(len(entries) - 1, 0, -1):
        a = _alpha_key(entries[i - 1].get("text"))
        b = _alpha_key(entries[i].get("text"))
        if a and b and a <= b:
            alpha_run += 1
        else:
            break
    shape_run = 0
    for i in range(len(entries) - 1, -1, -1):
        if _INDEX_ENTRY_RE.search((entries[i].get("text") or "").strip()):
            shape_run += 1
        else:
            break
    run = max(alpha_run, shape_run)
    if 8 <= run < len(entries):
        return entries[: len(entries) - run], run
    return entries, 0


def _demote_index_region_titles(blocks: list) -> int:
    """书末索引区映射标题降格：末 30% 书页中，连续 ≥2 页每页 ≥15 个标题块的
    区段判为索引区（真标题密度每页个位数；Feeling Great p527-529 每页 95+
    个索引词条被 stage1 标为标题实测），区段内标题块全部降回正文。
    三条件缺一不动作（铁律 0）。"""
    per_page = Counter(b["page"] for b in blocks if b.get("level", -1) > 0)
    if not per_page:
        return 0
    max_page = max(b["page"] for b in blocks)
    dense = sorted(p for p, n in per_page.items()
                   if n >= 15 and p >= max_page * 0.7)
    region: set[int] = set()
    run: list[int] = []
    for p in dense:
        if run and p == run[-1] + 1:
            run.append(p)
        else:
            if len(run) >= 2:
                region |= set(run)
            run = [p]
    if len(run) >= 2:
        region |= set(run)
    if not region:
        return 0
    n = 0
    for b in blocks:
        if b["page"] in region and b.get("level", -1) > 0 and not b.get("_anchored"):
            b["type"] = "text"
            b["level"] = -1
            n += 1
    if n:
        logger.info(f"  索引区降格: {sorted(region)} 页 {n} 个映射标题降回正文"
                    f"（书末索引区，密度判定）")
    return n


# ── 规则版层级一致性收口（替代 _global_level_pass，零 LLM） ─────────────

_FAMILY_RANKS = {name: i for i, (name, _p) in enumerate(_SHAPE_PATTERNS)}
# 全角点/顿号数字起首（'1．物的可支配性'）：_title_shape 落入 plain，
# 语义上是 num_dot 家族（minfa 实测：全角点小节链整体游离在形状栈外）
_NUM_DOT_FULLWIDTH_RE = re.compile(r"^\d+\s*[．、]\s*\S")


def _family_rank(text: str):
    """编号形状家族排名（_SHAPE_PATTERNS 次序，浅→深）；无编号 → None。"""
    shape = _title_shape(text)
    if shape != "plain":
        return _FAMILY_RANKS.get(shape)
    if _NUM_DOT_FULLWIDTH_RE.match((text or "").strip()):
        return _FAMILY_RANKS["num_dot"]
    return None


def _level_consistency_pass(blocks: list) -> int:
    """锚定标题 level 锁定；无锚标题按"相邻锚点约束 + 局部形状栈"收敛。

    两个锚点之间的连续无锚标题段，按编号形状家族的深浅次序
    （第X编>第X章>第X节>一、>（一）>1.>（1）…）从 锚点 level+1 起局部
    嵌套。不做全局形状众数收敛——同一形状家族在全书不同部位可合法处于
    不同深度（民法总论 自然人章 四、/五、 锚定 L5 实测），全局抹平会
    制造父子倒挂空章。只许加深、不许上浮（铁律 0）；形状家族不深于
    所属锚点的（目录漏收的同级/节级标题）不动作；无编号 plain 由
    _sink_unanchored_plain 管，这里不碰。
    """
    titled = [b for b in blocks
              if b.get("type") == "title" and b.get("level", -1) > 0]

    n = 0
    anchor_lv = anchor_r = None
    stack = []  # [(family_rank, level)] 锚点间无锚标题的局部形状栈
    for b in titled:
        r = _family_rank((b.get("content") or "").strip())
        if b.get("_anchored"):
            anchor_lv, anchor_r = b["level"], r
            stack = []
            continue
        if r is None or anchor_lv is None:
            continue  # plain 由 _sink_unanchored_plain 管；首个锚点之前不动作
        while stack and stack[-1][0] >= r:
            stack.pop()
        if stack:
            base = stack[-1][1] + 1
        elif anchor_r is not None and r <= anchor_r:
            stack.append((r, b["level"]))
            continue      # 家族不深于所属锚点 = 目录漏收的同级标题，不动作
        else:
            base = anchor_lv + 1
        if base > b["level"]:
            logger.info(f"    局部形状栈下沉: P{b.get('page')} "
                        f"L{b['level']}→L{base} {(b.get('content') or '')[:30]}")
            b["level"] = base
            n += 1
        stack.append((r, b["level"]))
    if n:
        logger.info(f"  层级一致性收口（规则版）: {n} 个无锚标题按局部形状栈下沉")
    return n


def _demote_empty_titles(blocks: list, toc_entries: list) -> int:
    """空枝标题降格：无锚、无内容子树的标题块降回正文。

    VLM 首票的 text_level 里混着扉页大字/书名副题（'民法学' 之于
    '第一编 民法总论' 扉页）——它们与真标题的唯一区别是"名下无内容"
    （到下一个同级/更浅标题之间没有任何正文/图/表块）。
    锚定（_anchored）与词表锚定（_fm_rule）标题绝不动：空枝在它们是
    上游内容丢失的信号，必须留给 QC 空章报出来，宁可报也不藏。
    锚得上目录条目的也不动（与 _veto_junk_titles 的豁免同理）。
    只降格不晋升；内容作为正文段落保留（失败方向安全）。
    """
    anchors = _build_anchors(toc_entries)
    titled_idx = [i for i, b in enumerate(blocks)
                  if b.get("type") == "title" and b.get("level", -1) > 0]
    n = 0
    for pos, i in enumerate(titled_idx):
        b = blocks[i]
        if b.get("_anchored") or b.get("_fm_rule"):
            continue
        text = (b.get("content") or "").strip()
        if anchors and _match_anchor(text, anchors):
            continue
        end = len(blocks)
        for j in titled_idx[pos + 1:]:
            if 0 < blocks[j].get("level", -1) <= b["level"]:
                end = j
                break
        has_content = any(
            (x.get("type") == "text" and (x.get("content") or "").strip())
            or x.get("type") in ("image", "table")
            for x in blocks[i + 1:end])
        if not has_content:
            logger.info(f"    空枝降格: P{b.get('page')} "
                        f"L{b['level']} {text[:30]}")
            b["type"] = "text"
            b["level"] = -1
            n += 1
    if n:
        logger.info(f"  空枝标题降格: {n} 个无内容标题块降回正文")
    return n


# ── 交叉校验（只报告不动作） ────────────────────────────────────────────

def _cross_check(blocks: list, toc_entries: list, content_list: list) -> dict:
    """目录先验 ↔ 正文标题对照：未锚上 / 锚定层级不一致 / 印刷页码偏移异常。

    印刷页→扫描页偏移用 content_list 的 page_number 块（stage1 每页
    printed_page 直读）逐页观测，按锚定块扫描页取最近邻局部偏移逐条比对
    （偏移随页慢漂移，且前页/正文页码 regime 不同，全局估计会误报）。
    """
    report = {"unanchored": [], "level_mismatch": [], "page_offset_anomaly": []}
    anchors = _build_anchors(toc_entries)
    if not anchors:
        return report
    titled = [b for b in blocks
              if b.get("type") == "title" and b.get("level", -1) > 0]

    # 条目按 (印刷页, level) 分组（完整/剥尾双形态视为同一条目，qc 同口径）
    groups: dict = {}
    for a in anchors:
        groups.setdefault((a[3], a[1]), []).append(a)

    offsets = []  # (扫描页, 扫描页 - 印刷页) 逐页观测
    for b in content_list:
        if b.get("type") != "page_number":
            continue
        t = (b.get("text") or "").strip()
        if t.isdigit():
            try:
                scan = int(b.get("page_idx")) + 1
                offsets.append((scan, scan - int(t)))
            except (TypeError, ValueError):
                continue
    # 印刷↔扫描偏移随页慢漂移（minfa 实测 6→22），且前页/正文可能各有
    # 一套页码 regime（印刷值在不同 regime 下重复）——按锚定块的扫描页
    # 取最近邻局部偏移，而不是按印刷值或全局中位数。
    offsets.sort()

    def _expected(printed: int, scan_page: int) -> int | None:
        if len(offsets) < 5 or not scan_page:
            return None
        _sc, off = min(offsets, key=lambda o: abs(o[0] - scan_page))
        return printed + off

    for (pg, lv), forms in sorted(groups.items(),
                                  key=lambda kv: (kv[0][0] is None, kv[0][0] or 0,
                                                  kv[0][1])):
        hit = None
        for b in titled:
            if _match_anchor((b.get("content") or "").strip(), forms):
                hit = b
                break
        if hit is None:
            report["unanchored"].append(
                {"text": forms[0][2], "level": lv, "page": pg})
            continue
        if hit.get("level") != lv:
            report["level_mismatch"].append(
                {"text": forms[0][2], "entry_level": lv,
                 "block_level": hit.get("level"), "page": hit.get("page")})
        if pg is not None:
            # 位置校验只信归一化精确命中：模糊兜底会把 2 字差的兄弟条目
            # （'第三节 …内部关系' ↔ '第四节 …外部关系'）错配到错误页码；
            # 精确命中 ≠1（未锚上但模糊命中/同名条目歧义）→ 不动作。
            form_keys = {a[0] for a in forms}
            exact = [b for b in titled
                     if _normalize_title((b.get("content") or "").strip())
                     in form_keys]
            if len(exact) == 1:
                expected = _expected(pg, exact[0].get("page") or 0)
                if expected is not None \
                        and abs((exact[0].get("page") or 0) - expected) > 2:
                    report["page_offset_anomaly"].append(
                        {"text": forms[0][2], "printed_page": pg,
                         "expected_scan_page": expected,
                         "actual_scan_page": exact[0].get("page")})

    for key, rows in report.items():
        if rows:
            logger.warning(f"  交叉校验 {key}: {len(rows)} 条"
                           f"（只报告不动作）: "
                           f"{[r.get('text', '')[:16] for r in rows[:5]]}")
    return report


# ── 主入口 ──────────────────────────────────────────────────────────────

def analyze_structure_vlm(content_list: list, book_name: str, work_dir: str,
                          vlm_state_db=None, progress=None) -> dict:
    """VLM 引擎的结构分析主入口（产物与 finish_structure 完全同构）。

    vlm_state_db: <work_dir>/vlm/vlm_state.db 路径（目录先验 + 页 JSON）；
    不存在/为空 → 回退 _light_metadata_pass 提取的目录（记日志）。
    """
    _report = progress or (lambda *a, **kw: None)
    work_dir = Path(work_dir)

    # 1. content_list → 标注 blocks（本地映射，零 LLM）
    _report("VLM-Structure: 本地映射标注 blocks...")
    blocks = _annotated_blocks(content_list)
    _demote_index_region_titles(blocks)
    _rule_contd(blocks)
    _rule_table_merge(blocks)

    # 2. 目录先验（vlm_state.db → toc_entries 直接作锚点表；
    #    跳过一切目录检测/兜底/伪造判定）
    prior, pages_json = _read_vlm_prior(vlm_state_db)

    # 3. 重页丢弃 → 轻量兜底（全书唯一 LLM 调用点：metadata + 前后页分类）
    blocks = _drop_duplicate_pages(blocks)
    popo_titles = [
        (b["page"], b["level"], (b.get("content") or "").strip())
        for b in blocks if b.get("level", -1) > 0
    ]
    light = _light_metadata_pass(content_list, book_name,
                                 popo_titles=popo_titles, progress=_report)
    if prior:
        prior, n_cut = _cut_index_tail(prior)
        if n_cut:
            logger.info(f"  目录先验切除索引尾 {n_cut} 条（书末索引页污染，"
                        f"剩 {len(prior)} 条）")
        logger.info(f"  目录先验: vlm_state.db {len(prior)} 条直接作锚")
        light["toc_entries"] = prior
        toc_source = "vlm"
    else:
        logger.info("  目录先验回退: vlm_state.db 不存在/为空，"
                    "使用轻量兜底提取的目录")
        light["toc_entries"], n_cut = _cut_index_tail(
            light.get("toc_entries", []))
        if n_cut:
            logger.info(f"  兜底目录切除索引尾 {n_cut} 条")
        toc_source = None

    # 目录区页（toc_pages）：优先按 stage1 自己的区域判定重算（页 JSON 里
    # 的 toc 块密度，非 OCR 检测家族）；db 不可用则回退轻量兜底的前页分类
    # toc 区间（LLM 页分类信号）。目录页上的条目块须降格，否则与正文真标题
    # 同锚竞争、把章题顶掉（目录区条目的 text_level=1 是 stage1 喂给规则
    # 引擎的 MinerU 形态，不是结构判断）。
    toc_pages = _toc_region_pages(pages_json) if pages_json else set()
    if not toc_pages:
        for f in light["front_matter"]:
            if f.get("type") == "toc":
                try:
                    start, end = int(f.get("page_start", 0)), int(f.get("page_end", 0))
                except (TypeError, ValueError):
                    continue
                toc_pages |= set(range(max(start - 1, 0), max(end, 0)))
    toc_pages_1b = {p + 1 for p in toc_pages}

    # 4. 锚定校正 → 守卫三件套（只降格不晋升） → 规则版层级收口
    _report("VLM-Structure: TOC 锚定校正层级...")
    _calibrate_levels(blocks, light["toc_entries"], toc_pages=toc_pages_1b)
    _dedup_anchored_titles(blocks, light["toc_entries"])
    _veto_junk_titles(blocks, light["toc_entries"])
    _sink_unanchored_plain(blocks)
    _level_consistency_pass(blocks)
    _anchor_frontmatter_lexicon(blocks, toc_pages=toc_pages_1b)
    _demote_empty_titles(blocks, light["toc_entries"])

    # 5. 交叉校验 + printed_page 偏移校验（只报告不动作）
    cross_check = _cross_check(blocks, light["toc_entries"], content_list)

    blocks_path = work_dir / "popo_blocks.json"
    with open(blocks_path, "w", encoding="utf-8") as f:
        json.dump(blocks, f, ensure_ascii=False)

    _report("VLM-Structure: 构建文档树...")
    tree = popo.build_tree(blocks)

    return {
        "engine": "vlm-hybrid",
        "metadata": light["metadata"],
        "front_matter": light["front_matter"],
        "back_matter": light["back_matter"],
        "noise_ranges": [],
        "tree": tree,
        "toc_entries": light["toc_entries"],
        "toc_source": toc_source,
        "popo_blocks_file": blocks_path.name,
        "cross_check": cross_check,
    }


__all__ = ["analyze_structure_vlm", "save_structure"]
