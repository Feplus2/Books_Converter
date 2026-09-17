"""stage2_vlm 单测：本地映射 / contd 规则 / 目录先验直接作锚 /
守卫只降格 / 交叉校验报告形状 / 无 db 回退。全部 mock，无网络。

运行：.venv/Scripts/python.exe tests/test_stage2_vlm.py
"""

import json
import sqlite3  # noqa: F401  (stage1_vlm._StateDB 底层)
import sys
import tempfile
import unittest.mock as mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import stage1_vlm
import stage2_vlm
from stage2_vlm import (
    _annotated_blocks,
    _cross_check,
    _demote_empty_titles,
    _level_consistency_pass,
    _rule_contd,
    analyze_structure_vlm,
)

# stage2_vlm 严禁触碰的 OCR 时代目录检测/救援家族（铁律：禁调）
_FORBIDDEN = [
    "_find_toc_page", "_rule_toc_extract", "_repair_toc_pages",
    "_detect_toc_pages_by_entries", "_forged_toc_fingerprint",
    "_rescue_by_page", "_global_level_pass", "_detect_toc_pages",
    "_anchor_generic_outline",
]

_TOC_PRIOR = [
    {"text": "第一章 总则", "level": 2, "page": 3},
    {"text": "第二章 分则", "level": 2, "page": 9},
    {"text": "第三章 附则", "level": 2, "page": 20},
]


def _blk(text, page_idx, level=None, btype="text", **kw):
    b = {"type": btype, "text": text,
         "bbox": [100, 100, 900, 140], "page_idx": page_idx}
    if level is not None:
        b["text_level"] = level
    b.update(kw)
    return b


def _light_mock(toc_entries=None, front_matter=None):
    """假的 _light_metadata_pass（全书唯一 LLM 调用点，测试中拔掉）。"""
    def _fake(content_list, book_name, popo_titles=None, progress=None):
        return {
            "metadata": {"title": book_name, "authors": [], "translator": None,
                         "publisher": None, "language": "zh"},
            "front_matter": front_matter if front_matter is not None else [],
            "back_matter": [],
            "toc_entries": toc_entries if toc_entries is not None else [],
        }
    return _fake


def _make_state_db(path: Path, toc_entries, pages_json):
    db = stage1_vlm._StateDB(path)
    db.set("toc_entries", json.dumps(toc_entries, ensure_ascii=False))
    for p, pj in pages_json.items():
        db.save_page(p, "ok", pj, len(pj.get("blocks", [])), 0, False, "", 0.01)
    db.close()


def _forbidden_mocks():
    return {name: mock.Mock(side_effect=AssertionError(f"禁调 {name}"))
            for name in _FORBIDDEN}


# ──────────────────────────────────────────────────────────────
# ① text_level → title/level 映射与 source_id 保真
# ──────────────────────────────────────────────────────────────

def test_mapping_text_level_and_source_id():
    cl = [
        _blk("第一章 总则", 4, level=2),
        _blk("正文段落。", 4),
        _blk(None, 4, btype="image", img_path="images/p0005_0.png",
             image_caption=["图1-1 示意"]),
        _blk(None, 5, btype="table", table_body="<table><tr><td>a</td></tr></table>"),
        _blk("① 注文", 4, btype="page_footnote"),
        _blk("民法总论", 4, btype="header"),
        _blk("3", 4, btype="page_number"),
    ]
    blocks = _annotated_blocks(cl)
    by_sid = {b["source_id"]: b for b in blocks}

    t = by_sid["t:0"]
    assert t["type"] == "title" and t["level"] == 2 and t["page"] == 5
    assert t["bbox"] == [0.1, 0.1, 0.9, 0.14]
    assert by_sid["t:1"]["type"] == "text" and by_sid["t:1"]["level"] == -1

    img = by_sid["t:2"]
    assert img["type"] == "image" and img["content"] is None
    cap = by_sid["t:2.cap0"]
    assert cap["type"] == "image_caption" and cap["image"] == img["id"]

    tb = by_sid["t:3"]
    assert tb["type"] == "table" and tb["content"].startswith("<table>")
    assert tb["table_merge"] == -1

    # 脚注绝不允许丢（stage3 章末尾注锚定从 popo blocks 收集）
    fn = by_sid["t:4"]
    assert fn["type"] == "page_footnote" and fn["content"] == "① 注文"

    # header / page_number 不进 blocks（stage3 渲染层本就跳过）
    assert not any(b["source_id"] in ("t:5", "t:6") for b in blocks)

    # id 全书递增、contd/image 默认 -1
    ids = [b["id"] for b in blocks]
    assert ids == list(range(1, len(blocks) + 1))
    assert all(b["contd"] == -1 for b in blocks)


