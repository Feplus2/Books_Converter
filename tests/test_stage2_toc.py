"""stage2_common 目录处理回归测试（《必须保卫社会》翻车案例）。

背景：该书的目录页码被 PaddleOCR 排成独立 aside_text 数字块（与条目分离），
LLM 提取 toc_entries 时瞎编了等差页码 → 页码救援按错误偏移在正文中合成
幻影标题块，文档树被切碎。修复：
1. _repair_toc_pages  —— 按 bbox y 坐标同行重配真实页码；
2. _rescue_by_page    —— 偏移众数 <3 票（页码不可信特征）时放弃救援。

运行: python tests/test_stage2_toc.py   （或 pytest tests/）
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stage2_common import (  # noqa: E402
    _repair_toc_pages,
    _rescue_by_page,
    _roman_to_int,
)


def _blk(type_, text, page_idx, y1, y2):
    return {"type": type_, "text": text, "page_idx": page_idx,
            "bbox": [100, y1, 900, y2]}


# ──────────────────────────────────────────────────────────────
# _roman_to_int
# ──────────────────────────────────────────────────────────────

def test_roman_to_int():
    assert _roman_to_int("ix") == 9
    assert _roman_to_int("XV") == 15
    assert _roman_to_int("vii") == 7
    assert _roman_to_int("xiv") == 14
    assert _roman_to_int("?") is None


def test_chunk_pages_adaptive():
    """PaddleOCR 自适应分片：44MB/561页的书应单片提交；大体积书按 45MB 切。"""
    from stage1_paddleocr import _chunk_pages
    # 44.3MB / 561 页 ≈ 81KB/页 → 单片 ~553 页上限，561 页书一次提交够吗？
    n = _chunk_pages(int(44.3 * 1024 * 1024), 561)
    assert n >= 500           # 一片（或接近一片）装下整本
    # 200MB / 1000 页 ≈ 205KB/页 → 每片 ~224 页
    n2 = _chunk_pages(200 * 1024 * 1024, 1000)
    assert 200 <= n2 <= 250
    # 小文件直接顶到 1000 页上限
    assert _chunk_pages(1024, 5000) == 1000
    # 巨页（每页 60MB）至少 1 页/片
    assert _chunk_pages(60 * 1024 * 1024, 1) == 1


def test_parse_toc_array():
    """紧凑目录数组 → toc_entries 字典，容错坏项。"""
    from stage2_common import _parse_toc_array
    data = [["第一编 总则", 1, 1], ["第一章 民法概念论", "2", "3"],
            ["坏项"], ["", 1, 5], ["第五章 x", 0, 9], "junk"]
    out = _parse_toc_array(data)
    assert out == [{"text": "第一编 总则", "level": 1, "page": 1},
                   {"text": "第一章 民法概念论", "level": 2, "page": 3}]
    assert _parse_toc_array({"not": "list"}) == []


def test_unescape_markdown_chars():
    """PaddleOCR 的 markdown 转义在数学区外解掉（'1\\.' → '1.'、'\\]' → ']'），
    数学区内的 LaTeX 命令不动。"""
    from stage1_paddleocr import _convert_scripts_outside_math as conv
    assert conv("1\\. 申请宣告死亡的要件。") == "1. 申请宣告死亡的要件。"
    assert conv("参见\\[德\\]梅迪库斯") == "参见[德]梅迪库斯"
    assert conv("$a\\_b$ 不动，1\\. 要动") == "$a\\_b$ 不动，1. 要动"


def test_normalize_strips_footnote_mark_and_decor():
    """标题键剥离上标脚注标记与法式装饰前缀（只影响匹配，不动原文）。"""
    from stage2_common import _normalize_title as nt
    assert nt("人名索引 $^{①}$") == "人名索引"
    assert nt("— X. — La crise des flèvres") == "lacrisedesflèvres"
    assert nt("— II. — Une conscience politique") == "uneconsciencepolitique"
    # 全半角括号统一（病例 037）：目录半角（义位系统） ↔ 正文全角（义位系统）
    assert nt("第七章 语义场（义位系统）（下）") == nt("第七章 语义场(义位系统)(下)")
    assert nt("第七章\u3000语义场（义位系统）（下）") == nt("第七章 语义场(义位系统)(下)")
    # unicode 下标 ↔ 数学形态下标 ↔ 平写 三形态归一（病例 046，机械手册
    # '15 Yₓ 形密封圈' 目录锚点落空实测）：\mathrm 包装只留内容
    assert nt("15.1 孔用 Yₓ 形密封圈") == nt(r"15.1 孔用 Y$_{\mathrm{X}}$ 形密封圈")
    assert nt("15 Yₓ 形密封圈") == nt("15 Yx 形密封圈")
    assert nt("₆₀ 下标数字") == nt("60 下标数字")


def test_anchor_math_delimiters_and_quotes():
    """公式定界符与弯直引号不影响锚定：
    '一、$f(x)=..$型' ↔ '$$ 一、f(x)=.. 型 $$'（公式节标题）、
    弯撇号长标题 ↔ 直撇号大写标题（Born a Crime ch14）。"""
    from stage2_common import _build_anchors, _match_anchor
    anchors = _build_anchors([
        {"text": "一、$f(x)=e^{\\lambda x}P_{m}(x)$型", "level": 3, "page": 300},
        {"text": "Chapter 14: A Young Man’s Long Education in Affairs of the Heart",
         "level": 1, "page": 191},
        {"text": "Chapter 1: Run", "level": 1, "page": 9},
    ])
    m = _match_anchor("$$ 一、f(x)=e^{\\lambda x}P_{m}(x) 型 $$", anchors)
    assert m is not None and m[1] == 3
    m2 = _match_anchor("A YOUNG MAN'S LONG EDUCATION IN AFFAIRS OF THE HEART", anchors)
    assert m2 is not None and m2[1] == 1
    m3 = _match_anchor("RUN", anchors)          # 3 字母尾部匹配
    assert m3 is not None and m3[1] == 1


def test_anchor_generic_tail_word_blocked():
    """幻影章守卫（病例 034，用户批准）：单个通用小节名不得走尾部匹配——
    'Exercises' 不得尾配 '12 Solutions to exercises'；
    但 'RUN'→'Chapter 1: Run'、'权利主体'→'第二编 权利主体' 等专名尾部匹配保留；
    通用词精确命中不受影响。"""
    from stage2_common import _build_anchors, _match_anchor
    anchors = _build_anchors([
        {"text": "12 Solutions to exercises", "level": 1, "page": 266},
        {"text": "Chapter 1: Run", "level": 1, "page": 9},
        {"text": "第二编 权利主体", "level": 1, "page": 89},
        {"text": "Exercises", "level": 2, "page": 41},
    ])
    # 幻影场景：单个通用词尾配长条目 → 拒绝（只剩同名的精确命中 page 41）
    m0 = _match_anchor("Exercises", anchors)
    assert m0 is not None and m0[3] == 41, f"通用词不得尾配长条目: {m0}"
    # 无同名精确命中时：尾配/子串双路都被拒 → None
    anchors_only12 = _build_anchors([{"text": "12 Solutions to exercises", "level": 1, "page": 266}])
    assert _match_anchor("Exercises", anchors_only12) is None
    # 专名尾部匹配保留
    m = _match_anchor("RUN", anchors)
    assert m is not None and m[3] == 9
    m2 = _match_anchor("权利主体", anchors)
    assert m2 is not None and m2[3] == 89
    # 通用词精确命中（rule 1）不受影响
    m3 = _match_anchor("Exercises", _build_anchors([{"text": "Exercises", "level": 2, "page": 41}]))
    assert m3 is not None and m3[3] == 41


def test_anchor_date_shape_key():
    """日期强键（自迭代批·必须保卫社会）：纯日期块锚定到含同日期串的条目；
    非日期块不受影响；歧义（同长双条目）不动作。"""
    from stage2_common import _build_anchors, _match_anchor
    anchors = _build_anchors([
        {"text": "one 7 JANUARY 1976 What is a lecture? - Subjugated knowledges.",
         "level": 1, "page": 1},
        {"text": "two 14 JANUARY 1976 War and power.", "level": 1, "page": 23},
        {"text": "21 JANUARY 1976 The social contract.", "level": 1, "page": 43},
    ])
    m = _match_anchor("7 JANUARY 1976", anchors)
    assert m is not None and m[3] == 1
    m2 = _match_anchor("14 January 1976", anchors)
    assert m2 is not None and m2[3] == 23
    # 非日期形块不触发
    assert _match_anchor("What is a lecture?", anchors) is None
    # 歧义：两条同长条目含同日期（长摘要让子串规则先出局）→ 不动作
    dup = _build_anchors([
        {"text": "one 7 JANUARY 1976 What is a lecture? - Subjugated knowledges. - Historical knowledge of struggles.",
         "level": 1, "page": 1},
        {"text": "two 7 JANUARY 1976 War and power. - Philosophy and the limits of power. - Law and royal power.",
         "level": 1, "page": 9},
    ])
    assert _match_anchor("7 JANUARY 1976", dup) is None


def test_anchor_fuzzy_short_key():
    """系列守卫：'答学友问1' 不得模糊命中 '答学友问'（它是系列另一项）；
    但 LLM 笔误 'PRÉSPACE'（8 字差 2）要能命中 'PRÉFACE'。"""
    from stage2_common import _build_anchors, _match_anchor
    anchors = _build_anchors([{"text": "答学友问", "level": 1, "page": 317}])
    assert _match_anchor("答学友问1", anchors) is None
    anchors2 = _build_anchors([{"text": "PRÉFACE", "level": 1, "page": 5}])
    m = _match_anchor("PRÉSPACE", anchors2)
    assert m is not None and m[1] == 1


def test_anchor_truncated_entry_prefix():
    """目录条目被 LLM 截断（'9.2.1 Kau'）：锚点是块的前缀也要命中；
    短锚点（'1.1'）不得用此前缀规则误配 '1.1.2'。"""
    from stage2_common import _build_anchors, _match_anchor
    anchors = _build_anchors([
        {"text": "9.2.1 Kau", "level": 3, "page": 150},
        {"text": "1.1 Overview", "level": 2, "page": 10},
        {"text": "1.1.2 Details", "level": 3, "page": 12},
    ])
    m = _match_anchor("9.2.1 Kauzmann paradox", anchors)
    assert m is not None and m[1] == 3
    # '1.1.2 Details' 应精确命中自己的锚点，而不是被 '1.1' 前缀抢走
    m2 = _match_anchor("1.1.2 Details", anchors)
    assert m2 is not None and m2[1] == 3


def test_calibrate_dedup_abbrev_same_page():
    """同页缩写标题（'21 | 阿伦特Ⅱ' vs 完整章名）同级时弃缩写。"""
    from stage2_common import _calibrate_levels
    toc = [{"text": "21 阿伦特Ⅱ：怎么才能不变成坏人", "level": 2, "page": 132}]
    blocks = [
        _title("21 | 阿伦特Ⅱ", 139, level=2, id_=1),
        _title("21 阿伦特Ⅱ：怎么才能不变成坏人", 139, level=2, id_=2),
    ]
    _calibrate_levels(blocks, toc)
    titled = [b for b in blocks if b.get("type") == "title"]
    assert len(titled) == 1
    assert "怎么才能不变成坏人" in titled[0]["content"]


def test_detect_toc_pages_narrow_span():
    """详目单页（只覆盖一章小节，印刷页跨度 22）也要识别为目录页。"""
    from stage2_common import _detect_toc_pages_by_entries
    entries = [{"text": "1 Crystal structure", "level": 2, "page": 3},
               {"text": "1.1 Crystal lattice", "level": 3, "page": 3},
               {"text": "1.2 Symmetry", "level": 3, "page": 8},
               {"text": "1.3 Bravais lattice", "level": 3, "page": 25}]
    lines = ["1 Crystal structure 3", "1.1 Crystal lattice 3",
             "1.2 Symmetry 8", "1.3 Bravais lattice 25"]
    cl = [{"type": "text", "text": "\n".join(lines), "page_idx": 15,
           "bbox": [0, 0, 1000, 1000]}]
    assert _detect_toc_pages_by_entries(entries, cl) == {15}


def test_plain_unanchored_sinks_below_anchor():
    """无编号无锚标题必须沉到所属锚定章下一级（'思想内在于现实'病例）；
    首个锚点之前的 plain 标题（序言类）不受影响。"""
    from stage2_common import _calibrate_levels, _sink_unanchored_plain
    toc = [{"text": "04 路标：韦伯与现代思想的成年", "level": 2, "page": 60}]
    blocks = [
        _title("序言", 3, level=1, id_=1),                      # 首个锚点前 → 不动
        _title("04 路标：韦伯与现代思想的成年", 67, level=2, id_=2),
        _title("思想内在于现实", 70, level=2, id_=3),           # plain 无锚 → 下沉
        _title("现代性与个体自由", 75, level=2, id_=4),         # 同上
    ]
    _calibrate_levels(blocks, toc)
    _sink_unanchored_plain(blocks)
    lv = {b["content"]: b["level"] for b in blocks}
    assert lv["序言"] == 1
    assert lv["04 路标：韦伯与现代思想的成年"] == 2
    assert lv["思想内在于现实"] == 3
    assert lv["现代性与个体自由"] == 3


def test_caption_geom_demote_but_anchor_wins():
    """贴图小字（图注在图下）降回正文；但能锚上目录的绝不动。"""
    from stage2_common import _calibrate_levels
    toc = [{"text": "图 1-1 能带结构", "level": 2, "page": 30},
           {"text": "Chapter One", "level": 1, "page": 1}]
    body = {"type": "text", "content": "这是正文段落，用来提供中位行高。",
            "page": 10, "id": 90, "contd": -1, "image": -1,
            "bbox": [0.1, 0.5, 0.9, 0.515]}
    img = {"type": "image", "content": None, "page": 10, "id": 91,
           "contd": -1, "image": -1, "bbox": [0.2, 0.05, 0.8, 0.19]}
    cap = _title("Figure 1.2 Crystal lattice", 10, level=1, id_=1)
    cap["bbox"] = [0.25, 0.195, 0.75, 0.21]      # 紧贴图下、行高≈正文
    real = _title("图 1-1 能带结构", 10, level=2, id_=2)
    real["bbox"] = [0.25, 0.22, 0.75, 0.235]     # 也在图下，但锚得上
    blocks = [img, body, cap, real]
    _calibrate_levels(blocks, toc)
    assert cap["type"] == "text" and cap["level"] == -1
    assert real["type"] == "title" and real["level"] == 2


def test_detect_toc_pages_blob():
    """blob 合并块形态的目录页（点线页码内嵌多行一块）能被识别。"""
    from stage2_common import _detect_toc_pages_by_entries
    entries = [{"text": f"第{i}节 某某", "level": 2, "page": i * 10}
               for i in range(1, 6)]
    blob = "\n".join(f"第{i}节 某某 …… {i * 10}" for i in range(1, 6))
    cl = [
        {"type": "text", "text": blob, "page_idx": 7,
         "bbox": [0, 0, 1000, 1000]},
        {"type": "page_number", "text": "004", "page_idx": 7,
         "bbox": [0, 0, 10, 10]},
    ]
    assert _detect_toc_pages_by_entries(entries, cl) == {7}


def test_detect_toc_pages_body_safe():
    """正文标题密集页（一章两节同页 + 大量正文行）不得误判为目录页。"""
    from stage2_common import _detect_toc_pages_by_entries
    entries = [{"text": "第十五章 期间与期日", "level": 2, "page": 535},
               {"text": "第一节 期间与期日的概念", "level": 3, "page": 535},
               {"text": "第二节 期间的计算", "level": 3, "page": 535}]
    lines = ["第十五章 期间与期日", "第一节 期间与期日的概念", "第二节 期间的计算"] + \
            [f"这是正文段落第{i}行，内容很长不是目录条目。" for i in range(20)]
    cl = [{"type": "text", "text": "\n".join(lines), "page_idx": 554,
           "bbox": [0, 0, 1000, 1000]},
          {"type": "page_number", "text": "555", "page_idx": 554,
           "bbox": [0, 0, 10, 10]}]
    assert _detect_toc_pages_by_entries(entries, cl) == set()


def test_detect_toc_pages_chapter_opening_safe():
    """章首页（章标题 + 本章节标题 + 正文）命中条目多但印刷页跨度为 0，
    不得误判为目录页（民法总论第八章案例）。"""
    from stage2_common import _detect_toc_pages_by_entries
    entries = [{"text": "第八章 权利变动概述", "level": 2, "page": 247},
               {"text": "第一节 权利变动的样态", "level": 3, "page": 247},
               {"text": "一、权利的静态与动态", "level": 4, "page": 247},
               {"text": "二、权利的取得", "level": 4, "page": 247}]
    lines = ["第八章 权利变动概述", "第一节 权利变动的样态", "一、权利的静态与动态",
             "私法上的权利有其静态的一面，也有其动态的一面。（正文段落）",
             "二、权利的取得",
             "权利的取得是指权利与特定人相结合。（正文段落）"]
    cl = [{"type": "text", "text": line, "page_idx": 266,
           "bbox": [0, 0, 1000, 1000]} for line in lines]
    assert _detect_toc_pages_by_entries(entries, cl) == set()


def test_detect_toc_pages_remnant_adjacent():
    """长目录末页（残余两三条目）相邻主检出页时也被识别。"""
    from stage2_common import _detect_toc_pages_by_entries
    entries = [{"text": "第一编 民法导论", "level": 1, "page": 1},
               {"text": "第二编 权利主体", "level": 1, "page": 60},
               {"text": "第三编 权利客体", "level": 1, "page": 200},
               {"text": "第四编 权利变动", "level": 1, "page": 240},
               {"text": "第五编 权利救济", "level": 1, "page": 430},
               {"text": "第六编 权利的时间维度", "level": 1, "page": 500}]
    cl = [
        # 主目录页：4 个编条目
        *[{"type": "text", "text": t, "page_idx": 6, "bbox": [0, 0, 1000, 1000]}
          for t in ["第一编 民法导论", "第二编 权利主体", "第三编 权利客体",
                    "第四编 权利变动"]],
        # 末页：残余 2 条
        *[{"type": "text", "text": t, "page_idx": 7, "bbox": [0, 0, 1000, 1000]}
          for t in ["第五编 权利救济", "第六编 权利的时间维度"]],
    ]
    assert _detect_toc_pages_by_entries(entries, cl) == {6, 7}


# ──────────────────────────────────────────────────────────────
# _repair_toc_pages
# ──────────────────────────────────────────────────────────────

def _paddle_toc_page():
    """《必须保卫社会》目录第一页（page_idx=3）的简化形态：
    条目是 text 块，页码是右栏 aside_text 块，y 与条目行对齐。"""
    return [
        _blk("text", "CONTENTS", 3, 208, 230),
        _blk("text", "Foreword: François Ewald and Alessandro Fontana", 3, 334, 358),
        _blk("text", "Introduction: Arnold I. Davidson", 3, 384, 405),
        _blk("text", "one 7 JANUARY 1976", 3, 435, 455),
        _blk("text", "two 14 JANUARY 1976", 3, 608, 628),
        _blk("text", "three 21 JANUARY 1976", 3, 754, 774),
        _blk("aside_text", "ix", 3, 337, 352),
        _blk("aside_text", "XV", 3, 390, 403),
        _blk("aside_text", "1", 3, 437, 451),
        _blk("aside_text", "23", 3, 609, 624),
        _blk("aside_text", "43", 3, 758, 773),
    ]


def _hallucinated_entries():
    """LLM 无法配对页码时编出的等差页码（真实应为 9/15/1/23/43）。"""
    return [
        {"text": "Foreword: François Ewald and Alessandro Fontana", "level": 1, "page": 7},
        {"text": "Introduction: Arnold I. Davidson", "level": 1, "page": 13},
        {"text": "one 7 JANUARY 1976", "level": 1, "page": 1},
        {"text": "two 14 JANUARY 1976", "level": 1, "page": 17},
        {"text": "three 21 JANUARY 1976", "level": 1, "page": 33},
    ]


def test_repair_detached_numbers():
    """页码分离的目录：按 y 同行重配真实页码；罗马数字（前置部分）
    页码置空——它与正文偏移 regime 不同，喂给救援只会算出错误位置。"""
    entries = _hallucinated_entries()
    out, toc_pages = _repair_toc_pages(entries, _paddle_toc_page())
    pages = {e["text"]: e["page"] for e in out}
    assert pages["Foreword: François Ewald and Alessandro Fontana"] is None   # ix
    assert pages["Introduction: Arnold I. Davidson"] is None                  # xv
    assert pages["one 7 JANUARY 1976"] == 1
    assert pages["two 14 JANUARY 1976"] == 23
    assert pages["three 21 JANUARY 1976"] == 43
    assert toc_pages == {3}      # 目录页被识别（供标题降格用）


def test_repair_noop_when_numbers_inline():
    """MinerU 风格（页码内嵌条目文本，无独立数字块）：保持原样。"""
    cl = [
        _blk("text", "Foreword: François Ewald and Alessandro Fontana ix", 3, 334, 358),
        _blk("text", "one 7 JANUARY 1976 ........ 1", 3, 435, 455),
        _blk("text", "two 14 JANUARY 1976 ....... 23", 3, 608, 628),
        _blk("text", "three 21 JANUARY 1976 ...... 43", 3, 754, 774),
    ]
    entries = _hallucinated_entries()
    snapshot = [dict(e) for e in entries]
    out, toc_pages = _repair_toc_pages(entries, cl)
    assert out == snapshot
    assert toc_pages == set()


def test_repair_requires_toc_density():
    """正文里恰好同名 + 附近页也有数字块，但命中不足 3 条 → 不动。"""
    cl = [
        _blk("text", "one 7 JANUARY 1976", 24, 435, 455),   # 正文真标题
        _blk("aside_text", "24", 24, 900, 915),             # 页脚页码
    ]
    entries = [{"text": "one 7 JANUARY 1976", "level": 1, "page": 1}]
    out, toc_pages = _repair_toc_pages(entries, cl)
    assert out[0]["page"] == 1
    assert toc_pages == set()


def test_repair_body_heading_page_not_toc():
    """正文标题密集页（3 个锚定标题但只有 1 个页脚数字块）不得误判为目录页。"""
    cl = [
        _blk("text", "one 7 JANUARY 1976", 24, 100, 120),
        _blk("text", "two 14 JANUARY 1976", 24, 130, 150),
        _blk("text", "three 21 JANUARY 1976", 24, 160, 180),
        _blk("page_number", "24", 24, 960, 975),            # 仅页脚 1 个数字块
    ]
    entries = _hallucinated_entries()
    snapshot = [dict(e) for e in entries]
    out, toc_pages = _repair_toc_pages(entries, cl)
    assert toc_pages == set()
    assert [e["page"] for e in out] == [e["page"] for e in snapshot]


def test_repair_each_number_once():
    """每个数字只配一次（两个条目行高相近时不得共用同一页码）。"""
    cl = [
        _blk("text", "aaa chapter", 3, 100, 120),
        _blk("text", "bbb chapter", 3, 130, 150),
        _blk("text", "ccc chapter", 3, 160, 180),
        _blk("aside_text", "10", 3, 100, 115),
        _blk("aside_text", "20", 3, 130, 145),
        _blk("aside_text", "30", 3, 160, 175),
    ]
    entries = [
        {"text": "aaa chapter", "level": 1, "page": 1},
        {"text": "bbb chapter", "level": 1, "page": 2},
        {"text": "ccc chapter", "level": 1, "page": 3},
    ]
    out, _ = _repair_toc_pages(entries, cl)
    assert [e["page"] for e in out] == [10, 20, 30]


# ──────────────────────────────────────────────────────────────
# _rescue_by_page
# ──────────────────────────────────────────────────────────────

def _title(content, page, level=1, id_=0):
    return {"type": "title", "content": content, "page": page,
            "level": level, "id": id_, "contd": -1, "image": -1,
            "bbox": [0.3, 0.2, 0.7, 0.25]}


def test_rescue_long_exact_match_bypasses_cap():
    """>64 字符非标题块：全键精确命中豁免长度帽晋升（FG 'III. The
    Spiritual/…' 编分隔页实测）；单字差模糊命中仍受帽（不动作）。
    家族健康前提：I./II. 已是标题（孤儿罚只打全书无 1 的家族）。"""
    from stage2_common import _calibrate_levels
    long_title = ("III. The Spiritual/Philosophical Dimension: "
                  "The Four Great Deaths of the Self")
    assert len(long_title) > 64
    toc = [{"text": "I. Part One", "level": 1, "page": 10},
           {"text": "II. Part Two", "level": 1, "page": 60},
           {"text": long_title, "level": 1, "page": None}]
    blocks = [
        _title("I. Part One", 12, level=1, id_=10),
        _title("II. Part Two", 61, level=1, id_=11),
        {"type": "text", "content": long_title, "page": 377, "level": -1,
         "id": 1, "contd": -1, "image": -1, "bbox": [0.3, 0.2, 0.7, 0.25]},
        # 同长但错一个字符（模糊命中）：仍受 64 帽拦截
        {"type": "text", "content": long_title.replace("Deaths", "Deatbs"),
         "page": 378, "level": -1, "id": 2, "contd": -1, "image": -1,
         "bbox": [0.3, 0.2, 0.7, 0.25]},
    ]
    _calibrate_levels(blocks, toc)
    by_id = {b["id"]: b for b in blocks}
    assert by_id[1]["type"] == "title" and by_id[1]["level"] == 1
    assert by_id[2]["type"] == "text" and by_id[2]["level"] == -1


def test_rescue_aborts_on_scattered_offsets():
    """《必须保卫社会》案例：真标题全锚上但目录页码是瞎编的（偏移票
    互不相同）→ 必须放弃救援，一个块都不许合成。"""
    toc = [
        {"text": "one 7 JANUARY 1976", "level": 1, "page": 1},
        {"text": "two 14 JANUARY 1976", "level": 1, "page": 17},
        {"text": "three 21 JANUARY 1976", "level": 1, "page": 33},
        {"text": "four 28 JANUARY 1976", "level": 1, "page": 51},
    ]
    blocks = [
        _title("one 7 JANUARY 1976", 24, id_=1),
        _title("two 14 JANUARY 1976", 46, id_=2),
        _title("three 21 JANUARY 1976", 66, id_=3),
        # 'four' 在 PDF 88 真存在，但按瞎编页码 51+偏移 算不到它 → 旧逻辑
        # 会在错误页造幻影块；新逻辑必须整体放弃
        _title("four 28 JANUARY 1976", 88, id_=4),
    ]
    n_before = len(blocks)
    rescued = _rescue_by_page(blocks, toc)
    assert rescued == 0
    assert len(blocks) == n_before
    assert not any(b.get("rescued") for b in blocks)


def test_rescue_still_works_with_consistent_pages():
    """正常书：目录页码正确（偏移一致 ≥3 票）时，章扉页被 OCR 漏识别的
    条目仍能被回补到正确页。"""
    toc = [
        {"text": "Chapter Alpha", "level": 1, "page": 1},
        {"text": "Chapter Beta", "level": 1, "page": 23},
        {"text": "Chapter Gamma", "level": 1, "page": 43},
        {"text": "Chapter Delta", "level": 1, "page": 65},
    ]
    blocks = [
        _title("Chapter Alpha", 24, id_=1),
        _title("Chapter Beta", 46, id_=2),
        _title("Chapter Gamma", 66, id_=3),
        # Chapter Delta 章扉页（印刷 65 → 扫描 88）整块漏识别，页面稀疏
        {"type": "text", "content": "x", "page": 88, "id": 4,
         "contd": -1, "image": -1, "bbox": [0.3, 0.5, 0.7, 0.55]},
    ]
    rescued = _rescue_by_page(blocks, toc)
    assert rescued == 1
    synth = [b for b in blocks if b.get("rescued") == "toc_page"]
    assert len(synth) == 1
    assert synth[0]["content"] == "Chapter Delta"
    assert synth[0]["page"] == 88
    assert synth[0]["level"] == 1


def test_rescue_satisfied_by_exact_match_despite_page():
    """锚点完整键精确命中的块，即使位置与页码推算不符（页码来自
    罗马/阿拉伯混合 regime）也视为已满足，不得在别处再造幻影块。"""
    toc = [
        {"text": "Chapter Alpha", "level": 1, "page": 1},
        {"text": "Chapter Beta", "level": 1, "page": 23},
        {"text": "Chapter Gamma", "level": 1, "page": 43},
        {"text": "Foreword: Someone", "level": 1, "page": 9},
    ]
    blocks = [
        _title("Chapter Alpha", 24, id_=1),
        _title("Chapter Beta", 46, id_=2),
        _title("Chapter Gamma", 66, id_=3),
        _title("Foreword: Someone", 7, id_=4),   # 精确命中，远离 9+23=32
    ]
    n_before = len(blocks)
    rescued = _rescue_by_page(blocks, toc)
    assert rescued == 0
    assert len(blocks) == n_before


# ──────────────────────────────────────────────────────────────
# _build_anchors / _calibrate_levels
# ──────────────────────────────────────────────────────────────

def test_anchor_keeps_digit_ending_title():
    """标题以数字结尾（'one 7 JANUARY 1976'）：剥尾页码不能吃掉年份，
    完整形态锚点必须能精确命中正文标题块。"""
    from stage2_common import _build_anchors, _match_anchor
    anchors = _build_anchors(
        [{"text": "one 7 JANUARY 1976", "level": 1, "page": 1}])
    keys = [a[0] for a in anchors]
    assert "one7january1976" in keys        # 完整形态保留（大小写折叠）
    assert "one7january" in keys            # 剥尾形态也在（截断匹配用）
    m = _match_anchor("one 7 JANUARY 1976", anchors)
    assert m is not None and m[1] == 1


def test_anchor_level_collapse_tiny_plain_top():
    """两级目录、高层级只有个别无编号条目（LLM 把 Foreword/Introduction
    拔高一级）→ 收敛到多数层级。"""
    from stage2_common import _build_anchors
    toc = [{"text": "Foreword", "level": 1, "page": None},
           {"text": "Introduction", "level": 1, "page": None}] + [
              {"text": f"Lecture {i}", "level": 2, "page": i * 20}
              for i in range(1, 7)]
    anchors = _build_anchors(toc)
    assert all(a[1] == 2 for a in anchors)


def test_anchor_level_no_collapse_for_real_parts():
    """高层级条目带"编/Part"编号形状 = 真实两部结构，不得收敛。"""
    from stage2_common import _build_anchors
    toc = [{"text": "第一编 总论", "level": 1, "page": 1},
           {"text": "第二编 分论", "level": 1, "page": 100}] + [
              {"text": f"第{i}章 某某", "level": 2, "page": i * 10}
              for i in range(1, 7)]
    anchors = _build_anchors(toc)
    lv = {a[0]: a[1] for a in anchors}
    assert lv["第一编总论"] == 1
    assert lv["第二编分论"] == 1


def test_anchor_case_insensitive_prefix():
    """大小写折叠：'FOREWORD' 应前缀命中 'Foreword: François Ewald …'。"""
    from stage2_common import _build_anchors, _match_anchor
    anchors = _build_anchors(
        [{"text": "Foreword: François Ewald and Alessandro Fontana",
          "level": 1, "page": None}])
    m = _match_anchor("FOREWORD", anchors)
    assert m is not None and m[1] == 1


def test_calibrate_dedup_same_page_title():
    """同页同文重复标题块（引擎重复输出/页眉混入）只保留一个。"""
    from stage2_common import _calibrate_levels
    toc = [{"text": "nine 3 MARCH 1976", "level": 1, "page": 189}]
    blocks = [
        _title("nine 3 MARCH 1976", 212, id_=1),
        _title("nine 3 MARCH 1976", 212, id_=2),   # 重复块
    ]
    _calibrate_levels(blocks, toc)
    titled = [b for b in blocks if b.get("type") == "title"]
    assert len(titled) == 1
    assert len(blocks) == 1


def test_calibrate_dedup_after_enrichment():
    """章名被拆成两个碎块（'nine' + '3 MARCH 1976'），各自被锚点富化成
    同一完整标题后也必须去重。"""
    from stage2_common import _calibrate_levels
    toc = [{"text": "nine 3 MARCH 1976", "level": 1, "page": 189}]
    blocks = [
        _title("nine", 212, id_=1),
        _title("3 MARCH 1976", 212, id_=2),
    ]
    _calibrate_levels(blocks, toc)
    titled = [b for b in blocks if b.get("type") == "title"]
    assert len(titled) == 1
    assert titled[0]["content"] == "nine 3 MARCH 1976"


def test_strip_trailing_page():
    """行尾页码剥离：点线/空格/斜杠引导都要剥（'xxx / 060'）。"""
    from stage2_common import _strip_trailing_page as stp
    assert stp('09 路标：现代人的“精神危机” / 060') == '09 路标：现代人的“精神危机”'
    assert stp('第二节 法人的分类 ..... 153') == '第二节 法人的分类'
    assert stp('one 7 JANUARY 1976') == 'one 7 JANUARY'   # 已知边界：年份被剥
    assert stp('前言 打开一本书') == '前言 打开一本书'


def test_sink_after_rescue_ordering():
    """下沉必须在救援之后：系列首项先被 page_fuzzy 锚定（_anchored=True），
    其余系列块沉到它下一级，而不是沉到上一章。"""
    from stage2_common import _sink_unanchored_plain
    blocks = [
        {"type": "title", "content": "43 结语", "page": 300, "level": 2,
         "_anchored": True, "contd": -1, "image": -1, "id": 1},
        {"type": "title", "content": "答学友问1", "page": 324, "level": 1,
         "_anchored": True, "rescued": "page_fuzzy",
         "contd": -1, "image": -1, "id": 2},
        {"type": "title", "content": "答学友问2", "page": 326, "level": 1,
         "contd": -1, "image": -1, "id": 3},
        {"type": "title", "content": "答学友问3", "page": 328, "level": 1,
         "contd": -1, "image": -1, "id": 4},
    ]
    _sink_unanchored_plain(blocks)
    assert blocks[2]["level"] == 2
    assert blocks[3]["level"] == 2
    assert blocks[1]["level"] == 1


def test_leader_line_toc_detection():
    """点线引导行判据：≥3 行点线+页码且跨度>5 → 目录页；跨度小不动。"""
    from stage2_common import _detect_toc_pages_by_entries
    cl = [
        {"type": "text", "page_idx": 5, "bbox": [0, 0, 1000, 1000],
         "text": "第一章 某某 …… 12\n第二章 某某 …… 45\n第三章 某某 …… 88"},
        # 章内小目录：跨度 3 ≤ 5 → 不判
        {"type": "text", "page_idx": 40, "bbox": [0, 0, 1000, 1000],
         "text": "第一节 某某 …… 100\n第二节 某某 …… 101\n第三节 某某 …… 103"},
    ]
    assert _detect_toc_pages_by_entries([], cl) == {5}


def test_running_head_convergence():
    """页首重复标题（前文已出现同名）降回正文；锚定块不动。"""
    from stage2_common import _calibrate_levels
    toc = [{"text": "第一章 某某", "level": 2, "page": 10}]
    real = _title("第一章 某某", 12, level=2, id_=1)
    real["bbox"] = [0.3, 0.2, 0.7, 0.23]
    rh = _title("第一章 某某", 14, level=2, id_=2)
    rh["bbox"] = [0.3, 0.03, 0.7, 0.055]      # 页首、同名、无锚 → 运行头
    blocks = [real, rh]
    _calibrate_levels(blocks, toc)
    assert real["type"] == "title"
    assert rh["type"] == "text" and rh["level"] == -1


def test_split_by_cv_basic():
    """CV 聚类：两档明显高度应分成两组，最大组均高排前。"""
    from stage2_common import _split_by_cv
    items = [(0.010, "a"), (0.011, "b"), (0.010, "c"),
             (0.020, "d"), (0.021, "e"), (0.020, "f"), (0.009, "g")]
    groups = _split_by_cv(items, max_cv=0.1, max_groups=4)
    assert len(groups) == 2
    assert groups[0][0][1] in ("d", "e", "f")     # 大字号组在前（rank 0）
    # 一档均匀数据不应再拆
    same = [(0.010 + i * 0.0001, str(i)) for i in range(8)]
    assert len(_split_by_cv(same, max_cv=0.1, max_groups=4)) == 1


def test_height_ladder_differentiates_plain_titles():
    """字号阶梯：锚定章 L2 大字、锚定节 L3 中字标定后，无锚小标题
    按自身字高档位差异化下沉（几何只许加深不许上浮）。"""
    from stage2_common import _calibrate_levels, _sink_unanchored_plain
    toc = [{"text": "Chapter One", "level": 2, "page": 5},
           {"text": "1.1 Section A", "level": 3, "page": 5}]
    blocks = [
        # 锚定章（大字号）+ 锚定节（中字号）提供标定
        {"type": "title", "content": "Chapter One", "page": 10, "level": 2,
         "id": 1, "contd": -1, "image": -1, "bbox": [0.3, 0.2, 0.7, 0.230]},
        # 无锚 plain 标题：中字号（应 L3）与小字号（应更深）
        {"type": "title", "content": "思考的含义", "page": 12, "level": 2,
         "id": 3, "contd": -1, "image": -1, "bbox": [0.3, 0.2, 0.7, 0.220]},
        {"type": "title", "content": "细枝末节的讨论", "page": 13, "level": 2,
         "id": 4, "contd": -1, "image": -1, "bbox": [0.3, 0.2, 0.7, 0.210]},
        # 锚定节（中字号）填充样本
        *[{"type": "title", "content": f"1.{i} Section filler", "page": 14 + i,
           "level": 3, "id": 10 + i, "contd": -1, "image": -1,
           "bbox": [0.3, 0.2, 0.7, 0.220]} for i in range(2, 9)],
    ]
    # 修正为真实量级：章 0.030 / 节 0.020 / 小字 0.010
    blocks[0]["bbox"] = [0.3, 0.2, 0.7, 0.230]
    blocks[1]["bbox"] = [0.3, 0.2, 0.7, 0.220]
    blocks[2]["bbox"] = [0.3, 0.2, 0.7, 0.210]
    _calibrate_levels(blocks, toc)
    _sink_unanchored_plain(blocks)
    assert blocks[1]["level"] == 3          # 中字 → 锚定章下一级
    assert blocks[2]["level"] >= 4          # 小字 → 按阶梯更深


def test_orphan_series_demote_and_rescue():
    """孤儿编号：无 '1' 且 ≥3 起跳 → 降回正文；附近有 '1' 文本块 → 救回。"""
    from stage2_common import _fix_orphan_series
    bad = [_title("4. 某某事项", 20, level=3, id_=1),
           _title("5. 某某事项", 21, level=3, id_=2)]
    _fix_orphan_series(bad)
    assert all(b["type"] == "text" for b in bad)

    good = [
        {"type": "text", "content": "1. 首个事项", "page": 19, "level": -1,
         "contd": -1, "image": -1, "id": 0, "bbox": [0.3, 0.3, 0.7, 0.33]},
        _title("2. 次要事项", 20, level=3, id_=1),
        _title("3. 第三事项", 21, level=3, id_=2),
    ]
    _fix_orphan_series(good)
    assert good[0]["type"] == "title" and good[0]["level"] == 3
    assert good[1]["type"] == "title" and good[2]["type"] == "title"


def test_calibrate_demotes_toc_page_titles():
    """目录页上的条目块一律降格为文本，不进文档树；正文同名标题不受影响。"""
    from stage2_common import _calibrate_levels
    toc = [{"text": "one 7 JANUARY 1976", "level": 1, "page": 1},
           {"text": "two 14 JANUARY 1976", "level": 1, "page": 23}]
    blocks = [
        _title("CONTENTS", 4, id_=1),
        _title("one 7 JANUARY 1976", 4, id_=2),     # 目录页条目（LLM 误投）
        _title("two 14 JANUARY 1976", 4, id_=3),
        _title("one 7 JANUARY 1976", 24, id_=4),    # 正文真标题
        _title("two 14 JANUARY 1976", 46, id_=5),
    ]
    _calibrate_levels(blocks, toc, toc_pages={4})
    toc_page_blocks = [b for b in blocks if b["page"] == 4]
    assert all(b["type"] == "text" and b["level"] == -1
               for b in toc_page_blocks)
    body = [b for b in blocks if b["page"] != 4]
    assert all(b["type"] == "title" and b["level"] == 1 for b in body)


def test_apply_global_levels():
    """全局定级应用逻辑：锚定锁死、无锚调整、plain 受下沉底线钳制、
    层级钳制 1..8、非法值忽略。"""
    from stage2_common import _apply_global_levels
    rows = [
        {"type": "title", "content": "第一章 某某", "id": 1, "level": 1,
         "_anchored": True},
        {"type": "title", "content": "韦伯的人生", "id": 2, "level": 1},   # plain 无锚
        {"type": "title", "content": "1.1 某某", "id": 3, "level": 5},      # 编号无锚
        {"type": "title", "content": "细 节", "id": 4, "level": 2},
    ]
    floors = {id(rows[1]): 2, id(rows[2]): 2, id(rows[3]): 2}
    lv_map = {2: 3, 3: 3, 4: 99, 1: 7}     # 1 是锚定（应被忽略）
    n = _apply_global_levels(rows, lv_map, floors)
    assert rows[0]["level"] == 1           # 锚定锁死
    assert rows[1]["level"] == 3           # plain 无锚取 LLM 级（≥floor 2）
    assert rows[2]["level"] == 3           # 编号无锚取 LLM 级
    assert rows[3]["level"] == 8           # 99 钳制到 8
    assert n == 3


def test_apply_global_levels_plain_floor():
    """plain 无锚：LLM 给浅于下沉底线的层级要被底线钳住。"""
    from stage2_common import _apply_global_levels
    rows = [{"type": "title", "content": "篇内小标题", "id": 1, "level": 3}]
    floors = {id(rows[0]): 3}
    _apply_global_levels(rows, {1: 1}, floors)
    assert rows[0]["level"] == 3           # LLM 给 1，底线 3 钳住


def test_forged_toc_fingerprint():
    """伪造目录指纹（Integral Calculus Made Easy 案例：674 页无目录页，
    LLM 拿全书标题列表编造 60 条 toc_entries，page 全抄标题块扫描页码）。
    全等 = 伪造；真目录给印刷页码，与扫描页有非零偏移 → 不相等。"""
    from stage2_common import _forged_toc_fingerprint
    blocks = [_title(f"Chapter {i}", i * 10, id_=i) for i in range(1, 7)]
    forged = [{"text": f"Chapter {i}", "level": 1, "page": i * 10}
              for i in range(1, 7)]
    n_cmp, n_eq = _forged_toc_fingerprint(forged, blocks)
    assert n_cmp == 6 and n_eq == 6          # 全等 → 伪造特征
    real = [{"text": f"Chapter {i}", "level": 1, "page": i * 10 - 7}
            for i in range(1, 7)]
    n_cmp, n_eq = _forged_toc_fingerprint(real, blocks)
    assert n_cmp == 6 and n_eq == 0          # 一致偏移 → 真目录
    # 无页码条目不可比对
    assert _forged_toc_fingerprint(
        [{"text": "Chapter 1", "level": 1, "page": None}], blocks) == (0, 0)


# ──────────────────────────────────────────────────────────────
# _sanitize_pdf_toc / 全角／页码尾巴（病例019：高等数学 PDF 自制书签
# '标题／页码' 平级假目录被当作 outline 真值采用 → TOC 72 条碎成 191 条）
# ──────────────────────────────────────────────────────────────

def test_strip_trailing_page_fullwidth_slash():
    from stage2_common import _strip_trailing_page
    assert _strip_trailing_page("第一章 函数与极限／1") == "第一章 函数与极限"
    assert _strip_trailing_page("第二节 数列的极限 …… 18") == "第二节 数列的极限"
    # 标题本身以数字结尾、无分隔符的不误剥
    assert _strip_trailing_page("习题1-10") == "习题1-10"


def test_sanitize_pdf_toc_junk_rejected():
    """'标题／页码'假书签 ≥80% 命中即整体丢弃（高等数学实测 193/198）。"""
    from stage2_common import _sanitize_pdf_toc
    junk = [{"text": t, "level": 1, "page": p} for t, p in [
        ("封面", 1), ("第一章 函数与极限／1", 16), ("第一节 映射与函数／1", 16),
        ("一、映射／1", 16), ("二、函数／3", 18), ("习题1-1／16", 31),
        ("第二节 数列的极限／18", 33), ("一、数列极限的定义／18", 33),
        ("二、收敛数列的性质／23", 38), ("习题1-2／26", 41)]]
    assert _sanitize_pdf_toc(junk) == []


def test_sanitize_pdf_toc_clean_kept_stray_stripped():
    """born-digital 真书签原样保留（层级不动）；个别页码尾巴逐条剥除。"""
    from stage2_common import _sanitize_pdf_toc
    good = [{"text": "Preface", "level": 1, "page": 1},
            {"text": "Chapter 1 Introduction", "level": 1, "page": 9},
            {"text": "1.1 Background", "level": 2, "page": 10},
            {"text": "Appendix C／301", "level": 1, "page": 320}]
    out = _sanitize_pdf_toc(good)
    assert [e["text"] for e in out] == [
        "Preface", "Chapter 1 Introduction", "1.1 Background", "Appendix C"]
    assert out[2]["level"] == 2
    # 空输入 / 剥空条目
    assert _sanitize_pdf_toc([]) == []
    assert _sanitize_pdf_toc(None) == []


# ──────────────────────────────────────────────────────────────

def test_sanitize_pdf_toc_digit_flood():
    """页码书签洪水（病例 025 续·汉语语义学）：outline 432/436 条是纯数字
    （每页页码录成一条书签）→ 整体丢弃，回退 LLM 目录提取。
    残留的 封面/版权/前言/目录 若留任会把'前言'锚成唯一 L1，下沉压平全书。"""
    from stage2_common import _sanitize_pdf_toc
    toc = [{"text": "封面", "level": 1, "page": 1},
           {"text": "前言", "level": 1, "page": 4}] +           [{"text": str(i), "level": 1, "page": 15 + i} for i in range(1, 430)]
    assert _sanitize_pdf_toc(toc) == []


def test_sanitize_pdf_toc_scattered_digit_entries_dropped():
    """好书签里混入零散页码书签：逐条剥除纯数字条目，保留真条目"""
    from stage2_common import _sanitize_pdf_toc
    toc = [{"text": "第一章 函数与极限", "level": 1, "page": 10},
           {"text": "12", "level": 1, "page": 12},
           {"text": "第二章 导数", "level": 1, "page": 30},
           {"text": "1976", "level": 1, "page": 40},
           {"text": "附录", "level": 1, "page": 200}]
    out = _sanitize_pdf_toc(toc)
    assert [e["text"] for e in out] == ["第一章 函数与极限", "第二章 导数", "附录"]


def test_sanitize_pdf_toc_good_outline_untouched():
    """正常书签（无页码尾巴、无纯数字）原样保留——失败方向必须是不动作"""
    from stage2_common import _sanitize_pdf_toc
    toc = [{"text": "前言", "level": 1, "page": 4},
           {"text": "第一章 语义研究的发展与现状", "level": 1, "page": 16},
           {"text": "一 语文学时期及我国的训诂学", "level": 2, "page": 16}]
    out = _sanitize_pdf_toc(toc)
    assert [e["text"] for e in out] == [e["text"] for e in toc]


# ── 前后页词表锚定（病例 028）──────────────────────────────

def _pblk(type_, content, page, level=-1):
    return {"type": type_, "content": content, "page": page, "level": level}


def test_fm_lexicon_front_items_locked_l1():
    """前言（括号署名）/版权页（text 块晋升）/Preface（英文）锚定锁 L1"""
    from stage2_common import _anchor_frontmatter_lexicon
    blocks = [
        _pblk("title", "前言（贾彦德）", 4, -1),
        _pblk("text", "版权页（CIP数据）", 3),
        _pblk("title", "Preface", 5, 2),
        _pblk("title", "第一章 语义研究的发展与现状", 16, 1),
    ]
    n = _anchor_frontmatter_lexicon(blocks)
    assert n == 3
    assert blocks[0]["level"] == 1 and blocks[0].get("_fm_rule")
    assert blocks[1]["type"] == "title" and blocks[1]["level"] == 1
    assert blocks[2]["level"] == 1
    assert "_fm_rule" not in blocks[3]


def test_fm_lexicon_rejects_structural_and_out_of_zone():
    """反例：导言（真章题候选）不收；非全等（'序列'）不收；出页区不收"""
    from stage2_common import _anchor_frontmatter_lexicon
    blocks = [
        _pblk("title", "导言", 3, -1),
        _pblk("title", "序列", 3, -1),
        _pblk("title", "前言", 60, -1),            # 第一章在 p50 → 已出前页区
        _pblk("title", "第一章 总论", 50, 1),
        _pblk("title", "第五章 语义", 180, 1),
        _pblk("title", "后记", 200, -1),           # 后页区（末章 p180）
    ]
    n = _anchor_frontmatter_lexicon(blocks)
    assert n == 1
    assert blocks[0]["level"] == -1 and blocks[1]["level"] == -1
    assert blocks[2]["level"] == -1
    assert blocks[5]["level"] == 1 and blocks[5].get("_fm_rule")


def test_fm_lexicon_no_chapter_book_fallback():
    """无章号书回退页区（前 25 页）：Foreword 锚定，正文不受影响"""
    from stage2_common import _anchor_frontmatter_lexicon
    blocks = [
        _pblk("title", "Foreword", 3, -1),
        _pblk("text", "It was a bright cold day in April...", 30),
    ]
    assert _anchor_frontmatter_lexicon(blocks) == 1
    assert blocks[0]["level"] == 1
    assert blocks[1]["type"] == "text"


def test_fm_lexicon_skips_toc_pages():
    """目录页上的词表条目是罗列行，不锚（BAC p5：Contents 列出
    Dedication/Acknowledgments/About the Author 被锚出幻影条目的实测）"""
    from stage2_common import _anchor_frontmatter_lexicon
    blocks = [
        _pblk("title", "Foreword", 3, -1),
        _pblk("text", "Dedication", 5),
        _pblk("title", "第一章 总论", 20, 1),
    ]
    n = _anchor_frontmatter_lexicon(blocks, toc_pages={5})
    assert n == 1  # 只有 Foreword
    assert blocks[1]["type"] == "text" and blocks[1]["level"] == -1


def test_fm_lexicon_skips_multi_hit_page():
    """同页 ≥2 个词表命中 = 罗列页形态（未识别出的目录页），整页不动作"""
    from stage2_common import _anchor_frontmatter_lexicon
    blocks = [
        _pblk("text", "Dedication", 5),
        _pblk("text", "Acknowledgments", 5),
        _pblk("title", "第一章 总论", 20, 1),
    ]
    assert _anchor_frontmatter_lexicon(blocks) == 0
    assert all(b["level"] == -1 for b in blocks[:2])


def test_rescue_long_title_block_exempt_from_len_cap():
    """引擎已标 title 的多行超长章题不受 64 字符上限（BAC ch12 实测：
    归一化 105 字符，锚点在手仍拿不到 level）"""
    from stage2_common import _calibrate_levels
    entry = ("Chapter 12: A Young Man’s Long, Awkward, Occasionally Tragic, "
             "and Frequently Humiliating Education in Affairs of the Heart, "
             "Part II: The Crush")
    body = ("A YOUNG MAN'S LONG,\nAWKWARD, OCCASIONALLY\nTRAGIC, AND FREQUENTLY\n"
            "HUMILIATING EDUCATION IN\nAFFAIRS OF THE HEART,\nPART II: THE CRUSH")
    blocks = [
        _pblk("title", "Chapter 10: A Young Man’s Long, Awkward", 8, 2),
        {"type": "title", "content": body, "page": 10, "level": -1},
    ]
    toc = [{"text": "Chapter 10: A Young Man's Long, Awkward", "level": 2, "page": None},
           {"text": entry, "level": 2, "page": None}]
    _calibrate_levels(blocks, toc)
    assert blocks[1]["level"] == 2, f"超长标题块应被救援赋级，实得 {blocks[1]['level']}"


def test_rescue_long_text_block_still_capped():
    """反例：同长度的纯文本块仍被 64 上限挡住（防正文段落误晋升）"""
    from stage2_common import _calibrate_levels
    entry = ("Chapter 12: A Young Man’s Long, Awkward, Occasionally Tragic, "
             "and Frequently Humiliating Education in Affairs of the Heart, "
             "Part II: The Crush")
    body = ("A YOUNG MAN'S LONG,\nAWKWARD, OCCASIONALLY\nTRAGIC, AND FREQUENTLY\n"
            "HUMILIATING EDUCATION IN\nAFFAIRS OF THE HEART,\nPART II: THE CRUSH")
    blocks = [
        _pblk("title", "Chapter 10: A Young Man’s Long, Awkward", 8, 2),
        {"type": "text", "content": body, "page": 10, "level": -1},
    ]
    toc = [{"text": "Chapter 10: A Young Man's Long, Awkward", "level": 2, "page": None},
           {"text": entry, "level": 2, "page": None}]
    _calibrate_levels(blocks, toc)
    assert blocks[1]["type"] == "text" and blocks[1]["level"] == -1


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  PASS {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL {fn.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"  ERROR {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    return failed


def test_anchor_latex_superscript_and_command_normalization():
    """病例 024：公式标题归一化——'$W^{\\pm}$ and $Z^{0}$' ↔ 'W± and Z0'
    （QFT 11.4 节标题，目录 OCR 形态与正文 LaTeX 形态系统性差异）"""
    from stage2_common import _build_anchors, _match_anchor
    anchors = _build_anchors([
        {"text": "11.4 Non-abelian gauge theories: the masses of W± and Z0",
         "level": 2, "page": 278},
    ])
    m = _match_anchor(
        "11.4 Non-abelian gauge theories: the masses of $W^{\\pm}$ and $Z^{0}$",
        anchors)
    assert m is not None and m[1] == 2


def test_anchor_label_prefix_suffix_match():
    """病例 024：锚点是块的尾部——块多冒号标签前缀
    （'Complement: Isospin and flavor $SU(3)$' ↔ 目录 'Isospin and flavor SU(3)'）"""
    from stage2_common import _build_anchors, _match_anchor
    anchors = _build_anchors([
        {"text": "Isospin and flavor SU(3)", "level": 2, "page": 200},
    ])
    m = _match_anchor("Complement: Isospin and flavor $SU(3)$", anchors)
    assert m is not None and m[1] == 2


def test_anchor_label_prefix_suffix_rejects_bare_suffix():
    """反例：无冒号前缀的裸后缀不命中（防 '附录 A gauge theories review'
    错配 'gauge theories review'）"""
    from stage2_common import _build_anchors, _match_anchor
    anchors = _build_anchors([
        {"text": "gauge theories review", "level": 1, "page": 1},
    ])
    assert _match_anchor("appendix a gauge theories review", anchors) is None


def test_find_toc_page_and_rule_extract_cn():
    """病例 029/030：目录页本地探测 + 规则兜底提取（八次危机形态——
    条目带介绍性段落、'第X部分/引言/一、'混合层级、页码 010 前导零）"""
    from stage2_common import _find_toc_page, _rule_toc_extract
    pages = {
        3: "扉页文字，没有目录。",
        23: ("目录\n推荐序 001\n自序 003\n概念提示 013\n"
             "第一部分 中国的8次危机及其“软着陆”\n引言 021"),
        24: ("一、从外资外债视角解析“中国经验” 010\n"
             "将当代中国半个多世纪的四次“对外开放”的复杂背景联系在一起不难发现，"
             "对外开放从来都是“双刃剑”：它既是机遇也是挑战，这段话很长不该收。\n"
             "二、从危机化解视角思考中国发展的可持续性 020"),
        25: "本章正文开始，没有页码尾巴的正文行。\n这也是正文行。",
    }
    assert _find_toc_page(pages) == 23
    entries = _rule_toc_extract(pages, 23)
    by = {e["text"]: e for e in entries}
    assert by["推荐序"]["page"] == 1 and by["推荐序"]["level"] == 1
    assert by["自序"]["page"] == 3
    assert by["第一部分 中国的8次危机及其“软着陆”"]["level"] == 1
    assert by["第一部分 中国的8次危机及其“软着陆”"]["page"] is None  # 结构词无页码也收
    assert by["引言"]["page"] == 21
    assert by["一、从外资外债视角解析“中国经验”"]["level"] == 3
    assert by["二、从危机化解视角思考中国发展的可持续性"]["page"] == 20
    assert not any("双刃剑" in t for t in by)  # 介绍性长段落不收


def test_rule_extract_author_line_merge():
    """中國36問形态：条目标题行无页码、作者行带页码 → 页码归条目；
    '导论 宋怡明 (Michael Szonyi) 3' 行内作者剥除"""
    from stage2_common import _rule_toc_extract
    pages = {
        7: ("目 录\n导论 宋怡明 (Michael Szonyi) 3\n第 一 部 分 政 治\n"
            "1. 中国共产党政权是否具有合法性？\n裴宜理 (Elizabeth J. Perry) 13\n"
            "2. 反贪腐能否救党？\n傅士卓 (Joseph Fewsmith) 19"),
    }
    entries = _rule_toc_extract(pages, 7)
    by = {e["text"]: e for e in entries}
    assert by["导论"]["page"] == 3 and by["导论"]["level"] == 1
    assert by["第 一 部 分 政 治"]["level"] == 1 and by["第 一 部 分 政 治"]["page"] is None
    assert by["1. 中国共产党政权是否具有合法性？"]["page"] == 13
    assert by["1. 中国共产党政权是否具有合法性？"]["level"] == 2  # 无章 shape → N. = 章级
    assert by["2. 反贪腐能否救党？"]["page"] == 19
    assert not any("裴宜理" in t or "傅士卓" in t for t in by)  # 作者行不成条目


def test_rule_extract_no_toc_page_noop():
    """反例：无目录词页 → 探测 None；正文页无页码尾行 → 提取为空（不动作）"""
    from stage2_common import _find_toc_page, _rule_toc_extract
    pages = {1: "正文第一页。", 2: "另一页正文。"}
    assert _find_toc_page(pages) is None
    assert _rule_toc_extract(pages, 1) == []


def test_anchor_strips_html_tags():
    """病例 031：正文标题混入 <sub>：</sub> 等 HTML 标签不影响锚定
    （全球化与国家竞争：文字版 PDF 文本层的下标冒号工件）"""
    from stage2_common import _build_anchors, _match_anchor
    anchors = _build_anchors([
        {"text": "第二章 土耳其：地缘格局重构中的“土耳其模式”", "level": 1, "page": 166},
    ])
    m = _match_anchor("第二章 土耳其<sub>：</sub>地缘格局重构中的“土耳其模式”", anchors)
    assert m is not None and m[1] == 1


if __name__ == "__main__":
    sys.exit(1 if _run_all() else 0)


