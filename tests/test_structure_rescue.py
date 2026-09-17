#!/usr/bin/env python3
"""test_structure_regression.py — Feeling Great 事故修复的单元回归。

覆盖（每条对应一个已定位的管线缺陷）：
  1. _PAGE_CHARS 不再截断目录页（800 → 4000）
  2. _build_anchors 拒收退化条目（'VI.' → 'vi.'），保留合法短锚（'跋'）
  3. _match_anchor 模糊匹配 off-by-one：超限（limit+1）不得被接受
     —— 'Total'/'1._____'/'• Mania'/'I said:'/'欽定四庫全書' 不得命中任何锚点
  4. 模糊匹配仍容忍真实 OCR 变体：'12 | All-or-Nothing Thinking' 命中
     '12. All-or-Nothing Thinking'（标点差异，编辑距离 1）
  5. extract_label2 拒收 level=0（第 18 章僵尸块黑洞），正常层级保留
  6. _spine_from_toc 识别英文罗马数字分区 + 纯数字章条目 → (1, 2)
  7. _title_shape 识别竖线编号 '12 | …' → num_bar

运行：.venv/Scripts/python.exe tests/test_structure_rescue.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stage2_common import (_PAGE_CHARS, _build_anchors, _match_anchor,  # noqa
                           _title_shape)
from popo.inference import extract_label2  # noqa
from stage3_epub import _spine_from_toc  # noqa

passed = failed = 0


def ok(cond, label):
    global passed, failed
    if cond:
        passed += 1
        print(f"  ✓ {label}")
    else:
        failed += 1
        print(f"  ✗ {label}")


# Feeling Great 完整目录（标准答案，33 章 + 6 分区 + 前后页）
FG_TOC = [
    {"text": "Acknowledgments", "level": 1, "page": 9},
    {"text": "Introduction: Then and Now", "level": 1, "page": 11},
    {"text": "I. How to Turn Depression and Anxiety into Joy", "level": 1},
    {"text": "1. How Are You Feeling?", "level": 2, "page": 3},
    {"text": "10. How to Change the Way You Feel: Part 1—Your Daily MoJo (Daily Mood Journal)", "level": 2, "page": 169},
    {"text": "11. How to Change the Way You Feel: Part 2—The Great Escape", "level": 2, "page": 201},
    {"text": "II. How to Crush Distorted Thoughts", "level": 1},
    {"text": "12. All-or-Nothing Thinking", "level": 2, "page": 209},
    {"text": "13. Overgeneralization", "level": 2, "page": 217},
    {"text": "18. Magnification and Minimization", "level": 2, "page": 293},
    {"text": "VI. Additional Resources", "level": 1},
    {"text": "33. Fifty Ways to Untwist Your Thinking", "level": 2, "page": 475},
    {"text": "Index", "level": 1, "page": 503},
]

print("1. _PAGE_CHARS 截断")
ok(_PAGE_CHARS >= 4000, f"_PAGE_CHARS={_PAGE_CHARS} ≥ 4000")

print("2. _build_anchors 退化条目拒收")
anchors = _build_anchors(FG_TOC + [{"text": "VI.", "level": 1, "page": None}])
keys = {a[0] for a in anchors}
ok("vi." not in keys and "vi" not in keys, "退化条目 'VI.' 未成为锚点")
ok(any("12.all-or-nothingthinking" == k for k in keys), "正常章条目在锚表中")
anchors_cjk = _build_anchors([{"text": "跋", "level": 1, "page": 600}])
ok(len(anchors_cjk) == 1, "单字 CJK 锚点 '跋' 保留")

print("3. 模糊匹配拒收短垃圾块（事故核心）")
for junk in ["Total", "1. _____", "• Mania", "I said:", "欽定四庫全書",
             "Filters", "My Answer"]:
    ok(_match_anchor(junk, anchors) is None, f"{junk!r} 不命中任何锚点")

print("4. 模糊匹配容忍真实 OCR 变体")
m = _match_anchor("12 | All-or-Nothing Thinking", anchors)
ok(m is not None and m[1] == 2, "'12 | …' 命中 '12. …'（L2）")
m = _match_anchor("Magnification and Minimization", anchors)
ok(m is not None and m[1] == 2, "去编号后缀命中 18 章（L2）")

print("5. extract_label2 拒收 level=0")
r = extract_label2("<|id|>5<|level|>0\n<|id|>7<|level|>2\n<|id|>9<|level|>abc")
ok(all(p["level"] >= 1 for p in r), "无 level=0 输出")
ok([p["id"] for p in r] == [7], "仅合法层级保留")

print("6. _spine_from_toc 英文编号目录")
ok(_spine_from_toc(FG_TOC) == (1, 2), "罗马分区+数字章 → (1, 2)")
ok(_spine_from_toc([{"text": "第一章 总论", "level": 1},
                    {"text": "第二章 分论", "level": 1}]) == (None, 1),
   "纯中文章目录 → (None, 1)")
ok(_spine_from_toc([{"text": "1. Foo", "level": 2},
                    {"text": "2. Bar", "level": 2}]) == (None, 2),
   "纯数字章目录 → (None, 2)")
ok(_spine_from_toc([{"text": "1 Introduction", "level": 1},
                    {"text": "1.1 Overview", "level": 2},
                    {"text": "5.5.1 Loops", "level": 3}]) == (None, None),
   "多级小节编号不误判为章（QFT 病例：'1.1' 曾把 spine 拉到 2 压平层级）")

print("7. 竖线编号形状")
ok(_title_shape("12 | All-or-Nothing Thinking") == "num_bar", "'12 | …' → num_bar")
ok(_title_shape("12. All-or-Nothing Thinking") == "num_dot", "'12. …' → num_dot")
ok(_title_shape("Magnification and Minimization") == "plain", "无编号 → plain")

print("8. 救援位置闸门（CliffsNotes 交叉引用防晋升）")
from stage2_common import _calibrate_levels  # noqa

blocks = [
    # 三个已锚定标题，建立 印刷页→扫描页 偏移 22（3→25, 169→191, 201→223）
    {"type": "title", "level": 2, "page": 25,
     "content": "1. How Are You Feeling?"},
    {"type": "title", "level": 2, "page": 191,
     "content": "10. How to Change the Way You Feel: Part 1—Your Daily MoJo (Daily Mood Journal)"},
    {"type": "title", "level": 2, "page": 223,
     "content": "11. How to Change the Way You Feel: Part 2—The Great Escape"},
    # 引擎漏判的真·第 12 章章首（印刷 209 → 预测扫描 231）→ 应获救
    {"type": "text", "level": -1, "page": 231,
     "content": "12 | All-or-Nothing Thinking"},
    # CliffsNotes 摘要表里的章节名交叉引用（扫描 40，偏离预测 191 页）→ 拒绝
    {"type": "text", "level": -1, "page": 40,
     "content": "12. All-or-Nothing Thinking"},
    # 引擎已标标题但 LLM 漏判的前置页块（罗马页码 regime，全局偏移不适用）
    # → 豁免位置闸门，应获救（病例：FG 'Acknowledgments' ix 被误杀）
    {"type": "title", "level": -1, "page": 13,
     "content": "Acknowledgments"},
    # 运行头噪声块：suffix 命中锚点也绝不晋升（病例 QFT ch4：
    # 真章题被 mini-TOC 降格后，下一页运行头被救援顶替）
    {"type": "header", "level": -1, "page": 232,
     "content": "All-or-Nothing Thinking"},
]
_calibrate_levels(blocks, FG_TOC)
ok(blocks[3].get("type") == "title" and blocks[3].get("level") == 2,
   "真章首块在预测位置获救晋升")
ok(blocks[4].get("type") != "title", "摘要表交叉引用被拒绝晋升")
ok(blocks[5].get("type") == "title" and blocks[5].get("level") == 1,
   "引擎标题块豁免位置闸门获救")
ok(blocks[6].get("type") == "header", "运行头噪声块不被救援晋升")

print("9. 子串锚定覆盖率闸门")
anchors9 = _build_anchors(FG_TOC)
ok(_match_anchor("Depression", anchors9) is None,
   "单词块 'Depression' 不得子串命中 41 字符分区条目")
m = _match_anchor(
    "How to Change the Way You Feel: Part 1—Your Daily MoJo",
    anchors9)
ok(m is not None and m[1] == 2, "长截断副标题仍正常子串命中（覆盖率 0.67）")

print("10. 锚点身份查重（与译文无关，位置择优）")
from stage2_common import _dedup_anchored_titles  # noqa
blocks10 = [
    # 三个精确命中块建立 印刷页→扫描页 偏移 22
    {"type": "title", "level": 2, "page": 25, "_anchored": True,
     "content": "1. How Are You Feeling?"},
    {"type": "title", "level": 2, "page": 191, "_anchored": True,
     "content": "10. How to Change the Way You Feel: Part 1—Your Daily MoJo (Daily Mood Journal)"},
    {"type": "title", "level": 2, "page": 223, "_anchored": True,
     "content": "11. How to Change the Way You Feel: Part 2—The Great Escape"},
    # 引用框幻影：阅读顺序在前、精确命中条目（也投野票），但位置离谱
    {"type": "title", "level": 3, "page": 40,
     "content": "12. All-or-Nothing Thinking"},
    # 真章首：模糊命中（'|' vs '.'），位置吻合预测 209+22=231
    {"type": "title", "level": 2, "page": 231, "_anchored": True,
     "content": "12 | All-or-Nothing Thinking"},
    # 不同条目不受影响
    {"type": "title", "level": 2, "page": 239, "_anchored": True,
     "content": "13 | Overgeneralization"},
]
_dedup_anchored_titles(blocks10, FG_TOC)
ok(blocks10[4]["type"] == "title", "真章首块保留（位置最优）")
ok(blocks10[3]["type"] == "text" and blocks10[3]["level"] == -1,
   "阅读顺序在前的引用框幻影降回正文")
ok(blocks10[5]["type"] == "title", "不同条目不受影响")
# 无页码锚点：退化为留首个（FG_TOC 中分区条目无 page）
blocks10b = [
    {"type": "title", "level": 1, "page": 23, "_anchored": True,
     "content": "I. How to Turn Depression and Anxiety into Joy"},
    {"type": "title", "level": 1, "page": 59, "_anchored": True,
     "content": "I. How to Turn Depression and Anxiety into Joy"},
]
_dedup_anchored_titles(blocks10b, FG_TOC)
ok(blocks10b[0]["type"] == "title" and blocks10b[1]["type"] == "text",
   "无页码锚点留阅读顺序首个")

print("11. 无锚垃圾标题否决器")
from stage2_common import _veto_junk_titles  # noqa
blocks11 = [
    {"type": "title", "level": 1, "page": 520, "content": "A"},          # 索引字母字头
    {"type": "title", "level": 1, "page": 62, "content": "二"},          # 孤字残片
    {"type": "title", "level": 2, "page": 447, "content": "马克·诺布尔博士 著"},  # 署名行
    {"type": "title", "level": 1, "page": 58, "content": "Depression"},  # 单个英文词（一次性浮头）
    {"type": "title", "level": 3, "page": 233, "content": "POSITIVE REFRAMING"},  # 真小节
    {"type": "title", "level": 1, "page": 600, "_anchored": True, "content": "跋"},  # 锚定豁免
    # 复发豁免：'Summary' 每章复发（教科书章末小节），不降格
    *[{"type": "title", "level": 3, "page": 100 + i * 20, "content": "Summary"}
      for i in range(4)],
]
_veto_junk_titles(blocks11, FG_TOC + [{"text": "跋", "level": 1, "page": 600}])
ok(all(b["type"] == "text" for b in blocks11[:4]), "四类垃圾形态全部降回正文")
ok(blocks11[4]["type"] == "title", "多词小节标题不受影响")
ok(blocks11[5]["type"] == "title", "锚定标题豁免否决")
ok(all(b["type"] == "title" for b in blocks11[6:]),
   "复发 ≥3 的相同短标题豁免（Summary/Exercises 章末小节）")

print("12. 泛名书签位置锚定（病例 022）")
from stage2_common import _anchor_generic_outline, _sanitize_pdf_toc  # noqa
toc_cm = [
    {"text": "9781107017108", "level": 1, "page": 1},
    {"text": "Part I", "level": 1, "page": 22},
    {"text": "Chapter 1", "level": 1, "page": 24},
    {"text": "Chapter 2", "level": 1, "page": 41},
    {"text": "Preface", "level": 1, "page": 14},
]
clean = _sanitize_pdf_toc(toc_cm)
ok(not any(e["text"] == "9781107017108" for e in clean), "ISBN 纯数字书签条目被拒收")
ok(len(clean) == 4, "正常书签条目保留")

blocks12 = [
    {"type": "title", "level": 1, "page": 22, "_anchored": True,
     "content": "PART I"},
    {"type": "title", "level": 2, "page": 24, "content": "Crystal structure"},
    {"type": "title", "level": 3, "page": 24, "content": "Introduction"},
    {"type": "title", "level": 3, "page": 24, "content": "1.1 Crystal lattice"},
    # 章题未被标出的第 2 章：章首块为 text，应由位置锚定晋升
    {"type": "text", "level": -1, "page": 41, "content": "Amorphous structure"},
    {"type": "text", "level": -1, "page": 41,
     "content": "In the previous chapter we saw that crystals"},
]
n = _anchor_generic_outline(blocks12, clean)
ok(n == 2, "两个泛名 Chapter 条目按页锁定（Part I 已锚定跳过）")
ok(blocks12[1].get("_anchored") and blocks12[1]["level"] == 2,
   "Chapter 1 页顶块锁 L2（全平 outline 按 Part/Chapter 类别重建层级）")
ok(blocks12[4].get("_anchored") and blocks12[4]["type"] == "title"
   and blocks12[4]["level"] == 2,
   "未标题化章首块晋升并锁 L2（书签页强证据兜底）")
ok(blocks12[5]["type"] == "text", "同页正文段不被晋升")
ok(not blocks12[2].get("_anchored") and not blocks12[3].get("_anchored"),
   "同页非页顶块不受影响")
# 非泛名/具名书签（'Chapter 1: Run'）不触发
blocks12c = [{"type": "title", "level": 2, "page": 10, "content": "Run"}]
ok(_anchor_generic_outline(blocks12c,
       [{"text": "Chapter 1: Run", "level": 1, "page": 10}]) == 0,
   "具名书签不走位置锚定")

print("13. 泛名书签层级重建（spine 检测前提）")
from stage2_common import _normalize_generic_outline_levels  # noqa
rew = _normalize_generic_outline_levels(clean)
lv = {e["text"]: e["level"] for e in rew}
ok(lv["Part I"] == 1 and lv["Chapter 1"] == 2 and lv["Chapter 2"] == 2,
   "全平 outline 重建为 Part→L1 / Chapter→L2")
ok(lv["Preface"] == 1, "非泛名条目层级不动")
ok(_spine_from_toc(rew) == (1, 2), "重建后 spine 检测 (partition, spine)=(1,2)")
same = _normalize_generic_outline_levels(
    [{"text": "Chapter 1: Run", "level": 1}, {"text": "Chapter 2: X", "level": 2}])
ok(same[0]["level"] == 1 and same[1]["level"] == 2, "无泛名/非全平原样返回")

print("14. 正文起点回收（泛名 outline 页码 vs LLM 前页波动）")
from stage3_epub import _body_range  # noqa
s_guard = {"front_matter": [{"page_end": 23}], "back_matter": [],
           "toc_source": "outline",
           "toc_entries": [{"text": "Part I", "level": 1, "page": 22},
                           {"text": "Chapter 1", "level": 2, "page": 24}]}
ok(_body_range(s_guard, 400)[0] == 22, "泛名 outline 页码回收正文起点")
s_no = {"front_matter": [{"page_end": 23}], "back_matter": [],
        "toc_source": "outline", "toc_entries": []}
ok(_body_range(s_no, 400)[0] == 24, "无泛名条目不受影响")
s_llm = {"front_matter": [{"page_end": 23}], "back_matter": [],
         "toc_source": None,
         "toc_entries": [{"text": "Chapter 1", "level": 1, "page": 1}]}
ok(_body_range(s_llm, 400)[0] == 24, "非 outline 来源条目不用于回收")

print("15. Problem N.M. 编号前缀剥离（病例 024）")
prob_toc = [{"text": "The fine structure of the hydrogen atom", "level": 2, "page": 90},
            {"text": "Three-body kinematics and phase space", "level": 2, "page": 187}]
prob_anchors = _build_anchors(prob_toc)
ok(_match_anchor("Problem 3.1. The fine structure of the hydrogen atom",
                 prob_anchors) is not None, "'Problem 3.1. X' 命中 'X' 条目")
ok(_match_anchor("Problem 6.1. Three-body kinematics and phase space",
                 prob_anchors) is not None, "'Problem 6.1. Y' 命中 'Y' 条目")
ok(_match_anchor("Problems", prob_anchors) is None,
   "无数字编号的 'Problems' 不触发剥离")
ok(_match_anchor("Problem 3.1. Something Else Entirely", prob_anchors) is None,
   "剥前缀后仍不存在的条目不误配")

print("16. 位置锚定候选规则（页顶优先/类型扩列/小节排除/章号记录）")
toc16 = _normalize_generic_outline_levels(
    [{"text": "Part I", "level": 1, "page": 22},
     {"text": "Chapter 2", "level": 1, "page": 41},
     {"text": "Chapter 12", "level": 1, "page": 222}])
blocks16 = [
    {"type": "title", "level": 1, "page": 22, "_anchored": True,
     "content": "PART I"},
    # ch2：真章题是页顶 text 块（被引擎标 header/降级款），'Introduction'
    # 是更深位置的已标小节头——页顶优先必须选前者
    {"type": "text", "level": -1, "page": 41, "bbox": [0.4, 0.13, 0.7, 0.16],
     "content": "Amorphous structure"},
    {"type": "title", "level": 2, "page": 41, "bbox": [0.4, 0.238, 0.7, 0.26],
     "content": "Introduction"},
    # ch12：真章题是 level=0 的 title（投票缺失），'12.1 …' 小节应排除
    {"type": "title", "level": 0, "page": 222, "bbox": [0.4, 0.13, 0.7, 0.16],
     "content": "Electrons: the free electron model"},
    {"type": "title", "level": 2, "page": 222, "bbox": [0.4, 0.6, 0.7, 0.63],
     "content": "12.1 Mobile electrons"},
]
n16 = _anchor_generic_outline(blocks16, toc16)
ok(n16 == 2, "两条泛名章锁定")
ok(blocks16[1].get("_anchored") and blocks16[1].get("_pos_num") == "2",
   "页顶真章题块压过更深的小节头当选（并记章号 2）")
ok(blocks16[3].get("_anchored") and blocks16[3].get("_pos_num") == "12",
   "level=0 投票缺失章题块可当选（并记章号 12）")
ok(not blocks16[4].get("_anchored"), "'12.1 …' 本章小节不被选为章题")

# ── 17. 运行页眉降格 + LaTeX 间距命令归一（病例 042）──
from stage2_common import _normalize_title, _veto_junk_titles  # noqa

blocks17 = [
    {"type": "title", "level": 1, "page": 412,
     "content": "第三章 习题 3-1（第 132 页）"},
    {"type": "title", "level": 1, "page": 100, "content": "第三章 微分中值定理"},
]
toc17 = [{"text": "第三章 微分中值定理", "level": 1, "page": 96}]
_veto_junk_titles(blocks17, toc17)
ok(blocks17[0]["type"] == "text", "运行页眉（章名+节名+页码后缀）降回正文")
ok(blocks17[1]["type"] == "title", "真章标题不受页眉规则影响")

norm_a = _normalize_title("f(x)=\\mathrm{e}^{\\lambda x}[\\,P_l(x)\\cos\\omega x\\,]")
norm_b = _normalize_title("f(x)=\\mathrm{e}^{\\lambda x}[P_l(x)\\cos \\omega x]")
ok(norm_a == norm_b, "LaTeX 间距命令 \\, 归一对齐（cos\\omega x\\, ↔ cos \\omega x）")

print(f"\n{passed} 过 / {failed} 挂")
sys.exit(1 if failed else 0)