# ──────────────────────────────────────────────────────────────
# ② contd 规则版：拼 / 不拼
# ──────────────────────────────────────────────────────────────

def test_contd_rule_joins_unfinished_page_tail():
    cl = [
        _blk("民法是调整平等主体之间人身关系与财产关系的法律规范的总和，其渊源包括", 0),
        _blk("① 页底注文。", 0, btype="page_footnote"),  # 脚注块不截断判定
        _blk("法律、行政法规以及习惯等。", 1),
    ]
    blocks = _annotated_blocks(cl)
    assert _rule_contd(blocks) == 1
    last, first = blocks[0], blocks[2]
    assert last["contd"] == first["id"]   # contd 值 = 续块 id（stage3 据此并段）


def test_contd_rule_no_join_when_complete_or_list():
    # p0 页尾已完结（。结尾）→ p0→p1 不拼；p1 未完结且 p2 非列表 → 拼；
    # p2 未完结但 p3 是列表/编号起首 → 不拼
    cl = [
        _blk("这一段已经完整结束了。", 0),
        _blk("下一段从新的内容开始，没有任何终止标点", 1),
        _blk("再下一段同样没有终止标点", 2),
        _blk("1. 列表项起首的段落不应被拼上。", 3),
    ]
    blocks = _annotated_blocks(cl)
    assert _rule_contd(blocks) == 1
    assert blocks[0]["contd"] == -1
    assert blocks[1]["contd"] == blocks[2]["id"]
    assert blocks[2]["contd"] == -1
    # 下页首块是列表/编号形状 → 不拼
    cl2 = [
        _blk("总则编分编章的结构安排考虑了多方面因素，其中包括", 0),
        _blk("1. 利益说认为权利的本质是利益。", 1),
    ]
    assert _rule_contd(_annotated_blocks(cl2)) == 0
    # 页尾以脚注圈码/公式/引号收束 → 视为完结不拼
    cl3 = [
        _blk("该说已为通说所采$^{①}$", 0),
        _blk("不受前款规定的限制，但是法律另有规定的除外", 1),
    ]
    assert _rule_contd(_annotated_blocks(cl3)) == 0


# ──────────────────────────────────────────────────────────────
# ③ 目录先验直接作锚（vlm_state.db），不触碰目录检测器
# ──────────────────────────────────────────────────────────────

def _toc_region_page_json():
    return {"kind": "toc", "printed_page": None, "running_head": None,
            "blocks": [
                {"t": "title", "text": "目录", "level": 1},
                {"t": "toc", "text": "第一章 总则", "level": 2, "page": 3},
                {"t": "toc", "text": "第二章 分则", "level": 2, "page": 9},
                {"t": "toc", "text": "第三章 附则", "level": 2, "page": 20},
            ], "footnotes": [], "has_image": False}


def test_toc_prior_anchors_directly_without_detectors():
    tmp = Path(tempfile.mkdtemp())
    db_path = tmp / "vlm" / "vlm_state.db"
    db_path.parent.mkdir(parents=True)
    _make_state_db(db_path, _TOC_PRIOR, {1: _toc_region_page_json()})

    cl = [
        # 目录页（page_idx 1）：stage1 喂给规则引擎的 MinerU 形态条目
        _blk("目录", 1, level=1),
        _blk("第一章 总则 …… 3", 1, level=1),
        _blk("第二章 分则 …… 9", 1, level=1),
        _blk("第三章 附则 …… 20", 1, level=1),
        # 正文页：真章题
        _blk("第一章 总则", 5, level=2),
        _blk("总则的正文内容。", 5),
        _blk("第二章 分则", 8, level=2),
        _blk("分则的正文内容。", 8),
        _blk("第三章 附则", 12, level=2),
        _blk("附则的正文内容。", 12),
    ]
    with mock.patch.object(stage2_vlm, "_light_metadata_pass", _light_mock()), \
            mock.patch.multiple("stage2_common", **_forbidden_mocks()):
        s = analyze_structure_vlm(cl, "测试书", str(tmp),
                                  vlm_state_db=db_path)

    # 产物同构
    for k in ("engine", "metadata", "front_matter", "back_matter",
              "noise_ranges", "tree", "toc_entries", "toc_source",
              "popo_blocks_file", "cross_check"):
        assert k in s, f"缺键 {k}"
    assert s["engine"] == "vlm-hybrid"
    assert s["toc_source"] == "vlm"
    assert s["toc_entries"] == _TOC_PRIOR
    assert (tmp / "popo_blocks.json").exists()

    blocks = json.loads((tmp / "popo_blocks.json").read_text(encoding="utf-8"))
    titled = {(b.get("content") or ""): b for b in blocks
              if b["type"] == "title" and b["level"] > 0}
    # 三个真章题锚定命中、level 锁定
    for e in _TOC_PRIOR:
        b = titled.get(e["text"])
        assert b is not None and b["_anchored"] and b["level"] == e["level"], e
    # 目录页条目块全部降格（不与真章题同锚竞争）
    assert not any(b["type"] == "title" and b["page"] == 2 for b in blocks)
    # 交叉校验全清
    assert s["cross_check"] == {"unanchored": [], "level_mismatch": [],
                                "page_offset_anomaly": []}


# ──────────────────────────────────────────────────────────────
# ④ 守卫只降格：页眉形状块降回正文
# ──────────────────────────────────────────────────────────────

def test_guards_only_demote():
    tmp = Path(tempfile.mkdtemp())
    cl = [
        _blk("第一章 总则", 4, level=2),
        _blk("总则的正文内容。", 4),
        _blk("第三章 习题 3-1（第 132 页）", 8, level=1),   # 运行页眉形状
        _blk("A", 8, level=1),                              # 索引字母字头
        _blk("正文继续。", 8),
    ]
    light = _light_mock(toc_entries=[{"text": "第一章 总则", "level": 2, "page": 1}])
    with mock.patch.object(stage2_vlm, "_light_metadata_pass", light), \
            mock.patch.multiple("stage2_common", **_forbidden_mocks()):
        s = analyze_structure_vlm(cl, "测试书", str(tmp), vlm_state_db=None)

    blocks = json.loads((tmp / "popo_blocks.json").read_text(encoding="utf-8"))
    junk = [b for b in blocks
            if (b.get("content") or "") in ("第三章 习题 3-1（第 132 页）", "A")]
    assert len(junk) == 2
    assert all(b["type"] == "text" and b["level"] == -1 for b in junk), junk
    # 真标题不受影响（只降格不晋升的另一个方向：章题保持锚定）
    chap = next(b for b in blocks if (b.get("content") or "") == "第一章 总则")
    assert chap["type"] == "title" and chap["level"] == 2 and chap["_anchored"]
    assert s["cross_check"]["unanchored"] == []


# ──────────────────────────────────────────────────────────────
# ⑤ cross_check 报告形状：未锚条目 / level 不一致 / 页偏移异常
# ──────────────────────────────────────────────────────────────

def test_cross_check_report_shape():
    blocks = [
        {"id": 1, "page": 13, "type": "title", "level": 2,
         "content": "第一章 总则", "contd": -1, "image": -1},
        {"id": 2, "page": 13, "type": "text", "level": -1,
         "content": "正文。", "contd": -1, "image": -1},
        {"id": 3, "page": 23, "type": "title", "level": 2,
         "content": "第三章 错位", "contd": -1, "image": -1},
    ]
    toc = [
        {"text": "第一章 总则", "level": 2, "page": 3},
        {"text": "第二章 缺失", "level": 2, "page": 9},
        {"text": "第三章 错位", "level": 3, "page": 20},
    ]
    # page_number 块给出 扫描页 = 印刷页 + 10 的整体偏移
    cl = [_blk(str(p - 10), p - 1, btype="page_number") for p in range(11, 20)]

    rep = _cross_check(blocks, toc, cl)
    assert set(rep) == {"unanchored", "level_mismatch", "page_offset_anomaly"}
    assert [r["text"] for r in rep["unanchored"]] == ["第二章 缺失"]
    mm = rep["level_mismatch"]
    assert len(mm) == 1 and mm[0]["text"] == "第三章 错位"
    assert mm[0]["entry_level"] == 3 and mm[0]["block_level"] == 2
    # 第一章 印刷 3 → 预期扫描 13 = 实际，无异常；
    # 第三章 印刷 20 → 预期 30，实际 23 → 异常
    an = rep["page_offset_anomaly"]
    assert len(an) == 1 and an[0]["text"] == "第三章 错位"
    assert an[0]["expected_scan_page"] == 30 and an[0]["actual_scan_page"] == 23


# ──────────────────────────────────────────────────────────────
# ⑦ 层级收口是局部形状栈：同一家族在不同部位合法不同深度
# ──────────────────────────────────────────────────────────────

def _t(text, level, page, anchored=False):
    b = {"id": 0, "page": page, "type": "title", "level": level,
         "content": text, "contd": -1, "image": -1, "bbox": None}
    if anchored:
        b["_anchored"] = True
    return b


def test_level_consistency_local_stack_not_global_flatten():
    # 一、二、 锚定 L4，四、 锚定 L5（民法总论 自然人章：同一 一、家族在
    # 不同部位合法不同深度）；（一） 家族无锚，须嵌进所属锚点之下而非
    # 全局抹平。只许加深：级别已正确的块不动，锚定块锁死。
    blocks = [
        _t("一、民事行为能力的概念与功能", 4, 10, anchored=True),
        _t("（一）概念辨析", 5, 10),
        _t("四、无民事行为能力", 5, 20, anchored=True),
        _t("（一）无民事行为能力人的种类", 4, 20),   # 倒挂：比所属锚点还浅
        _t("1. 第一种", 4, 20),                       # 倒挂且应比 （一） 更深
        _t("（二）不能实施法律行为", 4, 21),
        _t("1．全角点起首的小节", 4, 21),            # 全角点 = num_dot 家族
        _t("第二节 被目录漏收的节", 3, 22),           # 家族浅于锚点 → 不动作
    ]
    n = _level_consistency_pass(blocks)
    assert blocks[0]["level"] == 4 and blocks[2]["level"] == 5  # 锚定锁死
    assert blocks[1]["level"] == 5                              # 本就对，不动
    assert blocks[3]["level"] == 6                              # 锚点+1
    assert blocks[4]["level"] == 7                              # （一）+1
    assert blocks[5]["level"] == 6                              # 同族兄弟替换
    assert blocks[6]["level"] == 7                              # 全角点按 num_dot 嵌套
    assert blocks[7]["level"] == 3                              # 家族浅于锚点：不动作
    assert n == 4


def test_demote_empty_titles():
    blocks = [
        _t("第一编 民法总论", 1, 1, anchored=True),
        _t("民法学", 2, 1),                    # 扉页书名副题，名下无内容
        _t("第一章 总则", 2, 2, anchored=True),
        _t("一、真实的节", 3, 2),              # 无锚但有正文
        {"id": 9, "page": 2, "type": "text", "level": -1,
         "content": "节下的正文段落。", "contd": -1, "image": -1},
        _t("二、空的小节", 3, 3),              # 无锚且无内容（下一个标题同级）
        _t("第二章 分则", 2, 4, anchored=True),
    ]
    n = _demote_empty_titles(blocks, [])
    assert n == 2
    assert blocks[1]["type"] == "text" and blocks[1]["level"] == -1
    assert blocks[3]["type"] == "title" and blocks[3]["level"] == 3   # 有正文，留
    assert blocks[5]["type"] == "text" and blocks[5]["level"] == -1
    # 锚定标题即使空枝也绝不动（留给 QC 空章报出）
    blocks2 = [_t("第三章 空章", 2, 5, anchored=True),
               _t("第四章 下一章", 2, 9, anchored=True)]
    assert _demote_empty_titles(blocks2, []) == 0
    # 锚得上目录条目但未被锚定的（查重降格等）也不动
    blocks3 = [_t("第五章 收录在目录", 2, 5), _t("第六章 空的小节", 3, 9)]
    toc = [{"text": "第五章 收录在目录", "level": 2, "page": 1}]
    assert _demote_empty_titles(blocks3, toc) == 1
    assert blocks3[0]["type"] == "title"      # 锚得上 → 豁免
    assert blocks3[1]["type"] == "text"       # 无锚且空 → 降格


# ──────────────────────────────────────────────────────────────
# ⑧ 无 db 回退：轻量兜底目录作锚，不崩
# ──────────────────────────────────────────────────────────────

def test_fallback_without_db():
    tmp = Path(tempfile.mkdtemp())
    cl = [
        _blk("第一章 总则", 4, level=2),
        _blk("总则的正文内容。", 4),
    ]
    light = _light_mock(toc_entries=[{"text": "第一章 总则", "level": 2, "page": 1}])
    for db_arg in (None, str(tmp / "不存在的.db")):
        with mock.patch.object(stage2_vlm, "_light_metadata_pass", light), \
                mock.patch.multiple("stage2_common", **_forbidden_mocks()):
            s = analyze_structure_vlm(cl, "测试书", str(tmp), vlm_state_db=db_arg)
        assert s["engine"] == "vlm-hybrid"
        assert s["toc_source"] is None
        assert s["toc_entries"] == [{"text": "第一章 总则", "level": 2, "page": 1}]
        blocks = json.loads((tmp / "popo_blocks.json").read_text(encoding="utf-8"))
        chap = next(b for b in blocks if (b.get("content") or "") == "第一章 总则")
        assert chap["_anchored"] and chap["level"] == 2
        assert set(s["cross_check"]) == {"unanchored", "level_mismatch",
                                         "page_offset_anomaly"}


def test_cut_index_tail():
    from stage2_vlm import _cut_index_tail
    real = [{"text": "one 7 JANUARY 1976 What is a lecture?", "level": 2, "page": 1},
            {"text": "two 14 JANUARY 1976 War and power", "level": 2, "page": 10}]
    index = [{"text": t, "level": 3, "page": 200 + i}
             for i, t in enumerate(["Weber, Max, 287", "William the Conqueror, 72, 99",
                                    "Yom Kippur War, 285", "africa, modern, 12",
                                    "anarchism, 44-45", "books read, 301",
                                    "childhood, 7", "demos, 99"])]
    # 连续索引尾 ≥8 → 切除；真目录全保
    kept, n = _cut_index_tail(real + index)
    assert kept == real and n == 8
    # 索引形状不足 8 条 → 铁律 0 不动作
    kept2, n2 = _cut_index_tail(real + index[:7])
    assert len(kept2) == len(real) + 7 and n2 == 0
    # 索引形状不在尾部（被真条目夹住）→ 不动作
    kept3, n3 = _cut_index_tail([index[0]] + real)
    assert len(kept3) == 3 and n3 == 0
    # 空/无索引 → 不动作
    assert _cut_index_tail([]) == ([], 0)
    assert _cut_index_tail(real) == (real, 0)
    # 显式 Index 边界：边界后只留真目录形状（词数+日期的讲次保下，
    # 索引交叉引用 'four Great Deaths. See …' 无年份也切）
    boundary_case = (
        [{"text": "Foreword: Someone", "level": 2, "page": 1},
         {"text": "Index", "level": 1, "page": 295},
         {"text": "four 28 JANUARY 1976 Historical discourse", "level": 2,
          "page": 65},
         {"text": "30. TEAM-CBT and the Art", "level": 2, "page": 400}]
        + [{"text": t, "level": 5, "page": 300 + i}
           for i, t in enumerate(["administrative knowledge", "Africa, modern",
                                  "archers", "fathers", "subjects", "truth",
                                  "Yom Kippur War, 285", "four Great Deaths. See Great Deaths"])])
    kept6, n6 = _cut_index_tail(boundary_case)
    assert n6 == 8
    assert [e["text"] for e in kept6] == [
        "Foreword: Someone", "Index",
        "four 28 JANUARY 1976 Historical discourse",
        "30. TEAM-CBT and the Art"]
    # 边界后切除数 <8 → 不动作
    kept7, n7 = _cut_index_tail(boundary_case[:6])
    assert n7 == 0 and len(kept7) == 6
    # 纯字母升序索引尾（must_defend 形态：无页码串的索引词条）→ 切
    alpha_idx = [{"text": t, "level": 5, "page": 300 + i}
                 for i, t in enumerate(["apples", "bananas", "cherries", "dates",
                                        "elderberry", "figs", "grapes", "hazelnuts"])]
    kept4, n4 = _cut_index_tail(real + alpha_idx)
    assert kept4 == real and n4 == 8
    # 真编号目录尾（数字起首）→ 字母序不参与，绝不误切
    numbered = real + [{"text": f"{10 + i}. Section X{i}", "level": 3, "page": 100 + i}
                       for i in range(10)]
    kept5, n5 = _cut_index_tail(numbered)
    assert len(kept5) == len(numbered) and n5 == 0


def test_demote_index_region_titles():
    from stage2_vlm import _demote_index_region_titles
    blocks = [
        {"type": "title", "level": 2, "page": 5, "content": "第一章 总则"},
        {"type": "title", "level": 3, "page": 90, "content": "习题"},
    ]
    # 末 30% 连续 2 页每页 ≥15 个标题块 → 索引区
    for p in (98, 99):
        blocks += [{"type": "title", "level": 5, "page": p,
                    "content": f"entry {i}"} for i in range(16)]
    n = _demote_index_region_titles(blocks)
    assert n == 32
    assert blocks[0]["type"] == "title"      # 真章不动
    assert blocks[1]["type"] == "title"      # 稀疏页不动
    assert all(b["type"] == "text" for b in blocks[2:])
    # 只有 1 页稠密 → 不构成区段，不动作
    blocks2 = [{"type": "title", "level": 5, "page": 99,
                "content": f"e{i}"} for i in range(20)]
    assert _demote_index_region_titles(blocks2) == 0
    assert blocks2[0]["type"] == "title"


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


if __name__ == "__main__":
    sys.exit(1 if _run_all() else 0)
